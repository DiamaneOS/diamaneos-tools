"""Platform signature verifiers with public trust material outside the artifacts."""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
import re
import tempfile
import zipfile

from . import signing_archive as za
from .signing_inputs import (CERT_ROLES, NAME, SHA256, SigningError, command,
                             fields, load_json, require, safe_file, sha256_file)


def apk_certificate_digests(output, expected):
    require(isinstance(expected, str) and SHA256.fullmatch(expected), 'Invalid APK certificate pin')
    lines = output.splitlines()
    require([line for line in lines if line.startswith('Number of signers:')] == ['Number of signers: 1'],
            'APK verifier did not establish exactly one signer')
    pattern = re.compile(r'^(?:Signer #[1-9][0-9]*|V[1-3](?:\.[0-9]+)? Signer):? '
                         r'certificate SHA-256 digest: ([0-9a-fA-F]{64})$')
    matches = [pattern.fullmatch(line) for line in lines if 'certificate SHA-256 digest:' in line]
    require(matches and all(matches), 'APK verifier certificate output is unsupported')
    observed = {match[1].lower() for match in matches}
    require(observed == {expected}, 'APK/APEX certificate does not match its declared role')
    return sorted(observed)


class NativeTools:
    def __init__(self, root, source, scratch, *, projects=None):
        self.root, self.source, self.scratch = map(Path, (root, source, scratch))
        self.bin = self.root / 'bin'
        jdks = [self.source / path / 'linux-x86' for path in projects if path.startswith('prebuilts/jdk/')] if projects else list(self.source.glob('prebuilts/jdk/jdk*/linux-x86'))
        jdks = sorted(jdks,
                      key=lambda path: int(re.sub(r'\D', '', path.parent.name) or 0))
        require(jdks, 'Source has no usable JDK')
        self.java_home = jdks[-1]
        require((self.java_home / 'bin/java').is_file(), 'Recorded JDK has no Java executable')
        self.env = {'PATH': str(self.bin) + ':' + str(self.java_home / 'bin') + ':/usr/bin:/bin',
                    'JAVA_HOME': str(self.java_home), 'HOME': str(self.scratch),
                    'LD_LIBRARY_PATH': str(self.root / 'lib64'),
                    'TMPDIR': str(self.scratch), 'LANG': 'C', 'LC_ALL': 'C',
                    'ANDROID_HOST_OUT': str(self.root)}

    def path(self, name):
        return safe_file(self.root, 'bin/' + name)

    def run(self, name, args, *, cwd=None):
        return command([self.path(name), *args], env=self.env, cwd=cwd).decode('utf-8', 'strict')

    def openssl(self, args, data=None):
        return command(['/usr/bin/openssl', *args], env=self.env, data=data)

    def certificate(self, path):
        raw = self.openssl(['x509', '-in', path, '-outform', 'DER'])
        public = self.openssl(['x509', '-in', path, '-pubkey', '-noout'])
        public = self.openssl(['pkey', '-pubin', '-outform', 'DER'], public)
        return hashlib.sha256(raw).hexdigest(), hashlib.sha256(public).hexdigest()

    def apk(self, path, expected):
        output = self.run('apksigner', ['verify', '--verbose', '--print-certs', path])
        return apk_certificate_digests(output, expected)

    def avb(self, path, expected, *, chains=(), payload=False):
        info = self.run('avbtool', ['info_image', '--image', path])
        require(re.search(r'(?m)^Algorithm:\s+SHA256_RSA4096\s*$', info), 'AVB algorithm is not the role algorithm')
        require(re.search(r'(?m)^Flags:\s+0\s*$', info), 'AVB disables a verification property')
        args = ['verify_image', '--image', path, '--key', expected]
        for name, location, key in chains:
            args += ['--expected_chain_partition', f'{name}:{location}:{key}']
        alias = None
        if payload:
            descriptors = avb_descriptors(info)
            require(len(descriptors) == 1 and descriptors[0][1] is None, 'APEX payload has an unsupported descriptor')
            # avbtool opens hashtree data by the descriptor's partition name.
            candidate = Path(path).parent / (descriptors[0][0] + '.img')
            if candidate != Path(path):
                require(not candidate.exists(), 'APEX payload alias already exists')
                os.link(path, candidate); alias = candidate
        try:
            self.run('avbtool', args, cwd=Path(path).parent)
        finally:
            if alias is not None: alias.unlink()
        return info

    def ssh_signature(self, path, signature, allowed_signers, identity, namespace):
        # The shell opens the bounded artifact directly; its bytes never enter Python memory.
        command(['/bin/sh', '-c', 'input=$1; shift; exec "$@" < "$input"', 'verify', path,
                 '/usr/bin/ssh-keygen', '-Y', 'verify', '-f', allowed_signers,
                 '-I', identity, '-n', namespace, '-s', signature], env=self.env)

    def ota(self, path, expected, provenance):
        with za.archive(path) as handle:
            require({'payload.bin', 'payload_properties.txt', 'META-INF/com/android/metadata'} <= set(handle.namelist()),
                    'OTA lacks payload or metadata')
            metadata = za.properties(za.read_text(handle, 'META-INF/com/android/metadata'))
            require(metadata.get('ota-type') == 'AB' and metadata.get('post-build-incremental') == provenance['build_number']
                    and metadata.get('post-timestamp') == str(provenance['build_datetime'])
                    and provenance['product'] in metadata.get('pre-device', '').split('|'),
                    'OTA metadata does not match the verified build')
            properties = za.properties(za.read_text(handle, 'payload_properties.txt'))
            require({'FILE_HASH', 'FILE_SIZE', 'METADATA_HASH', 'METADATA_SIZE'} <= set(properties),
                    'OTA lacks payload integrity properties')
            require(properties['FILE_SIZE'].isdecimal() and properties['METADATA_SIZE'].isdecimal(),
                    'OTA payload sizes are invalid')
            metadata_size = int(properties['METADATA_SIZE'])
            require(int(properties['FILE_SIZE']) == handle.getinfo('payload.bin').file_size
                    and 0 < metadata_size <= za.MAX_TEXT and metadata_size <= int(properties['FILE_SIZE']),
                    'OTA payload sizes disagree with the archive')
            with handle.open('payload.bin') as stream:
                metadata_hash = hashlib.sha256(stream.read(metadata_size)).digest()
            require(base64.b64encode(metadata_hash).decode() == properties['METADATA_HASH']
                    and base64.b64encode(bytes.fromhex(za.digest_member(handle, 'payload.bin'))).decode() == properties['FILE_HASH'],
                    'OTA payload properties do not match the final bytes')
        # The platform checker validates whole-file signatures and invokes delta_generator
        # with the independent certificate's public key for payload and metadata signatures.
        self.run('check_ota_package_signature', [expected, path])

    def apply_payload(self, payload, target_files, source_files=None):
        """Apply with delta_generator and compare every resulting partition byte."""
        with tempfile.TemporaryDirectory(dir=self.scratch) as directory:
            root = Path(directory)
            def images(archive_path, prefix):
                result = {}
                with za.archive(archive_path) as handle:
                    names = za.read_text(handle, 'META/ab_partitions.txt').split()
                    require(names and len(set(names)) == len(names) and all(NAME.fullmatch(name) for name in names),
                            'Invalid OTA partition inventory')
                    for name in names:
                        member = next((p for p in ('IMAGES/' + name + '.img', 'RADIO/' + name + '.img')
                                       if p in handle.namelist()), None)
                        require(member, 'Target-files lacks an OTA partition')
                        path = za.extract(handle, member, root / (prefix + name + '.img'))
                        with path.open('rb') as stream:
                            sparse = stream.read(4) == b'\x3a\xff\x26\xed'
                        if sparse:
                            raw = root / (prefix + name + '.raw.img')
                            self.run('simg2img', [path, raw]); path = raw
                        result[name] = path
                return result
            target = images(target_files, 'target-')
            old = images(source_files, 'source-') if source_files is not None else {}
            require(not old or set(old) == set(target), 'Delta partition inventory changed')
            names = sorted(target)
            outputs = []
            for name in names:
                path = root / (name + '.img')
                with path.open('xb') as stream: stream.truncate(target[name].stat().st_size)
                outputs.append(path)
            args = ['--in_file=' + str(payload), '--partition_names=' + ':'.join(names),
                    '--new_partitions=' + ':'.join(map(str, outputs))]
            if old:
                args += ['--old_partitions=' + ':'.join(str(old[name]) for name in names)]
            self.run('delta_generator', args)
            require(all(sha256_file(path) == sha256_file(target[name]) for name, path in zip(names, outputs)),
                    'Applied OTA partitions differ from signed target-files')

    def extract_filesystem(self, image, destination):
        with tempfile.TemporaryDirectory(dir=self.scratch) as directory:
            container = Path(directory) / 'partition.apex'
            with zipfile.ZipFile(container, 'w') as handle:
                handle.write(image, 'apex_payload.img')
            self.run('deapexer', ['--debugfs_path', self.path('debugfs_static'),
                                 '--fsckerofs_path', self.path('fsck.erofs'), 'extract', container, destination])


class PublicKeys:
    def __init__(self, path, tools, *, production):
        self.root = Path(path).resolve().parent
        self.inventory = load_json(path)
        fields(self.inventory, {'schema_version', 'certificate_roles', 'avb_public_key', 'factory',
                                'presigned', 'metadata_only'})
        require(self.inventory['schema_version'] == 1 and set(self.inventory['certificate_roles']) == CERT_ROLES,
                'Public key inventory has unknown or missing roles')
        self.tools = tools
        self.files = {}
        self.fingerprints, self.public_ids = {}, {}
        for role, relative in self.inventory['certificate_roles'].items():
            cert = self.file(relative)
            self.fingerprints[role], self.public_ids[role] = tools.certificate(cert)
            info = tools.openssl(['x509', '-in', cert, '-text', '-noout']).decode()
            subject = tools.openssl(['x509', '-in', cert, '-subject', '-noout', '-nameopt', 'RFC2253']).decode().strip()
            require('Public-Key: (4096 bit)' in info and 'Signature Algorithm: sha256WithRSAEncryption' in info
                    and subject == 'subject=CN=DiamaneOS', 'Certificate does not match the role policy')
        avb = self.file(self.inventory['avb_public_key'])
        self.avb_id = hashlib.sha256(tools.openssl(['pkey', '-pubin', '-in', avb, '-outform', 'DER'])).hexdigest()
        require(len(set(self.public_ids.values()) | {self.avb_id}) == len(CERT_ROLES) + 1,
                'Public identities are reused across signing roles')
        factory = self.inventory['factory']
        fields(factory, {'allowed_signers', 'identity'})
        allowed = self.file(factory['allowed_signers']).read_text().splitlines()
        require(allowed and all(len(line.split()) == 3 and line.split()[0] == factory['identity']
                                and line.split()[1] in {'ssh-ed25519', 'sk-ssh-ed25519@openssh.com'} for line in allowed),
                'Factory authority must be an explicit Ed25519 signer')
        require(isinstance(self.inventory['presigned'], dict)
                and isinstance(self.inventory['metadata_only'], list)
                and len(set(self.inventory['metadata_only'])) == len(self.inventory['metadata_only'])
                and all(NAME.fullmatch(name) for name in self.inventory['metadata_only']), 'Invalid package exceptions')
        for record in self.inventory['presigned'].values():
            fields(record, {'certificate', 'avb_public_key'})
            self.file(record['certificate'])
            if record['avb_public_key'] is not None:
                self.file(record['avb_public_key'])
        if production:
            self.reject_public_test_keys()

    def file(self, relative):
        path = safe_file(self.root, relative)
        require(path.stat().st_size <= 1024 * 1024, 'Public key input exceeds its bounds')
        require(b'PRIVATE KEY' not in path.read_bytes(), 'Verifier received private key material')
        digest = sha256_file(path)
        require(relative not in self.files or self.files[relative] == digest, 'Public key input changed during verification')
        self.files[relative] = digest
        return path

    def cert(self, role, logical):
        if role == 'PRESIGNED':
            require(logical in self.inventory['presigned'], 'Unlisted presigned package')
            return self.file(self.inventory['presigned'][logical]['certificate'])
        require(role in CERT_ROLES, 'Unknown certificate role')
        return self.file(self.inventory['certificate_roles'][role])

    def avb(self, logical=None):
        if logical is not None:
            record = self.inventory['presigned'].get(logical)
            require(record and record['avb_public_key'], 'Unlisted presigned APEX payload authority')
            return self.file(record['avb_public_key'])
        return self.file(self.inventory['avb_public_key'])

    def reject_public_test_keys(self):
        source = self.tools.source
        certificates = list((source / 'build/make/target/product/security').glob('*.x509.pem'))
        certificates += list((source / 'system/apex').rglob('*.x509.pem'))
        require(certificates, 'Public test certificate deny list is unavailable')
        denied = {self.tools.certificate(cert)[1] for cert in certificates}
        presigned_ids = {self.tools.certificate(self.file(record['certificate']))[1]
                         for record in self.inventory['presigned'].values()}
        require(not (set(self.public_ids.values()) | presigned_ids) & denied,
                'Production refuses AOSP public test certificates')
        test_keys = list((source / 'external/avb/test/data').glob('testkey_rsa*.pem'))
        require(test_keys, 'Public AVB test key deny list is unavailable')
        expected = set()
        public_keys = [self.avb()] + [self.file(r['avb_public_key']) for r in self.inventory['presigned'].values()
                                     if r['avb_public_key'] is not None]
        for key in public_keys:
            output = self.tools.scratch / 'expected-avb.bin'
            self.tools.run('avbtool', ['extract_public_key', '--key', key, '--output', output])
            expected.add(output.read_bytes())
        for test in test_keys:
            output = self.tools.scratch / 'test-avb.bin'
            self.tools.run('avbtool', ['extract_public_key', '--key', test, '--output', output])
            require(output.read_bytes() not in expected, 'Production refuses AOSP public AVB test keys')


def package_members(target_files, tools):
    """Yield regular APKs, APEX containers/payloads and APKs inside APEX images."""
    with za.archive(target_files) as handle:
        for name in sorted(handle.namelist()):
            if not name.endswith(('.apk', '.apex', '.capex')):
                continue
            with tempfile.TemporaryDirectory(dir=tools.scratch) as directory:
                root = Path(directory)
                path = za.extract(handle, name, root / Path(name).name)
                if name.endswith('.apk'):
                    yield 'apk', name, path
                    continue
                yield 'apex', name, path
                if name.endswith('.capex'):
                    uncompressed = root / 'original.apex'
                    tools.run('deapexer', ['decompress', '--input', path, '--output', uncompressed])
                    path = uncompressed
                    yield 'apex', name, path
                with za.archive(path) as apex:
                    payload = za.extract(apex, 'apex_payload.img', root / 'apex_payload.img')
                yield 'payload', name, payload
                content = root / 'content'
                tools.run('deapexer', ['--debugfs_path', tools.path('debugfs_static'),
                                      '--fsckerofs_path', tools.path('fsck.erofs'), 'extract', path, content])
                for apk in sorted(content.rglob('*.apk')):
                    require(not apk.is_symlink() and apk.resolve().is_relative_to(content.resolve()),
                            'APEX contains an unsafe APK path')
                    yield 'apk', name + '!/' + str(apk.relative_to(content)), apk


def avb_descriptors(info):
    result = []
    for block in re.split(r'(?m)^\s*(?:Hash|Hashtree|Chain Partition) descriptor:\s*$', info)[1:]:
        name = re.search(r'(?m)^\s*Partition Name:\s+([A-Za-z0-9_.-]+)\s*$', block)
        require(name, 'AVB descriptor has an unsupported partition name')
        location = re.search(r'(?m)^\s*Rollback Index Location:\s+(\d+)\s*$', block)
        result.append((name[1], int(location[1]) if location else None))
    require(len({name for name, _ in result}) == len(result), 'Duplicate AVB partition descriptor')
    return result


def verify_images(tools, keys, images, expected_partitions, expected_chains):
    pending, visited, covered = ['vbmeta'], set(), set()
    public_blob = tools.scratch / 'chain-key.bin'
    tools.run('avbtool', ['extract_public_key', '--key', keys.avb(), '--output', public_blob])
    observed_chains = {}
    while pending:
        name = pending.pop()
        require(name not in visited and name in expected_partitions, 'Unexpected or cyclic AVB chain')
        visited.add(name)
        path = safe_file(images, name + '.img')
        descriptors = avb_descriptors(tools.run('avbtool', ['info_image', '--image', path]))
        chains = []
        for partition, location in descriptors:
            require(partition in expected_partitions, 'AVB references an undeclared partition')
            safe_file(images, partition + '.img')
            covered.add(partition)
            if location is not None:
                require(expected_chains.get(partition) == location and partition not in observed_chains,
                        'AVB rollback chain differs from the verified input')
                observed_chains[partition] = location
                pending.append(partition)
                chains.append((partition, location, public_blob))
        tools.avb(path, keys.avb(), chains=chains)
    require(covered | visited == set(expected_partitions) and observed_chains == expected_chains,
            'AVB does not cover every declared partition and chain')
    return sorted(covered | visited)

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
from .signing_inputs import DEFAULT_CONFIG, validate_config, presigned_rule


APK_SIGNER = (r'(?:Signer #1:?|V[12] Signer:|V3\.[012] Signer:'
              r'(?: \(minSdkVersion=[0-9]{1,10}(?: \(dev release=true\))?, '
              r'maxSdkVersion=[0-9]{1,10}\))?)')


def apk_signer_digests(output, kind):
    """Read every printed signer, including SDK-restricted scheme signers."""
    marker = kind + ' SHA-256 digest:'
    pattern = re.compile(r'^(' + APK_SIGNER + r') ' + re.escape(marker) + r' ([0-9a-fA-F]{64})$')
    matches = [pattern.fullmatch(line) for line in output.splitlines() if marker in line]
    require(matches and all(matches), 'APK verifier ' + kind + ' output is unsupported')
    records = {match[1]: match[2].lower() for match in matches}
    require(len(records) == len(matches), 'APK verifier repeats a signer digest')
    return records


def apk_certificate_digests(output, expected):
    require(isinstance(expected, str) and SHA256.fullmatch(expected), 'Invalid APK certificate pin')
    lines = output.splitlines()
    require([line for line in lines if line.startswith('Number of signers:')] == ['Number of signers: 1'],
            'APK verifier did not establish exactly one signer')
    observed = set(apk_signer_digests(output, 'certificate').values())
    require(observed == {expected}, 'APK/APEX certificate does not match its declared role')
    return sorted(observed)


class NativeTools:
    def __init__(self, root, source, scratch, *, projects=None, policy=None):
        self.policy = load_json(DEFAULT_CONFIG) if policy is None else policy
        validate_config(self.policy)
        self.denied_apk_public_keys = set()
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
        result = apk_certificate_digests(output, expected)
        if self.denied_apk_public_keys:
            certificates = apk_signer_digests(output, 'certificate')
            public = apk_signer_digests(output, 'public key')
            require(set(public) == set(certificates), 'APK verifier lacks public key coverage for every signer')
            require(not set(public.values()) & self.denied_apk_public_keys,
                    'Production refuses AOSP public APK test keys')
        return result

    def avb(self, path, expected, *, chains=(), payload=False):
        info = self.run('avbtool', ['info_image', '--image', path])
        require(re.search(r'(?m)^Algorithm:\s+' + re.escape(self.policy['avb_algorithm']) + r'\s*$', info),
                'AVB algorithm is not the role algorithm')
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
    def __init__(self, path, tools, *, production, policy=None, artifact_root=None):
        self.policy = load_json(DEFAULT_CONFIG) if policy is None else policy
        validate_config(self.policy)
        self.root = Path(path).resolve().parent
        self.artifact_root = Path(artifact_root).resolve() if artifact_root is not None else None
        self.inventory = load_json(path)
        fields(self.inventory, {'schema_version', 'certificate_roles', 'avb_public_key', 'factory'})
        require(self.inventory['schema_version'] == 2 and isinstance(self.inventory['certificate_roles'], dict)
                and set(self.inventory['certificate_roles']) == CERT_ROLES,
                'Public key inventory has unknown or missing roles')
        self.tools = tools
        self.files = {}
        self.fingerprints, self.public_ids = {}, {}
        for role, relative in self.inventory['certificate_roles'].items():
            cert = self.file(relative)
            self.fingerprints[role], self.public_ids[role] = tools.certificate(cert)
            info = tools.openssl(['x509', '-in', cert, '-text', '-noout']).decode()
            subject = tools.openssl(['x509', '-in', cert, '-subject', '-noout', '-nameopt', 'RFC2253']).decode().strip()
            require(self.policy['certificate_algorithm'] == 'RSA-4096 with SHA-256'
                    and 'Public-Key: (4096 bit)' in info and 'Signature Algorithm: sha256WithRSAEncryption' in info
                    and subject == 'subject=' + self.policy['certificate_subject'].strip('/'),
                    'Certificate does not match the role policy')
        avb = self.file(self.inventory['avb_public_key'])
        self.avb_id = hashlib.sha256(tools.openssl(['pkey', '-pubin', '-in', avb, '-outform', 'DER'])).hexdigest()
        require(len(set(self.public_ids.values()) | {self.avb_id}) == len(CERT_ROLES) + 1,
                'Public identities are reused across signing roles')
        factory = self.inventory['factory']
        fields(factory, {'allowed_signers', 'identity'})
        allowed = self.file(factory['allowed_signers']).read_text().splitlines()
        require(self.policy['factory_algorithm'] == 'Ed25519' and allowed
                and all(len(line.split()) == 3 and line.split()[0] == factory['identity']
                                and line.split()[1] in {'ssh-ed25519', 'sk-ssh-ed25519@openssh.com'} for line in allowed),
                'Factory authority must be an explicit Ed25519 signer')
        if production:
            self.reject_public_test_keys()

    def file(self, relative):
        path = safe_file(self.root, relative)
        require(self.artifact_root is None or not path.resolve().is_relative_to(self.artifact_root),
                'Expected public material is inside the publication')
        require(path.stat().st_size <= 1024 * 1024, 'Public key input exceeds its bounds')
        require(b'PRIVATE KEY' not in path.read_bytes(), 'Verifier received private key material')
        digest = sha256_file(path)
        require(relative not in self.files or self.files[relative] == digest, 'Public key input changed during verification')
        self.files[relative] = digest
        return path

    def fingerprint(self, role, logical):
        if role == 'PRESIGNED':
            rule = presigned_rule(self.policy, logical)
            require(rule is not None and len(rule['certificate_sha256']) == 1,
                    'Presigned package has no exact reviewed signer')
            return rule['certificate_sha256'][0]
        return self.tools.certificate(self.cert(role, logical))[0]

    def cert(self, role, logical):
        require(role in CERT_ROLES, 'Unknown certificate role')
        return self.file(self.inventory['certificate_roles'][role])

    def avb(self, logical=None):
        if logical is not None:
            raise SigningError('Presigned APEX payload has no reviewed authority')
        return self.file(self.inventory['avb_public_key'])

    def reject_public_test_keys(self):
        source = self.tools.source
        certificates = list((source / 'build/make/target/product/security').glob('*.x509.pem'))
        certificates += list((source / 'system/apex').rglob('*.x509.pem'))
        require(certificates, 'Public test certificate deny list is unavailable')
        pairs = [self.tools.certificate(cert) for cert in certificates]
        denied = {public for _certificate, public in pairs}
        approved = {digest for rule in self.policy['presigned_packages'] for digest in rule['certificate_sha256']}
        require(not set(self.public_ids.values()) & denied
                and not approved & {digest for digest, _public in pairs},
                'Production refuses AOSP public test certificates')
        self.tools.denied_apk_public_keys = denied
        test_keys = list((source / 'external/avb/test/data').glob('testkey_rsa*.pem'))
        require(test_keys, 'Public AVB test key deny list is unavailable')
        expected = set()
        public_keys = [self.avb()]
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

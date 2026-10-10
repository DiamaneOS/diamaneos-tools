"""Check certificate substitution and SELinux MAC selectors, not APK signatures."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
import xml.etree.ElementTree as ET

from . import signing_archive as za
from .signing_inputs import CERT_ROLES, SHA256, SigningError, canonical_sha256, fields, file_stamp, require

MAX_XML = 2 * 1024 * 1024
MAX_FILES = 16
MAX_CERTIFICATE = 16 * 1024
MAX_CERTIFICATES = 256
VALUE = re.compile(r'[A-Za-z0-9_.]{1,255}\Z')


@contextmanager
def _opened(path):
    value = os.fspath(path)
    require(isinstance(value, str) and value == os.path.normpath(value)
            and '..' not in PurePosixPath(value).parts and '\x00' not in value, 'Unsafe MAC input path')
    path = Path(os.path.abspath(path))
    directory = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                    and before.st_size <= za.MAX_EXPANDED, 'MAC input is not a bounded single-link regular file')
            yield stream
            require(file_stamp(before, ownership=True) == file_stamp(os.fstat(stream.fileno()), ownership=True)
                    == file_stamp(os.stat(path.name, dir_fd=directory, follow_symlinks=False), ownership=True)
                    == file_stamp(os.stat(path, follow_symlinks=False), ownership=True), 'MAC input changed during verification')
    finally:
        os.close(directory)


def _read(path, snapshots):
    with _opened(path) as stream:
        require(os.fstat(stream.fileno()).st_size <= MAX_CERTIFICATE, 'Public certificate exceeds its bound')
        raw = stream.read(MAX_CERTIFICATE + 1)
        require(len(raw) <= MAX_CERTIFICATE and b'PRIVATE KEY' not in raw, 'Invalid public-only certificate input')
        snapshot = file_stamp(os.fstat(stream.fileno()), ownership=True)
    name = str(Path(os.path.abspath(path)))
    require(name not in snapshots or snapshots[name] == snapshot, 'Public certificate input changed')
    require(len(snapshots) < MAX_CERTIFICATES or name in snapshots, 'Too many public certificate inputs')
    snapshots[name] = snapshot
    return raw


def _der(tools, raw, encoding):
    value = tools.openssl(['x509', '-inform', encoding, '-outform', 'DER'], data=raw)
    require(isinstance(value, bytes) and 0 < len(value) <= MAX_CERTIFICATE, 'Native X.509 conversion failed')
    require(encoding != 'DER' or value == raw, 'MAC certificate is not exact canonical DER')
    return value


class _Builder(ET.TreeBuilder):
    def __init__(self):
        super().__init__()
        self.depth = self.nodes = 0

    def start(self, tag, attributes):
        self.depth += 1
        self.nodes += 1
        require(self.depth <= 4 and self.nodes <= 8192, 'MAC XML exceeds structural bounds')
        return super().start(tag, attributes)

    def end(self, tag):
        value = super().end(tag)
        self.depth -= 1
        return value

    def doctype(self, *args):
        raise SigningError('MAC XML DTDs are prohibited')

    def pi(self, *args):
        raise SigningError('MAC XML processing instructions are prohibited')


def _empty(node):
    require(not (node.text or '').strip() and not (node.tail or '').strip(), 'MAC XML contains unexpected text')


def _attribute(node, tag, name):
    require(node.tag == tag and set(node.attrib) == {name}, 'Unsupported MAC XML element or attributes')
    _empty(node)
    value = node.attrib[name]
    require(VALUE.fullmatch(value), 'Invalid MAC package or seinfo selector')
    return value


def _policies(raw, certificates):
    require(len(raw) <= MAX_XML, 'MAC XML exceeds its byte bound')
    text = raw.decode('utf-8', 'strict')
    # Comments have no selectors. ET still validates their syntax; its doctype
    # handler rejects DTDs even if comment-like text appears inside a declaration.
    content = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
    require('\x00' not in text and '&' not in content, 'MAC XML entities or non-UTF-8 encoding are prohibited')
    root = ET.fromstring(text, parser=ET.XMLParser(target=_Builder()))
    require(root.tag == 'policy' and not root.attrib, 'Unsupported MAC XML root')
    _empty(root)
    result = []

    def certificate(value):
        require(isinstance(value, str) and 0 < len(value) <= 2 * MAX_CERTIFICATE
                and len(value) % 2 == 0 and re.fullmatch('[0-9a-fA-F]+', value), 'Invalid literal MAC certificate')
        raw = bytes.fromhex(value)
        digest = hashlib.sha256(raw).hexdigest()
        if digest not in certificates:
            require(len(certificates) < MAX_CERTIFICATES, 'Too many MAC certificates')
            certificates[digest] = raw
        require(certificates[digest] == raw, 'Conflicting MAC certificate identity')
        return digest

    for signer in root:
        require(signer.tag == 'signer' and set(signer.attrib) <= {'signature'}, 'Unsupported MAC signer')
        _empty(signer)
        certs = [certificate(signer.attrib['signature'])] if 'signature' in signer.attrib else []
        global_label, packages = '', {}
        for child in signer:
            if child.tag == 'cert':
                require(set(child.attrib) == {'signature'} and not len(child), 'Unsupported MAC certificate element')
                _empty(child)
                certs.append(certificate(child.attrib['signature']))
            elif child.tag == 'seinfo':
                require(not len(child), 'Nested MAC global seinfo')
                value = _attribute(child, 'seinfo', 'value')
                require(not global_label or global_label == value, 'Conflicting MAC global seinfo')
                global_label = value
            else:
                name = _attribute(child, 'package', 'name')
                require(len(child), 'Missing MAC package mapping')
                values = set()
                for label in child:
                    require(not len(label), 'Unsupported nested MAC seinfo')
                    values.add(_attribute(label, 'seinfo', 'value'))
                require(len(values) == 1, 'Conflicting MAC package seinfo')
                value = values.pop()
                require(name not in packages or packages[name] == value, 'Conflicting MAC package mapping')
                packages[name] = value
        require(certs and len(certs) <= 16, 'Missing or excessive MAC certificates')
        require(bool(global_label) != bool(packages), 'MAC signer needs global seinfo XOR package mappings')
        result.append((tuple(sorted(set(certs))), global_label, tuple(sorted(packages.items()))))
    return sorted(result)


def _unique(policies):
    global_selectors, package_selectors = set(), set()
    for certs, label, packages in policies:
        if label:
            require(certs not in global_selectors, 'Repeated MAC global certificate selector')
            global_selectors.add(certs)
        for name, _ in packages:
            key = certs, name
            require(key not in package_selectors, 'Repeated or conflicting MAC package selector')
            package_selectors.add(key)


def _replacements(handle, metadata, roles, public, tools, certificates, snapshots):
    require(za.metadata_members(handle) == metadata, 'Unsigned package metadata differs from the supplied mapping')
    require(isinstance(roles, dict), 'Missing exact package roles')
    destinations, kept = {}, set()

    def destination(role, logical=''):
        require(role in CERT_ROLES, 'Unknown MAC destination certificate role')
        if role not in destinations:
            raw = _der(tools, _read(public.cert(role, logical), snapshots), 'PEM')
            destinations[role] = hashlib.sha256(raw).hexdigest()
            require(len(set(destinations.values())) == len(destinations), 'MAC destination certificate roles are reused')
        return destinations[role]

    # These are independent policy approvals, never pins obtained from an APK or
    # the unsigned XML. Their package constraints remain the package verifier's job.
    for rule in public.policy['presigned_packages']:
        pins = rule['certificate_sha256']
        require(isinstance(pins, list) and len(pins) == 1 and isinstance(pins[0], str)
                and SHA256.fullmatch(pins[0]), 'Presigned MAC authority lacks one exact external pin')
        kept.add(pins[0])

    mapped = {}

    def source(relative, role, *, optional=False):
        path = PurePosixPath(relative)
        require(not path.is_absolute() and '..' not in path.parts and str(path) == relative
                and '\\' not in relative and relative.endswith('.x509.pem'), 'Unsafe original public certificate selector')
        try:
            raw = _read(Path(tools.source) / relative, snapshots)
        except FileNotFoundError:
            require(optional, 'Original public certificate mapping is missing')
            return
        old, new = hashlib.sha256(_der(tools, raw, 'PEM')).hexdigest(), destination(role)
        require(old not in mapped or mapped[old] == new, 'Conflicting original MAC certificate roles')
        mapped[old] = new

    misc = za.properties(za.read_text(handle, 'META/misc_info.txt'))
    prefix = PurePosixPath(misc.get('default_system_dev_certificate', 'build/make/target/product/security/testkey')).parent
    require(public.policy['apk_selectors'].get('testkey') == 'releasekey', 'Unsupported MAC default signing selector')
    # Exactly the native -d BuildKeyMap defaults. --extra_apks affects package
    # signatures, not ReplaceCerts; it must not invent extra MAC key mappings.
    selectors = {role: role for role in CERT_ROLES - {'releasekey'}}
    selectors.update(testkey='releasekey', devkey='releasekey')
    for selector, role in sorted(selectors.items()):
        require(VALUE.fullmatch(selector), 'Unknown MAC source selector')
        source(str(prefix / (selector + '.x509.pem')), role, optional=True)
    for name, role in roles.items():
        fields(role, {'kind', 'container', 'payload'})
        require(role['kind'] in {'apk', 'apex'} and name in metadata[role['kind']]
                and role['container'] in CERT_ROLES | {'PRESIGNED'}, 'Unknown planned MAC role')
        if role['container'] == 'PRESIGNED':
            require(sum(rule['metadata_name'] == name for rule in public.policy['presigned_packages']) == 1,
                    'Presigned MAC package lacks its exact external policy rule')
    for old in certificates:
        if old in kept:
            _der(tools, certificates[old], 'DER')
            require(old not in mapped or mapped[old] == old, 'Presigned MAC certificate would be replaced')
            mapped[old] = old
        require(old in mapped, 'MAC certificate has no independently approved replacement or preservation mapping')
    return mapped


def _inventory(handle):
    names = {i.filename for i in handle.infolist() if i.filename.endswith('mac_permissions.xml')}
    require(names and len(names) <= MAX_FILES, 'MAC policy file inventory is missing or exceeds its bound')
    return names


def _read_policies(handle, names):
    result, certificates = {}, {}
    for name in sorted(names):
        info = handle.getinfo(name)
        mode = info.external_attr >> 16
        require(info.file_size <= MAX_XML and not info.is_dir()
                and (not stat.S_IFMT(mode) or stat.S_ISREG(mode)), 'MAC policy is oversized or not a regular member')
        result[name] = _policies(handle.read(name), certificates)
    require(certificates, 'MAC policy has no certificate selectors')
    _unique([p for values in result.values() for p in values])
    return result, certificates


def _snapshot(expected):
    require(isinstance(expected, dict) and 0 < len(expected) <= MAX_FILES, 'Missing expected MAC snapshot')
    result = {}
    for name, policies in expected.items():
        path = PurePosixPath(name)
        require(not path.is_absolute() and '..' not in path.parts and str(path) == name
                and '\\' not in name and name.endswith('mac_permissions.xml'), 'Unsafe expected MAC member')
        require(isinstance(policies, list) and len(policies) <= 8192, 'Expected MAC policies exceed their bound')
        rows, nodes = [], 1
        for policy in policies:
            fields(policy, {'certificates', 'seinfo', 'packages'})
            certs, label, packages = policy['certificates'], policy['seinfo'], policy['packages']
            require(isinstance(certs, list) and 0 < len(certs) <= 16
                    and all(isinstance(c, str) and SHA256.fullmatch(c) for c in certs)
                    and len(set(certs)) == len(certs), 'Invalid expected MAC certificate set')
            require(label is None or isinstance(label, str) and VALUE.fullmatch(label), 'Invalid expected MAC seinfo')
            require(isinstance(packages, dict) and len(packages) <= 4096
                    and all(isinstance(k, str) and VALUE.fullmatch(k) and isinstance(v, str) and VALUE.fullmatch(v)
                            for k, v in packages.items()),
                    'Invalid expected MAC package selectors')
            require(bool(label) != bool(packages), 'Expected MAC policy needs global seinfo XOR package mappings')
            nodes += 1 + 2 * len(packages) + bool(label)
            require(nodes <= 8192, 'Expected MAC snapshot exceeds structural bounds')
            rows.append((tuple(sorted(certs)), label or '', tuple(sorted(packages.items()))))
        result[name] = sorted(rows)
    require(any(result.values()), 'Expected MAC snapshot has no selectors')
    _unique([p for values in result.values() for p in values])
    return result


def prepare_mac_permissions(original, metadata, role_map, keys, tools):
    """Build a JSON-ready plan snapshot using native X.509 and external public trust.

    Inputs are the verified unsigned target-files, za.metadata(original), the
    metadata-name role map, PublicKeys, and source-bound NativeTools. No private
    material is read. Parent must authenticate the snapshot as part of its plan.
    """
    try:
        binding = canonical_sha256([metadata, role_map, keys.policy, keys.inventory, keys.files])
        snapshots = {}
        with _opened(original) as stream, za.archive(stream) as handle:
            policies, certificates = _read_policies(handle, _inventory(handle))
            replacements = _replacements(handle, metadata, role_map, keys, tools, certificates, snapshots)
            expected = {name: [{'certificates': sorted(replacements[c] for c in certs), 'seinfo': label or None,
                                'packages': dict(packages)} for certs, label, packages in rows]
                        for name, rows in policies.items()}
            _snapshot(expected)
        for path, snapshot in snapshots.items():
            with _opened(path) as stream:
                require(file_stamp(os.fstat(stream.fileno()), ownership=True) == snapshot, 'MAC public certificate material changed')
        require(binding == canonical_sha256([metadata, role_map, keys.policy, keys.inventory, keys.files]), 'MAC preparation inputs changed')
        return expected
    except (OSError, UnicodeError, ET.ParseError, KeyError, TypeError, AttributeError, ValueError, RecursionError) as error:
        if isinstance(error, SigningError): raise
        raise SigningError('Unreadable or unsupported MAC preparation input') from None


def verify_mac_permissions(signed, expected):
    """Check the final XML against a trusted prepared plan snapshot, without tools.

    Literal certificate bytes must match the native-validated identities prepared
    earlier. This postcondition does not verify APK signatures or running SELinux.
    """
    try:
        binding = canonical_sha256(expected)
        policies = _snapshot(expected)
        with _opened(signed) as stream, za.archive(stream) as handle:
            names = _inventory(handle)
            require(names == set(policies), 'MAC policy file inventory changed')
            actual, _ = _read_policies(handle, names)
            require(actual == policies, 'Signed MAC selectors, seinfo or certificate replacements differ')
        require(binding == canonical_sha256(expected), 'Expected MAC snapshot changed during verification')
        rows = [p for values in policies.values() for p in values]
        return {'files': len(names), 'signers': len(rows), 'global_selectors': sum(bool(p[1]) for p in rows),
                'package_selectors': sum(len(p[2]) for p in rows), 'certificate_selectors': sum(len(p[0]) for p in rows)}
    except (OSError, UnicodeError, ET.ParseError, KeyError, TypeError, AttributeError, ValueError, RecursionError) as error:
        if isinstance(error, SigningError): raise
        raise SigningError('Unreadable or unsupported MAC verification input') from None

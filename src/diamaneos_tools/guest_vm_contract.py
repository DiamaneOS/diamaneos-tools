# Copyright (C) 2026 DiamaneOS
"""Protected guest contract, shared by the signer and diamaneos-tools.

The tools copy is vendored byte for byte from Virtualization build/apex.
Role digests below identify the build outputs of the 2026-11-27 folded sources, per variant. The compiled
signer replaces them with digests derived directly from its Soong producers.
No digest from the input APEX is trusted as the role identity.
"""
import argparse
import gzip
import hashlib
import io
import re
import struct
from pathlib import Path

AVB_FOOTER, AVB_HEADER = b'AVBf', b'AVB0'
AVB_HASH_DESCRIPTOR = 2
# avb_crypto.h: algorithm type -> (hash, RSA key bits). Type 0 is an unsigned vbmeta.
AVB_ALGORITHMS = {1: ('sha256', 2048), 2: ('sha256', 4096), 3: ('sha256', 8192),
                  4: ('sha512', 2048), 5: ('sha512', 4096), 6: ('sha512', 8192)}
# The DER DigestInfo prefixes of PKCS#1 v1.5 signatures (RFC 8017 section 9.2).
DIGEST_INFO = {'sha256': bytes.fromhex('3031300d060960864801650304020105000420'),
               'sha512': bytes.fromhex('3051300d060960864801650304020305000440')}


def avb_vbmeta(data: bytes) -> dict:
    """The vbmeta of an image with an AVB footer (avb_vbmeta_image.h): its algorithm type, the signed bytes
    (header and auxiliary block), the hash and signature of its authentication block, its public key, every
    descriptor in order as (tag, partition name, (image size, hash algorithm, salt, digest)) with name and hash None
    for descriptors other than hash descriptors, and the size of the image before the footer."""
    if len(data) < 64 or data[-64:-60] != AVB_FOOTER:
        raise ValueError('no AVB footer')
    original, offset, size = struct.unpack('>QQQ', data[-52:-28])
    if original > offset or offset + size > len(data) - 64 or size < 256:
        raise ValueError('AVB footer points outside the image')
    vbmeta = data[offset:offset + size]
    if vbmeta[:4] != AVB_HEADER:
        raise ValueError('no vbmeta at the AVB footer offset')
    auth, aux = struct.unpack('>QQ', vbmeta[12:28])
    algorithm = struct.unpack('>I', vbmeta[28:32])[0]
    (hash_offset, hash_size, signature_offset, signature_size, key_offset, key_size, _metadata_offset,
     _metadata_size, start, length) = struct.unpack('>10Q', vbmeta[32:112])
    if 256 + auth + aux > len(vbmeta):
        raise ValueError('vbmeta blocks outside the vbmeta')
    auth_block, block = vbmeta[256:256 + auth], vbmeta[256 + auth:256 + auth + aux]
    if (hash_offset + hash_size > auth or signature_offset + signature_size > auth or key_offset + key_size > aux
            or start + length > aux):
        raise ValueError('vbmeta fields outside their blocks')
    descriptors, position, end = [], start, start + length
    while position < end:
        if position + 16 > end:
            raise ValueError('truncated vbmeta descriptor')
        tag, following = struct.unpack('>QQ', block[position:position + 16])
        body = block[position + 16:position + 16 + following]
        if len(body) != following or position + 16 + following > end:
            raise ValueError('truncated vbmeta descriptor')
        if tag == AVB_HASH_DESCRIPTOR:
            if following < 116:
                raise ValueError('truncated hash descriptor')
            image_size = struct.unpack('>Q', body[:8])[0]
            algorithm_name = body[8:40].rstrip(b'\0').decode('ascii', 'replace')
            name_length, salt_length, digest_length = struct.unpack('>III', body[40:52])
            at = 116
            if at + name_length + salt_length + digest_length > following:
                raise ValueError('truncated hash descriptor')
            name = body[at:at + name_length].decode('utf-8', 'replace')
            salt = body[at + name_length:at + name_length + salt_length]
            digest = body[at + name_length + salt_length:at + name_length + salt_length + digest_length]
            descriptors.append((tag, name, (image_size, algorithm_name, salt, digest)))
        else:
            descriptors.append((tag, None, None))
        position += 16 + following
    return {'algorithm': algorithm, 'signed': vbmeta[:256] + block,
            'hash': auth_block[hash_offset:hash_offset + hash_size],
            'signature': auth_block[signature_offset:signature_offset + signature_size],
            'key': block[key_offset:key_offset + key_size], 'descriptors': descriptors, 'original': original}


def avb_signature_problem(meta: dict) -> str:
    """Why a vbmeta's signature does not verify with its own public key, or '' when it does: the hash of its header
    and auxiliary block, and RSA PKCS#1 v1.5 with exponent 65537 over that hash, as libavb checks it."""
    if meta['algorithm'] not in AVB_ALGORITHMS:
        return 'not signed' if meta['algorithm'] == 0 else f'unknown AVB algorithm {meta["algorithm"]}'
    hash_name, bits = AVB_ALGORITHMS[meta['algorithm']]
    size, key = bits // 8, meta['key']
    if len(key) != 8 + 2 * size or struct.unpack('>I', key[:4])[0] != bits:
        return f'its public key is not an RSA-{bits} key'
    modulus = int.from_bytes(key[8:8 + size], 'big')
    digest = hashlib.new(hash_name, meta['signed']).digest()
    if meta['hash'] != digest:
        return 'its vbmeta hash does not match'
    if len(meta['signature']) != size or not modulus:
        return 'its signature has the wrong size'
    signature = int.from_bytes(meta['signature'], 'big')
    if signature >= modulus:
        return 'its signature is outside the canonical RSA range'
    message = pow(signature, 65537, modulus).to_bytes(size, 'big')
    tail = DIGEST_INFO[hash_name] + digest
    if message != b'\0\1' + b'\xff' * (size - len(tail) - 3) + b'\0' + tail:
        return 'its signature does not verify'
    return ''


def hash_descriptor_matches(descriptor: tuple, content: bytes) -> bool:
    size, algorithm, salt, digest = descriptor
    if algorithm != 'sha256':
        return False
    return size == len(content) and hashlib.new(algorithm, salt + content).digest() == digest



# mode, link count, device major and minor. uid and gid are always zero.
FILES = {
    'decoder_vm': {
        'dev': (0o40755, 2, 0, 0), 'dev/console': (0o20600, 1, 5, 1),
        'proc': (0o40755, 2, 0, 0), 'init': (0o100755, 1, 0, 0),
    },
    'fsvm': {
        **{name: (0o40755, 1, 0, 0) for name in ('bin', 'dev', 'mnt')},
        **{name: (0o40555, 1, 0, 0) for name in ('proc', 'sys')},
        **{name: (0o100755, 1, 0, 0) for name in
           ('init', 'bin/fsck.exfat', 'bin/fsck_msdos', 'bin/mkfs.exfat', 'bin/newfs_msdos')},
    },
}
ROLE_DIGESTS = {
    'decoder_vm': (5575680, '19503656eb0f4b168e42f880f281eadf8314d1bd6322b44b20dd0e0b7e2bdbc0'),
    'fsvm': (1886208, '51cd76155eebeff1af5494057f62bb67caf3109f58aaa54494f3e6cded84bb13'),
}
# The same sources built as userdebug: the VM programs carry the debuggable build's settings (their filters log).
# fsvm: the filesystem VM's guest whose accept loop waits without a timeout (an idle VM keeps no timer of its own).
ROLE_DIGESTS_USERDEBUG = {
    'decoder_vm': (5580288, 'dd7f3572d31dde05f09d30917355815a6cd1a35416eefaae42f3ee834e69b4f3'),
    'fsvm': (1886720, '350d2c4c9ef4e6c28145283c9f8536e6b0508692a54dab2d2f9e59f5642e9de8'),
}


def role_digests(variant):
    """The role digests of a build variant: userdebug's own, user's for every other variant."""
    return ROLE_DIGESTS_USERDEBUG if variant == 'userdebug' else ROLE_DIGESTS
DESCRIPTORS = ('boot', 'initrd_normal', 'initrd_debug')
MAX_INITRD_BYTES = 64 << 20


def cpio_entries(data):
    if len(data) > MAX_INITRD_BYTES:
        raise ValueError('initramfs too large')
    if data[:2] == b'\x1f\x8b':
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
            data = stream.read(MAX_INITRD_BYTES + 1)
        if len(data) > MAX_INITRD_BYTES:
            raise ValueError('initramfs too large')
    entries, at = {}, 0
    while True:
        header = data[at:at + 110]
        if len(header) != 110 or header[:6] != b'070701' or not re.fullmatch(b'[0-9a-fA-F]{104}', header[6:]):
            raise ValueError('initramfs holds data that is not a newc cpio archive')
        fields = [int(header[i:i + 8], 16) for i in range(6, 110, 8)]
        size, name_size = fields[6], fields[11]
        raw = data[at + 110:at + 110 + name_size]
        if not name_size or len(raw) != name_size or raw[-1:] != b'\0' or b'\0' in raw[:-1]:
            raise ValueError('truncated or noncanonical cpio member name')
        name = raw[:-1].decode('utf-8', 'strict')
        if name != 'TRAILER!!!' and (not name or name.startswith('/') or any(p in ('', '.', '..') for p in name.split('/'))):
            raise ValueError('noncanonical cpio path')
        start = (at + 110 + name_size + 3) & ~3
        end = start + size
        next_at = (end + 3) & ~3
        if next_at > len(data):
            raise ValueError('truncated cpio member payload or padding')
        if fields[12] != 0:
            raise ValueError('unexpected newc checksum')
        if name == 'TRAILER!!!':
            if size or data[next_at:].strip(b'\0'):
                raise ValueError('nonempty trailer or data after the cpio trailer')
            return entries
        if name in entries:
            raise ValueError('duplicate cpio member: ' + name)
        entries[name] = (fields, data[start:end])
        at = next_at


def cpio_members(data):
    return list(cpio_entries(data))


def initrd_problem(role, data, digests=None):
    entries = cpio_entries(data)
    if set(entries) != set(FILES[role]):
        extra = sorted(set(entries) - set(FILES[role]))
        missing = sorted(set(FILES[role]) - set(entries))
        return f'not the {role} initramfs, extra {", ".join(extra)}, missing {", ".join(missing)}'
    for name, (fields, body) in entries.items():
        mode, links, major, minor = FILES[role][name]
        if (fields[1], fields[2], fields[3], fields[4], fields[9], fields[10]) != (mode, 0, 0, links, major, minor):
            return f'{name} has unexpected file metadata'
        if mode & 0o170000 == 0o100000:
            if not body.startswith(b'\x7fELF'):
                return f'{name} is not an ELF executable'
        elif body:
            return f'{name} has unexpected payload data'
    size, digest = (ROLE_DIGESTS if digests is None else digests)[role]
    if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
        return f'{role} initramfs does not match its independent build role digest'
    return ''


def descriptor_problems(meta, kernel, initrd):
    descriptors = meta['descriptors']
    if sorted((tag, name) for tag, name, _ in descriptors) != sorted((2, name) for name in DESCRIPTORS):
        return ['descriptors are not one each of boot, initrd_normal, initrd_debug']
    content = {'boot': kernel[:meta['original']], 'initrd_normal': initrd, 'initrd_debug': initrd}
    return [f'{name} descriptor does not match the full SHA-256 image' for _, name, desc in descriptors
            if not hash_descriptor_matches(desc, content[name])]


def emit_build_digests():
    parser = argparse.ArgumentParser()
    parser.add_argument('--decoder-vm', required=True)
    parser.add_argument('--fsvm', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    values = {}
    for role, filename in [('decoder_vm', args.decoder_vm), ('fsvm', args.fsvm)]:
        data = Path(filename).read_bytes()
        # Producer output must meet the same structural contract before its digest is carried.
        digest = (len(data), hashlib.sha256(data).hexdigest())
        problem = initrd_problem(role, data, {role: digest})
        if problem:
            raise ValueError(problem)
        values[role] = digest
    Path(args.out).write_text('# Generated from independent Soong initramfs outputs.\nROLE_DIGESTS = ' + repr(values) + '\n')


if __name__ == '__main__':
    emit_build_digests()

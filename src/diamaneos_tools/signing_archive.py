"""Bounded archive and Android metadata handling, without signature parsing."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path, PurePosixPath
import shlex
import shutil
import stat
import zipfile

from .signing_inputs import NAME, SigningError, require, sha256_stream

MAX_MEMBERS = 250_000
MAX_EXPANDED = 64 * 1024 ** 3
MAX_TEXT = 32 * 1024 ** 2


@contextmanager
def archive(path, *, allow_links=True):
    try:
        with zipfile.ZipFile(path) as handle:
            infos = handle.infolist()
            require(len(infos) <= MAX_MEMBERS and sum(i.file_size for i in infos) <= MAX_EXPANDED,
                    'Archive exceeds its bounds')
            names = set()
            for info in infos:
                name = PurePosixPath(info.filename.rstrip('/'))
                require(not name.is_absolute() and '..' not in name.parts and '\\' not in info.filename
                        and str(name) == info.filename.rstrip('/') and info.filename not in names
                        and not info.flag_bits & 1, 'Unsafe or duplicate archive member')
                require(allow_links or not stat.S_ISLNK(info.external_attr >> 16), 'Archive symlinks are prohibited')
                names.add(info.filename)
            yield handle
    except (OSError, zipfile.BadZipFile, RuntimeError, KeyError):
        raise SigningError('Unreadable or incomplete ZIP archive') from None


def read_text(handle, name):
    require(not stat.S_ISLNK(handle.getinfo(name).external_attr >> 16), 'Metadata is symlinked')
    require(handle.getinfo(name).file_size <= MAX_TEXT, 'Metadata exceeds its bounds')
    try:
        return handle.read(name).decode('utf-8')
    except UnicodeError:
        raise SigningError('Metadata is not UTF-8') from None


def extract(handle, name, destination):
    require(not stat.S_ISLNK(handle.getinfo(name).external_attr >> 16), 'Selected payload is symlinked')
    destination = Path(destination)
    with handle.open(name) as source, destination.open('xb') as target:
        shutil.copyfileobj(source, target, 1024 * 1024)
    return destination


def digest_member(handle, name):
    with handle.open(name) as stream:
        return sha256_stream(stream)


def attributes(text):
    result = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        tokens = shlex.split(line)
        require(all('=' in token for token in tokens), 'Malformed package metadata')
        pairs = [token.split('=', 1) for token in tokens]
        record = dict(pairs)
        require(len(record) == len(pairs) and NAME.fullmatch(record.get('name', ''))
                and record['name'] not in result, 'Duplicate or unsafe package metadata')
        result[record['name']] = record
    return result


def properties(text):
    result = {}
    for line in text.splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        require('=' in line, 'Malformed key/value metadata')
        key, value = line.split('=', 1)
        require(key not in result or result[key] == value, 'Conflicting metadata values')
        result[key] = value
    return result


def metadata_members(handle):
    result = {}
    for kind, name in (('apk', 'META/apkcerts.txt'), ('apex', 'META/apexkeys.txt')):
        result[kind] = {package: {('source_key_selector' if key == 'private_key' else key): value
                                for key, value in record.items()}
                        for package, record in attributes(read_text(handle, name)).items()}
    return result


def metadata(path):
    with archive(path) as handle:
        return metadata_members(handle)


def package_name(logical):
    name = Path(logical).name
    return name.removesuffix('.capex') + '.apex' if name.endswith('.capex') else name


def basename_role(value):
    name = PurePosixPath(value).name
    for suffix in ('.x509.pem', '.pk8', '.avbpubkey', '.pem'):
        if name.endswith(suffix):
            return name[:-len(suffix)]
    return name


def package_metadata_shape(value):
    """Keep package membership and storage properties, not rewritten key paths."""
    return {kind: {name: {key: record[key] for key in ('compressed', 'partition') if key in record}
                   for name, record in value[kind].items()} for kind in ('apk', 'apex')}


def unpack_tools(path, destination):
    destination.mkdir(mode=0o700)
    with archive(path, allow_links=False) as handle:
        for info in handle.infolist():
            target = destination / info.filename
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            extract(handle, info.filename, target)
            target.chmod(0o700 if info.external_attr >> 16 & 0o111 else 0o600)
    return destination

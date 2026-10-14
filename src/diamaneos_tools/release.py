"""Make a release from a verified official image set.

The release directory holds what DiamaneOS publishes for one build and what
it keeps about it:

  publish/     the factory package the user flashes, the full OTA package
               (and an incremental one with --incremental-from) and one file
               per update channel, named as the Updater asks for them
  provenance/  build.json, the resolved manifest, the image set's SHA256SUMS,
               the verify report and release.json, which records the hash of
               every file and the commits the release comes from
  <device>-target_files-<build number>.zip, kept for the next release's
               incremental OTA package and never published

It refuses an image set that is not an official, reproducible user build,
did not pass verify, lacks a file or holds an unknown one, or whose files do
not match their hashes. A release is signed with the public test keys its
images carry, as every build of these tools is. A build.json that claims other
signing and an OTA package signed with another key are refused, and nothing
here writes a signature.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sys
import zipfile

from . import build_workspace as bw
from . import flash_all, image_package, image_verify, product_inputs
from .build_workspace import Action
from .signing_inputs import SigningError

CHANNELS = ('alpha', 'beta', 'stable')
# The update information the Updater reads (DownloadChecks.metadata): one line
# "<build number> <build date> <device> <channel>" of at most 4096 bytes.
CHANNEL_LINE = re.compile(r'[A-Za-z0-9_.-]{1,128} [0-9]{1,18} [A-Za-z0-9_-]{1,64} [A-Za-z0-9_-]{1,64}')
MAX_CHANNEL_BYTES = 4096
# The largest package the Updater downloads (DownloadChecks.MAX_PACKAGE_BYTES).
MAX_OTA_BYTES = 8 * 1024 ** 3
DEVICE = re.compile(r'[A-Za-z0-9_-]{1,64}')
BUILD_NUMBER = re.compile(r'[A-Za-z0-9._-]{1,64}')
FILE_NAME = re.compile(r'[A-Za-z0-9_][A-Za-z0-9._+-]{0,254}')
RECORD = 'release.json'
LOG = 'release.log'
IMAGE_CHECKS = bw.ROOT / 'config/fp6-image-checks.json'
OTA_METADATA = 'META-INF/com/android/metadata'
OTA_CERTIFICATE = 'META-INF/com/android/otacert'


class ReleaseError(Exception):
    pass


class PublicTestKeys:
    """The signing of every image set these tools make: the public test keys.

    A signer names the key ota_from_target_files signs with and checks what an
    OTA package says about its signing. Anyone can sign with the public test
    keys, so they give a release no signature, and a phone running such a
    build checks for updates but installs none. Release keys need a signer of
    their own here, one that also checks the signatures it adds.
    """
    name = 'public-test-keys'
    key = 'build/make/target/product/security/testkey'

    def ota_key(self, misc_info: dict) -> str:
        """The key the build's own OTA settings name, which must be the public test key."""
        if misc_info.get('default_system_dev_certificate') != self.key:
            raise ReleaseError('the target-files archive names the OTA key '
                               f'{misc_info.get("default_system_dev_certificate")!r}, not the public test key '
                               f'{self.key}. This command signs only with the public test keys')
        return self.key

    def check_ota(self, metadata: dict, certificate: bytes, tools: Path) -> None:
        if not metadata.get('post-build', '').endswith('/test-keys'):
            raise ReleaseError('the OTA package is not for a build signed with the public test keys')
        if certificate != (tools / (self.key + '.x509.pem')).read_bytes():
            raise ReleaseError('the OTA package carries another certificate than the public test key')


SIGNERS = {PublicTestKeys.name: PublicTestKeys}


@dataclass
class ImageSet:
    directory: Path
    record: dict
    sums: dict
    sums_sha256: str
    report: dict
    report_path: Path
    signer: PublicTestKeys
    tools: dict

    @property
    def product(self) -> str:
        return self.record['product']

    @property
    def build_number(self) -> str:
        return self.record['build_number']

    @property
    def name(self) -> str:
        return f'{self.product}-{self.build_number}'


def require(condition, message: str) -> None:
    if not condition:
        raise ReleaseError(message)


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ------------------------------------------------------------ the image set

def listed_files(record: dict) -> dict:
    """Every file build.json says the set holds, with the hash it records (None
    for the files it records no hash for)."""
    files = {}

    def add(name, digest):
        require(isinstance(name, str) and FILE_NAME.fullmatch(name), f'build.json names an unsafe file: {name!r}')
        require(files.get(name, digest) == digest, f'build.json gives {name} two hashes')
        files[name] = digest

    for name in (image_package.RECORD, image_package.SUMS, flash_all.SH, flash_all.BAT):
        add(name, None)
    try:
        for name, digest in record['images'].items():
            add(f'{name}.img', digest)
        for image in record['wipe']['images'].values():
            add(image['file'], image['sha256'])
        carried = record.get('firmware') or {}
        for name, digest in (carried.get('images') or {}).items():
            add(name, digest)
        for image in (carried.get('reset') or {}).values():
            add(image['file'], image['sha256'])
        add(record['target_files']['file'], record['target_files']['sha256'])
        if record.get('otatools'):
            add(record['otatools']['file'], record['otatools']['sha256'])
        add(record['manifest']['file'], record['manifest']['resolved_sha256'])
    except (KeyError, TypeError, AttributeError) as error:
        raise ReleaseError(f'build.json does not name its files as build package writes them ({error})') from None
    return files


def check_files(directory: Path, record: dict) -> dict:
    """The set holds exactly the files build.json names, each matching
    SHA256SUMS, and SHA256SUMS agrees with build.json. Returns SHA256SUMS."""
    listed = listed_files(record)
    present = {}
    for path in directory.iterdir():
        require(path.is_file() and not path.is_symlink(), f'{path.name} in the image set is not a plain file')
        present[path.name] = path
    missing = sorted(set(listed) - set(present))
    require(not missing, 'the image set lacks ' + ', '.join(missing))
    unknown = sorted(set(present) - set(listed))
    require(not unknown, 'the image set holds files build.json does not name: ' + ', '.join(unknown))
    try:
        sums = image_package.read_sums(directory)
    except bw.BuildStepError as error:
        raise ReleaseError(str(error)) from None
    require(set(sums) == set(listed) - {image_package.SUMS}, 'SHA256SUMS does not list exactly the files of the set')
    changed = sorted(name for name, digest in sums.items() if bw.sha_file(directory / name) != digest)
    require(not changed, 'these files do not match SHA256SUMS: ' + ', '.join(changed))
    disagree = sorted(name for name, digest in listed.items() if digest is not None and sums[name] != digest)
    require(not disagree, 'build.json and SHA256SUMS give other hashes for ' + ', '.join(disagree))
    scripts = flash_all.scripts(record)
    stale = sorted(name for name, text in scripts.items() if (directory / name).read_bytes() != text.encode())
    require(not stale, f'{" and ".join(stale)} {"differs" if len(stale) == 1 else "differ"} from the scripts '
                       'these tools write for this build.json. Package the set again with these tools')
    return sums


def check_build(record: dict) -> PublicTestKeys:
    """An official, reproducible user build that claims no signing nothing here can check."""
    require(record.get('official') is True, 'the image set is not an official build ("build all --official")')
    require(record.get('variant') == 'user', f'a release is a user build, not {record.get("variant")}')
    require(record.get('reproducible') is True,
            'build.json marks the build not reproducible (built by hand, local changes, deviations, vendor files '
            'from an earlier source or other tools than the source\'s)')
    require(record.get('otatools'), 'build.json names no otatools archive, which the OTA package needs')
    require(isinstance(record.get('product'), str) and DEVICE.fullmatch(record['product']),
            'build.json names no valid product')
    require(isinstance(record.get('build_number'), str) and BUILD_NUMBER.fullmatch(record['build_number']),
            'build.json names no valid build number')
    require(type(record.get('build_datetime')) is int and 0 < record['build_datetime'] < 10 ** 18,
            'build.json names no valid build date')
    signing = record.get('signing')
    require(isinstance(signing, str) and signing in SIGNERS and record.get('release') is False,
            f'build.json says the images are signed with {signing!r}'
            + (' for a release' if record.get('release') is not False else '')
            + ', but no signature result is here to show it. This command makes releases signed with the public '
              'test keys only')
    require(record.get('never_lock') is True, 'build.json does not say that a test-key build must never be locked')
    return SIGNERS[signing]()


def check_report(record: dict, sums_sha256: str, report_path: Path) -> dict:
    """The verify report shows that this set passed every check these tools have."""
    require(report_path.is_file(), f'no verify report for this image set ({report_path}): run "diamaneos build '
                                   'verify"')
    try:
        report = json.loads(report_path.read_bytes())
    except ValueError:
        raise ReleaseError(f'{report_path} is not a verify report') from None
    problem = image_verify.report_problem(report, record['build_id'], sums_sha256)
    require(not problem, f'the verify report does not show that this image set passed: {problem}')
    require(report.get('build_identity') == record.get('build_identity'),
            'the verify report belongs to another build identity')
    checks = json.loads(IMAGE_CHECKS.read_bytes())
    require(report.get('rule_set_sha256') == bw.digest(checks),
            'the set was verified with other image checks than these tools have: run "diamaneos build verify" '
            'again with these tools')
    applicable = [rule['id'] for rule in image_verify.validate_rules(checks)
                  if image_verify.rule_applies(rule, record['variant'], True)]
    listed = {check.get('id') for check in report['checks']}
    missing = [rule for rule in applicable if rule not in listed]
    require(not missing, 'the verify report lacks the image checks ' + ', '.join(missing))
    return report


def load_image_set(directory: Path, report_path: Path | None = None) -> ImageSet:
    record_path = directory / image_package.RECORD
    require(record_path.is_file(), f'{directory} is not a DiamaneOS image directory (no build.json)')
    record = json.loads(record_path.read_bytes())
    require(isinstance(record, dict) and isinstance(record.get('build_id'), str), 'build.json is not a build record')
    sums = check_files(directory, record)
    signer = check_build(record)
    sums_sha256 = bw.sha_file(directory / image_package.SUMS)
    report_path = report_path or directory.parent / (record['build_id'] + '.verify.json')
    report = check_report(record, sums_sha256, report_path)
    tools = product_inputs.tools_identity()
    require(tools.get('commit') and tools.get('clean') is True,
            f'these tools ({bw.ROOT}) have local changes or no known commit. A release is made with committed tools')
    return ImageSet(directory, record, sums, sums_sha256, report, report_path, signer, tools)


# ------------------------------------------------------------- the release

def load_previous(directory: Path, current: ImageSet) -> dict:
    """An earlier release made by this command, for an incremental OTA package."""
    path = directory / 'provenance' / RECORD
    require(path.is_file(), f'{directory} is not a release directory (no provenance/{RECORD})')
    record = json.loads(path.read_bytes())
    require(isinstance(record, dict) and record.get('schema_version') == 1
            and isinstance(record.get('target_files'), dict), f'{path} has an unknown format')
    target = directory / str(record['target_files'].get('file'))
    require(target.is_file() and bw.sha_file(target) == record['target_files'].get('sha256'),
            f'the target-files archive of {directory.name} does not match its release record')
    require(record.get('device') == current.product, f'{directory.name} is a release for another device')
    signing = record.get('signing')
    require(isinstance(signing, dict) and signing.get('keys') == current.signer.name,
            f'{directory.name} is signed with other keys than this image set')
    require(record.get('build_number') != current.build_number, f'{directory.name} is this build')
    require(type(record.get('build_datetime')) is int and record['build_datetime'] < current.record['build_datetime'],
            f'{directory.name} is not older than this build, and the Updater installs no older build')
    require(isinstance(record.get('fingerprint'), str), f'the release record of {directory.name} has no fingerprint')
    return dict(record, target_files_path=target)


def java_home(workspace: bw.Workspace) -> Path | None:
    """The newest JDK of the workspace's source (the one the build used), else the one on PATH."""
    jdks = sorted((workspace.src / 'prebuilts/jdk').glob('jdk*/linux-x86'),
                  key=lambda path: int(re.sub(r'\D', '', path.parent.name) or 0))
    if jdks:
        return jdks[-1]
    found = shutil.which('java')
    return Path(found).resolve().parent.parent if found else None


def host_problem(java: Path | None) -> str | None:
    """Why ota_from_target_files from otatools.zip cannot run here, or None."""
    if platform.system() != 'Linux' or platform.machine() != 'x86_64':
        return 'the OTA tools in otatools.zip run on Linux x86-64 only'
    if java is None:
        return 'ota_from_target_files signs with Java: run in the build workspace or install a JDK'
    if shutil.which('openssl') is None:
        return 'ota_from_target_files signs the payload with openssl: install it'
    return None


def zip_time(timestamp: int) -> tuple:
    moment = datetime.datetime.fromtimestamp(max(timestamp, 315532800), datetime.timezone.utc)
    return moment.timetuple()[:6]


def copy_checked(source: Path, destination: Path, digest: str) -> None:
    """Copy, and refuse unless the copied bytes have the verified hash."""
    hasher = hashlib.sha256()
    with source.open('rb') as reading, destination.open('xb') as writing:
        for chunk in iter(lambda: reading.read(16 * 1024 * 1024), b''):
            hasher.update(chunk)
            writing.write(chunk)
    require(hasher.hexdigest() == digest, f'{source.name} changed while it was copied')


def write_factory(image_set: ImageSet, destination: Path) -> dict:
    """The image set as the user flashes it: every file but the build's two
    archives, under one directory, with its own SHA256SUMS for flash-all.sh.
    Entries are sorted and carry the build date, so the same set gives the
    same package."""
    left_out = {image_set.record['target_files']['file'], image_set.record['otatools']['file']}
    members = {name: digest for name, digest in sorted(image_set.sums.items()) if name not in left_out}
    sums = ''.join(f'{digest}  {name}\n' for name, digest in members.items()).encode()
    top = destination.name.removesuffix('.zip')
    when = zip_time(image_set.record['build_datetime'])

    def entry(name, size):
        info = zipfile.ZipInfo(f'{top}/{name}', when)
        info.create_system = 3
        info.external_attr = (0o100755 if name == flash_all.SH else 0o100644) << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        info.file_size = size
        return info

    with zipfile.ZipFile(destination, 'x', allowZip64=True) as archive:
        for name, digest in members.items():
            source = image_set.directory / name
            hasher = hashlib.sha256()
            with source.open('rb') as reading, archive.open(entry(name, source.stat().st_size), 'w') as writing:
                for chunk in iter(lambda: reading.read(16 * 1024 * 1024), b''):
                    hasher.update(chunk)
                    writing.write(chunk)
            require(hasher.hexdigest() == digest, f'{name} changed while it was packed')
        archive.writestr(entry(image_package.SUMS, len(sums)), sums)
    members[image_package.SUMS] = sha_bytes(sums)
    with zipfile.ZipFile(destination) as archive:
        require(sorted(archive.namelist()) == sorted(f'{top}/{name}' for name in members),
                'the factory package does not hold exactly the files of the set')
    return {'file': destination.name, 'directory': top, 'members': members}


def misc_info(target_files: Path) -> dict:
    with zipfile.ZipFile(target_files) as archive:
        require('META/misc_info.txt' in archive.namelist(), 'the target-files archive lacks META/misc_info.txt')
        text = archive.read('META/misc_info.txt').decode('utf-8', 'replace')
    return dict(line.split('=', 1) for line in text.splitlines() if '=' in line and not line.startswith('#'))


def ota_metadata(path: Path) -> tuple[dict, bytes]:
    from . import signing_archive as za
    try:
        with za.archive(path) as handle:
            names = set(handle.namelist())
            needed = {OTA_METADATA, OTA_CERTIFICATE, 'payload.bin', 'payload_properties.txt'}
            require(needed <= names, f'{path.name} lacks ' + ', '.join(sorted(needed - names)))
            return za.properties(za.read_text(handle, OTA_METADATA)), handle.read(OTA_CERTIFICATE)
    except SigningError as error:
        raise ReleaseError(f'{path.name}: {error}') from None


def check_ota(path: Path, image_set: ImageSet, tools: Path, previous: dict | None = None) -> dict:
    """The OTA package passes the Updater's own checks for this build, and is
    signed as the image set is. Returns its metadata."""
    record = image_set.record
    metadata, certificate = ota_metadata(path)
    require(path.stat().st_size <= MAX_OTA_BYTES, f'{path.name} is larger than the Updater downloads')
    expected = {'ota-type': 'AB', 'post-build-incremental': record['build_number'],
                'post-timestamp': str(record['build_datetime']), 'pre-device': image_set.product}
    if previous:
        expected.update({'pre-build-incremental': previous['build_number'], 'pre-build': previous['fingerprint']})
    wrong = sorted(key for key, value in expected.items() if metadata.get(key) != value)
    require(not wrong, f'{path.name} metadata does not match the build: ' + ', '.join(
        f'{key} is {metadata.get(key)!r}, not {expected[key]!r}' for key in wrong))
    require('serialno' not in metadata, f'{path.name} is bound to one phone (serialno)')
    if not previous:
        require('pre-build' not in metadata and 'pre-build-incremental' not in metadata,
                f'{path.name} is an incremental package, not a full one')
    fingerprint = metadata.get('post-build', '')
    require(fingerprint.split('/')[4:5] == [record['build_number'] + ':' + record['variant']],
            f'{path.name} has the fingerprint {fingerprint!r}, not one of build {record["build_number"]}')
    streamed = [entry.split(':') for entry in metadata.get('ota-streaming-property-files', '').split(',')]
    require(any(len(e) == 3 and e[0] == 'payload.bin' and e[1].isdecimal() for e in streamed),
            f'{path.name} names no payload offset')
    image_set.signer.check_ota(metadata, certificate, tools)
    return metadata


def channel_file(metadata: dict, channel: str) -> bytes:
    """What the Updater reads at <device>-<channel>, from the OTA package's own metadata."""
    line = ' '.join((metadata['post-build-incremental'], metadata['post-timestamp'], metadata['pre-device'], channel))
    require(CHANNEL_LINE.fullmatch(line) and len(line) < MAX_CHANNEL_BYTES,
            f'the update information {line!r} is not one the Updater reads')
    return (line + '\n').encode()


def ota_environment(tools: Path, java: Path | None) -> dict:
    paths = [str(tools / 'bin')] + ([str(java / 'bin')] if java else []) + [os.environ.get('PATH', '')]
    return {'PATH': os.pathsep.join(paths), **({'JAVA_HOME': str(java)} if java else {})}


def planned_files(image_set: ImageSet, channels, previous: dict | None) -> dict:
    """The release's files by role, as paths relative to the release directory."""
    name, product, number = image_set.name, image_set.product, image_set.build_number
    ota = [f'publish/{product}-ota_update-{number}.zip']
    if previous:
        ota.append(f'publish/{product}-incremental-{previous["build_number"]}-{number}.zip')
    names = (image_package.RECORD, image_set.record['manifest']['file'], image_package.SUMS,
             image_set.record['build_id'] + '.verify.json', RECORD)
    return {'factory': f'publish/{product}-factory-{number}.zip', 'ota': ota,
            'channels': [f'publish/{product}-{channel}' for channel in channels],
            'provenance': [f'provenance/{n}' for n in names],
            'target_files': f'{product}-target_files-{number}.zip', 'name': name}


def make_release(image_set: ImageSet, channels, output: Path, workspace: bw.Workspace,
                 previous: dict | None = None, echo=print) -> Path:
    record = image_set.record
    final = output / image_set.name
    require(not final.exists(), f'{final} exists: a release is never made twice. Move it aside first')
    java = java_home(workspace)
    problem = host_problem(java)
    require(problem is None, f'cannot make the OTA package here: {problem}')
    plan = planned_files(image_set, channels, previous)
    partial = output / (image_set.name + '.partial')
    if partial.exists():
        shutil.rmtree(partial)
    for part in ('publish', 'provenance', '.work/tmp'):
        (partial / part).mkdir(parents=True)
    log = partial / LOG
    runner = bw.Runner(True, echo, partial / '.work/tmp')

    echo('  Copy the provenance')
    for relative in plan['provenance'][:3]:
        name = Path(relative).name
        digest = image_set.sums_sha256 if name == image_package.SUMS else image_set.sums[name]
        copy_checked(image_set.directory / name, partial / relative, digest)
    report = partial / plan['provenance'][3]
    report.write_bytes(image_set.report_path.read_bytes())
    require(json.loads(report.read_bytes()) == image_set.report, 'the verify report changed during the release')
    target_name = record['target_files']['file']
    copy_checked(image_set.directory / target_name, partial / plan['target_files'], image_set.sums[target_name])

    echo('  Write the factory package')
    factory = write_factory(image_set, partial / plan['factory'])

    tools_archive = image_set.directory / record['otatools']['file']
    tools = partial / '.work/otatools'
    echo('  Unpack otatools')
    from . import signing_archive as za
    snapshot = partial / '.work/otatools.zip'
    copy_checked(tools_archive, snapshot, image_set.sums[record['otatools']['file']])
    try:
        za.unpack_tools(snapshot, tools)
    except SigningError as error:
        raise ReleaseError(f'otatools.zip: {error}') from None
    key = image_set.signer.ota_key(misc_info(partial / plan['target_files']))
    tool = tools / 'bin/ota_from_target_files'
    require(tool.is_file() and all((tools / (key + suffix)).is_file() for suffix in ('.pk8', '.x509.pem')),
            'otatools.zip lacks ota_from_target_files or the public test key')
    environment = ota_environment(tools, java)
    metadata = []
    for index, relative in enumerate(plan['ota']):
        source = previous if index else None
        argv = [tool, '-p', tools, '-k', key] + (['-i', source['target_files_path']] if source else []) + [
            partial / plan['target_files'], partial / relative]
        title = (f'Make the incremental OTA package from {source["build_number"]}' if source
                 else 'Make the full OTA package')
        runner.run(Action(title, argv=argv, cwd=tools, env=environment), log)
        metadata.append(check_ota(partial / relative, image_set, tools, source))
    shutil.rmtree(partial / '.work')

    echo('  Write the channel files')
    for channel, relative in zip(channels, plan['channels']):
        (partial / relative).write_bytes(channel_file(metadata[0], channel))

    artifacts = {}
    for relative in [plan['factory'], *plan['ota'], *plan['channels'], *plan['provenance'][:-1], plan['target_files']]:
        path = partial / relative
        artifacts[relative] = {'sha256': bw.sha_file(path), 'bytes': path.stat().st_size}
    value = release_record(image_set, channels, plan, factory, metadata, previous, artifacts)
    bw.write_atomic(partial / plan['provenance'][-1], bw.encoded(value), 0o444)
    # Exactly the planned files and the log: nothing claims a signature the release does not have.
    expected = set(artifacts) | {plan['provenance'][-1], LOG}
    found = {str(p.relative_to(partial)) for p in partial.rglob('*') if not p.is_dir()}
    require(found == expected, 'the release holds unexpected files: ' + ', '.join(sorted(found ^ expected)))
    for path in partial.rglob('*'):
        if path.is_file():
            path.chmod(0o444)
    partial.rename(final)
    return final


def validated(record: dict) -> dict:
    """Whether the wipe by empty images and the firmware steps flash-all runs are tested on a phone."""
    return {'wipe': (record.get('wipe') or {}).get('validated') is True,
            'firmware': (record.get('firmware') or {}).get('validated') is True}


def release_record(image_set: ImageSet, channels, plan: dict, factory: dict, metadata: list,
                   previous: dict | None, artifacts: dict) -> dict:
    record, report = image_set.record, image_set.report
    source = record.get('source') or {}
    return {
        'schema_version': 1, 'device': image_set.product, 'build_id': record['build_id'],
        'build_number': record['build_number'], 'build_datetime': record['build_datetime'],
        'variant': record['variant'], 'official': True, 'channels': list(channels),
        'fingerprint': metadata[0]['post-build'],
        'validated_on_a_phone': validated(record),
        'signing': {'keys': image_set.signer.name, 'signatures': {}, 'never_lock': True,
                    'notice': 'Signed with the public test keys. Keep the bootloader unlocked. The Updater of this '
                              'build checks for updates but installs none.'},
        'commits': {'manifest': (record.get('manifest') or {}).get('commit'),
                    'kernel_prebuilts': (record.get('kernel_prebuilts') or {}).get('commit'),
                    'tools_build': (record.get('tools') or {}).get('commit'),
                    'tools_packaging': (record.get('packaging_tools') or {}).get('commit'),
                    'tools_release': image_set.tools['commit']},
        'source': {'manifest_url': source.get('manifest_url'), 'manifest_branch': source.get('manifest_branch'),
                   'resolved_manifest_sha256': source.get('resolved_manifest_sha256'),
                   'project_count': source.get('project_count')},
        'image_set': {'build_identity': record['build_identity'], 'source_identity': record.get('source_identity'),
                      'sums_sha256': bw.sha_file(image_set.directory / image_package.SUMS),
                      'files': image_set.sums},
        'verify': {'file': plan['provenance'][3], 'status': report['status'], 'checked': report['checked'],
                   'rule_set_sha256': report['rule_set_sha256']},
        'factory': dict(factory, file=plan['factory']),
        'ota': [{'file': relative, 'source_build_number': previous['build_number'] if index else None}
                for index, relative in enumerate(plan['ota'])],
        'target_files': {'file': plan['target_files'], 'sha256': record['target_files']['sha256']},
        'otatools_sha256': record['otatools']['sha256'],
        'artifacts': artifacts,
    }


# ------------------------------------------------------------------ command

def resolve_images(value: str | None, workspace: bw.Workspace) -> Path:
    if value:
        return Path(value).expanduser().absolute()
    latest = workspace.images / 'latest'
    require(latest.is_symlink(), f'no packaged build in {workspace.root}: run "diamaneos build all --official" first')
    return latest.resolve()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog='diamaneos release', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    result.add_argument('images', nargs='?', help='image directory (default: the latest build in the workspace)')
    result.add_argument('--workspace', help='build directory (default: $DIAMANEOS_WORKSPACE, the workspace of the '
                                            'checkout these tools run from, or ~/diamaneos-build)')
    result.add_argument('--channel', action='append', choices=CHANNELS, default=[],
                        help='an update channel the release goes to. Give it once for each channel')
    result.add_argument('--output', help='directory of the releases (default: <workspace>/releases)')
    result.add_argument('--verify-report', metavar='FILE',
                        help='the image set\'s verify report (default: <build id>.verify.json next to the image '
                             'directory)')
    result.add_argument('--incremental-from', metavar='RELEASE',
                        help='also make an incremental OTA package from this earlier release directory')
    result.add_argument('--check', action='store_true',
                        help='only check the image set and print what the release would hold. Writes nothing')
    return result


def main(argv=None, echo=print) -> int:
    args = parser().parse_args(argv)
    channels = list(dict.fromkeys(args.channel))
    try:
        require(channels or args.check, 'name the update channels of the release with --channel')
        workspace = bw.Workspace(Path(args.workspace).expanduser() if args.workspace else bw.default_workspace())
        directory = resolve_images(args.images, workspace)
        report = Path(args.verify_report).expanduser().absolute() if args.verify_report else None
        image_set = load_image_set(directory, report)
        previous = load_previous(Path(args.incremental_from).expanduser().absolute(), image_set) \
            if args.incremental_from else None
        output = Path(args.output).expanduser().absolute() if args.output else workspace.root / 'releases'
        plan = planned_files(image_set, channels, previous)
        echo(f'{image_set.record["build_id"]}: official user build {image_set.build_number}, '
             f'{image_set.report["checked"]} checks passed, signed with the public test keys')
        untested = [part for part, done in validated(image_set.record).items() if not done]
        if untested:
            echo(f'note: build.json says the {" and the ".join(untested)} steps of flash-all are not yet tested on '
                 'a phone. release.json records it')
        if args.check:
            echo(f'The release {output / image_set.name} would hold:')
            for relative in [plan['factory'], *plan['ota'], *plan['channels'], *plan['provenance'],
                             plan['target_files']]:
                echo('  ' + relative)
            problem = host_problem(java_home(workspace))
            if problem:
                echo(f'note: this host cannot make the OTA package ({problem})')
            return 0
        final = make_release(image_set, channels, output, workspace, previous, echo)
        echo(f'Release: {final}')
        echo('Upload publish/ with the channel files last, so that a phone reading a channel file finds its '
             'packages.')
        return 0
    except (ReleaseError, OSError, ValueError, KeyError, zipfile.BadZipFile, bw.BuildStepError) as error:
        print('ERROR: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())

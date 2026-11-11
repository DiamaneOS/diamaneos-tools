"""The OTA self-update test: a test build N+1 of the same tree as N, and the incremental from N to N+1.

The phone test installs N+1 on a phone that runs N by incremental update, switches slots and boots it.
Two builds of one tree share their build date (the newest commit time), and update_engine and the
Updater need a later one. So N+1 takes N's date plus SUCCESSOR_SECONDS and N's build number with
SUFFIX, and its records mark it as an OTA test build, which release signing refuses.

  diamaneos ota-test plan --base N_IMAGES
  diamaneos build all --workspace W --resolved-manifest N_IMAGES/resolved-manifest.xml \\
      --build-json N_IMAGES/build.json --ota-successor-of N_IMAGES
  diamaneos ota-test incremental --base N_IMAGES --successor N1_IMAGES --output FILE
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sys
import zipfile

from . import build_workspace as bw
from .build_workspace import Action, BuildStepError, CheckFailed, UsageError

# N+1's build date is N's plus this, so it is strictly later and a real later release stays newer.
SUCCESSOR_SECONDS = 60
# N+1's build number is N's with this suffix: higher, and never a release number.
SUFFIX = '.1'
BUILD_NUMBER = re.compile(r'[A-Za-z0-9._-]{1,64}')
RECORD = 'build.json'
SUMS = 'SHA256SUMS'
METADATA = 'META-INF/com/android/metadata'
# What N+1's records keep of N: enough to find N and to tell a changed N.
BASE_FIELDS = ('build_id', 'build_number', 'build_datetime', 'build_identity', 'product', 'variant', 'official',
               'resolved_sha256')
NOTICE = 'OTA test build for the update test. Never a release.'
# The public test key in otatools.zip, which signs packages of unsigned builds.
TEST_KEY = 'build/make/target/product/security/testkey'


def load_record(directory: Path) -> dict:
    """An image set's build.json, which its SHA256SUMS must list with these bytes."""
    directory = Path(directory)
    path = directory / RECORD
    try:
        data = path.read_bytes()
        sums = {}
        for line in (directory / SUMS).read_text().splitlines():
            digest, _, name = line.partition('  ')
            sums[name] = digest
        record = json.loads(data)
    except (OSError, ValueError) as error:
        raise UsageError(f'{directory} is not an image set with build.json and SHA256SUMS ({error})') from None
    if sums.get(RECORD) != bw.sha_file(path):
        raise UsageError(f'{path} does not match its SHA256SUMS')
    if not isinstance(record, dict):
        raise UsageError(f'{path} is not a build record')
    return record


def base_facts(record: dict) -> dict:
    """What a successor build takes from N's record, checked."""
    facts = {key: record.get(key) for key in BASE_FIELDS[:-1]}
    facts['resolved_sha256'] = (record.get('manifest') or {}).get('resolved_sha256')
    if not (isinstance(facts['build_number'], str) and BUILD_NUMBER.fullmatch(facts['build_number'])):
        raise UsageError('the base build record has no valid build number')
    if type(facts['build_datetime']) is not int or facts['build_datetime'] <= 0:
        raise UsageError('the base build record has no build date')
    if type(facts['official']) is not bool or not isinstance(facts['variant'], str):
        raise UsageError('the base build record has no variant or official choice')
    for key in ('build_id', 'build_identity', 'product', 'resolved_sha256'):
        if not isinstance(facts[key], str) or not facts[key]:
            raise UsageError(f'the base build record has no {key}')
    return facts


def successor_number(number: str) -> str:
    result = number + SUFFIX
    if not BUILD_NUMBER.fullmatch(result):
        raise UsageError(f'the build number {number} is too long for a successor build number')
    return result


def successor_datetime(base: int, newest: int | None = None) -> int:
    """Strictly later than N and than the newest source commit."""
    return max(base, int(newest or 0)) + SUCCESSOR_SECONDS


def number_key(number: str) -> tuple:
    return tuple(int(part) for part in re.findall(r'[0-9]+', number))


def newer_number(candidate: str, base: str) -> bool:
    """The candidate's digit groups compare above the base's: 2026111100.1 is above 2026111100."""
    return number_key(candidate) > number_key(base)


def check_context(base: dict, variant: str, official: bool) -> None:
    if variant != base['variant']:
        raise UsageError(f'the base build {base["build_number"]} is {base["variant"]}, so its successor is '
                         f'{base["variant"]} too, not {variant}. Leave out --variant')
    if official != base['official']:
        raise UsageError(f'the base build {base["build_number"]} is {"" if base["official"] else "not "}official. '
                         f'Build its successor {"with --official" if base["official"] else "with --no-official"}')


def check_same_tree(base: dict, sync_outputs: dict) -> None:
    if sync_outputs.get('resolved_manifest_sha256') != base['resolved_sha256']:
        raise UsageError(f'the workspace\'s source is not the tree of the base build {base["build_number"]}. Sync '
                         'it with --resolved-manifest and --build-json of the base image set')
    if sync_outputs.get('modified_sha256') or sync_outputs.get('deviations_sha256'):
        raise UsageError('the workspace\'s source has local changes or deviations: the successor build must be the '
                         'base build\'s tree')


def successor_mark(base: dict) -> dict:
    """The record of an OTA test build: whose successor it is."""
    return {'successor_of': {key: base[key] for key in ('build_id', 'build_number', 'build_datetime', 'build_identity')},
            'notice': NOTICE}


def check_pair(base: dict, successor: dict) -> None:
    """N+1's record must be the OTA test build made from this N's tree, later and higher."""
    mark = successor.get('ota_test')
    if not isinstance(mark, dict) or mark.get('successor_of') != successor_mark(base)['successor_of']:
        raise UsageError(f'the successor image set is not the OTA test build of {base["build_number"]} '
                         f'({base["build_id"]})')
    later = base_facts(successor)
    for key in ('product', 'variant', 'official', 'resolved_sha256'):
        if later[key] != base[key]:
            raise UsageError(f'the successor build has another {key} than the base build')
    if later['build_datetime'] <= base['build_datetime']:
        raise UsageError('the successor build is not later than the base build')
    if not newer_number(later['build_number'], base['build_number']):
        raise UsageError('the successor build number is not higher than the base build number')


def properties(data: bytes) -> dict:
    values = {}
    for line in data.decode('utf-8', 'replace').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip()
    return values


def target_facts(path: Path) -> dict:
    """The build number and date a target-files archive carries."""
    try:
        with zipfile.ZipFile(path) as archive:
            values = properties(archive.read('SYSTEM/build.prop'))
    except (OSError, KeyError, zipfile.BadZipFile) as error:
        raise UsageError(f'{path} is not a target-files archive ({error})') from None
    try:
        return {'build_number': values['ro.build.version.incremental'],
                'build_datetime': int(values['ro.build.date.utc'])}
    except (KeyError, ValueError):
        raise UsageError(f'{path} has no build number or date in SYSTEM/build.prop') from None


def package_facts(path: Path) -> dict:
    """The metadata and payload headers of an A/B update package."""
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        if not {METADATA, 'payload.bin', 'payload_properties.txt'} <= names:
            raise CheckFailed(f'{Path(path).name} is not an A/B update package')
        if archive.getinfo('payload.bin').compress_type != zipfile.ZIP_STORED:
            raise CheckFailed('payload.bin is compressed')
        return {'metadata': properties(archive.read(METADATA)),
                'payload_properties': properties(archive.read('payload_properties.txt'))}


def package_problems(facts: dict, base: dict, successor: dict) -> list:
    """Why the package is not the incremental from base to successor, or nothing."""
    meta, headers = facts['metadata'], facts['payload_properties']
    problems = []
    expected = {'ota-type': 'AB', 'pre-build-incremental': base['build_number'],
                'post-build-incremental': successor['build_number'],
                'post-timestamp': str(successor['build_datetime'])}
    for key, value in expected.items():
        if meta.get(key) != value:
            problems.append(f'{key} is {meta.get(key)!r}, expected {value!r}')
    if not meta.get('pre-device'):
        problems.append('pre-device is missing')
    for key in ('ota-downgrade', 'ota-wipe', 'spl-downgrade'):
        if meta.get(key, 'no') not in ('no', ''):
            problems.append(f'{key} is set')
    for key in ('POWERWASH', 'SPL_DOWNGRADE'):
        if headers.get(key, '0') not in ('0', ''):
            problems.append(f'{key} is set')
    for key in ('FILE_HASH', 'FILE_SIZE', 'METADATA_HASH', 'METADATA_SIZE'):
        if not headers.get(key):
            problems.append(f'payload_properties.txt lacks {key}')
    if successor['build_datetime'] <= base['build_datetime']:
        problems.append('the successor is not later than the base')
    return problems


def incremental_argv(otatools: Path, key: str, source: Path, target: Path, destination: Path) -> list:
    """ota_from_target_files from the successor's otatools, as the update tests run it."""
    return [str(otatools / 'bin' / 'ota_from_target_files'), '-p', str(otatools), '-k', str(key),
            '-i', str(source), str(target), str(destination)]


def tool_path(otatools: Path, source_root: Path | None) -> str:
    """otatools first, then the source's JDK and build tools when given (signapk needs java)."""
    parts = [str(otatools / 'bin')]
    if source_root:
        for sub in ('prebuilts/jdk/jdk21/linux-x86/bin', 'prebuilts/build-tools/linux-x86/bin',
                    'prebuilts/build-tools/path/linux-x86'):
            if (source_root / sub).is_dir():
                parts.append(str(source_root / sub))
    return os.pathsep.join(parts + [os.environ.get('PATH', '')])


def checked_target_files(directory: Path, record: dict) -> Path:
    path = directory / (record.get('target_files') or {}).get('file', 'target-files.zip')
    if bw.sha_file(path) != (record.get('target_files') or {}).get('sha256'):
        raise UsageError(f'{path} does not match its build record')
    return path


def make_incremental(base_dir: Path, successor_dir: Path, output: Path, *, source_tf: Path | None = None,
                     target_tf: Path | None = None, key: str | None = None, source_root: Path | None = None,
                     scratch: Path | None = None, echo=print, runner=None) -> dict:
    """Make and check the incremental from N to N+1 and write its record beside it."""
    from . import image_verify, product_inputs
    base_record, successor_record = load_record(base_dir), load_record(successor_dir)
    base, successor = base_facts(base_record), base_facts(successor_record)
    check_pair(base, successor_record)
    signed = (source_tf, target_tf, key)
    if any(signed) and not all(signed):
        raise UsageError('signed copies need --source-target-files, --target-target-files and --key together')
    test_keys = not any(signed)
    if test_keys and 'public-test-keys' not in (base_record.get('signing'), successor_record.get('signing')):
        raise UsageError('the image sets are not signed with the public test keys: give the signed copies and --key')
    if test_keys:
        source_tf, target_tf = checked_target_files(base_dir, base_record), checked_target_files(successor_dir, successor_record)
    for path, facts in ((source_tf, base), (target_tf, successor)):
        found = target_facts(path)
        if found != {'build_number': facts['build_number'], 'build_datetime': facts['build_datetime']}:
            raise UsageError(f'{path} carries build {found["build_number"]} of {found["build_datetime"]}, '
                             f'not {facts["build_number"]} of {facts["build_datetime"]}')
    output = Path(output).absolute()
    record_path = output.with_suffix('.json')
    partial = output.with_name(output.name + '.partial')
    if output.exists() or record_path.exists():
        raise UsageError(f'{output} or its record exists already')
    tool = successor_record.get('otatools') or {}
    scratch = Path(scratch or output.parent / '.ota-test-tmp')
    bin_dir = image_verify.unpack_otatools(successor_dir / tool.get('file', 'otatools.zip'), tool.get('sha256'), scratch)
    otatools = bin_dir.parent
    try:
        if not (bin_dir / 'ota_from_target_files').is_file():
            raise BuildStepError('otatools.zip lacks ota_from_target_files')
        if test_keys:
            key = str(otatools / TEST_KEY)
            if not (otatools / (TEST_KEY + '.pk8')).is_file():
                raise BuildStepError('otatools.zip lacks the public test key')
        partial.unlink(missing_ok=True)
        log = output.with_suffix('.log')
        runner = runner or bw.Runner(True, echo, scratch)
        runner.run(Action('Make the incremental', detail=f'{base["build_number"]} to {successor["build_number"]}',
                          argv=incremental_argv(otatools, key, source_tf, target_tf, partial), cwd=otatools,
                          env={'PATH': tool_path(otatools, source_root)}), log)
        problems = package_problems(package_facts(partial), base, successor)
        if problems:
            raise CheckFailed('the incremental is not the update from N to N+1: ' + ', '.join(problems))
        os.replace(partial, output)
    finally:
        partial.unlink(missing_ok=True)
        shutil.rmtree(otatools.parent, ignore_errors=True)
    facts = package_facts(output)
    record = {'schema_version': 1, 'kind': 'ota-test-incremental', 'release': False, 'notice': NOTICE,
              'package': {'file': output.name, 'sha256': bw.sha_file(output), 'bytes': output.stat().st_size},
              'base': base, 'successor': successor,
              'signing': 'public-test-keys' if test_keys else 'own-keys',
              'metadata': {k: facts['metadata'].get(k) for k in ('ota-type', 'pre-device', 'pre-build-incremental',
                                                                  'post-build-incremental', 'post-timestamp')},
              'tools': product_inputs.tools_identity()}
    bw.write_atomic(record_path, bw.encoded(record), 0o644)
    return record


def plan_text(base: dict) -> str:
    return (f'base: {base["build_number"]} ({base["build_id"]}), date {base["build_datetime"]}\n'
            f'successor: {successor_number(base["build_number"])}, date '
            f'{successor_datetime(base["build_datetime"])} ({base["variant"]}'
            f'{", official" if base["official"] else ""})')


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog='diamaneos ota-test', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = result.add_subparsers(dest='command', required=True)
    plan = commands.add_parser('plan', help='the successor build number and date of a base image set')
    plan.add_argument('--base', required=True, type=Path, help="N's image set")
    incremental = commands.add_parser('incremental', help='the incremental from N to its OTA test build N+1')
    incremental.add_argument('--base', required=True, type=Path, help="N's image set")
    incremental.add_argument('--successor', required=True, type=Path, help="N+1's image set")
    incremental.add_argument('--output', required=True, type=Path, help='the package (its record goes beside it as .json)')
    incremental.add_argument('--source-target-files', type=Path, help="N's signed target-files (signed builds)")
    incremental.add_argument('--target-target-files', type=Path, help="N+1's signed target-files (signed builds)")
    incremental.add_argument('--key', help='the OTA key both were signed with, without .pk8 (signed builds)')
    incremental.add_argument('--source-root', type=Path, help='a source checkout whose JDK and build tools go on PATH')
    incremental.add_argument('--scratch', type=Path, help='where otatools is unpacked (default: beside the output)')
    return result


def main(argv=None, echo=print) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == 'plan':
            echo(plan_text(base_facts(load_record(args.base))))
            return 0
        record = make_incremental(args.base, args.successor, args.output, source_tf=args.source_target_files,
                                  target_tf=args.target_target_files, key=args.key, source_root=args.source_root,
                                  scratch=args.scratch, echo=echo)
        echo(f'incremental {record["base"]["build_number"]} to {record["successor"]["build_number"]}: '
             f'{args.output} ({record["package"]["bytes"]} bytes), record {args.output.with_suffix(".json")}')
        return 0
    except BuildStepError as error:
        print('ERROR: ' + str(error), file=sys.stderr)
        return error.exit_code

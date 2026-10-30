"""Compare kernel builds.

diamaneos kernel compare --run RUN --against RUN checks that two passed kernel
builds (KERNEL_WORKSPACE/runs/...) of the same pins packaged the same files with
the same bytes. Each build signs with a key of its own, so a module may differ in
its appended signature only, and the Image in its built-in certificate and the
build ID that hashes it. Everything else must match.

diamaneos kernel compare --run RUN --reference DIR compares a build with a kernel
set built another way (a directory in the layout of device_fairphone_FP6-kernels,
plus vendor_cmdline for its kernel command line). The modules of two separate
builds differ in bytes, so it compares what the Android build and the kernel
read: the module names, their metadata, the lists and their load order, the
device tree after decompiling, the command line and the Image configuration.
Modules the reference ships and the packaging deny list leaves out are reported
as policy, anything else is a failure.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

MARKER = b'~Module signature appended~\n'
# The GNU build ID note header: name size 4, descriptor size 20, type 3, "GNU".
BUILD_ID = (4).to_bytes(4, 'little') + (20).to_bytes(4, 'little') + (3).to_bytes(4, 'little') + b'GNU\0'
# Kernel arguments of the bring-up work: none of them belongs in a production command line.
BRING_UP_ARGUMENTS = ('initcall_debug', 'loglevel', 'log_buf_len', 'panic', 'trace_event', 'qcom_dload_mode.download_mode',
                      'qcom_scm.download_mode', 'reboot', 'sysctl.kernel.panic_on_rcu_stall', 'module_blacklist',
                      'dwc3_msm.bringup_peripheral', 'androidboot.first_stage_console')
# Module metadata that decides how a module loads and what it provides. The vermagic and
# the signature are the build's own.
LOAD_FIELDS = ('depends', 'alias', 'firmware', 'license', 'softdep', 'import_ns', 'parm', 'parmtype', 'name')
VARIABLE = re.compile(r'^(BOARD_[A-Z_]+) (:=|\+=) (.*)$')


def unsigned(data):
    """A signed module without its signature, or None when it carries none."""
    if not data.endswith(MARKER) or len(data) < len(MARKER) + 12:
        return None
    size = int.from_bytes(data[-len(MARKER) - 4:-len(MARKER)], 'big')
    end = len(data) - len(MARKER) - 12 - size
    return data[:end] if end >= 0 else None


def without(data, certificate):
    """The Image with its built-in certificate and every build ID zeroed (the
    kernel's hashes the certificate, the vDSOs' their own code), and where they
    were. None unless the certificate is there exactly once."""
    at = data.find(certificate)
    if not certificate or at < 0 or data.find(certificate, at + 1) >= 0:
        return None
    spans = [(at, len(certificate))]
    at = data.find(BUILD_ID)
    while at >= 0:
        spans.append((at + len(BUILD_ID), 20))
        at = data.find(BUILD_ID, at + 1)
    masked = bytearray(data)
    for start, size in spans:
        masked[start:start + size] = bytes(size)
    return bytes(masked), spans


def files(candidate):
    return {p.relative_to(candidate).as_posix() for p in candidate.rglob('*') if p.is_file()}


def compare(run, against):
    a, b = run / 'candidate', against / 'candidate'
    records = [json.loads((r / 'result.json').read_text()) for r in (run, against)]
    for path, record in zip((run, against), records):
        if record.get('status') != 'PASS' or not (path / 'candidate').is_dir():
            raise ValueError('not a passed kernel build run: ' + str(path))
    for key in ('source_commit', 'common_commit', 'packaging_recipe_sha256'):
        if records[0].get(key) != records[1].get(key):
            raise ValueError('the runs differ in ' + key)
    certificates = [(r / 'builtin-cert.der').read_bytes() for r in (run, against)]
    names_a, names_b = files(a), files(b)
    result = {'only_in_run': sorted(names_a - names_b), 'only_in_against': sorted(names_b - names_a),
              'identical': 0, 'signature_only': 0, 'key_only': [], 'different': []}
    for name in sorted(names_a & names_b):
        data = [(a / name).read_bytes(), (b / name).read_bytes()]
        if data[0] == data[1]:
            result['identical'] += 1
            continue
        payloads = [unsigned(d) for d in data]
        if None not in payloads and payloads[0] == payloads[1]:
            result['signature_only'] += 1
            continue
        if name == 'Image':
            masked = [without(d, c) for d, c in zip(data, certificates)]
            if None not in masked and masked[0] == masked[1]:
                result['key_only'].append(name)
                continue
        result['different'].append(name)
    same = not (result['only_in_run'] or result['only_in_against'] or result['different'])
    result['status'] = 'PASS' if same else 'FAIL'
    return result


def board_variables(text):
    """BoardConfigKernel.mk: variable -> list of words, += appended."""
    values = {}
    for line in text.splitlines():
        match = VARIABLE.match(line)
        if match:
            words = [w.rsplit('/', 1)[-1] if match.group(1).endswith('MODULES') else w for w in match.group(3).split()]
            values.setdefault(match.group(1), []).extend(words)
    return values


def load_value(values, field):
    """A metadata field as the loader reads it. The order of the dependencies a build lists does not matter."""
    found = values.get(field)
    if field == 'depends' and found:
        return sorted(d for value in found for d in value.split(',') if d)
    return found


def list_differences(first, second):
    """What differs between two ordered word lists."""
    only_first, only_second = [w for w in first if w not in second], [w for w in second if w not in first]
    common_first, common_second = [w for w in first if w in second], [w for w in second if w in first]
    return {'only_in_run': only_first, 'only_in_reference': only_second, 'order_differs': common_first != common_second}


def arguments_of(text):
    return [w for w in text.split() if w]


def bring_up_words(words):
    return sorted(w for w in words if w.split('=', 1)[0] in BRING_UP_ARGUMENTS)


def config_differences(first, second):
    """Symbols whose value differs between two kernel configurations: symbol -> [run, reference]."""
    def read(text):
        values = {}
        for line in text.splitlines():
            match = re.fullmatch(r'(CONFIG_\w+)=(.*)', line) or re.fullmatch(r'# (CONFIG_\w+) is (not set)', line)
            if match:
                values[match.group(1)] = 'n' if match.group(2) == 'not set' else match.group(2)
        return values
    a, b = read(first), read(second)
    return {k: [a.get(k, 'n'), b.get(k, 'n')] for k in sorted(set(a) | set(b)) if a.get(k, 'n') != b.get(k, 'n')}


def decompile(dtc, blob):
    result = subprocess.run([str(dtc), '-q', '-I', 'dtb', '-O', 'dts', str(blob)], capture_output=True, text=True, timeout=300)
    if result.returncode:
        raise ValueError('cannot decompile ' + str(blob))
    return result.stdout


def compare_reference(run, reference, denied, metadata, dtc):
    """The run's candidate against a reference kernel set. denied: module keys of the packaging deny list.
    dtc decompiles the device trees to compare them."""
    if dtc is None:
        raise ValueError('comparing with a reference needs dtc to compare the device trees')
    candidate = run / 'candidate'
    record = json.loads((run / 'result.json').read_text())
    if record.get('status') != 'PASS' or not candidate.is_dir():
        raise ValueError('not a passed kernel build run: ' + str(run))
    key = lambda name: name.removesuffix('.ko').replace('-', '_')
    mine = {p.name: p for p in (candidate / 'modules').glob('*.ko')}
    theirs = {p.name: p for p in (reference / 'modules').glob('*.ko')}
    policy = sorted(n for n in set(theirs) - set(mine) if key(n) in denied)
    result = {'modules': {'run': len(mine), 'reference': len(theirs),
                          'only_in_run': sorted(set(mine) - set(theirs)),
                          'only_in_reference': sorted(set(theirs) - set(mine) - set(policy)),
                          'denied_by_policy': policy}}
    field_differences = {}
    for name in sorted(set(mine) & set(theirs)):
        a, b = metadata(mine[name]), metadata(theirs[name])
        changed = {f: [a.get(f), b.get(f)] for f in LOAD_FIELDS if load_value(a, f) != load_value(b, f)}
        if changed:
            field_differences[name] = changed
    result['modules']['metadata_differences'] = field_differences
    first, second = board_variables((candidate / 'BoardConfigKernel.mk').read_text()), \
        board_variables((reference / 'BoardConfigKernel.mk').read_text())
    lists = {}
    for variable in sorted(set(first) | set(second)):
        if variable == 'BOARD_KERNEL_CMDLINE':
            continue
        a, b = first.get(variable, []), second.get(variable, [])
        if variable.endswith(('_LOAD', 'MODULES')):
            # The deny list takes policy modules out of the run: leave them out of the reference side too.
            difference = list_differences(a, [w for w in b if w not in policy])
            if difference['only_in_run'] or difference['only_in_reference'] or difference['order_differs']:
                lists[variable] = difference
        elif a != b:
            lists[variable] = {'run': a, 'reference': b}
    result['lists'] = lists
    mine_cmd = first.get('BOARD_KERNEL_CMDLINE', [])
    their_cmd = arguments_of((reference / 'vendor_cmdline').read_text()) if (reference / 'vendor_cmdline').is_file() else None
    result['command_line'] = {'run': mine_cmd, 'run_bring_up_words': bring_up_words(mine_cmd), 'reference': their_cmd,
                              'reference_bring_up_words': bring_up_words(their_cmd or []),
                              'only_in_run': [w for w in mine_cmd if their_cmd is not None and w not in their_cmd]}
    dtbs, their_dtbs = (sorted(p.name for p in (top / 'dtbs').glob('*.dtb')) for top in (candidate, reference))
    result['device_tree'] = {'files': [dtbs, their_dtbs]}
    same = {n: decompile(dtc, candidate / 'dtbs' / n) == decompile(dtc, reference / 'dtbs' / n) for n in dtbs if n in their_dtbs}
    result['device_tree']['decompiled_identical'] = same
    result['device_tree']['bytes_identical'] = {n: (candidate / 'dtbs' / n).read_bytes() == (reference / 'dtbs' / n).read_bytes()
                                                for n in same}
    result['device_tree']['dtbo_identical'] = (candidate / 'dtbo.img').read_bytes() == (reference / 'dtbo.img').read_bytes()
    result['image'] = {'run_bytes': (candidate / 'Image').stat().st_size, 'reference_bytes': (reference / 'Image').stat().st_size,
                       'identical': (candidate / 'Image').read_bytes() == (reference / 'Image').read_bytes(),
                       'release': record.get('kernel_release')}
    if (reference / 'config').is_file() and (run / 'core.config').is_file():
        differing = config_differences((run / 'core.config').read_text(), (reference / 'config').read_text())
        result['image']['configuration_differences'] = differing
    problems = []
    if result['modules']['only_in_run'] or result['modules']['only_in_reference'] or field_differences:
        problems.append('module set or module metadata differs')
    if lists:
        problems.append('module lists or load order differ')
    # The debug profile carries the bring-up arguments on purpose.
    if result['command_line']['run_bring_up_words'] and record.get('config_profile') != 'debug':
        problems.append('bring-up words on the command line')
    if dtbs != their_dtbs or not all(same.values()):
        problems.append('device trees differ')
    if not result['device_tree']['dtbo_identical']:
        problems.append('overlay image differs')
    result['problems'] = problems
    result['status'] = 'FAIL' if problems else 'PASS'
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(prog='diamaneos kernel compare', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--run', type=Path, required=True, help='kernel build run directory (KERNEL_WORKSPACE/runs/...)')
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument('--against', type=Path, help='another build run of the same pins')
    which.add_argument('--reference', type=Path, help='a kernel set in the layout of the prebuilts repository')
    parser.add_argument('--dtc', type=Path, help='dtc, to compare device trees decompiled (required with --reference)')
    args = parser.parse_args(argv)
    if args.reference and not args.dtc:
        parser.error('--reference needs --dtc')
    try:
        if args.against:
            result = compare(args.run, args.against)
        else:
            from .kernel import denied_modules, module_key, module_metadata
            from .vendor import ROOT, load_json
            denied = {module_key(n) for n in denied_modules(load_json(ROOT / 'config/fp6-kernel-packaging.json'))}

            def metadata(path):
                values = {}
                for name, value in module_metadata(path):
                    values.setdefault(name.decode(), []).append(value.decode(errors='replace'))
                return values
            result = compare_reference(args.run, args.reference, denied, metadata, args.dtc)
    except (OSError, ValueError, KeyError) as error:
        print('ERROR: kernel compare failed: ' + str(error), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result['status'] == 'PASS' else 1

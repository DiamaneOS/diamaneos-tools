"""Extract configuration data, never executable code, from a stock carrier APK."""
import hashlib
import argparse
import json
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET
import zipfile

from .vendor import VendorError

APK_PATH = 'system_ext/priv-app/CustomerCarrierConfig/CustomerCarrierConfig.apk'
ASSET_DIRECTORY = 'carrier-assets/device-carrier-config'
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
_NAME = re.compile(r'carrier_config_(carrierid_[0-9]+_.+|(?:mccmnc_)?[0-9]{5,6}|no_sim)\.xml')
# FP6.QREL.16.100.0 contains an empty no-SIM asset with an unclosed root.
# Repair only those exact bytes, leaving every other malformed input an error.
NO_SIM_REPAIR_SHA256 = 'fb9407aad050f2e8d250582788108a9574e9e8199096542628688cb447565047'


def decode_xml_dump(text):
    """Decode aapt2's strict XML tree output; reject unsupported syntax."""
    stack, root = [], None
    for line in text.splitlines():
        element = re.fullmatch(r'(\s*)E: ([\w-]+) \(line=\d+\)', line)
        if element:
            depth, node = len(element[1]), ET.Element(element[2])
            while stack and stack[-1][0] >= depth:
                stack.pop()
            if stack:
                stack[-1][1].append(node)
            elif root is None:
                root = node
            else:
                raise VendorError('multiple carrier XML roots')
            stack.append((depth, node))
            continue
        attribute = re.fullmatch(r'\s*A: ([\w-]+)=(.*)', line)
        if attribute and stack:
            raw = re.search(r' \(Raw: ("(?:[^"\\]|\\.)*")\)$', attribute[2])
            value = raw[1] if raw else attribute[2]
            if value.startswith('"'):
                value = json.loads(value)
            elif not re.fullmatch(r'-?\d+|true|false', value):
                raise VendorError('unsupported carrier XML attribute')
            if attribute[1] in stack[-1][1].attrib:
                raise VendorError('duplicate carrier XML attribute')
            stack[-1][1].set(attribute[1], value)
            continue
        content = re.fullmatch(r"\s*T: '(.*)'", line)
        if content and stack:
            stack[-1][1].text = (stack[-1][1].text or '') + content[1]
            continue
        if line.strip():
            raise VendorError('unsupported carrier XML dump')
    if root is None or root.tag != 'carrier_config_list':
        raise VendorError('unexpected carrier resource root')
    ET.indent(root, space='    ')
    return ET.tostring(root, encoding='utf-8', xml_declaration=True) + b'\n'


def extract(apk, aapt2):
    """Return a bounded, deterministic set of XML files and their provenance.

    The caller authenticates the complete APK with its stock recipe before this
    step. Keep asset bytes and filter order. The stock service uses the same
    carrier-ID and mccmnc prefixes as Android; obsolete numeric-only assets
    are not used by either service and must not be renamed into active data.
    """
    if aapt2 is None:
        raise VendorError('stock carrier data requires the aapt2 executable')
    output, members, identities, ignored, repairs, total = {}, {}, set(), [], {}, 0
    try:
        archive = zipfile.ZipFile(apk)
    except zipfile.BadZipFile as error:
        raise VendorError('invalid carrier APK archive') from error
    with archive:
        if len(archive.namelist()) != len(set(archive.namelist())):
            raise VendorError('duplicate carrier APK member')
        for info in archive.infolist():
            if not info.filename.startswith('assets/carrier_config_'):
                continue
            name = info.filename.removeprefix('assets/')
            if not _NAME.fullmatch(name) or '/' in name or '\\' in name:
                raise VendorError('unsupported carrier asset name')
            if re.fullmatch(r'carrier_config_[0-9]{5,6}\.xml', name):
                ignored.append(info.filename)
                continue
            identity = re.sub(r'(carrier_config_carrierid_[0-9]+)_.*', r'\1', name)
            if identity in identities:
                raise VendorError('duplicate carrier identity')
            identities.add(identity)
            total += info.file_size
            if info.file_size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                raise VendorError('carrier data exceeds size limit')
            try:
                data = archive.read(info)
            except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as error:
                raise VendorError('unable to read carrier APK member') from error
            source_hash = hashlib.sha256(data).hexdigest()
            if name == 'carrier_config_no_sim.xml' and source_hash == NO_SIM_REPAIR_SHA256:
                data = b'<?xml version="1.0" encoding="utf-8"?>\n<carrier_config_list />\n'
                repairs[name] = {'source_sha256': source_hash,
                                 'reason': 'Close the stock empty no-SIM XML root'}
            try:
                decoded = data.decode('utf-8')
            except UnicodeDecodeError as error:
                raise VendorError('carrier assets must use UTF-8') from error
            if '\0' in decoded:
                raise VendorError('invalid carrier asset encoding')
            if b'<!DOCTYPE' in data or b'<!ENTITY' in data:
                raise VendorError('carrier XML declarations are not allowed')
            try:
                root = ET.fromstring(data)
            except ET.ParseError as error:
                # Some stock entries intentionally contain comments only. They
                # stay empty, preserving the service's fallback behavior.
                empty = re.sub(rb'<!--.*?-->|<\?xml\s.*?\?>', b'', data, flags=re.S).strip()
                if empty:
                    raise VendorError('malformed carrier asset') from error
            else:
                if root.tag not in ('carrier_config', 'carrier_config_list'):
                    raise VendorError('unexpected carrier asset root')
            # The numeric carrier ID is authoritative; the suffix is only a
            # display label. Soong's assets copy rule interpolates paths into
            # a shell script without quoting them. Never send stock labels
            # (spaces, apostrophes, &, parentheses, etc.) to that rule.
            output_name = (identity + '_device.xml'
                           if name.startswith('carrier_config_carrierid_') else name)
            output[output_name] = data
            members[output_name] = info.filename
        if not output:
            raise VendorError('carrier APK contains no carrier data')
    for name in ('vendor.xml', 'vendor_no_sim.xml'):
        try:
            result = subprocess.run([str(aapt2), 'dump', 'xmltree', '--file', 'res/xml/' + name,
                                     str(apk)], capture_output=True, timeout=30, check=False)
        except subprocess.SubprocessError as error:
            raise VendorError('carrier resource decoder did not complete') from error
        if result.returncode or len(result.stdout) > MAX_TOTAL_BYTES:
            raise VendorError('unable to decode stock carrier resource')
        output[name] = decode_xml_dump(result.stdout.decode('utf-8'))
        total += len(output[name])
        if len(output[name]) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise VendorError('decoded carrier data exceeds size limit')
        members[name] = 'res/xml/' + name
    return output, {
        'apk_sha256': hashlib.sha256(Path(apk).read_bytes()).hexdigest(),
        'aapt2_sha256': hashlib.sha256(Path(aapt2).read_bytes()).hexdigest(),
        'unused_legacy_members': sorted(ignored),
        'repairs': repairs,
        'files': {name: {'member': members[name], 'sha256': hashlib.sha256(data).hexdigest(),
                        'bytes': len(data)} for name, data in sorted(output.items())},
    }


def verify_apk(apk, provenance):
    """Compare built device assets to the authenticated generator's output record."""
    expected = provenance['carrier_data']['files']
    if (not isinstance(expected, dict) or not expected or len(expected) > 1024
            or Path(apk).stat().st_size > 64 * 1024 * 1024):
        raise VendorError('invalid carrier asset inventory')
    if any(not isinstance(row, dict) or type(row['bytes']) is not int for row in expected.values()) or sum(
            row['bytes'] for row in expected.values()) > MAX_TOTAL_BYTES:
        raise VendorError('carrier asset inventory exceeds size limit')
    prefix = 'assets/device-carrier-config/'
    with zipfile.ZipFile(apk) as archive:
        entries = archive.infolist()
        if len(entries) != len({entry.filename for entry in entries}):
            raise VendorError('duplicate APK member')
        actual = {entry.filename.removeprefix(prefix): entry for entry in entries
                  if entry.filename.startswith(prefix) and not entry.is_dir()}
        if set(actual) != set(expected):
            raise VendorError('built carrier assets differ from the selected inventory')
        for name, record in expected.items():
            if ('/' in name or '\\' in name or not name.endswith('.xml')
                    or not re.fullmatch(r'[a-f0-9]{64}', record['sha256'])
                    or actual[name].file_size != record['bytes']
                    or not 0 <= record['bytes'] <= MAX_FILE_BYTES):
                raise VendorError('invalid carrier asset identity')
            if hashlib.sha256(archive.read(actual[name])).hexdigest() != record['sha256']:
                raise VendorError('built carrier asset bytes differ')
    return len(expected)


def main():
    parser = argparse.ArgumentParser(description='Check built carrier assets against generator provenance')
    parser.add_argument('--apk', type=Path, required=True)
    parser.add_argument('--provenance', type=Path, required=True)
    args = parser.parse_args()
    try:
        with args.provenance.open('rb') as source:
            data = source.read(MAX_TOTAL_BYTES + 1)
        if len(data) > MAX_TOTAL_BYTES:
            raise VendorError('carrier provenance exceeds size limit')
        count = verify_apk(args.apk, json.loads(data))
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile):
        parser.exit(1, 'ERROR: carrier APK does not match supplied generation provenance\n')
    print(json.dumps({'status': 'PASS', 'device_assets': count,
                      'scope': 'packaged data comparison; not runtime or carrier acceptance'}))


if __name__ == '__main__':
    main()

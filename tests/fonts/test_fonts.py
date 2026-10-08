"""Font customization check: synthetic fonts in temporary directories, no network."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from diamaneos_tools import fonts

TALLY_WEIGHTS = '280,300,380,400,500,560,580,600,680,700,800,860,900'


def make_font(postscript='Test-Regular', italic=False, wght=(1, 400, 1000), os2=True):
    """A minimal sfnt: the tables the check reads, the others present and empty."""
    string = postscript.encode('utf-16-be')
    tables = {
        'name': struct.pack('>HHHHHHHHH', 0, 1, 18, 3, 1, 0x409, 6, len(string), 0) + string,
        'head': bytes(44) + struct.pack('>H', 2 if italic else 0) + bytes(8),
        'cmap': bytes(4), 'hhea': bytes(36), 'hmtx': bytes(4), 'maxp': bytes(6), 'glyf': bytes(4), 'loca': bytes(4),
    }
    if os2:
        tables['OS/2'] = bytes(62) + struct.pack('>H', 1 if italic else 0x40) + bytes(14)
    if wght:
        axis = b'wght' + b''.join(struct.pack('>i', round(v * 65536)) for v in wght) + struct.pack('>HH', 0, 256)
        tables['fvar'] = struct.pack('>HHHHHHHH', 1, 0, 16, 2, 1, 20, 0, 8) + axis
    header = struct.pack('>IHHHH', 0x00010000, len(tables), 0, 0, 0)
    offset = 12 + 16 * len(tables)
    directory, body = b'', b''
    for tag, data in sorted(tables.items()):
        directory += tag.encode('latin-1') + struct.pack('>III', 0, offset + len(body), len(data))
        body += data + bytes(-len(data) % 4)
    return header + directory + body


def collection(*members):
    """A TrueType collection of the given single fonts."""
    offsets, body = [], b''
    start = 12 + 4 * len(members)
    for member in members:
        offsets.append(start + len(body))
        body += member
    data = b'ttcf' + struct.pack('>HHI', 1, 0, len(members)) + b''.join(struct.pack('>I', o) for o in offsets)
    # Rebase each member's table offsets onto the collection.
    data = bytearray(data + body)
    for member_start in offsets:
        count = struct.unpack_from('>H', data, member_start + 4)[0]
        for i in range(count):
            record = member_start + 12 + 16 * i
            value = struct.unpack_from('>I', data, record + 8)[0]
            struct.pack_into('>I', data, record + 8, value + member_start)
    return bytes(data)


FAMILY = ('<family customizationType="new-named-family" name="{name}">'
          '<font weight="400" style="normal" supportedAxes="wght" postScriptName="{ps}-Regular">{ps}-Regular.ttf</font>'
          '<font weight="400" style="italic" supportedAxes="wght" postScriptName="{ps}-Italic">{ps}-Italic.ttf</font>'
          '</family>')


def document(*families):
    return ('<?xml version="1.0" encoding="utf-8"?>\n<fonts-modification version="1">'
            + ''.join(families) + '</fonts-modification>')


def named(fonts_xml, name='a', extra=''):
    return f'<family customizationType="new-named-family" name="{name}"{extra}>{fonts_xml}</family>'


REGULAR = '<font>Sans-Regular.ttf</font>'
OTHER_NAME = ('postScriptName="Sans-Regular"', 'postScriptName="Other"')


BLUEPRINT = '''
prebuilt_font {{ name: "{ps}-Regular.ttf", src: "{ps}-Regular.ttf", product_specific: true }}
prebuilt_font {{ name: "{ps}-Italic.ttf", src: "{ps}-Italic.ttf", product_specific: true }}
prebuilt_etc {{
    name: "Customization",
    src: "fonts_customization.xml",
    filename: "fonts_customization.xml",
    product_specific: true,
    required: ["{ps}-Regular.ttf", "{ps}-Italic.ttf"],
}}
'''


class Check(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def write_fonts(self, ps='Sans', italic_flag=True, wght=(1, 400, 1000)):
        (self.dir / f'{ps}-Regular.ttf').write_bytes(make_font(f'{ps}-Regular', False, wght))
        (self.dir / f'{ps}-Italic.ttf').write_bytes(make_font(f'{ps}-Italic', italic_flag, wght))

    def files(self, xml, *arguments):
        (self.dir / 'custom.xml').write_text(xml, encoding='utf-8')
        return self.run_main('--xml', str(self.dir / 'custom.xml'), '--font-dir', str(self.dir), *arguments)

    def module(self, xml, blueprint=None, *arguments):
        (self.dir / 'fonts_customization.xml').write_text(xml, encoding='utf-8')
        (self.dir / 'Android.bp').write_text(blueprint or BLUEPRINT.format(ps='Sans'), encoding='utf-8')
        return self.run_main('--module-dir', str(self.dir), *arguments)

    def run_main(self, *arguments):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = fonts.main([*arguments, '--json'])
        report = json.loads(out.getvalue()) if out.getvalue() else None
        return code, report, err.getvalue()

    def codes(self, report, severity='error'):
        return sorted({f['code'] for f in report['findings'] if f['severity'] == severity})

    def messages(self, report):
        return '\n'.join(f['message'] for f in report['findings'])


class Passing(Check):
    def test_module_with_two_variable_families(self):
        self.write_fonts('Sans')
        self.write_fonts('Narrow')
        blueprint = BLUEPRINT.format(ps='Sans').replace(
            'required: [', 'required: ["Narrow-Regular.ttf", "Narrow-Italic.ttf", ') + '\n'.join(
            f'prebuilt_font {{ name: "Narrow-{s}.ttf", src: "Narrow-{s}.ttf", product_specific: true }}'
            for s in ('Regular', 'Italic'))
        code, report, _ = self.module(document(FAMILY.format(name='sans', ps='Sans'),
                                               FAMILY.format(name='narrow', ps='Narrow')),
                                      blueprint, '--weights', TALLY_WEIGHTS, '--strict')
        self.assertEqual(code, 0, report['findings'])
        self.assertEqual([f['name'] for f in report['families']], ['sans', 'narrow'])
        fonts_ = report['families'][0]['members'][0]
        self.assertEqual([f['family_type'] for f in fonts_], ['TWO_FONTS_WGHT'] * 2)
        self.assertEqual(fonts_[1]['wght'], [1, 400, 1000])
        self.assertEqual(report['summary'], {'errors': 0, 'warnings': 0, 'families': 2, 'fonts': 4})

    def test_installed_files(self):
        self.write_fonts()
        code, report, _ = self.files(document(FAMILY.format(name='sans', ps='Sans')), '--strict')
        self.assertEqual(code, 0, report['findings'])
        self.assertEqual(report['source'], {'kind': 'files', 'xml': 'custom.xml', 'font_dir': self.dir.name})

    def test_font_name_whitespace_and_comments_as_android_reads_them(self):
        self.write_fonts()
        xml = document(named('<font weight="400">\n  Sans-<!-- x -->Regular.ttf\t</font>', 'sans'))
        code, report, _ = self.files(xml, '--strict')
        self.assertEqual(code, 0, report['findings'])
        self.assertEqual(report['families'][0]['members'][0][0]['postscript_name'], 'Sans-Regular')

    def test_collection_index(self):
        data = collection(make_font('One', wght=None), make_font('Two', wght=None))
        (self.dir / 'Pair.ttc').write_bytes(data)
        xml = document('<family customizationType="new-named-family" name="pair">'
                       '<font index="1" postScriptName="Two">Pair.ttc</font></family>')
        code, report, _ = self.files(xml, '--strict')
        self.assertEqual(code, 0, report['findings'])


class Fatal(Check):
    def test_not_well_formed_or_wrong_root_or_dtd(self):
        self.write_fonts()
        for xml in ('<fonts-modification><family>', document().replace('fonts-modification', 'familyset'),
                    '<!DOCTYPE fonts-modification><fonts-modification/>'):
            code, report, _ = self.files(xml)
            self.assertEqual((code, self.codes(report)), (1, ['xml']), xml)

    def test_family_attribute_rules(self):
        self.write_fonts()
        cases = {
            f'<family name="a">{REGULAR}</family>': 'without customizationType',
            f'<family customizationType="odd" name="a">{REGULAR}</family>': "unknown customizationType 'odd'",
            named(REGULAR, extra=' lang="ja"'): 'has lang',
            f'<family customizationType="new-named-family">{REGULAR}</family>': 'without a name',
            f'<family customizationType="new-locale-family" lang="ja">{REGULAR}</family>': 'without operation',
            (f'<family-list customizationType="new-named-family" name="l"><family variant="compact">{REGULAR}'
             '</family></family-list>'): 'has variant',
            '<alias name="x" to="sans" weight="bold"/>': 'not an integer',
        }
        for element, message in cases.items():
            code, report, _ = self.files(document(element))
            self.assertEqual((code, self.codes(report)), (1, ['boot']), element)
            self.assertIn(message, self.messages(report))

    def test_repeated_family_name(self):
        self.write_fonts()
        sans = FAMILY.format(name='sans', ps='Sans')
        code, report, _ = self.files(document(sans, sans))
        self.assertIn('two families named sans', self.messages(report))
        self.assertEqual(self.codes(report), ['boot'])

    def test_numbers_as_java_parses_them(self):
        self.write_fonts()
        # Mathematical digits (U+1D7DC...) are UTF-16 surrogate pairs: no Java digits.
        for weight in ('bold', ' 400', '400.0', '1_000', '4e2', '\U0001d7dc\U0001d7d8\U0001d7d8'):
            xml = document(named(f'<font weight="{weight}">Sans-Regular.ttf</font>'))
            self.assertEqual(self.codes(self.files(xml)[1]), ['boot'], weight)
        for weight in ('+400', '0400', '٤٠٠'):
            xml = document(named(f'<font weight="{weight}" supportedAxes="wght" '
                                 'postScriptName="Sans-Regular">Sans-Regular.ttf</font>'))
            self.assertEqual(self.codes(self.files(xml)[1]), [], weight)

    def test_weight_range_only_for_existing_files(self):
        self.write_fonts()
        template = '<family customizationType="new-named-family" name="a"><font weight="1001">{}</font>{}</family>'
        report = self.files(document(template.format('Sans-Regular.ttf', '')))[1]
        self.assertEqual(self.codes(report), ['boot'])
        # readFont drops a missing file before it builds the FontStyle.
        report = self.files(document(template.format('Missing.ttf', '<font>Sans-Regular.ttf</font>')))[1]
        self.assertEqual(self.codes(report), ['dropped'])

    def test_axis_rules_apply_even_to_missing_files(self):
        self.write_fonts()
        for axis in ('<axis tag="wg" stylevalue="1"/>', '<axis tag="wght"/>', '<axis tag="wght" stylevalue="1_0"/>'):
            xml = document(named(f'<font>Missing.ttf{axis}</font>'))
            self.assertEqual(self.codes(self.files(xml)[1]), ['boot'], axis)

    def test_two_fonts_of_one_style(self):
        self.write_fonts()
        xml = document(named('<font>Sans-Regular.ttf</font><font weight="400">Sans-Italic.ttf</font>'))
        report = self.files(xml)[1]
        self.assertIn('both weight 400 upright', self.messages(report))
        self.assertIn('boot', self.codes(report))

    def test_not_a_font_and_bad_index(self):
        (self.dir / 'Broken.ttf').write_bytes(b'\x00\x01\x00\x00' + bytes(8))
        (self.dir / 'Sans-Regular.ttf').write_bytes(make_font('Sans-Regular'))
        for font in ('Broken.ttf', '<![CDATA[Sans-Regular.ttf]]>'):
            index = ' index="2"' if 'Sans' in font else ''
            xml = document(named(f'<font{index}>{font}</font>'))
            report = self.files(xml)[1]
            self.assertEqual(self.codes(report), ['boot'], font)
            self.assertIn('IllegalArgumentException', self.messages(report))

    def test_unreadable_table(self):
        data = bytearray(make_font('Sans-Regular'))
        name_offset = data.find(b'name') + 8
        start = int.from_bytes(data[name_offset:name_offset + 4], 'big')
        data[start + 2:start + 4] = (40).to_bytes(2, 'big')  # 40 name records in a table that holds one
        (self.dir / 'Sans-Regular.ttf').write_bytes(bytes(data))
        report = self.files(document(named(REGULAR)))[1]
        self.assertEqual(self.codes(report), ['font-file'])
        self.assertIn('cannot read its name table', self.messages(report))

    def test_too_short_for_a_postscript_name(self):
        xml = document(named('<font>abc</font>'))
        self.assertIn('StringIndexOutOfBoundsException', self.messages(self.files(xml)[1]))


class Dropped(Check):
    def test_missing_file_and_empty_family(self):
        xml = document(named('<font>Missing.ttf</font>'))
        code, report, _ = self.files(xml)
        self.assertEqual((code, self.codes(report)), (1, ['dropped']))
        self.assertIn('named family a has no usable font', self.messages(report))

    def test_name_that_leaves_the_directory(self):
        self.write_fonts()
        xml = document(named('<font>../x/Sans-Regular.ttf</font>'))
        self.assertEqual(self.codes(self.files(xml)[1]), ['dropped', 'path'])

    def test_alias_without_target(self):
        self.write_fonts()
        code, report, _ = self.files(document(FAMILY.format(name='sans', ps='Sans'), '<alias name="x"/>'))
        self.assertEqual(self.codes(report), ['dropped'])


class FontTables(Check):
    def test_default_instance_and_requested_weights(self):
        self.write_fonts(wght=(100, 300, 900))
        code, report, _ = self.files(document(FAMILY.format(name='sans', ps='Sans')), '--weights', '50,400,950')
        self.assertEqual(self.codes(report), ['weight'])
        text = self.messages(report)
        self.assertIn('default wght instance 300', text)
        self.assertIn('weights 50, 950 are outside', text)

    def test_slant_and_postscript_name(self):
        self.write_fonts(italic_flag=False)
        xml = document(FAMILY.format(name='sans', ps='Sans').replace(*OTHER_NAME))
        code, report, _ = self.files(xml)
        self.assertEqual(self.codes(report), ['slant'])
        self.assertEqual(self.codes(report, 'warning'), ['postscript-name'])

    def test_italic_from_head_without_os2(self):
        (self.dir / 'Sans-Regular.ttf').write_bytes(make_font('Sans-Regular', False, os2=False))
        (self.dir / 'Sans-Italic.ttf').write_bytes(make_font('Sans-Italic', True, os2=False))
        code, report, _ = self.files(document(FAMILY.format(name='sans', ps='Sans')), '--strict')
        self.assertEqual(code, 0, report['findings'])

    def test_warnings_fail_only_with_strict(self):
        self.write_fonts()
        xml = document(FAMILY.format(name='sans', ps='Sans').replace(*OTHER_NAME))
        self.assertEqual(self.files(xml)[0], 0)
        self.assertEqual(self.files(xml, '--strict')[0], 1)

    def test_variable_family_types(self):
        self.write_fonts()
        (self.dir / 'Sans-Bold.ttf').write_bytes(make_font('Sans-Bold', False))
        three = FAMILY.format(name='sans', ps='Sans').replace(
            '</family>', '<font weight="700" supportedAxes="wght" postScriptName="Sans-Bold">Sans-Bold.ttf</font>'
            '</family>')
        report = self.files(document(three))[1]
        self.assertEqual(self.codes(report, 'warning'), ['static-family'])
        italic_first = document(named(
            '<font style="italic" supportedAxes="wght" postScriptName="Sans-Italic">Sans-Italic.ttf</font>'
            '<font supportedAxes="wght" postScriptName="Sans-Regular">Sans-Regular.ttf</font>', 'sans'))
        report = self.files(italic_first)[1]
        self.assertEqual(self.codes(report, 'warning'), ['font-order'])
        single = document('<family customizationType="new-named-family" name="s"><font supportedAxes="wght,ital" '
                          'postScriptName="Sans-Regular">Sans-Regular.ttf</font></family>')
        report = self.files(single)[1]
        self.assertEqual(report['families'][0]['members'][0][0]['family_type'], 'SINGLE_FONT_WGHT_ITAL')
        self.assertEqual(self.codes(report, 'warning'), ['no-axis'])


class Module(Check):
    def test_font_not_product_or_not_required_or_unused(self):
        self.write_fonts()
        (self.dir / 'Extra.ttf').write_bytes(make_font('Extra'))
        blueprint = (BLUEPRINT.format(ps='Sans')
                     .replace('name: "Sans-Italic.ttf", src: "Sans-Italic.ttf", product_specific: true',
                              'name: "Sans-Italic.ttf", src: "Sans-Italic.ttf"')
                     .replace('"Sans-Regular.ttf", "Sans-Italic.ttf"]', '"Sans-Italic.ttf"]')
                     + 'prebuilt_font { name: "Extra.ttf", src: "Extra.ttf", product_specific: true }\n')
        code, report, _ = self.module(document(FAMILY.format(name='sans', ps='Sans')), blueprint)
        text = self.messages(report)
        self.assertIn('Sans-Italic.ttf: Sans-Italic.ttf is not product_specific', text)
        self.assertIn('does not require Sans-Regular.ttf', text)
        self.assertEqual(self.codes(report), ['not-installed'])
        self.assertEqual(self.codes(report, 'warning'), ['unused-font'])

    def test_customization_module_rules(self):
        self.write_fonts()
        xml = document(FAMILY.format(name='sans', ps='Sans'))
        missing = self.module(xml, 'prebuilt_font { name: "Sans-Regular.ttf", src: "Sans-Regular.ttf" }')[1]
        self.assertIn('no prebuilt_etc installs fonts_customization.xml', self.messages(missing))
        not_product = self.module(xml, BLUEPRINT.format(ps='Sans').replace(
            '    product_specific: true,\n    required', '    sub_dir: "x",\n    required'))[1]
        text = self.messages(not_product)
        self.assertIn('is not product_specific', text)
        self.assertIn('sets sub_dir', text)
        code, _, err = self.module(xml, 'prebuilt_etc {')
        self.assertEqual(code, 2)
        self.assertIn('Android.bp', err)

    def test_source_record(self):
        self.write_fonts()
        data = (self.dir / 'Sans-Regular.ttf').read_bytes()
        (self.dir / 'source.json').write_text(json.dumps({'files': [
            {'path': 'Sans-Regular.ttf', 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()},
            {'path': 'Sans-Italic.ttf', 'bytes': 1, 'sha256': '0' * 64},
        ]}))
        report = self.module(document(FAMILY.format(name='sans', ps='Sans')))[1]
        self.assertEqual(self.codes(report), ['source'])
        self.assertIn('Sans-Italic.ttf: SHA-256 differs', self.messages(report))
        (self.dir / 'source.json').write_text(json.dumps({'files': []}))
        report = self.module(document(FAMILY.format(name='sans', ps='Sans')))[1]
        self.assertEqual(self.codes(report, 'warning'), ['unrecorded'])


class Numbers(unittest.TestCase):
    def test_java_int(self):
        self.assertEqual([fonts.java_int(t) for t in ('400', '+400', '-1', '٤٠٠', '2147483647')],
                         [400, 400, -1, 400, 2147483647])
        for text in (' 400', '1_000', '4.0', '', '2147483648', None, '\U0001d7dc\U0001d7d8\U0001d7d8', '4\U0001d7d80'):
            self.assertIsNone(fonts.java_int(text), text)

    def test_java_float(self):
        cases = {'1.5f': 1.5, '0x1p3': 8.0, ' 12 ': 12.0, '.5': 0.5, '1.': 1.0, '-Infinity': float('-inf'),
                 '1e3d': 1000.0}
        for text, value in cases.items():
            self.assertEqual(fonts.java_float(text), value, text)
        for text in ('1_0', 'abc', '', '1e', 'NaNf', '١', None):
            self.assertIsNone(fonts.java_float(text), text)


class CommandLine(unittest.TestCase):
    def run_cli(self, *arguments):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        return subprocess.run([str(ROOT / 'bin/diamaneos'), 'fonts', 'check', *arguments],
                              capture_output=True, text=True, timeout=60, cwd=ROOT, env=env)

    def test_routed_text_report_and_usage_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / 'Sans-Regular.ttf').write_bytes(make_font('Sans-Regular'))
            (directory / 'custom.xml').write_text(document(
                '<family customizationType="new-named-family" name="sans"><font supportedAxes="wght" '
                'postScriptName="Sans-Regular">Sans-Regular.ttf</font></family>'))
            result = self.run_cli('--xml', str(directory / 'custom.xml'), '--font-dir', str(directory))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('SINGLE_FONT_WGHT_ONLY', result.stdout)
            self.assertIn('PASS: 0 errors, 0 warnings (1 families, 1 fonts checked)', result.stdout)
            self.assertEqual(self.run_cli('--xml', str(directory / 'custom.xml')).returncode, 2)
            self.assertEqual(self.run_cli('--module-dir', temp, '--font-dir', temp).returncode, 2)
            result = self.run_cli('--xml', str(directory / 'custom.xml'), '--font-dir', temp, '--weights', '0')
            self.assertEqual(result.returncode, 2)
            self.assertIn('--weights', result.stderr)

    def test_help_lists_the_command(self):
        result = subprocess.run([str(ROOT / 'bin/diamaneos'), '--help'], capture_output=True, text=True, timeout=30)
        self.assertIn('fonts check', result.stdout)


if __name__ == '__main__':
    unittest.main()

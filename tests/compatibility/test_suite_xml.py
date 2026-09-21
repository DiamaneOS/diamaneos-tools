import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from diamaneos_tools.suite_xml import parse_config, SuiteXmlError


class SuiteXmlTest(unittest.TestCase):
    def test_literal_internal_entity_and_comment_marker(self):
        data = b'''<!DOCTYPE configuration [<!ENTITY dir "/data/local/tmp/synthetic">]>
        <configuration><!-- <!ENTITY is text here -->
        <option name="path" value="&dir;/file"/><test class="example.Runner"/>
        </configuration>'''
        self.assertEqual(['example.Runner'], parse_config(data))

    def test_external_parameter_nested_and_excessive_entities_fail(self):
        declarations = [
            '<!ENTITY x SYSTEM "file:///etc/passwd">',
            '<!ENTITY % x "literal">',
            '<!ENTITY x "&y;"><!ENTITY y "nested">',
            '<!ENTITY x "' + 'a' * 32769 + '">',
            ''.join(f'<!ENTITY x{i} "x">' for i in range(33)),
        ]
        for declaration in declarations:
            with self.subTest(declaration=declaration[:80]), self.assertRaises(SuiteXmlError):
                parse_config(('<!DOCTYPE configuration [' + declaration + ']><configuration/>').encode())
        with self.assertRaises(SuiteXmlError):
            parse_config(b'<!DOCTYPE configuration SYSTEM "https://example.invalid/a"><configuration/>')

    def test_limits_invalid_root_and_missing_runner(self):
        for data in [b'', b'<other/>', b'<configuration><test/></configuration>',
                     b'<configuration>' + b'<a>' * 65 + b'</a>' * 65 + b'</configuration>',
                     b'x' * (16 * 1024**2 + 1)]:
            with self.assertRaises(SuiteXmlError):
                parse_config(data)

    def test_repeated_literal_expansion_is_bounded(self):
        declaration = b'<!DOCTYPE configuration [<!ENTITY x "' + b'a' * 32768 + b'">]>'
        with self.assertRaises(SuiteXmlError):
            parse_config(declaration + b'<configuration>' + b'&x;' * 1025 + b'</configuration>')

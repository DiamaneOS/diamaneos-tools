"""String resource check: small resource trees and a local git repository, no network."""
import contextlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from diamaneos_tools import strings

HEADER = '<resources xmlns:xliff="urn:oasis:names:tc:xliff:document:1.2">\n'


class StringsCheck(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.res = Path(self.tmp.name) / 'res'

    def write(self, config, body, name='strings.xml'):
        path = self.res / config / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(HEADER + body + '\n</resources>\n', encoding='utf-8')

    def findings(self, *argv):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = strings.main(['--json', *argv])
        report = __import__('json').loads(output.getvalue())
        return code, {(f['code'], f['resource'].split('/', 1)[1].split(' ')[0]) for f in report['findings']}

    def test_aapt2_escapes_and_formats(self):
        self.write('values', '<string name="apostrophe">It\'s</string>'
                             '<string name="escaped">It\\\'s "it\'s"</string>'
                             '<string name="unicode">\\u00e</string>'
                             '<string name="nonpositional">%s and %s</string>'
                             '<string name="positional">%1$s and %2$s, 100%%</string>'
                             '<string name="question">Why?</string>')
        self.write('values-de', '<string name="question">?Warum</string>'
                                '<string name="escaped">Es ist "so"</string>')
        code, found = self.findings(str(self.res))
        self.assertEqual(code, 1)
        self.assertEqual(found, {('escape', 'apostrophe'), ('escape', 'unicode'), ('format', 'nonpositional'),
                                 ('reference', 'question'), ('quote', 'escaped')})

    def test_translation_placeholders_markup_and_defaults(self):
        self.write('values', '<string name="files"><xliff:g id="user">%1$s</xliff:g> has %2$d files</string>'
                             '<string name="link">Read <annotation id="url">more</annotation></string>'
                             '<string name="plain">Wi-Fi settings</string>')
        self.write('values-de', '<string name="files">%2$d Dateien hat <xliff:g id="user">%1$s</xliff:g></string>'
                                '<string name="link">Mehr <b>lesen</b></string>'
                                '<string name="plain">Wi-Fi settings</string>'
                                '<string name="orphan">Waise</string>')
        self.write('values-fr', '<string name="files">%1$d a %2$d fichiers à 100%</string>')
        self.write('values-es', '<string name="files">Archivos de %1$s</string>')
        self.write('values-en-rGB', '<string name="plain">Wi-Fi settings</string>')
        code, found = self.findings(str(self.res))
        self.assertEqual(code, 1)
        self.assertEqual(found, {('markup', 'link'), ('english', 'plain'), ('no-default', 'orphan'),
                                 ('placeholder', 'files'), ('format', 'files'), ('dropped', 'files'),
                                 ('xliff', 'files')})

    def test_plurals_and_message_format(self):
        self.write('values', '<plurals name="count"><item quantity="one">One file</item>'
                             '<item quantity="other">%d files</item></plurals>'
                             '<string name="icu">{count, plural, =1 {# song by {artist}} other {# songs}}</string>'
                             '<string name="icu_no_other">{count, plural, =1 {# song}}</string>')
        self.write('values-de', '<plurals name="count"><item quantity="one">Eine Datei</item>'
                                '<item quantity="other">%d Dateien</item></plurals>'
                                '<string name="icu">{count, plural, =1 {# Lied von {wer}} other {# Lieder}}</string>')
        self.write('values-fr', '<plurals name="count"><item quantity="one">%d fichier</item>'
                                '<item quantity="many">%d fichiers</item></plurals>'
                                '<string name="icu">{count, plural, =1 {l\\\'{artist}} other {# chansons}}</string>')
        code, found = self.findings(str(self.res))
        self.assertEqual(code, 1)
        self.assertEqual(found, {('icu', 'icu_no_other'), ('icu', 'icu'), ('dropped', 'icu'), ('plural', 'count')})

    def test_git_checks_only_changed_names_in_every_locale(self):
        repo = Path(self.tmp.name)
        git = lambda *a: subprocess.run(['git', '-C', str(repo), '-c', 'commit.gpgsign=false', '-c', 'user.name=t',
                                         '-c', 'user.email=t@t', *a], check=True, capture_output=True)
        git('init', '-q')
        self.write('values', '<string name="changed">%1$s</string><string name="stock">%1$s</string>')
        self.write('values-de', '<string name="changed">%1$s</string><string name="stock">%1$d</string>')
        git('add', '.')
        git('commit', '-q', '-m', 'base')
        self.write('values', '<string name="changed">%1$d</string><string name="stock">%1$s</string>')
        self.write('values', '<string name="added">It\'s new</string>', name='tally_strings.xml')
        git('add', '.')
        git('commit', '-q', '-m', 'head')
        code, found = self.findings('--repo', str(repo), '--base', 'HEAD~1')
        self.assertEqual(code, 1)
        self.assertEqual(found, {('placeholder', 'changed'), ('escape', 'added')})


if __name__ == '__main__':
    unittest.main()

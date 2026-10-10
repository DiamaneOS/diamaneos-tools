"""Docs stay in short blocks: no list item or paragraph longer than the word limit.

A block is one list item or one paragraph. Headings, tables, quotes and code fences are not
counted. Split a long block into sub-bullets instead of extending it.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
MAX_WORDS = 45
ITEM = re.compile(r'^\s*([-*]|\d+\.)\s+(.*)$')
SKIP = ('#', '|', '>', '<!--')


def blocks(text):
    """Yield (line number, text) for every list item and paragraph."""
    current, start, fence = [], 0, False
    for number, line in enumerate(text.split('\n'), 1):
        stripped = line.lstrip()
        if stripped.startswith('```'):
            fence = not fence
            stripped = ''
        item = None if fence else ITEM.match(line)
        if fence or item or not stripped or stripped.startswith(SKIP):
            if current:
                yield start, ' '.join(current)
            current, start = ([item.group(2)], number) if item else ([], 0)
        else:
            if not current:
                start = number
            current.append(stripped)
    if current:
        yield start, ' '.join(current)


def documents():
    return sorted([*ROOT.glob('*.md'), *(ROOT / 'docs').glob('*.md')])


class DocLengthTests(unittest.TestCase):
    def test_documents_are_found(self):
        names = {path.name for path in documents()}
        self.assertLessEqual({'README.md', 'TERMS.md', 'BUILD.md'}, names)

    def test_no_block_exceeds_the_word_limit(self):
        long_blocks = []
        for path in documents():
            for line, text in blocks(path.read_text()):
                words = len(text.split())
                if words > MAX_WORDS:
                    long_blocks.append(f'{path.relative_to(ROOT)}:{line}: {words} words: {text[:60]}...')
        self.assertEqual(long_blocks, [], f'blocks over {MAX_WORDS} words; split them into sub-bullets')

    def test_blocks_skips_code_tables_and_headings(self):
        text = '# Title\n\n| a | b |\n\n```\n' + 'word ' * 80 + '\n```\n\n- one two\n  three\n\nfour five\n'
        self.assertEqual(list(blocks(text)), [(9, 'one two three'), (12, 'four five')])


if __name__ == '__main__':
    unittest.main()

"""Check Android string resources for escaping, placeholder and markup mistakes.

Reads the values*/ XML files of resource directories: every string in
directories on disk, or with --repo and --base only the strings, plurals and
string arrays changed between two Git revisions, in every configuration of
their resource directory (read from Git objects). Nothing is built.

Errors (exit 1):
  xml           not well-formed XML, or an element aapt2 rejects
  escape        aapt2 rejects the text: an unescaped apostrophe or a bad \\u escape
  reference     a translation that aapt2 reads as a resource reference
  format        several non-positional placeholders (aapt2 rejects them), or a %
                that java.util.Formatter rejects in a string with placeholders
  placeholder   a translation has a placeholder (%1$s, ^1) the default lacks, or
                of another type
  icu           a MessageFormat text with an argument the default lacks, unbalanced
                braces, an apostrophe that quotes a placeholder, or no other case
  markup        a translation's <a> or <annotation> spans differ from the default
  plural        no other quantity, or an unknown or repeated quantity
  array         a translated string array has another number of items
  duplicate     a name defined twice for one configuration
  no-default    a translation of a name without a default
Warnings (exit 1 only with --strict):
  dropped       a translation drops a placeholder (not reported for zero, one, two)
  style         other spans or HTML tags differ from the default, or are unbalanced
  xliff         <xliff:g> ids or contents differ from the default
  quote         unescaped double quotes inside the text, which aapt2 drops
  english       a translation that repeats the English default
  untranslatable  a translation of a name marked translatable="false"
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

XLIFF_G = '{urn:oasis:names:tc:xliff:document:1.2}g'
FEATURE_FLAG = '{http://schemas.android.com/apk/res/android}featureFlag'
EMPTY_TREE = '4b825dc642cb6eb9a060e54bf8d69288fbee4904'
VALUES_FILE = re.compile(r'^(?:(.+)/)?(values(?:-[^/]+)?)/([^/]+\.xml)$')
QUANTITIES = ('zero', 'one', 'two', 'few', 'many', 'other')
OPTIONAL_QUANTITIES = {'zero', 'one', 'two'}
RESOURCE_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_.]*$')
LOCALE = re.compile(r'^(?:[a-z]{2,3}(?:-r(?:[A-Z]{2}|[0-9]{3}))?|b\+[a-z]{2,3}(?:\+[A-Za-z0-9]+)*)$')
PSEUDO_LOCALES = {'en-rXA', 'ar-rXB', 'en-rXC'}
# java.util.Formatter: an index or <, flags, width, precision and a known conversion.
JAVA_SPEC = re.compile(r'%(\d+\$|<)?([-#+ 0,(<]*)(\d+)?(\.\d+)?([tT][a-zA-Z]|[bBhHsScCdoxXeEfgGaA%n])')
# What aapt2 parses as a reference (ResourceUtils::TryParseItemForAttribute) instead of text.
REFERENCE = re.compile(r'^(?:@[+*]?(?:[\w.]+:)?[a-z-]+/[\w.]+|@null|@empty|\?(?:[\w.]+:)?(?:attr/)?[\w.]+)$')
HTML_TAG = re.compile(r'<\s*(/?)\s*(a|b|i|u|em|strong|small|big|sub|sup|tt|s|strike|del|font|span|p|br|li|ul|ol'
                      r'|h[1-6]|annotation)\b[^<>]*?(/?)>', re.I)
FUNCTIONAL_SPANS = {'a', 'annotation'}
# TextUtils.expandTemplate's ^1 to ^9. ^^ is a literal caret.
TEMPLATE_ARG = re.compile(r'\^(\^|[1-9])')


def finding(severity, code, message, entry=None, path=None):
    if entry is not None:
        path = entry.path
    return {'severity': severity, 'code': code, 'file': path,
            'resource': entry.label if entry is not None else None, 'message': message}


class CheckError(Exception):
    pass


class _NoDoctype(ET.TreeBuilder):
    def doctype(self, name, pubid, system):
        raise ET.ParseError('DTDs are not accepted')


def parse_xml(data):
    parser = ET.XMLParser(target=_NoDoctype())
    parser.feed(data)
    return parser.close()


def locale_of(config):
    """The locale qualifier of a values directory name, or None."""
    parts = config.split('-')[1:]
    while parts and re.fullmatch(r'mcc\d+|mnc\d+', parts[0]):
        parts.pop(0)
    if not parts or parts[0] == 'car':
        return None
    if len(parts) > 1 and re.fullmatch(r'r(?:[A-Z]{2}|[0-9]{3})', parts[1]) and LOCALE.match('-'.join(parts[:2])):
        return '-'.join(parts[:2])
    return parts[0] if LOCALE.match(parts[0]) else None


def default_config(config):
    locale = locale_of(config)
    return config.replace('-' + locale, '', 1) if locale else config


class Text:
    """aapt2's StringBuilder: the text of an element after escapes, quotes and spaces."""

    def __init__(self, element):
        self.chars, self.raw, self.quote, self.error, self.space = [], [], False, None, False
        self.spans, self.xliff = [], []
        self._walk(element)
        self.text = ''.join(self.chars).strip()
        self.raw_text = ''.join(self.raw).strip()

    def _walk(self, element):
        self._add(element.text or '')
        for child in element:
            if child.tag == XLIFF_G:
                # Translation tools upper-case ids and number repeated ones (DEVICE_NAME_0).
                xliff_id = re.sub(r'_\d+$', '', (child.get('id') or '').lower())
                self.xliff.append((xliff_id, Text(child).text))
            elif isinstance(child.tag, str):
                tag = child.tag.rsplit('}', 1)[-1]
                key = child.get('href') if tag == 'a' else sorted(child.attrib.items()) if tag == 'annotation' else ''
                self.spans.append(f'{tag} {key}'.strip())
            self._walk(child)
            self._add(child.tail or '')

    def _add(self, raw):
        self.raw.append(raw)
        i = 0
        while i < len(raw) and not self.error:
            c = raw[i]
            i += 1
            if c in ' \t\n\r\f\v' and not self.quote:
                if not self.space:
                    self.chars.append(' ')
                self.space = True
                continue
            self.space = False
            if c == '\\':
                if i == len(raw):
                    continue
                c = raw[i]
                i += 1
                if c == 'u':
                    digits = raw[i:i + 4]
                    if not re.fullmatch(r'[0-9a-fA-F]{4}', digits):
                        self.error = 'a \\u escape without four hex digits'
                        return
                    self.chars.append(chr(int(digits, 16)))
                    i += 4
                else:
                    self.chars.append({'t': '\t', 'n': '\n'}.get(c, c))
            elif c == '"':
                self.quote = not self.quote
            elif c == "'" and not self.quote:
                self.error = 'an unescaped apostrophe'
                return
            else:
                self.chars.append(c)

    def stray_quotes(self):
        """Unescaped double quotes inside a text segment, which aapt2 drops, or an unclosed quote."""
        total = 0
        for segment in self.raw:
            bare = re.sub(r'\\.', '', segment).strip()
            total += bare.count('"')
            if '"' in bare[1:-1]:
                return True
        return total % 2 == 1


def java_format(text):
    """{argument index: conversions} of a java.util.Formatter pattern, and rejected % sequences."""
    args, bad, ordinary, last = {}, [], 0, None
    i = text.find('%')
    while i >= 0:
        match = JAVA_SPEC.match(text, i)
        if not match:
            bad.append(text[i:i + 3])
            i = text.find('%', i + 1)
            continue
        index, flags, conversion = match.group(1), match.group(2), match.group(5)
        if conversion not in ('%', 'n'):
            if index == '<' or '<' in flags:
                position = last
            elif index:
                position = int(index[:-1])
            else:
                ordinary += 1
                position = ordinary
            if position is not None:
                args.setdefault(position, set()).add(conversion[0].lower())
                last = position
        i = text.find('%', match.end())
    return args, bad


def aapt2_positional(text):
    """util::VerifyJavaStringFormat: False for several arguments that are not all positional."""
    count, nonpositional, i = 0, False, 0
    while i < len(text):
        if text[i] == '%' and i + 1 < len(text):
            i += 1
            if text[i] in '%n':
                i += 1
                continue
            count += 1
            digits = re.match(r'\d*', text[i:]).group()
            i += len(digits)
            if digits:
                if i < len(text) and text[i] != '$':
                    nonpositional = True
            elif i < len(text) and text[i] == '<':
                nonpositional = True
                i += 1
                if i < len(text) and text[i] == '$':
                    i += 1
            else:
                nonpositional = True
            while i < len(text) and text[i] in '-#+ ,(0123456789':
                i += 1
            if i < len(text) and text[i] in 'DK':
                return True
        i += 1
    return not (count > 1 and nonpositional)


ICU_SELECT = re.compile(r'\{\s*\w+\s*,\s*(?:plural|select|selectordinal)\s*,')
_ICU_NAME = re.compile(r'\s*([^\s,{}]+)\s*([,}])')
_ICU_TYPE = re.compile(r'\s*(\w+)\s*([,}])')
_ICU_CASE = re.compile(r'\s*(?:(\})|(?:offset:\s*\d+\s*)?(=?[^\s{}]+)\s*\{)')


def icu_arguments(text):
    """Argument names of an ICU MessageFormat pattern (apostrophe mode DOUBLE_OPTIONAL).
    Raises ValueError for a pattern ICU rejects or a placeholder an apostrophe quotes."""
    names = set()
    if _icu_message(text, 0, names, nested=False, plural=False) != len(text):
        raise ValueError('a } without {')
    return names


def _icu_message(text, i, names, nested, plural):
    while i < len(text):
        c = text[i]
        if c == "'":
            follower = text[i + 1:i + 2]
            if follower == "'":
                i += 2
                continue
            if follower in ('{', '}') or (plural and follower == '#'):
                raise ValueError('an apostrophe quotes a placeholder (write two apostrophes)')
        elif c == '{':
            i = _icu_argument(text, i + 1, names)
            continue
        elif c == '}':
            if nested:
                return i
            raise ValueError('a } without {')
        i += 1
    if nested:
        raise ValueError('a { without }')
    return i


def _icu_argument(text, i, names):
    match = _ICU_NAME.match(text, i)
    if not match:
        raise ValueError('an argument without a name')
    names.add(match.group(1))
    if match.group(2) == '}':
        return match.end()
    kind = _ICU_TYPE.match(text, match.end())
    if not kind:
        raise ValueError(f'argument {match.group(1)} has no type')
    if kind.group(2) == '}':
        return kind.end()
    i = kind.end()
    if kind.group(1) not in ('plural', 'selectordinal', 'select'):
        depth = 1
        while i < len(text) and depth:
            depth += {'{': 1, '}': -1}.get(text[i], 0)
            i += 1
        if depth:
            raise ValueError('a { without }')
        return i
    cases = set()
    while True:
        case = _ICU_CASE.match(text, i)
        if not case:
            raise ValueError(f'a {kind.group(1)} case without a message')
        if case.group(1):
            if 'other' not in cases:
                raise ValueError(f'a {kind.group(1)} without an other case')
            return case.end()
        cases.add(case.group(2))
        i = _icu_message(text, case.end(), names, nested=True, plural=kind.group(1) != 'select') + 1


def icu_or_none(text):
    if '{' not in text or java_format(text)[0]:
        return None
    try:
        return icu_arguments(text) or None
    except ValueError:
        return None


def html_tags(text):
    return Counter(f'{close}{name.lower()}' for close, name, _ in HTML_TAG.findall(text))


def html_unbalanced(tags):
    return sorted(n for n in tags if not n.startswith('/') and n not in ('br', 'p') and tags[n] != tags['/' + n])


class Entry:
    def __init__(self, path, config, kind, name, product, element, translatable):
        self.path, self.config, self.kind, self.name = path, config, kind, name
        self.product, self.element, self.translatable = product, element, translatable
        self.flag = element.get(FEATURE_FLAG)
        self.locale = locale_of(config)
        self.label = f'{kind}/{name}' + (f' product={product}' if product else '') + \
            (f' flag={self.flag}' if self.flag else '')
        if kind == 'string':
            self.units = [(None, Text(element))]
        else:
            self.units = [(item.get('quantity') if kind == 'plurals' else n, Text(item))
                          for n, item in enumerate(i for i in element if i.tag == 'item')]

    def key(self):
        return self.kind, self.name

    def signature(self):
        return _signature(self.element), self.translatable


def _signature(element):
    return (element.tag, tuple(sorted(element.attrib.items())), element.text or '',
            tuple((_signature(c), c.tail or '') for c in element))


def read_entries(path, config, data, findings):
    """The string, plurals and string-array entries of one values file."""
    try:
        root = parse_xml(data)
    except ET.ParseError as error:
        findings.append(finding('error', 'xml', f'not well-formed XML ({error})', path=path))
        return []
    if root.tag != 'resources':
        findings.append(finding('error', 'xml', f'the root element is <{root.tag}>, not <resources>', path=path))
        return []
    untranslatable_file = Path(path).name.startswith('donottranslate')
    result = []
    for element in root:
        kind = {'string': 'string', 'plurals': 'plurals', 'string-array': 'array'}.get(element.tag)
        if element.tag == 'item' and element.get('type') == 'string':
            kind = 'string'
        if kind is None:
            continue
        name = element.get('name')
        if not name or not RESOURCE_NAME.match(name):
            findings.append(finding('error', 'xml', f'<{element.tag}> with an invalid name {name!r}', path=path))
            continue
        translatable = element.get('translatable') != 'false' and not untranslatable_file
        result.append(Entry(path, config, kind, name, element.get('product'), element, translatable))
    return result


class ResourceDir:
    """The entries of one resource directory, by configuration."""

    def __init__(self, label, files, findings, overlay=False):
        self.label, self.overlay, self.entries = label, overlay, []
        for (config, name), data in sorted(files.items()):
            self.entries += read_entries(f'{label}/{config}/{name}', config, data, findings)
        self.by_config = {}
        for entry in self.entries:
            self.by_config.setdefault(entry.config, {}).setdefault(entry.key(), []).append(entry)

    def default_of(self, entry):
        for config in dict.fromkeys((default_config(entry.config), 'values')):
            candidates = sorted(self.by_config.get(config, {}).get(entry.key(), []),
                                key=lambda e: e.flag is not None)
            for product in (entry.product, 'default', None):
                for candidate in candidates:
                    if candidate.product == product:
                        return candidate
            if candidates:
                return candidates[0]
        for config, keys in self.by_config.items():
            if locale_of(config) is None and entry.key() in keys:
                return keys[entry.key()][0]
        return None


def _compare(entry, default, findings):
    default_units = dict(default.units)
    for unit, text in entry.units:
        if default.kind == 'plurals':
            reference = [t for _, t in default.units]
        elif unit in default_units:
            reference = [default_units[unit]]
        else:
            continue
        where = f' (item {unit})' if unit is not None else ''
        expected = {}
        for ref in reference:
            for index, conversions in java_format(ref.text)[0].items():
                expected.setdefault(index, set()).update(conversions)
        args = java_format(text.text)[0]
        templates = {a for ref in reference for a in TEMPLATE_ARG.findall(ref.text)} - {'^'}
        if templates:
            mine = set(TEMPLATE_ARG.findall(text.text)) - {'^'}
            if mine - templates:
                findings.append(finding('error', 'placeholder', f'^{", ^".join(sorted(mine - templates))} '
                                        f'is not in the default{where}', entry))
            if templates - mine and unit not in OPTIONAL_QUANTITIES:
                findings.append(finding('warning', 'dropped', f'drops ^{", ^".join(sorted(templates - mine))}{where}',
                                        entry))
        for index in sorted(set(args) - set(expected)):
            findings.append(finding('error', 'placeholder', f'argument {index} is not in the default{where}', entry))
        for index in sorted(set(args) & set(expected)):
            if not args[index] <= expected[index]:
                findings.append(finding('error', 'placeholder', f'argument {index} is %{"/%".join(sorted(args[index]))}'
                                        f', the default %{"/%".join(sorted(expected[index]))}{where}', entry))
        missing = sorted(set(expected) - set(args))
        if missing and unit not in OPTIONAL_QUANTITIES:
            findings.append(finding('warning', 'dropped', f'drops argument {", ".join(map(str, missing))}{where}',
                                    entry))
        reference_names = set()
        for ref in reference:
            reference_names |= icu_or_none(ref.text) or set()
        if reference_names:
            try:
                names = icu_arguments(text.text)
            except ValueError as error:
                findings.append(finding('error', 'icu', f'{error}{where}', entry))
            else:
                if names - reference_names:
                    findings.append(finding('error', 'icu', 'arguments not in the default: '
                                            f'{", ".join(sorted(names - reference_names))}{where}', entry))
                if reference_names - names and unit not in OPTIONAL_QUANTITIES:
                    findings.append(finding('warning', 'dropped', 'drops '
                                            f'{", ".join(sorted(reference_names - names))}{where}', entry))
        counterpart = default_units.get(unit, default_units.get('other') if default.kind == 'plurals' else None)
        if counterpart is not None:
            _markup(entry, counterpart, text, where, findings)


def _markup(entry, ref, text, where, findings):
    mine, theirs = Counter(text.spans) + html_tags(text.text), Counter(ref.spans) + html_tags(ref.text)
    if mine != theirs:
        differ = sorted((mine - theirs) + (theirs - mine))
        functional = any(d.lstrip('/').split(' ')[0].lower() in FUNCTIONAL_SPANS for d in differ)
        findings.append(finding('error' if functional else 'warning', 'markup' if functional else 'style',
                                f'spans differ from the default: {", ".join(differ)}{where}', entry))
    if set(i for i, _ in text.xliff) != set(i for i, _ in ref.xliff):
        findings.append(finding('warning', 'xliff', f'<xliff:g> ids differ from the default{where}', entry))
    elif sorted(text.xliff) != sorted(ref.xliff):
        findings.append(finding('warning', 'xliff', f'<xliff:g> contents differ from the default{where}', entry))


def _english(entry, default):
    if entry.locale is None or entry.locale.split('-')[0] == 'en' or entry.locale in PSEUDO_LOCALES:
        return False
    mine, theirs = [t.text for _, t in entry.units], [t.text for _, t in default.units]
    return mine == theirs and any(re.search(r'[^\W\d_]{3}', JAVA_SPEC.sub('', t)) for t in mine)


def check_entry(entry, resources, findings):
    default = resources.default_of(entry) if entry.locale else entry
    formatted = default is not None and any(java_format(t.text)[0] or '%%' in t.text for _, t in default.units)
    for unit, text in entry.units:
        where = f' (item {unit})' if unit is not None else ''
        if text.error:
            findings.append(finding('error', 'escape', f'aapt2 rejects {text.error}{where}', entry))
            continue
        if text.stray_quotes():
            findings.append(finding('warning', 'quote', f'unescaped double quotes are dropped{where}', entry))
        if entry.kind == 'string' and entry.element.get('formatted') != 'false' and entry.translatable \
                and not aapt2_positional(text.text):
            findings.append(finding('error', 'format', 'several non-positional placeholders, which aapt2 rejects',
                                    entry))
        bad = java_format(text.text)[1]
        if bad and formatted:
            findings.append(finding('error', 'format', f'{bad[0]!r} is not a Java format{where}', entry))
        unbalanced = html_unbalanced(html_tags(text.text))
        if unbalanced:
            findings.append(finding('warning', 'style', f'unbalanced HTML tags: {", ".join(unbalanced)}{where}', entry))
        if entry.locale is None and ICU_SELECT.search(text.text):
            try:
                icu_arguments(text.text)
            except ValueError as error:
                findings.append(finding('error', 'icu', f'{error}{where}', entry))
    if entry.kind == 'plurals':
        quantities = [unit for unit, _ in entry.units]
        unknown = sorted(set(q for q in quantities if q not in QUANTITIES), key=str)
        repeated = sorted(q for q, n in Counter(quantities).items() if n > 1 and q in QUANTITIES)
        if unknown or repeated or 'other' not in quantities:
            problems = (['no other quantity'] if 'other' not in quantities else []) + \
                [f'unknown quantity {q}' for q in unknown] + [f'repeated quantity {q}' for q in repeated]
            findings.append(finding('error', 'plural', ', '.join(problems), entry))
    if entry.locale is None:
        return
    if default is None:
        findings.append(finding('warning' if resources.overlay else 'error', 'no-default',
                                'translation without a default' + (' in the overlay' if resources.overlay else ''),
                                entry))
        return
    if not default.translatable:
        findings.append(finding('warning', 'untranslatable', 'the default is translatable="false"', entry))
        return
    if default.kind == 'string' and len(entry.units) == 1:
        raw = entry.units[0][1].raw_text
        if not entry.units[0][1].spans and REFERENCE.match(raw) and not REFERENCE.match(default.units[0][1].raw_text):
            findings.append(finding('error', 'reference', f'aapt2 reads {raw!r} as a reference', entry))
    if entry.kind == 'array' and len(entry.units) != len(default.units):
        findings.append(finding('error', 'array', f'{len(entry.units)} items, the default {len(default.units)}', entry))
    _compare(entry, default, findings)
    if _english(entry, default):
        findings.append(finding('warning', 'english', 'repeats the English default', entry))


def check_resources(resources, names, findings):
    """Check the entries with these (kind, name) keys, or all entries for names=None."""
    checked = [e for e in resources.entries if names is None or e.key() in names]
    for entry in checked:
        check_entry(entry, resources, findings)
    seen = {}
    for entry in checked:
        key = (entry.config, entry.kind, entry.name, entry.product, entry.flag)
        if key in seen:
            findings.append(finding('error', 'duplicate', f'also defined in {seen[key].path}', entry))
        else:
            seen[key] = entry
    return checked


# --- Sources ----------------------------------------------------------------

def _git(repo, *args, data=None):
    try:
        return subprocess.run(['git', '-C', str(repo), *args], input=data, capture_output=True, check=True).stdout
    except subprocess.CalledProcessError as error:
        raise CheckError(f'git {" ".join(args[:2])} failed: {error.stderr.decode(errors="replace").strip()}') from None


def _values_blobs(repo, rev, res_dirs):
    """{res_dir: {(config, file): blob}} for the values*/ XML files directly in these directories."""
    listing = _git(repo, 'ls-tree', '-r', '-z', '--full-tree', rev, '--', *[d or '.' for d in res_dirs])
    result = {d: {} for d in res_dirs}
    for record in listing.decode().split('\0'):
        if not record:
            continue
        meta, path = record.split('\t', 1)
        match = VALUES_FILE.match(path)
        if match and meta.split()[1] == 'blob' and (match.group(1) or '') in result:
            result[match.group(1) or ''][(match.group(2), match.group(3))] = meta.split()[2]
    return result


def _read_blobs(repo, blobs):
    if not blobs:
        return {}
    output = _git(repo, 'cat-file', '--batch', data=''.join(f'{b}\n' for b in blobs).encode())
    result, i = {}, 0
    while i < len(output):
        header_end = output.index(b'\n', i)
        blob, _, size = output[i:header_end].decode().split()
        result[blob] = output[header_end + 1:header_end + 1 + int(size)]
        i = header_end + 2 + int(size)
    return result


def _is_overlay(manifest):
    return manifest is not None and b'<overlay' in manifest


def check_git(repo, base, head, findings):
    """Check the entries a repository changed between base and head, in every configuration."""
    changed = _git(repo, 'diff', '--name-only', '-z', '--no-renames', base, head, '--').decode().split('\0')
    res_dirs = sorted({(m.group(1) or '') for m in map(VALUES_FILE.match, changed) if m})
    head_blobs, base_blobs = _values_blobs(repo, head, res_dirs), _values_blobs(repo, base, res_dirs)
    wanted = {b for files in head_blobs.values() for b in files.values()}
    wanted |= {b for d in res_dirs for k, b in base_blobs[d].items() if head_blobs[d].get(k) != b}
    blobs = _read_blobs(repo, wanted)
    results = []
    for res_dir in res_dirs:
        label = res_dir or '.'
        manifest = None
        if res_dir:
            parent = res_dir.rsplit('/', 1)[0] + '/' if '/' in res_dir else ''
            try:
                manifest = _git(repo, 'show', f'{head}:{parent}AndroidManifest.xml')
            except CheckError:
                pass
        head_files = {k: blobs[b] for k, b in head_blobs[res_dir].items()}
        base_files = {k: blobs.get(b) for k, b in base_blobs[res_dir].items() if head_blobs[res_dir].get(k) != b}
        base_files.update({k: v for k, v in head_files.items() if base_blobs[res_dir].get(k) == head_blobs[res_dir][k]})
        resources = ResourceDir(label, head_files, findings, _is_overlay(manifest))
        before = ResourceDir(label, base_files, [])
        old = {}
        for entry in before.entries:
            old.setdefault((entry.config, entry.key()), []).append(entry.signature())
        new = {}
        for entry in resources.entries:
            new.setdefault((entry.config, entry.key()), []).append(entry.signature())
        names = {key for (config, key) in set(old) | set(new)
                 if sorted(old.get((config, key), []), key=repr) != sorted(new.get((config, key), []), key=repr)}
        results.append((label, names, check_resources(resources, names, findings)))
    return results


def check_dirs(res_dirs, findings):
    results = []
    for res_dir in res_dirs:
        files = {(p.parent.name, p.name): p.read_bytes() for p in sorted(res_dir.glob('values*/*.xml'))}
        manifest = res_dir.parent / 'AndroidManifest.xml'
        resources = ResourceDir(str(res_dir), files, findings,
                                _is_overlay(manifest.read_bytes() if manifest.is_file() else None))
        names = {e.key() for e in resources.entries}
        results.append((str(res_dir), names, check_resources(resources, None, findings)))
    return results


def run_check(args):
    findings = []
    if args.repo:
        results = check_git(args.repo, args.base, args.head, findings)
    else:
        results = check_dirs(args.res_dir, findings)
    order = {'error': 0, 'warning': 1}
    findings.sort(key=lambda f: (order[f['severity']], f['file'] or '', f['resource'] or '', f['code']))
    errors = sum(f['severity'] == 'error' for f in findings)
    warnings = len(findings) - errors
    fail = errors or (warnings and args.strict)
    return {
        'status': 'fail' if fail else 'pass',
        'resource_dirs': [{'dir': label, 'names': len(names), 'entries': len(checked)}
                          for label, names, checked in results],
        'findings': findings,
        'summary': {'errors': errors, 'warnings': warnings, 'names': sum(len(n) for _, n, _ in results),
                    'entries': sum(len(c) for _, _, c in results)},
    }


def render_text(report):
    lines = [f'  {d["dir"]}: {d["names"]} names, {d["entries"]} entries' for d in report['resource_dirs']]
    for severity, title in (('error', 'Errors'), ('warning', 'Warnings')):
        chosen = [f for f in report['findings'] if f['severity'] == severity]
        if chosen:
            lines.append(f'{title}:')
            lines.extend(f'  [{f["code"]}] {f["file"]}: {f["resource"] or ""}: {f["message"]}' for f in chosen)
    summary = report['summary']
    lines.append(f'{report["status"].upper()}: {summary["errors"]} errors, {summary["warnings"]} warnings '
                 f'({summary["names"]} names in {summary["entries"]} entries checked)')
    return '\n'.join(lines)


def _parser():
    parser = argparse.ArgumentParser(prog='diamaneos strings check', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('res_dir', nargs='*', type=Path, help='resource directories to check in full')
    parser.add_argument('--repo', type=Path, help='a Git repository: check what changed from --base to --head')
    parser.add_argument('--base', help=f'the upstream revision ({EMPTY_TREE} for a repository of its own)')
    parser.add_argument('--head', default='HEAD', help='the revision to check (default HEAD)')
    parser.add_argument('--strict', action='store_true', help='fail on warnings too')
    parser.add_argument('--json', action='store_true', help='print the machine-readable report')
    return parser


def main(argv=None):
    parser = _parser()
    args = parser.parse_args(argv)
    if bool(args.repo) == bool(args.res_dir) or bool(args.repo) != bool(args.base):
        parser.error('give resource directories, or --repo with --base')
    try:
        report = run_check(args)
    except (CheckError, OSError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2) if args.json else render_text(report))
    return 1 if report['status'] == 'fail' else 0


if __name__ == '__main__':
    sys.exit(main())

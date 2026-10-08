"""Check a product font customization the way Android reads it.

Android reads /product/etc/fonts_customization.xml, with /product/fonts/ as its
font directory, together with the system font list in Zygote and system_server
(FontCustomizationParser, FontListParser and SystemFonts at the pinned
GrapheneOS release). A mistake there is not local to the added fonts: a
malformed file makes Android drop every system font, and several mistakes throw
while the system fonts load, which stops boot. The check reads the file and the
fonts with Android's rules and the fonts' own tables. Nothing is built,
installed or run.

Input, one of:
  --module-dir DIR    a build module directory: its Android.bp installs the file
                      (a product prebuilt_etc named fonts_customization.xml) and
                      the fonts (product prebuilt_font modules); a source.json
                      there, if present, records each file's SHA-256
  --xml FILE --font-dir DIR
                      an installed file and its font directory, for example
                      product/etc/fonts_customization.xml and product/fonts of
                      a built image

Errors (exit 1):
  xml            not well-formed XML, a DTD, or a root other than
                 <fonts-modification>: Android drops every system font when its
                 parser rejects the file (it accepts a DTD and repeated
                 attributes, which the check refuses)
  boot           Android throws while it loads the system fonts: a missing or
                 unknown customizationType or operation, an attribute a named
                 family may not have, a missing or repeated family name, a
                 weight or index that is not an integer, a weight outside 1 to
                 1000, an axis with a bad tag or value, two fonts of one style
                 in a family, a font file that is not a font
  dropped        Android skips a font (no such file in the font directory), a
                 family left without fonts, or an alias without a name or
                 target
  path           a font name with a '..' part, which leaves the font directory
  weight         a variable font's default wght instance is not its declared
                 weight, so a request for exactly that weight draws the default
                 instance; or a weight given with --weights is outside a font's
                 wght axis, so it is clamped
  slant          the font's italic flag differs from its declared style
  font-file      the check cannot read a font's name, OS/2, head or fvar table
  not-installed  (module mode) a font the file names is not installed to
                 /product/fonts by a product prebuilt_font in the directory, or
                 the file's module does not require it
  source         (module mode) a file differs from the size or SHA-256 that
                 source.json records
  module         (module mode) Android.bp cannot be read, or no single product
                 prebuilt_etc installs fonts_customization.xml to /product/etc
Warnings (exit 1 only with --strict):
  postscript-name  the declared PostScript name (or the file name without its
                   extension) is not the font's own
  static-family    supportedAxes is declared but Android treats the family as
                   static (SystemFonts.resolveVarFamilyType)
  no-axis          supportedAxes or an <axis> names an axis the font lacks
  font-order       in a two-font variable family the upright font is not first
  unused-font      (module mode) a product font in the directory is not named
  unrecorded       (module mode) source.json does not list an installed file
  skipped          an element Android ignores
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

from diamaneos_tools.overlays import OverlayCheckError, UNKNOWN, parse_blueprint, parse_xml

INSTALL_NAME = 'fonts_customization.xml'
FONT_DIR = '/product/fonts/'
MAX_FONT_BYTES = 64 * 1024 * 1024
MAX_BLUEPRINT_BYTES = 1024 * 1024
MAX_SOURCE_BYTES = 1024 * 1024
# FontStyle.FONT_WEIGHT_MIN and FONT_WEIGHT_MAX.
WEIGHT_MIN, WEIGHT_MAX = 1, 1000
# Integer.parseInt: an optional sign and decimal digits (Character.digit accepts
# any Unicode decimal digit), nothing else.
JAVA_INT = re.compile(r'[+-]?\d+')
# Float.parseFloat after String.trim(): Java's floating-point literal grammar.
JAVA_FLOAT = re.compile(
    r'[+-]?(?:NaN|Infinity|(?:(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?'
    r'|0[xX](?:[0-9a-fA-F]+\.?[0-9a-fA-F]*|\.[0-9a-fA-F]+)[pP][+-]?[0-9]+)[fFdD]?)')
# FontVariationAxis: exactly four characters in U+0020..U+007E.
AXIS_TAG = re.compile(r'[\x20-\x7e]{4}')
# FontListParser's FILENAME_WHITESPACE_PATTERN strips only these.
XML_SPACE = ' \n\r\t'
SFNT_VERSIONS = {b'\x00\x01\x00\x00', b'true', b'OTTO'}
REQUIRED_TABLES = ('cmap', 'head', 'hhea', 'hmtx', 'maxp', 'name')
OUTLINES = (('glyf', 'loca'), ('CFF ',), ('CFF2',), ('CBDT', 'CBLC'), ('sbix',))
VAR_NAMES = {0: 'NONE', 1: 'SINGLE_FONT_WGHT_ONLY', 2: 'SINGLE_FONT_WGHT_ITAL', 3: 'TWO_FONTS_WGHT'}


def finding(severity, code, message, family=None, font=None):
    return {'severity': severity, 'code': code, 'message': message, 'family': family, 'font': font}


def _local(name):
    return name.rsplit('}', 1)[-1]


def attr(element, name, any_namespace=True):
    """KXmlParser.getAttributeValue: the last attribute with this local name; namespace null
    (any_namespace) matches any namespace, "" only attributes without one."""
    for key in reversed(list(element.attrib)):
        if _local(key) == name and (any_namespace or not key.startswith('{')):
            return element.attrib[key]
    return None


def java_int(text):
    """Integer.parseInt: Character.digit reads one UTF-16 unit at a time, so a
    digit outside the Basic Multilingual Plane (a surrogate pair) is no digit."""
    if text is None or not JAVA_INT.fullmatch(text) or any(ord(c) > 0xFFFF for c in text):
        return None
    value = int(text)
    return value if -2**31 <= value < 2**31 else None


def java_float(text):
    if text is None:
        return None
    text = text.strip(''.join(chr(c) for c in range(0x21)))
    if not JAVA_FLOAT.fullmatch(text):
        return None
    body = text.rstrip('fFdD') if not text.endswith(('NaN', 'Infinity')) else text
    sign = -1.0 if body.startswith('-') else 1.0
    body = body.lstrip('+-')
    if body == 'NaN':
        return float('nan')
    if body == 'Infinity':
        return sign * float('inf')
    return sign * (float.fromhex(body) if body[:2] in ('0x', '0X') else float(body))


# --- Font files -------------------------------------------------------------

class FontFileError(Exception):
    """The file is not a font that FreeType can open."""


class FontTableError(FontFileError):
    """The font opens, but the check cannot read one of the tables it compares with the XML."""


def _u16(data, offset):
    if offset < 0 or offset + 2 > len(data):
        raise FontFileError('truncated')
    return int.from_bytes(data[offset:offset + 2], 'big')


def _u32(data, offset):
    if offset < 0 or offset + 4 > len(data):
        raise FontFileError('truncated')
    return int.from_bytes(data[offset:offset + 4], 'big')


def _fixed(data, offset):
    return int.from_bytes(data[offset:offset + 4], 'big', signed=True) / 65536 if offset + 4 <= len(data) else None


def _postscript_name(table):
    count, strings = _u16(table, 2), _u16(table, 4)
    best = None
    for i in range(count):
        record = 6 + 12 * i
        platform, encoding, language, name_id, length, offset = (
            _u16(table, record + 2 * k) for k in range(6))
        if name_id != 6:
            continue
        start = strings + offset
        if start + length > len(table):
            raise FontFileError('name record outside the name table')
        raw = table[start:start + length]
        if platform == 3 and encoding in (0, 1, 10):
            rank, text = (0 if language == 0x409 else 1), raw.decode('utf-16-be', 'replace')
        elif platform == 1 and encoding == 0:
            rank, text = 2, raw.decode('latin-1')
        elif platform == 0:
            rank, text = 3, raw.decode('utf-16-be', 'replace')
        else:
            continue
        if best is None or rank < best[0]:
            best = (rank, text)
    return best[1] if best else None


def _axes(table):
    offset, count, size = _u16(table, 4), _u16(table, 8), _u16(table, 10)
    if size < 20:
        raise FontFileError('fvar axis records are too short')
    axes = {}
    for i in range(count):
        record = offset + size * i
        if record + 20 > len(table):
            raise FontFileError('fvar axis outside the fvar table')
        tag = table[record:record + 4].decode('latin-1')
        axes[tag] = (_fixed(table, record + 4), _fixed(table, record + 8), _fixed(table, record + 12))
    return axes


def read_font(data, index):
    """Structure, PostScript name, italic flag and variation axes of one font in a file."""
    if len(data) < 12:
        raise FontFileError('too short to be a font')
    base, fonts = 0, 1
    if data[:4] == b'ttcf':
        fonts = _u32(data, 8)
        if not 0 <= index < fonts:
            raise FontFileError(f'collection index {index} is not in 0 to {fonts - 1}')
        base = _u32(data, 12 + 4 * index)
    elif index != 0:
        raise FontFileError(f'index {index} on a file that holds one font')
    if data[base:base + 4] not in SFNT_VERSIONS:
        raise FontFileError('not a TrueType or OpenType font')
    tables = {}
    for i in range(_u16(data, base + 4)):
        record = base + 12 + 16 * i
        if record + 16 > len(data):
            raise FontFileError('table directory past the end of the file')
        tag = data[record:record + 4].decode('latin-1')
        offset, length = _u32(data, record + 8), _u32(data, record + 12)
        if offset + length > len(data):
            raise FontFileError(f'table {tag.strip()} past the end of the file')
        tables[tag] = data[offset:offset + length]
    missing = [tag for tag in REQUIRED_TABLES if tag not in tables]
    if not any(all(tag in tables for tag in group) for group in OUTLINES):
        missing.append('outlines')
    if missing:
        raise FontFileError('missing ' + ', '.join(missing))
    table = 'name'
    try:
        postscript = _postscript_name(tables['name'])
        table = 'OS/2' if 'OS/2' in tables else 'head'
        italic = bool(_u16(tables['OS/2'], 62) & 1) if 'OS/2' in tables else bool(_u16(tables['head'], 44) & 2)
        table = 'fvar'
        axes = _axes(tables['fvar']) if 'fvar' in tables else {}
    except FontFileError as error:
        raise FontTableError(f'{table} table: {error}') from None
    return {'postscript_name': postscript, 'italic': italic, 'axes': axes, 'fonts_in_file': fonts}


# --- Android's reading of the file ------------------------------------------

class _Thrown(Exception):
    pass


class Reader:
    """FontCustomizationParser.parse and the SystemFonts steps that build its families."""

    def __init__(self, resolve):
        self.resolve = resolve  # file name -> (path, None, None) or (None, code, reason)
        self.findings = []
        self.families = []
        self.named = set()
        self.aliases = 0
        self.cache = {}

    def add(self, severity, code, message, family=None, font=None):
        self.findings.append(finding(severity, code, message, family, font))

    def fail(self, message, family=None, font=None):
        """Android throws here, so it reads nothing more; the check goes on with the next element."""
        self.add('error', 'boot', message, family, font)
        raise _Thrown

    def run(self, data):
        try:
            root = parse_xml(data)
        except OverlayCheckError as error:
            # Android's KXmlParser rejects most of what expat rejects (and then drops every system
            # font), but accepts a DTD and repeated attributes (reading the last); the check does not.
            self.add('error', 'xml', f'{error}: Android drops every system font when its parser rejects the file')
            return
        if _local(root.tag) != 'fonts-modification':
            self.add('error', 'xml', f'the root element is <{_local(root.tag)}>, not <fonts-modification>: '
                     'Android would drop every system font')
            return
        for element in root:
            tag = _local(element.tag)
            try:
                if tag == 'family':
                    self.family(element)
                elif tag == 'family-list':
                    self.family_list(element)
                elif tag == 'alias':
                    self.alias(element)
                else:
                    self.add('warning', 'skipped', f'<{tag}> is not read by Android')
            except _Thrown:
                pass

    def family(self, element):
        kind = attr(element, 'customizationType')
        name = attr(element, 'name')
        label = name or '(unnamed)'
        if kind is None:
            self.fail('a <family> without customizationType (IllegalArgumentException)', label)
        if kind == 'new-named-family':
            bad = [a for a in ('lang', 'variant', 'ignore') if attr(element, a) is not None]
            if bad:
                self.fail(f'named family {label} has {", ".join(bad)} (IllegalArgumentException)', label)
            fonts = self.fonts(element, label)
            if not fonts:
                self.add('error', 'dropped', f'named family {label} has no usable font, so Android drops it', label)
                return
            self.register(name, 'named', [fonts], label)
        elif kind == 'new-locale-family':
            lang, operation = attr(element, 'lang'), attr(element, 'operation')
            if operation is None or lang is None:
                missing = 'operation' if operation is None else 'lang'
                self.fail(f'a new-locale-family without {missing} (NullPointerException)', label)
            if operation not in ('append', 'prepend', 'replace'):
                self.fail(f'new-locale-family operation {operation!r} (IllegalArgumentException)', label)
            fonts = self.fonts(element, label, allow_ignore=True)
            entry = {'name': f'{lang} ({operation})', 'kind': 'locale', 'operation': operation,
                     'members': [fonts] if fonts else []}
            self.families.append(entry)
            if not fonts:
                self.add('error', 'boot', f'the {lang} {operation} family has no usable font: SystemFonts passes '
                         'a null family on (NullPointerException when a system family of that script is set up)', label)
                return
            self.build(fonts, entry['name'])
        else:
            self.fail(f'unknown customizationType {kind!r} (IllegalArgumentException)', label)

    def family_list(self, element):
        kind = attr(element, 'customizationType')
        name = attr(element, 'name')
        label = name or '(unnamed)'
        if kind != 'new-named-family':
            what = 'without customizationType' if kind is None else f'with customizationType {kind!r}'
            self.fail(f'a <family-list> {what} (IllegalArgumentException)', label)
        members = []
        for child in element:
            if _local(child.tag) != 'family':
                self.add('warning', 'skipped', f'<{_local(child.tag)}> in family-list {label} is not read', label)
                continue
            bad = [a for a in ('name', 'lang', 'variant', 'ignore') if attr(child, a) is not None]
            if bad:
                self.fail(f'a family in family-list {label} has {", ".join(bad)} '
                          '(IllegalArgumentException)', label)
            fonts = self.fonts(child, label)
            if fonts:
                members.append(fonts)
        if not members:
            self.add('error', 'dropped', f'family-list {label} has no usable family, so Android drops it', label)
            return
        self.register(name, 'family-list', members, label)

    def register(self, name, kind, members, label):
        if name is None:
            self.fail('a new-named-family without a name (IllegalArgumentException)', label)
        if name in self.named:
            self.fail(f'two families named {name} (IllegalArgumentException)', label)
        self.named.add(name)
        self.families.append({'name': name, 'kind': kind, 'members': members})
        for fonts in members:
            self.build(fonts, label)

    def alias(self, element):
        name, target, weight = attr(element, 'name'), attr(element, 'to'), attr(element, 'weight')
        if weight is not None and java_int(weight) is None:
            self.fail(f'alias {name} weight {weight!r} is not an integer (NumberFormatException)')
        self.aliases += 1
        if name is None or target is None:
            self.add('error', 'dropped', 'an alias without a name or a target, which Android drops')
        # An alias to a family that neither this file nor the system font list defines is also
        # dropped; the system list is not an input here.

    def fonts(self, element, label, allow_ignore=False):
        """FontListParser.readFamily: the fonts Android keeps, or None when the family is ignored."""
        fonts = []
        for child in element:
            tag = _local(child.tag)
            if tag != 'font':
                self.add('warning', 'skipped', f'<{tag}> in family {label} is not read', label)
                continue
            font = self.font(child, label)
            if font:
                fonts.append(font)
        if allow_ignore and attr(element, 'ignore') in ('true', '1'):
            return None
        return fonts

    def font(self, element, label):
        """FontListParser.readFont, then the file checks of SystemFonts.createFontFamily."""
        text = (element.text or '') + ''.join(child.tail or '' for child in element)
        name = text.strip(XML_SPACE)
        index_text, weight_text = attr(element, 'index'), attr(element, 'weight')
        index = 0 if index_text is None else java_int(index_text)
        weight = 400 if weight_text is None else java_int(weight_text)
        if index is None or weight is None:
            bad = 'index' if index is None else 'weight'
            value = index_text if index is None else weight_text
            self.fail(f'{name}: {bad} {value!r} is not an integer (NumberFormatException)', label, name)
        axes = []
        for child in element:
            if _local(child.tag) != 'axis':
                self.add('warning', 'skipped', f'<{_local(child.tag)}> in font {name} is not read', label, name)
                continue
            tag, value_text = attr(child, 'tag'), attr(child, 'stylevalue')
            value = java_float(value_text)
            if value is None:
                self.fail(f'{name}: axis {tag} stylevalue {value_text!r} is not a number '
                          f'({"NullPointerException" if value_text is None else "NumberFormatException"})', label, name)
            # readAxis builds the FontVariationAxis while it reads, whether or not the file exists.
            if tag is None or not AXIS_TAG.fullmatch(tag):
                self.fail(f'{name}: axis tag {tag!r} is not four printable ASCII characters '
                          '(IllegalArgumentException)', label, name)
            axes.append((tag, value))
        supported = set()
        for part in (attr(element, 'supportedAxes') or '').split(','):
            if part.strip() in ('wght', 'ital'):
                supported.add(part.strip())
        postscript = attr(element, 'postScriptName')
        if postscript is None:
            if len(name) < 4:
                self.fail(f'font file name {name!r} is too short to derive a PostScript name '
                          '(StringIndexOutOfBoundsException)', label, name)
            postscript = name[:-4]
        path, code, reason = self.resolve(name)
        if path is None:
            self.add('error', code, f'{name or "(empty name)"}: {reason}', label, name)
            return None
        # readFont builds the FontStyle only for a file that exists.
        if not WEIGHT_MIN <= weight <= WEIGHT_MAX:
            self.fail(f'{name}: weight {weight} is outside {WEIGHT_MIN} to {WEIGHT_MAX} '
                      '(IllegalArgumentException)', label, name)
        italic = attr(element, 'style') == 'italic'
        return {'file': name, 'path': path, 'weight': weight, 'italic': italic, 'index': index, 'axes': axes,
                'supported': supported, 'postscript_name': postscript,
                'fallback_for': attr(element, 'fallbackFor'), 'info': None}

    def build(self, fonts, label):
        """SystemFonts.createFontFamily and resolveVarFamilyType for one family."""
        styles = {}
        for font in fonts:
            info = self.file(font, label)
            if info is None:
                return
            key = (font['weight'], font['italic'])
            if key in styles:
                self.add('error', 'boot', f'{styles[key]} and {font["file"]} are both weight {key[0]} '
                         f'{"italic" if key[1] else "upright"} (FontFamily.Builder.addFont throws '
                         'IllegalArgumentException)', label, font['file'])
                return
            styles[key] = font['file']
        kind = resolve_var_family_type(fonts)
        for font in fonts:
            font['family_type'] = VAR_NAMES[kind]
        if kind == 0 and any(f['supported'] for f in fonts):
            self.add('warning', 'static-family', f'family {label} declares supportedAxes but Android treats it as '
                     'static (resolveVarFamilyType NONE): each font is drawn at its declared weight', label)
        if kind == 3 and fonts[0]['italic']:
            self.add('warning', 'font-order', f'family {label} lists its italic font first: Minikin takes the '
                     'first font of a two-font variable family for upright text', label)

    def file(self, font, label):
        path = font['path']
        if path not in self.cache:
            try:
                self.cache[path] = (read_bounded(path, MAX_FONT_BYTES), {})
            except OSError as error:
                self.cache[path] = error
        cached = self.cache[path]
        if isinstance(cached, OSError):
            self.add('error', 'dropped', f'{font["file"]}: cannot be read ({cached.strerror or cached}), so '
                     'Android skips it', label, font['file'])
            return None
        data, per_index = cached
        if font['index'] not in per_index:
            try:
                per_index[font['index']] = read_font(data, font['index'])
            except FontFileError as error:
                per_index[font['index']] = error
        info = per_index[font['index']]
        if isinstance(info, FontTableError):
            self.add('error', 'font-file', f'{font["file"]}: the check cannot read its {info}, so it cannot compare '
                     'the font with the XML', label, font['file'])
            return None
        if isinstance(info, FontFileError):
            self.add('error', 'boot', f'{font["file"]}: {info}; Skia cannot open it and Font.Builder throws '
                     'IllegalArgumentException', label, font['file'])
            return None
        font['info'] = info
        return info


def resolve_var_family_type(fonts):
    """SystemFonts.resolveVarFamilyType for a named family (fonts without fallbackFor count)."""
    wght = ital = targets = 0
    has_italic = False
    for font in fonts:
        if font['fallback_for'] is not None:
            continue
        if not font['supported']:
            return 0
        wght += 'wght' in font['supported']
        ital += 'ital' in font['supported']
        has_italic |= font['italic']
        targets += 1
    if ital == 0:
        if targets == 1 and wght == 1:
            return 1
        if targets == 2 and wght == 2 and has_italic:
            return 3
    elif ital == 1 and wght == 1 and targets == 1:
        return 2
    return 0


def font_checks(reader, weights):
    """What the font files say against what the XML says."""
    for family in reader.families:
        for fonts in family['members']:
            for font in fonts:
                info = font['info']
                if info is None:
                    continue
                name, label = font['file'], family['name']
                if info['postscript_name'] != font['postscript_name']:
                    reader.add('warning', 'postscript-name', f'{name}: the XML gives PostScript name '
                               f'{font["postscript_name"]}, the font has {info["postscript_name"]}', label, name)
                if info['italic'] != font['italic']:
                    reader.add('error', 'slant', f'{name}: declared {"italic" if font["italic"] else "upright"}, '
                               f'but the font is {"italic" if info["italic"] else "upright"}', label, name)
                axes = info['axes']
                for tag in sorted(font['supported']) + [tag for tag, _ in font['axes']]:
                    if tag not in axes:
                        reader.add('warning', 'no-axis', f'{name}: {tag} is named but the font has no {tag} axis',
                                   label, name)
                wght = axes.get('wght')
                if wght and font['supported'] and 'wght' in font['supported']:
                    low, default, high = wght
                    if abs(default - font['weight']) > 0.5:
                        reader.add('error', 'weight', f'{name}: declared weight {font["weight"]}, default wght '
                                   f'instance {default:g}: a request for exactly {font["weight"]} draws '
                                   f'{default:g} (Minikin sets wght only for other weights)', label, name)
                    outside = [w for w in weights if not low <= w <= high]
                    if outside:
                        reader.add('error', 'weight', f'{name}: weights {", ".join(map(str, outside))} are outside '
                                   f'its wght axis {low:g} to {high:g} and would be clamped', label, name)


def read_bounded(path, limit):
    with open(path, 'rb') as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise OSError(f'{path.name} exceeds the size limit')
    return data


# --- Inputs -----------------------------------------------------------------

def relative_file(value):
    """A path inside a directory: not empty, not absolute, no '..' part."""
    return (isinstance(value, str) and value != '' and not value.startswith('/') and '\0' not in value
            and '..' not in value.split('/'))


def directory_resolver(font_dir):
    def resolve(name):
        # Android reads fontDir + name, so a leading '/' stays inside the directory.
        if '..' in name.split('/') or '\0' in name:
            return None, 'path', f'the name leaves {FONT_DIR}; the check reads only files inside it'
        path = Path(f'{font_dir}/{name}')
        if name and path.is_file():
            return path, None, None
        return None, 'dropped', 'no such file in the font directory, so Android skips the font'
    return resolve


def _string(props, key):
    value = props.get(key) if isinstance(props, dict) else None
    return value if isinstance(value, str) else None


def read_module(directory):
    """The XML and font files one Android.bp installs to /product: (xml path, fonts, required, findings)."""
    findings = []
    blueprint = directory / 'Android.bp'
    try:
        modules = parse_blueprint(read_bounded(blueprint, MAX_BLUEPRINT_BYTES).decode('utf-8'))
    except (OSError, UnicodeDecodeError, OverlayCheckError) as error:
        raise OverlayCheckError(f'cannot read {directory.name}/Android.bp: {error}') from None
    etc, fonts = [], {}
    for module in modules:
        props = module['props']
        if props is UNKNOWN:
            continue
        product = props.get('product_specific') is True
        name, src = _string(props, 'name'), _string(props, 'src')
        if module['type'] == 'prebuilt_etc':
            installed = _string(props, 'filename') or (Path(src).name if src else name)
            if installed == INSTALL_NAME:
                etc.append((module, product))
        elif module['type'] == 'prebuilt_font' and name and src:
            installed = _string(props, 'filename') or (Path(src).name if props.get('filename_from_src') else name)
            fonts[installed] = {'module': name, 'src': src, 'product': product, 'line': module['line']}
    if len(etc) != 1:
        what = 'no prebuilt_etc installs' if not etc else f'{len(etc)} prebuilt_etc modules install'
        findings.append(finding('error', 'module', f'{what} {INSTALL_NAME} in {directory.name}/Android.bp'))
        return None, fonts, set(), findings
    module, product = etc[0]
    props = module['props']
    if not product:
        findings.append(finding('error', 'module', f'{_string(props, "name")} is not product_specific, so '
                                f'{INSTALL_NAME} is not installed to /product/etc'))
    for key in ('sub_dir', 'relative_install_path'):
        if props.get(key) not in (None, ''):
            findings.append(finding('error', 'module', f'{_string(props, "name")} sets {key}, so '
                                    f'{INSTALL_NAME} is not installed to /product/etc'))
    src = _string(props, 'src')
    if not relative_file(src):
        raise OverlayCheckError(f'{_string(props, "name")} has no src file inside {directory.name}')
    required = props.get('required') if isinstance(props.get('required'), list) else []
    return directory / src, fonts, {r for r in required if isinstance(r, str)}, findings


def module_resolver(directory, fonts, required, findings, used):
    def resolve(name):
        font = fonts.get(name)
        if font is None:
            return None, 'not-installed', (f'no prebuilt_font in {directory.name}/Android.bp installs it to '
                                           f'{FONT_DIR}, so Android skips the font')
        used.add(name)
        if not font['product']:
            return None, 'not-installed', (f'{font["module"]} is not product_specific, so it is installed to '
                                           f'/system/fonts and Android skips the font')
        if font['module'] not in required:
            findings.append(finding('error', 'not-installed', f'{name}: the {INSTALL_NAME} module does not require '
                                    f'{font["module"]}, so a product can install the file without it', font=name))
        path = directory / font['src'] if relative_file(font['src']) else None
        if path is None or not path.is_file():
            return None, 'not-installed', f'{font["module"]} src {font["src"]!r} is not a file in {directory.name}'
        return path, None, None
    return resolve


def source_checks(directory, installed):
    """source.json, when present: every installed font listed, each with its recorded size and SHA-256."""
    path = directory / 'source.json'
    if not path.is_file():
        return []
    findings = []
    try:
        record = json.loads(read_bounded(path, MAX_SOURCE_BYTES))
        files = {f['path']: f for f in record['files']}
    except (OSError, ValueError, KeyError, TypeError) as error:
        return [finding('error', 'source', f'{directory.name}/source.json cannot be read ({error})')]
    for name in sorted(installed):
        entry = files.get(name)
        if entry is None:
            findings.append(finding('warning', 'unrecorded', f'{name} is installed but source.json does not list it',
                                    font=name))
            continue
        try:
            data = read_bounded(directory / name, MAX_FONT_BYTES)
        except OSError as error:
            findings.append(finding('error', 'source', f'{name}: cannot be read ({error})', font=name))
            continue
        if 'bytes' in entry and entry['bytes'] != len(data):
            findings.append(finding('error', 'source', f'{name}: {len(data)} bytes, source.json records '
                                    f'{entry["bytes"]}', font=name))
        if entry.get('sha256') != hashlib.sha256(data).hexdigest():
            findings.append(finding('error', 'source', f'{name}: SHA-256 differs from source.json', font=name))
    return findings


# --- Report -----------------------------------------------------------------

def run_check(args):
    weights = parse_weights(args.weights)
    extra, used = [], set()
    if args.module_dir:
        directory = args.module_dir
        xml, fonts, required, extra = read_module(directory)
        source = {'kind': 'module', 'path': directory.name}
        if xml is None:
            return build_report(source, [], extra, args.strict)
        resolve = module_resolver(directory, fonts, required, extra, used)
    else:
        xml = args.xml
        source = {'kind': 'files', 'xml': xml.name, 'font_dir': args.font_dir.name}
        resolve = directory_resolver(args.font_dir)
    try:
        data = read_bounded(xml, 16 * 1024 * 1024)
    except OSError as error:
        raise OverlayCheckError(f'cannot read {xml.name}: {error}') from None
    reader = Reader(resolve)
    reader.run(data)
    font_checks(reader, weights)
    if args.module_dir:
        for name, font in sorted(fonts.items()):
            if font['product'] and name not in used:
                extra.append(finding('warning', 'unused-font', f'{name} is installed to {FONT_DIR} but '
                                     f'{INSTALL_NAME} does not name it', font=name))
        installed = {font['src'] for font in fonts.values() if font['product'] and relative_file(font['src'])}
        extra.extend(source_checks(args.module_dir, installed))
    return build_report(source, reader.families, extra + reader.findings, args.strict)


def build_report(source, families, findings, strict):
    unique, seen = [], set()
    for item in findings:
        key = (item['severity'], item['code'], item['message'])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    unique.sort(key=lambda f: (f['severity'] != 'error', f['code'], f['message']))
    errors = sum(f['severity'] == 'error' for f in unique)
    warnings = len(unique) - errors
    listed = []
    for family in families:
        members = []
        for fonts in family['members']:
            members.append([{
                'file': f['file'], 'weight': f['weight'], 'style': 'italic' if f['italic'] else 'normal',
                'index': f['index'], 'postscript_name': f['postscript_name'],
                'supported_axes': sorted(f['supported']), 'axes': [list(a) for a in f['axes']],
                'family_type': f.get('family_type'),
                'wght': list(f['info']['axes']['wght']) if f['info'] and 'wght' in f['info']['axes'] else None,
            } for f in fonts])
        listed.append({'name': family['name'], 'kind': family['kind'], 'members': members})
    return {
        'schema_version': 1,
        'check': 'fonts',
        'status': 'fail' if errors or (strict and warnings) else 'pass',
        'strict': strict,
        'source': source,
        'families': listed,
        'summary': {'errors': errors, 'warnings': warnings, 'families': len(listed),
                    'fonts': sum(len(m) for f in listed for m in f['members'])},
        'findings': unique,
    }


def render_text(report):
    source = report['source']
    if source['kind'] == 'module':
        lines = [f'Font customization check of the module directory {source["path"]}']
    else:
        lines = [f'Font customization check of {source["xml"]} with the font directory {source["font_dir"]}']
    for family in report['families']:
        for fonts in family['members']:
            kind = (fonts[0]['family_type'] if fonts else None) or 'not built'
            lines.append(f'  {family["name"]} ({family["kind"]}, {len(fonts)} fonts, variable type {kind})')
            for f in fonts:
                low, default, high = f['wght'] or (None, None, None)
                wght = f'wght {low:g} to {high:g}, default {default:g}' if f['wght'] else 'no wght axis'
                lines.append(f'    {f["file"]}: weight {f["weight"]} {f["style"]}, {wght}')
    for severity, title in (('error', 'Errors'), ('warning', 'Warnings')):
        chosen = [f for f in report['findings'] if f['severity'] == severity]
        if chosen:
            lines.append(f'{title}:')
            lines.extend(f'  [{f["code"]}] {f["message"]}' for f in chosen)
    summary = report['summary']
    lines.append(f'{report["status"].upper()}: {summary["errors"]} errors, {summary["warnings"]} warnings '
                 f'({summary["families"]} families, {summary["fonts"]} fonts checked)')
    return '\n'.join(lines)


def parse_weights(text):
    if not text:
        return []
    weights = []
    for part in text.split(','):
        value = java_int(part.strip())
        if value is None or not WEIGHT_MIN <= value <= WEIGHT_MAX:
            raise OverlayCheckError(f'--weights: {part.strip()!r} is not a weight from {WEIGHT_MIN} to {WEIGHT_MAX}')
        weights.append(value)
    return sorted(set(weights))


def _parser():
    parser = argparse.ArgumentParser(prog='diamaneos fonts check', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--module-dir', type=Path, help='directory of the Android.bp that installs the file')
    parser.add_argument('--xml', type=Path, help='an installed fonts_customization.xml')
    parser.add_argument('--font-dir', type=Path, help='the font directory the file is read with')
    parser.add_argument('--weights', help='comma-separated weights the product asks for; each must be '
                        'inside every variable font\'s wght axis')
    parser.add_argument('--strict', action='store_true', help='fail on warnings too')
    parser.add_argument('--json', action='store_true', help='print the machine-readable report')
    return parser


def main(argv=None):
    parser = _parser()
    args = parser.parse_args(argv)
    if bool(args.module_dir) == bool(args.xml or args.font_dir) or (args.xml is None) != (args.font_dir is None):
        parser.error('give either --module-dir, or --xml with --font-dir')
    try:
        report = run_check(args)
    except OverlayCheckError as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 2
    except OSError as error:
        print(f'ERROR: the check could not run: {error}', file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2) if args.json else render_text(report))
    return 1 if report['status'] == 'fail' else 0


if __name__ == '__main__':
    sys.exit(main())

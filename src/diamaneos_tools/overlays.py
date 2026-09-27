"""Check DiamaneOS resource overlays against their targets at a GrapheneOS release.

The overlays are the runtime_resource_overlay modules under the configured
overlay roots: DiamaneOS-wide overlays (product partition) and device overlays
(vendor or odm partition). The targets' resource sources come either from a
checked-out source tree or from each project's manifest remote, fetched at the
commit the GrapheneOS release manifest pins and cached in a caller-selected
directory. Nothing is built, installed, signed or pushed.

Errors (exit 1):
  missing              an overlaid resource (type/name) is not in the target
  shadowed             the target defines the resource for an API-level
                       qualifier that always applies and the overlay does not
  not-overlayable      the target declares <overlayable> and the resource is
                       in no overlayable group
  target-name          android:targetName does not match the overlayable group
  needs-key            only a signature, actor or config-signature policy
                       allows the resource, so the overlay would need that key
  policy               no overlayable policy allows the overlay's partition
  not-static, has-code, partition, certificate, platform-key
                       packaging rules for preinstalled overlays
  same-priority        two overlays on one target share android:priority
  overlap              two overlays on one target define the same resource
  unknown-target       the target package is not in the target registry
  target-source        a registered target source is not available
  module, manifest, resources-map, android-mk
                       an overlay that this check cannot read or model
Warnings (exit 1 only with --strict):
  qualifier-not-in-target  the overlay adds a qualifier the target lacks
  target-qualifier         the target has qualifiers the overlay does not
                           cover, where the target value still wins
  flagged-in-target        every target definition is behind a feature flag
  conditional, static-libs the check covers the overlay only partly
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / 'config/overlays.json'
ENVIRONMENT = ROOT / 'config/build-environment.json'
ANDROID = '{http://schemas.android.com/apk/res/android}'
ROLES = {'product': {'product'}, 'device': {'vendor', 'odm'}}
# PackagePartitions order; OverlayConfig applies static overlays in this order,
# then by android:priority, then by APK path, so later entries win.
PARTITION_ORDER = ['system', 'vendor', 'odm', 'oem', 'product', 'system_ext']
PARTITION_POLICY = {'system': 'system', 'system_ext': 'system', 'vendor': 'vendor',
                    'odm': 'odm', 'oem': 'oem', 'product': 'product'}
POLICIES = {'public', 'system', 'vendor', 'product', 'signature', 'odm', 'oem',
            'actor', 'config_signature'}
KEY_POLICIES = {'signature', 'actor', 'config_signature'}
FILE_TYPES = {'anim', 'animator', 'color', 'drawable', 'font', 'interpolator', 'layout',
              'menu', 'mipmap', 'navigation', 'raw', 'transition', 'xml'}
ARRAY_TAGS = {'array', 'string-array', 'integer-array'}
IGNORED_TAGS = {'public', 'public-group', 'staging-public-group', 'staging-public-group-final',
                'java-symbol', 'symbol', 'eat-comment', 'skip', 'feature-flag'}
MAX_XML_BYTES = 16 * 1024 * 1024
MAX_BLUEPRINT_BYTES = 1024 * 1024
FETCH_BATCH = 500
GIT_TIMEOUT = 900
SAFE_PATH = re.compile(r'^[A-Za-z0-9._+@-]+(/[A-Za-z0-9._+@-]+)*$')
SAFE_PATTERN = re.compile(r'^[A-Za-z0-9._+@*-]+(/[A-Za-z0-9._+@*-]+)*$')
PACKAGE = re.compile(r'^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)*$')
TAG = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')
SHA1 = re.compile(r'^[0-9a-f]{40}$')
API_QUALIFIER = re.compile(r'^v([0-9]+)$')


class OverlayCheckError(Exception):
    """Invalid input or an unavailable source: the check could not run (exit 2)."""


def finding(severity, code, message, overlay=None, target=None, resource=None, qualifiers=None):
    result = {'severity': severity, 'code': code, 'message': message}
    if overlay:
        result['overlay'] = overlay
    if target:
        result['target'] = target
    if resource:
        result['resource'] = resource
    if qualifiers:
        result['qualifiers'] = [q or 'default' for q in sorted(qualifiers, key=qualifier_key)]
    return result


def qualifier_key(value):
    return (value != '', value.lower())


def show(qualifiers, limit=8):
    """Qualifiers for a message: 'default' for none, then the directory suffixes."""
    names = [q or 'default' for q in sorted(qualifiers, key=qualifier_key)]
    text = ', '.join(names[:limit])
    return text + (f' and {len(names) - limit} more' if len(names) > limit else '')


# --- Blueprint (Android.bp) ------------------------------------------------

UNKNOWN = object()
_TOKEN = re.compile(r'''(?P<space>\s+)|(?P<comment>//[^\n]*|/\*.*?\*/)|
    (?P<string>"(?:[^"\\\n]|\\.)*")|(?P<raw>`[^`]*`)|(?P<int>-?[0-9]+)|
    (?P<ident>[A-Za-z_][A-Za-z0-9_.]*)|(?P<punct>\+=|[{}\[\]:,=+()])''', re.S | re.X)
_ESCAPES = {'n': '\n', 't': '\t', 'r': '\r', '"': '"', '\\': '\\', "'": "'"}


def _tokens(text):
    position, line, result = 0, 1, []
    while position < len(text):
        match = _TOKEN.match(text, position)
        if not match:
            raise OverlayCheckError(f'unreadable Android.bp syntax at line {line}')
        kind, value = match.lastgroup, match.group()
        if kind == 'string':
            value = re.sub(r'\\(.)', lambda m: _ESCAPES.get(m.group(1), m.group(1)), value[1:-1])
        elif kind == 'raw':
            kind, value = 'string', value[1:-1]
        if kind not in ('space', 'comment'):
            result.append((kind, value, line))
        line += match.group().count('\n')
        position = match.end()
    return result


class _Blueprint:
    def __init__(self, text):
        self.tokens = _tokens(text)
        self.position = 0
        self.variables = {}

    def peek(self, offset=0):
        index = self.position + offset
        return self.tokens[index] if index < len(self.tokens) else ('end', '', -1)

    def take(self, kind=None, value=None):
        token = self.peek()
        if (kind and token[0] != kind) or (value is not None and token[1] != value):
            raise OverlayCheckError(f'unexpected {token[1] or "end"!r} in Android.bp at line {token[2]}')
        self.position += 1
        return token

    def modules(self):
        result = []
        while self.peek()[0] != 'end':
            _, name, line = self.take('ident')
            following = self.peek()[1]
            if following in ('=', '+='):
                self.take()
                value = self.expression()
                if following == '+=':
                    value = _concat(self.variables.get(name, UNKNOWN), value)
                self.variables[name] = value
            elif following == '{':
                result.append({'type': name, 'props': self.mapping(), 'line': line})
            elif following == '(':
                self.take()
                self.skip_group('(', ')')
                result.append({'type': name, 'props': UNKNOWN, 'line': line})
            else:
                raise OverlayCheckError(f'unexpected {following!r} in Android.bp at line {line}')
        return result

    def skip_group(self, opening, closing):
        depth = 1
        while depth:
            kind, value, line = self.take()
            if kind == 'end':
                raise OverlayCheckError('unterminated group in Android.bp')
            depth += (value == opening) - (value == closing)

    def mapping(self):
        self.take('punct', '{')
        result = {}
        while self.peek()[1] != '}':
            kind, key, _ = self.take()
            if kind not in ('ident', 'string'):
                raise OverlayCheckError(f'unexpected {key!r} in Android.bp property map')
            separator = self.take('punct')[1]
            if separator not in (':', '='):
                raise OverlayCheckError('expected ":" in Android.bp property map')
            result[key] = self.expression()
            if self.peek()[1] == ',':
                self.take()
            elif self.peek()[1] != '}':
                raise OverlayCheckError(f'expected "," in Android.bp at line {self.peek()[2]}')
        self.take('punct', '}')
        return result

    def expression(self):
        value = self.value()
        while self.peek()[1] == '+':
            self.take()
            value = _concat(value, self.value())
        return value

    def value(self):
        kind, value, line = self.peek()
        if kind in ('string', 'int'):
            self.take()
            return int(value) if kind == 'int' else value
        if value == '[':
            self.take()
            items = []
            while self.peek()[1] != ']':
                items.append(self.expression())
                if self.peek()[1] == ',':
                    self.take()
                elif self.peek()[1] != ']':
                    raise OverlayCheckError(f'expected "," in Android.bp list at line {line}')
            self.take('punct', ']')
            return items
        if value == '{':
            return self.mapping()
        if kind == 'ident':
            self.take()
            if value in ('true', 'false'):
                return value == 'true'
            if self.peek()[1] == '(':  # select(...) and other configurable values
                self.take()
                self.skip_group('(', ')')
                return UNKNOWN
            return self.variables.get(value, UNKNOWN)
        raise OverlayCheckError(f'unexpected {value or "end"!r} in Android.bp at line {line}')


def _concat(left, right):
    if left is UNKNOWN or right is UNKNOWN:
        return UNKNOWN
    if isinstance(left, list) and isinstance(right, list):
        return left + right
    if isinstance(left, str) and isinstance(right, str):
        return left + right
    if isinstance(left, dict) and isinstance(right, dict):
        return {**left, **right}
    return UNKNOWN


def parse_blueprint(text):
    """Module definitions of one Android.bp file: [{'type', 'props', 'line'}]."""
    return _Blueprint(text).modules()


# --- Resources --------------------------------------------------------------

class Resources:
    """Resources by (type, name): qualifiers and feature flags, plus <overlayable>."""

    def __init__(self):
        self.entries = {}
        self.overlayable = {}
        self.defines_overlayable = False
        self.problems = []

    def add(self, rtype, name, qualifier, flag=None):
        self.entries.setdefault((rtype, name), {}).setdefault(qualifier, set()).add(flag)

    def add_path(self, relative, read):
        """Record one file below a resource directory; read() returns values XML bytes."""
        parsed = classify(relative)
        if parsed is None:
            return
        kind, rtype, qualifier, name, flag = parsed
        if kind == 'file':
            self.add(rtype, name, qualifier, flag)
        else:
            self.add_values(read(), qualifier, flag, relative)

    def add_values(self, data, qualifier, flag, where):
        try:
            root = parse_xml(data)
        except OverlayCheckError as error:
            self.problems.append(f'{where}: {error}')
            return
        if root.tag != 'resources':
            return
        for element in root:
            tag = element.tag
            if not isinstance(tag, str) or tag.startswith('{') or tag in IGNORED_TAGS:
                continue
            if tag == 'overlayable':
                self.add_overlayable(element)
                continue
            name = element.get('name')
            element_flag = element.get(ANDROID + 'featureFlag') or flag
            if tag in ('item', 'bag', 'add-resource'):
                rtype = element.get('type')
            elif tag in ARRAY_TAGS:
                rtype = 'array'
            elif tag == 'declare-styleable':
                rtype = 'styleable'
                for child in element:
                    child_name = child.get('name') or ''
                    if child.tag == 'attr' and child_name and ':' not in child_name:
                        self.add('attr', child_name, qualifier, element_flag)
            else:
                rtype = tag
            if rtype and name:
                self.add(rtype, name, qualifier, element_flag)

    def add_overlayable(self, element):
        self.defines_overlayable = True
        group = {'name': element.get('name') or '', 'actor': element.get('actor')}
        for child in element:
            if child.tag == 'policy':
                policies = {p.strip() for p in (child.get('type') or '').split('|') if p.strip()}
                if policies - POLICIES:
                    self.problems.append(f'overlayable {group["name"]} has unknown policy '
                                         f'{"|".join(sorted(policies - POLICIES))}')
                items = [item for item in child if item.tag == 'item']
            elif child.tag == 'item':
                policies, items = set(), [child]  # aapt2 rejects this; it grants nothing
            else:
                continue
            for item in items:
                key = (item.get('type'), item.get('name'))
                self.overlayable.setdefault(key, []).append(dict(group, policies=sorted(policies)))


def classify(relative):
    """(kind, type, qualifier, name, flag) for a path below a res directory, or None."""
    parts = relative.split('/')
    flags = [p[5:-1] for p in parts if p.startswith('flag(') and p.endswith(')')]
    parts = [p for p in parts if not (p.startswith('flag(') and p.endswith(')'))]
    if len(parts) != 2 or len(flags) > 1:
        return None
    directory, filename = parts
    if filename.startswith('.') or filename.endswith('~'):
        return None
    rtype, _, qualifier = directory.partition('-')
    flag = flags[0] if flags else None
    if rtype == 'values':
        return ('values', None, qualifier, None, flag) if filename.endswith('.xml') else None
    if rtype not in FILE_TYPES:
        return None
    name = filename[:-6] if filename.endswith('.9.png') else filename.rsplit('.', 1)[0]
    return ('file', rtype, qualifier, name, flag)


def parse_xml(data):
    if len(data) > MAX_XML_BYTES:
        raise OverlayCheckError('XML file exceeds the size limit')
    if b'<!ENTITY' in data:
        raise OverlayCheckError('XML entity declarations are not accepted')
    try:
        return ET.fromstring(data)
    except ET.ParseError as error:
        raise OverlayCheckError(f'invalid XML ({error})') from None


# --- Configuration ----------------------------------------------------------

def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise OverlayCheckError(f'duplicate JSON key: {key}')
        result[key] = value
    return result


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=_unique)
    except (OSError, UnicodeError, ValueError) as error:
        raise OverlayCheckError(f'cannot read {Path(path).name}: {error}') from None


def _safe(value, pattern=SAFE_PATH):
    return (isinstance(value, str) and pattern.match(value) is not None
            and all(part not in ('.', '..') for part in value.split('/')))


def load_config(path=CONFIG):
    data = read_json(path)
    if data.get('schema_version') != 1:
        raise OverlayCheckError('overlay configuration needs schema_version 1')
    level = data.get('api_level')
    if not isinstance(level, int) or isinstance(level, bool) or level < 1:
        raise OverlayCheckError('overlay configuration needs a positive api_level')
    tokens = data.get('inapplicable_qualifier_tokens', [])
    try:
        inapplicable = [re.compile(t) for t in tokens if isinstance(t, str)]
    except (re.error, TypeError):
        inapplicable = None
    if not isinstance(tokens, list) or inapplicable is None or len(inapplicable) != len(tokens):
        raise OverlayCheckError('inapplicable_qualifier_tokens must be a list of regular expressions')
    roots = data.get('overlay_roots')
    if not isinstance(roots, list):
        raise OverlayCheckError('overlay configuration needs overlay_roots')
    for root in roots:
        if not isinstance(root, dict) or not _safe(root.get('path')) or root.get('role') not in ROLES:
            raise OverlayCheckError(f'invalid overlay root: {root}')
    targets = {}
    for target in data.get('targets', []):
        package = target.get('package') if isinstance(target, dict) else None
        if not isinstance(package, str) or not PACKAGE.match(package) or package in targets:
            raise OverlayCheckError(f'invalid or duplicate target: {package}')
        sources = target.get('sources')
        if not isinstance(sources, list) or not sources:
            raise OverlayCheckError(f'target {package} needs sources')
        for source in sources:
            if (not isinstance(source, dict) or not _safe(source.get('project'))
                    or not isinstance(source.get('res'), list) or not source['res']
                    or not all(_safe(p, SAFE_PATTERN) for p in source['res'])):
                raise OverlayCheckError(f'invalid source for target {package}')
        targets[package] = target
    return {'api_level': level, 'overlay_roots': roots, 'targets': targets, 'inapplicable': inapplicable}


def pinned_release(path=ENVIRONMENT):
    """The build environment's GrapheneOS release: tag, manifest URL and digests."""
    upstream = read_json(path).get('upstream', {})
    if not TAG.match(str(upstream.get('release_tag', ''))) or not isinstance(upstream.get('manifest_url'), str):
        raise OverlayCheckError('build environment has no usable upstream release')
    return upstream


# --- Overlays ---------------------------------------------------------------

def _partition(props):
    chosen = set()
    if props.get('vendor') is True or props.get('soc_specific') is True or props.get('proprietary') is True:
        chosen.add('vendor')
    if props.get('device_specific') is True:
        chosen.add('odm')
    if props.get('product_specific') is True:
        chosen.add('product')
    if props.get('system_ext_specific') is True:
        chosen.add('system_ext')
    return chosen.pop() if len(chosen) == 1 else ('system' if not chosen else None)


def _relative_label(path, base):
    try:
        return Path(path).resolve().relative_to(Path(base).resolve()).as_posix()
    except ValueError:
        return Path(path).name


def discover(roots, label_base):
    """Parse every runtime_resource_overlay under the roots. Returns (overlays, findings)."""
    overlays, findings, manifests_used, manifests_found = [], [], set(), {}
    for root in roots:
        base = Path(root['path'])
        if not base.is_dir():
            raise OverlayCheckError(f'overlay root not found: {root["label"]}')
        for directory, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith('.') and d != 'out')
            here = Path(directory)
            label = _relative_label(here, label_base)
            if 'AndroidManifest.xml' in filenames:
                manifests_found[(here / 'AndroidManifest.xml').resolve()] = label + '/AndroidManifest.xml'
            if 'Android.mk' in filenames:
                text = (here / 'Android.mk').read_text(encoding='utf-8', errors='replace')
                if 'BUILD_RRO_PACKAGE' in text:
                    findings.append(finding('error', 'android-mk', f'{label}/Android.mk defines an overlay in make; '
                                            'declare it as runtime_resource_overlay in Android.bp so it can be checked'))
            if 'Android.bp' not in filenames:
                continue
            bp = here / 'Android.bp'
            if bp.stat().st_size > MAX_BLUEPRINT_BYTES:
                raise OverlayCheckError(f'{label}/Android.bp exceeds the size limit')
            try:
                modules = parse_blueprint(bp.read_text(encoding='utf-8'))
            except (OverlayCheckError, UnicodeError) as error:
                findings.append(finding('error', 'module', f'{label}/Android.bp: {error}'))
                continue
            for module in modules:
                if module['type'] != 'runtime_resource_overlay':
                    continue
                overlay, problems, manifest_path = read_overlay(module, here, root['role'], label, label_base)
                findings.extend(problems)
                manifests_used.add(manifest_path)
                if overlay:
                    overlays.append(overlay)
    for path, label in sorted(manifests_found.items(), key=lambda item: item[1]):
        if path in manifests_used:
            continue
        try:
            root_element = parse_xml(path.read_bytes())
        except (OverlayCheckError, OSError):
            continue
        if root_element.find('overlay') is not None:
            findings.append(finding('error', 'module', f'{label} declares an overlay that no '
                                    'runtime_resource_overlay module builds'))
    names = [o['module'] for o in overlays]
    for name in sorted({n for n in names if names.count(n) > 1}):
        findings.append(finding('error', 'module', f'overlay module name {name} is defined more than once',
                                overlay=name))
    return overlays, findings


def read_overlay(module, directory, role, label, label_base):
    """(overlay or None, findings, manifest path) for one runtime_resource_overlay module."""
    props = module['props']
    where = f'{label}/Android.bp:{module["line"]}'
    if props is UNKNOWN or not isinstance(props.get('name'), str):
        return None, [finding('error', 'module', f'{where}: runtime_resource_overlay without a readable name')], None
    name = props['name']
    manifest = props.get('manifest', 'AndroidManifest.xml')
    resource_dirs = props.get('resource_dirs', ['res'])
    if (not _safe(manifest) or not isinstance(resource_dirs, list)
            or not all(_safe(d) for d in resource_dirs)):
        return None, [finding('error', 'module', f'{where}: manifest or resource_dirs cannot be read '
                              'statically', overlay=name)], None
    manifest_path = (directory / manifest).resolve()
    try:
        element = parse_xml(manifest_path.read_bytes())
    except (OSError, OverlayCheckError) as error:
        return None, [finding('error', 'manifest', f'{name}: cannot read {manifest}: {error}',
                              overlay=name)], manifest_path
    tag = element.find('overlay')
    target = tag.get(ANDROID + 'targetPackage') if tag is not None else None
    if element.tag != 'manifest' or not target:
        return None, [finding('error', 'manifest', f'{name}: {manifest} has no <overlay> with a targetPackage',
                              overlay=name)], manifest_path
    problems = []
    try:
        priority = int(tag.get(ANDROID + 'priority', '0'))
    except ValueError:
        problems.append(finding('error', 'manifest', f'{name}: android:priority is not an integer', overlay=name))
        priority = 0
    application = element.find('application')
    overlay = {
        'module': name, 'package': element.get('package'), 'role': role,
        'partition': _partition(props), 'target': target,
        'target_name': tag.get(ANDROID + 'targetName', ''),
        'static': tag.get(ANDROID + 'isStatic') == 'true', 'priority': priority,
        'has_code': application is None or application.get(ANDROID + 'hasCode') != 'false',
        'certificate': props.get('certificate'),
        'path': _relative_label(manifest_path.parent, label_base), 'resources': Resources(),
    }
    required = tag.get(ANDROID + 'requiredSystemPropertyName')
    if tag.get(ANDROID + 'resourcesMap'):
        problems.append(finding('error', 'resources-map', f'{name} uses android:resourcesMap, which this check '
                                'does not model', overlay=name, target=target))
    if required:
        problems.append(finding('warning', 'conditional', f'{name} applies only when the system property '
                                f'{required} matches', overlay=name, target=target))
    if props.get('static_libs') or props.get('resource_libs'):
        problems.append(finding('warning', 'static-libs', f'{name} takes resources from libraries, which this '
                                'check does not read', overlay=name, target=target))
    for resource_dir in resource_dirs:
        base = directory / resource_dir
        if not base.is_dir():
            problems.append(finding('error', 'module', f'{name}: resource directory {resource_dir} is missing',
                                    overlay=name))
            continue
        for path in sorted(p for p in base.rglob('*') if p.is_file()):
            overlay['resources'].add_path(path.relative_to(base).as_posix(), path.read_bytes)
    for problem in overlay['resources'].problems:
        problems.append(finding('error', 'module', f'{name}: {problem}', overlay=name))
    return overlay, problems, manifest_path


# --- Target sources ---------------------------------------------------------

def _match(parts, pattern):
    if not pattern:
        return not parts
    head, rest = pattern[0], pattern[1:]
    if head == '**':
        return any(_match(parts[index:], rest) for index in range(len(parts) + 1))
    return bool(parts) and fnmatch.fnmatchcase(parts[0], head) and _match(parts[1:], rest)


def match_dirs(directories, pattern):
    """Directories matching a pattern: '*' within one path segment, '**' for any segments."""
    segments = pattern.split('/')
    return sorted(d for d in directories if _match(d.split('/'), segments))


class SourceTree:
    """Target sources from a checked-out tree, by manifest path or by repository name."""

    kind = 'tree'

    def __init__(self, root, projects=None):
        self.root = Path(root)
        self.projects = projects  # path -> project entry for the name layout
        if not self.root.is_dir():
            raise OverlayCheckError('source tree not found')

    def base(self, project):
        if self.projects is None:
            return self.root / project
        entry = self.projects.get(project)
        return self.root / entry['name'] if entry else None

    def describe(self, project):
        base = self.base(project)
        if base is None:
            return {'available': False, 'reason': 'not in the manifest'}
        if not base.is_dir():
            return {'available': False, 'reason': 'not in the source tree'}
        return {'available': True}

    def expand(self, project, pattern):
        base = self.base(project)
        segments = pattern.split('/')
        literal = []
        for segment in segments:
            if '*' in segment:
                break
            literal.append(segment)
        start = base.joinpath(*literal) if base else None
        if not start or not start.is_dir():
            return []
        if len(literal) == len(segments):
            return [pattern]
        depth = None if '**' in segments else len(segments) - len(literal)
        found = []
        for directory, dirnames, _ in os.walk(start):
            level = len(Path(directory).relative_to(start).parts)
            dirnames[:] = [d for d in dirnames if not d.startswith('.')]
            if depth is not None and level >= depth:
                dirnames[:] = []
            found.append(Path(directory).relative_to(base).as_posix())
        return match_dirs(found, pattern)

    def files(self, project, directory):
        start = self.base(project) / directory
        result = []
        for path, dirnames, filenames in os.walk(start):
            dirnames[:] = [d for d in dirnames if not d.startswith('.')]
            relative = Path(path).relative_to(start)
            if len(relative.parts) >= 2:
                dirnames[:] = []
            result.extend((relative / name).as_posix() for name in filenames)
        return sorted(result)

    def read_many(self, project, paths):
        base = self.base(project)
        result = {}
        for path in paths:
            full = base / path
            if full.stat().st_size > MAX_XML_BYTES:
                raise OverlayCheckError(f'{project}/{path} exceeds the size limit')
            result[path] = full.read_bytes()
        return result


def https_only(url):
    """Production remote policy: https URLs without credentials."""
    return url.startswith('https://') and '@' not in url[8:].split('/', 1)[0]


class ManifestRemote:
    """Target sources fetched from each project's manifest remote into a partial-clone cache.

    Every project is fetched at the full commit the manifest pins, with trees
    only; values files are then fetched in batches. Nothing leaves the cache.
    """

    kind = 'fetch'

    def __init__(self, cache, projects, url_allowed=https_only, log=None):
        self.cache = Path(cache)
        self.projects = projects
        self.url_allowed = url_allowed
        self.log = log or (lambda message: None)
        self.listings = {}
        self.fetched_blobs = 0

    def _git(self, repo, *arguments, offline=True, input_data=None, timeout=GIT_TIMEOUT):
        env = dict(os.environ, GIT_TERMINAL_PROMPT='0')
        if offline:
            env['GIT_NO_LAZY_FETCH'] = '1'
        try:
            result = subprocess.run(['git', '-C', str(repo), *arguments], input=input_data,
                                    capture_output=True, env=env, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise OverlayCheckError(f'git {arguments[0]} timed out') from None
        if result.returncode != 0:
            message = result.stderr.decode('utf-8', 'replace').strip()[-400:]
            raise OverlayCheckError(f'git {arguments[0]} failed: {message}')
        return result.stdout

    def project(self, project):
        entry = self.projects.get(project)
        if entry is None:
            raise OverlayCheckError(f'project {project} is not in the manifest')
        return entry

    def repository(self, url, promisor=True):
        if not self.url_allowed(url):
            raise OverlayCheckError(f'refusing to fetch from {url}: only https remotes are allowed')
        name = re.sub(r'^[a-z]+://', '', url).strip('/').removesuffix('.git')
        name = re.sub(r'[^A-Za-z0-9._/-]', '_', name)
        if not _safe(name):
            raise OverlayCheckError(f'cannot derive a cache path from {url}')
        repo = self.cache / 'git' / (name + '.git')
        if not (repo / 'HEAD').exists():
            repo.mkdir(parents=True, exist_ok=True)
            self._git(repo, 'init', '--quiet', '--bare')
            self._git(repo, 'remote', 'add', 'origin', url)
            if promisor:
                for key, value in (('core.repositoryformatversion', '1'), ('remote.origin.promisor', 'true'),
                                   ('remote.origin.partialclonefilter', 'blob:none'),
                                   ('extensions.partialClone', 'origin')):
                    self._git(repo, 'config', key, value)
        elif self._git(repo, 'config', '--get', 'remote.origin.url').decode().strip() != url:
            raise OverlayCheckError(f'cache entry {repo.name} belongs to another remote')
        return repo

    def _has(self, repo, objects):
        if not objects:
            return set()
        output = self._git(repo, 'cat-file', '--batch-check', input_data=''.join(o + '\n' for o in objects).encode())
        return {line.split()[0] for line in output.decode().splitlines() if not line.endswith(' missing')}

    def listing(self, project):
        if project in self.listings:
            return self.listings[project]
        entry = self.project(project)
        if entry.get('problem'):
            raise OverlayCheckError(f'project {project}: {entry["problem"]}')
        repo = self.repository(entry['url'])
        commit = entry['revision']
        if not self._has(repo, [commit]):
            self.log(f'fetching {project} trees at {commit[:12]}')
            self._git(repo, 'fetch', '--quiet', '--depth=1', '--filter=blob:none', '--no-tags',
                      'origin', commit, offline=False)
            if not self._has(repo, [commit]):
                raise OverlayCheckError(f'{project}: commit {commit} was not fetched')
        files = {}
        output = self._git(repo, 'ls-tree', '-r', '-z', '--full-tree', commit)
        for record in output.split(b'\0'):
            if not record:
                continue
            meta, _, path = record.partition(b'\t')
            mode, kind, oid = meta.decode().split()
            if kind == 'blob':
                files[path.decode('utf-8', 'surrogateescape')] = oid
        self.listings[project] = (repo, files)
        return self.listings[project]

    def describe(self, project):
        entry = self.projects.get(project)
        if entry is None:
            return {'available': False, 'reason': 'not in the manifest'}
        result = {'name': entry['name'], 'revision': entry['revision'], 'url': entry['url']}
        if entry.get('problem'):
            return dict(result, available=False, reason=entry['problem'])
        return dict(result, available=True)

    def expand(self, project, pattern):
        _, files = self.listing(project)
        directories = set()
        for path in files:
            parts = path.split('/')[:-1]
            for index in range(1, len(parts) + 1):
                directories.add('/'.join(parts[:index]))
        return match_dirs(directories, pattern)

    def files(self, project, directory):
        _, files = self.listing(project)
        prefix = directory + '/'
        return sorted(p[len(prefix):] for p in files if p.startswith(prefix)
                      and p[len(prefix):].count('/') <= 2)

    def read_many(self, project, paths):
        repo, files = self.listing(project)
        wanted = sorted({files[p] for p in paths})
        present = self._has(repo, wanted)
        missing = [o for o in wanted if o not in present]
        if missing:
            self.log(f'fetching {len(missing)} resource files of {project}')
        for start in range(0, len(missing), FETCH_BATCH):
            batch = missing[start:start + FETCH_BATCH]
            self._git(repo, 'fetch', '--quiet', '--no-tags', '--no-write-fetch-head', '--filter=blob:none',
                      'origin', *batch, offline=False)
        self.fetched_blobs += len(missing)
        if missing and len(self._has(repo, missing)) != len(missing):
            raise OverlayCheckError(f'{project}: some resource files were not fetched')
        contents = self._cat(repo, wanted)
        return {p: contents[files[p]] for p in paths}

    def _cat(self, repo, objects):
        if not objects:
            return {}
        output = self._git(repo, 'cat-file', '--batch', input_data=''.join(o + '\n' for o in objects).encode())
        result, position = {}, 0
        for oid in objects:
            end = output.index(b'\n', position)
            header = output[position:end].decode().split()
            if len(header) != 3 or header[0] != oid or header[1] != 'blob':
                raise OverlayCheckError('unexpected object in the fetch cache')
            size = int(header[2])
            if size > MAX_XML_BYTES:
                raise OverlayCheckError('resource file exceeds the size limit')
            result[oid] = output[end + 1:end + 1 + size]
            position = end + 2 + size
        return result


def parse_manifest(data):
    """Project map of a repo manifest: path -> {name, revision, url}."""
    root = parse_xml(data)
    if root.tag != 'manifest':
        raise OverlayCheckError('not a repo manifest')
    remotes = {r.get('name'): r for r in root.findall('remote')}
    default = root.find('default')
    default = default.attrib if default is not None else {}
    projects = {}
    for project in root.findall('project'):
        name = project.get('name')
        path = project.get('path', name)
        remote_name = project.get('remote', default.get('remote'))
        remote = remotes.get(remote_name)
        if not name or not _safe(path) or remote is None or path in projects:
            raise OverlayCheckError(f'manifest project {name} is incomplete or duplicated')
        revision = project.get('revision') or remote.get('revision') or default.get('revision')
        fetch = remote.get('fetch', '')
        entry = {'name': name, 'revision': revision,
                 'url': fetch.rstrip('/') + '/' + name if '://' in fetch else None}
        if entry['url'] is None:
            entry['problem'] = 'its remote has a relative fetch URL'
        elif not SHA1.match(revision or ''):
            entry['problem'] = 'the manifest does not pin it to a full commit'
        projects[path] = entry
    return projects


def fetch_manifest(cache, url, tag, url_allowed=https_only, log=None):
    remote = ManifestRemote(cache, {}, url_allowed, log)
    repo = remote.repository(url, promisor=False)
    ref = f'refs/tags/{tag}'
    if not remote._has(repo, [ref]):
        (log or (lambda m: None))(f'fetching manifest tag {tag}')
        remote._git(repo, 'fetch', '--quiet', '--depth=1', '--no-tags', 'origin', f'+{ref}:{ref}', offline=False)
    return remote._git(repo, 'cat-file', 'blob', f'{ref}^{{commit}}:default.xml')


# --- Checks -----------------------------------------------------------------

def load_target(target, source):
    """Resources of one registered target and the problems reading its sources."""
    resources, problems, used = Resources(), [], []
    for entry in target['sources']:
        project = entry['project']
        description = dict(source.describe(project), project=project, res=list(entry['res']))
        used.append(description)
        if not description['available']:
            problems.append(f'project {project} is {description["reason"]}' if description['reason'].startswith('not')
                            else f'project {project}: {description["reason"]}')
            continue
        values = []
        for pattern in entry['res']:
            directories = source.expand(project, pattern)
            if not directories:
                problems.append(f'no resource directory matches {project}/{pattern}')
            for directory in directories:
                for relative in source.files(project, directory):
                    parsed = classify(relative)
                    if parsed and parsed[0] == 'values':
                        values.append((directory, relative))
                    elif parsed:
                        resources.add_path(relative, None)
        contents = source.read_many(project, [f'{d}/{r}' for d, r in values])
        for directory, relative in values:
            resources.add_path(relative, lambda: contents[f'{directory}/{relative}'])
    problems.extend(resources.problems)
    return resources, problems, used


def module_checks(overlay):
    name, target, result = overlay['module'], overlay['target'], []
    if not overlay['static']:
        result.append(finding('error', 'not-static', f'{name} is not android:isStatic="true"', name, target))
    if overlay['has_code']:
        result.append(finding('error', 'has-code', f'{name} does not declare android:hasCode="false"', name, target))
    allowed = ROLES[overlay['role']]
    if overlay['partition'] not in allowed:
        result.append(finding('error', 'partition', f'{name} is a {overlay["role"]} overlay and must install on '
                              f'{" or ".join(sorted(allowed))}, not {overlay["partition"] or "conflicting partitions"}',
                              name, target))
    certificate = overlay['certificate']
    if certificate == 'platform':
        result.append(finding('error', 'platform-key', f'{name} is signed with the platform key; overlays use the '
                              'default key', name, target))
    elif certificate is not None:
        result.append(finding('error', 'certificate', f'{name} sets certificate; overlays use the default key',
                              name, target))
    return result


def applicable(qualifier, inapplicable):
    """False when a qualifier token (a pseudo-locale, a TV or watch mode) never matches the phones."""
    return not any(p.fullmatch(token) for token in qualifier.split('-') for p in inapplicable)


def resource_checks(overlay, target, api_level, inapplicable=(), incomplete=False):
    name, package, result = overlay['module'], overlay['target'], []
    caveat = ' (its sources are incomplete)' if incomplete else ''
    fulfilled = {'public', PARTITION_POLICY.get(overlay['partition'], 'system')}
    for key, qualifiers in sorted(overlay['resources'].entries.items()):
        resource = f'{key[0]}/{key[1]}'
        mine = set(qualifiers)
        theirs = target.entries.get(key)
        if theirs is None:
            result.append(finding('error', 'missing', f'{name}: {resource} is not in {package}{caveat}',
                                  name, package, resource, mine))
            continue
        lower = {q.lower() for q in theirs}
        extra = {q for q in mine if q.lower() not in lower}
        if extra:
            result.append(finding('warning', 'qualifier-not-in-target', f'{name}: {resource} adds qualifiers '
                                  f'{package} does not define for it: {show(extra)}', name, package, resource, extra))
        mine_lower = {q.lower() for q in mine}
        uncovered = {q for q in theirs if q.lower() not in mine_lower and applicable(q, inapplicable)}
        always = {q for q in uncovered if API_QUALIFIER.match(q) and int(q[1:]) <= api_level}
        if always:
            result.append(finding('error', 'shadowed', f'{name}: {package} defines {resource} for {show(always)}, '
                                  f'which applies on every API {api_level} device and replaces the overlaid value',
                                  name, package, resource, always))
        if uncovered - always:
            result.append(finding('warning', 'target-qualifier', f'{name}: {package} also defines {resource} for '
                                  f'qualifiers the overlay does not cover, where its own value still applies: '
                                  f'{show(uncovered - always)}', name, package, resource, uncovered - always))
        if all(None not in flags for flags in theirs.values()):
            flags = sorted({f for fs in theirs.values() for f in fs})
            result.append(finding('warning', 'flagged-in-target', f'{name}: {resource} exists in {package} only '
                                  f'behind feature flag {", ".join(flags)}', name, package, resource))
        if not target.defines_overlayable:
            continue
        groups = target.overlayable.get(key)
        if not groups:
            result.append(finding('error', 'not-overlayable', f'{name}: {package} declares <overlayable> and '
                                  f'{resource} is in no overlayable group', name, package, resource))
            continue
        group = groups[0]
        if overlay['target_name'] != group['name']:
            result.append(finding('error', 'target-name', f'{name}: android:targetName '
                                  f'"{overlay["target_name"]}" does not match overlayable "{group["name"]}" '
                                  f'of {resource}', name, package, resource))
        policies = set(group['policies'])
        if not policies & fulfilled:
            if policies & KEY_POLICIES:
                result.append(finding('error', 'needs-key', f'{name}: {resource} is overlayable only with policy '
                                      f'{"|".join(sorted(policies))}, which needs a signing key; the overlay '
                                      f'fulfils {"|".join(sorted(fulfilled))}', name, package, resource))
            else:
                result.append(finding('error', 'policy', f'{name}: {resource} is overlayable only with policy '
                                      f'{"|".join(sorted(policies)) or "none"}; the overlay fulfils '
                                      f'{"|".join(sorted(fulfilled))}', name, package, resource))
    return result


def _order(overlay):
    """OverlayConfig's order for static overlays; the last one wins. APKs tie-break by path."""
    partition = overlay['partition'] if overlay['partition'] in PARTITION_ORDER else 'system'
    return (PARTITION_ORDER.index(partition), overlay['priority'], overlay['module'])


def cross_checks(package, group):
    result = []
    ordered = sorted(group, key=_order)
    for index, first in enumerate(ordered):
        for second in ordered[index + 1:]:
            pair = f'{first["module"]} ({first["partition"]}) and {second["module"]} ({second["partition"]})'
            if first['priority'] == second['priority']:
                how = ('the APK path decides their order' if first['partition'] == second['partition']
                       else 'only the partition order decides which wins')
                result.append(finding('error', 'same-priority', f'{pair} both target {package} with priority '
                                      f'{first["priority"]}; {how}', f'{first["module"]},{second["module"]}',
                                      package))
            shared = sorted(set(first['resources'].entries) & set(second['resources'].entries))
            for key in shared:
                result.append(finding('error', 'overlap', f'{pair} both overlay {key[0]}/{key[1]} in {package}; '
                                      f'{second["module"]} wins', f'{first["module"]},{second["module"]}',
                                      package, f'{key[0]}/{key[1]}'))
    return result


def run_check(overlays, config, source):
    """Check discovered overlays against the registered targets. Returns (findings, targets)."""
    findings, summaries = [], {}
    by_target = {}
    for overlay in overlays:
        findings.extend(module_checks(overlay))
        by_target.setdefault(overlay['target'], []).append(overlay)
    for package, group in sorted(by_target.items()):
        registered = config['targets'].get(package)
        if registered is None:
            for overlay in group:
                findings.append(finding('error', 'unknown-target', f'{overlay["module"]} targets {package}, which '
                                        'is not in the target registry', overlay['module'], package))
        else:
            resources, problems, used = load_target(registered, source)
            for problem in problems:
                findings.append(finding('error', 'target-source', f'{package}: {problem}', target=package))
            summaries[package] = {'resources': len(resources.entries),
                                  'defines_overlayable': resources.defines_overlayable, 'sources': used}
            for overlay in group:
                findings.extend(resource_checks(overlay, resources, config['api_level'], config['inapplicable'],
                                                bool(problems)))
        findings.extend(cross_checks(package, group))
    return sort_findings(findings), summaries


def sort_findings(findings):
    return sorted(findings, key=lambda f: (f['severity'] != 'error', f.get('target', ''), f.get('overlay', ''),
                                           f['code'], f.get('resource', '')))


def build_report(overlays, findings, summaries, source_info, strict):
    errors = sum(f['severity'] == 'error' for f in findings)
    warnings = len(findings) - errors
    failed = errors > 0 or (strict and warnings > 0)
    return {
        'schema_version': 1,
        'check': 'overlays',
        'status': 'fail' if failed else 'pass',
        'strict': strict,
        'source': source_info,
        'overlays': [{'module': o['module'], 'package': o['package'], 'path': o['path'], 'role': o['role'],
                      'partition': o['partition'], 'target': o['target'], 'target_name': o['target_name'] or None,
                      'static': o['static'], 'priority': o['priority'],
                      'resources': len(o['resources'].entries)} for o in overlays],
        'targets': summaries,
        'summary': {'errors': errors, 'warnings': warnings, 'overlays': len(overlays), 'targets': len(summaries)},
        'findings': findings,
    }


def render_text(report):
    lines = []
    source = report['source']
    if source['kind'] == 'fetch':
        lines.append(f'Overlay check against GrapheneOS {source.get("release_tag") or "(untagged manifest)"} '
                     f'(manifest {source["manifest"]}, sha256 {source["manifest_sha256"][:12]}; fetched sources)')
    else:
        lines.append(f'Overlay check against the source tree {source["path"]} ({source["layout"]} layout)')
    for overlay in report['overlays']:
        lines.append(f'  {overlay["partition"] or "?":<8} {overlay["module"]} -> {overlay["target"]} '
                     f'(priority {overlay["priority"]}, {overlay["resources"]} resources)')
    for severity, title in (('error', 'Errors'), ('warning', 'Warnings')):
        chosen = [f for f in report['findings'] if f['severity'] == severity]
        if chosen:
            lines.append(f'{title}:')
            lines.extend(f'  [{f["code"]}] {f["message"]}' for f in chosen)
    summary = report['summary']
    lines.append(f'{report["status"].upper()}: {summary["errors"]} errors, {summary["warnings"]} warnings '
                 f'({summary["overlays"]} overlays, {summary["targets"]} targets checked)')
    return '\n'.join(lines)


# --- Command line -----------------------------------------------------------

def _parser():
    parser = argparse.ArgumentParser(prog='diamaneos overlays check', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--config', type=Path, default=CONFIG, help='overlay roots and target registry')
    parser.add_argument('--root', type=Path, default=ROOT.parent,
                        help='directory holding the overlay repositories named in the configuration')
    parser.add_argument('--product-overlays', type=Path, action='append', default=[],
                        help='product overlay directory (replaces the configured roots; repeatable)')
    parser.add_argument('--device-overlays', type=Path, action='append', default=[],
                        help='device overlay directory (replaces the configured roots; repeatable)')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--source-tree', type=Path, help='checked-out source tree holding the targets')
    source.add_argument('--fetch', action='store_true',
                        help="fetch the targets' resource files at the commits the release manifest pins")
    parser.add_argument('--source-layout', choices=('path', 'name'), default='path',
                        help='source tree layout: manifest paths (default) or repository names')
    parser.add_argument('--cache', type=Path, help='download cache directory (needed with --fetch)')
    parser.add_argument('--tag', help='GrapheneOS release tag (default: the pinned build environment)')
    parser.add_argument('--manifest', type=Path, help='release manifest file instead of fetching the tag')
    parser.add_argument('--json', action='store_true', help='print the report as JSON')
    parser.add_argument('--strict', action='store_true', help='fail on warnings too')
    return parser


def _log(message):
    print(message, file=sys.stderr, flush=True)


def resolve_manifest(args, release, url_allowed=https_only):
    """Manifest bytes and how they were identified.

    A fetched manifest of the pinned release must match the build environment's
    digest. A manifest file is identified by that digest or by an explicit --tag.
    """
    tag = args.tag or release['release_tag']
    if not TAG.match(tag):
        raise OverlayCheckError('invalid release tag')
    pinned = release.get('default_manifest_sha256')
    if args.manifest:
        try:
            data = args.manifest.read_bytes()
        except OSError:
            raise OverlayCheckError('cannot read the manifest file') from None
        digest = hashlib.sha256(data).hexdigest()
        if digest == pinned:
            tag, state = release['release_tag'], 'pinned-digest'
        else:
            tag, state = args.tag, 'unverified-file'
        return data, {'release_tag': tag, 'manifest_sha256': digest, 'manifest': state}
    if not args.cache:
        raise OverlayCheckError('fetching the release manifest needs --cache (or pass --manifest)')
    data = fetch_manifest(args.cache, release['manifest_url'], tag, url_allowed, _log)
    digest = hashlib.sha256(data).hexdigest()
    if tag == release['release_tag'] and digest != pinned:
        raise OverlayCheckError(f'the fetched manifest of {tag} does not match the pinned digest')
    state = 'pinned-digest' if tag == release['release_tag'] else 'fetched-unpinned-tag'
    return data, {'release_tag': tag, 'manifest_sha256': digest, 'manifest': state}


def overlay_roots(args, config):
    """Overlay directories with their roles, and the base for printed paths."""
    if args.product_overlays or args.device_overlays:
        roots = [{'path': p, 'role': 'product', 'label': p.name} for p in args.product_overlays]
        roots += [{'path': p, 'role': 'device', 'label': p.name} for p in args.device_overlays]
        common = Path(os.path.commonpath([str(r['path'].resolve()) for r in roots]))
        return roots, common.parent
    roots = [{'path': args.root / r['path'], 'role': r['role'], 'label': r['path']}
             for r in config['overlay_roots']]
    return roots, args.root


def main(argv=None, url_allowed=https_only, release_path=ENVIRONMENT):
    args = _parser().parse_args(argv)
    try:
        config = load_config(args.config)
        roots, label_base = overlay_roots(args, config)
        if args.fetch:
            if not args.cache:
                raise OverlayCheckError('--fetch needs --cache')
            data, info = resolve_manifest(args, pinned_release(release_path), url_allowed)
            source = ManifestRemote(args.cache, parse_manifest(data), url_allowed, _log)
            source_info = dict(info, kind='fetch')
        else:
            projects = None
            if args.source_layout == 'name':
                if not (args.manifest or args.cache):
                    raise OverlayCheckError('the name layout needs --manifest or --cache for the project map')
                data, _ = resolve_manifest(args, pinned_release(release_path), url_allowed)
                projects = parse_manifest(data)
            source = SourceTree(args.source_tree, projects)
            source_info = {'kind': 'tree', 'path': args.source_tree.name, 'layout': args.source_layout}
        overlays, findings = discover(roots, label_base)
        checked, summaries = run_check(overlays, config, source)
        if args.fetch:
            source_info['fetched_files'] = source.fetched_blobs
        report = build_report(overlays, sort_findings(findings + checked), summaries, source_info, args.strict)
    except OverlayCheckError as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2) if args.json else render_text(report))
    return 1 if report['status'] == 'fail' else 0


if __name__ == '__main__':
    sys.exit(main())

"""Kbuild files for the FP6 vendor module packages, from Qualcomm's DDK registry.

Every package under modules/ of the vendor tree registers its modules in
modules.bzl (register_modules, one registry.register() per ddk_module). This
reads that registry the way the DDK macros do for srcs, conditional_srcs,
includes, local_defines and copts and writes the Kbuild of an external-module
(M=) build against the device kernel build:

  PKG/Kbuild                obj-m lines, objects and per-object flags
  OUT/vendor_autoconf.h     the vendor CONFIG values as C defines
  OUT/modules.tsv           module, registry name, objects

The vendor values are the package defconfig on top of the base layer's, on top
of the kernel's auto.conf. Headers come from the package, the packages named in
fp6/header-deps.txt, hw-common and the base layer, then the kernel.
"""
import os
import re


class KbuildError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise KbuildError(message)


def read_config(path):
    """CONFIG_ values of a defconfig or auto.conf. Unset symbols read 'n'. A missing file is an error."""
    require(os.path.isfile(path), 'configuration file missing: ' + str(path))
    values = {}
    with open(path) as stream:
        for line in stream:
            line = line.strip()
            match = re.match(r'^(CONFIG_\w+)=(.*)$', line)
            if match:
                values[match.group(1)] = match.group(2)
                continue
            match = re.match(r'^# (CONFIG_\w+) is not set$', line)
            if match:
                values[match.group(1)] = 'n'
    return values


def registry(path):
    """The modules a modules.bzl registers, in registration order."""
    found = []

    class Registry:
        def register(self, **kwargs):
            found.append(kwargs)

    scope = {'select': lambda choices: choices.get('//conditions:default', []), 'True': True, 'False': False}
    with open(path) as stream:
        source = stream.read()
    exec(compile(source, 'modules.bzl', 'exec'), scope)
    require('register_modules' in scope, 'modules.bzl has no register_modules: ' + str(path))
    scope['register_modules'](Registry())
    for entry in found:
        require(isinstance(entry.get('out'), str) and entry['out'].endswith('.ko') and entry.get('name'),
                'invalid module registration in ' + str(path))
    return found


def genrule_outputs(path):
    """Output file of each genrule in fp6/rules.extra, by rule name."""
    if not os.path.exists(path):
        return {}
    with open(path) as stream:
        text = stream.read()
    return {m.group(1): m.group(2) for m in re.finditer(r'genrule\(\s*name = "(\w+)",.*?outs = \["([^"]+)"', text, re.S)}


def package_config(root, base, kernel_auto, extra=()):
    """The vendor values of a package and the kernel values it builds against."""
    vendor = dict(read_config(os.path.join(base, 'defconfig')))
    vendor.update(read_config(os.path.join(root, 'defconfig')))
    for name in extra:
        vendor.update(read_config(os.path.join(root, name)))
    # Symbols a package symbol selects, as olddefconfig would set them.
    kconfig = os.path.join(root, 'Kconfig')
    text = ''
    if os.path.exists(kconfig):
        with open(kconfig) as stream:
            text = stream.read()
    for block in re.split(r'\n(?=config )', text):
        match = re.match(r'config (\w+)', block)
        if match and vendor.get('CONFIG_' + match.group(1)) in ('y', 'm'):
            for symbol in re.findall(r'^\s*select (\w+)', block, re.M):
                vendor.setdefault('CONFIG_' + symbol, vendor['CONFIG_' + match.group(1)])
    return vendor, read_config(kernel_auto)


def include_dirs(entries):
    """Include directories a registry names, only those that are header directories."""
    found = []
    for entry in entries:
        for directory in entry.get('includes', []):
            if '/include' in '/' + directory and directory not in found:
                found.append(directory)
    return found


def header_packages(root):
    """Include flags for the packages listed in fp6/header-deps.txt."""
    listing = os.path.join(root, 'fp6', 'header-deps.txt')
    flags = []
    packages = []
    if os.path.exists(listing):
        with open(listing) as stream:
            packages = stream.read().split()
    for package in packages:
        other = os.path.join(os.path.dirname(root), package)
        directories = ['include', 'include/uapi'] + [d for d in include_dirs(registry(os.path.join(other, 'modules.bzl')))
                                                     if d not in ('include', 'include/uapi')]
        flags += ['-I%s/%s' % (other, d) for d in directories if os.path.isdir(os.path.join(other, d))]
    return flags


def generate(root, base, out, kernel_auto, only=(), extra_defconfig=()):
    """Write PKG/Kbuild, OUT/vendor_autoconf.h and OUT/modules.tsv. Returns the module table."""
    entries = registry(os.path.join(root, 'modules.bzl'))
    names = {e['out'][:-3] for e in entries}
    require(set(only) <= names, 'not in modules.bzl: ' + ', '.join(sorted(set(only) - names)))
    vendor, kernel = package_config(root, base, kernel_auto, extra_defconfig)
    enabled = lambda key: vendor.get(key, kernel.get(key)) in ('y', 'm')
    outputs = genrule_outputs(os.path.join(root, 'fp6', 'rules.extra'))
    common = os.path.join(os.path.dirname(root), 'hw-common')
    # Modules that keep their include directories after the kernel's (late_includes) stay out of the early flags.
    early = include_dirs([e for e in entries if not e.get('late_includes') and (not only or e['out'][:-3] in only)])
    includes = '-I$(VENDOR_ROOT)/arch/arm64/include -I$(VENDOR_ROOT)/include -I$(VENDOR_ROOT)/include/uapi'
    includes += ' ' + ' '.join('-I$(VENDOR_ROOT)/' + d for d in early)
    includes += ' ' + ' '.join(header_packages(root))
    includes += ' -I%s/include ' % common + ' '.join('-I%s/%s' % (base, d) for d in ('arch/arm64/include', 'include', 'include/uapi'))
    lines = ['# Generated by diamaneos kernel build from modules.bzl. Do not edit.',
             'LINUXINCLUDE := %s $(LINUXINCLUDE) -I$(VENDOR_ROOT) -I%s' % (' '.join(includes.split()), base),
             'ccflags-y += -include $(VENDOR_OUT)/vendor_autoconf.h']
    table = []
    for entry in entries:
        module = entry['out'][:-3]
        if only and module not in only:
            continue
        sources = list(entry.get('srcs', []))
        for key, choice in (entry.get('conditional_srcs') or {}).items():
            sources += choice.get(enabled(key), [])
        objects = [s[:-2] + '.o' for s in sources if s.endswith(('.c', '.S'))]
        directories = []
        for source in sources:
            directory = os.path.dirname(source)
            if directory not in directories:
                directories.append(directory)
        flags = ['-I$(VENDOR_ROOT)/' + d for d in entry.get('includes', [])]
        flags += ['-I$(VENDOR_ROOT)/' + d for d in directories]
        flags += ['-D' + d for d in entry.get('local_defines', [])]
        options = list(entry.get('copts', []))
        for index, option in enumerate(options):
            match = re.fullmatch(r'\$\(location :(\w+)\)', option)
            if match:
                require(match.group(1) in outputs, 'unknown genrule in copts: ' + option)
                options[index] = option = outputs[match.group(1)]
            if index and options[index - 1] == '-include' and (os.path.exists(os.path.join(root, option)) or match):
                options[index] = '$(VENDOR_ROOT)/' + option
        flags += options
        lines.append('obj-m += %s.o' % module)
        if not (len(objects) == 1 and objects[0] == module + '.o'):
            lines.append('%s-y := %s' % (module, ' '.join(objects)))
        for obj in objects:
            lines.append('CFLAGS_%s += %s' % (obj, ' '.join(flags)))
        table.append('%s.ko\t%s\t%s' % (module, entry['name'], ' '.join(objects)))
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(root, 'Kbuild'), 'w') as stream:
        stream.write('\n'.join(lines) + '\n')
    header = ['/* Generated by diamaneos kernel build: vendor Kconfig values. Do not edit. */']
    for key, value in sorted(vendor.items()):
        if kernel.get(key) == value or value == 'n':
            continue
        if value == 'y':
            header.append('#define %s 1' % key)
        elif value == 'm':
            header.append('#define %s_MODULE 1' % key)
        else:
            header.append('#define %s %s' % (key, value))
    with open(os.path.join(out, 'vendor_autoconf.h'), 'w') as stream:
        stream.write('\n'.join(header) + '\n')
    with open(os.path.join(out, 'modules.tsv'), 'w') as stream:
        stream.write('\n'.join(table) + '\n')
    return table


def feature_probes(kernel_source, probes):
    """What the sources contain of the cfg80211 features the Wi-Fi driver tests for: symbol -> found. Each
    probe is (file in the kernel source, text to find, symbol)."""
    found = {}
    for path, text, symbol in probes:
        with open(os.path.join(kernel_source, path), errors='replace') as stream:
            found[symbol] = text in stream.read()
    return found


def feature_header(results):
    """The #define lines for the features found, in the order the probes were declared."""
    return ''.join('#define %s (1)\n' % symbol for symbol, found in results.items() if found)

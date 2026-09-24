"""Review the selected rebuilt stock module set; never claim runtime acceptance."""
from collections import defaultdict
from graphlib import TopologicalSorter, CycleError
from pathlib import Path


def name(value):
    return Path(value).name.removesuffix('.ko').replace('-', '_')


def review(modules, symbols, selected, *, protection=None, signed=()):
    issues = []
    by_name = {}
    for module in modules:
        n = name(module['path'])
        if n in by_name:
            raise ValueError('ambiguous module basename: ' + n)
        if module['metadata'].get('name') != [n]:
            raise ValueError('module filename/metadata disagreement: ' + n)
        by_name[n] = module
    selected = {name(n) for n in selected}
    missing = sorted(selected - by_name.keys())
    if missing:
        return {'status': 'FAIL', 'issues': [{'missing_modules': missing}]}
    versions = {v for n in selected for v in by_name[n]['metadata'].get('vermagic', [])}
    if len(versions) != 1 or not all(len(by_name[n]['metadata'].get('vermagic', [])) == 1 for n in selected):
        issues.append({'check': 'vermagic', 'values': sorted(versions)})
    providers = defaultdict(set)
    for symbol in symbols:
        owner = name(symbol['owner'])
        # Only the chosen common GKI supplies built-ins; a vendor compilation's
        # vmlinux is not a substitute provider for the Image being packaged.
        if owner == 'vmlinux':
            if not symbol['table'].endswith('/common/kernel_aarch64/vmlinux.symvers'):
                continue
        elif owner not in selected:
            continue
        providers[symbol['symbol']].add((symbol['crc'], owner, symbol['namespace'], symbol['export']))
    signed = {name(n) for n in signed}
    for symbol, records in providers.items():
        if len({r[1] for r in records}) > 1:
            issues.append({'symbol': symbol, 'check': 'duplicate-selected-export'})
        if protection and protection['gki_unprotected_symbols']:
            for _, owner, _, _ in records:
                if owner != 'vmlinux' and owner not in signed and symbol in protection['gki_protected_exports_symbols']:
                    issues.append({'module': owner, 'symbol': symbol, 'check': 'unsigned-protected-export'})
    graph = {n: set() for n in selected}
    firmware = {}
    required_count = 0
    for n in sorted(selected):
        m = by_name[n]
        imported = set(m['metadata'].get('import_ns', []))
        dependencies = {name(d) for value in m['metadata'].get('depends', []) for d in value.split(',') if d}
        absent = dependencies - selected
        if absent:
            issues.append({'module': n, 'missing_dependencies': sorted(absent)})
        graph[n].update(dependencies & selected)
        firmware[n] = sorted(set(m['metadata'].get('firmware', [])))
        for requirement in m['required_symbols']:
            required_count += 1
            found = providers.get(requirement['symbol'], set())
            error = None
            if not found:
                error = 'missing-selected-provider'
            elif len(found) != 1:
                error = 'ambiguous-selected-provider'
            else:
                crc, owner, namespace, export = next(iter(found))
                if crc != requirement['crc']:
                    error = 'crc-mismatch'
                elif namespace and namespace not in imported:
                    error = 'missing-namespace-import'
                elif export.endswith('_GPL') and not any(l in ('GPL', 'GPL v2', 'GPL and additional rights', 'Dual BSD/GPL', 'Dual MIT/GPL', 'Dual MPL/GPL') for l in m['metadata'].get('license', [])):
                    error = 'gpl-only-export-with-incompatible-module-license'
                if (protection and protection['gki_unprotected_symbols'] and n not in signed
                        and owner in signed and requirement['symbol'] not in protection['gki_unprotected_symbols']):
                    error = 'unsigned-access-to-protected-signed-module-symbol'
                if owner != 'vmlinux' and owner != n:
                    graph[n].add(owner)
            if error:
                issues.append({'module': n, 'symbol': requirement['symbol'], 'check': error})
    try:
        order = list(TopologicalSorter({n: sorted(graph[n]) for n in sorted(graph)}).static_order())
    except CycleError as exc:
        order = []
        issues.append({'check': 'dependency-cycle', 'cycle': exc.args[1]})
    return {'status': 'FAIL' if issues else 'PASS', 'issues': issues,
            'selected_module_count': len(selected), 'required_symbol_count': required_count,
            'vermagic': sorted(versions), 'dependency_order': order,
            'dependencies': {n: sorted(graph[n]) for n in sorted(graph)},
            'declared_firmware': {n: firmware[n] for n in sorted(firmware) if firmware[n]}}



def read_symbol_tables(outputs):
    """Read declared core and target-prefixed external Module.symvers outputs."""
    symbols = []
    for p in sorted(set(outputs)):
        if not p.name.endswith(('Module.symvers', 'vmlinux.symvers')):
            continue
        for line in p.read_text().splitlines():
            fields = line.split()
            if not 4 <= len(fields) <= 5:
                raise ValueError('invalid symbol table row')
            symbols.append(dict(crc=int(fields[0],16), symbol=fields[1], owner=fields[2],
                                export=fields[3], namespace=fields[4] if len(fields)==5 else '', table=str(p)))
    return symbols

def verify_built(work, run, selected, outputs, vmlinux, metadata, call, require):
    """Bind selected module CRCs/namespaces and signatures to the built GKI ELF."""
    import re
    import tempfile
    from . import process
    from .vendor import encoded
    from .vendor_extract import sha

    def binary(args):
        r = process.run(list(map(str, args)), 120, max_output_bytes=64*1024*1024, cwd=work)
        require(r['transport'] == 'ok', 'kernel verification command failed')
        return r['stdout']
    symbols = read_symbol_tables(outputs)
    nm = call(['nm', '-S', '--defined-only', vmlinux], cwd=work)
    sections = call(['readelf', '-SW', vmlinux], cwd=work)
    header = call(['readelf', '-h', vmlinux], cwd=work)
    require('ELF64' in header and 'little endian' in header and 'AArch64' in header, 'unexpected GKI ELF format')
    def symbol(label, optional=False):
        found = [l.split() for l in nm.splitlines() if l.split()[-1:] == [label]]
        if optional and not found:
            return None
        require(len(found) == 1, 'missing/ambiguous GKI symbol: ' + label)
        return found[0]
    def data(address, size):
        require(0 < size <= 1024*1024, 'invalid GKI symbol-data size')
        offsets = []
        for line in sections.splitlines():
            m = re.match(r'\s*\[\s*\d+\]\s+\S+\s+(\S+)\s+([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s', line)
            if m:
                kind, start, offset, length = m.groups()
                start, offset, length = int(start,16), int(offset,16), int(length,16)
                if kind != 'NOBITS' and start <= address and address+size <= start+length:
                    offsets.append(offset+address-start)
        require(len(offsets) == 1, 'GKI symbol data does not fit a unique section')
        with vmlinux.open('rb') as stream:
            stream.seek(offsets[0]); value = stream.read(size)
        require(len(value) == size, 'truncated GKI symbol data')
        return value
    size = int.from_bytes(data(int(symbol('system_certificate_list_size')[0],16),8),'little')
    cert = data(int(symbol('system_certificate_list')[0],16),size)
    der = run / 'builtin-cert.der'; der.write_bytes(cert)
    require(binary(['openssl','x509','-inform','DER','-in',der,'-outform','DER']) == cert, 'expected one exact built-in certificate')
    pem = run / 'builtin-cert.pem'; pem.write_bytes(binary(['openssl','x509','-inform','DER','-in',der]))
    # GKI module protection (MODULE_SIG_PROTECT) lets unsigned vendor modules
    # load with restricted symbol access. Kernels that force signatures
    # instead (GrapheneOS) have no protection arrays; then every module
    # must carry a signature from the built-in certificate.
    rows = {label: symbol(label, optional=True)
            for label in ('gki_unprotected_symbols','gki_protected_exports_symbols')}
    require(len({row is None for row in rows.values()}) == 1, 'partial GKI protection arrays')
    protection = None
    if all(rows.values()):
        protection = {}
        for label, row in rows.items():
            require(len(row) == 4, 'missing sized GKI protection array')
            protection[label] = [v.decode('ascii') for v in data(int(row[0],16),int(row[1],16)).split(b'\0') if v]
            require(protection[label] == sorted(set(protection[label])), 'noncanonical GKI protection array')
    modules, signed = [], []
    for filename, p in sorted(selected.items()):
        info = {}
        for k,v in metadata(p): info.setdefault(k.decode(),[]).append(v.decode())
        required = []
        for line in call(['modprobe','--dump-modversions',p],cwd=work).splitlines():
            fields = line.split(); require(len(fields) == 2, 'invalid module CRC row')
            required.append(dict(crc=int(fields[0],16),symbol=fields[1]))
        modules.append(dict(path=filename,metadata=info,required_symbols=required,sha256=sha(p)))
        if any(info.get('signer',[])):
            with tempfile.TemporaryDirectory(dir=run) as temp:
                temp = Path(temp); payload = temp/'payload'; signature = temp/'signature'
                extractor = work/'common/scripts/extract-module-sig.pl'
                payload.write_bytes(binary(['perl',extractor,'-0',p]))
                signature.write_bytes(binary(['perl',extractor,'-s',p]))
                binary(['openssl','cms','-verify','-binary','-inform','DER','-in',signature,
                        '-content',payload,'-certfile',pem,'-nointern','-noverify','-out','/dev/null'])
            signed.append(filename)
    if protection is None:
        require(sorted(signed) == sorted(selected), 'unsigned module with forced module signatures')
    result = review(modules, symbols, selected, protection=protection, signed=signed)
    result.update(builtin_certificate_sha256=sha(der),vmlinux_sha256=sha(vmlinux),signed_module_count=len(signed),
                  module_protection='gki-protected-exports' if protection else 'forced-signatures')
    (run/'module-interfaces.json').write_bytes(encoded(result))
    require(result['status'] == 'PASS', 'selected module CRC, namespace or protection check failed')
    return result

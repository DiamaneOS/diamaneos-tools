"""Read suite configuration metadata with bounded literal internal entities.

Tradefed configuration files can declare directory/command constants in a DTD.
No external resource, parameter entity or nested entity expansion is allowed.
This parser does not execute or rewrite configuration commands.
"""
from xml.parsers import expat


class SuiteXmlError(ValueError):
    pass


def parse_config(data: bytes) -> list[str]:
    if len(data) > 16 * 1024**2:
        raise SuiteXmlError('suite configuration exceeds byte limit')
    parser = expat.ParserCreate()
    depth = nodes = expanded = entity_bytes = entity_count = 0
    runners = []

    def reject(*_args):
        raise SuiteXmlError('unsupported suite XML declaration')

    def doctype(name, system_id, public_id, _internal):
        if name != 'configuration' or system_id or public_id:
            reject()

    def entity(_name, parameter, value, _base, system_id, public_id, notation):
        nonlocal entity_bytes, entity_count
        if parameter or value is None or system_id or public_id or notation:
            reject()
        # Reject references at declaration time, before expansion can grow.
        if '&' in value or '%' in value:
            reject()
        entity_count += 1
        entity_bytes += len(value.encode('utf-8'))
        if entity_count > 32 or entity_bytes > 32768:
            raise SuiteXmlError('suite XML entities exceed limits')

    def count(size):
        nonlocal expanded
        expanded += size
        if expanded > 32 * 1024**2:
            raise SuiteXmlError('expanded suite XML exceeds limit')

    def start(name, attrs):
        nonlocal depth, nodes
        depth += 1
        nodes += 1
        if depth > 64 or nodes > 200000:
            raise SuiteXmlError('suite XML structure exceeds limits')
        if depth == 1 and name != 'configuration':
            raise SuiteXmlError('suite XML root is not configuration')
        count(sum(len(k.encode('utf-8')) + len(v.encode('utf-8')) for k, v in attrs.items()))
        if depth == 2 and name == 'test':
            if not attrs.get('class'):
                raise SuiteXmlError('suite test runner lacks a class')
            runners.append(attrs['class'])

    def end(_name):
        nonlocal depth
        depth -= 1

    parser.StartDoctypeDeclHandler = doctype
    parser.EntityDeclHandler = entity
    parser.ExternalEntityRefHandler = reject
    parser.SkippedEntityHandler = reject
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = lambda value: count(len(value.encode('utf-8')))
    try:
        parser.Parse(data, True)
    except expat.ExpatError:
        raise SuiteXmlError('invalid suite XML') from None
    if not nodes:
        raise SuiteXmlError('empty suite XML')
    return runners

import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from diamaneos_tools import kernel, kernel_dt as dt

ROOT = Path(__file__).resolve().parents[2]
cells = lambda *v: struct.pack('>%dI' % len(v), *v)


def tree():
    root = dt.Node('')
    root.props = [('#address-cells', cells(2)), ('#size-cells', cells(2))]
    soc = dt.Node('soc')
    soc.props = [('#address-cells', cells(1)), ('#size-cells', cells(1))]
    soc.children = [dt.Node('plain'), dt.Node('named')]
    soc.children[1].props = [('memory-region', cells(1, 2, 3)), ('bits', cells(0, 0))]
    reserved = dt.Node('reserved-memory')
    reserved.props = [('#address-cells', cells(2)), ('#size-cells', cells(2))]
    region = dt.Node('region')
    region.props = [('size', cells(0, 4096)), ('compatible', b'ramoops\0')]
    reserved.children = [region]
    root.children = [soc, reserved]
    return dt.Tree(root, [(0x1000, 0x2000)], 0)


def blob(nodes):
    """A device tree blob from {path: {property: bytes}}, parents made as needed."""
    root = dt.Node('')
    for path, props in sorted(nodes.items()):
        node = root
        for part in [p for p in path.split('/') if p]:
            child = node.child(part)
            if child is None:
                child = dt.Node(part)
                node.children.append(child)
            node = child
        node.props = list(props.items())
    return dt.serialize(dt.Tree(root, [], 0))


def table(blobs):
    """A dtbo.img holding these overlays."""
    first = 32 + 32 * len(blobs)
    offsets = [first + sum(map(len, blobs[:i])) for i in range(len(blobs))]
    header = struct.pack('>8I', dt.DTBO_MAGIC, first + sum(map(len, blobs)), 32, 32, len(blobs), 32, 4096, 0)
    entries = b''.join(struct.pack('>8I', len(b), o, 0, 0, 0, 0, 0, 0) for b, o in zip(blobs, offsets))
    return header + entries + b''.join(blobs)


class DisabledNodeTests(unittest.TestCase):
    RULES = [{'compatible': 'qcom,qcedev', 'reason': 'r'}, {'compatible': 'rtc6226', 'reason': 'r'}]
    VOLCANO, CLIFFS = cells(0x27c, 0x10000), cells(0x266, 0x10000, 0x278, 0x10000)

    def base(self, status=b'disabled\0', **more):
        crypto = {'compatible': b'qcom,qcedev\0', **({'status': status} if status else {})}
        nodes = {'/': {'qcom,msm-id': self.VOLCANO}, '/soc': {}, '/soc/qcedev@1': crypto,
                 '/soc/sdhc': {'compatible': b'qcom,sdhci\0', 'status': b'disabled\0', 'phandle': cells(7)},
                 '/__symbols__': {'sdhc': b'/soc/sdhc\0'}}
        return blob({**nodes, **more})

    def overlay(self, soc=VOLCANO, status=b'disabled\0', target=None, body=None):
        """One fragment with a new FM node, or with properties for a node of the base."""
        nodes = {'/': {'qcom,msm-id': soc} if soc else {}, '/fragment@0': dict(target or {}), '/fragment@0/__overlay__': dict(body or {})}
        if target is None:
            nodes['/fragment@0/__overlay__/nq@64'] = {'compatible': b'vendor,fm\0rtc6226\0', 'status': status}
            nodes['/fragment@0'] = {'target-path': b'/soc\0'}
        return blob(nodes)

    def check(self, base=None, overlays=()):
        return dt.check_disabled_nodes(base or self.base(), list(overlays), self.RULES)

    def test_a_listed_node_must_be_disabled_in_the_base(self):
        self.assertEqual([], self.check())
        self.assertEqual([], self.check(self.base(status=b'fail\0')))
        for status in (b'okay\0', None):
            with self.subTest(status=status):
                self.assertEqual(['/soc/qcedev@1 in the device tree'], self.check(self.base(status=status)))

    def test_a_new_node_in_an_overlay_for_the_base_soc_must_be_disabled(self):
        self.assertEqual([], self.check(overlays=[self.overlay()]))
        self.assertEqual(['/fragment@0/__overlay__/nq@64 in overlay 1'],
                         self.check(overlays=[self.overlay(), self.overlay(status=b'okay\0')]))
        # An overlay with no SoC list applies to every SoC.
        self.assertEqual(['/fragment@0/__overlay__/nq@64 in overlay 0'], self.check(overlays=[self.overlay(soc=None, status=b'ok\0')]))

    def test_an_overlay_for_another_soc_is_not_applied_and_is_not_checked(self):
        self.assertEqual([], self.check(overlays=[self.overlay(soc=self.CLIFFS, status=b'okay\0')]))

    def test_an_overlay_may_not_enable_a_listed_node_of_the_base_it_targets(self):
        enable = {'status': b'okay\0'}
        crypto = {'/fragment@0/__overlay__/qcedev@1': enable}
        for how, fragment in (('path', {'target-path': b'/soc\0'}), ('phandle', {'target': cells(9)})):
            with self.subTest(how=how):
                base = self.base(**{'/soc': {'phandle': cells(9)}}) if how == 'phandle' else self.base()
                overlay = blob({'/': {}, '/fragment@0': fragment, **crypto})
                self.assertEqual(['/soc/qcedev@1 enabled by overlay 0'], self.check(base, [overlay]))
        # By symbol: the overlay's placeholder target and its __fixups__ entry name a label of the base.
        labelled = blob({'/': {}, '/fragment@0': {'target': cells(0xffffffff)}, '/fragment@0/__overlay__': enable,
                         '/__fixups__': {'sdhc': b'/fragment@0:target:0\0'}})
        base = self.base(**{'/soc/sdhc': {'compatible': b'qcom,qcedev\0', 'status': b'disabled\0'}})
        self.assertEqual(['/soc/sdhc enabled by overlay 0'], self.check(base, [labelled]))

    def test_an_overlay_that_disables_or_touches_other_nodes_passes(self):
        path = {'target-path': b'/soc\0'}
        disable = blob({'/': {}, '/fragment@0': path, '/fragment@0/__overlay__/qcedev@1': {'status': b'disabled\0'}})
        other = blob({'/': {}, '/fragment@0': path, '/fragment@0/__overlay__/sdhc': {'status': b'okay\0'}})
        untouched = blob({'/': {}, '/fragment@0': {'target-path': b'/soc/qcedev@1\0'}, '/fragment@0/__overlay__': {'x': b'1'}})
        unresolved = blob({'/': {}, '/fragment@0': {'target': cells(0xffffffff)},
                           '/fragment@0/__overlay__/qcedev@1': {'status': b'okay\0'}})
        self.assertEqual([], self.check(overlays=[disable, other, untouched, unresolved]))

    def test_the_overlay_image_is_read_entry_by_entry(self):
        blobs = [self.overlay(), self.overlay(status=b'okay\0')]
        self.assertEqual(blobs, dt.overlays(table(blobs)))
        self.assertEqual(2, dt.overlay_count(table(blobs)))
        for bad in (b'', table(blobs)[:-5], bytes(64)):
            with self.subTest(size=len(bad)):
                self.assertRaises(dt.DeviceTreeError, dt.overlays, bad)

    def test_the_shipped_rules_name_the_nodes_the_deep_sleep_work_found_and_the_crypto_engine(self):
        plan = kernel.validate_dt_plan(json.loads((ROOT / 'config/fp6-kernel-dt.json').read_text()))
        self.assertEqual(['qcom,msm-eud', 'qcom,volcano-debugcc', 'rtc6226', 'qcom,qcedev'], [r['compatible'] for r in plan['disabled_nodes']])
        # The crypto engine fix-up is what keeps the last rule satisfied.
        engine = {'/soc/qcedev@1de0000': {'compatible': b'qcom,qcedev\0'}}
        before = blob({'/': {'qcom,msm-id': self.VOLCANO}, '/soc': {}, **engine})
        self.assertEqual(['/soc/qcedev@1de0000 in the device tree'], dt.check_disabled_nodes(before, [], plan['disabled_nodes']))
        fixups = [f for f in plan['fixups'] if f['id'] == 'crypto-engine-off']
        self.assertEqual([], dt.check_disabled_nodes(dt.apply_fixups(before, fixups)[0], [], plan['disabled_nodes']))
        for rows in ([], [{'compatible': 'x'}], [{'compatible': 'a b', 'reason': 'r'}], [{'compatible': 'a', 'reason': 'r'}] * 2):
            with self.subTest(rows=rows):
                self.assertRaisesRegex(kernel.KernelError, 'invalid disabled device tree nodes', kernel.validate_dt_plan,
                                       dict(plan, disabled_nodes=rows))


class DeviceTreeTests(unittest.TestCase):
    def blob(self):
        return dt.serialize(tree())

    def test_round_trip_keeps_nodes_properties_and_reservations(self):
        first = dt.parse(self.blob())
        self.assertEqual([(0x1000, 0x2000)], first.reserved)
        self.assertEqual(self.blob(), dt.serialize(first))
        self.assertEqual(cells(1, 2, 3), dt.find(first.root, '/soc/named').prop('memory-region'))
        self.assertEqual([], dt.differences(first, dt.parse(self.blob()))[0])

    def test_rejects_a_blob_that_is_not_a_device_tree(self):
        for data in (b'', b'x' * 64, self.blob()[:-4], self.blob() + b'\0'):
            with self.subTest(size=len(data)):
                self.assertRaises(dt.DeviceTreeError, dt.parse, data)

    def test_fixups_edit_exactly_the_declared_properties(self):
        fixups = [{'id': 'a', 'reason': 'test', 'edits': [
            {'node': '/soc/named', 'property': 'memory-region', 'expect': {'cells': [1, 2, 3]}, 'set': {'cells': [1, 3]}},
            {'node': '/soc/named', 'property': 'bits', 'expect': {'cells': [0, 0]}, 'set': None},
            {'node': '/soc/named', 'property': 'reg-names', 'expect': None, 'set': {'strings': ['tx', 'rx']}},
            {'node': '/soc/plain', 'inherit_cells': [1, 1]},
            {'node': '/reserved-memory', 'property': '#size-cells', 'expect': {'cells': [2]}, 'set': {'cells': [2]}}]}]
        out, report = dt.apply_fixups(self.blob(), fixups)
        fixed = dt.parse(out)
        named = dt.find(fixed.root, '/soc/named')
        self.assertEqual(cells(1, 3), named.prop('memory-region'))
        self.assertIsNone(named.prop('bits'))
        self.assertEqual(b'tx\0rx\0', named.prop('reg-names'))
        self.assertEqual(cells(1), dt.find(fixed.root, '/soc/plain').prop('#address-cells'))
        self.assertEqual(['memory-region', 'bits', 'reg-names', '#address-cells', '#size-cells'],
                         [c['property'] for c in report[0]['changes']])
        # A new property goes first in its node, as libfdt adds it.
        self.assertEqual('#size-cells', dt.find(fixed.root, '/soc/plain').props[0][0])

    def test_a_forbidden_property_anywhere_in_the_result_stops_the_build(self):
        edit = {'node': '/soc/plain', 'property': 'qcom,disable-lpm', 'expect': None, 'set': {'cells': [1]}}
        fixup = {'id': 'a', 'reason': 'test', 'edits': [edit]}
        dt.apply_fixups(self.blob(), [fixup])
        self.assertRaisesRegex(dt.DeviceTreeError, 'forbidden property: /soc/plain qcom,disable-lpm', dt.apply_fixups,
                               self.blob(), [fixup], ['qcom,disable-lpm'])
        # A base that already carries it fails the same way, with or without an edit of it.
        base = tree()
        dt.find(base.root, '/soc/named').props.append(('qcom,disable-lpm', b''))
        other = {'id': 'b', 'reason': 'test', 'edits': [{'node': '/soc/plain', 'inherit_cells': [1, 1]}]}
        self.assertRaisesRegex(dt.DeviceTreeError, 'forbidden property', dt.apply_fixups, dt.serialize(base), [other],
                               ['qcom,disable-lpm'])

    def test_an_edit_that_finds_another_value_stops_the_build(self):
        edit = {'node': '/soc/named', 'property': 'memory-region', 'expect': {'cells': [9]}, 'set': {'cells': [1]}}
        with self.assertRaisesRegex(dt.DeviceTreeError, 'unexpected memory-region'):
            dt.apply_fixups(self.blob(), [{'id': 'a', 'reason': 'test', 'edits': [edit]}])

    def test_inherited_cells_must_match_what_the_walk_finds(self):
        edit = {'node': '/soc/plain', 'inherit_cells': [2, 1]}
        with self.assertRaisesRegex(dt.DeviceTreeError, 'resolve to another value'):
            dt.apply_fixups(self.blob(), [{'id': 'a', 'reason': 'test', 'edits': [edit]}])

    def test_malformed_fixups_are_refused(self):
        for fixup in ({'id': 'a', 'reason': '', 'edits': [{'node': '/soc', 'inherit_cells': [1, 1]}]},
                      {'id': 'a', 'reason': 'x', 'edits': []},
                      {'id': 'a', 'reason': 'x', 'edits': [{'node': '/nowhere', 'inherit_cells': [1, 1]}]}):
            with self.subTest(fixup=fixup):
                self.assertRaises(dt.DeviceTreeError, dt.apply_fixups, self.blob(), [fixup])

    def test_base_files_must_match_their_pins(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / 'dtbs').mkdir()
            (Path(temp) / 'dtbs/fp6.dtb').write_bytes(b'a')
            (Path(temp) / 'dtbo.img').write_bytes(b'b')
            spec = {'files': {'dtbs/fp6.dtb': hashlib.sha256(b'a').hexdigest(), 'dtbo.img': hashlib.sha256(b'b').hexdigest()}}
            self.assertEqual({'dtbs/fp6.dtb': b'a', 'dtbo.img': b'b'}, dt.base_files(Path(temp), spec))
            (Path(temp) / 'dtbo.img').write_bytes(b'c')
            self.assertRaisesRegex(dt.DeviceTreeError, 'differs from its pin', dt.base_files, Path(temp), spec)

    def test_overlay_count_reads_the_table_header(self):
        header = struct.pack('>8I', dt.DTBO_MAGIC, 32 + 95 * 32, 32, 32, 95, 32, 4096, 0) + bytes(95 * 32)
        self.assertEqual(95, dt.overlay_count(header))
        self.assertRaises(dt.DeviceTreeError, dt.overlay_count, bytes(64))
        # A header that counts more entries than the image holds is refused.
        self.assertRaisesRegex(dt.DeviceTreeError, 'invalid overlay image table', dt.overlay_count,
                               struct.pack('>8I', dt.DTBO_MAGIC, 64, 32, 32, 95, 32, 4096, 0) + bytes(32))

    def sdhc_blob(self, compatible=b'qcom,sdhci-msm-v5\0', reg=(0x8804000, 0x1000), phandles=(0x286, 0x28f), extra=()):
        """The nodes the shipped sdhc-supplies fix-up asserts, under their real paths."""
        def make(path, props):
            node = root
            for name in path.strip('/').split('/'):
                found = node.child(name)
                if found is None:
                    found = dt.Node(name)
                    node.children.append(found)
                node = found
            node.props = props
        root = dt.Node('')
        make('/soc/sdhci@4784000', [('compatible', compatible), ('reg', cells(*reg)), *extra])
        make('/soc/rsc@17a00000/drv@2/rpmh-regulator-ldob13/regulator-pm7550-l13', [('phandle', cells(phandles[0]))])
        make('/soc/rsc@17a00000/drv@2/rpmh-regulator-ldob23/regulator-pm7550-l23', [('phandle', cells(phandles[1]))])
        return dt.serialize(dt.Tree(root, [], 0))

    def test_the_shipped_sdhc_fixup_adds_the_two_supplies_and_asserts_what_it_relies_on(self):
        plan = kernel.validate_dt_plan(json.loads((ROOT / 'config/fp6-kernel-dt.json').read_text()))
        fixups = [f for f in plan['fixups'] if f['id'] == 'sdhc-supplies']
        out, report = dt.apply_fixups(self.sdhc_blob(), fixups)
        card = dt.find(dt.parse(out).root, '/soc/sdhci@4784000')
        self.assertEqual(cells(0x286), card.prop('vmmc-supply'))
        self.assertEqual(cells(0x28f), card.prop('vqmmc-supply'))
        # A new property goes first, so the second one added is the first in the node.
        self.assertEqual(['vqmmc-supply', 'vmmc-supply'], [name for name, _ in card.props[:2]])
        self.assertEqual([('/soc/sdhci@4784000', 'vmmc-supply', 'absent', '<0x286>'),
                          ('/soc/sdhci@4784000', 'vqmmc-supply', 'absent', '<0x28f>')],
                         [(c['node'], c['property'], c['before'], c['after']) for c in report[0]['changes']])
        for what, blob in (('compatible', self.sdhc_blob(compatible=b'qcom,sdhci-msm-v4\0')),
                           ('reg', self.sdhc_blob(reg=(0x8804000, 0x2000))),
                           ('l13 phandle', self.sdhc_blob(phandles=(0x287, 0x28f))),
                           ('l23 phandle', self.sdhc_blob(phandles=(0x286, 0x290))),
                           ('vmmc-supply present', self.sdhc_blob(extra=[('vmmc-supply', cells(1))])),
                           ('vqmmc-supply present', self.sdhc_blob(extra=[('vqmmc-supply', cells(1))]))):
            with self.subTest(changed=what):
                self.assertRaisesRegex(dt.DeviceTreeError, 'unexpected', dt.apply_fixups, blob, fixups)

    def test_the_shipped_plan_is_well_formed(self):
        plan = kernel.validate_dt_plan(json.loads((ROOT / 'config/fp6-kernel-dt.json').read_text()))
        self.assertEqual(['qcom,disable-lpm'], plan['forbidden_properties'])
        ids = [f['id'] for f in plan['fixups']]
        self.assertEqual(['nvmem-modem-cpucp', 'explicit-cells', 'static-ramoops', 'crypto-engine-off', 'sdhc-supplies'], ids)
        for fixup in plan['fixups']:
            self.assertTrue(fixup['reason'].strip())
            for edit in fixup['edits']:
                if 'inherit_cells' in edit:
                    self.assertEqual(2, len(edit['inherit_cells']))
                else:
                    for key in ('expect', 'set'):
                        if edit[key] is not None:
                            dt.encode(edit[key])

    def test_a_plan_with_a_missing_or_an_extra_key_is_refused(self):
        plan = json.loads((ROOT / 'config/fp6-kernel-dt.json').read_text())
        for name in plan:
            with self.subTest(missing=name):
                self.assertRaisesRegex(kernel.KernelError, 'invalid device tree plan', kernel.validate_dt_plan,
                                       {k: v for k, v in plan.items() if k != name})
        self.assertRaisesRegex(kernel.KernelError, 'invalid device tree plan', kernel.validate_dt_plan, dict(plan, extra=1))


if __name__ == '__main__':
    unittest.main()

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
        header = struct.pack('>8I', dt.DTBO_MAGIC, 64, 32, 32, 95, 32, 4096, 0) + bytes(32)
        self.assertEqual(95, dt.overlay_count(header))
        self.assertRaises(dt.DeviceTreeError, dt.overlay_count, bytes(64))

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

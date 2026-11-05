import json
from pathlib import Path
import struct
import sys
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
        self.assertEqual([], self.check(overlays=[disable, other, untouched]))

    def test_an_unresolved_target_in_an_overlay_that_applies_fails_because_the_check_could_not_see_it(self):
        placeholder = {'target': cells(0xffffffff)}
        for how, fragment, extra in (('no symbol', placeholder, {}),
                                     ('no such label', placeholder, {'/__fixups__': {'ghost': b'/fragment@0:target:0\0'}}),
                                     ('no such phandle', {'target': cells(77)}, {}),
                                     ('no such path', {'target-path': b'/nowhere\0'}, {})):
            overlay = blob({'/': {'qcom,msm-id': self.VOLCANO}, '/fragment@0': fragment,
                            '/fragment@0/__overlay__/qcedev@1': {'status': b'okay\0'}, **extra})
            with self.subTest(how=how):
                self.assertEqual(['/fragment@0 of overlay 0 has a target the device tree does not resolve'],
                                 self.check(overlays=[overlay]))
        # Another SoC's overlay is not applied, so its targets do not matter.
        other = blob({'/': {'qcom,msm-id': self.CLIFFS}, '/fragment@0': placeholder, '/fragment@0/__overlay__': {}})
        self.assertEqual([], self.check(overlays=[other]))

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
        # The disabled crypto engine node is what keeps the last rule satisfied.
        engine = {'compatible': b'qcom,qcedev\0'}
        before = blob({'/': {'qcom,msm-id': self.VOLCANO}, '/soc': {}, '/soc/qcedev@1de0000': engine})
        after = blob({'/': {'qcom,msm-id': self.VOLCANO}, '/soc': {}, '/soc/qcedev@1de0000': {**engine, 'status': b'disabled\0'}})
        self.assertEqual(['/soc/qcedev@1de0000 in the device tree'], dt.check_disabled_nodes(before, [], plan['disabled_nodes']))
        self.assertEqual([], dt.check_disabled_nodes(after, [], plan['disabled_nodes']))
        for rows in ([], [{'compatible': 'x'}], [{'compatible': 'a b', 'reason': 'r'}], [{'compatible': 'a', 'reason': 'r'}] * 2):
            with self.subTest(rows=rows):
                self.assertRaisesRegex(kernel.KernelError, 'invalid disabled device tree nodes', kernel.validate_dt_plan,
                                       dict(plan, disabled_nodes=rows))


class ForbiddenPropertyTests(unittest.TestCase):
    NAMES = ['qcom,disable-lpm']
    EXEMPT = [{'property': 'qcom,disable-lpm', 'overlay': 1, 'board_id': 15, 'reason': 'r'}]
    VOLCANO, CLIFFS = cells(0x27c, 0x10000), cells(0x266, 0x10000)

    def base(self):
        return blob({'/': {'qcom,msm-id': self.VOLCANO}, '/soc': {}})

    def overlay(self, soc=None, board=None, forbidden=True):
        soc = self.VOLCANO if soc is None else soc
        root = {'qcom,msm-id': soc, **({'qcom,board-id': cells(board, 0)} if board is not None else {})}
        return blob({'/': root, '/fragment@0': {'target-path': b'/soc\0'},
                     '/fragment@0/__overlay__': {'qcom,disable-lpm': b''} if forbidden else {}})

    def check(self, overlays, exemptions=()):
        return dt.check_forbidden_properties(self.base(), overlays, self.NAMES, exemptions)

    def test_a_forbidden_property_in_an_overlay_for_the_base_soc_fails(self):
        self.assertEqual([], self.check([self.overlay(forbidden=False)]))
        self.assertEqual(['/fragment@0/__overlay__ qcom,disable-lpm in overlay 1'],
                         self.check([self.overlay(forbidden=False), self.overlay()]))
        # An overlay for another SoC is not applied.
        self.assertEqual([], self.check([self.overlay(soc=self.CLIFFS)]))

    def test_a_forbidden_property_in_the_device_tree_fails(self):
        base = blob({'/': {'qcom,msm-id': self.VOLCANO}, '/soc/ufshc': {'qcom,disable-lpm': b''}})
        self.assertEqual(['/soc/ufshc qcom,disable-lpm in the device tree'], dt.check_forbidden_properties(base, [], self.NAMES))

    def test_an_exemption_names_the_overlay_and_the_board_id_that_justifies_it(self):
        overlays = [self.overlay(forbidden=False), self.overlay(board=15)]
        self.assertEqual([], self.check(overlays, self.EXEMPT))
        self.assertEqual(['overlay 1 is exempt for board id 15 and has another'],
                         self.check([self.overlay(forbidden=False), self.overlay(board=8)], self.EXEMPT))
        self.assertEqual(['overlay 1 is exempt for board id 15 and has another'],
                         self.check([self.overlay(forbidden=False), self.overlay()], self.EXEMPT))

    def test_an_exemption_that_is_not_needed_any_more_fails(self):
        self.assertEqual(['the exemption of qcom,disable-lpm in overlay 1 is no longer needed'],
                         self.check([self.overlay(forbidden=False), self.overlay(board=15, forbidden=False)], self.EXEMPT))
        self.assertEqual(['the exemption of qcom,disable-lpm in overlay 1 is no longer needed'],
                         self.check([self.overlay(forbidden=False)], self.EXEMPT))
        # An exempt overlay for another SoC is not applied and so the exemption is not needed either.
        self.assertEqual(['the exemption of qcom,disable-lpm in overlay 1 is no longer needed'],
                         self.check([self.overlay(forbidden=False), self.overlay(soc=self.CLIFFS, board=15)], self.EXEMPT))

    def test_the_shipped_exemption_is_the_volcano_emulator_overlay(self):
        plan = kernel.validate_dt_plan(json.loads((ROOT / 'config/fp6-kernel-dt.json').read_text()))
        self.assertEqual([('qcom,disable-lpm', 84, 15)],
                         [(r['property'], r['overlay'], r['board_id']) for r in plan['forbidden_property_exemptions']])
        for rows in ([{'property': 'qcom,other', 'overlay': 1, 'board_id': 1, 'reason': 'r'}],
                     [{'property': 'qcom,disable-lpm', 'overlay': -1, 'board_id': 1, 'reason': 'r'}],
                     [{'property': 'qcom,disable-lpm', 'overlay': 1, 'board_id': 'x', 'reason': 'r'}],
                     [{'property': 'qcom,disable-lpm', 'overlay': 1, 'board_id': 1, 'reason': ' '}],
                     self.EXEMPT * 2, [{'property': 'qcom,disable-lpm', 'overlay': 1}]):
            with self.subTest(rows=rows):
                self.assertRaisesRegex(kernel.KernelError, 'invalid forbidden property exemptions', kernel.validate_dt_plan,
                                       dict(plan, forbidden_property_exemptions=rows))


class DeviceTreeTests(unittest.TestCase):
    def blob(self):
        return dt.serialize(tree())

    def test_round_trip_keeps_nodes_properties_and_reservations(self):
        first = dt.parse(self.blob())
        self.assertEqual([(0x1000, 0x2000)], first.reserved)
        self.assertEqual(self.blob(), dt.serialize(first))
        self.assertEqual(cells(1, 2, 3), dt.find(first.root, '/soc/named').prop('memory-region'))

    def test_rejects_a_blob_that_is_not_a_device_tree(self):
        for data in (b'', b'x' * 64, self.blob()[:-4], self.blob() + b'\0'):
            with self.subTest(size=len(data)):
                self.assertRaises(dt.DeviceTreeError, dt.parse, data)

    def test_overlay_count_reads_the_table_header(self):
        header = struct.pack('>8I', dt.DTBO_MAGIC, 32 + 95 * 32, 32, 32, 95, 32, 4096, 0) + bytes(95 * 32)
        self.assertEqual(95, dt.overlay_count(header))
        self.assertRaises(dt.DeviceTreeError, dt.overlay_count, bytes(64))
        # A header that counts more entries than the image holds is refused.
        self.assertRaisesRegex(dt.DeviceTreeError, 'invalid overlay image table', dt.overlay_count,
                               struct.pack('>8I', dt.DTBO_MAGIC, 64, 32, 32, 95, 32, 4096, 0) + bytes(32))

    def test_the_shipped_plan_is_well_formed(self):
        plan = kernel.validate_dt_plan(json.loads((ROOT / 'config/fp6-kernel-dt.json').read_text()))
        self.assertEqual(['qcom,disable-lpm'], plan['forbidden_properties'])
        for bad in (None, {}, dict(plan['reference'], overlay_count=0), dict(plan['reference'], **{'dtbo.img': 'x'}),
                    dict(plan['reference'], extra=1)):
            with self.subTest(reference=bad):
                self.assertRaisesRegex(kernel.KernelError, 'invalid device tree reference', kernel.validate_dt_plan,
                                       dict(plan, reference=bad))

    def test_a_plan_with_a_missing_or_an_extra_key_is_refused(self):
        plan = json.loads((ROOT / 'config/fp6-kernel-dt.json').read_text())
        for name in plan:
            with self.subTest(missing=name):
                self.assertRaisesRegex(kernel.KernelError, 'invalid device tree plan', kernel.validate_dt_plan,
                                       {k: v for k, v in plan.items() if k != name})
        self.assertRaisesRegex(kernel.KernelError, 'invalid device tree plan', kernel.validate_dt_plan, dict(plan, extra=1))


if __name__ == '__main__':
    unittest.main()

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
        # The crypto engine difference is what keeps the last rule satisfied.
        engine = {'compatible': b'qcom,qcedev\0'}
        before = blob({'/': {'qcom,msm-id': self.VOLCANO}, '/soc': {}, '/soc/qcedev@1de0000': engine})
        after = blob({'/': {'qcom,msm-id': self.VOLCANO}, '/soc': {}, '/soc/qcedev@1de0000': {**engine, 'status': b'disabled\0'}})
        self.assertEqual(['/soc/qcedev@1de0000 in the device tree'], dt.check_disabled_nodes(before, [], plan['disabled_nodes']))
        self.assertEqual([], dt.check_disabled_nodes(after, [], plan['disabled_nodes']))
        crypto = [d for d in plan['differences'] if d['id'] == 'crypto-engine-off']
        self.assertEqual(1, len(dt.check_differences(before, after, crypto)[0]['changes']))
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
        self.assertEqual(([], []), dt.tree_differences(first, dt.parse(self.blob())))

    def test_rejects_a_blob_that_is_not_a_device_tree(self):
        for data in (b'', b'x' * 64, self.blob()[:-4], self.blob() + b'\0'):
            with self.subTest(size=len(data)):
                self.assertRaises(dt.DeviceTreeError, dt.parse, data)

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

    def test_the_shipped_plan_is_well_formed(self):
        plan = kernel.validate_dt_plan(json.loads((ROOT / 'config/fp6-kernel-dt.json').read_text()))
        self.assertEqual(['qcom,disable-lpm'], plan['forbidden_properties'])
        self.assertEqual(['nvmem-modem-cpucp', 'explicit-cells', 'static-ramoops', 'crypto-engine-off', 'sdhc-supplies',
                          'sdhc-voltages', 'usb-ipa-path'], [d['id'] for d in plan['differences']])
        anything = AnyPath()
        for edit in [e for d in plan['differences'] for e in d['edits']]:
            for key in ('base', 'source'):
                if edit.get(key) is not None:
                    dt.encode(edit[key], anything)
        for rows in ([], [{'id': 'a', 'reason': 'r', 'edits': []}], [{'id': 'a', 'reason': 'r', 'edits': [{'node': '/'}]}],
                     plan['differences'][:1] * 2):
            with self.subTest(rows=rows):
                self.assertRaisesRegex(kernel.KernelError, 'invalid device tree differences', kernel.validate_dt_plan,
                                       dict(plan, differences=rows))

    def test_a_plan_with_a_missing_or_an_extra_key_is_refused(self):
        plan = json.loads((ROOT / 'config/fp6-kernel-dt.json').read_text())
        for name in plan:
            with self.subTest(missing=name):
                self.assertRaisesRegex(kernel.KernelError, 'invalid device tree plan', kernel.validate_dt_plan,
                                       {k: v for k, v in plan.items() if k != name})
        self.assertRaisesRegex(kernel.KernelError, 'invalid device tree plan', kernel.validate_dt_plan, dict(plan, extra=1))


class AnyPath(dict):
    """A phandle for every node path."""
    def __contains__(self, key):
        return True

    def __getitem__(self, key):
        return 1


class DifferenceTests(unittest.TestCase):
    """The tree from source against the base, with phandles numbered differently in each."""

    def base(self, **more):
        return blob({'/': {}, '/soc': {'#address-cells': cells(1), '#size-cells': cells(1)},
                     '/soc/a': {'phandle': cells(5), 'reg-names': b'tx\0'}, '/soc/b': {'phandle': cells(6)},
                     '/soc/user': {'supply': cells(5), 'mode': cells(1)}, '/soc/plain': {}, **more})

    def source(self, **more):
        return blob({'/': {}, '/soc': {'#address-cells': cells(1), '#size-cells': cells(1)},
                     '/soc/a': {'phandle': cells(7), 'reg-names': b'tx\0'}, '/soc/b': {'phandle': cells(8)},
                     '/soc/user': {'supply': cells(7), 'mode': cells(1)}, '/soc/plain': {}, **more})

    def check(self, base, source, edits):
        return dt.check_differences(base, source, [{'id': 'x', 'reason': 'r', 'edits': edits}])

    def test_renumbered_phandles_are_no_difference(self):
        self.assertEqual(([], []), dt.tree_differences(dt.parse(self.base()), dt.parse(self.source())))
        moved = self.source(**{'/soc/user': {'supply': cells(8), 'mode': cells(1)}})
        self.assertEqual([('/soc/user', 'supply', cells(5), cells(8))], dt.tree_differences(dt.parse(self.base()), dt.parse(moved))[0])

    def test_declared_differences_pass_and_name_nodes_by_path_where_a_phandle_goes(self):
        source = self.source(**{'/soc/user': {'supply': cells(8), 'mode': cells(2)}, '/soc/plain': {'reg-names': b'rx\0'}})
        edits = [{'node': '/soc/user', 'property': 'supply', 'base': {'cells': [{'phandle': '/soc/a'}]},
                  'source': {'cells': [{'phandle': '/soc/b'}]}},
                 {'node': '/soc/user', 'property': 'mode', 'base': {'cells': [1]}, 'source': {'cells': [2]}},
                 {'node': '/soc/plain', 'property': 'reg-names', 'base': None, 'source': {'strings': ['rx']}}]
        report = self.check(self.base(), source, edits)
        self.assertEqual([('/soc/user', 'supply', '<0x5>', '<0x8>'), ('/soc/user', 'mode', '<0x1>', '<0x2>'),
                          ('/soc/plain', 'reg-names', 'absent', '"rx"')],
                         [(c['node'], c['property'], c['base'], c['source']) for c in report[0]['changes']])

    def test_an_undeclared_a_stale_or_another_difference_fails(self):
        changed = self.source(**{'/soc/user': {'supply': cells(7), 'mode': cells(2)}, '/soc/plain': {'x': b''}})
        mode = {'node': '/soc/user', 'property': 'mode', 'base': {'cells': [1]}, 'source': {'cells': [2]}}
        self.assertRaisesRegex(dt.DeviceTreeError, 'beyond the declared differences: /soc/plain x$', self.check,
                               self.base(), changed, [mode])
        changed = self.source(**{'/soc/user': {'supply': cells(7), 'mode': cells(2)}})
        self.assertRaisesRegex(dt.DeviceTreeError, 'no longer one', self.check, self.base(), self.source(), [mode])
        self.assertRaisesRegex(dt.DeviceTreeError, 'unexpected mode of /soc/user', self.check, self.base(), changed,
                               [dict(mode, source={'cells': [3]})])
        self.assertRaisesRegex(dt.DeviceTreeError, 'two edits', self.check, self.base(), changed, [mode, mode])

    def test_nodes_child_order_and_reservations_must_match(self):
        extra = self.source(**{'/soc/new': {}})
        self.assertRaisesRegex(dt.DeviceTreeError, 'node /soc/new is only in the source tree', self.check, self.base(), extra, [])
        swapped = dt.parse(self.source())
        soc = dt.find(swapped.root, '/soc')
        soc.children.reverse()
        self.assertRaisesRegex(dt.DeviceTreeError, 'children of /soc come in another order', dt.check_differences, self.base(),
                               dt.serialize(swapped), [{'id': 'x', 'reason': 'r', 'edits': []}])
        reserved = dt.parse(self.source())
        reserved.reserved = [(0x1000, 0x1000)]
        self.assertRaisesRegex(dt.DeviceTreeError, 'memory reservations differ', self.check, self.base(), dt.serialize(reserved), [])

    def test_inherited_cells_must_be_what_the_base_resolves(self):
        source = self.source(**{'/soc/plain': {'#address-cells': cells(1), '#size-cells': cells(1)}})
        report = self.check(self.base(), source, [{'node': '/soc/plain', 'inherit_cells': [1, 1]}])
        self.assertEqual(['#address-cells', '#size-cells'], [c['property'] for c in report[0]['changes']])
        self.assertRaisesRegex(dt.DeviceTreeError, 'resolve to another value', self.check, self.base(), source,
                               [{'node': '/soc/plain', 'inherit_cells': [2, 1]}])
        # A base that has one of them already only asserts it.
        base = self.base(**{'/soc/plain': {'#address-cells': cells(1)}})
        report = self.check(base, source, [{'node': '/soc/plain', 'inherit_cells': [1, 1]}])
        self.assertEqual(['#size-cells'], [c['property'] for c in report[0]['changes']])


if __name__ == '__main__':
    unittest.main()

import copy
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from diamaneos_tools import datad_selection as subject, devices, vendor_files, vendor_product
from diamaneos_tools.vendor import VendorError


def row(path, deps=(), notices=('n1',)):
    return {'path': path, 'input': path, 'sha256': '0' * 64, 'bytes': 1, 'dependencies': list(deps),
            'notices': list(notices)}


def stock_pair():
    """The shape of the real pair: nicmd and its closure, the two libraries DSI links, two more files."""
    files = [row(p) for p in subject.DROPPED]
    files += [row('vendor/lib64/libdsi_netctrl.so', ['vendor/lib64/libnicm.so', 'vendor/lib64/libnicm_dsi.so',
                                                      'vendor/lib64/libdsutils.so']),
              row('vendor/lib64/libdsutils.so', notices=('n2',)),
              row('vendor/lib64/libqcrilDataModule.so', ['vendor/lib64/libdsi_netctrl.so'])]
    recipe = {'files': files, 'notices': [{'sha256': 'n1'}, {'sha256': 'n2'}, {'sha256': 'n3'}], 'symlinks': []}
    elf = [{'path': p} for p in subject.DROPPED if p not in subject.NOT_ELF] + [
        {'path': 'vendor/lib64/libdsi_netctrl.so'}, {'path': 'vendor/lib64/libdsutils.so'},
        {'path': 'vendor/lib64/libqcrilDataModule.so'}]
    edge = lambda consumer, needed, provider: {'consumer': consumer, 'needed': needed, 'kind': 'selected-stock',
                                               'provider': provider}
    edges = [
        edge('vendor/bin/nicmd', 'libnicm.so', 'vendor/lib64/libnicm.so'),
        {'consumer': 'vendor/lib64/libnicm.so', 'needed': 'libc.so', 'kind': 'platform-or-vndk34'},
        edge('vendor/lib64/libnicm_dsi.so', 'libnicm.so', 'vendor/lib64/libnicm.so'),
        edge('vendor/lib64/libdsi_netctrl.so', 'libnicm.so', 'vendor/lib64/libnicm.so'),
        edge('vendor/lib64/libdsi_netctrl.so', 'libnicm_dsi.so', 'vendor/lib64/libnicm_dsi.so'),
        edge('vendor/lib64/libdsi_netctrl.so', 'libdsutils.so', 'vendor/lib64/libdsutils.so'),
        edge('vendor/lib64/libqcrilDataModule.so', 'libdsi_netctrl.so', 'vendor/lib64/libdsi_netctrl.so'),
    ]
    selection = {'files': elf, 'edges': edges, 'symlinks': [], 'firmware_inputs': [],
                 'roots': ['vendor/bin/nicmd', 'vendor/lib64/libqcrilDataModule.so']}
    return recipe, selection


class TransformTests(unittest.TestCase):
    def test_the_stock_files_leave_and_the_two_edges_become_source_modules(self):
        recipe, selection = stock_pair()
        r2, s2 = subject.transform(recipe, selection)
        self.assertEqual(['vendor/lib64/libdsi_netctrl.so', 'vendor/lib64/libdsutils.so',
                          'vendor/lib64/libqcrilDataModule.so'], sorted(r['path'] for r in r2['files']))
        self.assertNotIn('vendor/bin/nicmd', [r['path'] for r in s2['files']])
        self.assertEqual(['vendor/lib64/libqcrilDataModule.so'], s2['roots'])
        dsi = next(r for r in r2['files'] if r['path'] == subject.CONSUMER)
        self.assertEqual(['vendor/lib64/libdsutils.so'], dsi['dependencies'])
        converted = [e for e in s2['edges'] if e['kind'] == 'source-module']
        self.assertEqual([(subject.CONSUMER, 'libnicm.so'), (subject.CONSUMER, 'libnicm_dsi.so')],
                         sorted((e['consumer'], e['needed']) for e in converted))
        self.assertTrue(all('provider' not in e for e in converted))
        for e in s2['edges']:
            self.assertNotIn(e['consumer'], subject.DROPPED)
            self.assertNotIn(e.get('provider'), subject.DROPPED)
        self.assertEqual(4, len(s2['edges']))
        # a notice only the dropped files used goes
        self.assertEqual(['n1', 'n2'], sorted(n['sha256'] for n in r2['notices']))

    def test_the_arguments_are_not_changed(self):
        recipe, selection = stock_pair()
        before = copy.deepcopy((recipe, selection))
        subject.transform(recipe, selection)
        self.assertEqual(before, (recipe, selection))

    def test_a_missing_stock_file_stops_it(self):
        for victim in ('vendor/bin/nicmd', 'vendor/lib64/libshsc.so', 'vendor/etc/init/nicmd.rc'):
            recipe, selection = stock_pair()
            recipe['files'] = [r for r in recipe['files'] if r['path'] != victim]
            with self.subTest(victim), self.assertRaisesRegex(VendorError, 'changed'):
                subject.transform(recipe, selection)
        recipe, selection = stock_pair()
        selection['files'] = [r for r in selection['files'] if r['path'] != 'vendor/lib64/libnicm_utils.so']
        with self.assertRaisesRegex(VendorError, 'changed'):
            subject.transform(recipe, selection)

    def test_a_second_run_on_the_result_stops(self):
        with self.assertRaises(VendorError):
            subject.transform(*subject.transform(*stock_pair()))

    def test_another_file_that_needs_a_dropped_library_stops_it(self):
        recipe, selection = stock_pair()
        next(r for r in recipe['files'] if r['path'].endswith('libqcrilDataModule.so'))['dependencies'].append(
            'vendor/lib64/libnicm_utils.so')
        with self.assertRaisesRegex(VendorError, 'libqcrilDataModule.so needs'):
            subject.transform(recipe, selection)

    def test_the_consumer_must_link_both_libraries(self):
        recipe, selection = stock_pair()
        next(r for r in recipe['files'] if r['path'] == subject.CONSUMER)['dependencies'].remove(
            'vendor/lib64/libnicm_dsi.so')
        with self.assertRaisesRegex(VendorError, 'no longer links both'):
            subject.transform(recipe, selection)

    def test_an_edge_to_a_dropped_file_from_another_consumer_stops_it(self):
        recipe, selection = stock_pair()
        selection['edges'].append({'consumer': 'vendor/lib64/libqcrilDataModule.so', 'needed': 'libshsc.so',
                                   'kind': 'selected-stock', 'provider': 'vendor/lib64/libshsc.so'})
        with self.assertRaisesRegex(VendorError, 'links'):
            subject.transform(recipe, selection)

    def test_the_root_must_be_there_and_one_must_be_left(self):
        recipe, selection = stock_pair()
        selection['roots'] = ['vendor/lib64/libqcrilDataModule.so']
        with self.assertRaisesRegex(VendorError, 'not a root'):
            subject.transform(recipe, selection)
        recipe, selection = stock_pair()
        selection['roots'] = ['vendor/bin/nicmd']
        with self.assertRaisesRegex(VendorError, 'no root left'):
            subject.transform(recipe, selection)

    def test_a_symlink_to_a_dropped_file_stops_it(self):
        recipe, selection = stock_pair()
        recipe['symlinks'] = [{'path': 'vendor/lib64/other.so', 'target': 'vendor/lib64/libnicm.so'}]
        with self.assertRaisesRegex(VendorError, 'symlink'):
            subject.transform(recipe, selection)

    def test_unreachable_lists_what_only_the_dropped_files_used(self):
        recipe, selection = stock_pair()
        selection['files'].append({'path': 'vendor/lib64/libonlynicmd.so'})
        before = subject.unreachable(selection)
        selection['edges'].append({'consumer': 'vendor/bin/nicmd', 'needed': 'libonlynicmd.so',
                                   'kind': 'selected-stock', 'provider': 'vendor/lib64/libonlynicmd.so'})
        self.assertEqual([p for p in before if p != 'vendor/lib64/libonlynicmd.so'], subject.unreachable(selection))
        _, s2 = subject.transform(recipe, selection)
        self.assertEqual(['vendor/lib64/libonlynicmd.so'], subject.unreachable(s2))

    def test_a_library_loaded_with_dlopen_counts_as_reached(self):
        _, selection = stock_pair()
        selection['files'].append({'path': 'vendor/lib64/libplugin.so'})
        selection['edges'].append({'consumer': 'vendor/lib64/libqcrilDataModule.so', 'needed': 'libplugin.so',
                                   'kind': subject.RUNTIME_EDGE, 'provider': 'vendor/lib64/libplugin.so'})
        self.assertNotIn('vendor/lib64/libplugin.so', subject.unreachable(selection))


class RealRecipeTests(unittest.TestCase):
    """The FP6 recipe and ELF selection of this checkout."""

    def setUp(self):
        self.device = devices.get('FP6')
        self.base = subject.pair(self.device, False)
        self.datad = subject.pair(self.device, True)
        self.kind = json.loads((ROOT / 'config/fp6-build.json').read_bytes())['notice_kind']

    def test_without_the_switch_the_pair_is_the_device_files(self):
        self.assertEqual((json.loads(self.device.path('vendor_files').read_bytes()),
                          json.loads(self.device.path('vendor_elf').read_bytes())), self.base)

    def test_twelve_files_and_eighty_six_edges_leave(self):
        (r0, s0), (r1, s1) = self.base, self.datad
        self.assertEqual(set(subject.DROPPED), {r['path'] for r in r0['files']} - {r['path'] for r in r1['files']})
        self.assertEqual(12, len(r0['files']) - len(r1['files']))
        self.assertEqual(10, len(s0['files']) - len(s1['files']))
        self.assertEqual(86, len(s0['edges']) - len(s1['edges']))
        self.assertEqual(len(r0['notices']), len(r1['notices']))
        self.assertEqual([subject.ROOT], sorted(set(s0['roots']) - set(s1['roots'])))
        # the one library that only the dropped files used stops being installed
        self.assertEqual(['vendor/lib64/android.system.net.netd-V1-ndk.so'],
                         [p for p in subject.unreachable(s1) if p not in subject.unreachable(s0)])

    def test_the_recipe_without_the_files_is_a_valid_recipe(self):
        stock = json.loads(self.device.path('stock_image_recipe').read_bytes())
        vendor_files.selection(self.datad[0], stock)

    def test_the_renderer_takes_the_pair_and_links_the_source_modules(self):
        rendered = vendor_product.render(*self.datad, self.kind)
        bp, mk = rendered['Android.bp'], rendered['device-vendor.mk']
        bp = bp.decode() if isinstance(bp, bytes) else bp
        mk = mk.decode() if isinstance(mk, bytes) else mk
        for word in ('nicmd', 'nicm_internal', 'nicm_modemproxy', 'nicm_netlink', 'nicm_recovery', 'nicm_utils',
                     'rmnetctl', 'libshsc', 'nicm_config', 'lib64_libnicm'):
            self.assertNotIn(word, bp + mk)
        block = bp[bp.index('fp6_stock_vendor_lib64_libdsi_netctrl'):]
        block = block[:block.index('\n}\n')]
        self.assertIn('"libnicm"', block)
        self.assertIn('"libnicm_dsi"', block)

    def test_the_renderer_refuses_the_source_modules_unless_they_are_reviewed(self):
        reviewed = vendor_product.SOURCE_MODULE_DEPENDENCIES - {'libnicm', 'libnicm_dsi'}
        with mock.patch.object(vendor_product, 'SOURCE_MODULE_DEPENDENCIES', reviewed):
            with self.assertRaisesRegex(VendorError, 'unreviewed source module'):
                vendor_product.render(*self.datad, self.kind)

    def test_the_two_reviewed_names_change_nothing_without_the_switch(self):
        base = vendor_product.render(*self.base, self.kind)
        reviewed = vendor_product.SOURCE_MODULE_DEPENDENCIES - {'libnicm', 'libnicm_dsi'}
        with mock.patch.object(vendor_product, 'SOURCE_MODULE_DEPENDENCIES', reviewed):
            self.assertEqual(base, vendor_product.render(*self.base, self.kind))
        # and the stock selection has no edge of that kind to either
        self.assertFalse([e for e in self.base[1]['edges'] if e['kind'] == 'source-module'
                          and e['needed'] in ('libnicm.so', 'libnicm_dsi.so')])

    def test_a_stock_update_that_changes_the_closure_stops_it(self):
        recipe, selection = copy.deepcopy(self.base)
        next(r for r in recipe['files'] if r['path'] == 'vendor/lib64/libqcrilDataModule.so')['dependencies'].append(
            'vendor/lib64/libnicm_utils.so')
        with self.assertRaisesRegex(VendorError, 'libqcrilDataModule.so needs'):
            subject.transform(recipe, selection)

    def test_the_image_check_of_the_data_stack_names_the_files_this_module_drops_and_replaces(self):
        import json
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        rule = next(r for r in json.loads((root / 'config/fp6-image-checks.json').read_text())['rules']
                    if r['id'] == 'data-stack')
        upper = lambda paths: sorted('VENDOR/' + p.split('/', 1)[1] for p in paths)
        self.assertEqual(upper(set(subject.DROPPED) - set(subject.REPLACED)), sorted(rule['datad']['absent']))
        self.assertEqual(upper(subject.REPLACED), sorted(rule['datad']['not_stock']))
        self.assertIn('VENDOR/bin/datad', rule['datad']['present'])
        self.assertTrue(set(rule['datad']['not_stock']) <= set(rule['datad']['present']))
        # the hashes are those of the stock files the recipe pins
        recipe = json.loads((root / 'config/fp6-minimal/vendor-files.json').read_text())
        pinned = {r['path']: r['sha256'] for r in recipe['files']}
        for path, sha in rule['datad']['not_stock'].items():
            self.assertEqual(pinned['vendor/' + path.split('/', 1)[1]], sha)


if __name__ == '__main__':
    unittest.main()

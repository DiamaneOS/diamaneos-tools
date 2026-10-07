"""Overlay check: small fixture trees, local git remotes, no network."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from diamaneos_tools import overlays

FIXTURES = Path(__file__).resolve().parent / 'fixtures'


def run_git(repo, *arguments):
    return subprocess.run(['git', '-C', str(repo), *arguments], check=True,
                          capture_output=True, text=True).stdout.strip()


class Fixture(unittest.TestCase):
    """A private copy of the fixture tree that each test may change."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        shutil.copytree(FIXTURES, self.base / 'fixtures')
        self.workspace = self.base / 'fixtures/workspace'
        self.tree = self.base / 'fixtures/tree'
        self.config = self.base / 'fixtures/config.json'
        self.manifest = self.base / 'fixtures/manifest.xml'

    def write(self, relative, text):
        path = self.base / 'fixtures' / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def edit(self, relative, old, new):
        path = self.base / 'fixtures' / relative
        text = path.read_text()
        self.assertIn(old, text)
        path.write_text(text.replace(old, new))

    def check(self, *extra, source=None):
        """Run the command; return (exit code, JSON report or None, stderr)."""
        source = source or ['--source-tree', str(self.tree)]
        argv = ['--config', str(self.config), '--root', str(self.workspace), *source, '--json', *extra]
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = overlays.main(argv, **self.main_options())
        return code, json.loads(stdout.getvalue()) if stdout.getvalue() else None, stderr.getvalue()

    def main_options(self):
        return {}

    def codes(self, report, severity='error'):
        return sorted(f['code'] for f in report['findings'] if f['severity'] == severity)

    def only(self, report, code):
        found = [f for f in report['findings'] if f['code'] == code]
        self.assertEqual(len(found), 1, report['findings'])
        return found[0]


class CleanTreeTests(Fixture):
    def test_clean_fixture_passes(self):
        code, report, _ = self.check()
        self.assertEqual(code, 0, report['findings'])
        self.assertEqual(report['status'], 'pass')
        self.assertEqual(report['findings'], [])
        self.assertEqual(report['summary'], {'errors': 0, 'warnings': 0, 'overlays': 4, 'targets': 3})
        by_module = {o['module']: o for o in report['overlays']}
        self.assertEqual(by_module['DeviceFrameworkOverlay']['partition'], 'vendor')
        self.assertEqual(by_module['ExampleFrameworkOverlay']['partition'], 'product')
        self.assertEqual(by_module['DeviceFrameworkOverlay']['path'], 'device_example/rro/DeviceFrameworkOverlay')
        self.assertEqual(by_module['DeviceFrameworkOverlay']['resources'], 3)
        self.assertTrue(report['targets']['com.example.locked']['defines_overlayable'])
        self.assertEqual(report['source'], {'kind': 'tree', 'path': 'tree', 'layout': 'path'})

    def test_text_report_names_every_overlay(self):
        argv = ['--config', str(self.config), '--root', str(self.workspace), '--source-tree', str(self.tree)]
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(overlays.main(argv), 0)
        text = stdout.getvalue()
        self.assertIn('vendor   DeviceFrameworkOverlay -> android (priority 1, 3 resources)', text)
        self.assertIn('PASS: 0 errors, 0 warnings (4 overlays, 3 targets checked)', text)

    def test_explicit_overlay_directories_replace_configured_roots(self):
        code, report, _ = self.check('--device-overlays', str(self.workspace / 'device_example/rro'))
        self.assertEqual(code, 0)
        self.assertEqual([o['module'] for o in report['overlays']], ['DeviceFrameworkOverlay'])
        self.assertEqual(report['overlays'][0]['path'], 'rro/DeviceFrameworkOverlay')

    def test_invalid_release_tag_is_an_input_error(self):
        code, report, stderr = self.check('--source-layout', 'name', '--manifest', str(self.manifest), '--tag=-x')
        self.assertEqual((code, report), (2, None))
        self.assertIn('invalid release tag', stderr)

    def test_missing_overlay_root_is_an_input_error(self):
        shutil.rmtree(self.workspace / 'device_example')
        code, report, stderr = self.check()
        self.assertEqual((code, report), (2, None))
        self.assertIn('overlay root not found: device_example/rro', stderr)


class ResourceTests(Fixture):
    def test_resource_missing_from_target(self):
        self.edit('workspace/vendor_example/overlay/ExampleFrameworkOverlay/res/values/config.xml',
                  '</resources>', '<bool name="config_removedUpstream">true</bool>\n</resources>')
        code, report, _ = self.check()
        self.assertEqual(code, 1)
        missing = self.only(report, 'missing')
        self.assertEqual((missing['overlay'], missing['target'], missing['resource'], missing['qualifiers']),
                         ('ExampleFrameworkOverlay', 'android', 'bool/config_removedUpstream', ['default']))

    def test_type_is_part_of_the_resource_identity(self):
        self.edit('workspace/vendor_example/overlay/ExampleFrameworkOverlay/res/values/config.xml',
                  '<dimen name="config_buttonCornerRadius">8dp</dimen>',
                  '<integer name="config_buttonCornerRadius">8</integer>')
        _, report, _ = self.check()
        self.assertEqual(self.only(report, 'missing')['resource'], 'integer/config_buttonCornerRadius')

    def test_file_resource_missing_from_target(self):
        self.write('workspace/device_example/rro/DeviceFrameworkOverlay/res/drawable-night/ic_gone.xml', '<vector/>')
        _, report, _ = self.check()
        self.assertEqual(self.only(report, 'missing')['resource'], 'drawable/ic_gone')

    def test_qualifier_the_target_lacks_is_a_warning_and_fails_strict(self):
        self.write('workspace/vendor_example/overlay/ExampleSettingsOverlay/res/values-de/strings.xml',
                   '<resources><string name="settings_title">Einstellungen</string></resources>')
        code, report, _ = self.check()
        self.assertEqual((code, report['status']), (0, 'pass'))
        warning = self.only(report, 'qualifier-not-in-target')
        self.assertEqual(warning['qualifiers'], ['de'])
        code, report, _ = self.check('--strict')
        self.assertEqual((code, report['status'], report['strict']), (1, 'fail', True))

    def test_target_qualifier_the_overlay_does_not_cover(self):
        for qualifier in ('night', 'en-rXC', 'sw600dp', 'sw400dp-land'):
            self.write(f'tree/frameworks/base/core/res/res/values-{qualifier}/config.xml',
                       '<resources><bool name="config_buttonTextAllCaps">true</bool>'
                       '<integer name="config_screenBrightnessDoze">1</integer></resources>')
        code, report, _ = self.check()
        self.assertEqual(code, 1)
        found = sorted((f['severity'], f['overlay'], f['resource'], tuple(f['qualifiers']))
                       for f in report['findings'] if f['code'] == 'target-qualifier')
        # Rule 2 is an error for product overlays and a warning for the device's own overlays;
        # pseudo-locales and widths above the device's never apply.
        self.assertEqual(found, [
            ('error', 'ExampleFrameworkOverlay', 'bool/config_buttonTextAllCaps', ('night',)),
            ('warning', 'DeviceFrameworkOverlay', 'integer/config_screenBrightnessDoze', ('night',))])

    def test_reviewed_waiver_covers_a_target_qualifier(self):
        self.write('tree/frameworks/base/core/res/res/values-night/config.xml',
                   '<resources><bool name="config_buttonTextAllCaps">true</bool></resources>')
        config = json.loads(self.config.read_text())
        config['product_rules']['qualifier_waivers'] = [{
            'overlay': 'ExampleFrameworkOverlay', 'resource': 'bool/config_buttonTextAllCaps',
            'qualifiers': ['night'], 'reason': 'the night value is the same'}]
        self.config.write_text(json.dumps(config))
        code, report, _ = self.check()
        self.assertEqual((code, report['findings']), (0, []))

    def test_api_level_qualifiers_on_the_device(self):
        self.write('tree/frameworks/base/core/res/res/values-v31/config.xml',
                   '<resources><dimen name="config_buttonCornerRadius">6dp</dimen></resources>')
        self.write('tree/frameworks/base/core/res/res/values-v99/config.xml',
                   '<resources><dimen name="config_buttonCornerRadius">6dp</dimen></resources>')
        code, report, _ = self.check()
        self.assertEqual(code, 1)
        # v99 never matches on API 37, so only v31 is reported.
        self.assertEqual(self.codes(report), ['shadowed'])
        self.assertEqual(self.only(report, 'shadowed')['qualifiers'], ['v31'])
        # An overlay variant at a higher applicable level wins, and the target default is unreachable.
        self.write('workspace/vendor_example/overlay/ExampleFrameworkOverlay/res/values-v33/config.xml',
                   '<resources><dimen name="config_buttonCornerRadius">8dp</dimen></resources>')
        code, report, _ = self.check()
        self.assertEqual((code, self.codes(report)), (0, []))
        self.assertEqual(self.codes(report, 'warning'), ['qualifier-not-in-target'])
        (self.base / 'fixtures/workspace/vendor_example/overlay/ExampleFrameworkOverlay/res/values-v33/config.xml').unlink()
        self.write('workspace/vendor_example/overlay/ExampleFrameworkOverlay/res/values-v31/config.xml',
                   '<resources><dimen name="config_buttonCornerRadius">8dp</dimen></resources>')
        code, report, _ = self.check()
        self.assertEqual((code, report['findings']), (0, []))

    def test_density_variants_in_the_target_shadow_a_default_overlay(self):
        for bucket in ('hdpi', 'xhdpi', 'xxhdpi', 'xxxhdpi'):
            self.write(f'tree/frameworks/base/core/res/res/drawable-{bucket}/ic_device.png', 'png')
        code, report, _ = self.check()
        self.assertEqual(code, 1)
        self.assertEqual(self.only(report, 'shadowed')['qualifiers'], ['hdpi', 'xhdpi', 'xxhdpi', 'xxxhdpi'])
        # The overlay's own xxhdpi variant is the device's exact density, so the overlay wins.
        self.write('workspace/device_example/rro/DeviceFrameworkOverlay/res/drawable-xxhdpi/ic_device.png', 'png')
        code, report, _ = self.check()
        self.assertEqual((code, report['findings']), (0, []))

    def test_nodpi_target_beats_a_default_overlay_and_anydpi_beats_it(self):
        self.write('tree/frameworks/base/core/res/res/drawable-nodpi/ic_device.png', 'png')
        _, report, _ = self.check()
        self.assertEqual(self.only(report, 'shadowed')['qualifiers'], ['nodpi'])
        self.write('workspace/device_example/rro/DeviceFrameworkOverlay/res/drawable-anydpi/ic_device.xml', '<vector/>')
        code, report, _ = self.check()
        self.assertEqual(code, 0)
        self.assertEqual(self.codes(report), [])

    def test_smallest_width_variants_on_the_device(self):
        self.write('tree/frameworks/base/core/res/res/values-sw360dp/config.xml',
                   '<resources><dimen name="config_buttonCornerRadius">6dp</dimen></resources>')
        self.write('tree/frameworks/base/core/res/res/values-sw400dp/config.xml',
                   '<resources><dimen name="config_buttonCornerRadius">6dp</dimen></resources>')
        code, report, _ = self.check()
        self.assertEqual(code, 1)
        # sw360dp matches the FP6 (372 dp) and beats the overlay's default; sw400dp never matches.
        self.assertEqual(self.codes(report), ['shadowed'])
        self.assertEqual(self.only(report, 'shadowed')['qualifiers'], ['sw360dp'])
        self.write('workspace/vendor_example/overlay/ExampleFrameworkOverlay/res/values-sw360dp/config.xml',
                   '<resources><dimen name="config_buttonCornerRadius">8dp</dimen></resources>')
        code, report, _ = self.check()
        self.assertEqual((code, report['findings']), (0, []))

    def test_committed_rules_keep_product_overlays_off_the_tally_indicators(self):
        rules = overlays.load_config()['product_rules']
        resources = overlays.Resources()
        for rtype, rname in (('dimen', 'tally_privacy_dot_size'), ('color', 'tally_sensor_dark'),
                             ('color', 'tally_capture_light'), ('dimen', 'tally_lens_radius'),
                             ('dimen', 'privacy_dot_size'), ('color', 'ongoing_privacy_chip')):
            resources.add(rtype, rname, '')
        overlay = {'module': 'ExampleSystemUIOverlay', 'target': 'com.android.systemui', 'resources': resources}
        denied = {f['resource'] for f in overlays.product_checks(overlay, rules) if f['code'] == 'denied'}
        self.assertEqual(denied, {f'{t}/{n}' for t, n in resources.entries})

    def test_configuration_model_follows_the_platform(self):
        # ResTable_config::isBetterThan at 480 dpi for the modelled fields.
        m = overlays.modelled
        self.assertEqual(m(''), (0, 0, 0))
        self.assertEqual(m('sw360dp-xxhdpi-v31'), (360, 480, 31))
        self.assertEqual(m('anydpi-v26'), (0, 0xfffe, 26))
        self.assertIsNone(m('night'))
        self.assertIsNone(m('v31-v33'))
        better = lambda a, b: overlays.better(m(a), m(b), 480)
        self.assertTrue(better('xxhdpi', 'xxxhdpi'))
        self.assertTrue(better('xxxhdpi', ''))
        self.assertTrue(better('hdpi', ''))
        self.assertFalse(better('ldpi', ''))
        self.assertTrue(better('', 'ldpi'))
        self.assertTrue(better('', 'mdpi'))
        self.assertTrue(better('nodpi', ''))
        self.assertTrue(better('anydpi', 'nodpi'))
        self.assertTrue(better('sw320dp', 'xxhdpi-v35'))
        self.assertTrue(better('v33', 'v31'))

    def test_feature_flagged_target_resource_is_a_warning(self):
        self.edit('tree/packages/apps/Settings/res/values/strings.xml', '<string name="settings_title">',
                  '<string name="settings_title" android:featureFlag="com.example.flag" '
                  'xmlns:android="http://schemas.android.com/apk/res/android">')
        self.edit('tree/packages/apps/Settings/res/values-fr/strings.xml', '<string name="settings_title">',
                  '<string xmlns:android="http://schemas.android.com/apk/res/android" '
                  'android:featureFlag="com.example.flag" name="settings_title">')
        _, report, _ = self.check()
        self.assertIn('com.example.flag', self.only(report, 'flagged-in-target')['message'])

    def test_flag_directories_mark_target_resources(self):
        self.write('tree/packages/apps/Locked/res/values/flag(!com.example.off)/more.xml',
                   '<resources><color name="later">#000</color></resources>')
        self.edit('tree/packages/apps/Locked/res/values/overlayable.xml', '<item type="color" name="accent" />',
                  '<item type="color" name="accent" /><item type="color" name="later" />')
        self.edit('workspace/vendor_example/overlay/ExampleLockedOverlay/res/values/colors.xml',
                  '</resources>', '<color name="later">#FFF</color></resources>')
        _, report, _ = self.check()
        self.assertEqual(self.only(report, 'flagged-in-target')['resource'], 'color/later')

    def test_unreadable_target_values_file_is_reported(self):
        self.write('tree/packages/apps/Settings/res/values/broken.xml', '<resources><string name="x">')
        code, report, _ = self.check()
        self.assertEqual(code, 1)
        self.assertIn('broken.xml', self.only(report, 'target-source')['message'])

    def test_document_type_declarations_are_refused(self):
        self.write('tree/packages/apps/Settings/res/values/entities.xml',
                   '<!DOCTYPE r [<!ENTITY a "aaaa">]><resources><string name="x">&a;</string></resources>')
        _, report, _ = self.check()
        self.assertIn('DTDs are not accepted', self.only(report, 'target-source')['message'])

    def test_document_type_declarations_are_refused_in_utf16(self):
        path = self.base / 'fixtures/tree/packages/apps/Settings/res/values/entities.xml'
        path.write_bytes('<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE r [<!ENTITY a "aaaa">]>'
                         '<resources><string name="x">&a;</string></resources>'.encode('utf-16'))
        _, report, _ = self.check()
        self.assertIn('DTDs are not accepted', self.only(report, 'target-source')['message'])
        with self.assertRaises(overlays.OverlayCheckError):
            overlays.parse_xml(b'<!DOCTYPE resources><resources/>')


class OverlayableTests(Fixture):
    LOCKED = 'workspace/vendor_example/overlay/ExampleLockedOverlay'

    def overlay_color(self, name):
        self.edit(f'{self.LOCKED}/res/values/colors.xml', '</resources>', f'<color name="{name}">#1</color></resources>')

    def test_resource_outside_every_overlayable_group(self):
        self.overlay_color('plain')
        code, report, _ = self.check()
        self.assertEqual(code, 1)
        self.assertEqual(self.only(report, 'not-overlayable')['resource'], 'color/plain')

    def test_signature_only_policy_needs_a_key(self):
        self.overlay_color('secret')
        _, report, _ = self.check()
        finding = self.only(report, 'needs-key')
        self.assertIn('policy signature', finding['message'])
        self.assertIn('product|public', finding['message'])

    def test_partition_policy_must_match(self):
        self.overlay_color('odm_only')
        _, report, _ = self.check()
        self.assertEqual(self.only(report, 'policy')['resource'], 'color/odm_only')

    def test_odm_device_overlay_fulfils_the_odm_policy(self):
        self.write('workspace/device_example/rro/odm/Android.bp', 'runtime_resource_overlay {\n'
                   '    name: "DeviceLockedOverlay",\n    device_specific: true,\n}\n')
        self.write('workspace/device_example/rro/odm/AndroidManifest.xml',
                   '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="org.example.odm">'
                   '<application android:hasCode="false"/><overlay android:targetPackage="com.example.locked" '
                   'android:targetName="Theme" android:isStatic="true" android:priority="5"/></manifest>')
        self.write('workspace/device_example/rro/odm/res/values/colors.xml',
                   '<resources><color name="odm_only">#1</color></resources>')
        code, report, _ = self.check()
        self.assertEqual(code, 0, report['findings'])
        self.assertIn({'module': 'DeviceLockedOverlay', 'partition': 'odm'},
                      [{'module': o['module'], 'partition': o['partition']} for o in report['overlays']])

    def test_unknown_policy_is_a_target_problem(self):
        self.edit('tree/packages/apps/Locked/res/values/overlayable.xml', '<policy type="odm">',
                  '<policy type="odm|everyone">')
        _, report, _ = self.check()
        self.assertIn('unknown policy everyone', self.only(report, 'target-source')['message'])

    def test_target_name_must_match_the_group(self):
        self.edit(f'{self.LOCKED}/AndroidManifest.xml', 'android:targetName="Theme"', 'android:targetName="Other"')
        _, report, _ = self.check()
        self.assertIn('"Other" does not match overlayable "Theme"', self.only(report, 'target-name')['message'])

    def test_missing_target_name_does_not_match(self):
        self.edit(f'{self.LOCKED}/AndroidManifest.xml', 'android:targetName="Theme"', '')
        _, report, _ = self.check()
        self.assertEqual(self.only(report, 'target-name')['resource'], 'color/accent')


class PackagingTests(Fixture):
    FRAMEWORK = 'workspace/vendor_example/overlay/ExampleFrameworkOverlay/AndroidManifest.xml'
    PRODUCT_BP = 'workspace/vendor_example/overlay/Android.bp'
    DEVICE_BP = 'workspace/device_example/rro/Android.bp'

    def test_overlay_must_be_static(self):
        self.edit(self.FRAMEWORK, 'android:isStatic="true" ', '')
        _, report, _ = self.check()
        self.assertEqual(self.codes(report), ['not-static'])

    def test_overlay_must_have_no_code(self):
        self.edit(self.FRAMEWORK, '<application android:hasCode="false" />', '<application />')
        _, report, _ = self.check()
        self.assertEqual(self.codes(report), ['has-code'])

    def test_platform_key_and_other_certificates_are_refused(self):
        self.edit(self.PRODUCT_BP, 'name: "ExampleFrameworkOverlay",',
                  'name: "ExampleFrameworkOverlay",\n    certificate: "platform",')
        self.edit(self.DEVICE_BP, 'vendor: true,', 'vendor: true,\n    certificate: "shared",')
        _, report, _ = self.check()
        self.assertEqual(self.codes(report), ['certificate', 'platform-key'])

    def test_product_overlay_must_install_on_product(self):
        self.edit(self.PRODUCT_BP, 'name: "ExampleFrameworkOverlay",\n    product_specific: true,',
                  'name: "ExampleFrameworkOverlay",\n    vendor: true,')
        code, report, _ = self.check()
        finding = self.only(report, 'partition')
        self.assertIn('must install on product, not vendor', finding['message'])

    def test_device_overlay_must_install_on_vendor_or_odm(self):
        self.edit(self.DEVICE_BP, 'vendor: true,', 'product_specific: true,')
        _, report, _ = self.check()
        self.assertIn('must install on odm or vendor, not product', self.only(report, 'partition')['message'])

    def test_system_partition_and_conflicting_partitions(self):
        self.edit(self.DEVICE_BP, 'vendor: true,', '')
        _, report, _ = self.check()
        self.assertIn('not system', self.only(report, 'partition')['message'])
        self.edit(self.DEVICE_BP, 'name: "DeviceFrameworkOverlay",',
                  'name: "DeviceFrameworkOverlay",\n    vendor: true,\n    device_specific: true,')
        _, report, _ = self.check()
        self.assertIn('not conflicting partitions', self.only(report, 'partition')['message'])

    def test_resources_map_and_conditional_overlays(self):
        self.edit(self.FRAMEWORK, 'android:priority="2"', 'android:priority="2" android:resourcesMap="@xml/map" '
                  'android:requiredSystemPropertyName="ro.example" android:requiredSystemPropertyValue="1"')
        _, report, _ = self.check()
        self.assertEqual(self.codes(report), ['resources-map'])
        self.assertEqual(self.codes(report, 'warning'), ['conditional'])

    def test_manifest_without_overlay_and_bad_priority(self):
        self.edit(self.FRAMEWORK, 'android:priority="2"', 'android:priority="high"')
        _, report, _ = self.check()
        self.assertIn('not an integer', self.only(report, 'manifest')['message'])
        self.write('workspace/vendor_example/overlay/ExampleFrameworkOverlay/AndroidManifest.xml',
                   '<manifest package="x"/>')
        _, report, _ = self.check()
        self.assertIn('has no <overlay>', self.only(report, 'manifest')['message'])

    def test_unbuilt_overlay_manifest_and_make_overlays_are_reported(self):
        self.write('workspace/vendor_example/overlay/Stray/AndroidManifest.xml',
                   '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="x">'
                   '<overlay android:targetPackage="android"/></manifest>')
        self.write('workspace/vendor_example/overlay/Make/Android.mk',
                   'include $(CLEAR_VARS)\nLOCAL_PACKAGE_NAME := Make\ninclude $(BUILD_RRO_PACKAGE)\n')
        _, report, _ = self.check()
        self.assertEqual(self.codes(report), ['android-mk', 'module'])
        messages = ' '.join(f['message'] for f in report['findings'])
        self.assertIn('Stray/AndroidManifest.xml declares an overlay that no runtime_resource_overlay module builds',
                      messages)

    def test_library_resources_are_an_error(self):
        self.edit(self.DEVICE_BP, 'vendor: true,', 'vendor: true,\n    static_libs: ["lib"],')
        _, report, _ = self.check()
        self.assertEqual(self.codes(report), ['static-libs'])

    def test_defaults_and_override_overlays_are_not_modelled(self):
        self.edit(self.DEVICE_BP, 'vendor: true,', 'vendor: true,\n    defaults: ["rro_defaults"],')
        self.write('workspace/vendor_example/overlay/Override/Android.bp',
                   'override_runtime_resource_overlay {\n    name: "Renamed",\n    base: "ExampleFrameworkOverlay",\n'
                   '    package_name: "org.example.renamed",\n}\n')
        _, report, _ = self.check()
        messages = sorted(f['message'] for f in report['findings'] if f['code'] == 'module')
        self.assertEqual(len(messages), 2, report['findings'])
        self.assertIn('defaults are not resolved', messages[0])
        self.assertIn('override_runtime_resource_overlay can change the package name', messages[1])

    def test_make_package_overlays_are_reported(self):
        self.write('workspace/device_example/device.mk', 'PRODUCT_PACKAGE_OVERLAYS += device/example/overlay\n')
        self.write('workspace/vendor_example/overlay/x/product.mk', 'DEVICE_PACKAGE_OVERLAYS := x\n')
        _, report, _ = self.check()
        messages = sorted(f['message'] for f in report['findings'] if f['code'] == 'android-mk')
        self.assertEqual(len(messages), 2, report['findings'])
        self.assertTrue(messages[0].startswith('device_example/device.mk sets PRODUCT_PACKAGE_OVERLAYS'))

    def test_unreadable_overlay_file_is_a_finding(self):
        path = self.write('workspace/device_example/rro/DeviceFrameworkOverlay/res/values/locked.xml', '<resources/>')
        path.chmod(0)
        self.addCleanup(path.chmod, 0o644)
        if os.access(path, os.R_OK):
            self.skipTest('running with privileges that ignore file modes')
        code, report, _ = self.check()
        self.assertEqual(code, 1)
        self.assertIn('cannot read values/locked.xml', self.only(report, 'module')['message'])

    def test_unreadable_blueprint_is_reported(self):
        self.write('workspace/device_example/rro/Android.bp', 'runtime_resource_overlay {\n    name: "X"\n    vendor: true,\n}\n')
        _, report, _ = self.check()
        messages = [f['message'] for f in report['findings'] if f['code'] == 'module']
        self.assertEqual(len(messages), 2, report['findings'])
        self.assertIn('device_example/rro/Android.bp: expected ","', messages[0])
        self.assertIn('declares an overlay that no runtime_resource_overlay module builds', messages[1])

    def test_duplicate_module_names(self):
        self.edit(self.DEVICE_BP, 'name: "DeviceFrameworkOverlay"', 'name: "ExampleFrameworkOverlay"')
        _, report, _ = self.check()
        self.assertIn('defined more than once', self.only(report, 'module')['message'])


class ProductRuleTests(Fixture):
    """Rules 3 and 4 of the agreed spec apply to product (DiamaneOS-wide) overlays only."""
    FRAMEWORK = 'workspace/vendor_example/overlay/ExampleFrameworkOverlay/res/values/config.xml'
    LOCKED = 'workspace/vendor_example/overlay/ExampleLockedOverlay/res/values/colors.xml'

    def test_types_and_config_names_outside_the_allowlist(self):
        self.edit(self.FRAMEWORK, '</resources>', '<integer name="config_screenBrightnessDoze">3</integer>'
                  '<dimen name="config_otherRadius">2dp</dimen><dimen name="toast_y_offset">116dp</dimen>'
                  '<string name="unlisted">x</string></resources>')
        _, report, _ = self.check()
        found = sorted(f['resource'] for f in report['findings'] if f['code'] == 'not-allowed')
        # toast_y_offset is a dimen without the config_ prefix, so the type allowlist covers it.
        self.assertEqual(found, ['dimen/config_otherRadius', 'integer/config_screenBrightnessDoze', 'string/unlisted'])

    def test_device_overlays_are_exempt(self):
        self.edit('workspace/device_example/rro/DeviceFrameworkOverlay/res/values/config.xml', '</resources>',
                  '<bool name="config_buttonTextAllCaps">false</bool></resources>')
        _, report, _ = self.check()
        self.assertNotIn('not-allowed', self.codes(report))

    def test_denylist_wins_over_the_allowlist(self):
        config = json.loads(self.config.read_text())
        config['product_rules']['allowed_names']['com.example.locked'] = {'forbidden': 'listed by mistake'}
        self.config.write_text(json.dumps(config))
        self.edit(self.LOCKED, '</resources>', '<color name="forbidden">#1</color><color name="privacy_chip">#2</color>'
                  '<bool name="shown">true</bool></resources>')
        _, report, _ = self.check()
        denied = sorted((f['resource'], f['message'].rsplit(': ', 1)[1]) for f in report['findings']
                        if f['code'] == 'denied')
        self.assertEqual(denied, [('bool/shown', 'behaviour switches stay upstream'),
                                  ('color/forbidden', 'never overlaid'),
                                  ('color/privacy_chip', 'privacy indicators stay upstream')])

    def test_rules_need_reasons_and_valid_entries(self):
        good = json.loads(self.config.read_text())
        changes = [
            {'allowed_types': ['string']},
            {'allowed_names': {'android': {'config_x': ''}}},
            {'denied_patterns': {'android': {'(': 'bad'}}},
            {'denied_types': {'android': {'Bool!': 'x'}}},
            {'qualifier_waivers': [{'overlay': 'X', 'resource': 'bool/x', 'qualifiers': ['night']}]},
        ]
        for change in changes:
            data = json.loads(json.dumps(good))
            data['product_rules'].update(change)
            self.config.write_text(json.dumps(data))
            with self.subTest(change=change), self.assertRaises(overlays.OverlayCheckError):
                overlays.load_config(self.config)
        for key in ('device', 'product_rules'):
            data = json.loads(json.dumps(good))
            del data[key]
            self.config.write_text(json.dumps(data))
            with self.subTest(missing=key), self.assertRaises(overlays.OverlayCheckError):
                overlays.load_config(self.config)


class CrossOverlayTests(Fixture):
    DEVICE_MANIFEST = 'workspace/device_example/rro/DeviceFrameworkOverlay/AndroidManifest.xml'

    def test_same_priority_across_partitions_is_fine(self):
        # Priority is only compared within a partition, so a vendor and a
        # product overlay may share one; the product overlay wins either way.
        self.edit(self.DEVICE_MANIFEST, 'android:priority="1"', 'android:priority="2"')
        code, report, _ = self.check()
        self.assertEqual(code, 0)
        self.assertNotIn('same-priority', self.codes(report))

    def test_same_priority_on_one_partition(self):
        self.write('workspace/vendor_example/overlay/Second/Android.bp',
                   'runtime_resource_overlay {\n    name: "SecondFrameworkOverlay",\n    product_specific: true,\n}\n')
        self.write('workspace/vendor_example/overlay/Second/AndroidManifest.xml',
                   '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="org.example.two">'
                   '<application android:hasCode="false"/><overlay android:targetPackage="android" '
                   'android:isStatic="true" android:priority="2"/></manifest>')
        self.write('workspace/vendor_example/overlay/Second/res/values/config.xml', '<resources/>')
        _, report, _ = self.check()
        self.assertIn('the APK path decides their order', self.only(report, 'same-priority')['message'])

    def test_two_overlays_defining_one_resource(self):
        self.edit('workspace/device_example/rro/DeviceFrameworkOverlay/res/values/config.xml', '</resources>',
                  '<bool name="config_buttonTextAllCaps">true</bool></resources>')
        _, report, _ = self.check()
        finding = self.only(report, 'overlap')
        self.assertEqual(finding['resource'], 'bool/config_buttonTextAllCaps')
        self.assertIn('ExampleFrameworkOverlay wins', finding['message'])

    def test_unknown_target(self):
        self.edit(self.DEVICE_MANIFEST, 'android:targetPackage="android"', 'android:targetPackage="com.example.none"')
        code, report, _ = self.check()
        self.assertEqual(code, 1)
        finding = self.only(report, 'unknown-target')
        self.assertEqual((finding['overlay'], finding['target']), ('DeviceFrameworkOverlay', 'com.example.none'))

    def test_unavailable_target_source(self):
        shutil.rmtree(self.tree / 'packages/apps/Locked')
        _, report, _ = self.check()
        self.assertIn('packages/apps/Locked is not in the source tree', self.only(report, 'target-source')['message'])
        self.assertIn('(its sources are incomplete)', self.only(report, 'missing')['message'])

    def test_unmatched_resource_pattern(self):
        self.edit('config.json', '"res": ["packages/*/res"]', '"res": ["packages/*/res", "gone/res"]')
        _, report, _ = self.check()
        self.assertIn('no resource directory matches frameworks/base/gone/res',
                      self.only(report, 'target-source')['message'])


class ParserTests(unittest.TestCase):
    def test_blueprint_values_variables_and_comments(self):
        modules = overlays.parse_blueprint('''
            // comment
            dirs = ["a"]
            dirs += ["b"]
            prefix = "Over" /* block
            comment */
            runtime_resource_overlay {
                name: prefix + "lay",
                resource_dirs: dirs + ["c"],
                aaptflags: select(soong_config_variable("x", "y"), {"a": ["b"], default: []}),
                product_variables: { debuggable: { enabled: true, }, },
                min: -1,
                raw: `a"b`,
                escaped: "x\\"y\\n",
            }
            other_module(name = "x")
        ''')
        props = modules[0]['props']
        self.assertEqual(modules[0]['type'], 'runtime_resource_overlay')
        self.assertEqual((props['name'], props['resource_dirs']), ('Overlay', ['a', 'b', 'c']))
        self.assertIs(props['aaptflags'], overlays.UNKNOWN)
        self.assertEqual(props['product_variables'], {'debuggable': {'enabled': True}})
        self.assertEqual((props['min'], props['raw'], props['escaped']), (-1, 'a"b', 'x"y\n'))
        self.assertIs(modules[1]['props'], overlays.UNKNOWN)

    def test_blueprint_errors(self):
        for text in ('m { name: "x" ', 'm { name "x" }', '= 1', 'm { name: "x" "y" }', 'm { a: ! }'):
            with self.subTest(text=text), self.assertRaises(overlays.OverlayCheckError):
                overlays.parse_blueprint(text)

    def test_unknown_variable_propagates(self):
        props = overlays.parse_blueprint('m { name: missing + "x", dirs: [other] }')[0]['props']
        self.assertIs(props['name'], overlays.UNKNOWN)
        self.assertEqual(props['dirs'], [overlays.UNKNOWN])

    def test_classify_resource_paths(self):
        self.assertEqual(overlays.classify('values-fr/strings.xml'), ('values', None, 'fr', None, None))
        self.assertEqual(overlays.classify('drawable-hdpi/frame.9.png'), ('file', 'drawable', 'hdpi', 'frame', None))
        self.assertEqual(overlays.classify('raw/anim.json'), ('file', 'raw', '', 'anim', None))
        self.assertEqual(overlays.classify('flag(a.b)/layout/main.xml'), ('file', 'layout', '', 'main', 'a.b'))
        self.assertEqual(overlays.classify('values/flag(!a.b)/x.xml'), ('values', None, '', None, '!a.b'))
        for ignored in ('values/.hidden.xml', 'values/notes.txt', 'unknown/x.xml', 'README', 'a/b/c/d.xml',
                        'drawable/x.png~', 'flag(a)/flag(b)/values/x.xml'):
            self.assertIsNone(overlays.classify(ignored), ignored)

    def test_values_parsing(self):
        resources = overlays.Resources()
        resources.add_values(b'''<resources xmlns:android="http://schemas.android.com/apk/res/android">
            <string-array name="sa"/><integer-array name="ia"/><array name="a"/>
            <item type="id" name="i"/><item type="dimen" name="f" format="float">1</item>
            <bag type="style" name="Legacy"/><add-resource type="string" name="later"/>
            <declare-styleable name="View"><attr name="own" format="color"/><attr name="android:text"/></declare-styleable>
            <public type="string" name="p"/><java-symbol type="string" name="j"/><eat-comment/>
            <style name="Theme.X"/><plurals name="pl"/><item name="no_type"/>
            <overlayable name="G"><item type="string" name="outside"/></overlayable>
        </resources>''', 'land', None, 'values-land/x.xml')
        self.assertEqual(sorted(resources.entries), [
            ('array', 'a'), ('array', 'ia'), ('array', 'sa'), ('attr', 'own'), ('dimen', 'f'), ('id', 'i'),
            ('plurals', 'pl'), ('string', 'later'), ('style', 'Legacy'), ('style', 'Theme.X'),
            ('styleable', 'View')])
        self.assertTrue(resources.defines_overlayable)
        self.assertEqual(resources.overlayable[('string', 'outside')][0]['policies'], [])
        self.assertEqual(resources.entries[('array', 'a')], {'land': {None}})

    def test_directory_patterns(self):
        directories = ['res', 'a/res', 'a/b/res', 'a/b/c/res', 'res-x']
        self.assertEqual(overlays.match_dirs(directories, 'res'), ['res'])
        self.assertEqual(overlays.match_dirs(directories, '*/res'), ['a/res'])
        self.assertEqual(overlays.match_dirs(directories, 'a/**/res'), ['a/b/c/res', 'a/b/res', 'a/res'])
        self.assertEqual(overlays.match_dirs(directories, 'res*'), ['res', 'res-x'])

    def test_manifest_projects(self):
        projects = overlays.parse_manifest((FIXTURES / 'manifest.xml').read_bytes())
        self.assertEqual(projects['frameworks/base'], {
            'name': 'platform_frameworks_base', 'revision': '1' * 40,
            'url': 'https://example.invalid/platform_frameworks_base'})
        projects = overlays.parse_manifest(b'''<manifest><remote name="r" fetch=".."/>
            <remote name="h" fetch="https://h.example/" revision="refs/tags/t"/><default remote="h"/>
            <project name="a"/><project name="b" remote="r" revision="''' + b'2' * 40 + b'''"/></manifest>''')
        self.assertEqual(projects['a']['problem'], 'the manifest does not pin it to a full commit')
        self.assertEqual(projects['b']['problem'], 'its remote has a relative fetch URL')
        for bad in (b'<other/>', b'<manifest><project name="a"/></manifest>',
                    b'<manifest><remote name="r" fetch="https://x/"/><default remote="r"/>'
                    b'<project name="a"/><project name="b" path="a"/></manifest>'):
            with self.subTest(bad=bad), self.assertRaises(overlays.OverlayCheckError):
                overlays.parse_manifest(bad)

    def test_manifest_names_stay_inside_the_tree_and_directives_are_refused(self):
        head = b'<manifest><remote name="r" fetch="https://x.example/"/><default remote="r"/>'
        for bad in (b'<project name="../outside" path="a"/>', b'<project name="/abs" path="a"/>',
                    b'<include name="other.xml"/>', b'<extend-project name="a"/>', b'<remove-project name="a"/>'):
            with self.subTest(bad=bad), self.assertRaises(overlays.OverlayCheckError):
                overlays.parse_manifest(head + bad + b'</manifest>')

    def test_remote_policy(self):
        self.assertTrue(overlays.https_only('https://github.com/GrapheneOS/platform_frameworks_base'))
        for url in ('http://github.com/x', 'file:///srv/x', 'ssh://git@github.com/x', 'https://user@github.com/x'):
            self.assertFalse(overlays.https_only(url), url)

    def test_configuration_validation(self):
        good = json.loads((FIXTURES / 'config.json').read_text())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'c.json'
            for change in ({'schema_version': 2}, {'api_level': 0}, {'overlay_roots': [{'path': '../x', 'role': 'product'}]},
                           {'overlay_roots': [{'path': 'x', 'role': 'system'}]},
                           {'targets': [{'package': 'android', 'sources': [{'project': 'a', 'res': ['../r']}]}]},
                           {'targets': [good['targets'][0], good['targets'][0]]},
                           {'inapplicable_qualifier_tokens': ['(']}):
                path.write_text(json.dumps(dict(good, **change)))
                with self.subTest(change=change), self.assertRaises(overlays.OverlayCheckError):
                    overlays.load_config(path)
            path.write_text('{"schema_version": 1, "schema_version": 1}')
            with self.assertRaises(overlays.OverlayCheckError):
                overlays.load_config(path)

    def test_repository_configuration_is_valid(self):
        config = overlays.load_config(overlays.CONFIG)
        self.assertIn('android', config['targets'])
        self.assertEqual({r['role'] for r in config['overlay_roots']}, {'product', 'device'})


class GitSourceTests(Fixture):
    """Fetch mode against local git remotes built from the fixture tree."""

    def setUp(self):
        super().setUp()
        gitconfig = self.base / 'gitconfig'
        gitconfig.write_text('[user]\n\tname = Test\n\temail = test@example.invalid\n'
                             '[init]\n\tdefaultBranch = main\n[commit]\n\tgpgsign = false\n')
        patcher = mock.patch.dict(os.environ, {'GIT_CONFIG_GLOBAL': str(gitconfig), 'GIT_CONFIG_NOSYSTEM': '1'})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.remotes = self.base / 'remotes'
        self.cache = self.base / 'cache'
        projects = {'frameworks/base': 'platform_frameworks_base', 'packages/apps/Settings': 'platform_packages_apps_Settings',
                    'packages/apps/Locked': 'platform_packages_apps_Locked'}
        entries = []
        for path, name in projects.items():
            repo = self.remotes / name
            shutil.copytree(self.tree / path, repo)
            run_git(repo, 'init', '-q')
            run_git(repo, 'config', 'uploadpack.allowFilter', 'true')
            run_git(repo, 'config', 'uploadpack.allowAnySHA1InWant', 'true')
            run_git(repo, 'add', '-A')
            run_git(repo, 'commit', '-q', '-m', 'fixture')
            entries.append(f'<project name="{name}" path="{path}" revision="{run_git(repo, "rev-parse", "HEAD")}"/>')
        self.git_manifest = self.write('git-manifest.xml', '<manifest><remote name="local" fetch="file://'
                                       f'{self.remotes}/"/><default remote="local"/>{"".join(entries)}</manifest>')
        self.release = self.write('environment.json', json.dumps({'upstream': {
            'release_tag': '2099010100', 'manifest_url': 'https://example.invalid/manifest',
            'default_manifest_sha256': hashlib.sha256(self.git_manifest.read_bytes()).hexdigest()}}))

    def main_options(self):
        return {'url_allowed': lambda url: url.startswith('file://'), 'release_path': self.release,
                'protocols': ('file',)}

    def fetch(self, *extra):
        return self.check(*extra, source=['--fetch', '--cache', str(self.cache), '--manifest', str(self.git_manifest)])

    def test_fetched_sources_match_the_tree_and_are_cached(self):
        code, report, stderr = self.fetch()
        self.assertEqual(code, 0, report and report['findings'])
        self.assertEqual(report['source']['kind'], 'fetch')
        self.assertEqual(report['source']['manifest'], 'pinned-digest')
        self.assertEqual(report['source']['release_tag'], '2099010100')
        self.assertEqual(report['source']['fetched_files'], 7)
        self.assertIn('fetching frameworks/base trees at', stderr)
        tree_code, tree_report, _ = self.check()
        self.assertEqual(tree_code, 0)
        self.assertEqual(report['targets']['android']['resources'], tree_report['targets']['android']['resources'])
        self.assertEqual(report['targets']['com.android.settings']['sources'][0]['revision'],
                         run_git(self.remotes / 'platform_packages_apps_Settings', 'rev-parse', 'HEAD'))
        # A second run needs nothing from the remotes.
        shutil.rmtree(self.remotes)
        code, report, stderr = self.fetch()
        self.assertEqual((code, report['source']['fetched_files'], stderr), (0, 0, ''))

    def test_fetched_sources_report_missing_resources(self):
        run_git(self.remotes / 'platform_frameworks_base', 'rm', '-q', 'core/res/res/drawable/ic_device.xml')
        run_git(self.remotes / 'platform_frameworks_base', 'commit', '-q', '-m', 'drop')
        head = run_git(self.remotes / 'platform_frameworks_base', 'rev-parse', 'HEAD')
        text = self.git_manifest.read_text()
        old = run_git(self.remotes / 'platform_frameworks_base', 'rev-parse', 'HEAD~1')
        self.git_manifest.write_text(text.replace(old, head))
        code, report, _ = self.fetch()
        self.assertEqual(code, 1)
        self.assertEqual(report['source']['manifest'], 'unverified-file')
        self.assertIsNone(report['source']['release_tag'])
        self.assertEqual(self.only(report, 'missing')['resource'], 'drawable/ic_device')

    def test_unpinned_project_is_a_source_problem(self):
        text = self.git_manifest.read_text()
        head = run_git(self.remotes / 'platform_packages_apps_Locked', 'rev-parse', 'HEAD')
        self.git_manifest.write_text(text.replace(head, 'refs/heads/main'))
        _, report, _ = self.fetch('--tag', '2099010100')
        self.assertEqual(report['source']['release_tag'], '2099010100')
        self.assertIn('does not pin it to a full commit', self.only(report, 'target-source')['message'])

    def test_default_policy_refuses_non_https_remotes(self):
        argv = ['--config', str(self.config), '--root', str(self.workspace), '--fetch', '--cache', str(self.cache),
                '--manifest', str(self.git_manifest)]
        stderr = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
            self.assertEqual(overlays.main(argv, release_path=self.release), 2)
        self.assertIn('only https remotes are allowed', stderr.getvalue())

    def test_default_protocols_refuse_file_remotes_even_when_the_url_is_allowed(self):
        argv = ['--config', str(self.config), '--root', str(self.workspace), '--fetch', '--cache', str(self.cache),
                '--manifest', str(self.git_manifest)]
        stderr = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
            code = overlays.main(argv, url_allowed=lambda url: True, release_path=self.release)
        self.assertEqual(code, 2)
        self.assertIn("transport 'file' not allowed", stderr.getvalue())

    def test_caller_git_directory_is_never_touched(self):
        victim = self.base / 'victim'
        victim.mkdir()
        run_git(victim, 'init', '-q')
        before = (victim / '.git/config').read_text()
        with mock.patch.dict(os.environ, {'GIT_DIR': str(victim / '.git'), 'GIT_WORK_TREE': str(victim),
                                          'GIT_INDEX_FILE': str(victim / '.git/index')}):
            code, report, _ = self.fetch()
        self.assertEqual(code, 0, report and report['findings'])
        self.assertEqual((victim / '.git/config').read_text(), before)

    def test_user_git_configuration_cannot_redirect_or_weaken_a_fetch(self):
        hostile = self.base / 'hostile-gitconfig'
        hostile.write_text(f'[url "file://{self.remotes}/"]\n\tinsteadOf = https://example.invalid/\n'
                           '[http]\n\tsslVerify = false\n[protocol]\n\tallow = always\n')
        manifest = self.write('https-manifest.xml', self.git_manifest.read_text().replace(
            f'file://{self.remotes}/', 'https://example.invalid/'))
        argv = ['--config', str(self.config), '--root', str(self.workspace), '--fetch', '--cache', str(self.cache),
                '--manifest', str(manifest)]
        # An unreachable proxy keeps the test offline: a fetch that stays https fails at once.
        environment = {'GIT_CONFIG_GLOBAL': str(hostile), 'GIT_SSL_NO_VERIFY': '1', 'GIT_CONFIG_COUNT': '1',
                       'GIT_CONFIG_KEY_0': f'url.file://{self.remotes}/.insteadOf',
                       'GIT_CONFIG_VALUE_0': 'https://example.invalid/',
                       'https_proxy': 'http://127.0.0.1:9', 'HTTPS_PROXY': 'http://127.0.0.1:9',
                       'no_proxy': '', 'NO_PROXY': ''}
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, environment), contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(stderr):
            code = overlays.main(argv, release_path=self.release)
        self.assertEqual(code, 2, stderr.getvalue())
        self.assertIn('git fetch failed', stderr.getvalue())
        self.assertNotIn('file://', stderr.getvalue())

    def test_missing_git_is_an_input_error(self):
        with mock.patch.dict(os.environ, {'PATH': str(self.base / 'empty')}):
            code, report, stderr = self.fetch()
        self.assertEqual((code, report), (2, None))
        self.assertIn('the check could not run', stderr)

    def test_cache_entry_bound_to_its_remote(self):
        self.fetch()
        repo = next((self.cache / 'git').rglob('platform_frameworks_base.git'))
        run_git(repo, 'config', 'remote.origin.url', 'file:///elsewhere')
        code, _, stderr = self.fetch()
        self.assertEqual(code, 2)
        self.assertIn('belongs to another remote', stderr)

    def test_fetch_needs_a_cache(self):
        code, _, stderr = self.check(source=['--fetch', '--manifest', str(self.git_manifest)])
        self.assertEqual(code, 2)
        self.assertIn('--fetch needs --cache', stderr)

    def test_name_layout_reads_repositories_by_name(self):
        code, report, _ = self.check('--source-layout', 'name', '--manifest', str(self.git_manifest),
                                     source=['--source-tree', str(self.remotes)])
        self.assertEqual(code, 0, report['findings'])
        self.assertEqual(report['source']['layout'], 'name')
        code, _, stderr = self.check('--source-layout', 'name', source=['--source-tree', str(self.remotes)])
        self.assertEqual(code, 2)
        self.assertIn('needs --manifest or --cache', stderr)

    def test_fetched_manifest_must_match_the_pinned_digest(self):
        remote = self.base / 'manifest-remote'
        remote.mkdir()
        (remote / 'default.xml').write_text(self.git_manifest.read_text())
        run_git(remote, 'init', '-q')
        run_git(remote, 'add', '-A')
        run_git(remote, 'commit', '-q', '-m', 'manifest')
        run_git(remote, 'tag', '2099010100')
        self.release.write_text(json.dumps({'upstream': {
            'release_tag': '2099010100', 'manifest_url': f'file://{remote}',
            'default_manifest_sha256': hashlib.sha256(self.git_manifest.read_bytes()).hexdigest()}}))
        code, report, stderr = self.check(source=['--fetch', '--cache', str(self.cache)])
        self.assertEqual(code, 0, stderr)
        self.assertEqual((report['source']['manifest'], report['source']['release_tag']),
                         ('pinned-digest', '2099010100'))
        self.assertIn('fetching manifest tag 2099010100', stderr)
        self.release.write_text(self.release.read_text().replace(report['source']['manifest_sha256'], '0' * 64))
        code, _, stderr = self.check(source=['--fetch', '--cache', str(self.cache)])
        self.assertEqual(code, 2)
        self.assertIn('does not match the pinned digest', stderr)


    def ssh_key(self, name):
        key = self.base / name
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', name, '-f', str(key)], check=True)
        public = Path(f'{key}.pub').read_text().split()
        listing = subprocess.run(['ssh-keygen', '-lf', f'{key}.pub'], check=True, capture_output=True, text=True)
        return key, ' '.join(public[:2]), listing.stdout.split()[1]

    @unittest.skipUnless(shutil.which('ssh-keygen'), 'needs ssh-keygen')
    def test_unpinned_tags_need_a_verified_signature(self):
        key, public, fingerprint = self.ssh_key('release')
        other, _, _ = self.ssh_key('other')
        remote = self.base / 'manifest-remote'
        remote.mkdir()
        (remote / 'default.xml').write_text(self.git_manifest.read_text())
        run_git(remote, 'init', '-q')
        run_git(remote, 'add', '-A')
        run_git(remote, 'commit', '-q', '-m', 'manifest')
        for tag, signing_key in (('2099020200', key), ('2099040400', other)):
            run_git(remote, '-c', 'gpg.format=ssh', '-c', f'user.signingkey={signing_key}', 'tag', '-s', '-m', tag, tag)
        run_git(remote, 'tag', '2099030300')
        signers = self.base / 'allowed_signers'
        signers.write_text(f'release@example.invalid {public}\n')
        self.release.write_text(json.dumps({'upstream': {
            'release_tag': '2099010100', 'manifest_url': f'file://{remote}',
            'default_manifest_sha256': hashlib.sha256(self.git_manifest.read_bytes()).hexdigest(),
            'allowed_signers_sha256': hashlib.sha256(signers.read_bytes()).hexdigest(),
            'signer_identity': 'release@example.invalid', 'signer_key_fingerprint': fingerprint}}))

        def run(*extra):
            return self.check(*extra, source=['--fetch', '--cache', str(self.cache)])

        code, _, stderr = run('--tag', '2099020200')
        self.assertEqual(code, 2)
        self.assertIn('is not the pinned release', stderr)
        code, report, _ = run('--tag', '2099020200', '--allow-unpinned')
        self.assertEqual((code, report['source']['manifest']), (0, 'unverified-tag'))
        code, report, _ = run('--tag', '2099020200', '--allowed-signers', str(signers))
        self.assertEqual((code, report['source']['manifest'], report['source']['release_tag']),
                         (0, 'signed-tag', '2099020200'))
        code, _, stderr = run('--tag', '2099030300', '--allowed-signers', str(signers))
        self.assertEqual(code, 2)
        self.assertIn('not an annotated, signed tag', stderr)
        code, _, stderr = run('--tag', '2099040400', '--allowed-signers', str(signers))
        self.assertEqual(code, 2)
        self.assertIn('verify-tag failed', stderr)
        signers.write_text(signers.read_text() + '# changed\n')
        code, _, stderr = run('--tag', '2099020200', '--allowed-signers', str(signers))
        self.assertEqual(code, 2)
        self.assertIn('does not match the build environment', stderr)


class PrebuiltTargetTests(unittest.TestCase):
    """A presigned app's resources come from the registry, pinned to a project revision."""

    REVISION = 'c63d8d3ddc37a582ddda3e0325e63c2d4b573b24'

    class Source:
        def __init__(self, revision):
            self.revision = revision

        def describe(self, project):
            return {'available': True, 'name': project, 'revision': self.revision, 'url': 'x'}

    def target(self, **changes):
        prebuilt = {'project': 'external/SpeechServices', 'revision': self.REVISION,
                    'evidence': 'aapt2 dump resources', 'resources': {'string': ['app_name']}}
        prebuilt.update(changes)
        return {'package': 'app.grapheneos.speechservices', 'prebuilt': prebuilt}

    def load(self, target):
        data = json.loads((FIXTURES / 'config.json').read_text())
        data['targets'].append(target)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.json'
            path.write_text(json.dumps(data))
            return overlays.load_config(path)

    def test_committed_registry_has_the_speech_services_prebuilt(self):
        target = overlays.load_config()['targets']['app.grapheneos.speechservices']
        self.assertEqual(target['prebuilt']['resources'], {'string': ['app_name']})

    def test_declared_resources_at_the_pinned_revision(self):
        target = self.load(self.target())['targets']['app.grapheneos.speechservices']
        resources, problems, _ = overlays.load_target(target, self.Source(self.REVISION))
        self.assertEqual(problems, [])
        self.assertEqual(resources.entries, {('string', 'app_name'): {'': {None}}})

    def test_moved_prebuilt_is_a_target_problem(self):
        target = self.load(self.target())['targets']['app.grapheneos.speechservices']
        _, problems, _ = overlays.load_target(target, self.Source('0' * 40))
        self.assertEqual(len(problems), 1)
        self.assertIn('read its resources again', problems[0])

    def test_invalid_prebuilt_entries_are_refused(self):
        for bad in (self.target(evidence=''), self.target(revision='main'), self.target(resources={}),
                    self.target(resources={'string': []}), dict(self.target(), sources=[])):
            with self.assertRaises(overlays.OverlayCheckError):
                self.load(bad)


class CommandLineTests(unittest.TestCase):
    def run_cli(self, *arguments):
        return subprocess.run([str(ROOT / 'bin/diamaneos'), 'overlays', 'check', *arguments],
                              capture_output=True, text=True, timeout=60, cwd=ROOT)

    def test_routed_json_report_and_exit_codes(self):
        result = self.run_cli('--config', str(FIXTURES / 'config.json'), '--root', str(FIXTURES / 'workspace'),
                              '--source-tree', str(FIXTURES / 'tree'), '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual((report['check'], report['status']), ('overlays', 'pass'))
        result = self.run_cli('--config', str(FIXTURES / 'config.json'), '--root', str(FIXTURES / 'workspace'))
        self.assertEqual(result.returncode, 2)
        self.assertIn('one of the arguments --source-tree --fetch is required', result.stderr)

    def test_help_lists_the_command(self):
        result = subprocess.run([str(ROOT / 'bin/diamaneos'), '--help'], capture_output=True, text=True, timeout=30)
        self.assertIn('overlays check', result.stdout)


if __name__ == '__main__':
    unittest.main()

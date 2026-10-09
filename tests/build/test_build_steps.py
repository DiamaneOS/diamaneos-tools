import argparse
import contextlib
import copy
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from diamaneos_tools import build_steps as steps, build_workspace as bw


def arguments(workspace, **kw):
    values = dict(workspace=str(workspace), environment=None, variant=None, jobs=None, allow_network=False,
                  factory_zip=None, shallow=False)
    values.update(kw)
    return argparse.Namespace(**values)


class Response(io.BytesIO):
    def __init__(self, data, status=200):
        super().__init__(data)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class DownloadTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data = b'factory package bytes' * 100
        self.sha = hashlib.sha256(self.data).hexdigest()
        self.requests = []

    def opener(self, request, timeout):
        self.requests.append(request.headers.get('Range'))
        start = int(request.headers['Range'].split('=')[1].rstrip('-')) if request.headers.get('Range') else 0
        return Response(self.data[start:], 206 if start else 200)

    def test_download_resumes_and_publishes_only_the_pinned_bytes(self):
        target = self.root / 'factory.zip'
        (self.root / 'factory.zip.partial').write_bytes(self.data[:100])
        steps.download('https://example.invalid/f.zip', target, len(self.data), self.sha, lambda *a: None, self.opener)
        self.assertEqual(['bytes=100-'], self.requests)
        self.assertEqual(self.data, target.read_bytes())
        steps.download('https://example.invalid/f.zip', target, len(self.data), self.sha, lambda *a: None, self.opener)
        self.assertEqual(1, len(self.requests))

    def test_wrong_bytes_are_discarded(self):
        target = self.root / 'factory.zip'
        with self.assertRaisesRegex(bw.BuildStepError, 'pinned size and SHA-256'):
            steps.download('https://example.invalid/f.zip', target, len(self.data), '0' * 64, lambda *a: None, self.opener)
        self.assertFalse(target.exists())
        self.assertFalse((self.root / 'factory.zip.partial').exists())

    def test_oversized_and_unknown_size_downloads_are_capped(self):
        target = self.root / 'factory.zip'
        big = lambda request, timeout: Response(self.data + b'extra')
        with self.assertRaisesRegex(bw.BuildStepError, 'larger than expected'):
            steps.download('https://example.invalid/f.zip', target, len(self.data), self.sha, lambda *a: None, big)
        huge = lambda request, timeout: Response(b'x' * (steps.MAX_UNKNOWN_DOWNLOAD + 1))
        with self.assertRaisesRegex(bw.BuildStepError, 'larger than expected'):
            steps.download('https://example.invalid/signers', self.root / 's', None, self.sha, lambda *a: None, huge)

    def test_redirects_leave_neither_the_host_nor_https(self):
        handler = steps.SameHostRedirects('android-builds.fairphone.com')
        for url in ('http://android-builds.fairphone.com/f.zip', 'https://mirror.example.invalid/f.zip'):
            with self.assertRaisesRegex(bw.BuildStepError, 'refusing a redirect'):
                handler.redirect_request(None, None, 302, 'Found', {}, url)

    def test_plain_http_is_refused(self):
        with self.assertRaisesRegex(bw.UsageError, 'HTTPS'):
            steps.download('http://example.invalid/f.zip', self.root / 'f', 1, self.sha)

    def test_pinned_archive_comes_from_the_official_host(self):
        recipe = json.loads((ROOT / 'config/fp6-stock-image-recipe.json').read_text())
        stock = json.loads((ROOT / 'config/stock-inputs.json').read_text())
        config = json.loads((ROOT / 'config/fp6-build.json').read_text())
        archive = steps.selected_factory_archive(recipe, stock, config)
        self.assertTrue(archive['download_url'].startswith('https://android-builds.fairphone.com/'))
        config['factory_host'] = 'mirror.example.invalid'
        with self.assertRaisesRegex(bw.UsageError, 'must come from'):
            steps.selected_factory_archive(recipe, stock, config)


class PlanTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.lines = []

    def context(self, **kw):
        return steps.make_context(arguments(self.root / 'ws', **kw), self.lines.append)

    def test_dry_run_prints_every_step_and_writes_nothing(self):
        self.assertEqual(0, steps.main(['all', '--dry-run', '--workspace', str(self.root / 'ws')], self.lines.append))
        text = '\n'.join(self.lines)
        for step in bw.STEPS:
            self.assertIn(step + ':', text)
        self.assertIn('repo init -u https://github.com/DiamaneOS/platform_manifest.git -b android17', text)
        self.assertIn('FP6-cur-user', text)
        self.assertIn('unshare --user --map-current-user --net --', text)
        self.assertFalse((self.root / 'ws').exists())

    def test_default_variant_is_user_and_userdebug_is_explicit(self):
        self.assertEqual('user', self.context().variant)
        self.assertEqual('userdebug', self.context(variant='userdebug').variant)
        with self.assertRaisesRegex(bw.UsageError, 'unknown variant'):
            self.context(variant='eng')

    def test_shallow_sync_fetches_only_pinned_commits(self):
        plan = steps.plan_sync(self.context(shallow=True))
        commands = [a.argv for a in plan.actions if a.argv]
        self.assertIn('--depth=1', commands[0])
        self.assertIn('-c', commands[1])
        self.assertTrue(all(a.network for a in plan.actions if a.argv))
        sync = next(a for a in plan.actions if a.argv and a.argv[:2] == ['repo', 'sync'])
        self.assertIn('--retry-fetches=4', sync.argv)
        self.assertEqual('HTTP/1.1', sync.env['GIT_CONFIG_VALUE_0'])

    def test_allow_network_is_recorded_as_an_input(self):
        ctx = self.context(allow_network=True)
        self.assertFalse(bw.Runner(ctx.allow_network).isolated(steps.plan_kernel(ctx).actions[1]))

    def test_step_options_are_checked(self):
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            self.assertEqual(2, steps.main(['android', '--shallow', '--dry-run', '--workspace', str(self.root)],
                                           self.lines.append))
            self.assertEqual(2, steps.main(['sync', '--factory-zip', 'x', '--dry-run', '--workspace', str(self.root)],
                                           self.lines.append))
        self.assertIn('--shallow is an option of: sync, all', errors.getvalue())

    def test_official_is_an_option_of_android_and_all(self):
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            for argv in (['sync', '--official'], ['vendor', '--no-official'], ['verify', '--official']):
                with self.subTest(argv=argv):
                    self.assertEqual(2, steps.main(argv + ['--dry-run', '--workspace', str(self.root / 'ws')],
                                                   self.lines.append))
        self.assertIn('--official is an option of: android, all', errors.getvalue())
        self.assertEqual(0, steps.main(['android', '--official', '--dry-run', '--workspace', str(self.root / 'ws')],
                                       self.lines.append))

    def android_plan(self, ctx):
        for name in ('sync', 'vendor'):
            ctx.workspace.write_state(name, {'status': 'PASS', 'inputs_sha256': 'x',
                                             'outputs': {'project_map_sha256': 'p', 'kernel_prebuilts_commit': 'k'}})
        with patch.object(steps, 'android_identity', return_value='a' * 64), \
                patch.object(steps, 'newest_commit_time', return_value='1'):
            return steps.plan_android(ctx)

    def test_only_official_builds_pass_the_official_flag_to_the_build(self):
        plans = {official: self.android_plan(self.context(official=official)) for official in (False, True)}
        build = {official: next(a for a in plan.actions if a.argv) for official, plan in plans.items()}
        self.assertEqual('true', build[True].env['DIAMANEOS_OFFICIAL_BUILD'])
        self.assertNotIn('DIAMANEOS_OFFICIAL_BUILD', build[True].unset)
        self.assertIn(', official)', build[True].description)
        self.assertNotIn('DIAMANEOS_OFFICIAL_BUILD', build[False].env)
        self.assertIn('DIAMANEOS_OFFICIAL_BUILD', build[False].unset)
        # GrapheneOS's flag never reaches either build.
        self.assertTrue(all('OFFICIAL_BUILD' in action.unset for action in build.values()))
        self.assertIs(True, plans[True].inputs['official'])
        # Other builds keep the input digest they had before the option existed.
        self.assertNotIn('official', plans[False].inputs)
        # The image tools never get the flag.
        ctx = self.context(official=True)
        ctx.workspace.write_state('sync', {'status': 'PASS', 'inputs_sha256': 'x', 'outputs': {'project_map_sha256': 'p'}})
        with patch.object(steps, 'newest_commit_time', return_value=1):
            tools = steps.plan_vendor(ctx).actions[0]
        self.assertEqual({'OFFICIAL_BUILD', 'DIAMANEOS_OFFICIAL_BUILD'}, set(tools.unset))

    def test_a_caller_environment_cannot_make_a_build_official(self):
        build = next(a for a in self.android_plan(self.context()).actions if a.argv)
        result = self.root / 'flag'
        probe = bw.Action('Probe', argv=['sh', '-c', 'printf "%s" "${DIAMANEOS_OFFICIAL_BUILD-unset}" > "$PROBE"'],
                          compile=True, env={'PROBE': str(result)}, unset=build.unset)
        with patch.dict(os.environ, {'DIAMANEOS_OFFICIAL_BUILD': 'true'}):
            bw.Runner(True, self.lines.append).run(probe, self.root / 'probe.log')
        self.assertEqual('unset', result.read_text())

    def test_official_builds_get_their_own_build_identity(self):
        sync = {'outputs': {}}
        with patch.object(steps.product_inputs, 'selected_inputs', return_value={'vendor': {'records': []}}), \
                patch.object(steps.product_inputs, 'records_sha256', return_value='r'):
            plain = steps.android_identity(self.context(), sync)
            official = steps.android_identity(self.context(official=True), sync)
            unchanged = bw.digest({'environment_sha256': self.context().environment_sha256,
                                   **steps.source_record(sync), 'vendor_records_sha256': 'r', 'variant': 'user',
                                   'build_config': steps.config_subset(self.context().config, steps.ANDROID_CONFIG)})
        self.assertNotEqual(plain, official)
        self.assertEqual(unchanged, plain)

    def test_the_workspace_remembers_the_official_choice(self):
        workspace = str(self.root / 'ws')
        self.assertEqual((False, False), (self.context().official, self.context().official_given))
        self.assertEqual(0, steps.main(['all', '--official', '--dry-run', '--workspace', workspace], self.lines.append))
        self.assertIn('variant user, official', '\n'.join(self.lines))
        self.assertFalse(self.context().official, 'a dry run changes nothing')
        steps.remember_official(self.context().workspace, True)
        later = self.context()
        self.assertEqual((True, False), (later.official, later.official_given))
        self.lines.clear()
        self.assertEqual(0, steps.main(['all', '--dry-run', '--workspace', workspace], self.lines.append))
        self.assertIn('--no-official turns that off', '\n'.join(self.lines))
        self.assertFalse(self.context(official=False).official)
        steps.remember_official(self.context().workspace, False)
        self.assertFalse(self.context().official)

    def test_a_prerequisite_keeps_the_official_choice_it_was_built_with(self):
        ctx = self.context()
        ctx.workspace.write_state('android', {'status': 'PASS', 'inputs_sha256': 'x', 'inputs': {
            'variant': 'user', 'official': True}})
        self.assertTrue(steps.recorded_context(ctx, 'android').official)
        self.assertFalse(steps.recorded_context(self.context(official=False), 'android').official)

    def test_build_number_override_is_validated(self):
        config = json.loads((ROOT / 'config/fp6-build.json').read_text())
        self.assertEqual('test.0123456789ab', steps.build_number('0123456789abcdef', config))
        with patch.dict(os.environ, {'DIAMANEOS_BUILD_NUMBER': 'test.20261003.10'}):
            self.assertEqual('test.20261003.10', steps.build_number('0' * 64, config))
        with patch.dict(os.environ, {'DIAMANEOS_BUILD_NUMBER': 'a b'}):
            with self.assertRaises(bw.UsageError):
                steps.build_number('0' * 64, config)

    def test_shallow_choice_is_remembered_and_not_part_of_the_digest(self):
        shallow = self.context(shallow=True)
        plain = self.context()
        self.assertEqual(steps.plan_sync(shallow).inputs, steps.plan_sync(plain).inputs)
        steps.plan_sync(shallow).actions[0].func()
        later = self.context()
        self.assertTrue(later.shallow)
        self.assertIn('--depth=1', [a.argv for a in steps.plan_sync(later).actions if a.argv][0])

    def test_out_dir_must_match_the_environment(self):
        environment = json.loads((ROOT / 'config/build-environment-fp6.json').read_text())
        environment['workspace']['output_subdirectory'] = environment['workspace']['source_subdirectory'] + '/out-other'
        path = self.root / 'environment.json'
        path.write_text(json.dumps(environment))
        with self.assertRaisesRegex(bw.UsageError, 'out_dir'):
            self.context(environment=str(path))

    def test_failed_commit_time_lookup_stops_a_real_build(self):
        ctx = self.context()
        for name in ('sync', 'vendor'):
            ctx.workspace.write_state(name, {'status': 'PASS', 'inputs_sha256': 'x',
                                             'outputs': {'project_map_sha256': 'p'}})
        failure = bw.BuildStepError('git log failed')
        with patch.object(steps, 'android_identity', return_value='a' * 64), \
                patch.object(steps, 'newest_commit_time', side_effect=failure):
            with self.assertRaisesRegex(bw.BuildStepError, 'BUILD_DATETIME'):
                steps.plan_android(ctx)
            ctx.dry_run = True
            plan = steps.plan_android(ctx)
        self.assertIn('<newest source commit time>', plan.actions[2].env['BUILD_DATETIME'])

    def test_a_prerequisite_keeps_the_options_it_was_built_with(self):
        ctx = self.context()
        ctx.workspace.write_state('android', {'status': 'PASS', 'inputs_sha256': 'x', 'inputs': {
            'variant': 'userdebug', 'network_isolation': False, 'build_number': 'test.7'}})
        recorded = steps.recorded_context(ctx, 'android')
        self.assertEqual(('userdebug', True, 'test.7'),
                         (recorded.variant, recorded.allow_network, recorded.build_number))
        named = steps.recorded_context(self.context(variant='user'), 'android')
        self.assertEqual(('user', True), (named.variant, named.allow_network))
        with patch.dict(os.environ, {'DIAMANEOS_BUILD_NUMBER': 'test.8'}):
            self.assertIsNone(steps.recorded_context(ctx, 'android').build_number)

    def test_android_uses_a_given_build_number(self):
        ctx = self.context()
        for name in ('sync', 'vendor'):
            ctx.workspace.write_state(name, {'status': 'PASS', 'inputs_sha256': 'x',
                                             'outputs': {'project_map_sha256': 'p', 'kernel_prebuilts_commit': 'k'}})
        with patch.object(steps, 'android_identity', return_value='a' * 64), \
                patch.object(steps, 'newest_commit_time', return_value='1'):
            plan = steps.plan_android(replace(ctx, build_number='test.7'))
        self.assertEqual('test.7', plan.inputs['build_number'])
        # The kernel prebuilts commit from the resolved manifest is an Android input.
        self.assertEqual('k', plan.inputs['sync']['kernel_prebuilts_commit'])
        self.assertNotIn('kernel', plan.inputs)

    def test_image_tools_get_a_fixed_build_number_and_the_source_date(self):
        # Otherwise aapt2's version string carries the build day, its hash
        # reaches the vendor inventory and the build identity changes daily.
        ctx = self.context()
        ctx.workspace.write_state('sync', {'status': 'PASS', 'inputs_sha256': 'x',
                                           'outputs': {'project_map_sha256': 'p'}})
        with patch.object(steps, 'newest_commit_time', return_value=1791172325):
            plan = steps.plan_vendor(ctx)
        tools = plan.actions[0]
        self.assertEqual(steps.HOST_TOOLS_BUILD_NUMBER, tools.env['BUILD_NUMBER'])
        self.assertEqual('1791172325', tools.env['BUILD_DATETIME'])
        self.assertEqual(steps.HOST_TOOLS_BUILD_NUMBER, plan.inputs['host_tools_build_number'])

    def test_vendor_step_depends_on_the_firmware_inventory_and_table_config(self):
        # The vendor tree carries the firmware release table, so a new release
        # in the inventory or a changed exclusion makes a new generation.
        ctx = self.context()
        ctx.workspace.write_state('sync', {'status': 'PASS', 'inputs_sha256': 'x',
                                           'outputs': {'project_map_sha256': 'p'}})
        with patch.object(steps, 'newest_commit_time', return_value=1):
            plan = steps.plan_vendor(ctx)
            self.assertIn('fp6-firmware-inventory.json', plan.inputs['recipes'])
            self.assertIn('firmware_release.py', plan.inputs['code'])
            config = json.loads(json.dumps(ctx.config))
            config['firmware_release']['not_checked']['tz.mbn'] = {'reason': 'test'}
            changed = steps.plan_vendor(replace(ctx, config=config))
        self.assertNotEqual(plan.inputs['firmware_release'], changed.inputs['firmware_release'])

    def test_the_vendor_step_installs_the_vendor_tree_in_the_source(self):
        ctx = self.context()
        ctx.workspace.write_state('sync', {'status': 'PASS', 'inputs_sha256': 'x',
                                           'outputs': {'project_map_sha256': 'p'}})
        with patch.object(steps, 'newest_commit_time', return_value=1):
            action = steps.plan_vendor(ctx).actions[-1]
        self.assertEqual('Install the vendor tree', action.description)
        with patch.object(steps.product_inputs, 'install') as install:
            action.func()
        install.assert_called_once_with(ctx.workspace.src, ctx.workspace.vendor, ctx.environment_path, replace=True)

    def test_build_all_follows_the_manifest_branch(self):
        environment = json.loads((ROOT / 'config/build-environment-fp6.json').read_text())
        environment['manifest']['revision'] = 'a' * 40
        pinned = self.root / 'environment.json'
        pinned.write_text(json.dumps(environment))
        for path, follows in ((None, True), (str(pinned), False)):
            with self.subTest(pinned=not follows):
                ctx = self.context(environment=path)
                self.assertEqual(follows, ctx.follows_branch)
                plan = steps.plan_sync(ctx)
                (ctx.workspace.src / '.repo').mkdir(parents=True, exist_ok=True)
                ctx.resolved_manifest.parent.mkdir(parents=True, exist_ok=True)
                ctx.resolved_manifest.write_bytes(b'<manifest/>')
                ctx.workspace.write_state('sync', {'status': 'PASS', 'inputs': plan.inputs,
                                                   'inputs_sha256': bw.digest(plan.inputs), 'outputs': {
                                                       'project_map_sha256': 'p', 'manifest_commit': 'm',
                                                       'resolved_manifest_sha256': bw.sha_file(ctx.resolved_manifest)}})
                self.lines.clear()
                argv = ['all', '--dry-run', '--workspace', str(ctx.workspace.root)]
                self.assertEqual(0, steps.main(argv + (['--environment', path] if path else []), self.lines.append))
                self.assertIn('sync: to run' if follows else 'sync: up to date', self.lines)

    def test_an_environment_must_declare_the_inputs_the_tools_use(self):
        environment = json.loads((steps.ROOT / 'config/build-environment-fp6.json').read_text())
        changes = {'stock build': lambda e: e['device_inputs'].update(selected_stock_build='FP6.WRONG.0'),
                   'factory hash': lambda e: e['device_inputs'].update(selected_stock_factory_sha256='0' * 64),
                   'project input': lambda e: e['project_inputs'][0].update(sha256='0' * 64)}
        for name, change in changes.items():
            with self.subTest(name):
                changed = copy.deepcopy(environment)
                change(changed)
                path = self.root / f'{name}.json'
                path.write_text(json.dumps(changed))
                with self.assertRaises(steps.UsageError):
                    steps.make_context(argparse.Namespace(
                        workspace=str(self.root / 'ws'), environment=str(path), variant=None, jobs=None,
                        allow_network=False, factory_zip=None, shallow=False), lambda *a: None)

    def test_environment_without_a_manifest_is_refused(self):
        environment = json.loads((ROOT / 'config/build-environment.json').read_text())
        path = self.root / 'environment.json'
        path.write_text(json.dumps(environment))
        with self.assertRaisesRegex(bw.UsageError, 'no source manifest'):
            self.context(environment=str(path))

    def test_sync_is_valid_only_with_its_recorded_resolved_manifest(self):
        ctx = self.context()
        plan = steps.plan_sync(ctx)
        (ctx.workspace.src / '.repo').mkdir(parents=True)
        state = {'outputs': {'resolved_manifest_sha256': hashlib.sha256(b'<manifest/>').hexdigest()}}
        self.assertFalse(plan.valid(state))
        ctx.resolved_manifest.parent.mkdir(parents=True, exist_ok=True)
        ctx.resolved_manifest.write_bytes(b'<manifest/>')
        self.assertTrue(plan.valid(state))
        ctx.resolved_manifest.write_bytes(b'<manifest></manifest>')
        self.assertFalse(plan.valid(state))
        with self.assertRaisesRegex(bw.BuildStepError, 'changed'):
            steps.synced_manifest(ctx, state)

    def test_old_overlay_is_moved_aside_and_other_local_manifests_stop_the_sync(self):
        src = self.root / 'src'
        local = src / '.repo/local_manifests'
        self.assertEqual([], steps.retire_overlay(src))
        local.mkdir(parents=True)
        (local / 'diamaneos.xml').write_text('<manifest/>')
        self.assertEqual(['the old manifest overlay'], steps.retire_overlay(src))
        self.assertFalse(local.exists())
        self.assertEqual('<manifest/>', (src / '.repo/diamaneos-previous-local-manifests/diamaneos.xml').read_text())
        local.mkdir()
        (local / 'mine.xml').write_text('<manifest/>')
        with self.assertRaisesRegex(bw.UsageError, 'mine.xml'):
            steps.retire_overlay(src)
        self.assertTrue((local / 'mine.xml').exists())

    def test_resolved_project_revision(self):
        resolved = (b'<manifest><remote name="r" fetch="https://example.invalid/"/><default remote="r"/>'
                    b'<project name="k" path="device/fairphone/FP6-kernel" revision="' + b'b' * 40 + b'"/></manifest>')
        self.assertEqual('b' * 40, steps.project_revision(resolved, 'device/fairphone/FP6-kernel'))
        self.assertIsNone(steps.project_revision(resolved, 'device/other'))


class PinnedSyncTests(unittest.TestCase):
    """build sync --resolved-manifest: plans and argument checks (the sync itself is in test_sync_flow)."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.lines = []
        self.workspace = self.root / 'ws'
        environment = (ROOT / 'config/build-environment-fp6.json').read_bytes()
        manifest = json.loads(environment)['manifest']
        self.commit = 'a' * 40
        self.resolved = self.root / 'resolved-manifest.xml'
        self.resolved.write_bytes(
            b'<manifest><remote name="diamaneos" fetch="https://github.com/DiamaneOS/"/><default remote="diamaneos"/>'
            b'<project name="device_example" path="device/example" revision="' + b'b' * 40 + b'"/></manifest>')
        sha = bw.sha_file(self.resolved)
        self.record = {'manifest': {'url': manifest['url'], 'branch': manifest['branch'], 'commit': self.commit,
                                    'resolved_sha256': sha},
                       'source': {'manifest_commit': self.commit, 'resolved_manifest_sha256': sha},
                       'environment': {'sha256': hashlib.sha256(environment).hexdigest()},
                       'tools': {'commit': 'c' * 40}}
        self.build_json = self.root / 'build.json'
        self.build_json.write_text(json.dumps(self.record))

    def main(self, *argv):
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            code = steps.main([*argv, '--workspace', str(self.workspace)], self.lines.append)
        return code, errors.getvalue()

    def test_dry_run_syncs_to_the_pinned_manifest_and_checks_the_recorded_commit(self):
        code, _ = self.main('sync', '--dry-run', '--resolved-manifest', str(self.resolved),
                            '--build-json', str(self.build_json))
        self.assertEqual(0, code)
        text = '\n'.join(self.lines)
        self.assertIn('repo init -u https://github.com/DiamaneOS/platform_manifest.git -b android17', text)
        self.assertIn(f'Check out the recorded manifest (the recorded manifest commit {self.commit}', text)
        self.assertIn('in the history of android17', text)
        self.assertIn('the branch-head check does not apply to a pinned manifest', text)
        self.assertIn('--retry-fetches=4 -m ' + str(self.workspace / 'state/pinned-manifest.xml'), text)
        self.assertIn('note: build.json records tools commit ' + 'c' * 40, text)
        self.assertFalse(self.workspace.exists())

    def test_a_pinned_shallow_plan_prefetches_and_marks_network_actions(self):
        ctx = steps.make_context(arguments(self.workspace, shallow=True, resolved_manifest=str(self.resolved),
                                           build_json=None, manifest_commit=self.commit), self.lines.append)
        plan = steps.plan_sync(ctx)
        network = [a for a in plan.actions if a.network]
        self.assertEqual(4, len(network))
        self.assertEqual(['Check out the recorded manifest', 'Fetch large prebuilts'],
                         [a.description for a in network[1:3]])
        # The terminal shows the short title; the log and the dry run add the detail.
        self.assertTrue(network[2].detail.startswith('the large prebuilt projects first, one revision each at depth 1'))
        self.assertIn('below 1000 bytes/s for 300 s stops, up to 8 attempts each', network[2].text(False))
        sync = next(a for a in plan.actions if a.argv and a.argv[:2] == ['repo', 'sync'])
        self.assertEqual(['-c', '--no-tags', '-m', ctx.pinned_manifest], sync.argv[-4:])
        self.assertEqual(steps.plan_sync(steps.make_context(arguments(self.workspace), print)).inputs, plan.inputs)

    def test_options_are_checked(self):
        cases = ((('sync', '--build-json', str(self.build_json)), 'go with --resolved-manifest'),
                 (('android', '--resolved-manifest', str(self.resolved)), 'is an option of: sync, all'),
                 (('all', '--from', 'vendor', '--resolved-manifest', str(self.resolved), '--manifest-commit',
                   self.commit), 'does not go with --from vendor'),
                 (('sync', '--resolved-manifest', str(self.resolved)), 'needs --build-json'),
                 (('sync', '--resolved-manifest', str(self.resolved), '--manifest-commit', 'abc'), 'full 40-character'),
                 (('sync', '--resolved-manifest', str(self.root / 'missing.xml'), '--manifest-commit', self.commit),
                  'cannot read the resolved manifest'))
        for argv, message in cases:
            with self.subTest(argv=argv):
                code, errors = self.main(*argv, '--dry-run')
                self.assertEqual(2, code)
                self.assertIn(message, errors)
        self.record['manifest']['resolved_sha256'] = 'f' * 64
        self.build_json.write_text(json.dumps(self.record))
        code, errors = self.main('sync', '--dry-run', '--resolved-manifest', str(self.resolved),
                                 '--build-json', str(self.build_json))
        self.assertEqual((2, True), (code, 'does not match' in errors))
        self.assertFalse(self.workspace.exists())

    def passed_sync(self, pinned):
        ctx = steps.make_context(arguments(self.workspace), self.lines.append)
        plan = steps.plan_sync(ctx)
        (ctx.workspace.src / '.repo').mkdir(parents=True, exist_ok=True)
        ctx.resolved_manifest.parent.mkdir(parents=True, exist_ok=True)
        ctx.resolved_manifest.write_bytes(self.resolved.read_bytes())
        outputs = {'project_map_sha256': 'p', 'manifest_commit': self.commit,
                   'resolved_manifest_sha256': bw.sha_file(ctx.resolved_manifest),
                   'pinned_manifest': {'manifest_commit': self.commit} if pinned else None}
        ctx.workspace.write_state('sync', {'status': 'PASS', 'inputs': plan.inputs,
                                           'inputs_sha256': bw.digest(plan.inputs), 'outputs': outputs})

    def test_build_all_keeps_a_reproduced_source_until_build_sync(self):
        self.passed_sync(pinned=True)
        self.assertEqual((0, ''), self.main('all', '--dry-run'))
        self.assertIn('sync: up to date', self.lines)
        self.assertTrue(any(line.startswith('note: the last sync reproduced a pinned resolved manifest')
                            and 'Run "diamaneos build sync" to move to the head of android17' in line
                            for line in self.lines))
        self.lines.clear()
        self.main('all', '--dry-run', '--resolved-manifest', str(self.resolved), '--build-json', str(self.build_json))
        self.assertIn('sync: to run', self.lines)
        self.lines.clear()
        self.passed_sync(pinned=False)
        self.main('all', '--dry-run')
        self.assertIn('sync: to run', self.lines)

    def test_android_checks_the_recorded_manifest_commit_after_a_pinned_sync(self):
        ctx = steps.make_context(arguments(self.workspace), self.lines.append)
        calls = []

        def checkout(*args, **kw):
            calls.append(kw.get('manifest_commit'))
            return {'resolved_manifest_sha256': 'r', 'resolved_project_map_sha256': 'p',
                    'generated_input_descriptor_sha256': 'd', 'modified': [], 'modified_sha256': None}
        for pinned, expected in ((True, self.commit), (False, None)):
            with self.subTest(pinned=pinned):
                ctx.workspace.write_state('sync', {'status': 'PASS', 'inputs_sha256': 'x', 'outputs': {
                    'project_map_sha256': 'p', 'kernel_prebuilts_commit': 'k', 'resolved_manifest_sha256': 'r',
                    'manifest_commit': self.commit,
                    'pinned_manifest': {'manifest_commit': self.commit} if pinned else None}})
                ctx.workspace.write_state('vendor', {'status': 'PASS', 'inputs_sha256': 'x', 'outputs': {}})
                with patch.object(steps, 'android_identity', return_value='a' * 64), \
                        patch.object(steps, 'newest_commit_time', return_value=1), \
                        patch.object(steps.build, 'verify_branch_checkout', side_effect=checkout):
                    steps.plan_android(ctx).actions[1].func()
                self.assertEqual(expected, calls[-1])
        self.assertTrue(any('not at the branch head' in line for line in self.lines))

    def test_android_builds_the_local_changes_the_sync_recorded_unless_official(self):
        allowed = []

        def checkout(*args, **kw):
            allowed.append(kw['allow_modified'])
            return {'resolved_manifest_sha256': 'r', 'resolved_project_map_sha256': 'p',
                    'generated_input_descriptor_sha256': 'd', 'modified': ['device/example'], 'modified_sha256': 'c'}
        for official in (False, True):
            ctx = steps.make_context(arguments(self.workspace, official=official), self.lines.append)
            for recorded in ('c', None):
                with self.subTest(official=official, recorded=recorded):
                    ctx.workspace.write_state('sync', {'status': 'PASS', 'inputs_sha256': 'x', 'outputs': {
                        'project_map_sha256': 'p', 'kernel_prebuilts_commit': 'k', 'resolved_manifest_sha256': 'r',
                        'manifest_commit': self.commit, 'modified_sha256': recorded}})
                    ctx.workspace.write_state('vendor', {'status': 'PASS', 'inputs_sha256': 'x', 'outputs': {}})
                    with patch.object(steps, 'android_identity', return_value='a' * 64), \
                            patch.object(steps, 'newest_commit_time', return_value=1), \
                            patch.object(steps.build, 'verify_branch_checkout', side_effect=checkout):
                        preflight = steps.plan_android(ctx).actions[1].func
                        if recorded:
                            preflight()
                        else:
                            with self.assertRaisesRegex(bw.BuildStepError, 'local changes are not the ones'):
                                preflight()
                    self.assertEqual(not official, allowed[-1])
        self.assertIn('    warning: local changes in device/example; build.json records them, and --official '
                      'refuses them', self.lines)
        # Local changes are a source input only when there are any.
        self.assertEqual(steps.SOURCE_KEYS, tuple(steps.source_record({'outputs': {'modified_sha256': None}})))
        self.assertEqual('c', steps.source_record({'outputs': {'modified_sha256': 'c'}})['modified_sha256'])


def git(path, *args):
    return subprocess.run(['git', '-C', str(path), '-c', 'user.name=Fixture', '-c', 'user.email=f@example.invalid',
                           '-c', 'commit.gpgsign=false', *args], check=True, capture_output=True,
                          text=True).stdout.strip()


class ToolsSkewTests(unittest.TestCase):
    """The running tools against the commit the sync checked out at tools/diamaneos."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.lines = []
        self.tools = self.root / 'tools'
        self.tools.mkdir()
        git(self.tools, 'init', '-q', '-b', 'main')
        self.commits = []
        for number in range(2):
            git(self.tools, 'commit', '-q', '--allow-empty', '-m', f'main {number}')
            self.commits.append(git(self.tools, 'rev-parse', 'HEAD'))
        git(self.tools, 'checkout', '-q', '-b', 'topic', self.commits[0])
        git(self.tools, 'commit', '-q', '--allow-empty', '-m', 'topic on the older main')
        self.topic = git(self.tools, 'rev-parse', 'HEAD')
        self.ctx = steps.make_context(arguments(self.root / 'ws'), self.lines.append)

    def synced(self, commit, tools=True):
        """A passed sync whose resolved manifest has the tools project at ``commit``."""
        project = f'<project name="diamaneos-tools" path="tools/diamaneos" revision="{commit}"/>' if tools else ''
        resolved = ('<manifest><remote name="diamaneos" fetch="https://github.com/DiamaneOS/"/>'
                    '<default remote="diamaneos"/><project name="device_example" path="device/example" '
                    f'revision="{"b" * 40}"/>{project}</manifest>').encode()
        bw.write_atomic(self.ctx.resolved_manifest, resolved)
        self.ctx.workspace.write_state('sync', {'status': 'PASS', 'inputs_sha256': 'x', 'outputs': {
            'project_map_sha256': 'p', 'manifest_commit': 'm',
            'resolved_manifest_sha256': hashlib.sha256(resolved).hexdigest()}})

    def skew(self, running, synced_now=False, root=None):
        ctx = replace(self.ctx, tools_commit=running)
        return steps.tools_skew(ctx, synced_now, root or self.tools)

    def test_the_source_tools_commit_or_a_newer_one_runs(self):
        old, new = self.commits
        self.synced(old)
        self.assertIsNone(self.skew(old))
        kind, message = self.skew(new)
        self.assertEqual('newer', kind)
        self.assertIn(f'newer than the source\'s tools/diamaneos ({old[:12]})', message)
        self.synced(old, tools=False)
        self.assertIsNone(self.skew(new))

    def test_tools_that_lack_the_source_tools_commit_stop_with_one_message(self):
        old, new = self.commits
        self.synced(new)
        for running in (old, self.topic):
            with self.subTest(running=running):
                kind, message = self.skew(running)
                self.assertEqual('stale', kind)
                self.assertIn(f'commit {running[:12]}) do not contain the source\'s tools/diamaneos commit '
                              f'{new[:12]}', message)
                self.assertIn('Run ' + str(self.ctx.workspace.src / 'tools/diamaneos/bin/diamaneos'), message)
        self.synced('f' * 40)
        self.assertEqual('stale', self.skew(new)[0])
        self.assertEqual('unknown', self.skew(None)[0])

    def test_check_tools_stops_notes_or_starts_again(self):
        old, new = self.commits
        self.synced(new)
        stale = replace(self.ctx, tools_commit=old)
        with patch.object(steps, 'tools_skew', return_value=('stale', 'these tools are old')):
            with self.assertRaisesRegex(bw.UsageError, 'these tools are old'):
                steps.check_tools(stale, False)
            steps.check_tools(stale, False, dry_run=True)
            self.assertIn('note: these tools are old', self.lines)
        with patch.object(steps, 'tools_skew', return_value=('moved', 'the sync moved these tools')):
            with self.assertRaises(steps.ToolsUpdated) as raised:
                steps.check_tools(stale, True)
            self.assertEqual(new, raised.exception.commit)
            with self.assertRaisesRegex(bw.UsageError, 'run the same command again'):
                steps.check_tools(replace(stale, restarted=True), True)

    def test_a_sync_that_moves_the_running_checkout_restarts_the_command(self):
        old, new = self.commits
        self.synced(new)
        checkout = self.ctx.workspace.src / 'tools/diamaneos'
        checkout.parent.mkdir(parents=True)
        checkout.symlink_to(self.tools)
        self.assertEqual('moved', self.skew(old, synced_now=True, root=checkout)[0])
        # Without a sync in this command, the recorded sync is only older or newer.
        self.assertEqual('stale', self.skew(old, synced_now=False, root=checkout)[0])
        calls = []
        with patch.object(steps, 'run_steps', side_effect=steps.ToolsUpdated('the sync moved these tools', new)), \
                patch.object(steps.bw, 'check_host', return_value={'warnings': []}), \
                patch.object(steps.os, 'execv', side_effect=lambda *a: calls.append(a)), \
                patch.dict(os.environ):
            argv = ['all', '--workspace', str(self.ctx.workspace.root)]
            self.assertEqual(0, steps.main(argv, self.lines.append))
            self.assertEqual(new, os.environ[steps.RESTARTED])
        self.assertEqual([(sys.executable, [sys.executable, str(steps.DIAMANEOS), 'build', *argv])], calls)
        self.assertIn('note: the sync moved these tools; starting again with the new tools', self.lines)

    def test_the_restarted_command_keeps_the_sync_that_moved_the_tools(self):
        plan = steps.plan_sync(self.ctx)
        (self.ctx.workspace.src / '.repo').mkdir(parents=True)
        self.synced(self.ctx.tools_commit or 'a' * 40)
        state = self.ctx.workspace.passed('sync')
        self.ctx.workspace.write_state('sync', dict(state, inputs=plan.inputs, inputs_sha256=bw.digest(plan.inputs)))
        argv = ['all', '--dry-run', '--workspace', str(self.ctx.workspace.root)]
        for restarted, expected in ((None, 'sync: to run'), (self.ctx.tools_commit, 'sync: up to date')):
            with self.subTest(restarted=restarted), patch.dict(os.environ):
                if restarted:
                    os.environ[steps.RESTARTED] = restarted
                self.lines.clear()
                self.assertEqual(0, steps.main(argv, self.lines.append))
                self.assertIn(expected, self.lines)
                self.assertNotIn(steps.RESTARTED, os.environ)


class RunnerTests(unittest.TestCase):
    """Skip, rerun and failure handling with stand-in steps."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.lines = []
        self.ctx = steps.make_context(arguments(self.root / 'ws'), self.lines.append)
        self.inputs = {name: {'value': 1} for name in bw.STEPS}
        self.calls = []
        self.fail = None

    def fake(self, name):
        def plan(ctx):
            def act():
                self.calls.append(name)
                if self.fail == name:
                    raise bw.BuildStepError(name + ' broke')
            upstream = [ctx.workspace.passed(s) for s in bw.STEPS[:bw.STEPS.index(name)]]
            inputs = dict(self.inputs[name], upstream=[u['outputs'] for u in upstream if u])
            return steps.StepPlan(name, inputs, [bw.Action('act', func=act)],
                                  lambda: {'value': self.inputs[name]['value']}, lambda state: True)
        return plan

    def run_all(self, **kw):
        with patch.dict(steps.PLANS, {n: self.fake(n) for n in bw.STEPS}):
            steps.run_steps(self.ctx, bw.STEPS, **kw)

    def test_unchanged_steps_are_skipped_and_changes_rerun_downstream(self):
        self.run_all()
        self.assertEqual(list(bw.STEPS), self.calls)
        self.calls.clear()
        self.run_all()
        self.assertEqual([], self.calls)
        self.inputs['vendor']['value'] = 2
        self.run_all()
        self.assertEqual(['vendor', 'android', 'package', 'verify'], self.calls)

    def test_forced_step_reruns_and_unchanged_outputs_stop_the_cascade(self):
        self.run_all()
        self.calls.clear()
        self.run_all(force=('package',))
        self.assertEqual(['package'], self.calls)
        self.calls.clear()
        self.run_all(force=('vendor',))
        self.assertEqual(['vendor'], self.calls)

    def test_any_exception_is_recorded_and_reported_plainly(self):
        def broken(ctx):
            plan = self.fake('sync')(ctx)
            plan.actions = [bw.Action('act', func=lambda: {}['missing key'] + 1)]
            plan.actions = [bw.Action('act', func=lambda: (_ for _ in ()).throw(TypeError('bad value')))]
            return plan
        with patch.dict(steps.PLANS, {'sync': broken}):
            with self.assertRaisesRegex(bw.BuildStepError, 'sync failed: TypeError: bad value'):
                steps.run_steps(self.ctx, ('sync',))
        self.assertEqual('TypeError: bad value', self.ctx.workspace.state('sync')['error'])

    def test_single_step_refuses_stale_prerequisites(self):
        self.run_all()
        self.inputs['vendor']['value'] = 2
        with patch.dict(steps.PLANS, {n: self.fake(n) for n in bw.STEPS}):
            with self.assertRaisesRegex(bw.UsageError, 'vendor is out of date'):
                steps.run_steps(self.ctx, ('package',), force=('package',))
            with self.assertRaisesRegex(bw.UsageError, 'vendor is out of date'):
                steps.run_steps(self.ctx, ('android',), force=('android',))
            steps.run_steps(self.ctx, ('vendor',), force=('vendor',))

    def test_build_all_has_no_kernel_step_and_build_kernel_stays(self):
        self.assertNotIn('kernel', bw.STEPS)
        self.assertNotIn('kernel', steps.DEPENDS['android'])
        self.assertEqual('build kernel', 'build ' + steps.parser().parse_args(['kernel']).step)

    def test_single_step_after_a_userdebug_build_checks_that_build(self):
        def android(ctx):
            plan = self.fake('android')(ctx)
            number = ctx.build_number or os.environ.get('DIAMANEOS_BUILD_NUMBER') or 'derived'
            plan.inputs = dict(plan.inputs, variant=ctx.variant, network_isolation=not ctx.allow_network,
                               build_number=number)
            return plan
        with patch.dict(steps.PLANS, dict({n: self.fake(n) for n in bw.STEPS}, android=android)):
            with patch.dict(os.environ, {'DIAMANEOS_BUILD_NUMBER': 'test.7'}):
                steps.run_steps(replace(self.ctx, variant='userdebug', allow_network=True), bw.STEPS)
            self.calls.clear()
            steps.run_steps(self.ctx, ('verify',), force=('verify',))
            self.assertEqual(['verify'], self.calls)
            named = replace(self.ctx, variant_given=True)
            with self.assertRaisesRegex(bw.UsageError, 'android was built as userdebug, not user; '
                                                       'run "diamaneos build android" or'):
                steps.run_steps(named, ('verify',), force=('verify',))

    def test_failure_is_recorded_and_the_next_run_resumes(self):
        self.fail = 'android'
        with self.assertRaisesRegex(bw.BuildStepError, 'android broke'):
            self.run_all()
        self.assertEqual('FAIL', self.ctx.workspace.state('android')['status'])
        self.fail = None
        self.calls.clear()
        self.run_all()
        self.assertEqual(['android', 'package', 'verify'], self.calls)

    def test_single_step_needs_its_prerequisites(self):
        with self.assertRaisesRegex(bw.UsageError, 'build sync'):
            steps.run_steps(self.ctx, ('android',))


if __name__ == '__main__':
    unittest.main()

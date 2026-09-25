from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from diamaneos_tools import kernel, kernel_layout

# pahole output for one object whose two compilation units disagree: the
# randomized layout (tag declared first) and the declaration-order layout.
PAHOLE = '''struct sde_connector_ops {
\tint                        (*post_init)(struct drm_connector *, void *); /*     0     8 */
\tint                        (*get_mode_info)(struct drm_connector *, void *); /*     8     8 */

\t/* size: 16, cachelines: 1, members: 2 */
};
struct sde_connector_ops {
\tint                        (*get_mode_info)(struct drm_connector *, void *); /*     0     8 */
\tint                        (*post_init)(struct drm_connector *, void *); /*     8     8 */
};
struct mixed {
\tint                        count;                /*     0     4 */
\tvoid                       (*done)(void);        /*     8     8 */
};
struct nested_ops {
\tstruct {
\t\tint                (*inner)(void);         /*     0     8 */
\t} group;
\tvoid                       (*outer)(void);       /*     8     8 */
};
'''


class LayoutScanTests(unittest.TestCase):
    def test_only_function_pointer_structures_are_kept_in_order(self):
        found = kernel_layout.definitions(PAHOLE)
        self.assertEqual(['sde_connector_ops', 'sde_connector_ops'], [n for n, _ in found])
        self.assertEqual('int (*post_init)(struct drm_connector *, void *)', found[0][1][0])
        self.assertEqual(found[0][1][::-1], found[1][1])

    def test_split_within_one_object_is_reported(self):
        report = kernel_layout.compare([('msm_drm.ko', kernel_layout.definitions(PAHOLE), None)])
        self.assertEqual(['sde_connector_ops'], [m['name'] for m in report['mismatches']])
        self.assertEqual(2, len(report['mismatches'][0]['variants']))

    def test_split_across_objects_is_reported_and_agreement_is_not(self):
        first, second = kernel_layout.definitions(PAHOLE)
        report = kernel_layout.compare([('vmlinux', [first], None), ('a.ko', [first], None)])
        self.assertEqual([], report['mismatches'])
        report = kernel_layout.compare([('vmlinux', [first], None), ('a.ko', [second], None)])
        self.assertEqual([['a.ko'], ['vmlinux']],
                         sorted(v['objects'] for v in report['mismatches'][0]['variants']))

    def test_different_member_sets_are_different_structures(self):
        first, _ = kernel_layout.definitions(PAHOLE)
        other = (first[0], first[1][:1])
        self.assertEqual([], kernel_layout.compare([('a', [first], None), ('b', [other], None)])['mismatches'])

    def test_unreadable_object_is_an_error(self):
        report = kernel_layout.compare([('a.ko', [], 'exit: no DWARF')])
        self.assertEqual([{'object': 'a.ko', 'message': 'exit: no DWARF'}], report['errors'])

    def test_scan_runs_pahole_on_each_object(self):
        with tempfile.TemporaryDirectory() as temp:
            pahole = Path(temp) / 'pahole'
            pahole.write_text('#!/bin/sh\ncat "$6"\n')
            pahole.chmod(0o755)
            objects = []
            for name, text in (('a.ko', PAHOLE.split('struct mixed')[0]), ('b.ko', '')):
                objects.append(Path(temp) / name)
                objects[-1].write_text(text)
            report = kernel_layout.scan(pahole, objects, 2)
        self.assertEqual(2, report['objects'])
        self.assertEqual(['sde_connector_ops'], [m['name'] for m in report['mismatches']])

    def test_visibility_warnings_are_found(self):
        log = (b'INFO: From Compiling:\n'
               b"wlan_lmac_if_def.h:588:12: warning: declaration of 'struct vdev_response_timer' "
               b'will not be visible outside of this function [-Wvisibility]\n'
               b'foo.h:1:1: warning: Warning: This header file will be deprecated [-W#pragma-messages]\n')
        found = kernel_layout.visibility_warnings(log)
        self.assertEqual(1, len(found))
        self.assertIn('vdev_response_timer', found[0])
        self.assertEqual([], kernel_layout.visibility_warnings(b'warning: [-Wvisibility-other]\n'))


class SharedHeaderTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for tree in ('common', 'vendor'):
            repo = self.root / tree
            (repo / 'include/drm').mkdir(parents=True)
            (repo / 'include/drm/mode.h').write_text('struct drm_framebuffer;\n')
            (repo / 'include/other.h').write_text(tree + '\n')
            self.commit(repo)
        self.adaptation = {'shared_headers': [{'trees': ['common', 'vendor'], 'paths': ['include/drm']}]}

    def commit(self, repo):
        run = lambda *a: subprocess.run(['git', '-C', str(repo), *a], check=True, capture_output=True)
        if not (repo / '.git').exists():
            run('init', '-q')
        run('add', '-A')
        run('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', '-c', 'commit.gpgsign=false',
            'commit', '-qm', 'state')

    def test_identical_headers_pass_and_other_files_may_differ(self):
        self.assertEqual(1, kernel.shared_headers(self.root, self.adaptation))

    def test_drift_is_rejected(self):
        (self.root / 'vendor/include/drm/mode.h').write_text('struct drm_framebuffer;\nstruct drm_file;\n')
        self.commit(self.root / 'vendor')
        with self.assertRaisesRegex(kernel.KernelError, 'shared headers differ between common and vendor'):
            kernel.shared_headers(self.root, self.adaptation)

    def test_header_only_in_one_tree_is_rejected(self):
        (self.root / 'common/include/drm/extra.h').write_text('\n')
        self.commit(self.root / 'common')
        with self.assertRaisesRegex(kernel.KernelError, 'differ'):
            kernel.shared_headers(self.root, self.adaptation)

    def test_missing_headers_are_rejected(self):
        self.adaptation['shared_headers'][0]['paths'] = ['include/absent']
        with self.assertRaisesRegex(kernel.KernelError, 'shared headers missing: common'):
            kernel.shared_headers(self.root, self.adaptation)

    def test_fp6_workspace_checks_the_drm_headers(self):
        adaptation = kernel.load_json(kernel.ROOT / 'config/kernel-workspace-fp6.json')
        self.assertEqual([{'trees': ['kernel_platform/common', 'kernel_platform/msm-kernel'],
                           'paths': ['include/drm', 'include/uapi/drm']}],
                         [{k: g[k] for k in ('trees', 'paths')} for g in adaptation['shared_headers']])


if __name__ == '__main__':
    unittest.main()

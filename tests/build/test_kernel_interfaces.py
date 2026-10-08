"""Selected module compatibility boundary fixtures."""
import copy
import tempfile
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from diamaneos_tools import kernel_interfaces as module

class SelectedKernelTests(unittest.TestCase):
    def setUp(self):
        self.modules = [dict(path='out/wlan.ko', metadata=dict(name=['wlan'], vermagic=['version'], depends=['audio-dsp'], license=['GPL'], import_ns=['DSP']), required_symbols=[dict(symbol='dsp', crc=7)]), dict(path='out/audio-dsp.ko', metadata=dict(name=['audio_dsp'], vermagic=['version'], depends=[''], license=['GPL']), required_symbols=[dict(symbol='base', crc=9)])]
        self.symbols = [dict(symbol='dsp', crc=7, owner='path/audio-dsp', namespace='DSP', export='EXPORT_SYMBOL_GPL', table='vendor/Module.symvers'), dict(symbol='base', crc=9, owner='vmlinux', namespace='', export='EXPORT_SYMBOL', table='out/common/kernel_aarch64/vmlinux.symvers')]
    def run_review(self):
        return module.review(self.modules, self.symbols, {'wlan', 'audio_dsp'})
    def test_prefixed_external_symbol_table_is_consumed(self):
        with tempfile.TemporaryDirectory() as temp:
            table = Path(temp) / 'fps_gki_audio_Module.symvers'
            table.write_text('0x00000007 dsp audio-dsp EXPORT_SYMBOL_GPL DSP\n')
            parsed = module.read_symbol_tables([table])
            self.assertEqual('dsp', parsed[0]['symbol'])
            self.assertEqual(7, parsed[0]['crc'])
            self.assertEqual('DSP', parsed[0]['namespace'])
    def test_complete_set_orders_provider_first(self):
        result = self.run_review()
        self.assertEqual('PASS', result['status'])
        self.assertEqual(['audio_dsp', 'wlan'], result['dependency_order'])
    def test_missing_audio(self):
        self.modules.pop()
        self.assertEqual('FAIL', self.run_review()['status'])
    def test_wrong_crc(self):
        self.symbols[0]['crc'] = 8
        self.assertEqual('crc-mismatch', self.run_review()['issues'][0]['check'])
    def test_missing_namespace(self):
        self.modules[0]['metadata']['import_ns'] = []
        self.assertEqual('missing-namespace-import', self.run_review()['issues'][0]['check'])
    def test_vendor_vmlinux_cannot_replace_selected_gki(self):
        self.symbols[-1]['table'] = 'vendor/Module.symvers'
        self.assertEqual('missing-selected-provider', self.run_review()['issues'][0]['check'])
    def test_unselected_provider_cannot_satisfy_required_symbol(self):
        self.symbols[0]['owner'] = 'other'
        self.assertEqual('missing-selected-provider', self.run_review()['issues'][0]['check'])
    def test_ambiguous_crc_is_not_hidden_by_a_matching_candidate(self):
        other = copy.deepcopy(self.symbols[0]);other['crc'] += 1
        self.symbols.append(other)
        self.assertEqual('ambiguous-selected-provider', self.run_review()['issues'][0]['check'])
    def test_cycle(self):
        self.modules[1]['metadata']['depends'] = ['wlan']
        self.assertEqual('dependency-cycle', self.run_review()['issues'][-1]['check'])
    def test_duplicate_normalized_name(self):
        other=copy.deepcopy(self.modules[-1]);other['path']='other/audio_dsp.ko';self.modules.append(other)
        with self.assertRaises(ValueError):self.run_review()
    def test_vermagic_disagreement(self):
        self.modules[0]['metadata']['vermagic']=['other']
        self.assertEqual('vermagic', self.run_review()['issues'][0]['check'])
    def stamped(self, wlan, audio):
        self.modules[0]['metadata']['vermagic']=[wlan]
        self.modules[1]['metadata']['vermagic']=[audio]
        releases = dict(image='6.1.177-android14-11-g464c017656bd', vendor_base='6.1.177-android14-11',
                        vendor_commit='1711bb81f140ff2a59e55821b4a8356e62faaeba')
        return module.review(self.modules, self.symbols, {'wlan', 'audio_dsp'}, releases=releases)
    def test_stamped_vendor_and_image_releases_with_crcs_pass(self):
        rest = ' SMP preempt mod_unload modversions aarch64RANDSTRUCT_x'
        result = self.stamped('6.1.177-android14-11-g1711bb81f140' + rest, '6.1.177-android14-11-g464c017656bd' + rest)
        self.assertEqual('PASS', result['status'])
    def test_stamped_release_of_another_commit_is_refused(self):
        rest = ' SMP preempt mod_unload modversions aarch64RANDSTRUCT_x'
        result = self.stamped('6.1.177-android14-11-gdeadbeef0000' + rest, '6.1.177-android14-11-g464c017656bd' + rest)
        self.assertEqual('vermagic-release', result['issues'][0]['check'])
    def test_stamped_releases_with_different_flags_are_refused(self):
        result = self.stamped('6.1.177-android14-11-g1711bb81f140 SMP aarch64RANDSTRUCT_x',
                              '6.1.177-android14-11-g464c017656bd SMP aarch64RANDSTRUCT_y')
        self.assertEqual('vermagic', result['issues'][0]['check'])
    def test_vendor_release_needs_symbol_crcs(self):
        rest = ' SMP preempt mod_unload modversions aarch64RANDSTRUCT_x'
        self.modules[0]['required_symbols'] = []
        result = self.stamped('6.1.177-android14-11-g1711bb81f140' + rest, '6.1.177-android14-11-g464c017656bd' + rest)
        self.assertIn('vermagic-release-without-crcs', [i['check'] for i in result['issues']])
    def test_gpl_export_requires_compatible_license(self):
        self.modules[0]['metadata']['license']=['Proprietary']
        self.assertEqual('gpl-only-export-with-incompatible-module-license', self.run_review()['issues'][0]['check'])

    def test_unsigned_cannot_use_signed_protected_export(self):
        result=module.review(self.modules,self.symbols,{'wlan','audio_dsp'},
            protection={'gki_unprotected_symbols':['base'],'gki_protected_exports_symbols':['dsp']},signed=['audio_dsp'])
        self.assertIn('unsigned-access-to-protected-signed-module-symbol',[i.get('check') for i in result['issues']])
    def test_unsigned_cannot_export_protected_symbol(self):
        result=module.review(self.modules,self.symbols,{'wlan','audio_dsp'},
            protection={'gki_unprotected_symbols':['base'],'gki_protected_exports_symbols':['dsp']})
        self.assertIn('unsigned-protected-export',[i.get('check') for i in result['issues']])
    def test_allowlisted_signed_provider_is_accessible(self):
        result=module.review(self.modules,self.symbols,{'wlan','audio_dsp'},
            protection={'gki_unprotected_symbols':['base','dsp'],'gki_protected_exports_symbols':['dsp']},signed=['audio_dsp'])
        self.assertEqual('PASS',result['status'])

if __name__ == '__main__':unittest.main()

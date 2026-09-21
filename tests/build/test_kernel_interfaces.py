"""Selected module compatibility boundary fixtures."""
import copy
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

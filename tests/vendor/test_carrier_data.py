"""Data-only carrier extraction rejects ambiguity and unsafe archive names."""
from pathlib import Path
import hashlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from diamaneos_tools import carrier_data
from diamaneos_tools.vendor import VendorError


class CarrierDataTests(unittest.TestCase):
    DUMP = '''E: carrier_config_list (line=1)
  E: carrier_config (line=2)
    A: mcc="001" (Raw: "001")
    E: boolean (line=3)
      A: name="carrier_wfc_ims_available_bool" (Raw: "carrier_wfc_ims_available_bool")
      A: value=true
'''

    def extract(self, members, run_error=None):
        with tempfile.TemporaryDirectory() as directory:
            apk, tool = Path(directory) / 'carrier.apk', Path(directory) / 'aapt2'
            tool.write_bytes(b'synthetic tool fixture')
            with zipfile.ZipFile(apk, 'w') as archive:
                for name, value in members.items():
                    archive.writestr(name, value)
            result = subprocess.CompletedProcess([], 0, self.DUMP.encode(), b'')
            with patch.object(carrier_data.subprocess, 'run', return_value=result, side_effect=run_error):
                return carrier_data.extract(apk, tool)

    def test_only_active_xml_data_is_extracted(self):
        xml = b'<carrier_config><boolean name="synthetic_bool" value="true"/></carrier_config>'
        files, provenance = self.extract({
            'assets/carrier_config_mccmnc_001001.xml': xml,
            'assets/carrier_config_001001.xml': b'<carrier_config/>',
            'assets/carrier_config_carrierid_123_Test.xml': b'<!-- intentionally empty -->',
            'classes.dex': b'not included',
            'assets/satellite/fixture.dat': b'not included',
        })
        self.assertEqual(set(files), {'carrier_config_mccmnc_001001.xml',
                                    'carrier_config_carrierid_123_Test.xml',
                                    'vendor.xml', 'vendor_no_sim.xml'})
        self.assertEqual(files['carrier_config_mccmnc_001001.xml'], xml)
        self.assertIn(b'mcc="001"', files['vendor.xml'])
        self.assertEqual(provenance['files']['carrier_config_mccmnc_001001.xml']['member'],
                         'assets/carrier_config_mccmnc_001001.xml')
        self.assertEqual(provenance['unused_legacy_members'], ['assets/carrier_config_001001.xml'])

    def test_duplicate_carrier_identity_rejected(self):
        with self.assertRaises(VendorError):
            self.extract({'assets/carrier_config_carrierid_1_A.xml': '<carrier_config/>',
                          'assets/carrier_config_carrierid_1_B.xml': '<carrier_config/>'})

    def test_malformed_oversize_and_external_xml_rejected(self):
        for data in [b'<broken>', b'<wrong/>', b'<!DOCTYPE a><carrier_config/>',
                     '<carrier_config/>'.encode('utf-16'), b' ' * (carrier_data.MAX_FILE_BYTES + 1)]:
            with self.subTest(data=data[:40]), self.assertRaises(VendorError):
                self.extract({'assets/carrier_config_mccmnc_001001.xml': data})

    def test_path_escape_rejected(self):
        with self.assertRaises(VendorError):
            self.extract({'assets/carrier_config_carrierid_1_../../escape.xml': '<carrier_config/>'})

    def test_archive_and_decoder_failures_are_reported_as_input_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            bad = Path(directory) / 'truncated.apk'
            bad.write_bytes(b'PK')
            with self.assertRaises(VendorError):
                carrier_data.extract(bad, Path(directory) / 'unused-tool')
        with self.assertRaises(VendorError):
            self.extract({'assets/carrier_config_mccmnc_001001.xml': '<carrier_config/>'},
                         subprocess.TimeoutExpired('aapt2', 30))

    def test_unrecognized_aapt_syntax_rejected(self):
        for dump in ['', 'E: wrong (line=1)', self.DUMP + '  unsupported\n']:
            with self.subTest(dump=dump), self.assertRaises(VendorError):
                carrier_data.decode_xml_dump(dump)

    def test_empty_no_sim_repair_requires_exact_source_bytes(self):
        broken = b'<carrier_config_list><carrier_config_list/>'
        member = 'assets/carrier_config_no_sim.xml'
        with self.assertRaises(VendorError):
            self.extract({member: broken})
        digest = hashlib.sha256(broken).hexdigest()
        with patch.object(carrier_data, 'NO_SIM_REPAIR_SHA256', digest):
            files, report = self.extract({member: broken})
            self.assertIn(b'<carrier_config_list />', files['carrier_config_no_sim.xml'])
            self.assertEqual(report['repairs']['carrier_config_no_sim.xml']['source_sha256'], digest)
            with self.assertRaises(VendorError):
                self.extract({member: broken + b'changed'})

    def test_built_apk_must_contain_exact_derived_data(self):
        data = b'<carrier_config_list/>'
        record = {'carrier_data': {'files': {'vendor.xml': {
            'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}}}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'built.apk'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('assets/device-carrier-config/vendor.xml', data)
                archive.writestr('assets/carrier_config_no_sim.xml', b'<carrier_config/>')
            self.assertEqual(1, carrier_data.verify_apk(path, record))
            for malformed in ([], {'vendor.xml': 'not a record'}):
                with self.assertRaises(VendorError):
                    carrier_data.verify_apk(path, {'carrier_data': {'files': malformed}})
            for content in ({}, {'vendor.xml': data + b'changed'},
                            {'vendor.xml': data, 'unreviewed.xml': data}):
                with zipfile.ZipFile(path, 'w') as archive:
                    for name, value in content.items():
                        archive.writestr('assets/device-carrier-config/' + name, value)
                with self.assertRaises(VendorError):
                    carrier_data.verify_apk(path, record)


if __name__ == '__main__':
    unittest.main()

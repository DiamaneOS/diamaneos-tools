"""Real scratch bytes and controlled application results, no OTA crypto acceptance."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from diamaneos_tools import signing_native as native, signing_inputs as inputs
from .fixtures import write_zip


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.expected = self.root/'expected.img'; self.applied = self.root/'applied.img'

    def compare(self, a, b, *, canonical_payload=False):
        self.expected.write_bytes(a); self.applied.write_bytes(b)
        return native.ota_partition_comparison(self.expected, self.applied, canonical_payload=canonical_payload)

    def test_platform_firmware_rounding_hashes_all_expected_bytes_and_zero_tail(self):
        # Actual firmware lengths. Expected canonical length is independent of helper arithmetic.
        data = (bytes(range(256)) * 960)[:245560]
        canonical = data + bytes(200)
        result = self.compare(data, canonical, canonical_payload=True)
        self.assertTrue(result['matched']); self.assertTrue(result['padding_zero'])
        self.assertEqual(245760, result['canonical_expected_size'])
        self.assertEqual(200, result['padding_size'])
        self.assertEqual(hashlib.sha256(data).hexdigest(), result['expected_sha256'])
        self.assertEqual(hashlib.sha256(canonical).hexdigest(), result['canonical_expected_sha256'])
        self.assertEqual(result['canonical_expected_sha256'], result['applied_sha256'])
        self.assertEqual(245560, self.expected.stat().st_size)

    def test_canonicalization_refuses_nonzero_tail_wrong_rounding_and_content_changes(self):
        data = b'a' * 245560
        good = data + bytes(200)
        for bad in (data, good[:-1], good + bytes(4096), data + bytes(199) + b'x',
                    b'b' + good[1:], bytes(len(good))):
            with self.subTest(size=len(bad)):
                self.assertFalse(self.compare(data, bad, canonical_payload=True)['matched'])

    def test_aligned_image_rejects_any_extra_data_even_zero(self):
        data = b'a' * 4096
        self.assertTrue(self.compare(data, data, canonical_payload=True)['matched'])
        for tail in (bytes(1), bytes(4096), b'x'):
            self.assertFalse(self.compare(data, data + tail, canonical_payload=True)['matched'])

    def test_padding_crosses_stream_chunk_boundary_without_discarding_bytes(self):
        data = b'a' * (1024 * 1024 + 1)
        result = self.compare(data, data + bytes(4095), canonical_payload=True)
        self.assertTrue(result['matched']); self.assertEqual(4095, result['padding_size'])

    def test_equal_bytes_hash_the_complete_files_without_mismatch_samples(self):
        data = b'partition data' + bytes(8192)
        result = self.compare(data, data)
        self.assertTrue(result['matched']); self.assertIsNone(result['first_mismatch_offset'])
        self.assertEqual(hashlib.sha256(data).hexdigest(), result['expected_sha256'])
        self.assertEqual(result['expected_sha256'], result['applied_sha256'])
        self.assertEqual(len(data), result['matched_prefix_size']); self.assertEqual([], result['samples'])

    def test_zeroed_unused_block_is_still_a_failure_and_reports_the_extent(self):
        data = b'prefix' + b'not-approved-to-discard' + bytes(8192)
        result = self.compare(data, data[:6] + bytes(22) + data[28:])
        self.assertFalse(result['matched']); self.assertEqual(6, result['first_mismatch_offset'])
        self.assertEqual(result['expected_size'], result['applied_size'])
        self.assertTrue(any(x['matched'] for x in result['samples']))
        self.assertNotIn('not-approved-to-discard', json.dumps(result))

    def test_equal_prefix_with_extra_padding_or_truncation_is_not_accepted(self):
        data = b'firmware' + bytes(8192)
        for a,b in ((data,data+bytes(4096)),(data+bytes(4096),data)):
            with self.subTest(expected=len(a),applied=len(b)):
                result = self.compare(a,b)
                self.assertFalse(result['matched']); self.assertEqual(len(data),result['first_mismatch_offset'])
                self.assertEqual(len(data),result['matched_prefix_size'])

    def test_mismatch_after_the_first_megabyte_has_exact_offset(self):
        data=bytes(1024*1024)+b'expected'+bytes(8192)
        changed=bytes(1024*1024)+b'applied!'+bytes(8192)
        result=self.compare(data,changed)
        self.assertFalse(result['matched']); self.assertEqual(1024*1024,result['first_mismatch_offset'])
        self.assertLessEqual(len(result['samples']),3)
        self.assertTrue(all(x['size']<=4096 for x in result['samples']))

    def test_symlink_comparison_input_is_refused(self):
        self.expected.write_bytes(b'expected'); self.applied.symlink_to(self.expected.name)
        with self.assertRaises(OSError):native.ota_partition_comparison(self.expected,self.applied)


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve()
        self.tools=native.NativeTools.__new__(native.NativeTools);self.tools.scratch=self.root
        self.payload=self.root/'payload.bin';self.payload.write_bytes(b'controlled payload placeholder')
        self.target=self.root/'target.zip';self.calls=[];self.timeouts=[]

    def target_files(self, partitions):
        write_zip(self.target,{'META/ab_partitions.txt':'\n'.join(partitions),
                              **{'IMAGES/'+name+'.img':data for name,data in partitions.items()}})

    def apply(self, results, source=None, sparse=None):
        def run(tool,args,**kwargs):
            self.calls.append((tool,args));self.timeouts.append(kwargs.get('timeout'))
            if tool=='simg2img':
                # Explicit native-normalization response only. No sparse decoder is mirrored.
                Path(args[1]).write_bytes(sparse);return ''
            self.assertEqual('delta_generator',tool)
            values=dict(x[2:].split('=',1) for x in args)
            names=values['partition_names'].split(':');paths=values['new_partitions'].split(':')
            for name,path in zip(names,paths):Path(path).write_bytes(results[name])
            return ''
        self.tools.run=run
        return self.tools.apply_payload(self.payload,self.target,source)

    def test_multiple_partitions_success_and_unchanged_api_result(self):
        values={'vendor':b'vendor','boot':b'boot'};self.target_files(values)
        self.assertIsNone(self.apply({'vendor':b'vendor'+bytes(4090),'boot':b'boot'+bytes(4092)}))
        self.assertEqual('delta_generator',self.calls[0][0])
        self.assertFalse(any(p.is_dir() for p in self.root.iterdir()))

    def test_only_the_delta_apply_gets_the_longer_limit(self):
        values={'boot':b'boot'};self.target_files(values)
        source=self.root/'source.zip';write_zip(source,{'META/ab_partitions.txt':'boot','IMAGES/boot.img':b'old'})
        self.apply({'boot':b'boot'+bytes(4092)});self.apply({'boot':b'boot'+bytes(4092)},source)
        self.assertEqual([inputs.NATIVE_TIMEOUT,inputs.DELTA_APPLY_TIMEOUT],self.timeouts)
        self.assertEqual((1200,10800),(inputs.NATIVE_TIMEOUT,inputs.DELTA_APPLY_TIMEOUT))
        with patch.object(inputs.process,'run',return_value={'transport':'ok','stdout':b''}) as run:
            inputs.command(['tool']);inputs.command(['tool'],timeout=inputs.DELTA_APPLY_TIMEOUT)
        self.assertEqual([1200,10800],[call.args[1] for call in run.call_args_list])

    def test_failure_reports_all_partition_hashes_and_no_paths_or_image_data(self):
        values={'vendor':b'do-not-print-image-content','boot':b'boot'};self.target_files(values)
        with self.assertRaisesRegex(inputs.SigningError,'Applied OTA partitions differ') as error:
            self.apply({'vendor':b'wrong-image-reconstruction','boot':b'boot'+bytes(4092)})
        detail=json.loads(error.exception.diagnostic)
        self.assertEqual(1,detail['mismatch_count']);self.assertEqual(2,detail['partition_count'])
        rows={x['partition']:x for x in detail['partitions']}
        self.assertTrue(rows['boot']['matched']);self.assertFalse(rows['vendor']['matched'])
        self.assertEqual(hashlib.sha256(values['vendor']).hexdigest(),rows['vendor']['expected_sha256'])
        self.assertNotIn(str(self.root),error.exception.diagnostic)
        self.assertNotIn('do-not-print-image-content',error.exception.diagnostic)
        self.assertFalse(any(p.is_dir() for p in self.root.iterdir()))

    def test_target_swap_and_size_change_are_both_refused(self):
        values={'boot':b'boot'+b'x'*32,'vendor':b'vendor'};self.target_files(values)
        with self.assertRaises(inputs.SigningError) as error:self.apply({'boot':values['vendor'],'vendor':values['boot']})
        rows=json.loads(error.exception.diagnostic)['partitions']
        self.assertEqual(2,sum(not x['matched'] for x in rows))
        self.assertTrue(all(x['expected_size']!=x['applied_size'] for x in rows))

    def test_sparse_target_uses_existing_native_normalization_before_strict_hash(self):
        raw=b'normalized raw partition'+bytes(8192-24)
        self.target_files({'vendor':b'\x3a\xff\x26\xed' + b'controlled sparse input'})
        self.assertIsNone(self.apply({'vendor':raw},sparse=raw))
        self.assertEqual(['simg2img','delta_generator'],[x[0] for x in self.calls])

    def test_sparse_normalization_cannot_claim_firmware_padding(self):
        self.target_files({'vendor':b'\x3a\xff\x26\xed' + b'controlled sparse input'})
        with self.assertRaisesRegex(inputs.SigningError,'Sparse OTA normalization'):
            self.apply({'vendor':b'x'+bytes(4095)},sparse=b'x')
        self.assertEqual(['simg2img'],[x[0] for x in self.calls])

    def test_firmware_tail_mutation_remains_fatal_in_application(self):
        self.target_files({'abl':b'a'*245560})
        with self.assertRaises(inputs.SigningError) as error:
            self.apply({'abl':b'a'*245560+bytes(199)+b'x'})
        row=json.loads(error.exception.diagnostic)['partitions'][0]
        self.assertFalse(row['padding_zero']);self.assertEqual(245759,row['first_mismatch_offset'])

    def test_delta_source_inventory_mismatch_refuses_before_application(self):
        self.target_files({'boot':b'boot'})
        old=self.root/'old.zip';write_zip(old,{'META/ab_partitions.txt':'vendor','IMAGES/vendor.img':b'vendor'})
        with self.assertRaisesRegex(inputs.SigningError,'Delta partition inventory changed'):self.apply({'boot':b'boot'},old)
        self.assertEqual([],self.calls)

    def test_oversized_partition_inventory_refuses_before_application(self):
        self.target_files({'p'+str(i):b'x' for i in range(65)})
        with self.assertRaisesRegex(inputs.SigningError,'Invalid OTA partition inventory'):self.apply({})
        self.assertEqual([],self.calls)


if __name__=='__main__':unittest.main()

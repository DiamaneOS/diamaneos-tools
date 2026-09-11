"""Schema and cross-repository design validation; no native/network proof.

Ordinary tests use a local service fixture. The real deployment selection must
also pass `bin/diamaneos endpoints validate --services <explicit path>`.
"""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLS/'src'))
from diamaneos_tools import endpoints as api
from jsonschema import Draft7Validator


class ContractsTest(unittest.TestCase):
    def setUp(self):
        self.inv = api.load_json(TOOLS/'config/endpoints.json')
        self.services = api.load_json(Path(__file__).parent/'fixtures/services.json')

    def bad(self):
        self.assertTrue(api.validate_inventory(self.inv))

    def bad_services(self):
        self.assertTrue(api.validate_services(self.inv, self.services))

    def test_committed_inventory_and_fixture(self):
        self.assertEqual([], api.validate_inventory(self.inv))
        self.assertEqual([], api.validate_services(self.inv, self.services))

    def test_real_schemas_are_valid(self):
        for name in ['endpoint-contract.schema.json','services.schema.json']:
            Draft7Validator.check_schema(json.loads((TOOLS/'schemas'/name).read_text()))

    def test_required_fields_at_every_layer(self):
        # Deleting any required field must fail, not only selected string fields.
        original = copy.deepcopy(self.inv)
        for section in [None,'endpoints','providers','sources','limits_profiles','client_defaults','topology']:
            data = original if section is None else original[section]
            data = data[0] if isinstance(data,list) else data
            for key in data:
                with self.subTest(section=section,key=key):
                    self.inv = copy.deepcopy(original)
                    target = self.inv if section is None else self.inv[section]
                    if isinstance(target,list):target=target[0]
                    del target[key]
                    self.bad()

    def test_null_verification_root(self):
        self.inv['endpoints'][0]['verification_root'] = None
        self.bad()

    def test_numeric_consumer(self):
        self.inv['endpoints'][0]['consumer'] = 123
        self.bad()

    def test_path_character_bound(self):
        self.inv['endpoints'][0]['path_method'] = 'x'*1001
        self.bad()
        self.inv['endpoints'][0]['path_method'] = 'x'*1000
        self.assertEqual([],api.validate_inventory(self.inv))

    def test_malformed_ids_do_not_raise(self):
        for value in [[],{},None,12,True]:
            with self.subTest(value=value):
                self.inv['endpoints'][0]['id']=value
                self.bad()

    def test_malformed_provider_does_not_raise(self):
        self.inv['providers'][0]=None
        self.bad()

    def test_malformed_topology(self):
        for value in [None, {'hosts':[]}, {}]:
            self.inv['topology']=value
            self.bad()

    def test_missing_provider_candidate(self):
        del self.inv['providers'][0]['candidate']
        self.bad()

    def test_unknown_fields_rejected(self):
        self.inv['providers'][0]['unreviewed']=True
        self.bad()

    def test_empty_inventory_sections_rejected(self):
        for section in ['providers','sources','limits_profiles','client_defaults','endpoints']:
            with self.subTest(section=section):
                original=self.inv[section]
                self.inv[section]=[]
                self.bad()
                self.inv[section]=original

    def test_duplicate_and_missing_endpoint(self):
        self.inv['endpoints'][-1]=copy.deepcopy(self.inv['endpoints'][0])
        self.bad()

    def test_mislabelled_host_rejected(self):
        self.inv['endpoints'][0]['upstream_host'],self.inv['endpoints'][1]['upstream_host']=self.inv['endpoints'][1]['upstream_host'],self.inv['endpoints'][0]['upstream_host']
        self.bad()

    def test_missing_source_or_limits(self):
        self.inv['endpoints'][0]['source_refs']=['not-present']
        self.bad()
        self.setUp()
        self.inv['endpoints'][0]['limits_profile']='not-present'
        self.bad()

    def test_source_cannot_escape_repository(self):
        self.inv['sources'][0]['path']='../secrets'
        self.bad()

    def test_blocker_status_consistent(self):
        self.inv['endpoints'][0]['implementation_blockers']=[]
        self.bad()
        self.setUp()
        self.inv['endpoints'][0]['status']='specified'
        self.bad()
        self.setUp()
        self.inv['endpoints'][0]['protocol_status']='specified'
        self.bad()

    def test_shared_blocker_cannot_have_two_owners(self):
        self.inv['endpoints'][3]['implementation_blockers'][0]['owner_task']='FP6-111'
        self.bad()

    def test_unapproved_domain_and_jurisdiction(self):
        self.inv['endpoints'][0]['replacement_host']='releases.grapheneos.org'
        self.bad()
        self.setUp()
        self.inv['endpoints'][1]['jurisdiction']='eu-primary'
        self.bad()
        self.setUp()
        self.inv['client_defaults'][0]['jurisdiction']='eu-primary'
        self.bad()

    def test_zero_negative_and_boolean_limits(self):
        for value in [0,-1,True,1.5,'60',None]:
            self.inv['limits_profiles'][0]['deadline_seconds']=value
            self.bad()

    def test_no_unbounded_retry_queue_or_redirect(self):
        for key in ['attempts','max_queued','redirects','access_log_retention_seconds']:
            old=self.inv['limits_profiles'][0][key]
            self.inv['limits_profiles'][0][key]=old+1
            self.bad()
            self.inv['limits_profiles'][0][key]=old

    def test_time_and_refresh_relationships(self):
        self.inv['limits_profiles'][0]['idle_seconds']=3601
        self.bad()
        self.setUp()
        self.inv['limits_profiles'][0]['refresh_seconds']=86401
        self.bad()

    def test_secret_url_rejected_across_whole_envelope(self):
        sensitive='https://user:sentinel-secret@example.invalid/'
        for where in ['note','candidate','description','data']:
            self.setUp()
            if where=='note':self.inv['note']=sensitive
            elif where=='candidate':self.inv['providers'][0][where]=sensitive
            elif where=='description':self.inv['endpoints'][0]['implementation_blockers'][0][where]=sensitive
            else:self.inv['endpoints'][1]['upstreams'][0][where]=sensitive
            errors=api.validate_inventory(self.inv)
            self.assertTrue(errors)
            self.assertNotIn('sentinel-secret',' '.join(errors))

    def test_schema_errors_never_echo_input(self):
        self.inv['endpoints'][0]['id']='sentinel-private-value'
        self.assertNotIn('sentinel-private-value',' '.join(api.validate_inventory(self.inv)))

    def test_missing_schema_dependency_fails_closed(self):
        with patch.object(api,'Draft7Validator',None):
            self.assertIn('missing jsonschema',api.validate_inventory(self.inv)[0])

    def test_empty_or_malformed_services(self):
        for data in [None,{}, {'hosts':[],'services':[]}]:
            self.assertTrue(api.validate_services(self.inv,data))
        for key in ['hosts','services']:
            saved=self.services[key]
            self.services[key]=[]
            self.bad_services()
            self.services[key]=saved

    def test_missing_host_mirror_or_endpoint_ref(self):
        for key in ['host','mirror','endpoints']:
            self.setUp()
            self.services['services'][0][key]=['missing'] if key=='endpoints' else 'missing'
            self.bad_services()

    def test_duplicate_assignment_or_host(self):
        self.services['services'].append(copy.deepcopy(self.services['services'][0]))
        self.bad_services()
        self.setUp()
        self.services['hosts'].append(copy.deepcopy(self.services['hosts'][0]))
        self.bad_services()

    def test_community_cannot_serve_or_administer_release(self):
        self.services['services'][0]['host']='community-future'
        self.bad_services()
        self.setUp()
        self.services['hosts'][-1]['credential_class']='release-admin'
        self.bad_services()
        self.setUp()
        self.services['management_policy']['community_can_administer_release']=True
        self.bad_services()

    def test_management_path_and_operator_independence(self):
        self.services['hosts'][0]['management']='separate-community-path'
        self.bad_services()
        self.setUp()
        self.inv['providers'][3]['candidate']=self.inv['providers'][2]['candidate']
        self.bad_services()

    def test_dns_has_authoritative_role_not_web_host(self):
        next(s for s in self.services['services'] if s['id']=='dns-check')['host']='release-primary'
        self.bad_services()

    def test_service_owner_and_mirror_scope(self):
        self.services['services'][0]['owner_task']='FP6-101'
        self.bad_services()
        self.setUp()
        self.services['services'][4]['mirror']='artifact-mirror-non-eu'
        self.bad_services()

    def test_activation_cannot_be_claimed_by_design(self):
        self.services['services'][0]['activation']='active'
        self.bad_services()

    def test_cli_uses_explicit_paths_and_reports_scope(self):
        command=[sys.executable,str(TOOLS/'bin/diamaneos'),'endpoints','validate']
        with tempfile.TemporaryDirectory() as temp:
            out=subprocess.run(command,cwd=temp,capture_output=True,text=True,timeout=30)
            self.assertEqual(0,out.returncode,out.stderr)
            self.assertIn('integration not checked',out.stdout)
            path=Path(temp)/'services.json';path.write_text(json.dumps(self.services))
            out=subprocess.run(command+['--services',str(path)],cwd=temp,capture_output=True,text=True,timeout=30)
            self.assertEqual(0,out.returncode,out.stderr)
            self.assertIn('explicit service selection',out.stdout)
            path.write_text('{"schema_version":2,"schema_version":2}')
            out=subprocess.run(command+['--services',str(path)],cwd=temp,capture_output=True,text=True,timeout=30)
            self.assertEqual(2,out.returncode)
            self.assertNotIn('Traceback',out.stderr)

    def test_concrete_http_success_examples(self):
        # Wire fixture constraints, not a Python reimplementation of native clients.
        by_id={e['id']:e for e in self.inv['endpoints']}
        for eid in ['time','connectivity-check','online-probe']:
            headers,body=by_id[eid]['example']['response'].split('\r\n\r\n',1)
            self.assertEqual('',body)
            self.assertTrue(headers.startswith('HTTP/1.1 204 No Content\r\n'))
            self.assertIn('Cache-Control: no-store',headers)
        self.assertIn('X-Time: 1789084800123\r\n',by_id['time']['example']['response'])
        self.assertNotIn('X-Time',by_id['online-probe']['example']['response'])


class JsonBoundaryTest(unittest.TestCase):
    def test_duplicate_keys_and_invalid_json(self):
        for data in [b'{"a":1,"a":2}', b'{"x":{"b":1,"b":2}}',b'NaN',b'Infinity',b'-Infinity',b'\xff',b'{',b'1e999',b'"\\ud800"']:
            with self.subTest(data=data):
                with self.assertRaises(api.ContractError):api.loads(data)

    def test_byte_exact_limit_with_unicode(self):
        # 2 quotes + 2 ASCII bytes + 4 bytes per emoji equals the exact cap.
        data=('"aa'+'\U0001f642'*((api.MAX_FILE_BYTES-4)//4)+'"').encode('utf-8')
        self.assertEqual(api.MAX_FILE_BYTES,len(data))
        self.assertIsInstance(api.loads(data),str)
        with self.assertRaises(api.ContractError):api.loads(data+b' ')

    def test_depth_nodes_cycles_nonjson_and_nonfinite(self):
        nested=0
        for _ in range(api.MAX_DEPTH+1):nested=[nested]
        cyclic=[];cyclic.append(cyclic)
        for value in [nested, [0]*(api.MAX_NODES+1), cyclic, {1:'value'}, set(), float('nan')]:
            with self.subTest(kind=type(value).__name__):
                self.assertTrue(api.validate_inventory(value))

    def test_file_size_checked_before_decode(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'oversized.json';p.write_bytes(b' '*(api.MAX_FILE_BYTES+1))
            with self.assertRaisesRegex(api.ContractError,'byte limit'):api.load_json(p)
            with self.assertRaisesRegex(api.ContractError,'unable to read'):api.load_json(p.parent/'absent')

    def test_private_material_is_not_echoed(self):
        for value in [{'password':'sentinel'}, {'x':'-----BEGIN PRIVATE KEY-----\nsentinel'}, {'x':'https://owner:sentinel@example.invalid'}]:
            with self.assertRaises(api.ContractError) as ctx:api.loads(json.dumps(value).encode())
            self.assertNotIn('sentinel',str(ctx.exception))


if __name__=='__main__':unittest.main()

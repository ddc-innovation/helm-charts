"""Storage scope, authenticated SQL, policy reconciliation and Job wiring tests."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml
from values_check import load, merge, check
from test_managed_chart import FakeAPI, authorize

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('storage', ROOT / 'scripts/manage-storage.py')
storage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(storage)


class StorageTests(unittest.TestCase):
    def values(self):
        return merge(load(ROOT / 'values.yaml'), load(ROOT / 'examples/pnt-storage.yaml'))

    def test_sql_maps_exact_buckets_and_rejects_injection_and_duplicates(self):
        v = self.values()
        sql = storage.schema_sql(v)
        self.assertIn("hive.pnt_lake WITH (location = 's3a://pnt-lake/')", sql)
        self.assertIn("hive.pnt_artifacts WITH (location = 's3a://pnt-artifacts/')", sql)
        for invalid in ["x';DROP SCHEMA hive.default;--", 'pnt_lake']:
            bad = copy.deepcopy(v)
            if invalid == 'pnt_lake':
                bad['hive']['schemas'].append(copy.deepcopy(bad['hive']['schemas'][0]))
            else:
                bad['hive']['schemas'][0]['bucket'] = invalid
            with self.assertRaises(ValueError):
                storage.schema_sql(bad)
        bad = copy.deepcopy(v)
        bad['objectStorage']['additionalBuckets'].append('pnt-lake')
        self.assertTrue(check(bad, 'offline'))

    def test_additional_ranger_grants_preserve_defaults_and_repeat_without_writes(self):
        v = self.values()
        settings = v['ranger']['authorization']
        self.assertEqual(settings['grants'], load(ROOT / 'values.yaml')['ranger']['authorization']['grants'])
        authorize.validate(settings)
        api = FakeAPI()
        authorize.reconcile(api, settings)
        for schema in ['pnt_lake', 'pnt_artifacts']:
            policies = [p for p in api.policies.values() if p['resources'].get('schema', {}).get('values') == [schema]]
            self.assertEqual(len(policies), 3)
            for p in policies:
                self.assertFalse(any('trino-healthcheck' in item['users'] for item in p['policyItems']))
        api.writes = 0
        authorize.reconcile(api, settings)
        self.assertEqual(api.writes, 0)
        settings['additionalGrants'][0]['users'] = ['undeclared-user']
        with self.assertRaises(ValueError):
            authorize.validate(settings)

    def run_initializer(self, directory, buckets='pnt-lake pnt-artifacts', attach_failed=False):
        d = Path(directory)
        (d / 'admin').mkdir(exist_ok=True)
        (d / 'app').mkdir(exist_ok=True)
        for folder, name, value in [('admin', 'MINIO_ROOT_USER', 'admin'), ('admin', 'MINIO_ROOT_PASSWORD', 'admin-secret'), ('app', 'accessKey', 'app'), ('app', 'secretKey', 'app-secret')]:
            (d / folder / name).write_text(value)
        fake = d / 'mc'
        fake.write_text('''#!/usr/bin/env python3
import json,os,pathlib,sys
args=sys.argv[1:]
d=pathlib.Path(os.environ['FAKE_ROOT'])
with (d/'calls').open('a') as f:f.write(json.dumps(args)+'\\n')
if args[:3]==['admin','policy','create']:
    (d/'policy.json').write_text(pathlib.Path(args[-1]).read_text())
if args[:3]==['admin','policy','attach'] and os.environ.get('FAIL_ATTACH'):
    sys.exit(1)
if args[:3]==['admin','user','info'] and '--json' in args:
    print(json.dumps({'policyName':'bdv-warehouse' if os.environ.get('FAIL_ATTACH') else 'bdv-warehouse,bdv-additional-buckets'}))
''')
        fake.chmod(0o755)
        env = dict(os.environ, PATH=str(d)+':'+os.environ['PATH'], FAKE_ROOT=str(d),
                   MINIO_ADMIN_DIR=str(d/'admin'), MINIO_APP_DIR=str(d/'app'),
                   S3_ENDPOINT='http://bdv-minio:9000', S3_BUCKET='bdv-warehouse',
                   S3_REGION='us-east-1', S3_ADDITIONAL_BUCKETS=buckets)
        if attach_failed:
            env['FAIL_ATTACH']='1'
        return subprocess.run(['sh', str(ROOT/'files/minio-additional-buckets.sh')], env=env, text=True, capture_output=True)

    def test_minio_scope_credentials_and_repeat(self):
        with tempfile.TemporaryDirectory() as directory:
            for _ in range(2):
                result = self.run_initializer(directory)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn('app-secret', result.stdout+result.stderr)
            d = Path(directory)
            policy = json.loads((d/'policy.json').read_text())
            self.assertEqual(policy['Statement'][0]['Resource'], ['arn:aws:s3:::pnt-lake','arn:aws:s3:::pnt-artifacts'])
            self.assertEqual(policy['Statement'][1]['Resource'], ['arn:aws:s3:::pnt-lake/*','arn:aws:s3:::pnt-artifacts/*'])
            calls = (d/'calls').read_text()
            self.assertNotIn('app-secret', calls)
            self.assertNotIn('admin-secret', calls)
            self.assertNotIn('"bdv-warehouse",', calls)

    def test_invalid_input_stops_before_mutation_and_failed_attach_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_initializer(directory, buckets='pnt-lake bad;name')
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((Path(directory)/'calls').exists())
            result = self.run_initializer(directory, attach_failed=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('association', result.stderr)

    @unittest.skipUnless(os.environ.get('HELM'), 'Set HELM to test real Helm rendering')
    def test_helm_job_mounts_script_and_keeps_pinned_initializer(self):
        args = [os.environ['HELM'], 'template', 'bdv', str(ROOT), '-n', 'bdv',
                '-f', str(ROOT/'values-install.yaml'), '-f', str(ROOT/'examples/pnt-storage.yaml'),
                '--set', 'deployment.enabled=true', '--set', 'deployment.evidenceConfirmed=true',
                '--set', 'deployment.proxyBoundaryConfirmed=true',
                '--set-string', 'ranger.installPropertiesTemplate=TEST-FIXTURE-NOT-FOR-INSTALL']
        docs = [d for d in yaml.safe_load_all(subprocess.check_output(args, text=True)) if d]
        job = next(d for d in docs if d['kind']=='Job' and d['metadata']['name'].startswith('bdv-buckets-'))
        c = job['spec']['template']['spec']['containers'][0]
        self.assertEqual(c['args'][2], '/usr/local/bin/bdv-minio-init')
        self.assertEqual(c['args'][0].splitlines()[0], '"$@"')
        self.assertIn('@sha256:', c['image'])
        self.assertTrue(any(m['name']=='storage-init' for m in c['volumeMounts']))
        self.assertEqual(next(e['value'] for e in c['env'] if e['name']=='S3_ADDITIONAL_BUCKETS'), 'pnt-lake pnt-artifacts')
        desired = json.loads(next(d for d in docs if d['kind']=='ConfigMap' and d['metadata']['name']=='bdv-authorization')['data']['desired.json'])
        self.assertEqual(len(desired['additionalGrants']), 6)


if __name__ == '__main__':
    unittest.main()

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml
from test_managed_chart import FakeAPI, authorize
from values_check import check

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('fresh',ROOT/'scripts/prepare-fresh-test.py')
fresh=importlib.util.module_from_spec(spec); spec.loader.exec_module(fresh)


class FreshInstallTests(unittest.TestCase):
    def test_test_database_client_and_policy_isolation(self):
        v,c=fresh.build()
        self.assertEqual(v['bootstrap']['hive']['database'],'bdv_test_hive')
        self.assertEqual(v['bootstrap']['ranger']['username'],'bdv_test_ranger')
        self.assertFalse(v['storage']['existingMinioClaim'])
        self.assertEqual(v['objectStorage']['bucket'],'bdv-test-warehouse')
        self.assertEqual(v['keycloak']['clientId'],c['keycloak_realm_clients'][0]['client_id'])
        self.assertEqual(c['keycloak_realm_clients'][0]['redirect_uris'],['https://bdv-test.dinecodetalkers.com/oauth2/callback'])
        api=FakeAPI()
        with contextlib.redirect_stdout(io.StringIO()):
            authorize.reconcile(api,v['ranger']['authorization'])
        self.assertEqual(api.service['name'],'bdv-test-trino')
        self.assertTrue(all(p['service']=='bdv-test-trino' for p in api.policies.values()))
        self.assertEqual(check(v,'bootstrap'),[])

    @unittest.skipUnless(os.environ.get('HELM'),'Set HELM for actual rendering')
    def test_six_worker_fresh_render(self):
        v,_=fresh.build()
        v['deployment'].update(enabled=True,evidenceConfirmed=True,domainsConfirmed=True,proxyBoundaryConfirmed=True)
        v['ingress']['enabled']=True
        v['ranger']['installPropertiesTemplate']='TEST-FIXTURE-NOT-FOR-INSTALL'
        self.assertEqual(check(v,'runtime'),[])
        with tempfile.NamedTemporaryFile(mode='w',suffix='.yaml') as f:
            yaml.safe_dump(v,f);f.flush()
            output=subprocess.check_output([os.environ['HELM'],'template','bdv',str(ROOT),'-n','bdv-test','-f',f.name],text=True)
        docs=[d for d in yaml.safe_load_all(output) if d]
        self.assertFalse(any(d['kind'].startswith('Cluster') for d in docs))
        hpa=next(d for d in docs if d['kind']=='HorizontalPodAutoscaler')
        self.assertEqual((hpa['spec']['minReplicas'],hpa['spec']['maxReplicas']),(6,6))
        catalog=next(d for d in docs if d['metadata']['name']=='bdv-managed-catalog')
        self.assertIn('http://bdv-minio:9000',catalog['data']['hive.properties'])
        auth=next(d for d in docs if d['metadata']['name']=='bdv-authorization')
        self.assertEqual(json.loads(auth['data']['desired.json'])['serviceName'],'bdv-test-trino')


if __name__=='__main__':unittest.main()

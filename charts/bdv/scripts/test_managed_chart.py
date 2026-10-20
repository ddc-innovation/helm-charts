#!/usr/bin/env python3
"""Regression tests for additive policy adoption and managed worker contracts."""
import copy
import contextlib
import io
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import unittest
import yaml
import xml.etree.ElementTree as ET
from values_check import load, merge, check

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('authorize', ROOT / 'files/ranger-authorize.py')
authorize = importlib.util.module_from_spec(spec)
spec.loader.exec_module(authorize)


class FakeAPI:
    def __init__(self):
        self.definition = None
        self.service = None
        self.users = {}
        self.policies = {}
        self.writes = 0

    def __call__(self, method, path, document=None, missing=False):
        if method in ('POST', 'PUT'):
            self.writes += 1
        if '/servicedef' in path:
            if method != 'GET':
                self.definition = copy.deepcopy(document)
                self.definition['id'] = 203
            return copy.deepcopy(self.definition)
        if '/xusers/users' in path:
            if method == 'POST':
                self.users[document['name']] = copy.deepcopy(document)
                return document
            return copy.deepcopy(self.users.get(authorize.urllib.parse.unquote(path.rsplit('/', 1)[-1])))
        if path.endswith('/policy') and '/service/' in path and method == 'GET':
            return copy.deepcopy(list(self.policies.values()))
        if path.startswith('/service/public/v2/api/policy'):
            if method == 'POST':
                document = copy.deepcopy(document)
                document['id'] = max(self.policies, default=0) + 1
                self.policies[document['id']] = document
                return copy.deepcopy(document)
            ident = int(path.rsplit('/', 1)[-1])
            if method == 'PUT':
                self.policies[ident] = copy.deepcopy(document)
            return copy.deepcopy(self.policies[ident])
        if '/service/name/' in path or (path.endswith('/service') and method == 'POST'):
            if method == 'POST':
                self.service = copy.deepcopy(document)
            return copy.deepcopy(self.service)
        raise AssertionError((method, path))


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.settings = load(ROOT / 'values.yaml')['ranger']['authorization']

    def test_fresh_then_repeat_has_no_writes(self):
        api = FakeAPI()
        authorize.reconcile(api, self.settings)
        first = copy.deepcopy(api.policies)
        api.writes = 0
        authorize.reconcile(api, self.settings)
        self.assertEqual(api.writes, 0)
        self.assertEqual(api.policies, first)

    def test_same_resource_adopts_existing_name_and_preserves_other_users(self):
        api = FakeAPI()
        api.policies[41] = {
            'id': 41, 'name': 'existing-query-policy', 'service': 'bdv-trino',
            'policyType': 0, 'isEnabled': True,
            'resources': {'queryid': {'values': ['*']}},
            'policyItems': [{'users': ['existing-user'], 'accesses': [{'type': 'execute', 'isAllowed': True}]}],
        }
        authorize.reconcile(api, self.settings)
        self.assertEqual(api.policies[41]['name'], 'existing-query-policy')
        self.assertTrue(authorize.covered(api.policies[41], 'existing-user', ['execute']))
        self.assertTrue(authorize.covered(api.policies[41], 'richard.stevens@ddc-dine.com', ['execute']))
        self.assertEqual(sum('queryid' in p['resources'] for p in api.policies.values()), 1)

    def test_self_impersonation_never_crosses_users(self):
        api = FakeAPI()
        authorize.reconcile(api, self.settings)
        for policy in api.policies.values():
            if 'trinouser' in policy['resources']:
                target = policy['resources']['trinouser']['values'][0]
                self.assertEqual(policy['policyItems'][0]['users'], [target])

    def test_empty_root_parent_is_equivalent_to_null(self):
        api = FakeAPI()
        authorize.definition(api, self.settings)
        for resource in api.definition['resources']:
            if not resource.get('parent'):
                resource['parent'] = ''
        api.writes = 0
        authorize.definition(api, self.settings)
        self.assertEqual(api.writes, 0)

    def test_unknown_or_wildcard_identity_is_rejected(self):
        self.settings['users'].append('*')
        with self.assertRaises(ValueError):
            authorize.validate(self.settings)

    def test_helm_omitted_null_parent(self):
        for contract in self.settings['resourceParents'].values():
            if contract.get('parent') is None:
                contract.pop('parent', None)
        authorize.reconcile(FakeAPI(), self.settings)

    def test_removing_values_is_not_an_implicit_revocation(self):
        api = FakeAPI()
        authorize.reconcile(api, self.settings)
        self.settings['grants'] = []
        before = copy.deepcopy(api.policies)
        authorize.reconcile(api, self.settings)
        self.assertEqual(api.policies, before)


class RenderTests(unittest.TestCase):
    def test_memory_and_hpa_guards(self):
        values = merge(load(ROOT / 'values.yaml'), load(ROOT / 'values-install.yaml'))
        values['deployment'].update(evidenceConfirmed=True, proxyBoundaryConfirmed=True)
        self.assertEqual(check(values, 'runtime'), [])
        values['bdv']['autoscaling']['maxReplicas'] = 1
        values['bdv']['jvm']['maxRAMPercentage'] = 40
        self.assertGreaterEqual(len(check(values, 'runtime')), 2)

    @unittest.skipUnless(os.environ.get('HELM'), 'Set HELM to run actual Helm rendering')
    def test_render_workers_env_hpa_and_memory(self):
        cmd = [os.environ['HELM'], 'template', 'bdv', str(ROOT), '-n', 'bdv',
               '-f', str(ROOT / 'values-install.yaml'), '--set', 'deployment.enabled=true',
               '--set', 'deployment.evidenceConfirmed=true', '--set', 'deployment.proxyBoundaryConfirmed=true',
               '--set', 'bdv.workerShutdown.enabled=true',
               '--set-string', 'ranger.installPropertiesTemplate=TEST-FIXTURE-NOT-FOR-INSTALL',
               '--set', 'bdv.workerResources.limits.memory=8Gi']
        docs = list(yaml.safe_load_all(subprocess.check_output(cmd, text=True)))
        docs = [d for d in docs if d]
        hpa = next(d for d in docs if d['kind'] == 'HorizontalPodAutoscaler')
        self.assertEqual((hpa['spec']['minReplicas'], hpa['spec']['maxReplicas']), (2, 6))
        self.assertEqual(hpa['spec']['behavior']['scaleDown']['selectPolicy'], 'Disabled')
        worker = next(d for d in docs if d['kind'] == 'Deployment' and d['metadata']['name'] == 'bdv-worker')
        self.assertNotIn('replicas', worker['spec'])
        main = worker['spec']['template']['spec']['containers'][0]
        self.assertEqual(worker['spec']['template']['spec']['terminationGracePeriodSeconds'], 900)
        self.assertEqual(main['lifecycle']['preStop']['exec']['command'],
                         ['/usr/bin/bash', '/etc/trino/worker-drain.sh', 'shutdown-wait', '840'])
        self.assertEqual(main['readinessProbe']['exec']['command'][-1], 'ready')
        coordinator = next(d for d in docs if d['kind'] == 'Deployment' and d['metadata']['name'] == 'bdv-coordinator')
        self.assertNotIn('lifecycle', coordinator['spec']['template']['spec']['containers'][0])
        self.assertEqual(main['resources']['limits']['memory'], '8Gi')
        names = {v['name'] for v in main['env']}
        self.assertIn('BDV_INTERNAL_SHARED_SECRET', names)
        self.assertIn('BDV_S3_SECRET_KEY', names)
        self.assertNotIn('BDV_OIDC_CLIENT_SECRET', names)
        config = next(d for d in docs if d['metadata']['name'] == 'bdv-managed-worker')['data']
        self.assertEqual(config['worker-drain.sh'], (ROOT / 'files/worker-drain.sh').read_text().strip())
        self.assertIn('-XX:MaxRAMPercentage=65', config['jvm.config'])
        self.assertNotIn('-Xmx', config['jvm.config'])
        self.assertIn(str(8 * 1073741824 * 30 // 100) + 'B', config['config.properties'])
        self.assertIn('${ENV:BDV_INTERNAL_SHARED_SECRET}', config['config.properties'])
        catalog = next(d for d in docs if d['metadata']['name'] == 'bdv-managed-catalog')['data']['hive.properties']
        self.assertIn('${ENV:BDV_S3_SECRET_KEY}', catalog)
        plugin = next(d for d in docs if d['metadata']['name'] == 'bdv-managed-plugin')['data']
        for key, content in plugin.items():
            if key.endswith('.xml'):
                self.assertTrue(content.startswith('<?xml'), key)
                ET.fromstring(content)
        authorization = next(d for d in docs if d['metadata']['name'] == 'bdv-authorization')
        with contextlib.redirect_stdout(io.StringIO()):
            authorize.reconcile(FakeAPI(), json.loads(authorization['data']['desired.json']))


if __name__ == '__main__':
    unittest.main()

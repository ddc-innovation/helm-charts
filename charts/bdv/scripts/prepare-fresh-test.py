#!/usr/bin/env python3
"""Prepare a namespace-isolated, six-worker fresh install. No cluster changes."""
import copy
from pathlib import Path
import sys
import yaml
from values_check import load, merge

ROOT = Path(__file__).resolve().parents[1]


def build():
    v = merge(load(ROOT / 'values.yaml'), load(ROOT / 'values-install.yaml'))
    v['deployment'].update(enabled=False, evidenceConfirmed=False,
                           domainsConfirmed=False, proxyBoundaryConfirmed=False)
    v['ingress'].update(enabled=False, bdvHost='bdv-test.dinecodetalkers.com',
                        rangerHost='bdv-test-policy-manager.dinecodetalkers.com')
    for role in ('hive', 'ranger'):
        v['bootstrap'][role].update(database='bdv_test_' + role, username='bdv_test_' + role)
    v['storage'].update(minioSize='20Gi', existingMinioClaim='')
    v['objectStorage']['bucket'] = 'bdv-test-warehouse'
    v['keycloak']['clientId'] = 'bdv-test'
    v['bdv']['workers'] = 6
    v['bdv']['autoscaling'].update(enabled=True, minReplicas=6, maxReplicas=6, scaleDownEnabled=False)
    v['bdv']['workerShutdown'].update(enabled=True, terminationGracePeriodSeconds=900, waitSeconds=840)
    worker = v['configuration']['files']['worker']
    worker['config.properties'] += '\nshutdown.grace-period=120s\n'
    auth = v['ranger']['authorization']
    auth['serviceName'] = 'bdv-test-trino'
    auth['serviceConfigs']['jdbc.url'] = 'jdbc:trino://bdv-coordinator.bdv-test.svc.cluster.local:8080'
    auth['smokeCreator'] = 'richard.stevens@ddc-dine.com'
    humans = ['richard.stevens@ddc-dine.com', 'michael.peterson@ddc-dine.com']
    auth['grants'] += [
        {'name': 'bdv-test-system-catalog', 'resources': {'catalog': ['system']},
         'users': humans, 'accesses': ['use']},
        {'name': 'bdv-test-runtime-nodes',
         'resources': {'catalog': ['system'], 'schema': ['runtime'], 'table': ['nodes'],
                       'column': ['node_id', 'coordinator', 'state']},
         'users': humans, 'accesses': ['select']},
        {'name': 'bdv-test-read-system-information', 'resources': {'sysinfo': ['*']},
         'users': humans, 'accesses': ['read_sysinfo']},
    ]
    client = copy.deepcopy(load(ROOT / 'examples/keycloak-bdv-client.yml'))
    c = client['keycloak_realm_clients'][0]
    c.update(client_id='bdv-test', name='BDV test', description='Isolated BDV six-worker acceptance test',
             root_url='https://bdv-test.dinecodetalkers.com', admin_url='https://bdv-test.dinecodetalkers.com',
             redirect_uris=['https://bdv-test.dinecodetalkers.com/oauth2/callback'],
             web_origins=['https://bdv-test.dinecodetalkers.com'])
    c['attributes']['post.logout.redirect.uris'] = 'https://bdv-test.dinecodetalkers.com/ui/logout/logout.html'
    for mapper in c['protocol_mappers']:
        if mapper.get('protocolMapper') == 'oidc-audience-mapper':
            mapper['name'] = 'bdv-test-audience'
            mapper['config']['included.client.audience'] = 'bdv-test'
    return v, client


def main():
    values_path = ROOT / 'values-test.yaml'
    client_path = ROOT / 'examples/keycloak-bdv-test-client.yml'
    if values_path.exists() or client_path.exists():
        raise SystemExit('Refusing to overwrite existing fresh-test files')
    v, client = build()
    for path, document in ((values_path, v), (client_path, client)):
        path.write_text(yaml.safe_dump(document, sort_keys=False))
        path.chmod(0o600)
        print('Prepared:', path)
    print('Namespace: bdv-test; release: bdv; databases/users: bdv_test_hive, bdv_test_ranger')
    print('Six-worker capacity test: HPA min=6/max=6; source namespace is not accessed')
    print('No Secrets copied or created, no cluster or Keycloak changes performed')


if __name__ == '__main__':
    main()

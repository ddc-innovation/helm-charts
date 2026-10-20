#!/usr/bin/env python3
"""Create missing retained credential Secrets once. Existing values never rotate."""
import argparse
import base64
import getpass
import json
import secrets
import subprocess
import sys
from values_check import load, merge
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('-f', '--values', required=True)
    p.add_argument('-n', '--namespace', default='bdv')
    p.add_argument('--create', action='store_true')
    args = p.parse_args()
    v = merge(load(Path(__file__).resolve().parents[1] / 'values.yaml'), load(args.values))
    b = v['bootstrap']
    wanted = {
        b['adminSecret']: ['username', 'password'],
        v['ranger']['credentialsSecret']: ['rangerAdmin_password', 'rangerTagsync_password',
                                         'rangerUsersync_password', 'keyadmin_password'],
        v['objectStorage']['credentialsSecret']: ['accessKey', 'secretKey'],
        v['bdv']['internalSecret']: ['sharedSecret'],
    }
    if v['minio']['enabled']:
        wanted[v['minio']['adminSecret']] = ['MINIO_ROOT_USER', 'MINIO_ROOT_PASSWORD']
    if v['bdv']['oauth2']['enabled']:
        wanted[v['bdv']['keycloakSecret']] = ['issuer', 'clientId', 'clientSecret']
    names = [b['adminSecret'], v['ranger']['credentialsSecret'],
             v['objectStorage']['credentialsSecret'], v['bdv']['internalSecret']]
    if v['minio']['enabled']:
        names.append(v['minio']['adminSecret'])
    if v['bdv']['oauth2']['enabled']:
        names.append(v['bdv']['keycloakSecret'])
    if '' in names or len(names) != len(set(names)):
        raise ValueError('Distinct nonempty credential Secret names required')
    missing = []
    for name, keys in wanted.items():
        raw = subprocess.check_output([
            'kubectl', '-n', args.namespace, 'get', 'secret', name,
            '--ignore-not-found', '-o', 'json'], stderr=subprocess.DEVNULL)
        if not raw.strip():
            missing.append(name)
            print('MISSING:', name)
            continue
        document = json.loads(raw)
        data = document.get('data', {})
        if any(not data.get(k) or not base64.b64decode(data[k], validate=True) for k in keys):
            raise ValueError('Existing Secret incomplete; refusing replacement: ' + name)
        if name == v['bdv'].get('keycloakSecret'):
            for key, expected in [('issuer', v['keycloak']['issuer']), ('clientId', v['keycloak']['clientId'])]:
                if base64.b64decode(data[key]).decode() != expected:
                    raise ValueError('Existing Keycloak Secret metadata differs from values')
        print('PASS: retained Secret keys verified:', name)
    if not args.create:
        print('PLAN ONLY: no Secrets changed; use --create to create missing names')
        return
    def password():
        return 'Aa1!' + secrets.token_hex(24)
    for name in missing:
        if name == b['adminSecret']:
            data = {'username': input('Existing MySQL DBA username: ').strip(),
                    'password': getpass.getpass('Existing MySQL DBA password: ')}
        elif name == v['bdv'].get('keycloakSecret'):
            data = {'issuer': v['keycloak']['issuer'], 'clientId': v['keycloak']['clientId'],
                    'clientSecret': getpass.getpass('Existing Keycloak client secret: ')}
        elif name == v['bdv']['internalSecret']:
            data = {'sharedSecret': base64.b64encode(secrets.token_bytes(64)).decode()}
        elif name == v['objectStorage']['credentialsSecret']:
            if v['objectStorage']['provider'] == 'aws-s3':
                data = {'accessKey': getpass.getpass('AWS S3 access key: '),
                        'secretKey': getpass.getpass('AWS S3 secret key: ')}
            else:
                data = {'accessKey': 'bdv' + secrets.token_hex(10), 'secretKey': password()}
        elif name == v['minio']['adminSecret']:
            data = {'MINIO_ROOT_USER': 'bdvroot' + secrets.token_hex(8),
                    'MINIO_ROOT_PASSWORD': password()}
        else:
            data = {k: password() for k in wanted[name]}
        if any(not value or value != value.strip() for value in data.values()):
            raise ValueError('Empty or whitespace-padded credential refused')
        document = {'apiVersion': 'v1', 'kind': 'Secret', 'type': 'Opaque',
                    'metadata': {'name': name, 'namespace': args.namespace},
                    'stringData': data}
        subprocess.run(['kubectl', 'create', '-f', '-'], input=json.dumps(document),
                       text=True, check=True, stdout=subprocess.DEVNULL)
        print('Created retained Secret:', name)
    print('PASS: missing credentials created; existing credentials unchanged')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, subprocess.CalledProcessError):
        sys.exit('FAIL: Secret setup stopped; inspect required keys/connectivity, no credential values printed')

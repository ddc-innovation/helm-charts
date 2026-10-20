#!/usr/bin/env python3
"""Read-only cluster prerequisite checks; never print Secret values."""
import json
import base64
import subprocess
import sys
from values_check import load, merge

v = merge(load(sys.argv[1]), load(sys.argv[2]))
namespace, mode = sys.argv[3:5]

def kubectl(*args):
    result = subprocess.run(['kubectl', '--request-timeout=20s', *args], capture_output=True, timeout=30)
    if result.returncode:
        sys.exit('FAIL: cluster read failed; inspect namespace, connectivity and RBAC')
    return json.loads(result.stdout)

version = kubectl('version', '-o', 'json')['serverVersion']['gitVersion'].lstrip('v').split('+')[0]
if version != v['kubernetesVersion']:
    sys.exit('FAIL: schema target does not match actual server version')
kubectl('get', 'namespace', namespace, '-o', 'json')
kubectl('-n', namespace, 'get', 'configmap', 'kube-root-ca.crt', '-o', 'json')
b = v['bootstrap']
required = {b['adminSecret']: ['username', 'password']}
if b['sslMode'] != 'REQUIRED':
    required[b['caSecret']] = [b['caKey']]
if mode == 'runtime':
    required.update({
        v['objectStorage']['credentialsSecret']: ['accessKey', 'secretKey'],
        b['hive']['secret']: ['host', 'port', 'database', 'username', 'password'],
        b['ranger']['secret']: ['host', 'port', 'database', 'username', 'password'],
        v['ranger']['credentialsSecret']: [
            'rangerAdmin_password', 'rangerTagsync_password',
            'rangerUsersync_password', 'keyadmin_password'],
    })
    if v['objectStorage']['provider'] == 'minio':
        required[v['minio']['adminSecret']] = ['MINIO_ROOT_USER', 'MINIO_ROOT_PASSWORD']
    if v['hive'].get('configSecret'):
        required[v['hive']['configSecret']] = []
    if not v['configuration']['managed']:
        required[v['bdv']['coordinatorConfigSecret']] = ['config.properties', 'node.properties', 'jvm.config']
        required[v['bdv']['catalogSecret']] = ['hive.properties']
        required[v['bdv']['pluginSecret']] = ['access-control.properties', 'ranger-trino-security.xml']
        if v['bdv']['workers']:
            required[v['bdv']['workerConfigSecret']] = ['config.properties', 'node.properties', 'jvm.config']
    if v['bdv']['autoscaling']['enabled']:
        kubectl('get', '--raw', '/apis/metrics.k8s.io/v1beta1/nodes')
    for key in ('mysqlConnectorSecret', 'keycloakSecret', 'internalSecret'):
        if v['bdv'].get(key):
            required[v['bdv'][key]] = []
    if v['bdv'].get('oauth2', {}).get('enabled'):
        required[v['bdv']['keycloakSecret']] = ['issuer', 'clientId', 'clientSecret']
        required[v['bdv']['internalSecret']] = ['sharedSecret']
    if v['ingress'].get('enabled'):
        kubectl('get', 'ingressclass', v['ingress']['className'], '-o', 'json')
    if v['objectStorage']['provider'] == 'minio':
        if not v['storage'].get('existingMinioClaim'):
            kubectl('get', 'storageclass', v['storage']['className'], '-o', 'json')
        else:
            kubectl('-n', namespace, 'get', 'pvc', v['storage']['existingMinioClaim'], '-o', 'json')
for name, keys in required.items():
    secret = kubectl('-n', namespace, 'get', 'secret', name, '-o', 'json')
    data = secret.get('data', {})
    if not data or any(not data.get(key) for key in keys):
        sys.exit('FAIL: required Secret is empty or missing required keys: ' + name)
if mode == 'runtime' and v['bdv'].get('oauth2', {}).get('enabled') and not v['configuration']['managed']:
    secret = kubectl('-n', namespace, 'get', 'secret', v['bdv']['coordinatorConfigSecret'], '-o', 'json')
    try:
        text = base64.b64decode(secret['data']['config.properties'], validate=True).decode()
        properties = {}
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith(('#', '!')):
                continue
            key, value = line.split('=', 1)
            key = key.strip()
            if key in properties:
                raise ValueError('duplicate property')
            properties[key] = value.strip()
        required_properties = {
            'http-server.authentication.type': 'oauth2',
            'web-ui.authentication.type': 'oauth2',
            'http-server.process-forwarded': 'true',
            'http-server.authentication.oauth2.issuer': '${ENV:BDV_OIDC_ISSUER}',
            'http-server.authentication.oauth2.client-id': '${ENV:BDV_OIDC_CLIENT_ID}',
            'http-server.authentication.oauth2.client-secret': '${ENV:BDV_OIDC_CLIENT_SECRET}',
            'http-server.authentication.oauth2.principal-field': 'preferred_username',
            'internal-communication.shared-secret': '${ENV:BDV_INTERNAL_SHARED_SECRET}',
        }
        if any(properties.get(key) != value for key, value in required_properties.items()):
            raise ValueError('OAuth config differs')
        if properties.get('http-server.authentication.allow-insecure-over-http', 'false') != 'false':
            raise ValueError('insecure HTTP authentication')
    except (KeyError, ValueError, UnicodeError):
        sys.exit('FAIL: coordinator OAuth configuration contract differs; no Secret values printed')
    print('PASS: coordinator OAuth config uses expected Secret-backed references')
print('PASS: read-only cluster prerequisites. Configuration content, RBAC for hooks, logins and application readiness are not proven.')

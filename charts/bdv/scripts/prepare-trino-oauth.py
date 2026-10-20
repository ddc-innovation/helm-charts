#!/usr/bin/env python3
"""Prepare a separate OAuth coordinator Secret. Plan-only unless --create is set.

Never prints Secret values or writes the client/internal secret to local disk.
Requires the current coordinator, Keycloak and internal Secrets to exist.
"""
import argparse
import base64
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import yaml

ADDITIONS = {
    'http-server.authentication.type': 'oauth2',
    'web-ui.authentication.type': 'oauth2',
    'http-server.process-forwarded': 'true',
    'http-server.authentication.oauth2.issuer': '${ENV:BDV_OIDC_ISSUER}',
    'http-server.authentication.oauth2.client-id': '${ENV:BDV_OIDC_CLIENT_ID}',
    'http-server.authentication.oauth2.client-secret': '${ENV:BDV_OIDC_CLIENT_SECRET}',
    'http-server.authentication.oauth2.principal-field': 'preferred_username',
    'http-server.authentication.oauth2.scopes': 'openid,profile,email',
    'internal-communication.shared-secret': '${ENV:BDV_INTERNAL_SHARED_SECRET}',
}


def build_config(content):
    properties = {}
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith(('#', '!')):
            continue
        match = re.fullmatch(r'([A-Za-z0-9_.-]+)\s*=\s*(.*)', line)
        if not match or line.endswith('\\'):
            raise ValueError('Source config needs manual review: unsupported property syntax')
        key, value = match.groups()
        if key in properties:
            raise ValueError('Source config has duplicate properties')
        properties[key] = value
        if (key.startswith(('http-server.authentication.', 'web-ui.authentication.'))
                or key in ('internal-communication.shared-secret', 'http-server.process-forwarded')):
            raise ValueError('Source config already contains security settings; refusing replacement')
    if properties.get('coordinator') != 'true' or properties.get('http-server.http.port') != '8080':
        raise ValueError('Expected the tested HTTP coordinator on port 8080')
    if properties.get('http-server.http.enabled', 'true') != 'true':
        raise ValueError('Source config disables the required internal HTTP listener')
    return content.rstrip() + '\n\n# OAuth2 candidate; credentials are resolved from Secret-backed environment.\n' + ''.join(
        key + '=' + value + '\n' for key, value in ADDITIONS.items()
    )


def candidate_values(values, source, target, keycloak, internal):
    result = copy.deepcopy(values)
    bdv = result.setdefault('bdv', {})
    if bdv.get('coordinatorConfigSecret') != source:
        raise ValueError('Source Secret differs from the selected values profile')
    if bdv.get('workers', 0) != 0:
        raise ValueError('OAuth candidate requires zero workers')
    bdv.update(coordinatorConfigSecret=target, keycloakSecret=keycloak, internalSecret=internal)
    bdv['oauth2'] = {'enabled': True}
    result.setdefault('bootstrap', {})['only'] = False
    result.setdefault('ingress', {})['enabled'] = False
    result['deployment'] = {
        'enabled': False, 'evidenceConfirmed': False,
        'domainsConfirmed': False, 'proxyBoundaryConfirmed': False,
    }
    return result


def kubectl(namespace, args, payload=None):
    result = subprocess.run(
        ['kubectl', '--request-timeout=20s', '-n', namespace, *args],
        input=None if payload is None else json.dumps(payload),
        text=True, capture_output=True, timeout=30,
    )
    if result.returncode:
        raise ValueError('Kubernetes operation failed; no raw Secret response is logged')
    return json.loads(result.stdout) if result.stdout.strip() else None


def get_secret(namespace, name):
    return kubectl(namespace, ['get', 'secret', name, '--ignore-not-found', '-o', 'json'])


def decode(secret, key, credential=True):
    try:
        value = base64.b64decode(secret['data'][key], validate=True).decode()
    except (KeyError, TypeError, ValueError, UnicodeError):
        raise ValueError('Required Secret key is missing or malformed') from None
    if not value or (credential and value != value.strip()):
        raise ValueError('Required Secret key is empty or has surrounding whitespace')
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--namespace', default='bdv')
    parser.add_argument('--source-secret', default='bdv-trino-coordinator')
    parser.add_argument('--target-secret', default='bdv-trino-coordinator-oidc')
    parser.add_argument('--keycloak-secret', default='bdv-keycloak')
    parser.add_argument('--internal-secret', default='bdv-internal')
    parser.add_argument('--values', default='values-dev.yaml')
    parser.add_argument('--output-values', default='values-dev-oidc.yaml')
    parser.add_argument('--create', action='store_true', help='Create candidate Secret and values file; never deploys')
    args = parser.parse_args()
    os.umask(0o077)
    if args.source_secret == args.target_secret:
        raise ValueError('Source and target Secrets must be different')
    output = Path(args.output_values)
    if output.resolve() == Path(args.values).resolve():
        raise ValueError('Source and output values files must be different')
    values = yaml.safe_load(Path(args.values).read_text())
    desired_values = candidate_values(values, args.source_secret, args.target_secret,
                                      args.keycloak_secret, args.internal_secret)
    if output.exists() and yaml.safe_load(output.read_text()) != desired_values:
        raise ValueError('Output values file already exists with different content')
    source = get_secret(args.namespace, args.source_secret)
    keycloak = get_secret(args.namespace, args.keycloak_secret)
    internal = get_secret(args.namespace, args.internal_secret)
    if decode(keycloak, 'issuer') != 'https://sso.dinecodetalkers.com/realms/innonet':
        raise ValueError('Keycloak issuer differs from the verified innonet discovery URL')
    if decode(keycloak, 'clientId') != 'bdv':
        raise ValueError('Keycloak client ID differs from bdv')
    decode(keycloak, 'clientSecret')
    shared = decode(internal, 'sharedSecret')
    try:
        if len(base64.b64decode(shared, validate=True)) < 32:
            raise ValueError('Internal shared secret is too short')
    except ValueError:
        raise ValueError('Internal shared secret must contain at least 32 base64-encoded bytes') from None
    for key in ('config.properties', 'node.properties', 'jvm.config', 'log.properties'):
        decode(source, key, credential=False)
    config = build_config(decode(source, 'config.properties', credential=False))
    data = dict(source['data'])
    data['config.properties'] = base64.b64encode(config.encode()).decode()
    existing = get_secret(args.namespace, args.target_secret)
    if existing is not None and existing.get('data') != data:
        raise ValueError('Target Secret already exists with different content; refusing overwrite')
    print('PASS: source config and OAuth/internal Secret contracts verified')
    print('Plan: separate coordinator config; OAuth2; preferred_username; forwarded HTTPS; HTTP readiness')
    print('Plan: ingress disabled; zero workers; confirmation flags remain false')
    if not args.create:
        print('PLAN ONLY: no Secret, file, release or workload changed; use --create after review')
        return
    if existing is None:
        kubectl(args.namespace, ['create', '-f', '-', '-o', 'json'], {
            'apiVersion': 'v1', 'kind': 'Secret', 'type': 'Opaque',
            'metadata': {'name': args.target_secret, 'namespace': args.namespace,
                         'annotations': {'bdv.dinecodetalkers.com/purpose': 'retained-oauth-candidate'}},
            'data': data,
        })
    if get_secret(args.namespace, args.target_secret).get('data') != data:
        raise ValueError('Candidate Secret verification failed')
    if not output.exists():
        with output.open('x') as stream:
            yaml.safe_dump(desired_values, stream, sort_keys=False)
        output.chmod(0o600)
    print('PASS: OAuth candidate Secret and values prepared; source Secret unchanged')
    print('No Helm upgrade, restart, ingress or authentication change performed')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError, yaml.YAMLError) as exc:
        print('FAIL: ' + (str(exc) if isinstance(exc, ValueError) else 'preparation failed; inspect prerequisites securely'), file=sys.stderr)
        raise SystemExit(1)

#!/usr/bin/env python3
"""Additive Ranger reconciliation. No Kubernetes credentials or password output."""
import base64
import copy
import json
import os
from pathlib import Path
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def resource_signature(resources):
    return tuple(sorted((key, tuple(sorted(value.get('values', []))),
                         bool(value.get('isExcludes', False)),
                         bool(value.get('isRecursive', False)))
                        for key, value in resources.items()))


def covered(policy, user, accesses):
    granted = set()
    for item in policy.get('policyItems', []):
        if (user in item.get('users', []) and not item.get('conditions')
                and not item.get('delegateAdmin', False)):
            granted.update(a['type'] for a in item.get('accesses', [])
                           if a.get('isAllowed'))
    return set(accesses) <= granted


def add_grant(policy, grant):
    result = copy.deepcopy(policy)
    result['isAuditEnabled'] = True
    for user in grant['users']:
        if not covered(result, user, grant['accesses']):
            result.setdefault('policyItems', []).append({
                'users': [user], 'groups': [], 'roles': [], 'conditions': [],
                'accesses': [{'type': a, 'isAllowed': True} for a in grant['accesses']],
                'delegateAdmin': False,
            })
    return result


def validate(settings):
    users = settings['users']
    if len(users) != len(set(users)) or any(not u or '*' in u for u in users):
        raise ValueError('Users must be unique explicit names')
    if settings.get('smokeCreator') and settings['smokeCreator'] not in users:
        raise ValueError('Smoke creator must be an explicitly declared user')
    for grant in settings['grants']:
        if not grant['resources'] or not grant['users'] or not grant['accesses']:
            raise ValueError('Empty grant is refused')
        if not set(grant['users']) <= set(users):
            raise ValueError('Grant references an undeclared user')
        if not set(grant['accesses']) <= set(settings['accessTypes']):
            raise ValueError('Unknown access type')
        if not set(grant['resources']) <= set(settings['resourceParents']):
            raise ValueError('Unknown resource type')
        if any(not isinstance(v, list) or not v or any(not isinstance(x, str) or not x for x in v)
               for v in grant['resources'].values()):
            raise ValueError('Resource values must be nonempty string lists')


class API:
    def __init__(self, base, password):
        self.base = base.rstrip('/')
        self.secret = password
        self.auth = 'Basic ' + base64.b64encode(('admin:' + password).encode()).decode()

    def __call__(self, method, path, document=None, missing=False):
        headers = {'Authorization': self.auth, 'Accept': 'application/json'}
        body = None
        if document is not None:
            headers['Content-Type'] = 'application/json'
            body = json.dumps(document).encode()
        req = urllib.request.Request(self.base + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                data = response.read()
                return json.loads(data) if data else None
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors='replace')
            if missing and (error.code == 404 or
                            (error.code == 400 and 'DATA_NOT_FOUND' in detail)):
                return None
            try:
                message = str(json.loads(detail).get('msgDesc', 'Request rejected'))
            except (ValueError, AttributeError):
                message = 'Non-JSON error body omitted'
            payload_password = document.get('password', '') if isinstance(document, dict) else ''
            for secret in (self.secret, self.auth, payload_password):
                if not secret:
                    continue
                message = message.replace(secret, '[REDACTED]')
            raise RuntimeError(f'{method} {path}: HTTP {error.code}: {message}') from None


def quote(value):
    return urllib.parse.quote(value, safe='')


def definition(api, settings):
    path = '/service/public/v2/api/servicedef/name/' + quote(settings['serviceType'])
    existing = api('GET', path, missing=True)
    value = copy.deepcopy(existing) if existing else {
        'name': settings['serviceType'], 'label': 'BDV Trino',
        'implClass': 'org.apache.ranger.services.trino.RangerServiceTrino',
        'configs': [], 'resources': [], 'accessTypes': [],
        'policyConditions': [], 'enums': [],
    }
    before = copy.deepcopy(value)
    resources = {r['name']: r for r in value['resources']}
    next_id = max((int(r['itemId']) for r in value['resources']), default=0) + 1
    for name, contract in settings['resourceParents'].items():
        if name in resources:
            if ((resources[name].get('parent') or None) != contract.get('parent') or
                    resources[name].get('level') != contract['level']):
                raise RuntimeError('Existing resource hierarchy differs: ' + name)
            continue
        item = {
            'itemId': next_id, 'name': name, 'label': name,
            'type': 'string', 'level': contract['level'],
            'mandatory': True, 'lookupSupported': False,
            'recursiveSupported': False,
            'matcher': 'org.apache.ranger.plugin.resourcematcher.RangerDefaultResourceMatcher',
            'matcherOptions': {'wildCard': True, 'ignoreCase': False},
        }
        if contract.get('parent'):
            item['parent'] = contract['parent']
        next_id += 1
        value['resources'].append(item)
    accesses = {a['name'] for a in value['accessTypes']}
    next_id = max((int(a['itemId']) for a in value['accessTypes']), default=0) + 1
    for name in settings['accessTypes']:
        if name not in accesses:
            value['accessTypes'].append({'itemId': next_id, 'name': name, 'label': name})
            next_id += 1
    if not existing:
        api('POST', '/service/public/v2/api/servicedef', value)
    elif value != before:
        api('PUT', '/service/public/v2/api/servicedef/' + str(value['id']), value)
    verified = api('GET', path)
    if not (set(settings['resourceParents']) <= {x['name'] for x in verified['resources']}
            and set(settings['accessTypes']) <= {x['name'] for x in verified['accessTypes']}):
        raise RuntimeError('Service definition readback failed')


def reconcile(api, settings):
    validate(settings)
    definition(api, settings)
    service = settings['serviceName']
    path = '/service/public/v2/api/service/name/' + quote(service)
    existing = api('GET', path, missing=True)
    if existing is None:
        api('POST', '/service/public/v2/api/service', {
            'name': service, 'type': settings['serviceType'],
            'isEnabled': True, 'configs': settings.get('serviceConfigs', {}),
        })
    existing = api('GET', path)
    if existing.get('type') != settings['serviceType'] or not existing.get('isEnabled'):
        raise RuntimeError('Existing Ranger service is incompatible')
    for user in settings['users']:
        path = '/service/xusers/users/userName/' + quote(user)
        existing = api('GET', path, missing=True)
        if existing is None:
            api('POST', '/service/xusers/users', {
                'name': user, 'firstName': user.split('@')[0],
                'password': 'Aa1!' + secrets.token_hex(24),
                'status': 1, 'isVisible': 1, 'userSource': 0,
                'userRoleList': ['ROLE_USER'],
            })
        verified = api('GET', path)
        if verified.get('name') != user:
            raise RuntimeError('User readback failed')
        print('PASS: Ranger identity exists:', user, flush=True)
    grants = copy.deepcopy(settings['grants'])
    if settings.get('smokeCreator'):
        grants.append({'name': 'bdv-smoke-creator', 'users': [settings['smokeCreator']],
                       'resources': {'catalog': ['hive'], 'schema': ['default'],
                                     'table': ['bdv_smoke_20261019']},
                       'accesses': ['create']})
    if settings.get('selfImpersonation'):
        grants += [{'name': 'bdv-self-' + user, 'users': [user],
                    'resources': {'trinouser': [user]}, 'accesses': ['impersonate']}
                   for user in settings['users']]
    for grant in grants:
        resources = {k: {'values': v, 'isExcludes': False, 'isRecursive': False}
                     for k, v in grant['resources'].items()}
        # Match by resources, not just policy name: Ranger rejects duplicate
        # resource policies. Adopt the current health-check policies additively.
        policies = api('GET', '/service/public/v2/api/service/' + quote(service) + '/policy')
        if not isinstance(policies, list):
            raise RuntimeError('Unexpected Ranger policy-list response')
        matches = [p for p in policies if p.get('service') == service
                   and p.get('policyType', 0) == 0
                   and not p.get('zoneName')
                   and resource_signature(p.get('resources', {})) == resource_signature(resources)]
        if len(matches) > 1:
            raise RuntimeError('Ambiguous existing policy resource scope')
        original = matches[0] if matches else None
        if original and (not original.get('isEnabled') or original.get('validitySchedules')):
            raise RuntimeError('Existing policy is disabled or time-scoped; manual review required')
        value = original or {
            'service': service, 'name': grant['name'], 'policyType': 0,
            'isEnabled': True, 'isAuditEnabled': True, 'resources': resources,
            'policyItems': [],
        }
        updated = add_grant(value, grant)
        if not original:
            updated = api('POST', '/service/public/v2/api/policy', updated)
        elif updated != original:
            updated = api('PUT', '/service/public/v2/api/policy/' + str(original['id']), updated)
        ident = (updated or original)['id']
        verified = api('GET', '/service/public/v2/api/policy/' + str(ident))
        if (resource_signature(verified['resources']) != resource_signature(resources)
                or not all(covered(verified, u, grant['accesses']) for u in grant['users'])):
            raise RuntimeError('Policy readback failed: ' + grant['name'])
        print('PASS: policy grant verified:', grant['name'], flush=True)


def main():
    settings = json.loads(Path(sys.argv[1]).read_text())
    validate(settings)
    password = Path('/ranger-credentials/rangerAdmin_password').read_text()
    if not password or password != password.strip():
        raise ValueError('Invalid administrator Secret format')
    api = API(os.environ['RANGER_URL'], password)
    deadline = time.monotonic() + 600
    while True:
        try:
            api('GET', '/service/metrics/status')
            break
        except (OSError, urllib.error.URLError, RuntimeError):
            if time.monotonic() >= deadline:
                raise RuntimeError('Ranger readiness/authentication timed out') from None
            time.sleep(5)
    reconcile(api, settings)
    print('PASS: values-driven Ranger authorization reconciled', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # HTTP errors are sanitized by API; never dump requests/payloads.
        print('FAIL:', str(exc), flush=True)
        sys.exit(1)

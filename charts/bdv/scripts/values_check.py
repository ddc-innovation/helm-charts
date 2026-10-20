#!/usr/bin/env python3
"""Read-only configuration checks. Does not install tools or change the cluster."""
import argparse
import copy
import ipaddress
import re
import sys
from pathlib import Path
import yaml


class UniqueLoader(yaml.SafeLoader):
    pass


def mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError('Duplicate YAML key: ' + str(key))
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)


def load(path):
    with Path(path).open() as stream:
        value = yaml.load(stream, Loader=UniqueLoader)
    if not isinstance(value, dict):
        raise ValueError('Values must be a mapping')
    return value


def merge(base, override):
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = value
    return result


def check(v, mode):
    errors = []
    def need(condition, message):
        if not condition:
            errors.append(message)
    def image(value, name):
        need(bool(value.get('repository')), name + '.repository is required')
        if value.get('digest'):
            need(bool(re.fullmatch(r'sha256:[0-9a-f]{64}', value['digest'])), name + '.digest is invalid')
        else:
            tag = value.get('tag', '')
            need(bool(re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}', tag)) and tag != 'latest', name + ' needs a digest or explicit non-latest tag')
    need(bool(re.fullmatch(r'1\.[0-9]+\.[0-9]+', str(v.get('kubernetesVersion', '')))), 'Explicit kubernetesVersion required, e.g. 1.36.4')
    if mode == 'offline':
        return errors
    b = v['bootstrap']
    image(b['image'], 'bootstrap.image')
    need(bool(b.get('host')), 'bootstrap.host is required')
    need(b.get('sslMode') in ('REQUIRED', 'VERIFY_CA', 'VERIFY_IDENTITY'), 'Unsupported bootstrap.sslMode')
    account = b.get('accountHost', '')
    need(bool(account) and account != '%', 'Restricted bootstrap.accountHost is required')
    if '/' in account:
        try:
            ipaddress.IPv4Network(account, strict=True)
        except ValueError:
            errors.append('bootstrap.accountHost must be a valid network restriction')
    for field in ('database', 'username', 'secret'):
        h, r = b['hive'].get(field), b['ranger'].get(field)
        need(bool(h) and bool(r) and h != r, 'Hive/Ranger ' + field + ' must be nonempty and distinct')
    for component in ('hive', 'ranger'):
        for field in ('database', 'username'):
            need(bool(re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,31}', str(b[component].get(field, '')))), component + '.' + field + ' is not a supported identifier')
        need(b[component]['secret'] != b['adminSecret'], 'Application Secret must differ from administrator Secret')
    if b['sslMode'] != 'REQUIRED':
        need(bool(b.get('caSecret')) and bool(b.get('caKey')), 'CA Secret and key are required for certificate verification')
    if mode == 'bootstrap':
        return errors
    for flag in ('evidenceConfirmed',):
        need(v['deployment'].get(flag) is True, 'deployment.' + flag + ' must reflect completed verification')
    if v['bdv'].get('oauth2', {}).get('enabled'):
        need(bool(v['bdv'].get('keycloakSecret')) and bool(v['bdv'].get('internalSecret')), 'OAuth requires Keycloak and internal Secret references')
        need(v['deployment'].get('proxyBoundaryConfirmed') is True, 'OAuth requires deployment.proxyBoundaryConfirmed')
        need(v.get('configuration', {}).get('managed') or (
            v['bdv'].get('workers') == 0 and not v['bdv']['autoscaling']['enabled']),
            'OAuth workers require managed shared-secret configuration')
        need(v['bdv'].get('port') == 8080, 'Verified HTTP readiness requires BDV port 8080')
    if v.get('configuration', {}).get('managed'):
        need(bool(v['bdv'].get('internalSecret')), 'Managed config requires internalSecret')
        jvm = v['bdv']['jvm']
        need(0 < jvm['initialRAMPercentage'] <= jvm['maxRAMPercentage'] < 90,
             'Require 0 < initial JVM percentage <= max < 90')
        need(0 < jvm['queryMemoryPercentageOfLimit'] and 0 < jvm['heapHeadroomPercentageOfLimit']
             and jvm['queryMemoryPercentageOfLimit'] + jvm['heapHeadroomPercentageOfLimit'] < jvm['maxRAMPercentage'],
             'Query memory plus headroom must be below the JVM max percentage')
        for role, resources in [('coordinator', v['bdv']['resources']), ('worker', v['bdv']['workerResources'])]:
            need(bool(re.fullmatch(r'[1-9][0-9]*(Mi|Gi)', str(resources['limits']['memory']))),
                 role + ' managed memory limit must use integer Mi or Gi')
        for role, names in {'coordinator': ['config.properties', 'node.properties', 'jvm.config', 'log.properties'],
                            'worker': ['config.properties', 'node.properties', 'jvm.config', 'log.properties'],
                            'catalog': ['hive.properties'],
                            'plugin': ['access-control.properties', 'ranger-trino-security.xml', 'ranger-trino-audit.xml', 'core-site.xml']}.items():
            need(all(v['configuration']['files'].get(role, {}).get(k) for k in names),
                 'Managed configuration files missing for ' + role)
    hpa = v['bdv']['autoscaling']
    shutdown = v['bdv']['workerShutdown']
    if shutdown['enabled']:
        need(v['configuration']['managed'] and bool(v['bdv'].get('internalSecret')),
             'Worker draining requires managed configuration and internal Secret')
        need(300 <= shutdown['waitSeconds'] <= shutdown['terminationGracePeriodSeconds'] - 30,
             'Worker drain wait must be at least 300s and leave 30s termination margin')
        need(v['bdv']['port'] == 8080, 'Worker drain helper currently requires port 8080')
        need('worker-drain.sh' not in v['configuration']['files'].get('worker', {}),
             'worker-drain.sh is chart-owned; do not override it in configuration.files')
    if hpa.get('scaleDownEnabled'):
        need(shutdown['enabled'], 'Automatic scale-down requires the worker drain hook')
    if hpa['enabled']:
        need(v['configuration']['managed'], 'HPA requires managed worker config')
        need(2 <= hpa['minReplicas'] <= hpa['maxReplicas'], 'Require 2 <= HPA min <= max')
        need(1 <= hpa['targetCPUUtilizationPercentage'] <= 100, 'HPA CPU target must be 1..100')
        need(bool(v['bdv']['workerResources']['requests'].get('cpu')), 'HPA needs worker CPU requests')
    if v['ranger']['authorization']['enabled']:
        import importlib.util
        spec = importlib.util.spec_from_file_location('authorize', Path(__file__).resolve().parents[1] / 'files/ranger-authorize.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        try:
            module.validate(v['ranger']['authorization'])
        except (KeyError, ValueError, TypeError) as exc:
            errors.append('Ranger authorization inventory invalid: ' + str(exc))
    if v['ingress'].get('enabled'):
        for flag in ('domainsConfirmed', 'proxyBoundaryConfirmed'):
            need(v['deployment'].get(flag) is True, 'deployment.' + flag + ' must reflect completed verification')
        need(bool(v['ingress'].get('className')), 'ingress.className is required')
    for component in ('hive', 'ranger', 'bdv'):
        c = v[component]
        image(c['image'], component + '.image')
        for key in ('command', 'readinessCommand'):
            command = c.get(key)
            need(isinstance(command, list) and bool(command) and all(isinstance(x, str) for x in command), component + '.' + key + ' needs verified argv')
        if component in ('hive', 'ranger'):
            command = c.get('schemaCommand')
            need(isinstance(command, list) and bool(command) and all(isinstance(x, str) for x in command), component + '.schemaCommand needs guarded argv')
            need(bool(c.get('configMountPath')), component + '.configMountPath is required')
            if command and '-checkupgrade' in command:
                errors.append(component + ': -checkupgrade alone is not a verified schema validation contract')
    storage = v['objectStorage']
    provider = storage.get('provider')
    need(provider in ('minio', 'aws-s3'), 'objectStorage.provider must be minio or aws-s3')
    need(bool(storage.get('bucket')), 'objectStorage.bucket is required')
    need(bool(storage.get('region')), 'objectStorage.region is required')
    need(bool(storage.get('credentialsSecret')), 'objectStorage.credentialsSecret is required')
    if provider == 'minio':
        need(v['minio'].get('enabled') is True, 'minio.enabled must be true for the minio provider')
        need(storage.get('pathStyleAccess') is True, 'MinIO requires objectStorage.pathStyleAccess=true')
        image(v['minio']['image'], 'minio.image')
        image(v['minio']['initImage'], 'minio.initImage')
        need(bool(v['minio'].get('initCommand')), 'minio.initCommand needs verified bucket/user/policy setup')
    if provider == 'aws-s3':
        need(v['minio'].get('enabled') is False, 'minio.enabled must be false for aws-s3')
        need(storage.get('pathStyleAccess') is False, 'AWS S3 production profile requires pathStyleAccess=false')
    need(1 <= int(v['ranger'].get('port', 0)) <= 65535, 'Ranger HTTP port is required')
    need(bool(v['ranger'].get('configMap')), 'ranger.configMap is required')
    need(bool(v['ranger'].get('credentialsSecret')), 'ranger.credentialsSecret is required')
    return errors


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--defaults', required=True)
    p.add_argument('--values', required=True)
    p.add_argument('--mode', choices=['offline', 'bootstrap', 'runtime'], required=True)
    p.add_argument('--version-only', action='store_true')
    a = p.parse_args()
    v = merge(load(a.defaults), load(a.values))
    if a.version_only:
        print(v['kubernetesVersion'])
        return
    errors = check(v, a.mode)
    if errors:
        print('\n'.join('BLOCKED: ' + item for item in errors), file=sys.stderr)
        raise SystemExit(1)
    print('PASS: ' + a.mode + ' configuration checks (not deployment or functional validation)')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError, yaml.YAMLError):
        sys.exit('FAIL: malformed/incomplete values; check YAML structure without exposing credential content')

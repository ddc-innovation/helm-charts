#!/usr/bin/env python3
"""Inspect actual Helm output, including duplicate identities and CA mount wiring."""
import sys
from pathlib import Path
import yaml
from values_check import UniqueLoader

docs = [d for d in yaml.load_all(Path(sys.argv[1]).read_text(), Loader=UniqueLoader) if d]
mode = sys.argv[2]
seen = set()
for doc in docs:
    ident = (doc['apiVersion'], doc['kind'], doc['metadata'].get('namespace', ''), doc['metadata']['name'])
    if ident in seen:
        sys.exit('FAIL: duplicate Kubernetes resource identity')
    seen.add(ident)
    pod = doc.get('spec', {}) if doc['kind'] == 'Pod' else doc.get('spec', {}).get('template', {}).get('spec')
    if not pod:
        continue
    if doc['metadata'].get('labels', {}).get('app.kubernetes.io/component', '').startswith('bootstrap-'):
        projected = next((v for v in pod.get('volumes', []) if v['name'] == 'kubernetes-api'), None)
        if not projected or not any('serviceAccountToken' in s for s in projected.get('projected', {}).get('sources', [])):
            sys.exit('FAIL: bootstrap needs explicit Kubernetes API token projection')
        if not all(any(m['name'] == 'kubernetes-api' and m.get('readOnly') for m in c.get('volumeMounts', [])) for c in pod.get('containers', [])):
            sys.exit('FAIL: bootstrap API credentials need a read-only mount')
    volumes = {v['name'] for v in pod.get('volumes', [])}
    for container in pod.get('containers', []) + pod.get('initContainers', []):
        mounts = {m['name']: m for m in container.get('volumeMounts', [])}
        if set(mounts) - volumes:
            sys.exit('FAIL: container mount references absent volume')
        env = {v['name']: v.get('value') for v in container.get('env', [])}
        if env.get('MYSQL_CA') and not any(m['mountPath'] == '/ca' for m in mounts.values()):
            sys.exit('FAIL: MYSQL_CA configured without certificate mount')
        if env.get('MYSQL_SSL_MODE') == 'REQUIRED' and 'ca' in volumes:
            sys.exit('FAIL: REQUIRED mode unexpectedly depends on CA volume')
    if doc['kind'] == 'Deployment' and doc['metadata'].get('labels', {}).get('app.kubernetes.io/component') == 'ranger':
        init_names = [c['name'] for c in pod.get('initContainers', [])]
        if not init_names or init_names[0] != 'vendor-install':
            sys.exit('FAIL: Ranger vendor installer must be the first init container')
        vendor = pod.get('initContainers', [])[0]
        mounts = {m['name']: m for m in vendor.get('volumeMounts', [])}
        if mounts.get('ranger-credentials', {}).get('mountPath') != '/ranger-credentials':
            sys.exit('FAIL: Ranger vendor installer needs its service credential Secret')
        ranger = next((c for c in pod.get('containers', []) if c['name'] == 'ranger'), None)
        if not ranger or not any(m.get('mountPath') == '/opt/bdv-policy-manager' and m.get('subPath') == 'ranger-home' for m in ranger.get('volumeMounts', [])):
            sys.exit('FAIL: Ranger main container must mount the prepared runtime home')
        ranger_mounts = {m['name'] for m in ranger.get('volumeMounts', [])}
        if ranger_mounts & {'db', 'ranger-credentials'}:
            sys.exit('FAIL: Ranger main container must not mount credential Secrets')
    if doc['kind'] == 'Deployment' and doc['metadata'].get('labels', {}).get('app.kubernetes.io/component') == 'hive':
        pod_security = pod.get('securityContext', {})
        if pod_security.get('fsGroup') != 10001:
            sys.exit('FAIL: Hive writable work directories require fsGroup 10001')
        init = {c['name']: c for c in pod.get('initContainers', [])}
        if list(init)[:2] != ['prerequisites', 'schema']:
            sys.exit('FAIL: Hive prerequisite and schema init ordering is required')
        for name in ('prerequisites', 'schema'):
            security = init[name].get('securityContext', {})
            if security.get('runAsUser') != 10001 or security.get('runAsGroup') != 10001:
                sys.exit('FAIL: Hive init containers must use verified UID/GID 10001')
            if security.get('readOnlyRootFilesystem') is not True:
                sys.exit('FAIL: Hive init containers require read-only root filesystems')
        schema_env = {item['name']: item.get('value') for item in init['schema'].get('env', [])}
        if not all(schema_env.get(key) is not None for key in ('S3_PROVIDER', 'S3_BUCKET', 'S3_REGION', 'S3_PATH_STYLE_ACCESS')):
            sys.exit('FAIL: Hive schema init requires the selected object-storage contract')
        schema_mounts = {m['name']: m for m in init['schema'].get('volumeMounts', [])}
        if not {'db', 's3', 'work', 'tmp'}.issubset(schema_mounts):
            sys.exit('FAIL: Hive schema init is missing database, storage, work or temporary mounts')
        hive = next((c for c in pod.get('containers', []) if c['name'] == 'hive'), None)
        hive_mounts = {m['name']: m for m in hive.get('volumeMounts', [])} if hive else {}
        hive_security = hive.get('securityContext', {}) if hive else {}
        if not {'db', 's3', 'work', 'tmp'}.issubset(hive_mounts):
            sys.exit('FAIL: Hive runtime is missing database, storage, work or temporary mounts')
        if hive_security.get('runAsUser') != 10001 or hive_security.get('readOnlyRootFilesystem') is not True:
            sys.exit('FAIL: Hive runtime must use the verified non-root image contract')
    if doc['kind'] == 'Deployment' and doc['metadata'].get('labels', {}).get('app.kubernetes.io/component') == 'minio':
        minio = next((c for c in pod.get('containers', []) if c['name'] == 'minio'), None)
        if not minio:
            sys.exit('FAIL: MinIO Deployment is missing its server container')
        pod_security = pod.get('securityContext', {})
        container_security = minio.get('securityContext', {})
        if pod_security.get('fsGroup') != 101:
            sys.exit('FAIL: MinIO persistent data requires verified fsGroup 101')
        if container_security.get('runAsUser') != 100 or container_security.get('runAsGroup') != 101:
            sys.exit('FAIL: MinIO must use the verified UID 100 and GID 101')
        if container_security.get('readOnlyRootFilesystem') is not True:
            sys.exit('FAIL: MinIO root filesystem must be read-only')
        paths = {m.get('mountPath') for m in minio.get('volumeMounts', [])}
        if not {'/data', '/home/minio', '/tmp'}.issubset(paths):
            sys.exit('FAIL: MinIO requires writable data, home and temporary mounts')
    if doc['kind'] == 'Job' and doc['metadata'].get('labels', {}).get('app.kubernetes.io/component') == 'buckets':
        initializer = next((c for c in pod.get('containers', []) if c['name'] == 'buckets'), None)
        security = initializer.get('securityContext', {}) if initializer else {}
        paths = {m.get('mountPath') for m in initializer.get('volumeMounts', [])} if initializer else set()
        if security.get('runAsUser') != 100 or security.get('runAsGroup') != 101:
            sys.exit('FAIL: MinIO initializer must use the verified UID and GID')
        if security.get('readOnlyRootFilesystem') is not True or '/tmp' not in paths:
            sys.exit('FAIL: MinIO initializer needs read-only root and bounded writable /tmp')
    if doc['kind'] == 'Job' and doc['metadata'].get('labels', {}).get('app.kubernetes.io/component') == 'ranger-registration':
        register = next((c for c in pod.get('containers', []) if c['name'] == 'register'), None)
        mounts = {m['name']: m for m in register.get('volumeMounts', [])} if register else {}
        if mounts.get('ranger-credentials', {}).get('mountPath') != '/ranger-credentials':
            sys.exit('FAIL: Ranger registration Job needs its protected credential mount')
        if pod.get('automountServiceAccountToken') is not False:
            sys.exit('FAIL: Ranger registration Job must not mount a Kubernetes API token')
kinds = [d['kind'] for d in docs]
if mode == 'offline' and kinds != ['ConfigMap']:
    sys.exit('FAIL: offline mode must render only its contract ConfigMap')
if mode == 'bootstrap':
    if sorted(kinds) != sorted(['ConfigMap', 'ConfigMap', 'ServiceAccount', 'Role', 'RoleBinding', 'Job']):
        sys.exit('FAIL: bootstrap-only mode rendered unexpected/missing resources')
    code = next((d for d in docs if d['kind'] == 'ConfigMap' and
                 d['metadata']['name'].endswith('-bootstrap-code')), None)
    if not code or not all(code.get('data', {}).get(key) for key in ('entrypoint.py', 'provision.py')):
        sys.exit('FAIL: bootstrap requires both bundled DBA scripts')
    job = next((d for d in docs if d['kind'] == 'Job'), None)
    containers = job.get('spec', {}).get('template', {}).get('spec', {}).get('containers', []) if job else []
    if len(containers) != 1 or not any(e.get('name') == 'DATABASES_JSON' and
                                        all(x in e.get('value', '') for x in ('"hive"', '"ranger"'))
                                        for e in containers[0].get('env', [])):
        sys.exit('FAIL: combined DBA Job must receive Hive and Ranger contracts')
if mode == 'runtime':
    contract = next((d for d in docs if d['kind'] == 'ConfigMap' and d['metadata']['name'].endswith('-contract')), None)
    provider = contract.get('data', {}).get('objectStorageProvider') if contract else None
    if contract and contract.get('data', {}).get('oauth2Enabled') == 'true':
        for doc in docs:
            if doc['kind'] != 'Deployment':
                continue
            role = doc['metadata'].get('labels', {}).get('app.kubernetes.io/component')
            if role not in ('coordinator', 'worker'):
                continue
            pod = doc['spec']['template']['spec']
            main = pod['containers'][0]
            env = {item['name']: item for item in main.get('env', [])}
            expected = {'BDV_INTERNAL_SHARED_SECRET': 'sharedSecret'}
            if role == 'coordinator':
                expected.update(BDV_OIDC_ISSUER='issuer', BDV_OIDC_CLIENT_ID='clientId', BDV_OIDC_CLIENT_SECRET='clientSecret')
            elif any(key.startswith('BDV_OIDC_') for key in env):
                sys.exit('FAIL: worker must not receive the OAuth client secret')
            for key, secret_key in expected.items():
                ref = env.get(key, {}).get('valueFrom', {}).get('secretKeyRef', {})
                if not ref.get('name') or ref.get('key') != secret_key:
                    sys.exit('FAIL: OAuth environment must use explicit expected Secret keys')
            for init in pod.get('initContainers', []):
                if any(item['name'].startswith(('BDV_OIDC_', 'BDV_INTERNAL_')) for item in init.get('env', [])):
                    sys.exit('FAIL: prerequisite containers must not receive OAuth environment secrets')
            probe = ' '.join(main.get('readinessProbe', {}).get('exec', {}).get('command', []))
            drain_probe = role == 'worker' and main.get('readinessProbe', {}).get('exec', {}).get('command') == ['/usr/bin/bash', '/etc/trino/worker-drain.sh', 'ready']
            if drain_probe:
                helper = next((d.get('data', {}).get('worker-drain.sh', '') for d in docs
                               if d['kind'] == 'ConfigMap' and d['metadata']['name'].endswith('-managed-worker')), '')
                if '/v1/info' not in helper or 'SHUTTING_DOWN' not in helper:
                    sys.exit('FAIL: worker drain readiness helper is missing')
            if (not drain_probe and '/v1/info' not in probe) or '--execute' in probe:
                sys.exit('FAIL: OAuth requires HTTP readiness rather than an unauthenticated SQL query')
    expected_deployments, expected_jobs = (5, 2) if provider == 'minio' else (4, 1)
    if contract and contract.get('data', {}).get('authorizationEnabled') == 'true':
        expected_jobs += 1
    if provider not in ('minio', 'aws-s3') or kinds.count('Deployment') != expected_deployments or kinds.count('Job') != expected_jobs:
        sys.exit('FAIL: runtime workload counts do not match the selected object-storage provider')
    minio_resources = [d for d in docs if d['metadata'].get('labels', {}).get('app.kubernetes.io/component') == 'minio']
    if provider == 'aws-s3' and minio_resources:
        sys.exit('FAIL: AWS S3 profile rendered MinIO resources')
    if provider == 'minio' and not {'Deployment', 'Service'}.issubset({d['kind'] for d in minio_resources}):
        sys.exit('FAIL: MinIO profile is missing its Deployment or Service')
    ranger_configs = [d for d in docs if d['kind'] == 'ConfigMap' and
                      d['metadata'].get('labels', {}).get('app.kubernetes.io/component') == 'ranger']
    if len(ranger_configs) != 1 or not ranger_configs[0].get('data', {}).get('install.properties'):
        sys.exit('FAIL: runtime must include the Ranger install.properties ConfigMap')
print('PASS: rendered resource identities, expected workload counts and volume wiring')

#!/usr/bin/env bash
# Intentionally fixed namespace/release so this workflow cannot target bdv live.
set -Eeuo pipefail
umask 077
CHART="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export NAMESPACE=bdv-test RELEASE=bdv
export KUBECONFIG="${KUBECONFIG:-/root/.kube/config}"
values="$CHART/values-test.yaml"
action="${1:-prepare}"
case "$action" in
  prepare) exec python3 "$CHART/scripts/prepare-fresh-test.py" ;;
  namespace)
    existing="$(kubectl get namespace bdv-test --ignore-not-found -o name)"
    [[ -z "$existing" ]] || { echo 'STOP: bdv-test already exists; this is not an empty namespace'; exit 1; }
    kubectl create namespace bdv-test
    kubectl label namespace bdv-test bdv-test-isolated=true
    exit 0 ;;
esac
label="$(kubectl get namespace bdv-test -o jsonpath='{.metadata.labels.bdv-test-isolated}')"
[[ "$label" == true ]] || { echo 'FAIL: expected isolated bdv-test namespace label'; exit 1; }
python3 - "$CHART" "$values" <<'PY'
from pathlib import Path
import sys
sys.path.insert(0, str(Path(sys.argv[1])/'scripts'))
from values_check import load, merge
v=merge(load(Path(sys.argv[1])/'values.yaml'),load(sys.argv[2]))
for role in ('hive','ranger'):
    assert v['bootstrap'][role]['database']==v['bootstrap'][role]['username']=='bdv_test_'+role, 'Refusing non-test database/user'
assert v['keycloak']['clientId']=='bdv-test' and v['bdv']['oauth2']['enabled'], 'Expected separate test Keycloak client'
assert v['ranger']['authorization']['serviceName']=='bdv-test-trino'
assert v['objectStorage']['provider']=='minio' and v['objectStorage']['bucket']=='bdv-test-warehouse'
assert not v['storage'].get('existingMinioClaim'), 'Fresh test must create its own PVC'
assert v['ingress']['bdvHost']=='bdv-test.dinecodetalkers.com'
assert v['ingress']['rangerHost']=='bdv-test-policy-manager.dinecodetalkers.com'
PY
case "$action" in
  secrets|prepare-ranger|status)
    step="$action"
    [[ "$action" != prepare-ranger ]] || step=prepare
    exec bash "$CHART/scripts/setup.sh" "$step" "$values" ;;
  preflight)
    python3 - "$CHART" "$values" <<'PY'
import json, os, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1])/'scripts'))
from values_check import load, merge
chart=Path(sys.argv[1]); v=merge(load(chart/'values.yaml'), load(sys.argv[2])); b=v['bootstrap']
assert b['hive']['database']==b['hive']['username']=='bdv_test_hive'
assert b['ranger']['database']==b['ranger']['username']=='bdv_test_ranger'
assert b['sslMode']=='REQUIRED', 'This fresh-test preflight expects the reviewed REQUIRED TLS profile'
subprocess.run(['kubectl','-n','bdv-test','create','configmap','bdv-fresh-db-check',
                '--from-file=provision.py='+str(chart/'files/provision.py')],check=True)
code='''
import os,sys
from pathlib import Path
sys.path.insert(0,'/code')
from provision import MySQL
try:
    c={'host':os.environ['DB_HOST'],'port':os.environ['DB_PORT'],
       'username':Path('/admin/username').read_text(),'password':Path('/admin/password').read_text()}
    with MySQL(c,None,'REQUIRED') as db:
        schemas=db.query("SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME IN ('bdv_test_hive','bdv_test_ranger')")
        users=db.query("SELECT COUNT(*) FROM mysql.user WHERE User IN ('bdv_test_hive','bdv_test_ranger')")
    if schemas!=['0'] or users!=['0']:
        raise SystemExit('STOP: test schemas or users already exist. Fresh bootstrap refused; nothing deleted')
except Exception:
    raise SystemExit('FAIL: could not verify empty test schemas/users. Check DBA permissions and connectivity') from None
print('PASS: bdv_test_hive and bdv_test_ranger schemas and users are absent')
print('PASS: read-only database preflight; no SQL DDL or grants executed')
'''
image=b['image']; image=image['repository']+'@'+image['digest']
job={'apiVersion':'batch/v1','kind':'Job','metadata':{'name':'bdv-fresh-db-check','namespace':'bdv-test'},
 'spec':{'backoffLimit':0,'activeDeadlineSeconds':180,'template':{'spec':{
 'automountServiceAccountToken':False,'restartPolicy':'Never',
 'securityContext':{'runAsNonRoot':True,'runAsUser':10001,'runAsGroup':10001},
 'containers':[{'name':'check','image':image,'command':['python3','-c',code],
 'env':[{'name':'DB_HOST','value':str(b['host'])},{'name':'DB_PORT','value':str(b['port'])},
        {'name':'PYTHONDONTWRITEBYTECODE','value':'1'}],
 'securityContext':{'readOnlyRootFilesystem':True,'allowPrivilegeEscalation':False,'capabilities':{'drop':['ALL']}},
 'resources':{'requests':{'cpu':'100m','memory':'128Mi'},'limits':{'cpu':'1','memory':'512Mi'}},
 'volumeMounts':[{'name':'code','mountPath':'/code','readOnly':True},
                 {'name':'admin','mountPath':'/admin','readOnly':True},{'name':'tmp','mountPath':'/tmp'}]}],
 'volumes':[{'name':'code','configMap':{'name':'bdv-fresh-db-check'}},
            {'name':'admin','secret':{'secretName':b['adminSecret']}},
            {'name':'tmp','emptyDir':{'sizeLimit':'64Mi'}}]}}}}
subprocess.run(['kubectl','create','-f','-'],input=json.dumps(job),text=True,check=True)
PY
    for attempt in {1..45}; do
      if kubectl -n bdv-test logs -f job/bdv-fresh-db-check --pod-running-timeout=90s; then break; fi
      sleep 2
    done
    kubectl -n bdv-test wait --for=condition=complete job/bdv-fresh-db-check --timeout=60s
    ;;
  bootstrap)
    complete="$(kubectl -n bdv-test get job bdv-fresh-db-check -o jsonpath='{.status.succeeded}')"
    [[ "$complete" == 1 ]] || { echo 'STOP: empty-database preflight has not succeeded'; exit 1; }
    exec bash "$CHART/scripts/setup.sh" bootstrap "$values" ;;
  runtime)
    bash "$CHART/scripts/validate-deployment.sh" --runtime --check -n bdv-test -r bdv -f "$values"
    exec bash "$CHART/scripts/setup.sh" runtime "$values" ;;
  *) echo 'Usage: fresh-test.sh prepare|namespace|secrets|prepare-ranger|preflight|bootstrap|runtime|status'; exit 2 ;;
esac

#!/usr/bin/env bash
# Uninstall only release bdv in bdv-test. Keep namespace, credentials, PVC and DBs.
set -Eeuo pipefail
umask 077
export KUBECONFIG="${KUBECONFIG:-/root/.kube/config}"
mode="${1:---plan}"
[[ "$mode" == --plan || "$mode" == --execute ]] || { echo 'Usage: undeploy-test.sh --plan|--execute'; exit 2; }
label="$(kubectl get namespace bdv-test -o jsonpath='{.metadata.labels.bdv-test-isolated}')"
[[ "$label" == true ]] || { echo 'STOP: expected bdv-test-isolated=true namespace'; exit 1; }
helm -n bdv-test status bdv >/dev/null
# Helm manifest may contain sensitive data: validate through a pipe, never display it.
helm -n bdv-test get manifest bdv | python3 -c '
import sys,yaml
found=False
for d in yaml.safe_load_all(sys.stdin):
    if d and d.get("kind")=="PersistentVolumeClaim" and d["metadata"]["name"]=="bdv-minio":
        found=True
        if d["metadata"].get("annotations",{}).get("helm.sh/resource-policy")!="keep":
            raise SystemExit("STOP: MinIO PVC does not have Helm keep annotation")
if not found: raise SystemExit("STOP: expected retained bdv-minio PVC in installed manifest")
'
kubectl -n bdv-test get pvc bdv-minio
printf '%s\n' 'Target: release bdv in namespace bdv-test only' \
  'Retain: namespace, MySQL schemas/accounts, credential Secrets, MinIO PVC, Keycloak client, DNS/HAProxy configuration' \
  'Hook Jobs and preflight resources can also remain. This does not restore a fresh-install baseline.'
[[ "$mode" == --execute ]] || { echo 'PLAN ONLY: no resources changed'; exit 0; }
helm -n bdv-test uninstall bdv --keep-history --wait --timeout 20m
remaining="$(kubectl -n bdv-test get deployment -l app.kubernetes.io/instance=bdv -o name)"
[[ -z "$remaining" ]] || { echo 'FAIL: release Deployments remain'; exit 1; }
# Completed hook Pods may remain; active release Pods must finish graceful shutdown.
for attempt in {1..120}; do
  active="$(kubectl -n bdv-test get pods -l app.kubernetes.io/instance=bdv -o json | python3 -c '
import json,sys
p=json.load(sys.stdin)
print(sum(x.get("status",{}).get("phase") not in ("Succeeded","Failed") for x in p["items"]))
')"
  [[ "$active" == 0 ]] && break
  sleep 5
done
[[ "$active" == 0 ]] || { echo 'FAIL: active release Pods still exist; inspect before further cleanup'; exit 1; }
kubectl -n bdv-test get pvc bdv-minio
kubectl -n bdv-test get secret -o name
kubectl -n bdv-test get deployment,service,ingress,hpa,pod
printf '%s\n' 'PASS: test release undeployed; recovery data retained'

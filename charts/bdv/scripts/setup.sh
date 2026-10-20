#!/usr/bin/env bash
# Run from the project or chart root. Secrets are never accepted in argv.
set -Eeuo pipefail
umask 077
CHART="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ACTION="${1:-plan}"
VALUES="${2:-$CHART/values-site.yaml}"
NAMESPACE="${NAMESPACE:-bdv}"
RELEASE="${RELEASE:-bdv}"
export KUBECONFIG="${KUBECONFIG:-/root/.kube/config}"
case "$ACTION" in
  plan)
    python3 "$CHART/scripts/setup-secrets.py" -f "$VALUES" -n "$NAMESPACE"
    ;;
  secrets)
    kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
    python3 "$CHART/scripts/setup-secrets.py" -f "$VALUES" -n "$NAMESPACE" --create
    ;;
  prepare)
    bash "$CHART/scripts/prepare-ranger-config.sh" --values "$VALUES"
    ;;
  bootstrap)
    if [[ -n "$(kubectl -n "$NAMESPACE" get deployment -l "app.kubernetes.io/instance=$RELEASE" -o name)" ]]; then
      echo 'Refusing bootstrap-only upgrade while runtime Deployments exist. Use the existing retained DB Secrets; see README migration.' >&2
      exit 2
    fi
    # Explicitly opt in; reruns retain passwords and validate database logins.
    helm upgrade --install "$RELEASE" "$CHART" -n "$NAMESPACE" --reset-values \
      -f "$VALUES" --set bootstrap.only=true --set deployment.enabled=false \
      --wait --wait-for-jobs --timeout 15m
    kubectl -n "$NAMESPACE" logs "job/$RELEASE-bootstrap" --tail=100
    ;;
  runtime|ingress)
    # All confirmation flags must be recorded in the reviewed values file.
    python3 "$CHART/scripts/values_check.py" --defaults "$CHART/values.yaml" --values "$VALUES" --mode runtime
    python3 "$CHART/scripts/cluster_check.py" "$CHART/values.yaml" "$VALUES" "$NAMESPACE" runtime
    if [[ "$ACTION" == ingress ]]; then
      python3 - "$VALUES" <<'PY'
import sys, yaml
v = yaml.safe_load(open(sys.argv[1]))
assert v.get('ingress', {}).get('enabled') is True, 'Set ingress.enabled=true after proxy/DNS review'
PY
    fi
    helm upgrade --install "$RELEASE" "$CHART" -n "$NAMESPACE" --reset-values \
      -f "$VALUES" --set bootstrap.only=false --set deployment.enabled=true \
      --wait --wait-for-jobs --timeout 20m
    ;;
  status)
    helm -n "$NAMESPACE" status "$RELEASE"
    kubectl -n "$NAMESPACE" get deployment,pod,job,pvc,ingress,hpa -l "app.kubernetes.io/instance=$RELEASE"
    ;;
  *) echo 'Usage: setup.sh plan|secrets|prepare|bootstrap|runtime|ingress|status [values-site.yaml]' >&2; exit 2 ;;
esac

#!/usr/bin/env bash
# =============================================================================
# ddc-pme chart: LIVE verification with the SCOPED deployer kubeconfig.
#
#   ci/live-verify.sh [KUBECONFIG]        (default: ../../../../specs/.kube/ddc-pme-deployer.yaml
#                                          relative to the repo, or $DEPLOYER_KUBECONFIG)
#
# It NEVER installs anything: only `--dry-run=server`. Nothing is persisted by
# Helm (no release record). Run it from anywhere; paths are resolved.
#
# Safety rules enforced here
#   * Refuses to run unless the kubeconfig identity is
#     system:serviceaccount:ddc-pme:ddc-pme-deployer (so the admin kubeconfig
#     can never be used by mistake) and the namespace is ddc-pme.
#   * Every kubectl/helm call passes --kubeconfig <that file> -n ddc-pme.
#   * Step 2 needs the 3 Secrets to exist. A Secret that ALREADY exists (the real
#     one, created in Rancher) is used as is and never modified or deleted. A
#     missing one is created with an obviously fake value and the label
#     ddc-pme.verify/temporary=true, and removed again on exit (selected by
#     that label only, so a real Secret can never be deleted).
#
# Steps
#   1  helm install --dry-run=server BEFORE the Secrets exist -> must FAIL with
#      the clear "required Secret ... not found" message (only when none of the
#      Secrets exists yet; otherwise reported as SKIPPED).
#   2  Secrets present -> helm upgrade --install --dry-run=server must PASS for
#      values-dev and values-prod (+ a prod "full" variant: IngressRoute,
#      migrate Job, NetworkPolicy, HPA).
#   3  IngressClass traefik and StorageClass longhorn resolve.
#
# Exit 0 only if every step behaved as described.
# =============================================================================
set -uo pipefail

CHART_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$CHART_DIR/../../.." && pwd)"
KUBECONFIG_FILE="${1:-${DEPLOYER_KUBECONFIG:-$REPO_ROOT/../specs/.kube/ddc-pme-deployer.yaml}}"
NS="ddc-pme"
RELEASE="ddc-pme"
IMAGE_TAG="${IMAGE_TAG:-ci-test}"
WANT_USER="system:serviceaccount:ddc-pme:ddc-pme-deployer"
LABEL="ddc-pme.verify/temporary=true"

[ -f "$KUBECONFIG_FILE" ] || { echo "kubeconfig not found: $KUBECONFIG_FILE" >&2; exit 2; }
case "$(basename "$KUBECONFIG_FILE")" in
  ddc-pme-deployer*.yaml|ddc-pme-deployer*.yml) ;;
  *) echo "refusing: $(basename "$KUBECONFIG_FILE") is not the scoped deployer kubeconfig" >&2; exit 2 ;;
esac

k() { kubectl --kubeconfig "$KUBECONFIG_FILE" -n "$NS" "$@"; }
h() { helm --kubeconfig "$KUBECONFIG_FILE" -n "$NS" "$@"; }

FAILS=0
ok()   { printf '  ok    %s\n' "$*"; }
bad()  { printf '  FAIL  %s\n' "$*"; FAILS=$((FAILS + 1)); }
skip() { printf '  skip  %s\n' "$*"; }

echo "kubeconfig : $(basename "$KUBECONFIG_FILE")"
who="$(k auth whoami -o jsonpath='{.status.userInfo.username}' 2>/dev/null || true)"
echo "identity   : ${who:-<unknown>}"
[ "$who" = "$WANT_USER" ] || { echo "refusing: identity is not $WANT_USER" >&2; exit 2; }
echo "server     : $(k version -o json 2>/dev/null | tr -d '\r\n ' | sed -E 's/.*"serverVersion":\{[^}]*"gitVersion":"([^"]+)".*/\1/')"
echo "helm       : $(helm version --short)"
echo "namespace  : $NS"
echo

SECRETS=(ddc-pme-mysql ddc-pme-redis ddc-pme-minio)
CREATED=()
cleanup() {
  # Delete ONLY what this run created (label selector), never a real Secret.
  if [ "${#CREATED[@]}" -gt 0 ]; then
    echo; echo "cleanup: removing temporary Secrets (label $LABEL)"
    k delete secret -l "$LABEL" --ignore-not-found 2>&1 | sed 's/^/  /'
  fi
}
trap cleanup EXIT

exists() { k get secret "$1" >/dev/null 2>&1; }

COMMON=(--set "global.imageTag=$IMAGE_TAG")

# ---------------------------------------------------------------- Step 1
echo "=== 1. helm install --dry-run=server BEFORE the Secrets exist ==="
present=0; for s in "${SECRETS[@]}"; do exists "$s" && present=$((present + 1)); done
if [ "$present" -eq 0 ]; then
  for profile in dev prod; do
    out="$(h install "$RELEASE" "$CHART_DIR" --dry-run=server -f "$CHART_DIR/values-$profile.yaml" "${COMMON[@]}" 2>&1)"; rc=$?
    echo "  \$ helm install $RELEASE <chart> --dry-run=server -f values-$profile.yaml --set global.imageTag=$IMAGE_TAG"
    echo "$out" | sed 's/^/    /'
    if [ "$rc" -ne 0 ] && echo "$out" | grep -q "required Secret 'ddc-pme-mysql' (keys 'username', 'password') not found in namespace $NS"; then
      ok "$profile: fails with the clear missing-Secret message (exit $rc)"
    else
      bad "$profile: expected the missing-Secret failure (exit $rc)"
    fi
  done
  # Each Secret and each key is reported individually: prove it for redis/minio and a missing key.
else
  skip "$present of ${#SECRETS[@]} Secrets already exist; the 'before' state cannot be shown"
fi
echo

# ---------------------------------------------------------------- Step 2
echo "=== 2. Secrets present: helm upgrade --install --dry-run=server ==="
for s in "${SECRETS[@]}"; do
  if exists "$s"; then
    echo "  using existing Secret $s (not modified)"
  else
    case "$s" in
      ddc-pme-mysql) args=(--from-literal=username=verify-only --from-literal=password=verify-only) ;;
      ddc-pme-redis) args=(--from-literal=password=verify-only) ;;
      ddc-pme-minio) args=(--from-literal=access-key=verify-only --from-literal=secret-key=verify-only-verify-only) ;;
    esac
    if k create secret generic "$s" "${args[@]}" >/dev/null; then
      k label secret "$s" "$LABEL" >/dev/null
      CREATED+=("$s"); echo "  created TEMPORARY Secret $s (fake values, label $LABEL)"
    else
      bad "could not create temporary Secret $s"
    fi
  fi
done

# A half-filled Secret must fail naming the key (checked on a throw-away name).
if [ "${#CREATED[@]}" -gt 0 ] && [[ " ${CREATED[*]} " == *" ddc-pme-redis "* ]]; then
  out="$(h upgrade --install "$RELEASE" "$CHART_DIR" --dry-run=server -f "$CHART_DIR/values-dev.yaml" "${COMMON[@]}" \
         --set existingSecrets.redis.passwordKey=nope 2>&1)"; rc=$?
  if [ "$rc" -ne 0 ] && echo "$out" | grep -q "required Secret 'ddc-pme-redis' in namespace $NS has no key 'nope'"; then
    ok "wrong key name -> fails naming the key: $(echo "$out" | grep -o "required Secret.*")"
  else
    bad "wrong key name did not give the expected message: $out"
  fi
fi

run_dry() { # label, extra helm args...
  local label="$1"; shift
  local out rc
  out="$(h upgrade --install "$RELEASE" "$CHART_DIR" --dry-run=server "${COMMON[@]}" "$@" 2>&1)"; rc=$?
  if [ "$rc" -eq 0 ]; then
    local n; n="$(echo "$out" | grep -c '^kind:')"
    ok "$label: passes ($n objects admitted by the API server)"
  else
    echo "$out" | sed 's/^/    /'; bad "$label: dry-run failed (exit $rc)"
  fi
  LAST="$out"
}
run_dry "dev"        -f "$CHART_DIR/values-dev.yaml"
run_dry "prod"       -f "$CHART_DIR/values-prod.yaml"
run_dry "prod full (IngressRoute, migrate, networkPolicy, HPA)" -f "$CHART_DIR/values-prod.yaml" \
  --set ingress.kind=IngressRoute --set migrate.enabled=true --set networkPolicy.enabled=true \
  --set autoscaling.nginx.enabled=true --set autoscaling.api.enabled=true

# No release must have been recorded, and nothing may have been created.
rel="$(h list -q 2>/dev/null | tr -d '\r')"
[ -z "$rel" ] && ok "no Helm release recorded (dry-run only)" || bad "a release exists: $rel"
left="$(k get deploy,sts,svc,ingress,pvc,job,cm,pdb,hpa,networkpolicy -o name 2>/dev/null | tr -d '\r' | grep -v '^configmap/kube-root-ca.crt$' || true)"  # kube-root-ca.crt: added by Kubernetes to every namespace
[ -z "$left" ] && ok "no workload / service / ingress / PVC object exists in $NS" || bad "unexpected objects exist: $left"
echo

# ---------------------------------------------------------------- Step 3
echo "=== 3. IngressClass and StorageClass resolve ==="
ic="$(kubectl --kubeconfig "$KUBECONFIG_FILE" get ingressclass traefik -o jsonpath='{.metadata.name}|{.spec.controller}' 2>&1)"
[ "$ic" = "traefik|traefik.io/ingress-controller" ] && ok "IngressClass traefik -> controller traefik.io/ingress-controller" || bad "IngressClass traefik: $ic"
sc="$(kubectl --kubeconfig "$KUBECONFIG_FILE" get storageclass longhorn -o jsonpath='{.metadata.name}|{.provisioner}|{.volumeBindingMode}|{.allowVolumeExpansion}' 2>&1)"
[ "${sc%%|*}" = "longhorn" ] && ok "StorageClass longhorn -> provisioner/binding/expansion: ${sc#*|}" || bad "StorageClass longhorn: $sc"
# What the rendered objects actually ask for.
rendered="$(h template "$RELEASE" "$CHART_DIR" -f "$CHART_DIR/values-prod.yaml" "${COMMON[@]}" --set secretValidation.allowOffline=true 2>&1)"
echo "$rendered" | grep -Eq '^\s*ingressClassName: traefik$' && ok "rendered Ingress asks for ingressClassName: traefik" || bad "rendered Ingress does not ask for traefik"
nsc="$(echo "$rendered" | grep -c 'storageClassName: "longhorn"')"
[ "$nsc" -ge 2 ] && ok "rendered StatefulSets ask for storageClassName: longhorn ($nsc volumeClaimTemplates)" || bad "expected >=2 longhorn volumeClaimTemplates, got $nsc"

echo
if [ "$FAILS" -eq 0 ]; then echo "LIVE VERIFY OK"; else echo "LIVE VERIFY FAILED ($FAILS)"; exit 1; fi

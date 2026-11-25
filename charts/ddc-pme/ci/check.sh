#!/usr/bin/env bash
# =============================================================================
# ddc-pme chart: OFFLINE verification. No cluster, no kubeconfig, no network
# access to the cluster is needed (kubeconform fetches JSON schemas over HTTPS).
#
#   ci/check.sh                      lint + kubeconform + assertions, all profiles
#   ci/check.sh --assert-only FILE   run ONLY the assertions on a rendered manifest
#                                    (used to prove the assertions can fail)
#
# What runs, for each profile (values-dev.yaml, values-prod.yaml) and each
# variant (base = defaults, full = IngressRoute + migrate + networkPolicy + HPA):
#   1. helm lint --strict        (secretValidation.allowOffline=true, test tag)
#   2. helm template | kubeconform -strict, with the Traefik CRD schemas
#   3. the policy assertions below on the rendered manifest
#
# Assertions
#   A1  no NodePort / LoadBalancer Service (every Service is ClusterIP)
#   A2  the only Ingress / IngressRoute backend is <release>-nginx
#   A3  api, redis, minio (and worker) are not referenced by any ingress object
#   A4  no `kind: Secret` is rendered (Helm never creates a credential)
#   A5  no TLS: no tls block, no tls annotation, no websecure entry point
#   A6  every image is docker.io/rstevens198238/* or redis, with a pinned,
#       non-`latest` tag
#   A7  every storageClassName is longhorn; MinIO's volumeClaimTemplate has one
#   A8  no '-dev' / '-prod' suffix on any name
#   A9  exactly one ingress object, ingressClassName traefik (kind Ingress)
#   A10 single sign-on wiring is self-consistent: the API's OIDC_ISSUER equals
#       the SPA's env-config.js issuer, OIDC_AUDIENCE is non-empty, OIDC_JWKS_URI
#       is the issuer's discovery path, the issuer host is not a placeholder, and
#       oidcOnly is true (the password form can only answer 410)
#
# Environment
#   RELEASE        release name used for rendering (default ddc-pme)
#   NAMESPACE      namespace used for rendering   (default ddc-pme)
#   IMAGE_TAG      test tag                       (default ci-test)
#   K8S_VERSION    kubeconform schema version     (default 1.34.1)
#   KUBECONFORM    path to the kubeconform binary (default: PATH, then ../../.tools)
#   SCHEMA_CACHE   kubeconform schema cache dir   (default <tmp>/kubeconform-cache)
# =============================================================================
set -euo pipefail

CHART_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RELEASE="${RELEASE:-ddc-pme}"
NAMESPACE="${NAMESPACE:-ddc-pme}"
IMAGE_TAG="${IMAGE_TAG:-ci-test}"
K8S_VERSION="${K8S_VERSION:-1.34.1}"
CRD_SCHEMA='https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'

NGINX_SVC="${RELEASE}-nginx"
FAILS=0
CHECKS=0

pass() { CHECKS=$((CHECKS + 1)); printf '  ok    %s\n' "$*"; }
fail() { CHECKS=$((CHECKS + 1)); FAILS=$((FAILS + 1)); printf '  FAIL  %s\n' "$*"; }

# ---------------------------------------------------------------------------
# Assertions on one rendered manifest. $1 = file, $2 = label.
# ---------------------------------------------------------------------------
assert_manifest() {
  local file="$1" label="$2" dir n kind name f
  dir="$(mktemp -d)"
  tr -d '\r' < "$file" > "$dir/all.yaml"
  # One file per YAML document.
  awk -v d="$dir" 'BEGIN{n=0} /^---[ \t]*$/ {n++; next} {print > sprintf("%s/doc%03d.yaml", d, n)}' "$dir/all.yaml"

  echo "assertions: $label"

  # A1 -- Services
  local bad=0
  for f in "$dir"/doc*.yaml; do
    kind="$(awk '/^kind:/{print $2; exit}' "$f")"
    [ "$kind" = "Service" ] || continue
    name="$(awk '/^metadata:/{m=1;next} m&&/^  name:/{gsub(/["'"'"']/,"",$2);print $2;exit}' "$f")"
    local t
    t="$(awk '/^spec:/{s=1;next} s&&/^  type:/{gsub(/["'"'"']/,"",$2);print $2;exit}' "$f")"
    if [ -n "$t" ] && [ "$t" != "ClusterIP" ]; then echo "        Service $name has type $t"; bad=1; fi
  done
  if grep -Eq '^\s*(type:\s*"?(NodePort|LoadBalancer)"?|nodePort:|loadBalancerIP:|externalIPs:)' "$dir/all.yaml"; then bad=1; fi
  [ "$bad" -eq 0 ] && pass "A1 no NodePort / LoadBalancer (all Services ClusterIP)" || fail "A1 NodePort / LoadBalancer found"

  # Ingress documents
  local ing_docs=() ing_count=0
  for f in "$dir"/doc*.yaml; do
    kind="$(awk '/^kind:/{print $2; exit}' "$f")"
    case "$kind" in Ingress|IngressRoute|IngressRouteTCP|IngressRouteUDP) ing_docs+=("$f"); ing_count=$((ing_count + 1));; esac
  done

  # A9 -- exactly one ingress object
  if [ "$ing_count" -eq 1 ]; then pass "A9 exactly one ingress object"; else fail "A9 expected 1 ingress object, found $ing_count"; fi

  # A2 -- backends
  local backends="" nb
  for f in "${ing_docs[@]}"; do
    kind="$(awk '/^kind:/{print $2; exit}' "$f")"
    if [ "$kind" = "Ingress" ]; then
      nb="$(awk '/^ +service:/{s=1;next} s&&/^ +name:/{gsub(/["'"'"']/,"",$2);print $2;s=0}' "$f")"
      if grep -Eq '^\s*(defaultBackend:|serviceName:)' "$f"; then nb="$nb defaultBackend-or-legacy"; fi
    else
      nb="$(awk '/^ +- name:/{gsub(/["'"'"']/,"",$3);print $3}' "$f")"
    fi
    backends="$backends $nb"
  done
  backends="$(echo $backends | tr ' ' '\n' | sort -u | grep -v '^$' || true)"
  if [ "$backends" = "$NGINX_SVC" ]; then pass "A2 only ingress backend is $NGINX_SVC"; else fail "A2 ingress backends are [$(echo $backends)], expected only $NGINX_SVC"; fi

  # A3 -- api / redis / minio / worker never referenced by an ingress object
  local leak=0 c
  for f in "${ing_docs[@]}"; do
    for c in api redis minio worker; do
      if grep -Eq -- "${RELEASE}-${c}([^a-z0-9-]|\$)" "$f"; then
        echo "        ingress object references ${RELEASE}-${c}"; leak=1
      fi
    done
  done
  [ "$leak" -eq 0 ] && pass "A3 api / redis / minio / worker are not in the ingress" || fail "A3 a backend component is exposed by the ingress"

  # A4 -- no Secret
  if grep -Eq '^kind:\s*"?Secret"?\s*$' "$dir/all.yaml"; then fail "A4 a kind: Secret is rendered"; else pass "A4 no kind: Secret rendered"; fi

  # A5 -- no TLS
  local tls=0
  if grep -Eq '^\s*tls:|router\.tls|websecure|kind:\s*(Certificate|TLSOption|TLSStore|IngressRouteTCP)\b|cert-manager\.io' "$dir/all.yaml"; then
    tls=1; grep -nE '^\s*tls:|router\.tls|websecure|kind:\s*(Certificate|TLSOption|TLSStore|IngressRouteTCP)\b|cert-manager\.io' "$dir/all.yaml" | sed 's/^/        /'
  fi
  [ "$tls" -eq 0 ] && pass "A5 no TLS configured on the ingress" || fail "A5 TLS configuration found"

  # A6 -- images
  local imgs img badimg=0
  imgs="$(grep -E '^\s*(-\s+)?image:' "$dir/all.yaml" | sed -E 's/^\s*(-\s+)?image:\s*//; s/["'"'"']//g; s/\s+$//' | sort -u || true)"
  if [ -z "$imgs" ]; then badimg=1; echo "        no image found"; fi
  while IFS= read -r img; do
    [ -n "$img" ] || continue
    if ! echo "$img" | grep -Eq '^(docker\.io/rstevens198238/[a-z0-9._-]+|redis):[A-Za-z0-9._-]+$' || echo "$img" | grep -Eqi ':latest$'; then
      echo "        bad image: $img"; badimg=1
    fi
  done <<< "$imgs"
  [ "$badimg" -eq 0 ] && pass "A6 images are rstevens198238/* or redis, pinned, none latest ($(echo "$imgs" | wc -l | tr -d ' ') distinct)" || fail "A6 image policy violated"

  # A7 -- storage class
  local sc badsc=0
  while IFS= read -r sc; do
    [ -n "$sc" ] || continue
    [ "$sc" = "longhorn" ] || { echo "        storageClassName: $sc"; badsc=1; }
  done < <(grep -E '^\s*storageClassName:' "$dir/all.yaml" | awk '{gsub(/["'"'"']/,"",$2);print $2}')
  local minio_sts="" minio_vct=0
  for f in "$dir"/doc*.yaml; do
    kind="$(awk '/^kind:/{print $2; exit}' "$f")"
    name="$(awk '/^metadata:/{m=1;next} m&&/^  name:/{gsub(/["'"'"']/,"",$2);print $2;exit}' "$f")"
    if [ "$kind" = "StatefulSet" ] && [ "$name" = "${RELEASE}-minio" ]; then
      minio_sts="$f"
      if grep -Eq '^\s*volumeClaimTemplates:' "$f" && awk '/volumeClaimTemplates:/{v=1} v&&/storageClassName:/{gsub(/["'"'"']/,"",$2); if($2=="longhorn") ok=1} END{exit ok?0:1}' "$f"; then minio_vct=1; fi
    fi
  done
  if [ -z "$minio_sts" ]; then badsc=1; echo "        MinIO StatefulSet not found"; fi
  [ "$minio_vct" -eq 1 ] || { badsc=1; echo "        MinIO volumeClaimTemplate has no storageClassName: longhorn"; }
  [ "$badsc" -eq 0 ] && pass "A7 MinIO PVC uses longhorn (every storageClassName is longhorn)" || fail "A7 storage class policy violated"

  # A8 -- no -dev / -prod names
  if grep -Eni -- '-(dev|prod)(\b|-)' "$dir/all.yaml" | sed 's/^/        /' | grep .; then fail "A8 '-dev' / '-prod' found in a rendered name"; else pass "A8 no '-dev' / '-prod' names"; fi

  # A9b -- Ingress kind: class traefik
  for f in "${ing_docs[@]}"; do
    kind="$(awk '/^kind:/{print $2; exit}' "$f")"
    if [ "$kind" = "Ingress" ]; then
      if grep -Eq '^\s*ingressClassName:\s*"?traefik"?\s*$' "$f"; then pass "A9 Ingress has ingressClassName: traefik"; else fail "A9 Ingress ingressClassName is not traefik"; fi
    fi
  done

  # A10 -- single sign-on consistency.
  #
  # WHY THIS EXISTS
  # ---------------
  # The chart renders the Keycloak settings into TWO consumers: the API's
  # OIDC_* environment (which decides whether a token is accepted) and the SPA's
  # env-config.js (which decides where the browser is sent to get one). Both come
  # from keycloak.*, so they agree today - but nothing STRUCTURALLY prevents a
  # change, or a hand-written extraEnv, from making them disagree. When they do,
  # every user gets a valid token from the issuer the SPA used and a universal
  # 401 from the API, with no other symptom. That is worth a render-time check
  # rather than an operator reading a ConfigMap inside a pod.
  local iss aud jwks cfg_iss cfg_only bad10=0
  iss="$(awk -F'"' '/^  OIDC_ISSUER:/{print $2; exit}' "$dir/all.yaml")"
  aud="$(awk -F'"' '/^  OIDC_AUDIENCE:/{print $2; exit}' "$dir/all.yaml")"
  jwks="$(awk -F'"' '/^  OIDC_JWKS_URI:/{print $2; exit}' "$dir/all.yaml")"
  cfg_iss="$(awk -F'"' '/"issuer":/{print $4; exit}' "$dir/all.yaml")"
  cfg_only="$(awk -F': ' '/"oidcOnly":/{gsub(/[^a-z]/,"",$2); print $2; exit}' "$dir/all.yaml")"

  [ -n "$iss" ]     || { echo "        API has no OIDC_ISSUER"; bad10=1; }
  [ -n "$cfg_iss" ] || { echo "        env-config.js has no issuer"; bad10=1; }
  if [ -n "$iss" ] && [ -n "$cfg_iss" ] && [ "$iss" != "$cfg_iss" ]; then
    echo "        issuer drift: API OIDC_ISSUER=$iss but SPA issuer=$cfg_iss"; bad10=1
  fi
  # The API verifies `aud` only when configured; empty silently accepts tokens
  # minted for any other client of the same realm.
  [ -n "$aud" ] || { echo "        OIDC_AUDIENCE is empty (aud would not be checked)"; bad10=1; }
  if [ -n "$iss" ] && [ -n "$jwks" ] && [ "$jwks" != "${iss}/protocol/openid-connect/certs" ]; then
    echo "        OIDC_JWKS_URI is not the standard discovery path of the issuer: $jwks"; bad10=1
  fi
  case "$iss" in
    *.invalid*|*example*) echo "        issuer host looks like a placeholder: $iss"; bad10=1 ;;
  esac
  # The password grant answers 410 in every environment (the chart refuses to
  # inject ALLOW_PASSWORD_GRANT), so offering the form is offering a dead end.
  [ "$cfg_only" = "true" ] || { echo "        env-config.js oidcOnly is '$cfg_only', so the login page shows a form that can only fail"; bad10=1; }

  [ "$bad10" -eq 0 ] && pass "A10 SSO config consistent: issuer=$iss audience=$aud oidcOnly=true" || fail "A10 single sign-on configuration inconsistent"

  rm -rf "$dir"
}

# ---------------------------------------------------------------------------
if [ "${1:-}" = "--assert-only" ]; then
  [ -f "${2:-}" ] || { echo "usage: $0 --assert-only RENDERED.yaml" >&2; exit 2; }
  assert_manifest "$2" "$2"
  echo; echo "$((CHECKS - FAILS))/$CHECKS checks passed"
  [ "$FAILS" -eq 0 ]
  exit $?
fi

command -v helm >/dev/null || { echo "helm not found" >&2; exit 2; }

# ---------------------------------------------------------------------------
# --self-test: prove each assertion CAN fail. Renders prod, applies one
# mutation at a time and requires the matching Axx to report FAIL.
# ---------------------------------------------------------------------------
if [ "${1:-}" = "--self-test" ]; then
  ST="$(mktemp -d)"; trap 'rm -rf "$ST"' EXIT
  helm template "$RELEASE" "$CHART_DIR" -n "$NAMESPACE" -f "$CHART_DIR/values-prod.yaml" \
    --set "global.imageTag=$IMAGE_TAG" --set secretValidation.allowOffline=true > "$ST/good.yaml"
  helm template "$RELEASE" "$CHART_DIR" -n "$NAMESPACE" -f "$CHART_DIR/values-prod.yaml" \
    --set "global.imageTag=$IMAGE_TAG" --set secretValidation.allowOffline=true \
    --set ingress.kind=IngressRoute > "$ST/good-ir.yaml"
  bad=0
  # expect NAME "sed-expression" SOURCE EXPECTED-FAIL-ID
  expect_fail() {
    local name="$1" expr="$2" src="$3" id="$4" out
    sed -E "$expr" "$ST/$src" > "$ST/mut.yaml"
    if cmp -s "$ST/$src" "$ST/mut.yaml"; then echo "  SELFTEST BROKEN  $name (mutation changed nothing)"; bad=1; return; fi
    out="$(assert_manifest "$ST/mut.yaml" "$name" 2>&1 || true)"
    if echo "$out" | grep -q "FAIL  $id "; then printf '  ok    %-34s -> %s fails as expected\n' "$name" "$id"
    else echo "  SELFTEST BROKEN  $name: $id did not fail"; bad=1; fi
  }
  echo "self-test: every assertion must be able to fail"
  expect_fail "api Service -> NodePort"        '0,/^  type: ClusterIP/s//  type: NodePort/'                    good.yaml A1
  expect_fail "nginx Service -> LoadBalancer"  's/^  type: ClusterIP/  type: LoadBalancer/'                   good.yaml A1
  expect_fail "ingress backend -> api"         's/(^ +name: )ddc-pme-nginx$/\1ddc-pme-api/; s/(^ +name: ")ddc-pme-nginx"$/\1ddc-pme-api"/' good.yaml A2
  expect_fail "IngressRoute extra minio route" 's/^(\s+)- name: ddc-pme-nginx$/\1- name: ddc-pme-minio/'     good-ir.yaml A2
  expect_fail "ingress references redis"       's/^(\s+)- name: ddc-pme-nginx$/\1- name: ddc-pme-redis/'     good-ir.yaml A3
  expect_fail "a Secret is rendered"           's/^kind: ServiceAccount$/kind: Secret/'                      good.yaml A4
  expect_fail "tls block on ingress"           's/^spec:$/spec:\n  tls:\n    - secretName: x/;t'             good.yaml A5
  expect_fail "websecure entry point"          's/"web"/"websecure"/'                                         good.yaml A5
  expect_fail "image :latest"                  's#(image: "docker.io/rstevens198238/pme-backend):ci-test"#\1:latest"#' good.yaml A6
  expect_fail "image from another registry"    's#image: "redis:7-alpine"#image: "quay.io/evil/redis:7"#'    good.yaml A6
  expect_fail "image without tag"              's#image: "redis:7-alpine"#image: "redis"#'                   good.yaml A6
  expect_fail "storageClass -> local-path"     's/storageClassName: "longhorn"/storageClassName: "local-path"/' good.yaml A7
  expect_fail "name with -dev"                 's/^  name: ddc-pme-redis$/  name: ddc-pme-redis-dev/'        good.yaml A8
  expect_fail "ingressClassName -> nginx"      's/ingressClassName: traefik/ingressClassName: nginx/'        good.yaml A9
  # A10. Each mutation breaks exactly one aspect of the SSO wiring; the two
  # issuer mutations keep the other consumer untouched so drift is isolated.
  expect_fail "API issuer drifts from SPA"     's#^  OIDC_ISSUER: ".*"#  OIDC_ISSUER: "https://sso.dinecodetalkers.com/realms/other"#' good.yaml A10
  expect_fail "OIDC_AUDIENCE emptied"          's/^  OIDC_AUDIENCE: "pme-api"/  OIDC_AUDIENCE: ""/'          good.yaml A10
  expect_fail "JWKS URI off the issuer path"   's#^  OIDC_JWKS_URI: ".*"#  OIDC_JWKS_URI: "https://elsewhere.example.org/certs"#' good.yaml A10
  expect_fail "issuer -> placeholder host"     's#sso\.dinecodetalkers.com#keycloak.example.invalid#g'       good.yaml A10
  expect_fail "oidcOnly -> false"              's/"oidcOnly": true/"oidcOnly": false/'                        good.yaml A10
  [ "$bad" -eq 0 ] && { echo "self-test OK"; exit 0; } || { echo "self-test FAILED"; exit 1; }
fi

# kubeconform: env override, PATH, then the repo-local tools dir (git-ignored).
KC="${KUBECONFORM:-}"
if [ -z "$KC" ]; then
  if command -v kubeconform >/dev/null 2>&1; then KC="kubeconform"
  elif [ -x "$CHART_DIR/../.tools/kubeconform" ]; then KC="$CHART_DIR/../.tools/kubeconform"
  elif [ -x "$CHART_DIR/../.tools/kubeconform.exe" ]; then KC="$CHART_DIR/../.tools/kubeconform.exe"
  else echo "kubeconform not found (install it or set KUBECONFORM)" >&2; exit 2; fi
fi
CACHE="${SCHEMA_CACHE:-${TMPDIR:-/tmp}/kubeconform-cache}"
mkdir -p "$CACHE"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "helm $(helm version --short)  |  $("$KC" -v | head -1)  |  schemas k8s $K8S_VERSION + datreeio CRDs-catalog (traefik.io)"
echo

# Variant "full" turns on every optional object so kubeconform sees it all.
BASE_SET=(--set "global.imageTag=$IMAGE_TAG" --set secretValidation.allowOffline=true)
FULL_SET=(--set ingress.kind=IngressRoute --set migrate.enabled=true --set networkPolicy.enabled=true
          --set autoscaling.nginx.enabled=true --set autoscaling.api.enabled=true)

for profile in dev prod; do
  VALUES="$CHART_DIR/values-$profile.yaml"
  for variant in base full; do
    label="$profile/$variant"
    extra=()
    [ "$variant" = "full" ] && extra=("${FULL_SET[@]}")
    out="$WORK/$profile-$variant.yaml"

    echo "=== $label ==="
    echo "helm lint --strict"
    if helm lint "$CHART_DIR" --strict -f "$VALUES" "${BASE_SET[@]}" "${extra[@]}" > "$WORK/lint.log" 2>&1; then
      pass "lint: $(grep -E 'chart\(s\) linted' "$WORK/lint.log" | tr -d '\r')"
    else
      cat "$WORK/lint.log"; fail "helm lint failed ($label)"
    fi

    echo "helm template | kubeconform"
    if helm template "$RELEASE" "$CHART_DIR" -n "$NAMESPACE" -f "$VALUES" "${BASE_SET[@]}" "${extra[@]}" > "$out" 2> "$WORK/tpl.err"; then
      if "$KC" -strict -summary -kubernetes-version "$K8S_VERSION" -cache "$CACHE" \
           -schema-location default -schema-location "$CRD_SCHEMA" < "$out" > "$WORK/kc.log" 2>&1; then
        pass "kubeconform: $(tail -1 "$WORK/kc.log" | tr -d '\r')"
      else
        cat "$WORK/kc.log"; fail "kubeconform failed ($label)"
      fi
      if [ "$variant" = "full" ]; then
        grep -q '^kind: IngressRoute' "$out" && pass "IngressRoute validated against the Traefik CRD schema" || fail "full variant did not render an IngressRoute"
      fi
    else
      cat "$WORK/tpl.err"; fail "helm template failed ($label)"
    fi

    assert_manifest "$out" "$label"
    echo
  done
done

echo "$((CHECKS - FAILS))/$CHECKS checks passed"
if [ "$FAILS" -ne 0 ]; then echo "FAILED: $FAILS check(s)"; exit 1; fi
echo "OK"

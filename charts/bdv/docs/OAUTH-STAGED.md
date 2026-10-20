# Staged Keycloak OAuth candidate, 0.1.29

This is opt-in. Existing dev values keep OAuth disabled. Nothing in this package
edits the two operator-created credential Secrets or installs cluster-wide
Traefik configuration. Production AWS S3 selection remains unchanged.

## Established live evidence

- Helm revision 6 completed one combined DBA bootstrap. Both application logins,
  database collation and exact restricted grants passed.
- Helm revision 7 deployed the final-name coordinator, Hive, Ranger and MinIO.
  All four reached Ready with zero restarts; workers remained zero.
- MinIO uses a new 100Gi Longhorn PVC, not the 5Gi check PVC.
- Scoped warehouse credentials wrote/read one object. Trino created
  `hive.default.bdv_smoke_20261019`, inserted and read one row. Ranger first
  denied the column read, then allowed only the two explicitly granted columns.
  A subsequent scoped listing found its data file under
  `bdv-warehouse/warehouse/bdv_smoke_20261019/`.
- The raw HTTP readiness command passed against the pinned coordinator image.
  Local tests reject HTTP 401 and `starting:true`, and accept HTTP 200 with
  `starting:false`. This is process readiness, not a replacement for SQL tests.
- The Keycloak `bdv` confidential client was created in realm `innonet` through
  the existing Ansible workflow. The verified issuer is
  `https://sso.dinecodetalkers.com/realms/innonet`.
- Existing `bdv-keycloak` keys are `issuer`, `clientId`, `clientSecret`.
  Existing `bdv-internal` key is `sharedSecret` (64 random bytes, base64).
- HAProxy terminates HTTPS at port 443, routes both application hostnames to
  `rancher_workers`, currently worker 02 at `192.168.250.18:80`. The supplied
  rules set `X-Forwarded-Proto=https` on TLS connections and forwarded port 443.
  Traefik trusts forwarded headers from `192.168.250.1/32` and
  `192.168.200.1/32`. No global insecure forwarded-header flag was shown.

These observations do not prove OAuth startup, a browser login, Java trust of
Keycloak, rejection of spoofed forwarded headers, or authenticated user access
to a catalog. Those remain live validation gates. The backend screenshot also
shows only one worker and no health check; external-path high availability is
not established.

## Prepare without changing the running release

From the extracted chart directory with the existing kubeconfig:

```bash
python3 scripts/test_trino_oauth.py
python3 scripts/test_dba_contract.py
bash scripts/prepare-ranger-config.sh
bash scripts/validate-deployment.sh --offline -f values-dev.yaml
python3 scripts/prepare-trino-oauth.py --values values-dev.yaml
```

The preparer defaults to plan-only. It checks the three existing Secrets,
validates the source config and refuses existing security settings rather than
silently replacing them. It prints no Secret content. It creates no resources
or files unless `--create` is added. Review that plan before continuing:

```bash
python3 scripts/prepare-trino-oauth.py --values values-dev.yaml --create
```

The explicit create step makes `bdv-trino-coordinator-oidc`, preserving all
original coordinator files and appending only OAuth configuration references.
It leaves `bdv-trino-coordinator` unchanged as the known-good configuration.
An existing matching candidate is verified and reused; a differing one causes
a hard stop. It also writes mode-600 `values-dev-oidc.yaml`, with OAuth selected,
the candidate Secret selected, workers zero, ingress disabled, and all
confirmation flags false. It does not enable authentication or restart pods.

Actual OAuth and internal secret values are resolved using `${ENV:...}` from
`secretKeyRef` in the main coordinator container. They are not embedded in Helm
values, config.properties, startup arguments, or prerequisite-container env.
Worker templates receive only the internal shared-secret environment reference,
not the OAuth client secret. The candidate gate disallows nonzero workers until
their authenticated config is separately prepared and verified.

The OAuth principal is `preferred_username`. The groups mapper alone does not
grant Ranger access or synchronize Ranger users/groups. After a real login,
verify the actual principal and deliberately provision narrow Ranger permissions
for that identity. Do not give all Keycloak users the health-check user's access.

## Dry runtime render only

After reviewing the plan and creating the candidate, render with explicit
confirmation flags only for local validation:

```bash
(
  set -Eeuo pipefail
  umask 077
  render="$(mktemp /root/bdv-oidc-render.XXXXXX.yaml)"
  trap 'rm -f "$render"' EXIT
  helm lint . -f values-dev-oidc.yaml \
    --set deployment.enabled=true \
    --set deployment.evidenceConfirmed=true \
    --set deployment.proxyBoundaryConfirmed=true
  helm template bdv . -n bdv -f values-dev-oidc.yaml \
    --kube-version 1.36.4 \
    --set deployment.enabled=true \
    --set deployment.evidenceConfirmed=true \
    --set deployment.proxyBoundaryConfirmed=true > "$render"
  python3 scripts/render_check.py "$render" runtime
  kubeconform -strict -summary -exit-on-error \
    -kubernetes-version 1.36.4 "$render"
)
python3 scripts/cluster_check.py values.yaml values-dev-oidc.yaml bdv runtime
```

This does not authorize or perform an upgrade. The read-only cluster check also
verifies the coordinator config uses the expected Secret-backed placeholders;
it never prints their contents.

## Gate before live OAuth or ingress

Review the rendered delta. Keep ingress disabled for the first OAuth startup.
Confirm HAProxy preserves the intended Host and removes/overwrites untrusted
`Forwarded` and `X-Forwarded-*` input. Confirm the actual backend source matches
Traefik trustedIPs; do not widen trust to a subnet or enable insecure mode.
Restrict direct access to backend ports to trusted paths. Validate public DNS
and certificate names before adding ingress. Neither frontend screenshots nor
an ingress URL displayed in Rancher establish these protections by themselves.

Do not enable `http-server.authentication.allow-insecure-over-http` to get a
probe or CLI working. HTTP `/v1/info` readiness is separate from authenticated
queries. First OAuth startup must confirm discovery/JWKS trust, no rejected
config properties, `starting:false`, and denial of unauthenticated query access.
Then expose only the intended HTTPS URLs and verify callback/return URLs.

Use the existing retained application/database/warehouse Secrets and PVC.
Do not delete the check stack, databases, smoke table or smoke policies during
this authentication change. Before any live upgrade, retain revision 7 as the
known-good release reference and verify `bdv-trino-coordinator` still exists.
A Helm rollback changes workload configuration, not MySQL schema or S3 data.
Do not remove the new credential Secrets to fix an OAuth error.

Reference contracts:
- https://trino.io/docs/current/security/oauth2.html
- https://trino.io/docs/current/security/tls.html
- https://trino.io/docs/current/security/internal-communication.html
- https://trino.io/docs/current/security/secrets.html

The pinned image identifies itself as a dirty/custom build; upstream docs are
configuration references, not proof that this exact binary supports every key.
Live authenticated validation remains required. No OAuth Helm upgrade was run
from the editing workspace.

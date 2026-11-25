# ddc-pme Helm chart

Deploys the PME stack to Kubernetes (Rancher, Traefik): nginx + SPA, NestJS API,
worker, Redis, optional MinIO. The database (remote Percona MySQL) and Keycloak
are external.

**Helm never creates a Secret or a credential.** Create these in the release
namespace (Rancher > Storage > Secrets) before installing:

| Secret | Keys | Needed |
|---|---|---|
| `ddc-pme-mysql` | `username`, `password` | always |
| `ddc-pme-redis` | `password` | always |
| `ddc-pme-minio` | `access-key`, `secret-key` | always |
| `ddc-pme-keycloak` | `client-secret` | never in practice — only if `keycloak.publicClient=false`, a mode the render rejects |

Names and keys are configurable under `existingSecrets.*`. The password in
`ddc-pme-mysql` is spliced into `DATABASE_URL` verbatim, so it must be URL-safe.

## Install

```sh
helm upgrade --install ddc-pme charts/ddc-pme -n ddc-pme \
  --set global.imageTag=1.2.3 \
  --set mysql.host=<percona-host> \
  --set keycloak.baseUrl=https://<keycloak-host>
```

`global.imageTag` is required and `latest` is rejected.

## Sizing profiles (values-dev.yaml / values-prod.yaml)

```sh
helm upgrade --install ddc-pme charts/ddc-pme -n ddc-pme \
  -f charts/ddc-pme/values-prod.yaml -f image-tags.yaml     # or values-dev.yaml
```

Both profiles target the **same** names: namespace `ddc-pme`, release `ddc-pme`,
host `ddc-pme.dinecodetalkers.com`, Keycloak client `ddc-pme` (realm `innonet`,
`https://sso.dinecodetalkers.com`, redirect `.../auth/callback`), database
`ddc_pme` on `192.168.250.15:3306`. Nothing carries a `-dev` suffix. They differ
only in sizing and runtime settings:

| | dev | prod |
|---|---|---|
| nginx / api / worker replicas | 1 / 1 / 1 | 2 / 2 / 2 |
| api resources (limits) | 0.5 CPU / 512Mi | 1 CPU / 768Mi (docker-compose) |
| worker resources (limits) | 1 CPU / 1Gi | 2 CPU / 1536Mi (docker-compose) |
| nginx resources (limits) | 0.25 CPU / 128Mi | 0.5 CPU / 128Mi (docker-compose) |
| DB pool api / worker (per Pod) | 5 / 3 | 20 / 5 |
| `LOG_LEVEL` | `debug` | `info` |
| MinIO / Redis volume (Longhorn) | 5Gi / 1Gi | 50Gi / 5Gi |
| Redis `maxmemory` | 128mb | 256mb |
| podAntiAffinity (soft, per component), PDBs, control-plane excluded | off | on (`minAvailable: 1`) |
| HPA (`autoscaling.api/nginx`) | off | off, ready to enable |

**The two profiles are not meant to run side by side.** They share a namespace,
a host, a Keycloak client and a database, so installing both would collide.
Switching is a `helm upgrade` of the same release with the other file.

* The profiles carry **no secrets** and **no image tag**. The tag comes from the
  `image-tags.yaml` overlay (`global.imageTag: <immutable tag>`; a component may
  also set `<component>.image.tag`). A missing tag fails the render.
* Enabling the HPA (`--set autoscaling.api.enabled=true`) drops `replicas` from
  the Deployment so the autoscaler owns it. Check the Percona `max_connections`
  first: connections = api Pods x `api.databasePoolMax` (+ worker Pods x its pool).
* PDBs are skipped for any component with fewer than 2 replicas (dev).
* Anti-affinity is `preferred` on purpose: with `maxUnavailable: 0` / `maxSurge: 1`
  a hard rule leaves a rolling update Pending while a worker is down. Set
  `scheduling.podAntiAffinity.mode=required` only if you accept that.
* **Volume size changes on a switch.** Kubernetes rejects edits to a
  StatefulSet's `volumeClaimTemplates`, so a dev <-> prod `helm upgrade` fails on
  `ddc-pme-minio` / `ddc-pme-redis` (the existing PVCs are not touched). Once,
  per StatefulSet, keep the Pods and the data and let Helm recreate the object:
  ```sh
  kubectl -n ddc-pme delete statefulset ddc-pme-minio ddc-pme-redis --cascade=orphan
  # dev -> prod only: grow the volumes (Longhorn supports expansion)
  kubectl -n ddc-pme patch pvc data-ddc-pme-minio-0 -p '{"spec":{"resources":{"requests":{"storage":"50Gi"}}}}'
  kubectl -n ddc-pme patch pvc data-ddc-pme-redis-0 -p '{"spec":{"resources":{"requests":{"storage":"5Gi"}}}}'
  helm upgrade ... -f values-prod.yaml -f image-tags.yaml
  ```
  (check the real PVC names with `kubectl -n ddc-pme get pvc`). A PVC cannot
  shrink, so prod -> dev keeps the larger volumes. Do this in a quiet window:
  Redis/MinIO are briefly unavailable while the Pods restart.


## Frontend (nginx) and ingress

| Object | What |
|---|---|
| `...-nginx` Deployment + Service | `pme-frontend` image, ClusterIP `:80`. Probes `GET /healthz` on `:80` (never touches the API). |
| `...-nginx-config` ConfigMap | Mounted with `subPath` over `/etc/nginx/conf.d/default.conf`, `/etc/nginx/snippets/security-headers.conf` and `/usr/share/nginx/html/env-config.js`. A `checksum/config` Pod annotation rolls the Deployment when any of them changes. |
| `...-nginx` Ingress or IngressRoute | `ingress.kind`: `Ingress` (networking.k8s.io/v1, `ingressClassName: traefik`, annotation `router.entrypoints: web`) or `IngressRoute` (traefik.io/v1alpha1, `entryPoints: [web]`). Routes **only** to the nginx Service. No `tls` by default; the chart never deploys Traefik. |

* `location /api/v1/` proxies to `http://<release>-ddc-pme-api.<namespace>.svc.<nginx.clusterDomain>:<api.service.port>` using `nginx.resolver` (default: RKE2 CoreDNS Service `rke2-coredns-rke2-coredns.kube-system.svc.cluster.local`, 10.43.0.10; set the IP if you prefer). The name is resolved per request, so a missing API never stops nginx from starting.
* `X-Forwarded-Proto` / `X-Forwarded-Port` set by pfSense/Traefik are preserved (falling back to the local scheme/port only when absent); `X-Forwarded-For` is appended. Traefik's `forwardedHeaders.trustedIPs` (pfSense) is configured outside this chart (digin-automation `rancher-web-ui`).
* `client_max_body_size` is `app.maxUploadBytes`. CSP `connect-src` is `'self'` + the Keycloak origin (+ `nginx.extraConnectSrc`).
* `env-config.js` is rendered from `keycloak.*` (`issuer` = `<baseUrl>/realms/<realm>`, `clientId`, `redirectUri`, `postLogoutRedirectUri`).
* The nginx config is a Helm copy of `ddc-pme/nginx.conf` and `ddc-pme/nginx-security-headers.conf`: keep them in sync.


## Backend (api, worker, migrate)

| Object | What |
|---|---|
| `<release>-ddc-pme-api` Deployment + Service | `node dist/main.js`, ClusterIP `:8000`. Readiness `/api/v1/health/ready` (DB), liveness/startup `/api/v1/health/live`. **Not referenced by any Ingress**: browsers reach `/api/v1` through nginx. |
| `...-worker` Deployment | `node dist/worker.js`, no Service. Exec liveness probe: `/tmp/worker-alive` (touched every 10 s while Redis is reachable) must be younger than 60 s. `terminationGracePeriodSeconds: 30`. |
| `...-migrate-<hash>` Job | Only with `migrate.enabled=true`. `npx prisma migrate deploy` from `pme-backend-migrate`. **Not a Helm hook.** |
| `...-config` ConfigMap | Non-secret env only (`envFrom` in api and worker). |

Pods run as uid/gid/fsGroup 10001, read-only root filesystem, `/tmp` is an emptyDir.

**Env wiring.** Credentials are never in the ConfigMap or in values. They are
`secretKeyRef` entries in the container `env`, and the URLs are built with
Kubernetes dependent-variable expansion, so the `secretKeyRef` entries must (and
do) come first:

```yaml
- {name: DB_USERNAME, valueFrom: {secretKeyRef: ...ddc-pme-mysql/username}}
- {name: DB_PASSWORD, valueFrom: {secretKeyRef: ...ddc-pme-mysql/password}}
- {name: DATABASE_URL, value: "mysql://$(DB_USERNAME):$(DB_PASSWORD)@<host>:<port>/<db>?<params>"}
- {name: REDIS_PASSWORD, valueFrom: {secretKeyRef: ...ddc-pme-redis/password}}
- {name: REDIS_URL, value: "redis://:$(REDIS_PASSWORD)@<release>-ddc-pme-redis:6379"}
```

S3 keys come from `ddc-pme-minio`. `KEYCLOAK_CLIENT_SECRET` is injected only when
`keycloak.publicClient=false`, and **nothing reads it** - the API has no such
setting, and the SPA's PKCE flow has no confidential-client mode - which is why
the render refuses that value (see *Single sign-on*). `JWT_ACCESS_SECRET`,
`DEV_AUTH_PASSWORD` and `ALLOW_PASSWORD_GRANT=true` are never set, and the render
**fails** if `extraEnv` tries to.

- **TLS to MySQL.** Percona has `require_secure_transport=ON`, so `mysql.params`
  defaults to `sslmode=REQUIRED&sslaccept=accept_invalid_certs`. That encrypts
  but does **not verify** the server certificate (prototype only). With a
  CA-signed certificate use `sslmode=REQUIRED`.
- **The DB password must be URL-safe** (letters, digits, `- _ . ~`): it is
  spliced into the URL unescaped.
- `CORS_ALLOWED_ORIGINS` defaults to `https://<ingress.host>` (override with
  `app.corsAllowedOrigins`). `TRUST_PROXY` defaults to `3`; set `app.trustProxy` to
  the real number of proxy hops (too low: all users share one rate-limit bucket;
  too high: spoofable client IPs).
- `S3_PUBLIC_ENDPOINT` is set only when `minio.publicEndpoint` is set.
- Extra env: `app.extraEnv` (api + worker), `api.extraEnv`, `worker.extraEnv`,
  `migrate.extraEnv`; any Kubernetes env entry, including `secretKeyRef`.

**Running the migration** (deliberate, after a backup of the shared database):

```sh
helm upgrade ddc-pme charts/ddc-pme -n ddc-pme --reuse-values --set migrate.enabled=true
kubectl -n ddc-pme logs job/$(kubectl -n ddc-pme get job -l app.kubernetes.io/component=migrate -o name | cut -d/ -f2)
helm upgrade ddc-pme charts/ddc-pme -n ddc-pme --reuse-values --set migrate.enabled=false   # removes the Job
```

The Job name carries a hash of image + command, so a new tag creates a new Job
(a Job's pod template is immutable).

## Single sign-on (`keycloak.*`)

The SPA runs authorization code + PKCE **directly against Keycloak**; the API
never talks to Keycloak's token endpoint. The chart therefore has to keep three
things agreeing: this release's values, the client registration in
digin-automation (`ansible/applications/keycloak/vars/realm-client.yml`), and the
`iss`/`aud` Keycloak actually puts in tokens. A mismatch in the first two is
silent at install time and fatal at login time, so the values below are rendered
into **two** places from one source.

| Value | API env (ConfigMap) | SPA `window.__PME_CONFIG__` | Notes |
|---|---|---|---|
| `keycloak.baseUrl` + `keycloak.realm` | `OIDC_ISSUER` (helper `ddc-pme.oidcIssuer` = `<baseUrl>/realms/<realm>`) | `issuer` | One helper feeds both, so they cannot drift. |
| derived from `OIDC_ISSUER` | `OIDC_JWKS_URI` (`<issuer>/protocol/openid-connect/certs`) | — | Presence of this selects RS256; absent, the API falls back to HS256. |
| `keycloak.audience` | `OIDC_AUDIENCE` | — | Must equal what the client's audience mapper injects (`pme-api`). |
| `keycloak.managerClaim` | `OIDC_MANAGER_CLAIM` | — | Claim holding the manager flag. |
| `keycloak.clientId` | — | `clientId` | Public PKCE client; no secret. |
| `keycloak.redirectUri` | — | `redirectUri` | Must also be registered on the Keycloak client. |
| `keycloak.postLogoutRedirectUri` | — | `postLogoutRedirectUri` | ditto. |
| `keycloak.oidcOnly` | — | `oidcOnly` | `true` hides the email/password form (see below). |

The login page's password form is a **dead end in every environment**: the API
answers the password grant with 410 `PASSWORD_GRANT_DISABLED`, and this chart
refuses to inject `ALLOW_PASSWORD_GRANT`. Set `keycloak.oidcOnly=true` so the
form is not offered at all.

`keycloak.publicClient=false` is not supported. `backendSecretEnv` would inject
`KEYCLOAK_CLIENT_SECRET`, but no code reads it and a browser SPA cannot hold a
secret, so the render fails instead of shipping a mode that cannot work.

Identity mapping (`sub` -> `users.external_identity_id`, else a one-time link by
**verified** email) is implemented in `identity-resolver.service.ts`. Which
claims a token carries, and whether Keycloak marks a federated email verified, is
governed by the client and IdP configuration in **digin-automation**, not by this
chart. If sign-in succeeds but the API answers 404 `USER_NOT_FOUND`, read the
API's `TokenVerifier` / `IdentityResolver` warn lines - they now name which
condition failed - and fix the claim in digin-automation.

### Render-time guards

`ddc-pme.validate` fails the render, rather than shipping a release whose sign-in
is broken, when:

- `keycloak.baseUrl` is not `https`, or its host looks like a placeholder
  (`.invalid`, `example`) - the default that shipped for a while pointed at
  `keycloak.example.invalid`, so any install that omitted a profile produced an
  `OIDC_ISSUER` no token could ever match.
- `keycloak.audience` is empty - the API only checks `aud` when configured, so an
  empty value silently accepts tokens minted for **any** client in the realm.
- `keycloak.publicClient` is `false`.
- `keycloak.redirectUri` / `postLogoutRedirectUri` are not on this release's own
  host (`ingress.host`) - Keycloak would refuse the login with
  `redirect_uri_mismatch`. Compared by **host** only, because the external
  scheme is decided by pfSense/Traefik, not by this chart.

## Validation

At install/upgrade the chart uses `lookup` to check every required Secret and
key and fails with, e.g.:

```
ddc-pme: required Secret 'ddc-pme-redis' (key 'password') not found in namespace ddc-pme. Create it in Rancher first.
```

`lookup` returns nothing without an API server, so offline commands cannot see
Secrets. For **CI render checks only**:

```sh
helm lint charts/ddc-pme --set secretValidation.allowOffline=true --set global.imageTag=test
helm template r charts/ddc-pme --set secretValidation.allowOffline=true --set global.imageTag=test
```

Against a cluster, `helm upgrade --install ... --dry-run=server` runs the real
check. Never set `allowOffline=true` on a real release.

### Verification scripts (`ci/`)

```sh
# Offline (CI, no cluster): lint --strict + helm template | kubeconform -strict
# (Kubernetes 1.34 + Traefik CRD schemas) + policy assertions A1-A10, for
# dev and prod, each in a base and a "full" variant (IngressRoute, migrate Job,
# NetworkPolicy, HPA).
bash charts/ddc-pme/ci/check.sh
bash charts/ddc-pme/ci/check.sh --self-test      # every assertion can fail

# Live, scoped deployer kubeconfig ONLY (refuses any other identity). Only
# `--dry-run=server`; nothing is installed.
bash charts/ddc-pme/ci/live-verify.sh specs/.kube/ddc-pme-deployer.yaml
```

kubeconform: put it on PATH, set `KUBECONFORM`, or drop the binary in
`charts/.tools/` (git-ignored). `live-verify.sh` shows the missing-Secret
failure while the Secrets are absent; for the "present" step it uses the real
Secrets if they exist, otherwise creates fake ones labelled
`ddc-pme.verify/temporary=true` and deletes only those on exit. The real
install is done by the Ansible run after db-bootstrap and migrate.
`values.schema.json` type-checks the common settings at lint/template/install time.
`.github/workflows/helm-ci.yml` runs the offline part on every chart change.


## Redis and MinIO (in-chart, internal only)

Both run as single-replica StatefulSets behind a **ClusterIP** Service and are
never referenced by the Ingress.

| | Redis | MinIO |
|---|---|---|
| Image | `redis:7-alpine` | `rstevens198238/pme-minio:<MinIO release>` |
| Port | 6379 | 9000 (console 9001 only if `minio.consoleEnabled`) |
| Credentials | `ddc-pme-redis` > `--requirepass`; probes use `REDISCLI_AUTH` | `ddc-pme-minio` > `MINIO_ROOT_USER/PASSWORD` |
| Volume | `redis.persistence.enabled` (default off, emptyDir) | `minio.persistence.enabled` (default on) |
| Probes | `redis-cli ping \| grep PONG` | `/minio/health/live`, `/minio/health/ready` |

* **Longhorn is set explicitly.** `*.persistence.storageClass` defaults to
  `longhorn` and is written onto the `volumeClaimTemplate`; an empty value is
  rejected at render time so the cluster default can never be used by accident.
  Size and access mode: `*.persistence.size` / `*.persistence.accessMode`.
  A StatefulSet cannot change `volumeClaimTemplates` in place: to resize,
  expand the PVC (Longhorn supports it) and keep the values in step.
* **Redis rejects unauthenticated commands** (`NOAUTH Authentication required`).
* **Bucket init** (`minio.bucketInit`): a `post-install,post-upgrade` hook Job
  running `mc mb --ignore-existing` for `app.s3Bucket`; safe to re-run.
* **No Ingress for MinIO.** `minio.ingress.enabled` is a placeholder; `true` fails
  the render. For the console: `minio.consoleEnabled=true` and
  `kubectl -n ddc-pme port-forward svc/<release>-ddc-pme-minio 9001`.
* **NetworkPolicies** (`networkPolicy.enabled=true`, default off): Redis accepts
  only api/worker Pods of the same release; MinIO accepts only api/worker/
  bucket-init Pods, on 9000 only.
* Both images run as non-root (redis 999:1000, minio 100:101) with a read-only
  root filesystem and all capabilities dropped. Re-check the ids on an image change.
* The community MinIO build is source-only and unmaintained (see
  `backend/minio/README.md`); treat it as demo-grade.

Other render-time guards: any Service type other than `ClusterIP`
(NodePort/LoadBalancer), `ingress.kind` not `Ingress|IngressRoute`, and
production settings the API refuses to boot with (wildcard CORS, non-s3 storage).

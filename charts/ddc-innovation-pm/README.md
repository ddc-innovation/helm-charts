# ddc-innovation-pm - Helm chart

DDC Innovation Portfolio Management (DCIPM): a .NET 9 API and a React/nginx web
tier on RKE2 v1.36.4 behind Traefik, with TLS terminated at pfSense HAProxy.

For a self-contained Rancher deployment context that can be embedded in a
larger AI request, see [README-AI-PROMPT.md](README-AI-PROMPT.md).

The chart lives **in this application repository**, not in `digin-automation`, so a
chart change and the code it deploys land in one commit and one review (plan
decision D7). The Ansible playbooks that prepare the cluster and database stay in
`digin-automation/ansible/applications/dcipm/`.

---

## Quick start

```bash
# Render only (no cluster needed). image.tag is REQUIRED - see below. Pass the tag you
# built with scripts/deploy/build-push-images.ps1 -Tag; do not substitute git here.
helm template ddc-innovation-pm . -n ddc-pm \
  -f values.yaml -f values-prod.yaml --set image.tag=949f353

# Deploy (this is what scripts/deploy/helm-deploy.ps1 assembles for you)
helm upgrade --install ddc-innovation-pm . -n ddc-pm \
  -f values.yaml -f values-prod.yaml \
  --set image.tag=949f353 \
  --wait --timeout 10m
```

Three layers of values, always `values.yaml` first (plan section 5):

| File | Role | Must never contain |
| --- | --- | --- |
| `values.yaml` | Generic shape, shared by every environment | an image tag, a public host, a credential |
| `values-prod.yaml` | The **only** file naming a public DNS host | an image tag, a credential |
| `values-dev.yaml` | No ingress at all, `Development`/`Development` | an ingress host, a credential |

There is deliberately no fourth, environment-named values file.

```bash
# Development: no Ingress is rendered, so reach it by port-forward.
# The tag must still look like a commit sha - `dev`/`latest`/branch names are refused.
helm template ddc-innovation-pm . -n ddc-pm-dev \
  -f values.yaml -f values-dev.yaml --set image.tag=949f353
kubectl -n ddc-pm-dev port-forward svc/web 8080:80    # then browse :8080
```

### Validate before you push

```bash
pwsh scripts/deploy/check-chart-guards.ps1   # 103 checks, non-zero exit on failure
helm lint . --strict -f values.yaml -f values-prod.yaml --set image.tag=x -n ddc-pm
```

The guard script renders the chart many times and asserts both that correct inputs
produce the right manifests **and that each known-bad input fails with its own named
message**. If you change a guard, you must be able to show the case it fires on -
several guards here were dead code at first write and looked entirely correct.

---

## Prerequisites (the chart does not create these)

| Object | Namespace | Why |
| --- | --- | --- |
| Namespace `ddc-pm` | cluster | Created by `bootstrap-namespace.yml` |
| Role + RoleBinding `dcipm-deployer` | `ddc-pm` | The deployer SA is bound inside `ddc-pme` only, so it has **no rights at all** in `ddc-pm` - not even `get namespace`. Run the bootstrap playbook with a cluster-admin kubeconfig; the deployer cannot grant this to itself. |
| Secret `dcipm-db` | `ddc-pm` | `ConnectionStrings__Dcipm`, `ConnectionStrings__DatabaseExplorerReadOnly` |
| Secret `dcipm-auth` *(optional)* | `ddc-pm` | `Auth__BootstrapAdmins__0` - the first application admin |
| Secret `dcipm-registry-pull` | `ddc-pm` | Pull-only robot credential for the private `ddc-innovation-pm` Harbor project |

Both image repositories are private in InnoNet Harbor. The generic and production
values therefore reference `dcipm-registry-pull` on the API Deployment, web
Deployment, and migration Job. The credential is created by digin-automation's
`ansible/applications/dcipm/playbooks/configure-registry.yml`; it is never rendered
by this chart or stored in Git.

```bash
# Production overlay: must print 3 (api, web, and migration Job).
helm template ddc-innovation-pm . -f values.yaml -f values-prod.yaml \
  --set image.tag=GIT_SHA -n ddc-pm | grep -c 'dcipm-registry-pull'
```

Secrets are **namespace-scoped**: a new namespace needs its own copies. If
`dcipm-db` is missing, the API exits at startup naming the missing key; that message
is the guard working, not a bug.

### `manifest unknown` is a missing PUSH, never a credential problem

The Harbor repositories are created by their first push. A tag exists only because
`scripts/deploy/build-push-images.ps1` put it there, so any other tag a render names
- a typo, a tag built locally but never pushed, or one image built without the other
- is absent from the registry.

That failure class is **indistinguishable at a glance from a missing pull
credential**. Separate the two by the message rather than by guesswork:

| Message | Meaning | Action |
| --- | --- | --- |
| `manifest unknown`, `not found`, `Name unknown` | **the tag or digest is not in the repository** - a missing or failed PUSH | Re-run `build-push-images.ps1`, then re-check the tags endpoint. **Never** a credential problem. |
| `unauthorized`, `denied` | auth or account state | Verify the Harbor robot and the `dcipm-registry-pull` Secret. |

Confirm before acting; do not infer the cause from the pod's `ImagePullBackOff` alone:

```bash
docker manifest inspect harbor.dinecodetalkers.com/ddc-innovation-pm/dcipm-api:GIT_SHA
docker manifest inspect harbor.dinecodetalkers.com/ddc-innovation-pm/dcipm-web:GIT_SHA
```

**Expected:** an OCI/Docker manifest for both references. The same tag is used for
both workloads and the migration Job because `web.image.tag` reuses `image.tag`.

`helm-deploy.ps1` enforces the same split before it mutates anything: step **4b**
queries each rendered registry reference and fails the run on HTTP 404. HTTP 401 is
reported as *unverified* rather than missing because private Harbor repositories
require authentication. Step **4c** then fails if the render references a pull Secret that
is absent from the target namespace. The verdict is deliberately held until both have
run, so a missing image cannot hide a missing Secret and vice versa.

---

## Secrets: names, not values

By default **no `Secret` object is rendered**. The chart references pre-created
Secrets by name (`api.dbSecretName`, `api.authSecretName`) via `envFrom`, so no
credential value appears in a values file, in a rendered manifest, or in
`helm get values` (plan decision D10).

`api.dbSecretName` / `api.authSecretName` **are** the `existingSecret` escape hatch:
point either at any pre-existing Secret and the workloads mount it unchanged. The
name is all this chart ever needs.

Order inside `envFrom` is load-bearing: ConfigMap first, then Secrets, so a Secret
key always wins over a ConfigMap key of the same name.

### The opt-in Secret

```bash
helm upgrade --install ... --set secrets.create=true \
  --set secrets.takeOwnership=true \
  --set secrets.api.ConnectionStrings__Dcipm='Server=...;Database=...'
```

`secrets.create` renders a real `Secret` from values (keys are configuration keys
verbatim, via `stringData`). It is off by default for a reason worth understanding
before you flip it: **Helm persists every value of every revision** in the release
Secret, so `helm get values --revision 3` reproduces a connection string from three
upgrades ago - after `uninstall`, and after `rollback`. That is risk R13, and it is
why the deploy automation passes only `image.tag` on `--set`.

The automation that upholds that rule is `scripts/deploy/helm-deploy.ps1` (the
GitHub Actions workflow that used to enforce it was retired on 2026-10-16); the full
mapping from script input to cluster object (and why `MYSQL_SERVER` is **not** a
chart value) is documented in
[`docs/development/ci-secrets.md`](../../../docs/development/ci-secrets.md).

Creating a Secret named `dcipm-db` also hands **ownership** of that object to Helm:
`helm uninstall` then deletes it, and the Ansible playbook that used to write it
contends with Helm over the same resource. Neither consequence appears on the day you
enable it; both appear during an incident as "the connection string vanished". So
`secrets.takeOwnership: true` is **required** alongside `create` - a second flag is
the cheapest way to make the handover deliberate.

Never put a connection string in `api.extraEnv`: it renders into the ConfigMap, which
is unencrypted and shown in Rancher's UI. `_helpers.tpl` fails the render on that and
on any key shaped like a credential.


---

## Keycloak: the issuer/metadata split, and which source actually wins

The API validates the `iss` claim against one address while **downloading signing
keys from another** (`Security/AuthOptions.cs:101-105`; applied at
`AuthenticationSetup.cs:154-157`). They legitimately differ when discovery is reached
in-cluster while tokens carry the public issuer:

```yaml
keycloak:
  baseUrl: https://sso.dinecodetalkers.com   # -> Keycloak__Issuer, the iss claim
  realm: innonet
  audience: dcipm-api
  metadataAddress: http://keycloak.ns.svc:8080/realms/innonet/.well-known/openid-configuration
```

**The in-cluster `http://` address only works with a non-Production environment.**
`requireHttpsMetadata: true` is mandatory in Production (`AuthOptions.cs:165-166`) and
JwtBearer then rejects a non-HTTPS discovery address outright - "The MetadataAddress
or Authority must use HTTPS unless disabled for development" - and the process exits.
Hence `values-prod.yaml` sets `metadataAddress: ""` (falling back to the public https
URL) while `values-dev.yaml` uses the in-cluster address. A guard in `_helpers.tpl`
fails the render rather than letting you discover this as a CrashLoop.

The task brief asked for exactly this split; it is implemented, and the constraint
above is the part that is easy to get wrong.

### Sign-in settings do not necessarily come from the chart

Read this before debugging an auth change. The `keycloak:` values above become the
**environment**; they are not necessarily the **effective** settings. `values.yaml:412-415`
states the same rule from the other end of the same file - here it is in full, with the line
references.

With `Auth__Mode=Keycloak` and the default `Auth__IdentitySettingsSource` (`Database`), the
**effective** issuer, client id, audience, scopes, claim names, discovery/JWKS addresses,
auto-provisioning, default roles and e-mail-domain rule come from the **Active**
`identity_provider_connections` row whenever one exists: that row replaces the entire
`Keycloak:` block and the sign-in part of `Auth:` (`IdentitySettingsProvider.cs:56,61-72,92-98`),
and `JwtBearer` is configured from that snapshot rather than from the environment
(`AuthenticationSetup.cs:149-162`, via `provider.Current`). **Only the row's absence leaves
the environment variables in force.** So editing `Keycloak__BaseUrl`, rolling the pod, and
observing **no behaviour change is EXPECTED** - not a broken ConfigMap. The ConfigMap is
correct; it is simply not the authority.

`Auth__IdentitySettingsSource=Configuration` is the **break-glass** that ignores the row and
makes the environment win (`AuthOptions.cs:57-64`, `UsesConfigurationOnly`); in the chart that
is the value `identitySettingsSource: Configuration`, which renders exactly that variable. It
changes the source only - it neither edits nor retires the row - and it takes effect on the
next render and pod start, because the variable is read at startup. Use it when a bad row has
locked everyone out and the API cannot validate any token.

Two settings are always environment-only, whichever source is in force:
**`Keycloak:RequireHttpsMetadata`** and **`Auth:BootstrapAdmins`**. They are floors taken from
the environment even when a row is applied (`IdentitySettingsProvider.cs:13-14`, applied at
`:185` and `:197`) - the same two that the chart's `values.yaml:417-418` names. Everything
else about sign-in follows the row, and the e-mail-domain rule in particular is **not** an
exception: with an Active row, `Auth__AllowedEmailDomains__*` is ignored rather than merged
(`IdentitySettingsProvider.cs:194`), so a Secret that sets it can be perfectly correct and
still have no effect. Separately, `Auth:Mode` is not part of the row at all: the row is not
consulted when `Auth__Mode=Development` (`IdentitySettingsProvider.cs:56`).

Which row is in force, in one line. At most one Active row can exist, and the `ORDER BY` /
`LIMIT` mirror the provider's own selection (`IdentitySettingsProvider.cs:92-95`), so this
answers "what is in force" rather than "what is in the table":

```sql
SELECT Id, DisplayName, Issuer, ClientId, Audience, Scopes, AllowedEmailDomainsJson FROM identity_provider_connections WHERE Status = 'Active' ORDER BY ActivatedAtUtc DESC LIMIT 1;
```

**No rows: the environment is the authority.** One row: every `Keycloak:` change made since it
was activated has been inert. Settings refresh on activation and by a 60 s poll
(`IdentitySettingsProvider.cs:19,51`), so activating, retiring, or correcting a row needs no
pod restart - a running API converges by itself.

The domain-rule consequence of this precedence, including how it interacts with
`email_verified`, is worked through in
[`docs/development/ci-secrets.md`](../../../docs/development/ci-secrets.md) section 3.2. The
precedence paragraph and the query above are repeated verbatim in
`digin-automation/ansible/applications/dcipm/README.md`, so that an operator meets them from
either side; the two copies are kept identical.

---

## TLS, scheme, and why the redirect is off

TLS terminates at **pfSense HAProxy**, then Traefik, then nginx - the container
listens on plain HTTP (plan D2/D3). Three settings depend on this and must be changed
together:

1. **`features.useHttpsRedirection: false`.** Config key `Features:UseHttpsRedirection`
   (the constants in `Program.DeploymentSwitches.cs` are the only authority). With the
   redirect on, the kubelet's plain-HTTP `GET /health/ready` - arriving from the
   *node*, outside `api.trustedProxyCidrs`, so with no rewritten scheme - gets a 307
   that kubelet does not follow. The pod never becomes Ready and the rollout
   CrashLoops (risk R5).
2. **`api.trustedProxyCidrs: [10.42.0.0/16]`** - the RKE2 **pod** CIDR
   (`digin-automation/ansible/applications/rancher/vars/rke2.yml` ->
   `rke2_cluster_cidr`). The web pod reaches Kestrel pod-to-pod and that traffic is
   not SNAT'd. It is deliberately **not** the node network: egress to MySQL *is*
   SNAT'd, so making the two equal breaks one of them (risk R10). With no CIDR listed,
   ASP.NET Core 8+ ignores the header entirely, which looks exactly like "the proxies
   are not forwarding" (risk R15).
3. **No `tls:` block by default** (`ingress.tls.enabled: false`). Traefik redirects at
   its own web entrypoint; a second redirect from the Ingress is the same failure
   class as risk R12. The task brief asked for ingress TLS - it is implemented and
   available (`ingress.tls.enabled`, `.secretName`, `.hosts`), just off, because plan
   D2 settles this deployment as no cluster-side TLS. Enable it only if you also stop
   Traefik redirecting.

`ASPNETCORE_HTTPS_PORT` is never emitted, on purpose: with no HTTPS endpoint in the
pod, Kestrel redirects every request to a port nothing listens on - with HAProxy in
front, an infinite loop. A guard refuses it in `api.extraEnv`.

The browser-facing scheme must survive all four hops, so nginx **passes through**
`X-Forwarded-Proto` rather than overwriting it with the local `$scheme` (handled in
`nginx/nginx.conf`).

---

## Migration hook Job (`MIGRATE_ONLY`)

`migration.enabled: true` renders a **`pre-install,pre-upgrade` hook Job** on the
**same image as the API** (D15) with `MIGRATE_ONLY=true`. The app applies pending EF
Core migrations and exits before the web host starts serving (`Program.cs:139-148`).

This is **not** the `APPLY_MIGRATIONS` path, which is Development-only and therefore
never runs in Production - the reason a Production schema went unapplied.

* Runs before the workloads are applied, so new code never meets an old schema.
* `migration.hookDeletePolicy` controls cleanup of completed/failed Jobs.
* Shares the API's ConfigMap and `dcipm-db` Secret, so migrations run against
  literally the connection string the application will use.
* **Rollback caveat:** `helm rollback` does **not** re-run migrations, and migrations
  must be **additive only**. Rolling the release back returns new code to an
  already-migrated database; a destructive migration cannot be undone this way.
* Set `migration.enabled: false` to hand migrations to an out-of-band process. The
  release notes then print a warning, because a drifted database fails at query time
  hours later and looks like an application defect.

---

## Images and the required tag

```bash
--set image.tag=GIT_SHA          # required, no default
--set web.image.tag=GIT_SHA      # optional; empty reuses image.tag
```

`image.tag` has no default, so the render **fails until you supply it**. A floating or
`:latest` tag makes `helm rollback` meaningless and makes it impossible to tell which
build is live; tags matching `latest` or a branch name are refused as well. The tag is
whatever you passed to `scripts/deploy/build-push-images.ps1 -Tag` - it is **not**
derived from git, because in this workspace copy git cannot be trusted to describe the
tree (`scripts/deploy/README.md`, "Where the image tag comes from").

`web.image.tag` defaults to empty, which reuses `image.tag` so one `--set` moves both
workloads together. Set it only when the two images genuinely come from different
builds; the same tag guard applies to it, because a render that pins only one of the
two images is precisely the half-upgrade the guard exists to prevent.

`web.env.VITE_API_BASE_URL=/api/v1` is a **mirror** of the web image's build arg. It
is baked into the JS bundle at image-build time, so changing it here changes nothing
at runtime - it exists so `kubectl describe pod` shows the contract the image was
supposed to be built with, turning "the UI calls the wrong endpoint" into an evidence
question. nginx routes `/api/` to the `api` Service (and in Kubernetes the Ingress
routes `/api` there directly), which is why the value is a same-origin relative path.

`web.env.VITE_DATA_SOURCE=api` is the mirror of the *other* build arg, and it is the
one that was missing until October 2026. It compiles into
`isApiDataSource` (`apps/web/src/api/client.ts`), which gates roughly twenty
live-data screens **and the account menu's `GET /api/v1/me`**. With the variable
undefined the comparison folds to `false` at build time and esbuild removes the gated
branches from the bundle altogether, so the deployed SPA does not merely behave
differently - it physically lacks the code, and no chart value, `kubectl set env`, or
restart can put it back.

The symptom is worth knowing because it does not look like a build problem. Sign-in
still works, because the auth mode is read from the API's `client-config` at
**runtime**, so the SPA really does perform the OIDC exchange and really does hold a
valid token. But the account menu takes the display name and e-mail from that token as
a fallback while it takes **roles only from `GET /me`** - so a PortfolioAdmin is shown
"No DCIPM roles assigned" on a perfectly good session, and every live-data screen
shows its static placeholder instead of data. Both the name/e-mail fallback and the
empty role list come from the same `me === null`, which is why the UI could not tell
"the request failed" from "this user has no roles"; the menu now reports those
separately. Rebuild with `VITE_DATA_SOURCE=api` (`build-push-images.ps1` passes it by
default and asserts the built bundle contains the `/me` request path).

---

## Service names are pinned, not derived

The API Service is literally **`api`**, not `<release>-api`: nginx inside the web
image proxies to `http://api:8080` by that name (`nginx/nginx.conf`,
`location /api/`). Renaming it turns the UI into 502s (risk R6). `_helpers.tpl`
refuses any `fullnameOverride` that would rename the `api`/`web` Services, and
`service.port`/`targetPort` must stay 8080.

Names are pinned rather than release-derived for the same reason: a second release in
the same namespace would either fail with `services "api" already exists` or take
ownership of it and silently retarget production `/api/` traffic at the dev API.

---

## Namespace: `ddc-pm`, never `ddc-pme` (risk R11)

`ddc-pme` - one character away - hosts the **live DDC-PME application**. This chart
guards against hitting it in two ways:

* `forbiddenNamespaces: [ddc-pme, kube-system]` is checked against **both**
  `--namespace` and `.Values.namespace`, so the render fails either way.
* When `.Values.namespace` is set, it must **equal** the `--namespace` flag. A
  mismatched pair would put objects in one namespace while an operator believes they
  are in another.

`.Values.namespace` is empty in `values.yaml` (take it from `--namespace`) and pinned
to `ddc-pm` in `values-prod.yaml`. `values-dev.yaml` uses `ddc-pm-dev`: because the
API Service name is pinned, a dev release inside `ddc-pm` cannot exist at all.

Verify on any render:

```bash
helm template ddc-innovation-pm . -f values.yaml -f values-prod.yaml \
  --set image.tag=GIT_SHA -n ddc-pm | grep -c 'ddc-pme'      # must be 0

helm template ddc-innovation-pm . -f values.yaml -f values-prod.yaml \
  --set image.tag=GIT_SHA -n ddc-pm | grep 'namespace:' | sort -u
```

---

## Values reference

Only the keys an operator is expected to touch. Every key is documented inline in
`values.yaml`, which stays the authoritative copy.

| Key | Default | Notes |
| --- | --- | --- |
| `image.repository` | `harbor.dinecodetalkers.com/ddc-innovation-pm/dcipm-api` | Private Harbor repository |
| `image.tag` | **required** | Render fails when empty |
| `web.image.repository` | `harbor.dinecodetalkers.com/ddc-innovation-pm/dcipm-web` | Private Harbor repository |
| `web.image.tag` | `""` | Empty = reuse `image.tag`, so one `--set` moves both |
| `namespace` | `""` | Empty = take `--namespace`; when set, must match it |
| `aspnetcoreEnvironment` | `Production` | |
| `authMode` | `Keycloak` | Production refuses anything else (risk R9) |
| `identitySettingsSource` | `""` | `Configuration` = break-glass; see Keycloak above |
| `api.replicaCount` | `1` | **Correctness constraint**, see below |
| `api.containerPort` | `8080` | Probe port, Service targetPort and `ASPNETCORE_URLS` all follow it |
| `api.trustedProxyCidrs` | `[10.42.0.0/16]` | RKE2 pod CIDR (risk R10/R15) |
| `api.dbSecretName` / `api.authSecretName` | `dcipm-db` / `dcipm-auth` | The `existingSecret` escape hatch |
| `api.probes.*` | `/health/live`, `/health/ready` | Guarded; see Health below |
| `web.replicaCount` | `2` | Static files, stateless |
| `web.image.buildArgs.VITE_API_BASE_URL` | `/api/v1` | Baked at image build, not at runtime |
| `web.image.buildArgs.VITE_DATA_SOURCE` | `api` | Baked at image build. Unset ⇒ live data *and* `GET /me` are compiled out; see above |
| `features.useHttpsRedirection` | `false` | `true` requires the whole TLS story to change |
| `ingress.enabled` / `ingress.host` | `false` / `""` | Host set only in `values-prod.yaml` |
| `ingress.tls.enabled` | `false` | See TLS above |
| `cors.allowedOrigins` | `[]` | Derived from `ingress.host` when empty; must be bare origins |
| `migration.enabled` | `true` | pre-install/pre-upgrade hook Job |
| `secrets.create` / `secrets.takeOwnership` | `false` / `false` | See Secrets above |
| `commonLabels` / `commonAnnotations` | `{}` | Applied to every object; **never** to a selector |

### `api.replicaCount: 1` is not a sizing decision

The API's hosted background workers have **no leader election**. A second replica
duplicates every scheduled job - duplicate exports, duplicate notifications. Raise it
only once that is fixed. The release notes warn when it exceeds 1.

`commonLabels` reaches `metadata.labels` and the pod template but deliberately **not**
`spec.selector.matchLabels`: selectors are immutable, so a label added after the first
install would make every later `helm upgrade` fail with "field is immutable".

---

## Security context

The API pod runs as **UID/GID 1654**, matching `USER $APP_UID` in the `Dockerfile`,
with `runAsNonRoot: true`, `allowPrivilegeEscalation: false`,
`capabilities.drop: [ALL]` and `seccompProfile: RuntimeDefault`. Guards refuse
`runAsUser: 0` and `runAsNonRoot: false` at render time.

`automountServiceAccountToken: false` - neither workload calls the Kubernetes API, so
the token is not mounted at all: least privilege by absence.

Generated documents go to an `emptyDir` at `/app/data`. Every artefact is
re-renderable from its manual, so there is **no PVC in this chart** and nothing to
back up.

---

## Health endpoints

There is **no `/healthz` route**. The only health endpoints are `/health/live` and
`/health/ready` (`Program.cs:108-109`). A guard fails the render if a probe is pointed
anywhere else, because a wrong path makes every pod CrashLoop and the failure looks
like an application defect.

* `startupProbe` -> `/health/live`, 3 s x 60 (~3 min) to absorb the cold start (JIT,
  EF model build, OIDC metadata fetch, first `SELECT 1`).
* `livenessProbe` -> `/health/live`. Not `/health/ready`: readiness includes the MySQL
  check, so a DB blip during cold start would restart a healthy API forever.
* `readinessProbe` -> `/health/ready`. Includes the `DbContext` check, so a database
  outage pulls the pod out of the Service endpoints - a clean 503 from the proxy
  rather than a misleading 500. Raise `failureThreshold` if the database fails over
  often and you would rather ride it out.

Probes are plain HTTP. If the app ever starts redirecting, the probe gets a 307, the
pod is killed, and the CrashLoop names the real cause (risk R5).

---

## Verify after deploying

```bash
kubectl -n ddc-pm rollout status deploy/ddc-innovation-pm-api
kubectl -n ddc-pm rollout status deploy/ddc-innovation-pm-web

# Health must be a plain 200, never a 307 (D8/R12)
kubectl -n ddc-pm exec deploy/ddc-innovation-pm-api -- \
  wget -qO- http://localhost:8080/health/ready

# Confirm the redirect gate actually reached the pod
kubectl -n ddc-pm get configmap ddc-innovation-pm-api-env -o yaml | findstr HttpsRedirection

# No credential leaked into the release values
helm get values ddc-innovation-pm -n ddc-pm --all
```

A 307 means `Features__UseHttpsRedirection` came out `true`. The in-cluster probe
(inside the web pod, against `http://api:8080/health/ready`) is the authoritative one:
the public URL can 307 at Traefik for reasons that have nothing to do with this chart.

---

## Rollback

```bash
helm rollback ddc-innovation-pm -n ddc-pm
```

Two caveats, both because the schema is ahead of the code:

1. `helm rollback` does **not** re-run migrations, and the hook Job is not re-applied
   in reverse. Migrations must be additive-only.
2. If `secrets.create=true` was ever used, `helm uninstall` deletes those Secrets and a
   reinstall comes back with no connection string. See Secrets above.

---

## Rendered objects

Production renders 8 objects; development is the same minus the `Ingress`.

`ServiceAccount`, `ConfigMap` (`ddc-innovation-pm-api-env`), `Service/api`,
`Service/web`, `Deployment/api`, `Deployment/web`, `Ingress` (prod only),
`Job/migrate` (hook). A `Secret` is rendered only when `secrets.create=true`.

---

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| CrashLoopBackOff, readiness gets a 307 | `Features__UseHttpsRedirection` true while TLS ends upstream |
| `ImagePullBackOff` + `manifest unknown` / `not found` | The tag was **never pushed** - a missing build/push, never a credential problem (see Prerequisites) |
| `ImagePullBackOff` + `unauthorized` / `denied` | Harbor pull robot or `dcipm-registry-pull` is missing, expired, or stale |
| Sign-in config change has no effect | An active `identity_provider_connections` row wins - see Keycloak above |
| API exits at startup naming a key | `dcipm-db` missing, or missing that key, in this namespace |
| UI is 502 | Service `api` renamed, or `service.port` changed away from 8080 |
| Public URL serves the wrong build | Two releases claim `ddc-innovation-pm.dinecodetalkers.com` |
| `X-Forwarded-For` shows the pod IP | `api.trustedProxyCidrs` empty or wrong CIDR (risk R15) |
| A CORS key named `...__Error` | `fromYaml`/`fromJson` on a sequence - use `fromJsonArray` |
| `YAML parse error ... could not find expected ':'` | A flow value on the same line as its key at the key's own indent - use `toJson`, not `toYaml \| nindent` |
| `can't evaluate field Release in type interface {}` | `range` rebound `.`; capture `.Release` before the loop |

The last three were real bugs in this chart, each invisible while writing it. The
guard script now fails on all of them.

---

## Editing this chart

* Long rationale goes in Helm template comments (`{{- /* ... */}}`), **not** YAML `#`.
  Helm emits `#` verbatim into rendered manifests, so prose that *names* a forbidden key
  (`tls:`, `ConnectionStrings__`) breaks the `grep -v` assertions this repo depends on -
  it did, in first draft.
* Keep `NOTES.txt` and `_helpers.tpl` **ASCII**: both print to terminals, and a
  non-ASCII dash renders as mojibake on a cp437 console - unreadable in exactly the
  moment it matters.
* Config keys are a **cross-repo contract**. The authoritative names are the constants
  in `apps/api/Dcipm.Api/Program.DeploymentSwitches.cs`. An invented key compiles,
  passes every unit test, and is silently ignored at runtime.
* A guard must not be stricter than the code it guards. The app compares
  `IdentitySettingsSource` case-insensitively, so a guard accepting only the exact
  capitalised spellings would reject a value that works. Derive a guard's rejected set
  from what the application actually rejects.
* `helm template` **skips `NOTES.txt`** and there is no `--notes` flag. To preview it:
  copy it to `templates/NOTES-check.txt`, render with `--debug --show-only
  templates/NOTES-check.txt` (it ends in a YAML parse error - expected, it is not
  YAML - but the text prints above it), then delete the copy.
* After any chart change, run `scripts/deploy/check-chart-guards.ps1`. Adding a
  behaviour without adding its assertion is how this chart acquires silent regressions.


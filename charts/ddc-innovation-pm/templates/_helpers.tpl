{{/*
Shared helpers and render-time guards for ddc-innovation-pm.

WHY A CHART ARGUES WITH ITS OPERATOR
------------------------------------
Every `fail` below exists because a specific values edit is reachable, renders
cleanly, lints clean, and then fails only inside the cluster — with symptoms that
point at the application rather than at the deployment. The chart ships with a
test suite of one: `helm template`. A guard that fires there costs one confused
minute; the same mistake deployed costs an outage and an hour of reading
application logs before anyone thinks to look at environment variables. Each
guard cites the file and line that make the constraint real, so it can be
re-checked rather than trusted.

COMMENT STYLE
-------------
Rationale inside RENDERED templates uses Helm comments ({{- /* like this */ -}})
rather than YAML `#`, because Helm emits `#` verbatim into the manifest. Prose
that names a forbidden key would then appear in the rendered output and break the
`grep -v` assertions this chart is verified with. Nothing is lost: kubectl shows
no comments. This file is never rendered, so it is free to be verbose.
*/}}

{{/*
Chart name. Overridable, but no object name derives from it, so changing it only
changes label values.
*/}}
{{- define "dcipm.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Name prefix for object names.

PINNED, NOT RELEASE-DERIVED. The API Service must be the literal `api` because
nginx inside the web image proxies to http://api:8080 by NAME (nginx/nginx.conf,
`location /api/`). A release-derived prefix (`<release>-api`) would leave the
ConfigMap and Deployments self-consistent and still 502 every API call in the
browser — a failure that looks exactly like an application bug. Risk R6.
*/}}
{{- define "dcipm.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Namespace for every object: .Values.namespace when pinned by an overlay,
otherwise the --namespace flag. Without that fallback `helm template` with no -n
renders namespace: default, so a reviewed manifest would not show the namespace
the overlay intends.
*/}}
{{- define "dcipm.namespace" -}}
{{- if .Values.namespace -}}
{{- .Values.namespace -}}
{{- else -}}
{{- .Release.Namespace -}}
{{- end -}}
{{- end -}}

{{/* API workload/ConfigMap name. */}}
{{- define "dcipm.api.fullname" -}}
{{- printf "%s-api" (include "dcipm.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Web workload name. */}}
{{- define "dcipm.web.fullname" -}}
{{- printf "%s-web" (include "dcipm.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
API Service name: the literal `api`, forever. A contract with a built artefact
(the nginx config in the web image), not with this chart. Renaming it does not
fail the deploy; it fails the product, silently, in the browser.
*/}}
{{- define "dcipm.api.serviceName" -}}api{{- end -}}

{{/* Web Service name: the literal `web`, used by the Ingress root path and by the documented `kubectl port-forward svc/web 8080:80`. */}}
{{- define "dcipm.web.serviceName" -}}web{{- end -}}

{{/* Service account name. */}}
{{- define "dcipm.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "dcipm.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/*
Common labels. `app.kubernetes.io/name` is the chart name, so renaming the
release never splits one application's objects into two selector groups.
*/}}
{{- define "dcipm.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
app.kubernetes.io/name: {{ include "dcipm.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: ddc-innovation-pm
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
{{- end -}}

{{/*
Selector labels. Chart name/version are deliberately excluded: they change on
every upgrade and a Deployment's selector is immutable, so including them makes
every upgrade un-applyable.
*/}}
{{- define "dcipm.selectorLabels" -}}
app.kubernetes.io/name: {{ include "dcipm.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/* Common labels plus a component. Call: (dict "root" $ "component" "api"). */}}
{{- define "dcipm.componentLabels" -}}
{{ include "dcipm.labels" .root }}
app.kubernetes.io/component: {{ .component }}
{{- with .root.Values.commonLabels }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{/* Selector labels plus a component. Same call shape as componentLabels. */}}
{{- define "dcipm.componentSelectorLabels" -}}
{{ include "dcipm.selectorLabels" .root }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/*
Common annotations for every object, or empty. Helm `range`/`with` over an empty
string would render a dangling key, so callers guard with `with`.
*/}}
{{- define "dcipm.annotations" -}}
{{- with .Values.commonAnnotations }}
{{- toYaml . }}
{{- end }}
{{- end -}}

{{/* ---------------------------------------------------------------- */}}
{{/* Images                                                         */}}
{{/* ---------------------------------------------------------------- */}}

{{/*
Tag hygiene. Refuses `latest`, `edge`, branch names and anything that looks like
an image reference, because all of them are mutable: rolling back to a retained
revision would then pull different bits than the ones that ran, and the release
history would lie about what is deployed.
Call: (dict "tag" "…" "key" "image.tag").
*/}}
{{- define "dcipm.guard.tag" -}}
{{- $tag := .tag | toString -}}
{{- if or (eq $tag "latest") (eq $tag "edge") (eq $tag "stable") -}}
{{-   fail (printf "%s is %q: a mutable tag makes `helm rollback` meaningless (the previous release resolves to whatever the registry serves today, not what ran) and makes the running build unverifiable. Pass an immutable identifier, e.g. --set %s=<git sha>." .key $tag .key) -}}
{{- end -}}
{{- if has $tag (list "main" "master" "develop" "dev" "release") -}}
{{-   fail (printf "%s is %q: a branch name is a moving pointer. Pass the commit sha of the build you just pushed, e.g. --set %s=949f353." .key $tag .key) -}}
{{- end -}}
{{- if contains "/" $tag -}}
{{-   fail (printf "%s is %q: that looks like an image reference, not a tag. Set the repository and the tag separately." .key $tag) -}}
{{- end -}}
{{- "" -}}
{{- end -}}

{{/*
API image reference. FAILS THE RENDER while image.tag is empty: a floating tag
makes rollback meaningless and "which build is live?" unanswerable. The value is
the tag passed to scripts/deploy/build-push-images.ps1 -Tag, forwarded on --set by
scripts/deploy/helm-deploy.ps1. It is not derived from git in this workspace copy.
*/}}
{{- define "dcipm.image" -}}
{{- $tag := required "image.tag is required. Pass --set image.tag=<tag> (the tag you built with scripts/deploy/build-push-images.ps1 -Tag). The chart refuses to render without it: a floating or :latest tag makes `helm rollback` meaningless and makes it impossible to tell which build is live." .Values.image.tag -}}
{{- $_ := include "dcipm.guard.tag" (dict "tag" $tag "key" "image.tag") -}}
{{- printf "%s:%s" .Values.image.repository $tag -}}
{{- end -}}

{{/*
Web image reference. Defaults to image.tag so one --set moves both workloads
together; carries the same guard, because a render that pins only one of the two
images is precisely the half-upgrade the guard exists to prevent.
*/}}
{{- define "dcipm.web.image" -}}
{{- $own := .Values.web.image.tag | default "" -}}
{{- $tag := $own | default .Values.image.tag -}}
{{- $key := ternary "web.image.tag" "image.tag" (ne $own "") -}}
{{- $tag := required (printf "%s is required. Pass --set image.tag=<tag>; web.image.tag defaults to it so both workloads move together." $key) $tag -}}
{{- $_ := include "dcipm.guard.tag" (dict "tag" $tag "key" $key) -}}
{{- printf "%s:%s" .Values.web.image.repository $tag -}}
{{- end -}}

{{/* ---------------------------------------------------------------- */}}
{{/* Namespace (risk R11)                                           */}}
{{/* ---------------------------------------------------------------- */}}

{{/*
Refuse to render into a namespace that belongs to something else.

`ddc-pm` is one character from `ddc-pme`, which hosts a DIFFERENT live
application (pme-api) and the deployer ServiceAccount. Kubernetes applies a
mis-namespaced manifest without complaint, so the typo silently redeploys onto
the wrong app.

Both the --namespace flag and .Values.namespace are checked, so the value that
exists to pin the namespace cannot be used to bypass the check on the flag.
*/}}
{{- define "dcipm.guard.namespace" -}}
{{- $target := include "dcipm.namespace" . -}}
{{- /* Captured BEFORE range: inside `range`, `.` is rebound to the element, so
       .Release and .Values do not exist there and referencing them fails the
       render with "can't evaluate field Release in type interface {}" — which
       looks like a broken template rather than the intended guard message. */ -}}
{{- $releaseNs := .Release.Namespace -}}
{{- $valuesNs := (toString .Values.namespace) -}}
{{- range $forbidden := (.Values.forbiddenNamespaces | default list) -}}
{{-   if eq $target (toString $forbidden) -}}
{{-     fail (printf "refusing to render into namespace %q: it is listed in forbiddenNamespaces (--namespace=%s, .Values.namespace=%s). `ddc-pme` hosts the live DDC-PME application (pme-api) and the deployer ServiceAccount, so deploying this chart there would silently redeploy onto someone else's application. This project's namespaces are `ddc-pm` (Production) and `ddc-pm-dev` (development)." $target $releaseNs (ternary $valuesNs "<unset>" (ne $valuesNs ""))) -}}
{{-   end -}}
{{- end -}}
{{- if and .Values.namespace (ne .Values.namespace .Release.Namespace) -}}
{{-   fail (printf "namespace mismatch: --namespace is %q but .Values.namespace is %q. Helm would record the release in one namespace while the objects specify another, which is either a mistake or a render that is not what you think it is. Pass the same value to both, e.g. -n %s." .Release.Namespace .Values.namespace .Values.namespace) -}}
{{- end -}}
{{- "" -}}
{{- end -}}

{{/* ---------------------------------------------------------------- */}}
{{/* Service names (risk R6)                                        */}}
{{/* ---------------------------------------------------------------- */}}

{{/*
Refuse a fullnameOverride that would collide with the reserved Service names.

The Services are named absolutely (`api`, `web`) because other artefacts
reference them by string, so a fullnameOverride cannot rename them — it can only
collide with them or orphan them. Fail with the reason instead of rendering
something subtle.
*/}}
{{- define "dcipm.guard.serviceNames" -}}
{{- $reserved := list "api" "web" -}}
{{- $api := include "dcipm.api.fullname" . -}}
{{- $web := include "dcipm.web.fullname" . -}}
{{- if or (has $api $reserved) (has $web $reserved) (has (include "dcipm.fullname" .) $reserved) -}}
{{-   fail (printf "fullnameOverride %q produces object names (%s, %s) that collide with the reserved Service names `api` and `web`. Those two names are referenced by string from the web image's nginx config (http://api:8080) and from the documented `kubectl port-forward svc/web`, so they cannot be reassigned or shadowed. Choose a different fullnameOverride." .Values.fullnameOverride $api $web) -}}
{{- end -}}
{{- "" -}}
{{- end -}}

{{/* ---------------------------------------------------------------- */}}
{{/* Secret references (D10)                                        */}}
{{/* ---------------------------------------------------------------- */}}

{{/*
Secret references must be literal, valid, non-empty names — they are
configuration, never credentials, so there is no reason for one to be templated
or blank. A blank name renders `secretRef: {name: ""}` and kubelet reports
CreateContainerConfigError, a message that never mentions values.yaml.
*/}}
{{- define "dcipm.guard.secretNames" -}}
{{- range $entry := (list (list "api.dbSecretName" .Values.api.dbSecretName) (list "api.authSecretName" .Values.api.authSecretName)) -}}
{{-   $key := index $entry 0 -}}
{{-   $name := index $entry 1 | toString -}}
{{-   if empty $name -}}
{{-     fail (printf "%s is empty. Secret NAMES are configuration (D10): the Secret itself is created out of band and referenced here. Set it to e.g. dcipm-db." $key) -}}
{{-   end -}}
{{-   if or (regexMatch "[^a-z0-9.-]" $name) (regexMatch "^[.-]" $name) (regexMatch "[.-]$" $name) (gt (len $name) 253) -}}
{{-     fail (printf "%s is %q, which is not a valid Kubernetes Secret name (lowercase alphanumeric, '.' or '-', no leading/trailing punctuation)." $key $name) -}}
{{-   end -}}
{{- end -}}
{{- "" -}}
{{- end -}}

{{/*
imagePullSecrets entries must each carry a name; an entry without one renders a
pod that ImagePullBackOffs with no message pointing at the cause.

The kindIs check exists because Helm's --set cannot express an empty list, and
both of its plausible-looking attempts produce a list of ONE element whose member
is a STRING rather than a map (measured against a scratch chart, 2026-10-16):

    --set imagePullSecrets={}    -> len 1, element type string, value ""
    --set imagePullSecrets=[]    -> the literal string "[]" (range: "can't iterate")

Without this branch `get $s "name"` dies on the string with
"wrong type for value; expected map[string]interface {}; got string" — Go
internals, naming neither the key nor the fix, which is the exact failure this
guard was written to prevent. To pull anonymously, set `imagePullSecrets: []` in
a values FILE (what values-prod.yaml does; that yields len 0 and is the supported
form), or `--set imagePullSecrets=null`.
*/}}
{{- define "dcipm.guard.imagePullSecrets" -}}
{{- range $i, $s := (.Values.imagePullSecrets | default list) -}}
{{-   if not (kindIs "map" $s) -}}
{{-     fail (printf "imagePullSecrets[%d] is %q, not a mapping with a `name` key. --set cannot build an empty list: `imagePullSecrets={}` yields one empty string and `imagePullSecrets=[]` yields the literal string \"[]\". To pull anonymously write `imagePullSecrets: []` in a values file, or pass `--set imagePullSecrets=null`." $i (toString $s)) -}}
{{-   end -}}
{{-   if not (get $s "name") -}}
{{-     fail (printf "imagePullSecrets[%d] has no `name`. It must reference a pre-created docker-registry Secret in the target namespace (D10: the chart references Secret names only and never holds a credential)." $i) -}}
{{-   end -}}
{{- end -}}
{{- "" -}}
{{- end -}}

{{/* ---------------------------------------------------------------- */}}
{{/* Auth shape (risk R9, decision D4)                              */}}
{{/* ---------------------------------------------------------------- */}}

{{/*
Fail the render on a shape the API itself refuses to boot with. Every rule is
read out of the application, not invented here:

  * Production requires Auth:Mode=Keycloak (Security/AuthOptions.cs:152-157).
  * Keycloak/Hybrid need a non-empty Issuer and Audience (AuthOptions.cs:161-164).
  * Production requires Keycloak:RequireHttpsMetadata=true (AuthOptions.cs:165-166).
  * With RequireHttpsMetadata=true, JwtBearer REJECTS a non-https
    MetadataAddress — "The MetadataAddress or Authority must use HTTPS unless
    disabled for development by setting RequireHttpsMetadata=false" — and the
    process exits at startup.

The last rule is the trap inside the issuer/discovery split. The split is
genuinely supported (AuthOptions.cs:101-105; AuthenticationSetup.cs:154-157,
"the metadata address may be an internal host … while tokens carry the external
issuer, so the issuer is validated explicitly rather than derived from
Authority"), and it is the right design when pods cannot reach the DMZ. But it
cannot be combined with the Production environment, because the Production guard
forces the https flag on. Both halves were verified against a host configured
exactly like AuthenticationSetup.cs.
*/}}
{{- define "dcipm.guard.auth" -}}
{{- $env := toString .Values.aspnetcoreEnvironment -}}
{{- $mode := toString .Values.authMode -}}
{{- $source := toString .Values.identitySettingsSource -}}
{{- /* Compared case-insensitively because that is what the application does
       (StringComparison.OrdinalIgnoreCase, AuthOptions.cs:63-64,169-174). A guard
       stricter than the code it guards produces false positives: it would reject
       `configuration`, which works perfectly well, and then nobody trusts the
       guard. Reject exactly what the API rejects, nothing more. */ -}}
{{- if and $source (not (has (lower $source) (list "database" "configuration"))) -}}
{{-   fail (printf "identitySettingsSource is %q: only \"Database\" and \"Configuration\" are accepted, and any other value fails startup validation (Security/AuthOptions.cs:169-174). Leave it empty to use the application default (Database). \"Configuration\" is the break-glass that makes the keycloak: block win over an active identity_provider_connections row." $source) -}}
{{- end -}}
{{- $production := eq $env "Production" -}}
{{- if and $production (ne $mode "Keycloak") -}}
{{-   fail (printf "aspnetcoreEnvironment=Production requires authMode=Keycloak, got %q. The API refuses to start in Production with any other mode (Security/AuthOptions.cs:152-157), so this renders a pod that CrashLoops quoting a config key. There is no development header login in Production; use authMode=Keycloak, or render the dev overlay for a non-Production environment." $mode) -}}
{{- end -}}
{{- if has $mode (list "Keycloak" "Hybrid") -}}
{{-   if and (empty (.Values.keycloak.baseUrl | toString)) (empty (.Values.keycloak.issuerOverride | toString)) -}}
{{-     fail "authMode=Keycloak needs keycloak.baseUrl (or keycloak.issuerOverride): the API fails to start with an empty issuer (Security/AuthOptions.cs:161-162)." -}}
{{-   end -}}
{{-   if and (empty (.Values.keycloak.realm | toString)) (empty (.Values.keycloak.issuerOverride | toString)) -}}
{{-     fail "authMode=Keycloak needs keycloak.realm: the issuer is derived as {baseUrl}/realms/{realm}, and an empty realm produces an issuer Keycloak will not match." -}}
{{-   end -}}
{{-   if empty (.Values.keycloak.audience | toString) -}}
{{-     fail "authMode=Keycloak needs keycloak.audience (e.g. dcipm-api): the API fails to start with an empty audience (Security/AuthOptions.cs:163-164). The value must match the audience mapper on the Keycloak client, or every token is rejected at validation instead of at boot." -}}
{{-   end -}}
{{-   if and $production (not .Values.keycloak.requireHttpsMetadata) -}}
{{-     fail "Production requires keycloak.requireHttpsMetadata=true (Security/AuthOptions.cs:165-166); the API refuses to start otherwise." -}}
{{-   end -}}
{{-   $meta := toString .Values.keycloak.metadataAddress -}}
{{-   if and $meta .Values.keycloak.requireHttpsMetadata (not (hasPrefix "https://" $meta)) -}}
{{-     fail (printf "keycloak.metadataAddress is %q (not https) while keycloak.requireHttpsMetadata is true. JwtBearer then throws \"The MetadataAddress or Authority must use HTTPS unless disabled for development by setting RequireHttpsMetadata=false\" and the pod CrashLoops. In Production that flag is FORCED true by the guard, so an in-cluster http discovery address cannot coexist with the Production environment. Either leave keycloak.metadataAddress empty (the public https issuer's discovery document is used), or use the issuer/discovery split outside Production — values-dev.yaml shows that shape." $meta) -}}
{{-   end -}}
{{-   $jwks := toString .Values.keycloak.jwksUrl -}}
{{-   if and $jwks .Values.keycloak.requireHttpsMetadata (not (hasPrefix "https://" $jwks)) -}}
{{-     fail (printf "keycloak.jwksUrl is %q (not https) while keycloak.requireHttpsMetadata is true; the resolver throws at the first token (Security/AuthenticationSetup.cs:208-209). Use an https address, or turn the flag off outside Production." $jwks) -}}
{{-   end -}}
{{- end -}}
{{- "" -}}
{{- end -}}

{{/* ---------------------------------------------------------------- */}}
{{/* Ingress + CORS (decisions D1/D4, risks R12/R15)                */}}
{{/* ---------------------------------------------------------------- */}}

{{/*
Effective CORS origins as a JSON array: explicit entries win; otherwise derive
the single public origin from the ingress host; otherwise nothing, so a dev
render sets no origin (correct for something reached only by port-forward).

JSON and not YAML, deliberately. A top-level YAML *sequence* cannot be read back
with sprig's fromYaml: it expects a mapping and on failure returns the map
{"Error": "..."} rather than failing loudly. That silently produced one bogus env
key called Cors__AllowedOrigins__Error in a real render. fromJson round-trips a
list cleanly, so what follows ranges over actual entries.

Kept in one helper so CORS and the Ingress cannot drift apart. That drift is a
browser-side failure with server-side symptoms ("the API is fine — ask the
browser"), which is the worst kind to debug.
*/}}
{{- define "dcipm.cors.origins" -}}
{{- $explicit := .Values.cors.allowedOrigins | default list -}}
{{- if gt (len $explicit) 0 -}}
{{-   toJson $explicit -}}
{{- else if and .Values.ingress.enabled .Values.ingress.host -}}
{{-   printf "[\"https://%s\"]" .Values.ingress.host -}}
{{- else -}}
{{-   "[]" -}}
{{- end -}}
{{- end -}}

{{/*
Ingress and CORS shape checks.

  * The scheme test runs BEFORE the RFC 1123 test on purpose: `https://host`
    already fails the hostname pattern, so checking hostname first would report
    a misleading error and leave the real mistake undiscoverable.
  * The API path must be more specific than the root path; with two identical
    Prefix rules Traefik picks one backend and the API is unreachable through the
    Ingress while everything looks applied.
  * CORS entries must be bare origins. WithOrigins() does NOT validate them
    (verified: "https://host/" and "https://host/path" both build without
    complaint), so a malformed entry silently matches nothing and the symptom is
    a CORS error in the browser against a perfectly healthy API. The chart does
    not normalise (no trimSuffix "/") so the guard fails loudly instead of
    quietly deploying something the operator did not write.
*/}}
{{- define "dcipm.guard.ingress" -}}
{{- if .Values.ingress.enabled -}}
{{-   $host := toString .Values.ingress.host -}}
{{-   if empty $host -}}
{{-     fail "ingress.enabled is true but ingress.host is empty. The host belongs to the production overlay (values-prod.yaml is the only file that names it). An Ingress with no host would match every hostname on the shared Traefik and steal traffic from other applications." -}}
{{-   end -}}
{{-   if or (contains "://" $host) (contains "/" $host) -}}
{{-     fail (printf "ingress.host is %q: give a bare hostname with no scheme and no path. https exists only in the browser — TLS terminates at pfSense HAProxy (D2/D3) — and the Ingress spec has nowhere to put a scheme." $host) -}}
{{-   end -}}
{{-   if not (regexMatch "^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$" $host) -}}
{{-     fail (printf "ingress.host %q is not a valid RFC 1123 DNS hostname; Kubernetes would reject the Ingress." $host) -}}
{{-   end -}}
{{-   $apiPath := toString .Values.ingress.apiPath -}}
{{-   $rootPath := toString .Values.ingress.path -}}
{{-   if not (hasPrefix $rootPath $apiPath) -}}
{{-     fail (printf "ingress.apiPath %q is not under ingress.path %q, so the API would not be routed by these rules." $apiPath $rootPath) -}}
{{-   end -}}
{{-   if eq $apiPath $rootPath -}}
{{-     fail (printf "ingress.apiPath and ingress.path are both %q: the two paths must differ, otherwise there is one rule for two backends and Traefik chooses one of them." $rootPath) -}}
{{-   end -}}
{{- end -}}
{{- range $i, $origin := (.Values.cors.allowedOrigins | default list) -}}
{{-   $o := toString $origin -}}
{{-   if not (regexMatch "^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?$" $o) -}}
{{-     fail (printf "cors.allowedOrigins[%d] is %q. It must be a bare origin — http(s)://host[:port] with no path and no trailing slash (D4). CORS does not treat an origin as a prefix, so an entry with a path or slash matches nothing: the browser blocks every API call while the API looks healthy. The chart does not normalise these values; fix the value." $i $o) -}}
{{-   end -}}
{{- end -}}
{{- "" -}}
{{- end -}}

{{/* ---------------------------------------------------------------- */}}
{{/* Pod security                                                   */}}
{{/* ---------------------------------------------------------------- */}}

{{/*
The API pod must be non-root and must not allow escalation.

The image ends with `USER $APP_UID` (uid 1654 in
mcr.microsoft.com/dotnet/aspnet:9.0, the Dockerfile's own APP_UID).
runAsNonRoot: true lets the kubelet enforce that at admission instead of
discovering a root container at runtime. Deleting the flag from a values overlay
would quietly undo the requirement, which is why it is asserted and not merely
defaulted.
*/}}
{{- define "dcipm.guard.securityContext" -}}
{{- $psc := .Values.api.podSecurityContext | default dict -}}
{{- if not (get $psc "runAsNonRoot") -}}
{{-   fail "api.podSecurityContext.runAsNonRoot must be true. The API image runs as $APP_UID (1654) and this deployment is specified non-root; dropping the flag lets a root container start without any signal." -}}
{{- end -}}
{{- if eq (get $psc "runAsUser" | toString) "0" -}}
{{-   fail "api.podSecurityContext.runAsUser must not be 0 (root)." -}}
{{- end -}}
{{- $sc := .Values.api.securityContext | default dict -}}
{{- if get $sc "privileged" -}}
{{-   fail "api.securityContext.privileged must not be true: the API needs no device access and no host namespaces." -}}
{{- end -}}
{{- if get $sc "allowPrivilegeEscalation" -}}
{{-   fail "api.securityContext.allowPrivilegeEscalation must be false, as shipped." -}}
{{- end -}}
{{- "" -}}
{{- end -}}

{{/* ---------------------------------------------------------------- */}}
{{/* Feature gate + probes                                          */}}
{{/* ---------------------------------------------------------------- */}}

{{/*
Features__UseHttpsRedirection must resolve to a real boolean: the API throws for
anything else (Program.DeploymentSwitches.cs:50-55), and because env values are
strings a YAML `False`, `no` or `0` renders fine and then CrashLoops the pod
quoting a config key.
*/}}
{{- define "dcipm.useHttpsRedirection" -}}
{{- $v := .Values.features.useHttpsRedirection -}}
{{- if kindIs "bool" $v -}}
{{-   $v | toString | lower -}}
{{- else -}}
{{-   $s := $v | toString | lower -}}
{{-   if not (has $s (list "true" "false")) -}}
{{-     fail (printf "features.useHttpsRedirection is %q and must be the boolean true or false. The API throws for any other value (Program.DeploymentSwitches.cs:50-55), so a typo here is a CrashLoopBackOff that quotes a configuration key instead of a values error you could read." $v) -}}
{{-   end -}}
{{-   $s -}}
{{- end -}}
{{- end -}}

{{/*
Probe paths must be the two endpoints the application actually maps
(Program.cs:156-157). There is no /healthz: a guessed path leaves the pod
permanently unready and reads as an application defect.
*/}}
{{- define "dcipm.guard.probes" -}}
{{- $valid := list "/health/live" "/health/ready" -}}
{{- range $entry := (list (list "api.probes.startup" .Values.api.probes.startup) (list "api.probes.liveness" .Values.api.probes.liveness) (list "api.probes.readiness" .Values.api.probes.readiness)) -}}
{{-   $key := index $entry 0 -}}
{{-   $p := index $entry 1 | default dict -}}
{{-   if get $p "enabled" -}}
{{-     $path := get $p "path" | toString -}}
{{-     if not (has $path $valid) -}}
{{-       fail (printf "%s.path is %q, but the only health endpoints the application maps are /health/live and /health/ready (apps/api/Dcipm.Api/Program.cs:156-157). A guessed path like /healthz makes the pod never become Ready, which looks exactly like an application bug rather than a probe typo." $key $path) -}}
{{-     end -}}
{{-   end -}}
{{- end -}}
{{- $ready := .Values.api.probes.readiness | default dict -}}
{{- if and (get $ready "enabled") (eq (get $ready "path" | toString) "/health/live") -}}
{{-   fail "api.probes.readiness.path should be /health/ready, not /health/live: /health/ready is the check that includes the database (Program.cs:24-25), and readiness is the probe that should remove a pod whose database is unreachable." -}}
{{- end -}}
{{- "" -}}
{{- end -}}

{{/* ---------------------------------------------------------------- */}}
{{/* API environment                                                */}}
{{/* ---------------------------------------------------------------- */}}

{{/*
The API's non-secret environment, as YAML map text. One definition on purpose:
the ConfigMap, the migration Job and the guards below all describe the same set,
and three copies of an env list is how drift starts.

The three deployment-shape keys are a CONTRACT with
apps/api/Dcipm.Api/Program.DeploymentSwitches.cs — the constants in that file are
the only authority on their names. A mistyped key compiles, passes every unit
test, and is silently ignored at runtime.
*/}}
{{- define "dcipm.api.env" -}}
ASPNETCORE_ENVIRONMENT: {{ .Values.aspnetcoreEnvironment | quote }}
ASPNETCORE_URLS: {{ .Values.api.urls | default "http://+:8080" | quote }}
Logging__LogLevel__Default: {{ .Values.api.logLevel | default "Information" | quote }}
Auth__Mode: {{ .Values.authMode | quote }}
{{- /* Omitted when empty so the application default (Database) applies. See the
       long note on `keycloak:` in values.yaml: with Database, a DB row overrides
       the Keycloak env below, which is the single most confusing thing about
       changing auth in this deployment. */}}
{{- with .Values.identitySettingsSource }}
Auth__IdentitySettingsSource: {{ . | quote }}
{{- end }}
{{- if has (.Values.authMode | toString) (list "Keycloak" "Hybrid") }}
Keycloak__BaseUrl: {{ .Values.keycloak.baseUrl | quote }}
Keycloak__Realm: {{ .Values.keycloak.realm | quote }}
Keycloak__ClientId: {{ .Values.keycloak.clientId | quote }}
Keycloak__Audience: {{ .Values.keycloak.audience | quote }}
Keycloak__RequireHttpsMetadata: {{ .Values.keycloak.requireHttpsMetadata | toString | lower | quote }}
{{- with .Values.keycloak.issuerOverride }}
Keycloak__Issuer: {{ . | quote }}
{{- end }}
{{- with .Values.keycloak.metadataAddress }}
Keycloak__MetadataAddress: {{ . | quote }}
{{- end }}
{{- with .Values.keycloak.jwksUrl }}
Keycloak__JwksUrl: {{ . | quote }}
{{- end }}
{{- end }}
Features__UseHttpsRedirection: {{ include "dcipm.useHttpsRedirection" . | quote }}
{{- range $i, $cidr := (.Values.api.trustedProxyCidrs | default list) }}
ForwardedHeaders__TrustedProxyCidrs__{{ $i }}: {{ $cidr | toString | quote }}
{{- end }}
{{- range $i, $ip := (.Values.api.trustedProxyIps | default list) }}
ForwardedHeaders__TrustedProxyIps__{{ $i }}: {{ $ip | toString | quote }}
{{- end }}
{{- $origins := fromJsonArray (include "dcipm.cors.origins" .) }}
{{- /* ASP.NET Core merges configuration arrays BY INDEX rather than replacing
       them, and appsettings.json ships two loopback CORS origins. Overriding
       index 0 alone therefore leaves http://localhost:6191 live in Production —
       a reachable allow-list entry that no values file mentions (verified
       against a pipeline built from Program.cs:66-72). Blank the inherited
       indices: an empty entry never matches a real Origin header, and the
       resulting policy was verified to allow the configured origin and deny the
       inherited ones.

       Only when at least one origin IS configured. With nothing configured
       (a dev render, reached by port-forward, with no ingress host) this emits
       no CORS keys at all, so appsettings.json's loopback entries survive and
       local development on http://localhost:6191 keeps working. Masking
       unconditionally would blank the very origins development depends on. */}}
{{- if gt (len $origins) 0 }}
{{- range $i, $origin := $origins }}
Cors__AllowedOrigins__{{ $i }}: {{ $origin | toString | quote }}
{{- end }}
{{- $from := len $origins }}
{{- range $i := until (int (.Values.cors.inheritedOriginCount | default 0)) }}
Cors__AllowedOrigins__{{ add $from $i }}: ""
{{- end }}
{{- end }}
{{- end -}}

{{/*
Keys the API ConfigMap would set, one per line, for the credential guard.
*/}}
{{- define "dcipm.api.envKeys" -}}
{{- range $line := splitList "\n" (include "dcipm.api.env" .) -}}
{{-   $key := trim (splitList ":" $line | first) -}}
{{-   if $key }}{{ printf "%s\n" $key }}{{ end -}}
{{- end -}}
{{- end -}}

{{/*
Keys an operator added via api.extraEnv, checked with the same rules as the
generated set — an escape hatch that skips validation is not an escape hatch.
*/}}
{{- define "dcipm.api.extraEnvKeys" -}}
{{- range $e := (.Values.api.extraEnv | default list) -}}
{{-   with get $e "name" }}{{ printf "%s\n" . }}{{ end -}}
{{- end -}}
{{- end -}}

{{/*
Env keys that must never be set, each with the reason it is forbidden.
Call with a list of key names.
*/}}
{{- define "dcipm.guard.forbiddenEnvKeys" -}}
{{- range $key := . -}}
{{-   $k := trim $key -}}
{{-   if $k -}}
{{-     if hasPrefix "ConnectionStrings__" $k -}}
{{-       fail (printf "%q must never be set in a ConfigMap. Connection strings come from the pre-created Secret only (D10/D11): the image ships empty values for exactly these keys so that the guard in Dcipm.Api/Security names the missing key when they are absent, instead of silently connecting somewhere unintended." $k) -}}
{{-     end -}}
{{-     if eq $k "ASPNETCORE_HTTPS_PORT" -}}
{{-       fail "ASPNETCORE_HTTPS_PORT must never be set (risk R12): with no HTTPS endpoint in the pod, Kestrel redirects every request to a port nothing listens on, which with HAProxy in front is an infinite redirect loop." -}}
{{-     end -}}
{{-     if or (regexMatch "(?i)secret" $k) (regexMatch "(?i)password" $k) (regexMatch "(?i)connectionstring" $k) -}}
{{-       fail (printf "%q looks like a credential and must not go into a ConfigMap. Non-secret env only (D10): put it in the Secret referenced by api.dbSecretName / api.authSecretName, or in api.extraEnv with a valueFrom.secretKeyRef." $k) -}}
{{-     end -}}
{{-   end -}}
{{- end -}}
{{- "" -}}
{{- end -}}

{{/*
One entry point that runs every guard. Each template begins by calling it, so a
template added later cannot skip validation and a subset render (--show-only)
cannot bypass a guard.
*/}}
{{- /*
Secret-creation guard. Only runs when secrets.create is true; with the default
(false) there is nothing to check, so the common path cannot be broken by it.

Two things are asserted, and the second is the reason this guard exists:

1. Creating a Secret with no keys is useless and fails confusingly at runtime --
   the API boots, finds no ConnectionStrings__Dcipm, and exits naming the key.

2. Creating a Secret named api.dbSecretName hands OWNERSHIP of that object to
   Helm: `helm uninstall` deletes it, and the Ansible playbook that used to write
   it starts fighting Helm over the same resource. Neither consequence shows up
   on the day someone flips the flag; both show up during an incident, as "the
   connection string vanished". Requiring secrets.takeOwnership=true makes the
   handover an explicit, deliberate act instead of a side effect.
*/ -}}
{{- define "dcipm.guard.secrets" -}}
{{- if .Values.secrets.create -}}
{{-   $api := .Values.secrets.api | default dict -}}
{{-   if eq (len $api) 0 -}}
{{-     fail "secrets.create is true but secrets.api is empty, which would render a Secret with no data. The API would start, read no ConnectionStrings__Dcipm, and exit at startup. Either supply the keys or set secrets.create=false and keep referencing the pre-created Secret by name (api.dbSecretName)." -}}
{{-   end -}}
{{-   if not .Values.secrets.takeOwnership -}}
{{-     fail (printf "secrets.create is true but secrets.takeOwnership is false, refusing to render. This chart would CREATE a Secret named %q -- the object the deploy playbook writes out of band -- which hands Helm ownership of it: `helm uninstall` DELETES it, the API comes back with no connection string, and the playbook then contends with Helm for the same object. Set secrets.takeOwnership=true once you have accepted that, or leave secrets.create=false (the default) and keep referencing the pre-created Secret by name." (toString .Values.api.dbSecretName)) -}}
{{-   end -}}
{{- end -}}
{{- "" -}}
{{- end -}}

{{- define "dcipm.guardAll" -}}
{{- $_ := include "dcipm.guard.namespace" . -}}
{{- $_ := include "dcipm.guard.serviceNames" . -}}
{{- $_ := include "dcipm.guard.imagePullSecrets" . -}}
{{- $_ := include "dcipm.guard.secretNames" . -}}
{{- $_ := include "dcipm.guard.auth" . -}}
{{- $_ := include "dcipm.guard.ingress" . -}}
{{- $_ := include "dcipm.guard.securityContext" . -}}
{{- $_ := include "dcipm.guard.secrets" . -}}
{{- $_ := include "dcipm.guard.probes" . -}}
{{- $_ := include "dcipm.guard.forbiddenEnvKeys" (splitList "\n" (printf "%s\n%s" (include "dcipm.api.envKeys" .) (include "dcipm.api.extraEnvKeys" .))) -}}
{{- $_ := include "dcipm.image" . -}}
{{- $_ := include "dcipm.web.image" . -}}
{{- "" -}}
{{- end -}}

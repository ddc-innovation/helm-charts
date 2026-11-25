{{/*
===============================================================================
ddc-pme chart helpers
===============================================================================
Conventions
  * Every helper that needs more than the root context takes a dict.
  * Helm NEVER creates a Secret or a credential here. Credentials are read by
    Pods from Secrets that already exist (created in Rancher). The only thing
    this file does with Secrets is VALIDATE that they exist (lookup + fail).
===============================================================================
*/}}

{{/* Chart name, overridable. */}}
{{- define "ddc-pme.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Fully qualified app name (max 63 chars: DNS label limit). */}}
{{- define "ddc-pme.fullname" -}}
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

{{/* Chart name + version, for the helm.sh/chart label. */}}
{{- define "ddc-pme.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Per-component resource name, e.g. <fullname>-api. Args: dict "root" . "component" "api"
Truncated so the suffix always survives.
*/}}
{{- define "ddc-pme.componentName" -}}
{{- $suffix := printf "-%s" .component -}}
{{- printf "%s%s" (include "ddc-pme.fullname" .root | trunc (int (sub 63 (len $suffix))) | trimSuffix "-") $suffix -}}
{{- end -}}

{{/* Selector labels (immutable once a workload exists: never add to this). */}}
{{- define "ddc-pme.selectorLabels" -}}
app.kubernetes.io/name: {{ include "ddc-pme.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/* Common labels. */}}
{{- define "ddc-pme.labels" -}}
helm.sh/chart: {{ include "ddc-pme.chart" . }}
{{ include "ddc-pme.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: ddc-pme
{{- with .Values.global.commonLabels }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{/* Component selector labels. Args: dict "root" . "component" "api" */}}
{{- define "ddc-pme.componentSelectorLabels" -}}
{{ include "ddc-pme.selectorLabels" .root }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/* Component labels. Args: dict "root" . "component" "api" */}}
{{- define "ddc-pme.componentLabels" -}}
{{ include "ddc-pme.labels" .root }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/* ServiceAccount name. */}}
{{- define "ddc-pme.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "ddc-pme.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/*
Image reference: <repository>:<tag>.
Args: dict "root" . "image" <values image map> "component" "<name>"
Tag = component tag if set, else global.imageTag. It is REQUIRED and may not be
"latest" (a moving tag makes rollbacks and node caches unpredictable).
*/}}
{{- define "ddc-pme.image" -}}
{{- $tag := toString (default .root.Values.global.imageTag .image.tag) -}}
{{- $tag = required (printf "ddc-pme: image tag for '%s' is required. Set global.imageTag (e.g. --set global.imageTag=1.2.3) or an explicit per-component image.tag." .component) $tag -}}
{{- if eq (lower $tag) "latest" -}}
{{- fail (printf "ddc-pme: image tag 'latest' is not allowed for '%s'. Use an immutable tag, e.g. --set global.imageTag=1.2.3" .component) -}}
{{- end -}}
{{- printf "%s:%s" (required (printf "ddc-pme: image.repository for '%s' is required" .component) .image.repository) $tag -}}
{{- end -}}

{{/* imagePullPolicy. Args: dict "root" . "image" <values image map> */}}
{{- define "ddc-pme.pullPolicy" -}}
{{- default (default "IfNotPresent" .root.Values.global.imagePullPolicy) .image.pullPolicy -}}
{{- end -}}

{{/* imagePullSecrets block (empty for public images). */}}
{{- define "ddc-pme.imagePullSecrets" -}}
{{- with .Values.imagePullSecrets }}
imagePullSecrets:
{{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{/*
Service type guard. Every Service in this chart is ClusterIP; reachability from
outside the cluster is the Ingress/IngressRoute's job. Args: dict "component" "api" "type" <value>
*/}}
{{- define "ddc-pme.serviceType" -}}
{{- $type := default "ClusterIP" .type -}}
{{- if ne (toString $type) "ClusterIP" -}}
{{- fail (printf "ddc-pme: %s.service.type is '%s'; only ClusterIP is allowed (NodePort/LoadBalancer would bypass the ingress). Expose it through ingress.* instead." .component $type) -}}
{{- end -}}
{{- $type -}}
{{- end -}}

{{/* ----------------------------------------------------------------------- */}}
{{/* Existing-Secret validation                                              */}}
{{/* ----------------------------------------------------------------------- */}}

{{/*
Assert that Secret <name> exists in the release namespace and carries every key.
Args: dict "root" . "name" "ddc-pme-redis" "keys" (list "password")

`lookup` only returns data against a live API server (helm install/upgrade and
`--dry-run=server`). With `helm template`, `helm lint` and `--dry-run=client` it
returns empty, which is indistinguishable from "Secret missing"; that is what
secretValidation.allowOffline is for (CI lint only, never a real release).
*/}}
{{- define "ddc-pme.assertSecret" -}}
{{- $root := .root -}}
{{- $name := .name -}}
{{- $keys := .keys -}}
{{- $ns := $root.Release.Namespace -}}
{{- if $root.Values.secretValidation.enabled -}}
{{- $secret := lookup "v1" "Secret" $ns $name -}}
{{- if not $secret -}}
{{- if not $root.Values.secretValidation.allowOffline -}}
{{- $quoted := list -}}
{{- range $keys -}}
{{- $quoted = append $quoted (printf "'%s'" .) -}}
{{- end -}}
{{- $keyText := printf "%s %s" (ternary "key" "keys" (eq (len $keys) 1)) (join ", " $quoted) -}}
{{- fail (printf "ddc-pme: required Secret '%s' (%s) not found in namespace %s. Create it in Rancher first." $name $keyText $ns) -}}
{{- end -}}
{{- else -}}
{{- $data := default (dict) $secret.data -}}
{{- range $keys -}}
{{- if not (get $data .) -}}
{{- fail (printf "ddc-pme: required Secret '%s' in namespace %s has no key '%s' (or it is empty). Add the key in Rancher first." $name $ns .) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Persistence guard. Only checked when persistence.enabled. The storage class is
set explicitly on the volumeClaimTemplate so a change of the cluster default can
never move the data to another backend. Args: dict "component" "minio" "persistence" <map>
*/}}
{{- define "ddc-pme.assertPersistence" -}}
{{- if .persistence.enabled -}}
{{- if not .persistence.storageClass -}}
{{- fail (printf "ddc-pme: %s.persistence.storageClass is required when persistence is enabled (set it explicitly, e.g. 'longhorn'; the cluster default is deliberately not used)" .component) -}}
{{- end -}}
{{- if not .persistence.size -}}
{{- fail (printf "ddc-pme: %s.persistence.size is required when persistence is enabled" .component) -}}
{{- end -}}
{{- if not (has .persistence.accessMode (list "ReadWriteOnce" "ReadWriteOncePod" "ReadWriteMany" "ReadOnlyMany")) -}}
{{- fail (printf "ddc-pme: %s.persistence.accessMode '%v' is not a valid access mode" .component .persistence.accessMode) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Pod scheduling block shared by every Pod of the release (nodeSelector,
tolerations, affinity). Args: the root context. Renders nothing when unset.
*/}}
{{- define "ddc-pme.scheduling" -}}
{{- with .Values.scheduling.nodeSelector }}
nodeSelector:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.scheduling.tolerations }}
tolerations:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.scheduling.affinity }}
affinity:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{/* Validate every Secret this release depends on. Renders nothing. */}}
{{- define "ddc-pme.validateSecrets" -}}
{{- $s := .Values.existingSecrets -}}
{{- include "ddc-pme.assertSecret" (dict "root" . "name" $s.mysql.name "keys" (list $s.mysql.usernameKey $s.mysql.passwordKey)) -}}
{{- include "ddc-pme.assertSecret" (dict "root" . "name" $s.redis.name "keys" (list $s.redis.passwordKey)) -}}
{{- include "ddc-pme.assertSecret" (dict "root" . "name" $s.minio.name "keys" (list $s.minio.accessKeyKey $s.minio.secretKeyKey)) -}}
{{- /* A public PKCE client has no secret. Only a confidential client does. */ -}}
{{- if not .Values.keycloak.publicClient -}}
{{- include "ddc-pme.assertSecret" (dict "root" . "name" $s.keycloak.name "keys" (list $s.keycloak.clientSecretKey)) -}}
{{- end -}}
{{- end -}}

{{/* ----------------------------------------------------------------------- */}}
{{/* Configuration guards (render-time)                                      */}}
{{/* ----------------------------------------------------------------------- */}}

{{/* Fails the render on an unsafe or unsupported configuration. Renders nothing. */}}
{{- define "ddc-pme.validate" -}}
{{- /* Services: ClusterIP only. */ -}}
{{- $_ := include "ddc-pme.serviceType" (dict "component" "nginx" "type" .Values.nginx.service.type) -}}
{{- $_ = include "ddc-pme.serviceType" (dict "component" "api" "type" .Values.api.service.type) -}}
{{- $_ = include "ddc-pme.serviceType" (dict "component" "redis" "type" .Values.redis.service.type) -}}
{{- $_ = include "ddc-pme.serviceType" (dict "component" "minio" "type" .Values.minio.service.type) -}}
{{- /* MinIO is internal only: it is never routed through the Ingress. */ -}}
{{- if .Values.minio.ingress.enabled -}}
{{- fail "ddc-pme: minio.ingress.enabled=true is not supported. MinIO is internal (ClusterIP) only and is never exposed through the ingress; use `kubectl port-forward` for the console." -}}
{{- end -}}
{{- /* Persistent volumes: the storage class must be named explicitly (never the cluster default). */ -}}
{{- if .Values.redis.enabled -}}
{{- $_ = include "ddc-pme.assertPersistence" (dict "component" "redis" "persistence" .Values.redis.persistence) -}}
{{- end -}}
{{- if .Values.minio.enabled -}}
{{- $_ = include "ddc-pme.assertPersistence" (dict "component" "minio" "persistence" .Values.minio.persistence) -}}
{{- end -}}
{{- /* Images: tag required for every enabled component (and never 'latest'). */ -}}
{{- $_ = include "ddc-pme.image" (dict "root" . "image" .Values.nginx.image "component" "nginx") -}}
{{- $_ = include "ddc-pme.image" (dict "root" . "image" .Values.api.image "component" "api") -}}
{{- if .Values.worker.enabled -}}
{{- $_ = include "ddc-pme.image" (dict "root" . "image" .Values.worker.image "component" "worker") -}}
{{- end -}}
{{- if .Values.migrate.enabled -}}
{{- $_ = include "ddc-pme.image" (dict "root" . "image" .Values.migrate.image "component" "migrate") -}}
{{- end -}}
{{- if .Values.redis.enabled -}}
{{- $_ = include "ddc-pme.image" (dict "root" . "image" .Values.redis.image "component" "redis") -}}
{{- end -}}
{{- if .Values.minio.enabled -}}
{{- $_ = include "ddc-pme.image" (dict "root" . "image" .Values.minio.image "component" "minio") -}}
{{- end -}}
{{- /* Ingress. */ -}}
{{- if not (has .Values.ingress.kind (list "Ingress" "IngressRoute")) -}}
{{- fail (printf "ddc-pme: ingress.kind must be 'Ingress' or 'IngressRoute', got '%v'" .Values.ingress.kind) -}}
{{- end -}}
{{- /* Settings the API itself refuses to boot with in production (backend env.ts). */ -}}
{{- if eq .Values.app.nodeEnv "production" -}}
{{- if contains "*" (toString .Values.app.corsAllowedOrigins) -}}
{{- fail "ddc-pme: app.corsAllowedOrigins must not contain a wildcard in production (the API refuses to boot)." -}}
{{- end -}}
{{- if ne .Values.app.storageDriver "s3" -}}
{{- fail "ddc-pme: app.storageDriver must be 's3' in production (local disk is lost on redeploy and not shared between replicas)." -}}
{{- end -}}
{{- end -}}
{{- /*
Single sign-on. Every one of these failures is a BROKEN LOGIN, not a cosmetic
issue, and all of them render into a valid-looking release: the derived issuer
is both the API's expected `iss` and the browser's redirect target, so a wrong
value yields 401 on every authenticated call and no obvious symptom at deploy
time. Fail the render instead.
*/ -}}
{{- $kc := .Values.keycloak -}}
{{- $issuer := include "ddc-pme.oidcIssuer" . -}}
{{- $issUrl := urlParse $issuer -}}
{{- if ne $issUrl.scheme "https" -}}
{{- fail (printf "ddc-pme: the OIDC issuer must be an https:// URL, got %q. The browser sends users to it and the API compares it to the token's iss claim; over http the tokens are interceptable and Keycloak will usually refuse the request. Set keycloak.baseUrl (and keycloak.issuer only when the provider's iss really differs)." $issuer) -}}
{{- end -}}
{{- /* Placeholder hosts pass every URL check but resolve nowhere, which is how a
release ships with sign-in permanently broken. */ -}}
{{- if or (hasSuffix ".invalid" $issUrl.host) (contains "example" $issUrl.host) -}}
{{- fail (printf "ddc-pme: keycloak.baseUrl looks like a placeholder host (%s). Override it with the real public Keycloak origin from the client registration in digin-automation; with a placeholder issuer no user can sign in." $issUrl.host) -}}
{{- end -}}
{{- if not $kc.audience -}}
{{- fail "ddc-pme: keycloak.audience is empty. The API only verifies the `aud` claim when it is configured, so an empty value silently accepts access tokens minted for ANY client in the realm. Set it to the value your audience mapper injects (default: pme-api)." -}}
{{- end -}}
{{- if not $kc.publicClient -}}
{{- fail "ddc-pme: keycloak.publicClient=false is not supported. backendSecretEnv does inject KEYCLOAK_CLIENT_SECRET from the keycloak Secret, but nothing consumes it: the API has no such setting in its env schema, and this SPA authenticates directly against Keycloak with authorization code + PKCE, which has no confidential-client mode - a secret in a browser is not a secret. The API only ever verifies signed tokens. Keep publicClient=true, or implement a real confidential flow (backend-side code exchange) first." -}}
{{- end -}}
{{- /*
The redirect URIs must land on this release's own host, or Keycloak refuses the
login with redirect_uri_mismatch and the user never returns to the app. Compared
by HOST only: the external scheme is decided by pfSense/Traefik, not by this
chart (ingress.tls.enabled=false still serves https in front of pfSense).
*/ -}}
{{- if .Values.ingress.enabled -}}
{{- $want := .Values.ingress.host -}}
{{- range $name, $uri := dict "keycloak.redirectUri" $kc.redirectUri "keycloak.postLogoutRedirectUri" $kc.postLogoutRedirectUri -}}
{{- $u := urlParse $uri -}}
{{- if ne $u.scheme "https" -}}
{{- fail (printf "ddc-pme: %s must be an https:// URL, got %q." $name $uri) -}}
{{- end -}}
{{- if ne $u.host $want -}}
{{- fail (printf "ddc-pme: %s (%s) is not on this release's host %q. Keycloak rejects an unregistered redirect_uri, so sign-in would fail before the user is authenticated. Register it on the Keycloak client (digin-automation realm-client.yml) or align it with ingress.host." $name $uri $want) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- /* Settings that must never be injected into the backend (env.ts refuses them in production). */ -}}
{{- range $list := list .Values.app.extraEnv .Values.api.extraEnv .Values.worker.extraEnv .Values.migrate.extraEnv -}}
{{- range $e := $list -}}
{{- if has $e.name (list "JWT_ACCESS_SECRET" "DEV_AUTH_PASSWORD") -}}
{{- fail (printf "ddc-pme: extraEnv must not set %s (dev-only credential; the API refuses to boot with it in production)." $e.name) -}}
{{- end -}}
{{- if and (eq $e.name "ALLOW_PASSWORD_GRANT") (has (lower (toString $e.value)) (list "1" "true" "yes" "on")) -}}
{{- fail "ddc-pme: extraEnv must not enable ALLOW_PASSWORD_GRANT (the API refuses to boot with it in production)." -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- include "ddc-pme.validateSecrets" . -}}
{{- end -}}

{{/* ----------------------------------------------------------------------- */}}
{{/* Derived connection settings (pure strings, no credentials)              */}}
{{/* ----------------------------------------------------------------------- */}}

{{/*
DATABASE_URL using Kubernetes dependent-variable expansion. $(DB_USERNAME) and
$(DB_PASSWORD) are expanded by the kubelet from env entries (secretKeyRef)
that MUST be declared EARLIER in the same container env list (see
ddc-pme.dbEnv). The credential never appears in a manifest. The password must
already be URL-safe (no @ : / ? # % unescaped) because it is spliced into a URL
as-is.
*/}}
{{- define "ddc-pme.databaseUrl" -}}
{{- $m := .Values.mysql -}}
{{- printf "mysql://$(DB_USERNAME):$(DB_PASSWORD)@%s:%v/%s" $m.host (int64 $m.port) $m.database -}}
{{- with $m.params }}?{{ . }}{{- end -}}
{{- end -}}

{{/* REDIS_URL; $(REDIS_PASSWORD) comes from a secretKeyRef declared earlier. */}}
{{- define "ddc-pme.redisUrl" -}}
{{- if .Values.redis.enabled -}}
{{- printf "redis://:$(REDIS_PASSWORD)@%s:%v" (include "ddc-pme.componentName" (dict "root" . "component" "redis")) .Values.redis.service.port -}}
{{- else -}}
{{- printf "redis://:$(REDIS_PASSWORD)@%s:%v" (required "ddc-pme: redis.external.host is required when redis.enabled=false" .Values.redis.external.host) (.Values.redis.external.port | default 6379) -}}
{{- end -}}
{{- end -}}

{{/* OIDC issuer: exactly the `iss` Keycloak puts in tokens (public base URL). */}}
{{- define "ddc-pme.oidcIssuer" -}}
{{- if .Values.keycloak.issuer -}}
{{- .Values.keycloak.issuer -}}
{{- else -}}
{{- printf "%s/realms/%s" (trimSuffix "/" .Values.keycloak.baseUrl) .Values.keycloak.realm -}}
{{- end -}}
{{- end -}}

{{/* JWKS URI the backend verifies signatures against (public client: no secret needed). */}}
{{- define "ddc-pme.oidcJwksUri" -}}
{{- if .Values.keycloak.jwksUri -}}
{{- .Values.keycloak.jwksUri -}}
{{- else -}}
{{- printf "%s/protocol/openid-connect/certs" (include "ddc-pme.oidcIssuer" .) -}}
{{- end -}}
{{- end -}}

{{/* S3 endpoint the API/worker use. In-cluster MinIO unless an external endpoint is set. */}}
{{- define "ddc-pme.s3Endpoint" -}}
{{- if .Values.minio.enabled -}}
{{- printf "http://%s:%v" (include "ddc-pme.componentName" (dict "root" . "component" "minio")) .Values.minio.service.apiPort -}}
{{- else -}}
{{- .Values.minio.externalEndpoint -}}
{{- end -}}
{{- end -}}

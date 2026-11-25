{{/*
===============================================================================
Backend (api / worker / migrate) shared pieces
===============================================================================
Env contract: backend/src/common/config/env.ts. Credentials are NEVER written
here or in the ConfigMap; Pods read them from the existing Secrets
(existingSecrets.*) through secretKeyRef.

Kubernetes expands $(VAR) in an env `value` only from variables defined EARLIER
in the same `env` list (and never inside envFrom / ConfigMaps). So the
secretKeyRef entries MUST come first and the URLs that use them right after.
*/}}

{{/* ConfigMap name holding the non-secret backend env. */}}
{{- define "ddc-pme.configName" -}}
{{- include "ddc-pme.componentName" (dict "root" . "component" "config") -}}
{{- end -}}

{{/*
Database credentials + DATABASE_URL. Used by api, worker and the migrate Job.
Args: the root context.
*/}}
{{- define "ddc-pme.dbEnv" -}}
- name: DB_USERNAME
  valueFrom:
    secretKeyRef:
      name: {{ .Values.existingSecrets.mysql.name }}
      key: {{ .Values.existingSecrets.mysql.usernameKey }}
- name: DB_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .Values.existingSecrets.mysql.name }}
      key: {{ .Values.existingSecrets.mysql.passwordKey }}
- name: DATABASE_URL
  value: {{ include "ddc-pme.databaseUrl" . | quote }}
{{- end -}}

{{/*
Everything api and worker share that is a credential or built from one:
database, Redis, S3 keys and (confidential client only) the Keycloak secret.
Args: the root context. Order matters, see the header.
*/}}
{{- define "ddc-pme.backendSecretEnv" -}}
{{ include "ddc-pme.dbEnv" . }}
- name: REDIS_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .Values.existingSecrets.redis.name }}
      key: {{ .Values.existingSecrets.redis.passwordKey }}
- name: REDIS_URL
  value: {{ include "ddc-pme.redisUrl" . | quote }}
- name: S3_ACCESS_KEY_ID
  valueFrom:
    secretKeyRef:
      name: {{ .Values.existingSecrets.minio.name }}
      key: {{ .Values.existingSecrets.minio.accessKeyKey }}
- name: S3_SECRET_ACCESS_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Values.existingSecrets.minio.name }}
      key: {{ .Values.existingSecrets.minio.secretKeyKey }}
{{- if not .Values.keycloak.publicClient }}
- name: KEYCLOAK_CLIENT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ .Values.existingSecrets.keycloak.name }}
      key: {{ .Values.existingSecrets.keycloak.clientSecretKey }}
{{- end }}
{{- end -}}

{{/* User supplied env, rendered as list items (nothing when empty). Args: a list. */}}
{{- define "ddc-pme.extraEnv" -}}
{{- range . }}
- {{ toYaml . | nindent 2 | trim }}
{{- end }}
{{- end -}}

{{/*
CORS origins: app.corsAllowedOrigins, else https://<ingress.host>.
*/}}
{{- define "ddc-pme.corsOrigins" -}}
{{- if .Values.app.corsAllowedOrigins -}}
{{- .Values.app.corsAllowedOrigins -}}
{{- else -}}
{{- printf "https://%s" (required "ddc-pme: ingress.host is required (CORS_ALLOWED_ORIGINS is derived from it)" .Values.ingress.host) -}}
{{- end -}}
{{- end -}}

{{/*
Backend Pod scheduling: nodeSelector, tolerations and affinity (same inputs as
the shared ddc-pme.scheduling block, plus the generated scheduling.podAntiAffinity
rule that keeps replicas of one component on different nodes) and topology spread.
Args: dict "root" . "component" "api". Spread constraints without a
labelSelector get this component's selector labels.
*/}}
{{- define "ddc-pme.backendScheduling" -}}
{{- $sch := .root.Values.scheduling -}}
{{- $aa := $sch.podAntiAffinity | default dict -}}
{{- with $sch.nodeSelector }}
nodeSelector:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with $sch.tolerations }}
tolerations:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- /* scheduling.affinity wins: a hand-written podAntiAffinity there disables the generated one. */ -}}
{{- $affinity := deepCopy ($sch.affinity | default dict) }}
{{- if and $aa.enabled (not (hasKey $affinity "podAntiAffinity")) }}
{{- $term := dict "topologyKey" ($aa.topologyKey | default "kubernetes.io/hostname") "labelSelector" (dict "matchLabels" (include "ddc-pme.componentSelectorLabels" (dict "root" .root "component" .component) | fromYaml)) }}
{{- if eq ($aa.mode | default "preferred") "required" }}
{{- $_ := set $affinity "podAntiAffinity" (dict "requiredDuringSchedulingIgnoredDuringExecution" (list $term)) }}
{{- else }}
{{- $_ := set $affinity "podAntiAffinity" (dict "preferredDuringSchedulingIgnoredDuringExecution" (list (dict "weight" 100 "podAffinityTerm" $term))) }}
{{- end }}
{{- end }}
{{- with $affinity }}
affinity:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- if $sch.topologySpreadConstraints }}
topologySpreadConstraints:
{{- range $sch.topologySpreadConstraints }}
{{- $c := deepCopy . }}
{{- if not (hasKey $c "labelSelector") }}
{{- $_ := set $c "labelSelector" (dict "matchLabels" (include "ddc-pme.componentSelectorLabels" (dict "root" $.root "component" $.component) | fromYaml)) }}
{{- end }}
- {{ toYaml $c | nindent 2 | trim }}
{{- end }}
{{- end }}
{{- end -}}

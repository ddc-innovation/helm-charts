{{- define "postfix-relay.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "postfix-relay.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := include "postfix-relay.name" . }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "postfix-relay.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | quote }}
app.kubernetes.io/name: {{ include "postfix-relay.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: app-mail
{{- end }}

{{- define "postfix-relay.selectorLabels" -}}
app.kubernetes.io/name: {{ include "postfix-relay.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "postfix-relay.image" -}}
{{- $digest := required "image.digest must pin an immutable sha256 digest" .Values.image.digest -}}
{{- printf "%s@%s" .Values.image.repository $digest -}}
{{- end }}



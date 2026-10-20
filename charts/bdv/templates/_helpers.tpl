{{- define "bdv.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 28 | trimSuffix "-" -}}
{{- end -}}
{{- define "bdv.memoryBudget" -}}
{{- $memory := toString .memory -}}
{{- if not (regexMatch "^[1-9][0-9]*(Mi|Gi)$" $memory) }}{{ fail "Managed Trino memory limits must use integer Mi or Gi quantities" }}{{ end -}}
{{- $number := int64 (regexFind "^[0-9]+" $memory) -}}
{{- $unit := int64 1048576 -}}
{{- if hasSuffix "Gi" $memory }}{{ $unit = int64 1073741824 }}{{ end -}}
{{- div (mul $number $unit (int64 .percent)) 100 -}}
{{- end -}}
{{- define "bdv.fullname" -}}
{{- $name := include "bdv.name" . -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 45 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 45 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- define "bdv.labels" -}}
app.kubernetes.io/name: {{ include "bdv.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}
{{- define "bdv.image" -}}
{{- if .digest -}}
{{- printf "%s@%s" .repository .digest -}}
{{- else -}}
{{- printf "%s:%s" .repository .tag -}}
{{- end -}}
{{- end -}}

{{- define "bdv.assertImage" -}}
{{- $i := .image -}}
{{- if not $i.repository -}}{{- fail (printf "%s.repository is required" .name) -}}{{- end -}}
{{- if $i.digest -}}
{{- if not (regexMatch "^sha256:[0-9a-f]{64}$" $i.digest) -}}{{- fail (printf "%s.digest must be SHA256" .name) -}}{{- end -}}
{{- else -}}
{{- if or (not $i.tag) (eq $i.tag "latest") -}}{{- fail (printf "%s needs a digest or explicit non-latest tag" .name) -}}{{- end -}}
{{- end -}}
{{- end -}}

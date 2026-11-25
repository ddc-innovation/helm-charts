{{/*
===============================================================================
nginx (SPA + /api/v1 reverse proxy) shared pieces
===============================================================================
*/}}

{{/* ConfigMap name: <fullname>-nginx-config */}}
{{- define "ddc-pme.nginxConfigName" -}}
{{- include "ddc-pme.componentName" (dict "root" . "component" "nginx-config") -}}
{{- end -}}

{{/*
In-cluster URL of the API Service that location /api/v1/ proxies to:
http://<fullname>-api.<namespace>.svc.<clusterDomain>:<port>
It is a fully qualified name on purpose: nginx's resolver does not apply the
Pod's DNS search list. No URI part (see the comment in nginx-configmap.yaml).
*/}}
{{- define "ddc-pme.apiUpstream" -}}
{{- printf "http://%s.%s.svc.%s:%v" (include "ddc-pme.componentName" (dict "root" . "component" "api")) .Release.Namespace .Values.nginx.clusterDomain (int64 .Values.api.service.port) -}}
{{- end -}}

{{/*
Origin (scheme://host[:port]) of the OIDC issuer. The SPA fetches the discovery
document and exchanges the code for tokens from the browser, so the CSP
`connect-src` must allow it.
*/}}
{{- define "ddc-pme.oidcOrigin" -}}
{{- $u := urlParse (include "ddc-pme.oidcIssuer" .) -}}
{{- printf "%s://%s" $u.scheme $u.host -}}
{{- end -}}

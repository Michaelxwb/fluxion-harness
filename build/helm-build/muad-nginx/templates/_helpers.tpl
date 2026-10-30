{{/*
Common chart labels.
*/}}
{{- define "muad-platform.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
app.kubernetes.io/part-of: muad
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Resolve one component image. Call with (dict "root" $ "service" .Values.console).
*/}}
{{- define "muad-platform.image" -}}
{{- $root := .root -}}
{{- $service := .service -}}
{{- printf "%s/%s:%s" $root.Values.global.imageRegistry $service.image (default $root.Values.global.imageTag $service.tag) -}}
{{- end }}

{{/*
Common pod security context (matches the images' UID/GID 10001).
*/}}
{{- define "muad-platform.podSecurityContext" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
fsGroup: 10001
{{- end }}

{{/*
Render imagePullSecrets when configured.
*/}}
{{- define "muad-platform.imagePullSecrets" -}}
{{- with .Values.global.imagePullSecrets }}
imagePullSecrets:
{{- toYaml . | nindent 2 }}
{{- end }}
{{- end }}

{{/*
Effective node selector: service overrides global.
*/}}
{{- define "muad-platform.nodeSelector" -}}
{{- $selector := .Values.service.nodeSelector | default .Values.global.nodeSelector -}}
{{- with $selector }}
nodeSelector:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end }}

{{/*
Effective tolerations: service overrides global.
*/}}
{{- define "muad-platform.tolerations" -}}
{{- $tolerations := .Values.service.tolerations | default .Values.global.tolerations -}}
{{- with $tolerations }}
tolerations:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end }}

{{/*
Effective affinity: service overrides global.
*/}}
{{- define "muad-platform.affinity" -}}
{{- $affinity := .Values.service.affinity | default .Values.global.affinity -}}
{{- with $affinity }}
affinity:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end }}

{{/*
Service account name: created one, explicit name, or empty (default SA).
*/}}
{{- define "muad-platform.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- .Values.serviceAccount.name | default .Chart.Name -}}
{{- else -}}
{{- .Values.serviceAccount.name -}}
{{- end -}}
{{- end }}

{{/*
Effective extra envFrom: global items followed by service items.
*/}}
{{- define "muad-platform.extraEnvFrom" -}}
{{- $items := concat (.Values.global.extraEnvFrom | default (list)) (.Values.service.extraEnvFrom | default (list)) -}}
{{- with $items }}
{{- toYaml . -}}
{{- end }}
{{- end }}

{{/*
Effective sysctls: service overrides global.
*/}}
{{- define "muad-platform.sysctls" -}}
{{- $sysctls := .Values.service.sysctls | default .Values.global.sysctls -}}
{{- with $sysctls }}
sysctls:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end }}

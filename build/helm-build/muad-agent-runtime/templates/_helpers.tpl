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

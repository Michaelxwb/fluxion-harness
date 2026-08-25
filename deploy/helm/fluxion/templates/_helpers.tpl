{{/* Fluxion 命名辅助函数 */}}

{{- define "fluxion.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "fluxion.fullname" -}}
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

{{- define "fluxion.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
app.kubernetes.io/name: {{ include "fluxion.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "fluxion.selectorLabels" -}}
app.kubernetes.io/name: {{ include "fluxion.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/*
数据库 DSN 拼接：
  1. databaseUrl 显式指定时优先；
  2. 否则 postgresql.enabled 时用子 chart 的账号密码与服务名自动拼 postgresql+asyncpg DSN；
  3. 否则回退到 externalDatabase.url。
子 chart 主服务名固定为 <release>-postgresql，端口默认 5432。
*/}}
{{- define "fluxion.databaseUrl" -}}
{{- if .Values.databaseUrl -}}
{{- .Values.databaseUrl -}}
{{- else if .Values.postgresql.enabled -}}
{{- $port := default 5432 .Values.postgresql.primary.service.ports.postgresql -}}
{{- printf "postgresql+asyncpg://%s:%s@%s-postgresql:%v/%s"
    .Values.postgresql.auth.username
    .Values.postgresql.auth.password
    .Release.Name
    $port
    .Values.postgresql.auth.database -}}
{{- else -}}
{{- .Values.externalDatabase.url -}}
{{- end -}}
{{- end -}}

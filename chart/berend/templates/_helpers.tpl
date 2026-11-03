{{- define "berend.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}{{ .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else -}}{{ printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" }}{{- end -}}
{{- end -}}

{{- define "berend.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "berend.selector" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "berend.env" -}}
envFrom:
  - secretRef:
      name: {{ .Values.existingSecret }}
env:
  - {name: CHROMA_DIR, value: /data/chroma}
  - {name: ARGOCD_STATE_FILE, value: /data/argocd_state.json}
  - {name: FEEDBACK_LOG, value: /data/feedback.jsonl}
  - {name: HEALTH_PORT, value: {{ .Values.service.port | quote }}}
  {{- if .Values.caBundleConfigMap }}
  - {name: REQUESTS_CA_BUNDLE, value: /etc/berend/ca/ca-bundle.crt}
  {{- end }}
  {{- range $k, $v := .Values.env }}
  - {name: {{ $k }}, value: {{ $v | quote }}}
  {{- end }}
volumeMounts:
  - {name: data, mountPath: /data}
  {{- if .Values.caBundleConfigMap }}
  - {name: ca, mountPath: /etc/berend/ca, readOnly: true}
  {{- end }}
{{- end -}}

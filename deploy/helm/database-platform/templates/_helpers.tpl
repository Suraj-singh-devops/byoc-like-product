{{- define "dp.labels" -}}
app.kubernetes.io/part-of: database-platform
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/instance: {{ .Release.Name }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "dp.selector" -}}
app.kubernetes.io/part-of: database-platform
app.kubernetes.io/component: {{ . }}
{{- end -}}

{{/* Restricted Pod Security: non-root, no privilege escalation, no capabilities, read-only root. */}}
{{- define "dp.podSecurity" -}}
runAsNonRoot: true
runAsUser: {{ . }}
runAsGroup: {{ . }}
fsGroup: {{ . }}
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{- define "dp.containerSecurity" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: ["ALL"]
{{- end -}}

{{- define "dp.backendImage" -}}
{{ .Values.image.backend.repository }}:{{ .Values.image.backend.tag }}
{{- end -}}

{{/* Settings shared by every backend process. Arguments: dict "root" $ "role" <SERVICE_ROLE> "ksa" <ServiceAccount>. */}}
{{- define "dp.backendEnv" -}}
{{- $v := .root.Values -}}
- name: SERVICE_ROLE
  value: {{ .role | quote }}
- name: MOCK_MODE
  value: {{ $v.settings.mockMode | quote }}
- name: ENVIRONMENT
  value: {{ $v.settings.environment | quote }}
- name: SEED_DEMO_DATA
  value: {{ $v.settings.seedDemoData | quote }}
{{- if $v.settings.seedDemoData }}
- name: DEMO_PASSWORD
  value: {{ required "settings.demoPassword is required with seedDemoData" $v.settings.demoPassword | quote }}
{{- end }}
- name: ELASTICSEARCH_VERSION
  value: {{ $v.settings.elasticsearchVersion | quote }}
- name: COOKIE_SECURE
  value: {{ $v.settings.cookieSecure | quote }}
- name: LOG_LEVEL
  value: {{ $v.settings.logLevel | quote }}
- name: LOG_FORMAT
  value: json
- name: RUN_MIGRATIONS
  value: "false"
- name: REDIS_URL
  value: redis://redis:6379/0
- name: REDIS_PASSWORD_FILE
  value: /var/run/secrets/byoc/redis-password
- name: SECRET_KEY_FILE
  value: /var/run/secrets/byoc/secret-key
{{- if eq $v.database.mode "cloudsql" }}
- name: DATABASE_URL
  value: {{ printf "postgresql+psycopg://%s@127.0.0.1:5432/%s" (index $v.database.users .ksa | required (printf "database.users.%s is required" .ksa) | urlquery) $v.database.name | quote }}
{{- else }}
- name: DATABASE_URL_FILE
  value: /var/run/secrets/byoc-database/url
{{- end }}
{{- end -}}

{{- define "dp.secretVolumes" -}}
- name: platform-secrets
{{- if eq .Values.secrets.source "secretManager" }}
  csi:
    driver: secrets-store-gke.csi.k8s.io
    readOnly: true
    volumeAttributes:
      secretProviderClass: byoc-platform
{{- else }}
  secret:
    secretName: {{ .Values.secrets.kubernetesSecret }}
    defaultMode: 0440
{{- end }}
{{- if ne .Values.database.mode "cloudsql" }}
- name: database-url
  secret:
    secretName: {{ .Values.database.urlSecret.name }}
    defaultMode: 0440
    items:
      - key: {{ .Values.database.urlSecret.key }}
        path: url
{{- end }}
- name: tmp
  emptyDir: {}
{{- end -}}

{{- define "dp.secretMounts" -}}
- name: platform-secrets
  mountPath: /var/run/secrets/byoc
  readOnly: true
{{- if ne .Values.database.mode "cloudsql" }}
- name: database-url
  mountPath: /var/run/secrets/byoc-database
  readOnly: true
{{- end }}
- name: tmp
  mountPath: /tmp
{{- end -}}

{{/* Cloud SQL Auth Proxy as a native sidecar: private IP, IAM login, listens on localhost only. */}}
{{- define "dp.sqlProxy" -}}
{{- if eq .Values.database.mode "cloudsql" }}
initContainers:
  - name: cloud-sql-proxy
    image: {{ .Values.database.proxyImage }}
    restartPolicy: Always
    args:
      - --private-ip
      - --auto-iam-authn
      - --structured-logs
      - --port=5432
      - --address=127.0.0.1
      - --health-check
      - --http-address=0.0.0.0
      - {{ required "database.instanceConnectionName is required with cloudsql" .Values.database.instanceConnectionName }}
    startupProbe:
      httpGet: {path: /startup, port: 9090}
      periodSeconds: 1
      failureThreshold: 60
    securityContext:
      {{- include "dp.containerSecurity" . | nindent 6 }}
    resources:
      {{- toYaml .Values.resources.proxy | nindent 6 }}
{{- end }}
{{- end -}}

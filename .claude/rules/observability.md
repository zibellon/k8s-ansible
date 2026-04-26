# Observability — Prometheus Operator, ServiceMonitor, Grafana, Alertmanager

Depth reference for the observability layer. Four components coordinate to provide metrics, dashboards, and alerting: `mon-prometheus-operator`, `mon-kube-state-metrics`, `mon-node-exporter`, `mon-grafana`.

For the big picture, see `CLAUDE.md` §1 (mental model, L7 Observability layer). For per-component install detail (namespaces, charts, dependencies, ESO integration), see [`components.md`](components.md) §17–§22.

---

## 1. Prometheus Operator

Lives in namespace `mon` (value of `prometheus_operator_namespace`). Installed by `playbook-app/mon-prometheus-operator-install.yaml`.

### 1.1 Install phases

Unlike the standard 3-phase pattern, the Prometheus Operator install has **extra phases** because CRDs must be applied separately and the Custom Resources (CRs) that the operator reconciles need to be declared after the operator is ready:

```
crds/         CRDs first (Helm timeout on hook-crd avoidance)
pre/          NetworkPolicies + ESO (if any)
install/      the operator itself
prometheus/   Prometheus CR (retention, storage, selectors)
alertmanager/ Alertmanager CR
post/         Ingress for Prometheus / Alertmanager UIs
```

Each is a separate Helm release — `mon-prometheus-operator-crds`, `mon-prometheus-operator-pre`, `mon-prometheus-operator`, `mon-prometheus-operator-prometheus`, `mon-prometheus-operator-alertmanager`, `mon-prometheus-operator-post`. See [`playbook-conventions.md`](playbook-conventions.md) §6 on the 3-phase pattern and extras.

### 1.2 Prometheus storage

- PVC on Longhorn storage class `lh-major-single-best-effort` (default)
- Retention: 60 days (default)
- Configurable via `prometheus_operator_*` variables in `hosts-vars/mon-prometheus-operator.yaml`

### 1.3 ServiceMonitor discovery scope

**Cluster-wide.** The Prometheus Operator's `ServiceMonitor` selector has no namespace restriction — it discovers any `ServiceMonitor` CR in any namespace. This lets each component own its own `ServiceMonitor` (in its own namespace, `post/` phase) without needing to touch the operator's config.

---

## 2. Per-component ServiceMonitor

### 2.1 Location in the 3-phase pattern

Every component's `ServiceMonitor` lives in its `post/` phase chart — applied only AFTER the workload's Service exists (otherwise the SM points at nothing). This is a core reason why `post/` exists as a separate phase (see [`playbook-conventions.md`](playbook-conventions.md) §6).

### 2.2 Variables

Per-component variables (see [`variables.md`](variables.md) §1.3):

| Variable | Purpose |
|---|---|
| `<c>_service_monitor_enabled` | Gate — default `true` where supported |
| `<c>_service_monitor_interval` | Scrape interval (e.g., `30s`) |
| `<c>_service_monitor_scrape_timeout` | Scrape timeout |
| `<c>_service_monitor_additional_labels` / `_labels` | Labels for Prometheus operator selector matching (name varies by component — grep before adding) |

### 2.3 Standard `ServiceMonitor` template shape

```yaml
{% if <c>_service_monitor_enabled %}
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: {{ .Values.component }}
  namespace: {{ .Values.namespace }}
  labels:
    {{ <c>_service_monitor_additional_labels | to_json }}
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: {{ .Values.component }}
  endpoints:
    - port: metrics
      interval: {{ <c>_service_monitor_interval }}
      scrapeTimeout: {{ <c>_service_monitor_scrape_timeout }}
{% endif %}
```

### 2.4 Dedicated exporter components

`kube-state-metrics` and `node-exporter` are separate components (in namespace `mon`), each with its own install playbook and `ServiceMonitor` in its `post/` phase. See [`components.md`](components.md) §19, §20.

`node-exporter` is a **DaemonSet** with `tolerations: [{operator: "Exists"}]` so it lands on every node including managers (which are usually tainted).

---

## 3. Grafana

Lives in its own namespace `grafana` (value of `grafana_namespace`). Installed by `playbook-app/mon-grafana-install.yaml`.

### 3.1 ESO integration

Grafana is one of the 8 ESO-integrated components (see [`secrets-and-eso.md`](secrets-and-eso.md) §1 and §9):

| Secret | Path in Vault | Used for |
|---|---|---|
| Admin password | `eso-secret/grafana/admin` | Login for the `admin` user |
| OIDC client-secret | `eso-secret/grafana/oidc` | Zitadel OIDC single sign-on |
| Datasource credentials | `eso-secret/grafana/<ds-name>` | Auth to Prometheus / Loki / etc. |

Credentials render into Grafana's provisioning ConfigMaps via `ExternalSecret` → K8s `Secret` → mounted-in `grafana.ini` / datasource YAML.

### 3.2 Dashboards

Dashboards are provisioned declaratively via Helm values (in `hosts-vars/mon-grafana.yaml`). User-added dashboards go in `grafana_dashboards_extra` to preserve the `*_extra` concat-merge contract (see [`variables.md`](variables.md) §1.5).

---

## 4. Alertmanager

The Alertmanager Custom Resource lives with the Prometheus Operator chart (phase `alertmanager/`). Routing rules are defined declaratively in that chart's values.

### 4.1 Phase separation

Keeping Alertmanager in its own chart phase (not folded into `install/`) gives two benefits:

- Operator can be upgraded without re-applying Alertmanager config.
- Alertmanager config can be edited + redeployed without touching the operator.

### 4.2 Routing & receivers

Config structure in `hosts-vars/mon-prometheus-operator.yaml`:

- `alertmanager_config.route` — the root route, with sub-routes matching on labels
- `alertmanager_config.receivers` — Slack / email / PagerDuty / webhook destinations
- `alertmanager_config.inhibit_rules` — suppression rules to avoid alert storms

Secret fields (webhook URLs, Slack tokens, etc.) should come from Vault via ESO — not hardcoded in values.

---

## 5. Dependency order

Observability components install **after** the platform and applications they monitor:

```
L1 control plane → L2 Cilium → L4 Longhorn → L5 Vault+ESO → L7 Prometheus Operator →
  mon-node-exporter, mon-kube-state-metrics → Grafana (needs Zitadel for OIDC)
```

See [`components.md`](components.md) §24 for the full dependency tier listing.

---

## 6. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| No metrics from component X | `ServiceMonitor` missing, wrong labels, or in `install/` instead of `post/` | Check `<c>_service_monitor_enabled`, verify SM exists in cluster after `post` phase |
| Prometheus OOM / disk pressure | Retention too high for available storage | Reduce `prometheus_retention`, reduce scrape interval, or add PVC capacity |
| Grafana OIDC login fails | Zitadel not installed yet, or OIDC client-secret not in Vault | Verify install order, run `eso-force-sync.yaml` for `grafana` namespace |
| Alertmanager routes match nothing | Label mismatch between PrometheusRule and `alertmanager_config.route` matchers | `kubectl -n mon get prometheusrules -o yaml` to see actual labels, align matchers |
| `kubectl -n mon get servicemonitors -A` shows SM but no scrape | Prometheus can't reach the service (NetworkPolicy?) or port name mismatch | Inspect Prometheus targets UI (`/targets`), usually NetworkPolicy + port name |

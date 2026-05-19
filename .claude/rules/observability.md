# Observability — Prometheus Operator, ServiceMonitor, Grafana, Alertmanager

Depth reference for the observability layer. After consolidation (SUB-1..10), eight workloads — Prometheus Operator + Prometheus + Alertmanager + Grafana + Loki + Vector + node-exporter + kube-state-metrics — share namespace `mon-system`, one inventory file, one chart tree, and one install playbook (`mon-system-install.yaml`).

For per-component install detail (chart subdirs, helm release names, enable flags, dependencies), see [`components.md`](components.md) §17. For the big picture, see `CLAUDE.md` §1 (mental model, L7 Observability layer).

---

## 1. Prometheus Operator

Lives in `mon-system` namespace. Installed via the `prometheus-operator` tag of `mon-system-install.yaml`. Customization (resources, tolerations, nodeSelector) is expressed declaratively через `mon_system_prometheus_operator_kustomize_patches` — pristine upstream `templates/prometheus-operator.yaml` не модифицируется. Namespace handled by `helm template --namespace` при render'е chart templates. См. [`playbook-conventions.md`](playbook-conventions.md) §21 для unified helm template + kustomize паттерна и [`components.md`](components.md) §17 для контекста mon-system stack.

### 1.1 Install phases

The mon-system playbook has 11 tags. Five are dedicated to the Prometheus Operator stack and depend on each other:

```
crds                  CustomResourceDefinitions (kubectl create -f, not Helm)
prometheus-operator   the operator workload (Deployment + RBAC + Service)
prometheus            Prometheus CR + RBAC + Service
alertmanager          Alertmanager CR + AlertmanagerConfig + Service
post                  Ingress + Certificate for Prometheus / Alertmanager UIs
                      + ServiceMonitor + system-services + system-service-monitors
```

Composite gates: `prometheus` and `alertmanager` are skipped if `mon_system_prometheus_operator_enabled: false`, regardless of their own flags. The `crds` phase is also gated by `mon_system_prometheus_operator_enabled`.

Each Helm release follows the `mon-system-<phase>` naming pattern. The CRDs phase deploys via `kubectl create -f charts/mon-system/crds/crds.yaml` — the 74771-line CRD bundle would otherwise hit Helm timeouts and complicate `--skip-crds` semantics.

### 1.2 Prometheus storage

- PVC on Longhorn storage class `lh-major-single-best-effort` (default)
- Retention: 60 days (default)
- Configurable via `mon_system_prometheus_spec` block scalar in `hosts-vars/mon-system.yaml` — the entire Prometheus CRD `spec` is passed verbatim from inventory.

### 1.3 ServiceMonitor discovery scope

**Cluster-wide.** The Prometheus Operator's `ServiceMonitor` selector has no namespace restriction (empty `serviceMonitorNamespaceSelector: {}`) — it discovers any `ServiceMonitor` CR in any namespace. This lets external components own their own SM resources without touching mon-system config.

---

## 2. ServiceMonitors

### 2.1 Location in the chart tree

All ServiceMonitors for mon-system workloads live in `mon-system/post/`, not in their respective install-phase charts. This is a deliberate departure from the older per-component pattern: post is "all observability config" — Ingress + Certificate + ServiceMonitor + system-* — gated per workload.

The post chart contains:

- `servicemonitor-loki.yaml` — gated by `loki.enabled`
- `servicemonitor-ksm.yaml` — gated by `ksm.enabled`
- `servicemonitor-node-exporter.yaml` — gated by `node_exporter.enabled`
- `system-service-monitors.yaml` — always rendered, iterates `mon_system_prometheus_system_service_monitors` list (kube-apiserver, kubelet, kube-controller-manager, kube-scheduler, etcd, coredns)
- `system-services.yaml` — always rendered, headless Services in `kube-system` so the system SMs above can find static-pod endpoints

Vector by design has no SM (no metrics endpoint). Grafana and Prometheus-Operator self-SMs are not currently shipped.

### 2.2 Per-component scrape config

Each per-component SM template uses scoped values: `.Values.<c>.serviceMonitor.{interval,scrapeTimeout,labels}`. Defaults all `30s` interval and `15s` timeout.

The system list (apiserver/kubelet/etc.) lives entirely in inventory as a list of dicts — adding a new system target is just appending to `mon_system_prometheus_system_service_monitors` (with matching headless Service in `mon_system_prometheus_system_services` if needed).

---

## 3. Grafana

Lives in `mon-system` namespace. Installed via the `grafana` tag of `mon-system-install.yaml`.

### 3.1 ESO integration

Grafana is the only ESO consumer in mon-system. The integration object is namespace-scoped:

```yaml
eso_vault_integration_mon_system:
  sa_name: "eso-main"
  role_name: "mon-system.eso-main"
  secret_store_name: "eso-main.vault"
  kv_engine_path: "eso-secret"
  is_need_eso: true
```

The grafana phase of `mon-system-install.yaml` runs the full Vault/ESO lifecycle before the Helm install: lookup ExternalSecret → vault-get current password → generate-if-missing → vault-put → eso-force-sync → wait-secret. The Grafana Deployment then mounts the K8s Secret `eso-mon-system-grafana-admin-creds` (rendered by the ExternalSecret in `mon-system/pre/`) for `GF_SECURITY_ADMIN_USER` + `GF_SECURITY_ADMIN_PASSWORD` env vars.

Vault path for grafana credentials: `eso-secret/mon-system/grafana/admin/creds`. See [`secrets-and-eso.md`](secrets-and-eso.md) §9 for full per-component path table.

### 3.2 Dashboards

Dashboards are provisioned declaratively via Helm values from `hosts-vars/mon-system.yaml`. User-added dashboards extend via the `*_extra` pattern (see [`variables.md`](variables.md) §1.5).

### 3.3 Database backend

Grafana state (dashboards, users, datasources, alerts) lives in a dedicated single-replica PostgreSQL instance deployed by the `grafana-postgresql` tag of `mon-system-install.yaml` (chart `mon-system/grafana-postgresql/`, release `mon-system-grafana-postgresql`, in the same `mon-system` namespace). Grafana itself is stateless — `/var/lib/grafana` is an `emptyDir` mount used only for plugin install cache at startup (`GF_INSTALL_PLUGINS`).

Connection is wired via `GF_DATABASE_*` env vars (type, host, name, user, sslMode, conn pool) sourced from the `mon_system_grafana_helm_values.database` dict in inventory. `GF_DATABASE_PASSWORD` comes via `secretKeyRef` on the ESO-managed K8s Secret `eso-mon-system-grafana-postgresql-creds` (Vault path `eso-secret/mon-system/grafana/postgresql/creds`, fields `username` + `password`).

The `grafana-postgresql` phase has no independent enable flag — it is gated by `mon_system_grafana_enabled` (Postgres is meaningless without Grafana). Install order: `grafana-postgresql` (STEP 10) runs before `grafana` (STEP 11), so the Postgres Service `mon-system-grafana-postgresql.mon-system.svc.cluster.local:5432` is reachable by the time Grafana starts. Intra-namespace traffic is allowed by the consolidated `allow-internal-traffic` NetworkPolicy in `mon-system/pre/`.

---

## 4. Alertmanager

The Alertmanager Custom Resource is deployed by the `alertmanager` tag of `mon-system-install.yaml` — a separate Helm release `mon-system-alertmanager` that is independent from the operator workload release `mon-system-prometheus-operator`.

### 4.1 Phase separation

Keeping Alertmanager in its own chart subdir (`mon-system/alertmanager/`, not folded into the operator install) yields:

- Operator can be upgraded (`--tags prometheus-operator`) without re-applying Alertmanager config.
- Alertmanager config can be edited + redeployed (`--tags alertmanager`) without touching the operator.

### 4.2 Routing & receivers

Config lives in `mon_system_alertmanager_root_config_spec` block scalar in `hosts-vars/mon-system.yaml` — the entire `AlertmanagerConfig.spec` is passed verbatim:

- `route` — root route, with sub-routes matching on labels
- `receivers` — Slack / email / PagerDuty / webhook destinations
- `inhibit_rules` — suppression rules to avoid alert storms

Per-namespace `AlertmanagerConfig` resources are merged as child routes via `spec.alertmanagerConfigSelector: {}` on the Alertmanager CR (cluster-wide discovery).

Secret fields (webhook URLs, Slack tokens, etc.) should come from Vault via ESO — not hardcoded in inventory.

---

## 5. Loki and Vector

`mon-system-loki` (single-binary Deployment + PVC + ConfigMap + Service) and `mon-system-vector` (DaemonSet + RBAC + ConfigMap, no Service) are independent of the Prometheus Operator stack and can be enabled/disabled separately via `mon_system_loki_enabled` / `mon_system_vector_enabled`.

### 5.1 Vector → Loki

Vector ships logs to Loki via in-cluster DNS `http://loki.{{ mon_system_namespace }}.svc.cluster.local:{{ mon_system_loki_port }}` (defined in `mon_system_vector_config_yaml` block scalar). After consolidation this is intra-namespace traffic, covered by the `allow-internal-traffic` baseline NetworkPolicy in `mon-system/pre/` — no dedicated cross-namespace rule needed.

### 5.2 Grafana → Loki

Grafana datasources can point at `http://loki.mon-system.svc.cluster.local:3100`. Like Vector→Loki, this is intra-namespace and needs no extra NetworkPolicy.

---

## 6. Dependency order

```
L0 cilium → L1 cert-manager + external-secrets → L2 longhorn → L3 vault → L4 traefik → L5 mon-system → ...
```

`mon-system` is at L5 (replacing the old L5 mon-prometheus-operator + L6 mon-node-exporter/mon-kube-state-metrics + L8 mon-grafana/mon-loki + L9 mon-vector). See [`components.md`](components.md) §19 for the full dependency tier listing.

---

## 7. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| No metrics from component X | `<c>.enabled: false` so post-phase ServiceMonitor isn't rendered | Set `mon_system_<c>_enabled: true` and re-run `--tags post` |
| Prometheus OOM / disk pressure | Retention too high for available storage | Reduce `retention` in `mon_system_prometheus_spec` block scalar; reduce scrape interval; or grow PVC capacity |
| Grafana OIDC login fails | Zitadel not installed yet, or OIDC client-secret not in Vault | Verify install order; run `eso-force-sync.yaml` targeting `mon-system` namespace |
| Alertmanager routes match nothing | Label mismatch between `PrometheusRule` and `mon_system_alertmanager_root_config_spec.route` matchers | `kubectl -n mon-system get prometheusrules -o yaml` to inspect actual labels, align matchers |
| `kubectl -n mon-system get servicemonitors` shows SM but no scrape | Prometheus can't reach the Service (NetworkPolicy?) or port name mismatch | Inspect Prometheus targets UI (`/targets`); verify the Service exists with the expected selector and port name |
| `--tags prometheus` skipped even though `mon_system_prometheus_enabled: true` | Operator gate is off — composite condition requires both flags | Set `mon_system_prometheus_operator_enabled: true` |
| `kubectl create -f .../crds.yaml` returns AlreadyExists on re-run | Expected (idempotency) — `failed_when: false` in playbook | No action — CRDs are cluster-scoped, second create is harmless |

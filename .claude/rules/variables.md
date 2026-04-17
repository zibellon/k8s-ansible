# Variables — Patterns & Global Catalog

Two tiers:

- **Tier 1 — Patterns.** Naming conventions that every component follows. Learn these once; infer 90% of variable names.
- **Tier 2 — Global catalog.** Variables that cross component boundaries: cluster-wide knobs, shared primitives, paths.

**Per-component variables are NOT listed here.** See [`components.md`](components.md) for per-component reference.

---

## Tier 1 — Patterns

### 1.1 Per-component suffix convention

Every component `<c>` defines a subset of the following. Not all suffixes are present on every component — use this as a recognition pattern when reading chart values.

| Suffix | Purpose | Type / example |
|---|---|---|
| `_namespace` | K8s namespace target | `argocd_namespace: "argocd"` |
| `_version` | App/image version | `vault_version: "1.21.2"` |
| `_chart_version` | Helm chart version (for external charts) | `traefik_chart_version: "38.0.2"` |
| `_image_registry`, `_image_repository`, `_image_tag` | Image coordinates; override registry for air-gap | `gitlab_image_registry: "registry.gitlab.com"` |
| `_replica_count` | Replica count on Deployments | `int` |
| `_tolerations`, `_node_selector`, `_affinity` | Scheduling | `list` / `dict` / `dict` |
| `_resources` | CPU/memory requests & limits | `dict` |
| `_helm_timeout` | `helm --timeout` value | `"5m"` |
| `_rollout_timeout` | `kubectl rollout status --timeout` | `"120s"` |
| `_daemonset_rollout_timeout` | Same, for DaemonSet workloads | `"180s"` |
| `_helm_values` | Full inline values dict (often large) | `dict` |

### 1.2 Ingress & TLS suffixes

| Suffix | Purpose |
|---|---|
| `_domain`, `_ui_domain`, `_rpc_domain` | FQDN for ingress |
| `_https_secret_name` | `tls.secretName` on the Ingress / IngressRoute |
| `_cluster_issuer_name` | cert-manager `ClusterIssuer` name |
| `_ingress_class_name` | Traefik ingress class (usually `traefik-lb`) |
| `_http_enable`, `_https_enable` | Toggle plain-HTTP ingress |
| `_vpn_only_enabled` | If true, attach Traefik `vpn-only` middleware |
| `_acme_cluster_issuer`, `_acme_solver`, `_acme_solver_pod_labels` | **Output facts** set dynamically by `tasks-resolve-acme-solver.yaml` — never set manually in vars files |

### 1.3 ServiceMonitor suffixes

| Suffix | Purpose |
|---|---|
| `_service_monitor_enabled` | Gate (default `true` where supported) |
| `_service_monitor_interval` | Scrape interval |
| `_service_monitor_scrape_timeout` | Scrape timeout |
| `_service_monitor_additional_labels` / `_service_monitor_labels` | Additional labels (name varies by component — `grep` before adding) |

### 1.4 ESO integration object

Every ESO-integrated component has an `eso_vault_integration_<c>` object with this shape:

```yaml
eso_vault_integration_<c>:
  sa_name: "<c>-eso-sa"
  role_name: "<c>-eso-role"
  secret_store_name: "<c>-eso-secret-store"
  kv_engine_path: "eso-secret"
  is_need_eso: true
```

Plus `eso_vault_integration_<c>_secrets` (base) and `eso_vault_integration_<c>_secrets_extra` (override layer). At runtime `tasks-eso-merge.yaml` produces `eso_vault_integration_<c>_secrets_merged = base + extra`. See [`secrets-and-eso.md`](secrets-and-eso.md) for the full schema.

### 1.5 The `*_extra` concat-merge pattern

All array variables that should be user-extendable follow this contract:

- **Base** lives in `hosts-vars/<file>.yaml`. Shipped with the project.
- **Extension** lives in `hosts-vars-override/<file>.yaml` under the `_extra` suffix. Secret and environment-specific.
- **Merge** happens at runtime: `<name>_final = <name> + (<name>_extra | default([]))`.

Non-`_extra` arrays in overrides **replace** (not concatenate) the base. If you want concat semantics, add `_extra`.

Known `_extra` names:

```
vault_policies_extra
vault_roles_extra
eso_vault_integration_<c>_secrets_extra    # for each of the 9 ESO-integrated components
argocd_cm_extra
argocd_cm_cmd_params_extra
argocd_cm_gpg_keys_extra
argocd_cm_notifications_extra
argocd_cm_rbac_extra
argocd_cm_ssh_known_hosts_extra
argocd_cm_tls_certs_extra
teleport_configure_<resource>_extra        # roles, users, bots, apps, databases, oidc, saml, access-lists, trusted-clusters, ...
```

See `hosts-extra.example.yaml` in the repo root for the full up-to-date template.

### 1.6 Inventory precedence

1. `hosts-vars/` (committed defaults) — lowest.
2. `hosts-vars-override/` (gitignored, real values) — overrides defaults.
3. Inline `vars:` in a play — highest.

Always invoke with both dirs: `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ ...`. See `CLAUDE.md` §7.3.

### 1.7 Rendering patterns

```yaml
# Inline scalar
replicaCount: {{ <c>_replica_count }}

# Inline object / list
tolerations: {{ <c>_tolerations | to_json }}
nodeSelector: {{ <c>_node_selector | to_json }}

# Block YAML (for larger dicts)
config:
  {{ <c>_config | to_nice_yaml | indent(4) }}

# Merged ESO secrets
secrets: {{ eso_vault_integration_<c>_secrets_merged | to_json }}

# Conditional attach
{% if <c>_vpn_only_enabled %}
middlewares:
  - name: vpn-only
    namespace: {{ traefik_namespace }}
{% endif %}
```

---

## Tier 2 — Global & Cross-cutting Catalog

Per-file source of truth in parentheses.

### 2.1 Kubernetes core (`hosts-vars/k8s-base.yaml`)

| Variable | Default | Purpose |
|---|---|---|
| `k8s_version` | `"1.34"` | Short version (for apt repo URL) |
| `k8s_full_version` | `"v1.34.0"` | Full version pin in kubeadm config |
| `containerd_version` | `"2.2.1"` | Container runtime |
| `runc_version` | `"1.4.0"` | OCI runtime |
| `cni_plugins_version` | `"1.9.0"` | CNI plugins bundle |
| `helm_version` | `"3.19.2"` | Helm binary version |
| `k9s_version` | `"0.50.18"` | k9s binary version |
| `service_subnet` | `"10.128.0.0/12"` | Kubernetes Service CIDR |
| `pod_subnet` | `"10.64.0.0/10"` | Pod CIDR (Cilium IPAM) |
| `cluster_dns_domain` | `"cluster.local"` | Cluster DNS suffix |
| `node_port_start`, `node_port_end` | `1`, `50000` | NodePort range (apiserver `service-node-port-range`) |
| `crd_wait_timeout` | `"60s"` | Used by `tasks-wait-crds.yaml` |
| `crd_wait_retries` | `15` | Same |
| `crd_wait_delay` | `"5s"` | Same |
| `node_drain_timeout` | `"10m"` | Default `kubectl drain --timeout` |
| `node_monitor_grace_period` | `"30s"` | kube-controller-manager flag |
| `remote_charts_dir` | `"/opt/helm-charts"` | Where charts are rsynced on the master manager |

### 2.2 HAProxy apiserver LB (`hosts-vars/k8s-base.yaml`)

| Variable | Default | Purpose |
|---|---|---|
| `haproxy_apiserver_lb_host` | `"127.0.0.1"` | Bind address on each node |
| `haproxy_apiserver_lb_port` | `16443` | Frontend port (apiserver LB) |
| `haproxy_apiserver_lb_healthz_port` | `16444` | HTTP `/healthz` endpoint |
| `haproxy_apiserver_lb_package_version` | `"3.3"` | Pinned apt version |

### 2.3 ETCD encryption (`hosts-vars/k8s-base.yaml`)

| Variable | Default / purpose |
|---|---|
| `etcd_encryption_resources` | `[secrets, configmaps]` — resources encrypted at rest |
| `etcd_encryption_config_path` | `/etc/kubernetes/pki/encryption-config.yaml` |
| `etcd_rotation_state_dir` | `/etc/kubernetes/pki` — where rekey state files are written |

### 2.4 Kubelet (`hosts-vars/k8s-base.yaml`)

| Variable | Purpose |
|---|---|
| `kubelet_container_log_max_size` | Per-container log size cap |
| `kubelet_container_log_max_files` | Log rotation file count |
| `kubelet_crashloop_max_backoff` | Caps `CrashLoopBackOff` (feature gate `KubeletCrashLoopBackOffMax`) |
| `kubelet_eviction_soft_memory_available`, `kubelet_eviction_soft_nodefs_available` | Soft eviction thresholds |
| `kubelet_eviction_hard_memory_available`, `kubelet_eviction_hard_nodefs_available` | Hard eviction thresholds |
| `kubelet_image_gc_high_threshold`, `kubelet_image_gc_low_threshold` | Image GC thresholds (%) |

### 2.5 kubeadm template (`hosts-vars/kubeadm-config.yaml`)

- `kubeadm_config_template` — full kubeadm `ClusterConfiguration` + `InitConfiguration` + `KubeletConfiguration` + `KubeProxyConfiguration` template. Rendered by `tasks-kubeadm-config-create.yaml`.
- `kubeadm_config_path` — `/etc/kubernetes/kubeadm-config.yaml`.

### 2.6 Vault & ESO cross-cutting (`hosts-vars/vault.yaml`, `hosts-vars/vault-eso.yaml`)

| Variable | Purpose |
|---|---|
| `vault_namespace` | `"vault"` — where bank-vaults operator + Vault pod live |
| `vault_internal_url` | `http://vault.{{ vault_namespace }}.svc.{{ cluster_dns_domain }}:8200` |
| `vault_key_shares` | `3` — Shamir |
| `vault_key_threshold` | `2` — Shamir |
| `vault_storage_class` | Longhorn class for Vault PVC |
| `vault_storage_size` | Default `"2Gi"` |
| `vault_creds_host_path` | `/etc/kubernetes/vault-unseal.json` — unseal creds on every manager |
| `vault_auto_unseal_schedule` | Cron for auto-unseal CronJob (default `"*/5 * * * *"`) |
| `vault_policies` / `vault_policies_extra` | Vault ACL policy definitions |
| `vault_roles` / `vault_roles_extra` | Kubernetes-auth role bindings (SA + ns → policies) |
| `eso_vault_integration_<c>` | One object per ESO-integrated component — see `secrets-and-eso.md` |

### 2.7 VPN allowlist (`hosts-vars/vpn-rules.yaml`)

| Variable | Purpose |
|---|---|
| `vpn_ips` | List of CIDRs allowed to reach VPN-gated ingresses |
| `vpn_traefik_middlewares` | Traefik `Middleware` resources (usually `vpn-only` IP allow-list) |
| `vpn_ingress_middlewares` | String reference used by standard K8s `Ingress` annotation |
| `vpn_ingress_route_middlewares` | List of middleware refs used by Traefik `IngressRoute` CRD |

### 2.8 cert-manager cross-cutting (`hosts-vars/cert-manager.yaml`)

| Variable | Purpose |
|---|---|
| `cert_manager_cluster_issuers` | List of `ClusterIssuer` definitions. Each includes `name`, `server`, `email`, `privateKeySecretName`, `solvers[]`. Single source of truth for HTTP-01 / DNS-01 issuers. |

Downstream components resolve their issuer via `tasks-resolve-acme-solver.yaml`, producing the `_acme_*` output facts listed in §1.2.

### 2.9 Teleport configure (`hosts-vars/teleport-configure.yaml`)

Pure declarative list of Teleport resources applied by `teleport/configure/` chart. Base empty; users populate via `_extra`.

| Variable family | Purpose |
|---|---|
| `teleport_configure_roles` / `_extra` | Teleport Role CRs |
| `teleport_configure_users` / `_extra` | Local users |
| `teleport_configure_bots` / `_extra` | Machine-ID bots |
| `teleport_configure_apps` / `_extra` | App service definitions |
| `teleport_configure_databases` / `_extra` | DB service definitions |
| `teleport_configure_oidc` / `_extra` | OIDC auth connectors |
| `teleport_configure_saml` / `_extra` | SAML auth connectors |
| `teleport_configure_access_lists` / `_extra` | Access lists |
| `teleport_configure_trusted_clusters` / `_extra` | Trusted cluster leaves |

### 2.10 Inventory host variables (`hosts-vars/hosts.yaml` + `hosts-vars-override/hosts.yaml`)

| Variable | Scope | Purpose |
|---|---|---|
| `ansible_host` | host | Public/routable IP |
| `ansible_user`, `ansible_password`, `ansible_port`, `ansible_ssh_private_key_file` | host | SSH credentials (override only) |
| `internal_ip` | host | Private/internal IP (used in HAProxy backend, Cilium host firewall, certSANs) |
| `is_master` | manager host | Exactly ONE manager must set `true` — becomes `master_manager_fact` |
| `api_server_advertise_address` | manager host | kubeadm `--apiserver-advertise-address` |
| `api_server_bind_port` | manager host | kubeadm apiserver bind port (default `6443`) |
| `node_labels` | host | List of labels applied at `cluster-init` / join via `tasks-apply-node-labels.yaml` |
| Groups: `managers`, `workers` | inventory | Defined in `hosts-vars/hosts.yaml` group vars |

### 2.11 Output facts (runtime-only, produced by tasks)

| Fact | Produced by | Consumed where |
|---|---|---|
| `master_manager_fact` | `tasks-set-master-manager.yaml` (via `tasks-pre-check.yaml` / `tasks-gather-cluster-facts.yaml`) | Every `delegate_to:` in `playbook-app/` |
| `is_master_manager_exist` | `tasks-set-master-manager.yaml` | Guards in bootstrap plays |
| `is_cluster_init` | `tasks-set-is-cluster-init.yaml` | Skip re-init logic |
| `is_node_joined` | `tasks-set-is-node-joined.yaml` | Skip re-join logic |
| `joined_node_ips` | `tasks-gather-cluster-facts.yaml` | Cilium host-firewall `nodeIps`, certSANs |
| `joined_node_hostnames` | same | Logging / validation |
| `vault_policies_final` | `tasks-eso-merge.yaml` | Rendered into `vault/install/values-override.yaml` |
| `vault_roles_final` | same | Same |
| `eso_vault_integration_<c>_secrets_merged` | same | Rendered into `<c>/pre/values-override.yaml` |
| `<c>_acme_cluster_issuer`, `<c>_acme_solver`, `<c>_acme_solver_pod_labels` | `tasks-resolve-acme-solver.yaml` | NetworkPolicy templates in `<c>/pre/` |

---

## 3. Conventions For New Variables

1. Follow the suffix convention (§1.1–§1.4). Prefer reusing an existing suffix over inventing one.
2. If the variable is per-component, document it in [`components.md`](components.md), not here.
3. If the variable is cross-cutting (used by more than one component or at bootstrap time), add it to Tier 2 above.
4. If the array should be user-extendable, add both `<name>` (base) and `<name>_extra` (override layer) and merge at runtime with `+`.
5. Never put secrets in `hosts-vars/`. Only in `hosts-vars-override/`.

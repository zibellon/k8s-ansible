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
| `_helm_chart_version` | Helm chart version (for external charts; rename from legacy `_chart_version`) | `traefik_helm_chart_version: "39.0.5"` |
| `_helm_is_oci` | Bool: HTTP repo (false) or OCI registry (true). Switches `tasks-add-helm-repo.yaml` between `helm repo add` and noop | `cilium_helm_is_oci: false`, `vault_operator_helm_is_oci: true` |
| `_helm_url` | HTTP repo URL **or** full OCI chart URL (depending on `_helm_is_oci`). Rename from legacy `_helm_repo_url` | `cilium_helm_url: "https://helm.cilium.io/"`, `vault_operator_helm_url: "oci://ghcr.io/..."` |
| `_helm_repo_name` | Helm repo alias for `helm repo add` (HTTP only; ignored for OCI) | `cilium_helm_repo_name: "cilium"` |
| `_helm_chart_name` | Chart name within HTTP repo (HTTP only; ignored for OCI) | `cilium_helm_chart_name: "cilium"` |
| `_image` | Full image URI:tag (handwritten charts only — chart's `image:` field receives the literal). AirGap interception via containerd `_default` mirror, не per-component override | `vault_image: "docker.io/hashicorp/vault:1.21.2"` |
| `_secret_key_<field>` | Vault secret field name — единое определение, переиспользуемое в ключе `dto_vault_put_data`, `dto_vault_get_field`, ESO `remoteRef.property`, ключе consumer-чарта. См. [`secrets-and-eso.md`](secrets-and-eso.md) §2.5. | `gitlab_redis_secret_key_password: "password"` |
| `_replica_count` | Replica count on Deployments | `int` |
| `_tolerations`, `_node_selector`, `_affinity` | Scheduling | `list` / `dict` / `dict` |
| `_resources` | CPU/memory requests & limits | `dict` |
| `_helm_timeout` | `helm --timeout` value | `"5m"` |
| `_rollout_timeout` | `kubectl rollout status --timeout` | `"120s"` |
| `_daemonset_rollout_timeout` | Same, for DaemonSet workloads | `"180s"` |
| `_helm_values` | Full inline values dict (often large) | `dict` |
| `_kustomize_patches` | Per-phase kustomize patches applied to all LOCAL-managed chart phases before helm install (see [`reusable-tasks.md`](reusable-tasks.md) §1.4б `tasks-helm-template-kustomize-build.yaml`, [`playbook-conventions.md`](playbook-conventions.md) §21). Each item: `{target: {kind, name}, patch: \|- ...}`. Default `[]`. Operator override replaces base (no concat-merge — no `_extra` companion). | `list` of dicts |
| `_extra_objects` | Per-phase list of arbitrary K8s manifests appended via `templates/extra-objects.yaml` (operator-side, default `[]`). Operator override полностью заменяет base (нет `_extra` companion). См. [`playbook-conventions.md`](playbook-conventions.md) §22. | `list` of K8s manifest dicts |

### 1.2 Ingress & TLS suffixes

| Suffix | Purpose |
|---|---|
| `_domain`, `_ui_domain`, `_rpc_domain` | FQDN for ingress |
| `_https_secret_name` | `tls.secretName` on the Ingress / IngressRoute |
| `_cluster_issuer_name` | cert-manager `ClusterIssuer` name |
| `_ingress_class_name` | Traefik ingress class (usually `traefik-lb`) |
| `_http_enable`, `_https_enable` | Toggle plain-HTTP ingress |
| `_vpn_only_enabled` | If true, attach Traefik `vpn-only` middleware |

### 1.3 ServiceMonitor suffixes

| Suffix | Purpose |
|---|---|
| `_service_monitor_enabled` | Gate (default `true` where supported) |
| `_service_monitor_interval` | Scrape interval |
| `_service_monitor_scrape_timeout` | Scrape timeout |
| `_service_monitor_additional_labels` / `_service_monitor_labels` | Additional labels (name varies by component — `grep` before adding) |

### 1.4 ESO integration object

Every ESO-integrated component has the following variables in its `hosts-vars/<c>.yaml`:

**Integration object:**
```yaml
eso_vault_integration_<c>:
  sa_name: "eso-main"
  role_name: "<c>.eso-main"
  secret_store_name: "eso-main.vault"
  kv_engine_path: "eso-secret"
```

**Named dict-variables** (`<c>_secret_<logical>`) — one per base secret. Each variable is a full dict with fields matching `secrets-and-eso.md` §2.4 (`external_secret_name`, `vault_path`, `body`, optional `is_need_eso`, `refresh_interval`). Example:
```yaml
zitadel_secret_postgresql_creds:
  external_secret_name: "eso-zitadel-postgresql-creds"
  vault_path: "/zitadel/postgresql/creds"
  body:
    target:
      name: "eso-zitadel-postgresql-creds"
    dataFrom:
      - extract:
          key: "{{ eso_vault_integration_zitadel.kv_engine_path }}/data/zitadel/postgresql/creds"
```
These variables serve two purposes: (1) referenced by name in the base `eso_vault_integration_<c>_secrets` array (a list of Jinja-string-references `"{{ <c>_secret_<logical> }}"`), and (2) accessed directly from `*_helm_values` and `<c>-{install,configure}.yaml` playbooks via `<c>_secret_<logical>.body.target.name` and `<c>_secret_<logical>.vault_path`.

**Secrets list** (`eso_vault_integration_<c>_secrets`) and **extension layer** (`eso_vault_integration_<c>_secrets_extra`). Base is a list of Jinja-string-references to named dict-variables (e.g. `- "{{ <c>_secret_<logical> }}"`). `_extra` is a list of full dict-items (operator extension, same format but inline). Inline merge `base + (extra | default([]))` is done at usage sites (`<c>_pre_helm_values.eso.secrets`); store-level gating (`is_need_eso` on the integration object) was removed — only item-level gating via `body.is_need_eso` per-secret remains. For field schema see [`secrets-and-eso.md`](secrets-and-eso.md) §2.4.

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
eso_vault_integration_<c>_secrets_extra    # for each of the 8 ESO-integrated components
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

# ESO secrets (inline base + extra merge, no runtime fact)
secrets: "{{ eso_vault_integration_<c>_secrets + (eso_vault_integration_<c>_secrets_extra | default([])) }}"

# Conditional attach
{% if <c>_vpn_only_enabled %}
middlewares:
  - name: vpn-only
    namespace: {{ traefik_namespace }}
{% endif %}
```

### 1.8 Local runtime facts — the `_local_` convention

Runtime variables created and consumed **within a single playbook** (generated secrets, `tasks-vault-get` / `tasks-k8s-secret-get` / `tasks-add-helm-repo` result facts, `set_fact` helpers, `register:` results) carry a `_local_` prefix. This separates them from inventory variables, `*_extra` arrays, and cross-playbook contract facts (`master_manager_fact`, `acme_*_result_fact`, `is_cluster_init`, … — see §2.11; those are **not** prefixed).

- **Naming.** Final name = `_local_` + base name with any single leading `_` stripped. E.g. `gitlab_postgresql_vault` → `_local_gitlab_postgresql_vault`; `_grafana_admin_password_generated` → `_local_grafana_admin_password_generated`.
- **Name hoisting.** For a fact whose name is supplied as a string (a `dto_*_fact_name` param of a reusable task, or a `set_fact` key), declare the name once in the play-level `vars:` block as `_local_<base>_key: "_local_<base>"`, then set and read the fact **through that key variable** — never by hardcoded string.
- **Dynamic set.** `dto_<...>_fact_name: "{{ _local_<base>_key }}"`, or inline `set_fact: "{{ _local_<base>_key }}": <value>`.
- **Dynamic get.** `{{ lookup('vars', _local_<base>_key) }}` — type-preserving (`bool` stays `bool`, `str` stays `str`).
- **Definedness check.** `_local_<base>_key in hostvars[inventory_hostname]` — a plain `lookup('vars', ...)` raises on an undefined variable, so membership is used for `is defined` / `is not defined` checks.
- **`register:` exception.** A `register:` variable name cannot be templated — such variables get the `_local_` prefix only (no key variable, no `lookup`), referenced normally as `{{ _local_<name> }}`.

```yaml
vars:
  _local_admin_pw_generated_key: "_local_admin_pw_generated"
# set (via a reusable task param):
dto_generate_fact_name: "{{ _local_admin_pw_generated_key }}"
# get:
password: "{{ lookup('vars', _local_admin_pw_generated_key) }}"
```

---

## Tier 2 — Global & Cross-cutting Catalog

Per-file source of truth in parentheses.

### 2.1 Kubernetes core (`hosts-vars/k8s-base.yaml`)

| Variable | Default | Purpose |
|---|---|---|
| `llvm_version` | `20` | LLVM/Clang version for Cilium eBPF |
| `llvm_download_host` | `"https://apt.llvm.org"` | Download host for `llvm.sh` script — AirGap override |
| `llvm_install_method` | `"url"` | Install method: `"url"` (default — download from `llvm_url`) or `"local_script"` (offline; path relative to `project_root`, file kept in `pkgs-sources/`). **Не решает AirGap полностью** — `llvm.sh` сам внутри ходит curl на `apt.llvm.org` за GPG-ключом |
| `llvm_local_script_path` | `""` | Path to local `llvm.sh` relative to `project_root` (used only when `llvm_install_method: local_script`) |
| `k8s_version` | `"1.35"` | Short version (for apt repo URL) |
| `k8s_full_version` | `"v1.35.3"` | Full version pin in kubeadm config |
| `containerd_version` | `"2.2.2"` | Container runtime |
| `containerd_download_host` | `"https://github.com"` | Download host for containerd tarball — AirGap override |
| `containerd_service_download_host` | `"https://raw.githubusercontent.com"` | Download host for containerd.service unit — AirGap override |
| `containerd_install_method` | `"url"` | Install method: `"url"` (default — download from `containerd_url` + `containerd_service_url`) or `"local_tarball"` (offline; paths relative to `project_root`, files kept in `pkgs-sources/`) |
| `containerd_local_tarball_path` | `""` | Path to local containerd tarball relative to `project_root` (used only when `containerd_install_method: local_tarball`) |
| `containerd_service_local_path` | `""` | Path to local `containerd.service` unit file relative to `project_root` (used only when `containerd_install_method: local_tarball`) |
| `containerd_additional_configs` | `[{dirName: "_default", content: "..."}]` | List of drop-in configs in `/etc/containerd/certs.d/<dirName>/hosts.toml`. Default has one `_default` entry pointing all registries (catch-all) at `mirror.gcr.io`. **Единая точка AirGap-интерсепции** для образов всех компонентов — заменяет per-component `_image_registry` overrides. **Full-sync семантика:** любая поддиректория в `/etc/containerd/certs.d/`, которой нет в этом списке (включая ручные `mkdir` оператора), удаляется на следующем прогоне `node-install.yaml`. См. `hosts-vars/k8s-base.yaml` для развёрнутого описания + примеров |
| `runc_version` | `"v1.4.2"` | OCI runtime |
| `runc_download_host` | `"https://github.com"` | Download host for runc binary — AirGap override |
| `runc_install_method` | `"url"` | Install method: `"url"` (default — download from `runc_url`) or `"local_file"` (offline; path relative to `project_root`, file kept in `pkgs-sources/`) |
| `runc_local_path` | `""` | Path to local runc binary relative to `project_root` (used only when `runc_install_method: local_file`) |
| `cni_plugins_version` | `"v1.9.1"` | CNI plugins bundle |
| `cni_plugins_download_host` | `"https://github.com"` | Download host for CNI plugins bundle — AirGap override |
| `cni_plugins_install_method` | `"url"` | Install method: `"url"` (default — download from `cni_plugins_url`) or `"local_tarball"` (offline; path relative to `project_root`, file kept in `pkgs-sources/`) |
| `cni_plugins_local_tarball_path` | `""` | Path to local CNI plugins tarball relative to `project_root` (used only when `cni_plugins_install_method: local_tarball`) |
| `helm_version` | `"v3.20.2"` | Helm binary version |
| `helm_download_host` | `"https://get.helm.sh"` | Download host for Helm tarball — AirGap override |
| `helm_install_method` | `"url"` | Install method: `"url"` (default — download from `helm_url`) or `"local_tarball"` (offline) |
| `helm_local_tarball_path` | `""` | Path to local Helm tarball relative to `project_root` (used only when `helm_install_method: local_tarball`) |
| `k9s_version` | `"v0.50.18"` | k9s binary version |
| `k9s_download_host` | `"https://github.com"` | Download host for k9s .deb — AirGap override |
| `k9s_install_method` | `"url"` | Install method: `"url"` (default — download from `k9s_url`) or `"local_deb"` (offline; path relative to `project_root`, file kept in `pkgs-sources/`) |
| `k9s_local_deb_path` | `""` | Path to local k9s `.deb` file relative to `project_root` (used only when `k9s_install_method: local_deb`) |
| `service_subnet` | `"10.128.0.0/12"` | Kubernetes Service CIDR |
| `pod_subnet` | `"10.64.0.0/10"` | Pod CIDR (Cilium IPAM) |
| `cluster_dns_domain` | `"cluster.local"` | Cluster DNS suffix |
| `node_port_start`, `node_port_end` | `1`, `50000` | NodePort range (apiserver `service-node-port-range`) |
| `node_monitor_grace_period` | `"30s"` | kube-controller-manager flag |
| `node_drain_timeout` | `"10m"` | Default `kubectl drain --timeout` |
| `softdog_timeout` | `30` | Watchdog (softdog) reboot timeout in seconds |
| `apt_additional_configs` | `[]` | List of ansible-managed apt files. Each entry: `{filePath, content}` where `filePath` is path under `/etc/apt/` (subdir + name, basename must start with `ansible-`; allowed subdirs: `sources.list.d/`, `apt.conf.d/`). Auto-cleanup: removing entry from variable deletes the file on next run. Implemented via `tasks-sync-managed-files.yaml` (one call per managed subdir). |
| `apt_preferences` | `[]` | List of ansible-managed apt pinning files in `/etc/apt/preferences.d/`. Each entry: `{name, content}` where `name` must start with `ansible-`. Auto-cleanup: same as `apt_additional_configs`. Implemented via `tasks-sync-managed-files.yaml`. |
| `crds_wait` | `{timeout: "60s", retries: 15, delay: 5}` | CRD wait config — used by `tasks-wait-crds.yaml` |
| `secret_wait` | `{retries: 15, delay: 5}` | K8s Secret wait config — used by ESO sync tasks |
| `rollout_wait` | `{retries: 15, delay: 5}` | Rollout wait config — used by `tasks-wait-rollout.yaml` |
| `helm_async` | `{timeout: 1800, poll: 5}` | Async Helm upgrade config — resilient to SSH disconnects on long upgrades |
| `system_async` | `{timeout: 900, poll: 10}` | Async system playbook config — resilient to SSH disconnects on long imperative ops in `playbook-system/` (kubeadm init/join, node drain, LLVM install) |

### 2.2 HAProxy apiserver LB (`hosts-vars/k8s-base.yaml`)

| Variable | Default | Purpose |
|---|---|---|
| `haproxy_apiserver_lb_host` | `"127.0.0.1"` | Bind address on each node |
| `haproxy_apiserver_lb_port` | `16443` | Frontend port (apiserver LB) |
| `haproxy_apiserver_lb_healthz_port` | `16444` | HTTP `/healthz` endpoint |
| `haproxy_apiserver_lb_package_version` | `"3.3"` | Pinned apt version (used when `install_method: ppa`) |
| `haproxy_apiserver_lb_install_method` | `"ppa"` | Install method: `"ppa"` (default — vbernat PPA) or `"local_deb"` (offline) |
| `haproxy_apiserver_lb_local_deb_path` | `""` | Path to local `.deb` relative to `project_root` (used only when `install_method: local_deb`) |

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
| `kubelet_image_gc_high_threshold`, `kubelet_image_gc_low_threshold` | Image GC thresholds (%) |
| `kubelet_image_minimum_gc_age` | Minimum age of an unused image before GC may remove it |
| `kubelet_eviction_soft` | Soft eviction thresholds — dict, keys are resources (`memory.available`, `nodefs.available`, `imagefs.available`), values are thresholds |
| `kubelet_eviction_soft_grace_period` | Grace periods for soft eviction — dict, same keys as `kubelet_eviction_soft`, values are durations |
| `kubelet_eviction_hard` | Hard eviction thresholds — dict, keys are resources (`memory.available`, `nodefs.available`, `nodefs.inodesFree`, `imagefs.available`, `pid.available`), values are thresholds |

### 2.5 kubeadm template (`hosts-vars/kubeadm-config.yaml`)

- `kubeadm_config_template` — full kubeadm `InitConfiguration` + `ClusterConfiguration` (with `proxy.disabled: true` to skip kube-proxy) + `KubeletConfiguration` template. Rendered by `tasks-kubeadm-config-create.yaml`.
- `kubeadm_config_path` — `/etc/kubernetes/kubeadm-config.yaml`.

### 2.6 Vault & ESO cross-cutting (`hosts-vars/vault.yaml`)

| Variable | Purpose |
|---|---|
| `vault_namespace` | `"vault"` — where bank-vaults operator + Vault pod live |
| `vault_internal_url` | `http://vault.{{ vault_namespace }}.svc.{{ cluster_dns_domain }}:8200` |
| `vault_key_shares` | `3` — Shamir |
| `vault_key_threshold` | `2` — Shamir |
| `vault_storage_class` | Longhorn class for Vault PVC |
| `vault_storage_size` | Default `"2Gi"` |
| `vault_creds_host_path` | `/etc/kubernetes/vault-unseal.json` — unseal creds on every manager |
| `vault_rekey_temp_file_path` | Temp-файл на `master_manager_fact`, куда `vault-rotate.yaml` пишет новые unseal keys + root token между `vault operator rekey` и обновлением K8s Secret. Default `/etc/kubernetes/vault-rekey-in-progress.json`. Формат — как у `vault_creds_host_path`. Удаляется в конце успешного rotate. Наличие при старте playbook'а = recovery-режим. |
| `vault_policies` / `vault_policies_extra` | Vault ACL policy definitions |
| `vault_roles` / `vault_roles_extra` | Kubernetes-auth role bindings (SA + ns → policies) |
| `eso_vault_integration_<c>` | One object per ESO-integrated component; lives in `hosts-vars/<c>.yaml` (not `vault.yaml`) — see `secrets-and-eso.md` |

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

| Variable | Produced by | Consumed where |
|---|---|---|
| `master_manager_fact` | `tasks-set-master-manager.yaml` (via `tasks-pre-check.yaml`; или explicit include в начале `tasks:` системных playbook'ов) | Every `delegate_to:` in `playbook-app/` |
| `is_master_manager_exist` | `tasks-set-master-manager.yaml` | Guards in bootstrap plays |
| `bastion_host_fact`, `is_bastion_host_exist` | `tasks-set-master-manager.yaml` (co-located with master finding; вызывается через explicit include в начале `tasks:` системных playbook'ов и playbook'ов с reboot) | `tasks-reboot-cluster.yaml` для ordered reboot (non-bastion first, bastion last) |
| `is_cluster_init` | `tasks-set-is-cluster-init.yaml` | Skip re-init logic |
| `is_node_joined` | `tasks-set-is-node-joined.yaml` | Skip re-join logic |
| `joined_node_ips` | `tasks-set-is-node-joined.yaml` | Cilium host-firewall `nodeIpsList`, certSANs |
| `joined_node_hostnames` | same | Logging / validation |
| `acme_cluster_issuer_result_fact`, `acme_solver_result_fact`, `acme_pod_labels_result_fact` (global, not per-component) | `tasks-resolve-acme-solver.yaml` | NetworkPolicy templates in `<c>/pre/` (typically `acme_pod_labels_result_fact`) |

### 2.12 Ansible runtime settings (`hosts-vars/ansible.yaml`)

| Variable | Default | Purpose |
|---|---|---|
| `remote_charts_dir` | `"/opt/helm-charts"` | Where charts are rsynced on the master manager |

### 2.13 Linux package & diagnostic settings (`hosts-vars/linux-pkgs.yaml`)

| Variable | Default | Purpose |
|---|---|---|
| `fail2ban_jail_d_files` | list-of-`{filename, content}` (defaults: `ansible-defaults.conf` с `[DEFAULT]` блоком + `ansible-sshd.conf` с `[sshd]` блоком) | Drop-in файлы в `/etc/fail2ban/jail.d/`. Filename ОБЯЗАН начинаться с `ansible-` (для auto-cleanup orphans). Оператор для смены full-replace'ит массив. Рендерится `playbook-system/linux-service-configure.yaml` (FAIL2BAN phase) через `tasks-sync-managed-files.yaml` |
| `sshd_config_d_files` | list-of-`{filename, content}` (default: `ansible-hardening.conf` отключает password auth + KbdInteractive + EmptyPasswords) | Drop-in файлы в `/etc/ssh/sshd_config.d/`. Filename ОБЯЗАН начинаться с `ansible-` (для auto-cleanup orphans). Оператор для смены full-replace'ит массив. Рендерится `playbook-system/linux-service-configure.yaml` (SSHD phase) через `tasks-sync-managed-files.yaml`. Validation: `sshd -t` после write; reload (не restart) сохраняет существующую ansible-сессию. |
| `iperf3_port`, `iperf3_duration`, `iperf3_streams` | `5201` / `30` / `4` | Параметры iperf3 для `playbook-system/network-bandwidth-test.yaml` (порт server'а, длительность теста в секундах, параллельных streams) |
| `fio_read_directory`, `fio_read_runtime`, `fio_read_size`, `fio_read_blocksize`, `fio_read_iodepth`, `fio_read_numjobs` | `"/mnt"` / `120` / `"1G"` / `"8k"` / `64` / `1` | Параметры random READ теста для `playbook-system/disk-io-test.yaml` |
| `fio_write_directory`, `fio_write_runtime`, `fio_write_size`, `fio_write_blocksize`, `fio_write_iodepth`, `fio_write_numjobs` | `"/mnt"` / `120` / `"1G"` / `"8k"` / `64` / `1` | Параметры random WRITE теста для `playbook-system/disk-io-test.yaml` |

### 2.14 Bastion / SSH ProxyJump (optional)

Optional opt-in connection mode for environments where manager/worker nodes have no public IP and are reachable only via a bastion host (e.g. cloud VPC). Activated by defining `bastion_host` + `bastion_user` in `hosts-vars-override/hosts.yaml` under `all.vars`, and overriding `ansible_ssh_common_args` on the `managers` / `workers` groups with a `ProxyJump` clause. In this mode `ansible_host` is a private IP for every node (typically equal to `internal_ip`).

| Variable | Scope | Purpose |
|---|---|---|
| `bastion_host` | `all.vars` (override only) | Public IP / DNS of the bastion host |
| `bastion_user` | `all.vars` (override only) | SSH user on the bastion |

Activation pattern in `hosts-vars-override/hosts.yaml`:

```yaml
all:
  vars:
    bastion_host: "<public-ip-or-dns>"
    bastion_user: "ubuntu"
  children:
    managers:
      vars:
        ansible_ssh_common_args: "-o StrictHostKeyChecking=no -o ProxyJump={{ bastion_user }}@{{ bastion_host }}"
      hosts: ...
    workers:
      vars:
        ansible_ssh_common_args: "-o StrictHostKeyChecking=no -o ProxyJump={{ bastion_user }}@{{ bastion_host }}"
      hosts: ...
```

Notes:
- **Opt-in.** If `bastion_host` / `bastion_user` are absent from overrides, the inventory operates in classic public-IP mode — the global `ansible_ssh_common_args` from `hosts-vars/ansible.yaml` applies, no `ProxyJump`.
- **Multi-cluster.** Each `hosts-vars-override-<cluster>/` directory selects its own scheme independently — no global state in the repo.
- **Skeleton example.** See the commented-out template at the end of [`hosts-vars/hosts.yaml`](../../hosts-vars/hosts.yaml).

### 2.14.1 On-node bastion (когда bastion — один из узлов кластера)

Альтернативный режим: bastion — это manager или worker сам же (например, единственный узел с публичным IP). Требует двух дополнительных шагов поверх общей схемы (§2.14):

1. **Inventory флаг `is_bastion: true`** ставится на host'е, который выступает jump-host'ом. Поведение аналогично `is_master: true` — exactly one (или zero) host в inventory имеет этот флаг.
2. **Bastion-хост сам не должен использовать ProxyJump** — он же и есть jump-host. Его group (managers или workers) НЕ должна иметь `ansible_ssh_common_args` с `ProxyJump`. Обычно это значит: bastion-хост в **отдельной** ansible-group или в default'ной без override'а `ansible_ssh_common_args`.

Эффект: `tasks-set-master-manager.yaml` (он же ищет и bastion) устанавливает `bastion_host_fact`, и `tasks-reboot-cluster.yaml` упорядочивает reboot — сначала non-bastion (parallel через bastion), затем bastion (direct connection, ProxyJump уже не нужен). Без этого reboot всех хостов параллельно убивал бы bastion первым, разрывая ProxyJump для остальных.

Если `is_bastion` не задан ни на одном host'е (external bastion, public-IP scheme, single-host) — `bastion_host_fact` undefined, reboot работает старым параллельным способом (backward-compat).

---

## 3. Conventions For New Variables

1. Follow the suffix convention (§1.1–§1.4). Prefer reusing an existing suffix over inventing one.
2. If the variable is per-component, document it in [`components.md`](components.md), not here.
3. If the variable is cross-cutting (used by more than one component or at bootstrap time), add it to Tier 2 above.
4. If the array should be user-extendable, add both `<name>` (base) and `<name>_extra` (override layer) and merge at runtime with `+`.
5. Never put secrets in `hosts-vars/`. Only in `hosts-vars-override/`.
6. Local runtime facts (created and consumed within a single playbook) follow the `_local_` prefix convention — see §1.8.

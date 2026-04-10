# Variable Conventions

## Naming Patterns

### Standard per-component variables

```yaml
<component>_namespace: "..."
<component>_version: "..."             # image/app version
<component>_chart_version: "..."       # helm chart version (traefik uses this)
<component>_helm_timeout: "5m"
<component>_rollout_timeout: "120s"
<component>_daemonset_rollout_timeout: "120s"   # for DaemonSet workloads (cilium, traefik)
<component>_replica_count: 1
<component>_tolerations: []
<component>_node_selector: {}
<component>_affinity: {}
<component>_resources: {}
```

### Ingress / TLS variables

```yaml
<component>_ui_domain: "..."
<component>_ui_https_secret_name: "..."
<component>_cluster_issuer_name: "..."
<component>_ingress_class_name: "traefik-lb"
<component>_vpn_only_enabled: false
<component>_http_enable: false
<component>_https_enable: true
```

### ACME resolver output variables (set dynamically by tasks-resolve-acme-solver.yaml)

```yaml
<component>_acme_cluster_issuer: "..."      # resolved ClusterIssuer name
<component>_acme_solver: {...}              # solver config dict
<component>_acme_solver_pod_labels: {...}   # labels for ACME challenge pods
```

### ServiceMonitor variables

```yaml
<component>_service_monitor_enabled: true
<component>_service_monitor_interval: "30s"
<component>_service_monitor_scrape_timeout: "10s"
<component>_service_monitor_additional_labels: {}
# Some components use _labels instead of _additional_labels:
<component>_service_monitor_labels: {}
```

### ESO / Vault integration variables

```yaml
# Declared in hosts-vars/<component>.yaml
eso_vault_integration_<component>:
  sa_name: "..."                # Kubernetes ServiceAccount name
  role_name: "..."              # Vault Kubernetes auth role name
  secret_store_name: "..."      # ESO SecretStore name
  kv_engine_path: "eso-secret"  # Vault KV engine path
  secrets: []                   # list of secrets to sync (base)
  secrets_extra: []             # additional secrets (override layer)

# Produced by tasks-eso-merge.yaml at runtime
eso_vault_integration_<component>_secrets_merged: [...]
```

Flag to enable ESO resources creation:
```yaml
<component>_is_need_eso: true   # false by default
```

### ConfigMap extra-data variables (ArgoCD pattern)

```yaml
argocd_cm_extra: {}
argocd_cm_cmd_params_extra: {}
argocd_cm_gpg_keys_extra: {}
argocd_cm_notifications_extra: {}
argocd_cm_rbac_extra: {}
argocd_cm_ssh_known_hosts_extra: {}
argocd_cm_tls_certs_extra: {}
```

### Sub-component scheduling (cert-manager pattern)

When a chart deploys multiple sub-components each gets its own set:
```yaml
cert_manager_tolerations: []
cert_manager_node_selector: {}
cert_manager_affinity: {}
cert_manager_resources: {}

cert_manager_cainjector_tolerations: []
cert_manager_cainjector_node_selector: {}
cert_manager_cainjector_resources: {}

cert_manager_webhook_tolerations: []
cert_manager_webhook_node_selector: {}
cert_manager_webhook_resources: {}
```

## hosts-vars/ File Structure

Every file follows `all: vars:` root with section comments:

```yaml
all:
  vars:
    # ------
    # Component Name
    # ------

    # Basics
    <component>_namespace: "..."
    <component>_version: "..."

    # Networking
    <component>_ui_domain: "..."

    # Tolerations / Scheduling
    <component>_tolerations: []
    <component>_node_selector: {}
    <component>_affinity: {}

    # Resources
    <component>_resources: {}

    # Prometheus / ServiceMonitor
    <component>_service_monitor_enabled: true
    <component>_service_monitor_interval: "30s"

    # Timeouts
    <component>_helm_timeout: "5m"
    <component>_rollout_timeout: "120s"
```

## hosts-vars-override/ File Structure

Same `all: vars:` root but contains real production values:
- Real IPs, hostnames, domains
- `ansible_host`, `ansible_user`, `ansible_password`
- Vault tokens and paths
- Storage class names
- Actual cluster issuer names

**Never commit** this directory.

## Global Variables (hosts-vars/k8s-base.yaml)

### Kubernetes & tooling versions
| Variable | Value |
|----------|-------|
| `k8s_version` | "1.34" |
| `k8s_full_version` | "v1.34.0" |
| `containerd_version` | "2.2.1" |
| `runc_version` | "1.4.0" |
| `cni_plugins_version` | "1.9.0" |
| `helm_version` | "3.19.2" |
| `k9s_version` | "0.50.18" |

### Networking
| Variable | Value |
|----------|-------|
| `service_subnet` | "10.128.0.0/12" |
| `pod_subnet` | "10.64.0.0/10" |
| `dns_domain` | "cluster.local" |
| `node_port_start` | 1 |
| `node_port_end` | 50000 |

### HAProxy LB
| Variable | Value |
|----------|-------|
| `haproxy_apiserver_lb_host` | "127.0.0.1" |
| `haproxy_apiserver_lb_port` | 16443 |
| `haproxy_apiserver_lb_healthz_port` | 16444 |
| `haproxy_apiserver_lb_package_version` | "3.3" |

### ETCD encryption
| Variable | Purpose |
|----------|---------|
| `etcd_encryption_resources` | [secrets, configmaps] |
| `etcd_encryption_config_path` | "/etc/kubernetes/pki/encryption-config.yaml" |
| `etcd_rotation_state_dir` | "/etc/kubernetes/pki" |

### CRD wait (used by tasks-wait-crds.yaml)
| Variable | Default |
|----------|---------|
| `crd_wait_timeout` | "60s" |
| `crd_wait_retries` | 15 |
| `crd_wait_delay` | "5s" |

### Kubelet settings
| Variable | Purpose |
|----------|---------|
| `kubelet_container_log_max_size` | "100Mi" |
| `kubelet_container_log_max_files` | 5 |
| `kubelet_crashloop_max_backoff` | "2m" |
| `kubelet_eviction_soft_memory_available` | "1Gi" |
| `kubelet_eviction_soft_nodefs_available` | "15%" |
| `kubelet_eviction_hard_memory_available` | "500Mi" |
| `kubelet_eviction_hard_nodefs_available` | "10%" |
| `kubelet_image_gc_high_threshold` | 85 (%) |
| `kubelet_image_gc_low_threshold` | 75 (%) |
| `node_monitor_grace_period` | "30s" |
| `node_drain_timeout` | "10m" |

### Remote paths
| Variable | Purpose |
|----------|---------|
| `remote_charts_dir` | Remote base path for Helm charts |
| `kubeadm_config_path` | "/etc/kubernetes/kubeadm-config.yaml" |
| `vault_creds_host_path` | "/etc/kubernetes/vault-unseal.json" |

## Vault / ESO Variables (hosts-vars/vault.yaml)

```yaml
vault_namespace: "vault"
vault_internal_url: "http://vault.vault.svc.cluster.local:8200"
vault_key_shares: 3
vault_key_threshold: 2
vault_storage_class: "lh-major-single-best-effort"
vault_storage_size: "2Gi"
vault_creds_host_path: "/etc/kubernetes/vault-unseal.json"
vault_auto_unseal_schedule: "*/5 * * * *"   # cron for auto-unseal CronJob
```

Vault KV engines: `secret`, `eso-secret`

Components with ESO integration (in vault.yaml): traefik, haproxy, longhorn, gitlab, gitlab-runner, zitadel, argocd, argocd-git-ops, grafana

## Rendering Patterns

```yaml
# Object to JSON (inline in YAML content)
tolerations: {{ component_tolerations | to_json }}
nodeSelector: {{ component_node_selector | to_json }}

# Dict to indented YAML block
config:
  {{ component_config | to_nice_yaml | indent(8) }}

# Conditional block
{% if component_vpn_only_enabled %}
middlewares:
  - name: vpn-only
{% endif %}

# ESO secrets merged list
secrets: {{ eso_vault_integration_component_secrets_merged | to_json }}
```

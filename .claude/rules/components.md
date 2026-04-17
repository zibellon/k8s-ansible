# Components — Per-component Reference

One strict template per component. For the 3-phase install pattern in general, see `CLAUDE.md` §4 and [`playbook-conventions.md`](playbook-conventions.md). For ESO details, see [`secrets-and-eso.md`](secrets-and-eso.md).

Template fields:

- **Chart path** — `playbook-app/charts/<c>/` subdirectories (phase dirs).
- **Install playbook** — `playbook-app/<c>-install.yaml`.
- **Namespace** — K8s namespace (and whether it is fixed by upstream).
- **Releases** — Helm release names deployed by the install playbook.
- **External Helm repo** — if the install phase uses an upstream chart, the `helm repo add` URL.
- **Required vars** — key knobs from the component's `hosts-vars/<c>.yaml` (full suffix list in [`variables.md`](variables.md) §1).
- **ESO integration** — `yes/no`; if yes, the `eso_vault_integration_<c>` object points to which Vault paths.
- **ServiceMonitor** — whether the post phase creates one.
- **Dependencies** — components that must be installed first.
- **Image registry overrides** — variables for air-gap re-targeting.
- **Non-install playbooks** — companion plays (`-configure`, `-restart`, `-rotate`, sync helpers).

---

## 1. `cilium`

- **Chart path.** `charts/cilium/{pre,install,post}/`.
- **Install playbook.** `cilium-install.yaml`.
- **Namespace.** `cilium`.
- **Releases.** `cilium-pre`, `cilium`, `cilium-post`.
- **External Helm repo.** `https://helm.cilium.io/` → chart `cilium/cilium`, version `cilium_chart_version` (matches `cilium_version`, default `1.19.1`).
- **Required vars.** `cilium_version`, `cilium_mask_size` (21), `cilium_helm_values` (large dict — `kubeProxyReplacement: true`, `k8sServiceHost`, `k8sServicePort`, etc.), per-sub-component tolerations/nodeSelector/resources for `agent`, `operator`, `envoy`, `hubble_relay`, `hubble_ui`, `hubble_ui_domain`.
- **ESO integration.** No.
- **ServiceMonitor.** Yes — per sub-component (`cilium_agent_service_monitor_enabled`, `hubble_service_monitor_enabled`, etc.).
- **Dependencies.** None (installed first, before any other app). Must run BEFORE each node join: `--tags post` regenerates the host-firewall policy (`CiliumClusterwideNetworkPolicy`) with the new node's IPs.
- **Image registry overrides.** `cilium_image_registry` (and per-sub-component if needed).
- **Non-install playbooks.** `cilium-restart.yaml` (rollout-restart agent DaemonSet, operator Deployment, envoy DS, Hubble components).
- **Notes.** Deployed as DaemonSet with `tolerations: [{operator: "Exists"}]` — runs on every node including tainted ones. `kube-proxy` is never installed — Cilium replaces it via `--skip-phases=addon/kube-proxy` at `kubeadm init`.

## 2. `cert-manager`

- **Chart path.** `charts/cert-manager/{pre,install,post}/`.
- **Install playbook.** `cert-manager-install.yaml`.
- **Namespace.** `cert-manager`.
- **Releases.** `cert-manager-pre`, `cert-manager`, `cert-manager-post`.
- **External Helm repo.** `https://charts.jetstack.io` → chart `jetstack/cert-manager`, version `v{{ cert_manager_chart_version }}` (note `v` prefix).
- **Required vars.** `cert_manager_namespace`, `cert_manager_version`, plus per-sub-component (`cert_manager_`, `cert_manager_cainjector_`, `cert_manager_webhook_`) tolerations/nodeSelector/affinity/resources. Global `cert_manager_cluster_issuers` (list of `ClusterIssuer` specs, including `solvers[]` with `ingressClass` + `podLabels`).
- **ESO integration.** No.
- **ServiceMonitor.** Yes.
- **Dependencies.** Cilium (CNI). Traefik (if using HTTP-01).
- **Image registry overrides.** `cert_manager_image_registry` (and per-sub-component variants).
- **Non-install playbooks.** None.
- **Notes.** `cert_manager_cluster_issuers` is the single source of truth — downstream components derive ACME solver pod labels via `tasks-resolve-acme-solver.yaml` (never hard-code).

## 3. `external-secrets`

- **Chart path.** `charts/external-secrets/{pre,install,post}/`.
- **Install playbook.** `external-secrets-install.yaml`.
- **Namespace.** `external-secrets`.
- **Releases.** `external-secrets-pre`, `external-secrets`, `external-secrets-post`.
- **External Helm repo.** No — local chart.
- **Required vars.** `external_secrets_namespace`, `external_secrets_version`, per-sub-component tolerations/nodeSelector/affinity/resources for `controller`, `webhook`, `cert_controller`.
- **ESO integration.** No (it *is* ESO).
- **ServiceMonitor.** Yes.
- **Dependencies.** Cilium, cert-manager.
- **Image registry overrides.** `external_secrets_image_registry`.
- **Non-install playbooks.** `external-secrets-restart.yaml`.

## 4. `vault`

- **Chart path.** `charts/vault/{pre,install,cr,post}/`.
- **Install playbook.** `vault-install.yaml`.
- **Namespace.** `vault`.
- **Releases.** `vault-pre`, `vault` (bank-vaults operator), `vault-cr` (Vault Custom Resource), `vault-post`.
- **External Helm repo.** No — local chart (HashiCorp chart embedded + bank-vaults operator).
- **Required vars.** `vault_namespace`, `vault_version` (image), `vault_storage_class`, `vault_storage_size`, `vault_key_shares` (3), `vault_key_threshold` (2), `vault_policies` / `_extra`, `vault_roles` / `_extra`, `vault_auto_unseal_schedule`, `vault_creds_host_path`.
- **ESO integration.** No (Vault is ESO's **source**, not a consumer).
- **ServiceMonitor.** Yes.
- **Dependencies.** Cilium, cert-manager, external-secrets (ESO before Vault so SecretStores + ExternalSecrets can resolve as Vault comes up), longhorn (for PVC storage class).
- **Image registry overrides.** `vault_image_registry`.
- **Non-install playbooks.** `vault-rotate.yaml` — rekey unseal shares + rotate root token. Uses state files (see `bootstrap-and-ha.md`).
- **Notes.** Unseal creds live at `/etc/kubernetes/vault-unseal.json` on every manager (mode 0600). Distributed to new managers at `manager-join.yaml` via `tasks-vault-distribute-creds.yaml`. Two KV engines mounted: `secret/` (admin use), `eso-secret/` (ESO read-only consumption).

## 5. `haproxy`

- **Chart path.** `charts/haproxy/{pre,install,post}/`.
- **Install playbook.** `haproxy-install.yaml`.
- **Namespace.** `haproxy-lb`.
- **Releases.** `haproxy-pre`, `haproxy`, `haproxy-post`.
- **External Helm repo.** No — local chart.
- **Required vars.** `haproxy_namespace`, `haproxy_helm_values`, tolerations/nodeSelector/resources, TLS/ingress vars.
- **ESO integration.** Yes (via `eso_vault_integration_haproxy`; base `_secrets` empty — users fill via `_extra`).
- **ServiceMonitor.** Yes.
- **Dependencies.** Cilium, cert-manager, external-secrets, vault, traefik.
- **Image registry overrides.** `haproxy_image_registry`.
- **Non-install playbooks.** `haproxy-restart.yaml`.
- **Notes.** This is the **in-cluster** HAProxy ingress — NOT to be confused with the systemd-level apiserver LB in `playbook-system/haproxy-apiserver-lb.yaml`.

## 6. `traefik`

- **Chart path.** `charts/traefik/{pre,install,post}/`.
- **Install playbook.** `traefik-install.yaml`.
- **Namespace.** `traefik-lb` (NOT `traefik`).
- **Releases.** `traefik-pre`, `traefik`, `traefik-post`.
- **External Helm repo.** `https://traefik.github.io/charts` → chart `traefik/traefik`, version `traefik_chart_version` (default `38.0.2`, app version `v3.6.2`).
- **Required vars.** `traefik_namespace`, `traefik_version`, `traefik_chart_version`, `traefik_web_entrypoint`, `traefik_websecure_entrypoint`, `traefik_prometheus_port` (9200), `traefik_dashboard_domain`, DaemonSet tolerations `[{operator: "Exists"}]`.
- **ESO integration.** Yes (via `eso_vault_integration_traefik`; base `_secrets` empty — users add via `_extra` for custom TLS / basic-auth).
- **ServiceMonitor.** Yes.
- **Dependencies.** Cilium, cert-manager.
- **Image registry overrides.** `traefik_image_registry`.
- **Non-install playbooks.** `traefik-restart.yaml`.
- **Notes.** Ingress class is `traefik-lb`, **not** `traefik`. `post/` creates middlewares: `vpn-only` (ipAllowList from `vpn_ips`), `http-to-https`, `http-www-to-https`.

## 7. `longhorn`

- **Chart path.** `charts/longhorn/{pre,install,post}/`.
- **Install playbook.** `longhorn-install.yaml`.
- **Namespace.** `longhorn-system` — **fixed upstream, cannot rename**.
- **Releases.** `longhorn-pre`, `longhorn`, `longhorn-post`.
- **External Helm repo.** No — local chart (upstream Longhorn values embedded).
- **Required vars.** `longhorn_namespace`, `longhorn_version`, `longhorn_storage_classes` (list — empty by default; populate in overrides), `longhorn_helm_values`, tolerations/nodeSelector/resources.
- **ESO integration.** Yes (via `eso_vault_integration_longhorn`; S3 backup creds live under `eso-secret/longhorn/*`).
- **ServiceMonitor.** Yes.
- **Dependencies.** Cilium, cert-manager, external-secrets, vault. Node prep via `playbook-system/longhorn-prepare.yaml` (kernel modules `iscsi_tcp`, `dm_crypt`; packages `open-iscsi`, `nfs-common`, `cryptsetup`, `dmsetup`).
- **Image registry overrides.** `longhorn_image_registry` (and per-sub-component).
- **Non-install playbooks.** `longhorn-tags-sync.yaml` (sync node tags from inventory → `nodes.longhorn.io` CRD), `longhorn-s3-restore-create.yaml`, `longhorn-s3-restore-delete.yaml` (DR helpers).
- **Notes.** Storage class conventions: `lh-manager`, `lh-major`, `lh-worker`, `lh-minor` node tags drive scheduling. Default class for critical PVCs (including Vault): `lh-major-single-best-effort`.

## 8. `longhorn-s3-restore`

- **Chart path.** `charts/longhorn-s3-restore/` (flat — no phase dirs).
- **Install playbooks.** `longhorn-s3-restore-create.yaml`, `longhorn-s3-restore-delete.yaml`.
- **Namespace.** `longhorn-system`.
- **Releases.** `longhorn-s3-restore` (create), removed on delete.
- **Required vars.** S3 backup creds (accessible only from overrides or on-disk files — NOT from Vault, since this is used for DR when Vault is unavailable).
- **ESO integration.** No (intentionally — DR must not depend on ESO).
- **Notes.** Special flat chart — departure from 3-phase pattern. Used only when Vault is down and you need to prime S3 credentials directly as a K8s Secret so Longhorn can read a backup.

## 9. `argocd`

- **Chart path.** `charts/argocd/{crds,pre,install,post}/`.
- **Install playbook.** `argocd-install.yaml`.
- **Namespace.** `argocd` — **fixed upstream, cannot rename**.
- **Releases.** `argocd-crds`, `argocd-pre`, `argocd`, `argocd-post`.
- **External Helm repo.** No — local chart (upstream values embedded).
- **Required vars.** `argocd_namespace`, `argocd_version`, `argocd_ui_domain`, `argocd_rpc_domain`, `argocd_external_url`, `argocd_session_duration` (120h), `argocd_reconciliation_timeout` (30s), ConfigMap extras (`argocd_cm_extra`, `argocd_cm_cmd_params_extra`, `argocd_cm_gpg_keys_extra`, `argocd_cm_notifications_extra`, `argocd_cm_rbac_extra`, `argocd_cm_ssh_known_hosts_extra`, `argocd_cm_tls_certs_extra`), `argocd_ingress_class_name` (`traefik-lb`), `argocd_cluster_issuer_name`.
- **ESO integration.** Yes (via `eso_vault_integration_argocd`; admin password at `eso-secret/argocd/admin`). Note: shares namespace with `argocd-git-ops` — the two must not define duplicate secret names.
- **ServiceMonitor.** Yes.
- **Dependencies.** Cilium, cert-manager, external-secrets, vault, traefik.
- **Image registry overrides.** `argocd_image_registry`.
- **Non-install playbooks.** `argocd-configure.yaml` (one-off admin-password resolve/rotate + validation against ArgoCD API), `argocd-restart.yaml`.
- **Notes.** 7 ConfigMaps (`argocd-cm`, `argocd-cmd-params-cm`, `argocd-gpg-keys-cm`, `argocd-notifications-cm`, `argocd-rbac-cm`, `argocd-ssh-known-hosts-cm`, `argocd-tls-certs-cm`) are applied in `pre/`, not `install/` — so they exist before the first controller reconcile.

## 10. `argocd-git-ops`

- **Chart path.** `charts/argocd-git-ops/{pre,install,post}/`.
- **Install playbook.** `argocd-git-ops-install.yaml`.
- **Namespace.** `argocd` (shared with `argocd`).
- **Releases.** `argocd-git-ops-pre`, `argocd-git-ops`, `argocd-git-ops-post`.
- **Required vars.** `argocd_git_ops_*` for repo credentials (pattern + per-repo), ESO vault integration object.
- **ESO integration.** Yes — pulls Git repo credentials (SSH keys, tokens) from Vault. `eso_vault_integration_argocd_git_ops` defines its own secret list.
- **Dependencies.** `argocd` (uses the `argocd` namespace).
- **Notes.** Separated from `argocd` because the set of watched repos changes frequently in operations and should not trigger a full ArgoCD chart redeploy.

## 11. `gitlab`

- **Chart path.** `charts/gitlab/{pre,postgresql,redis,minio,gitlab,post}/` (no top-level `install/` — the main workload chart lives in `gitlab/`).
- **Install playbook.** `gitlab-install.yaml`.
- **Namespace.** `gitlab`.
- **Releases.** `gitlab-pre`, `gitlab-postgresql`, `gitlab-redis`, `gitlab-minio`, `gitlab`, `gitlab-post`.
- **Required vars.** `gitlab_namespace`, `gitlab_version`, per-sibling (`gitlab_postgresql_*`, `gitlab_redis_*`, `gitlab_minio_*`) storage class + size + tolerations/nodeSelector/resources + credentials via ESO. Domain vars (`gitlab_domain`, `gitlab_registry_domain`).
- **ESO integration.** Yes — Postgres password, Redis password, MinIO root + registry creds, GitLab root password, optional PAT tokens.
- **ServiceMonitor.** Yes.
- **Dependencies.** Cilium, cert-manager, external-secrets, vault, traefik, longhorn.
- **Image registry overrides.** `gitlab_image_registry`, `gitlab_postgresql_image_registry`, `gitlab_redis_image_registry`, `gitlab_minio_image_registry`.
- **Non-install playbooks.** `gitlab-configure.yaml` (rotate root password, regenerate PAT, etc.).
- **Notes.** Uses `tasks-helm-upgrade-async.yaml` for the main `gitlab` release (synchronous Ansible command times out on the multi-release GitLab chart).

## 12. `gitlab-runner`

- **Chart path.** `charts/gitlab-runner/{pre,install}/` (no `post/`).
- **Install playbook.** `gitlab-runner-install.yaml`.
- **Namespace.** `gitlab-runner` (separate from `gitlab` — runners can scale independently).
- **Releases.** `gitlab-runner-pre`, `gitlab-runner`.
- **Required vars.** `gitlab_runner_namespace`, `gitlab_runner_version`, `gitlab_runner_helm_values`, tolerations/nodeSelector/resources.
- **ESO integration.** Yes — registration token + S3 cache creds.
- **ServiceMonitor.** No (runner itself doesn't expose metrics worth scraping).
- **Dependencies.** `gitlab` (for runner registration token).
- **Image registry overrides.** `gitlab_runner_image_registry`.

## 13. `zitadel`

- **Chart path.** `charts/zitadel/{pre,postgresql,install,post}/`.
- **Install playbook.** `zitadel-install.yaml`.
- **Namespace.** `zitadel`.
- **Releases.** `zitadel-pre`, `zitadel-postgresql`, `zitadel`, `zitadel-post`.
- **Required vars.** `zitadel_namespace`, `zitadel_version`, `zitadel_postgresql_*` (storage, creds via ESO), `zitadel_domain`, `zitadel_masterkey` (in Vault via ESO).
- **ESO integration.** Yes — Postgres password, `masterkey`.
- **ServiceMonitor.** Yes.
- **Dependencies.** Cilium, cert-manager, external-secrets, vault, traefik, longhorn.
- **Image registry overrides.** `zitadel_image_registry`, `zitadel_postgresql_image_registry`.

## 14. `teleport`

- **Chart path.** `charts/teleport/{pre,install,post,configure}/`.
- **Install playbook.** `teleport-install.yaml` + companion `teleport-ssh-agent-install.yaml` (non-k8s, installs the Teleport SSH agent as a systemd unit on arbitrary hosts).
- **Namespace.** `teleport`.
- **Releases.** `teleport-pre`, `teleport`, `teleport-post`, `teleport-configure`.
- **Required vars.** `teleport_namespace`, `teleport_version`, `teleport_cluster_name`, `teleport_proxy_domain`, etc. All declarative resources in `hosts-vars/teleport-configure.yaml` (each as `teleport_configure_<resource>` + `_extra`): `roles`, `users`, `bots`, `apps`, `databases`, `oidc`, `saml`, `access_lists`, `trusted_clusters`.
- **ESO integration.** No.
- **ServiceMonitor.** Yes.
- **Dependencies.** Cilium, cert-manager, traefik.
- **Image registry overrides.** `teleport_image_registry`.
- **Notes.** `configure/` phase runs after the server is up and applies the declarative resource list.

## 15. `medik8s`

- **Chart path.** `charts/medik8s/{pre,install,post}/`.
- **Install playbook.** `medik8s-install.yaml`.
- **Namespace.** `medik8s`.
- **Releases.** `medik8s-pre`, `medik8s`, `medik8s-post`.
- **Required vars.** `medik8s_namespace`, `medik8s_version`, NHC (NodeHealthCheck) + SNR (Self-Node-Remediation) config.
- **ESO integration.** No.
- **Dependencies.** Cilium. Kernel module `softdog` enabled on nodes via `playbook-system/server-prepare.yaml` (blacklist-aware — uses a unit-service `modprobe` strategy rather than dropping a file in `/etc/modules-load.d/`).
- **Notes.** NodeHealthCheck operator + Self-Node-Remediation for hardware-failure auto-healing.

## 16. `metrics-server`

- **Chart path.** `charts/metrics-server/{pre,install}/` (no `post/`).
- **Install playbook.** `metrics-server-install.yaml`.
- **Namespace.** `kube-system`. Exceptional — uses `kube-system` because metrics-server is a cluster-scoped API aggregator. The `tasks-forbid-kube-system` guard is bypassed inside this playbook specifically.
- **Releases.** `metrics-server-pre`, `metrics-server`.
- **Required vars.** `metrics_server_version`, tolerations/nodeSelector/resources.
- **ESO integration.** No.
- **ServiceMonitor.** No.
- **Dependencies.** Cilium.
- **Image registry overrides.** `metrics_server_image_registry`.

## 17. `mon-prometheus-operator`

- **Chart path.** `charts/mon-prometheus-operator/{crds,pre,install,prometheus,alertmanager,post}/`.
- **Install playbook.** `mon-prometheus-operator-install.yaml`.
- **Namespace.** `mon` (value of `prometheus_operator_namespace`).
- **Releases.** `mon-prometheus-operator-crds`, `mon-prometheus-operator-pre`, `mon-prometheus-operator`, `mon-prometheus-operator-prometheus` (Prometheus CR), `mon-prometheus-operator-alertmanager` (Alertmanager CR), `mon-prometheus-operator-post`.
- **Required vars.** `prometheus_operator_namespace` (`mon`), `prometheus_operator_version`, Prometheus CR config (retention, storage class, selectors, `additionalScrapeConfigs`), Alertmanager CR config (routes, receivers), tolerations/nodeSelector/resources per CR.
- **ESO integration.** No (but depends on ESO being present — Grafana joins the stack and uses ESO).
- **ServiceMonitor.** Yes — operator self-monitors.
- **Dependencies.** Cilium, cert-manager, longhorn (for Prometheus PVC on `lh-major-single-best-effort`).
- **Image registry overrides.** `prometheus_operator_image_registry` (per sub-image).
- **Notes.** ServiceMonitor selector is cluster-wide — discovers SMs in any namespace.

## 18. `mon-grafana`

- **Chart path.** `charts/mon-grafana/{pre,install,post}/`.
- **Install playbook.** `mon-grafana-install.yaml`.
- **Namespace.** `grafana`.
- **Releases.** `mon-grafana-pre`, `mon-grafana`, `mon-grafana-post`.
- **Required vars.** `grafana_namespace` (`grafana`), `grafana_version`, `grafana_domain`, datasources list, dashboards list, OIDC config (Zitadel).
- **ESO integration.** Yes — admin password, OIDC client-secret, datasource credentials.
- **ServiceMonitor.** Yes.
- **Dependencies.** Cilium, cert-manager, external-secrets, vault, traefik, zitadel (for OIDC), mon-prometheus-operator.
- **Image registry overrides.** `grafana_image_registry`.

## 19. `mon-kube-state-metrics`

- **Chart path.** `charts/mon-kube-state-metrics/{pre,install,post}/`.
- **Install playbook.** `mon-kube-state-metrics-install.yaml`.
- **Namespace.** `mon`.
- **Releases.** `mon-kube-state-metrics-pre`, `mon-kube-state-metrics`, `mon-kube-state-metrics-post`.
- **Required vars.** `kube_state_metrics_namespace` (`mon`), `kube_state_metrics_version`, tolerations/nodeSelector/resources.
- **ESO integration.** No.
- **ServiceMonitor.** Yes.
- **Dependencies.** mon-prometheus-operator.
- **Image registry overrides.** `kube_state_metrics_image_registry`.

## 20. `mon-node-exporter`

- **Chart path.** `charts/mon-node-exporter/{pre,install,post}/`.
- **Install playbook.** `mon-node-exporter-install.yaml`.
- **Namespace.** `mon`.
- **Releases.** `mon-node-exporter-pre`, `mon-node-exporter`, `mon-node-exporter-post`.
- **Required vars.** `node_exporter_namespace` (`mon`), `node_exporter_version`, DaemonSet tolerations `[{operator: "Exists"}]`.
- **ESO integration.** No.
- **ServiceMonitor.** Yes.
- **Dependencies.** mon-prometheus-operator.
- **Image registry overrides.** `node_exporter_image_registry`.
- **Notes.** DaemonSet on every node including managers.

---

## 21. Namespaces Matrix

| Namespace | Owners | Fixed by upstream? |
|---|---|---|
| `cilium` | cilium | no |
| `cert-manager` | cert-manager | no |
| `external-secrets` | external-secrets | no |
| `vault` | vault | no |
| `haproxy-lb` | haproxy | no |
| `traefik-lb` | traefik | no |
| `longhorn-system` | longhorn, longhorn-s3-restore | **yes** — cannot rename |
| `argocd` | argocd, argocd-git-ops | **yes** — cannot rename |
| `gitlab` | gitlab | no |
| `gitlab-runner` | gitlab-runner | no |
| `zitadel` | zitadel | no |
| `teleport` | teleport | no |
| `medik8s` | medik8s | no |
| `kube-system` | metrics-server (exceptional) | upstream |
| `mon` | mon-prometheus-operator, mon-kube-state-metrics, mon-node-exporter | no |
| `grafana` | mon-grafana | no |

## 22. Cross-cutting Dependency Order

Install in roughly this order (first → last). Parallel installation within a dependency tier is safe.

```
L0  cilium
L1  cert-manager   external-secrets
L2  longhorn       metrics-server
L3  vault
L4  traefik        haproxy
L5  mon-prometheus-operator
L6  mon-node-exporter   mon-kube-state-metrics
L7  zitadel
L8  mon-grafana    argocd    gitlab    teleport    medik8s
L9  argocd-git-ops    gitlab-runner
```

## 23. ESO-integrated Components (9)

Only these have `eso_vault_integration_<c>` objects and are validated by `tasks-eso-merge.yaml`:

`traefik`, `haproxy`, `longhorn`, `gitlab`, `gitlab_runner`, `zitadel`, `argocd`, `argocd_git_ops`, `grafana`

See [`secrets-and-eso.md`](secrets-and-eso.md) for the per-component secret paths and `SecretStore` layout.

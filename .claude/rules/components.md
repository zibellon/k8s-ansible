# Components — Per-component Reference

One strict template per component. For the 3-phase install pattern in general, see `CLAUDE.md` §4 and [`playbook-conventions.md`](playbook-conventions.md). For ESO details, see [`secrets-and-eso.md`](secrets-and-eso.md).

Template fields:

- **Chart path** — `playbook-app/charts/<c>/` subdirectories (phase dirs).
- **Install playbook** — `playbook-app/<c>-install.yaml`.
- **Namespace** — K8s namespace (and whether it is fixed by upstream).
- **Releases** — Helm release names deployed by the install playbook.
- **External Helm repo** — if the install phase uses an upstream chart, the chart source: HTTP repo URL (`helm repo add` URL + chart name within) **or** full OCI chart URL (`oci://...`). Switchable per component via `<c>_helm_is_oci`. See [`reusable-tasks.md`](reusable-tasks.md) §1.5 for the unified `tasks-add-helm-repo.yaml` contract.
- **Required vars** — key knobs from the component's `hosts-vars/<c>.yaml` (full suffix list in [`variables.md`](variables.md) §1).
- **ESO integration** — `yes/no`; if yes, the `eso_vault_integration_<c>` object points to which Vault paths.
- **ServiceMonitor** — whether the post phase creates one.
- **Dependencies** — components that must be installed first.
- **Non-install playbooks** — companion plays (`-configure`, `-restart`, `-rotate`, sync helpers).

---

## 1. `cilium`

- **Chart path.** `charts/cilium/{pre,install,post}/`.
- **Install playbook.** `cilium-install.yaml`.
- **Namespace.** `cilium`.
- **Releases.** `cilium-pre`, `cilium`, `cilium-post`.
- **External Helm repo.** `https://helm.cilium.io/` → chart `cilium/cilium`, version `cilium_helm_chart_version` (matches `cilium_version`, default `1.19.1`). HTTP↔OCI switchable via `cilium_helm_is_oci`.
- **Required vars.** `cilium_version`, `cilium_mask_size` (21), `cilium_helm_values` (large dict — `kubeProxyReplacement: true`, `k8sServiceHost`, `k8sServicePort`, etc.), per-sub-component tolerations/nodeSelector/resources for `agent`, `operator`, `envoy`, `hubble_relay`, `hubble_ui`, `hubble_ui_domain`. Kustomize patches (default `[]`): `cilium_pre_kustomize_patches`, `cilium_post_kustomize_patches`.
- **ESO integration.** No.
- **ServiceMonitor.** Yes — per sub-component (`cilium_agent_service_monitor_enabled`, `hubble_service_monitor_enabled`, etc.).
- **Dependencies.** None (installed first, before any other app). Must run BEFORE each node join: `--tags post` regenerates the host-firewall policy (`CiliumClusterwideNetworkPolicy`) with the new node's IPs.
- **Non-install playbooks.** `cilium-restart.yaml` (rollout-restart agent DaemonSet, operator Deployment, envoy DS, Hubble components).
- **Notes.** Deployed as DaemonSet with `tolerations: [{operator: "Exists"}]` — runs on every node including tainted ones. `kube-proxy` is never installed — Cilium replaces it; the kubeadm template sets `proxy.disabled: true` in `ClusterConfiguration` so the addon is never deployed.

## 2. `cert-manager`

- **Chart path.** `charts/cert-manager/{pre,install,post}/`.
- **Install playbook.** `cert-manager-install.yaml`.
- **Namespace.** `cert-manager`.
- **Releases.** `cert-manager-pre`, `cert-manager`, `cert-manager-post`.
- **External Helm repo.** `https://charts.jetstack.io` → chart `jetstack/cert-manager`, version `{{ cert_manager_helm_chart_version }}` (default `v1.20.2`; `v` префикс хранится в значении переменной — единая нормализация). HTTP↔OCI switchable via `cert_manager_helm_is_oci`.
- **Required vars.** `cert_manager_namespace`, `cert_manager_helm_chart_version`, plus per-sub-component (`cert_manager_`, `cert_manager_cainjector_`, `cert_manager_webhook_`) tolerations/nodeSelector/affinity/resources. Global `cert_manager_cluster_issuers` (list of raw `{name, spec}` — `spec` is the verbatim `ClusterIssuer` spec). Kustomize patches (default `[]`): `cert_manager_pre_kustomize_patches`, `cert_manager_post_kustomize_patches`.
- **ESO integration.** No.
- **ServiceMonitor.** Yes.
- **Dependencies.** Cilium (CNI). Traefik (if using HTTP-01).
- **Non-install playbooks.** None.
- **Notes.** `cert_manager_cluster_issuers` provides cluster-wide raw `ClusterIssuer` resources as operator infrastructure — standard ingress components no longer consume it; each defines its own namespaced `Issuer` via `<c>_cert_manager_issuer` (see [`networking.md`](networking.md) §4).

## 3. `external-secrets`

- **Chart path.** `charts/external-secrets/{pre,install,post}/`.
- **Install playbook.** `external-secrets-install.yaml`.
- **Namespace.** `external-secrets`.
- **Releases.** `external-secrets-pre`, `external-secrets`, `external-secrets-post`.
- **External Helm repo.** `https://charts.external-secrets.io` → chart `external-secrets/external-secrets`, version `external_secrets_helm_chart_version` (default `2.3.0`). HTTP↔OCI switchable via `external_secrets_helm_is_oci`.
- **Required vars.** `external_secrets_namespace`, `external_secrets_helm_chart_version`, per-sub-component tolerations/nodeSelector/affinity/resources for `controller`, `webhook`, `cert_controller`. Kustomize patches (default `[]`): `external_secrets_pre_kustomize_patches`.
- **ESO integration.** No (it *is* ESO).
- **ServiceMonitor.** Yes.
- **Dependencies.** Cilium, cert-manager.
- **Non-install playbooks.** `external-secrets-restart.yaml`.

## 4. `vault`

- **Chart path.** `charts/vault/{pre,install,cr,post}/`.
- **Install playbook.** `vault-install.yaml`.
- **Namespace.** `vault`.
- **Releases.** `vault-pre`, `vault` (bank-vaults operator), `vault-cr` (Vault Custom Resource), `vault-post`.
- **External Helm repo.** OCI: `oci://ghcr.io/bank-vaults/helm-charts/vault-operator` (bank-vaults operator chart), version `vault_operator_helm_chart_version` (default `1.23.4`). OCI-only via `vault_operator_helm_is_oci=true`. **Note:** the HashiCorp Vault chart itself is embedded locally in `charts/vault/`; only the bank-vaults operator is from external OCI.
- **Required vars.** `vault_namespace`, `vault_image` (Vault server image — full URI:tag), `vault_operator_helm_chart_version` (bank-vaults operator chart), `vault_storage_class`, `vault_storage_size`, `vault_key_shares` (3), `vault_key_threshold` (2), `vault_policies` / `_extra`, `vault_roles` / `_extra`, `vault_creds_host_path`. Kustomize patches (default `[]`): `vault_pre_kustomize_patches`, `vault_cr_kustomize_patches`, `vault_post_kustomize_patches`.
- **ESO integration.** No (Vault is ESO's **source**, not a consumer).
- **ServiceMonitor.** Yes.
- **Dependencies.** Cilium, cert-manager, external-secrets (ESO before Vault so SecretStores + ExternalSecrets can resolve as Vault comes up), longhorn (for PVC storage class).
- **Non-install playbooks.** `vault-rotate.yaml` — rekey unseal shares + rotate root token. Uses state files (see `bootstrap-and-ha.md`).
- **Notes.** Unseal creds live at `/etc/kubernetes/vault-unseal.json` on every manager (mode 0600). Distributed to new managers at `manager-join.yaml` via `tasks-vault-distribute-creds.yaml`. Two KV engines mounted: `secret/` (admin use), `eso-secret/` (ESO read-only consumption).

## 5. `haproxy`

- **Chart path.** `charts/haproxy/{pre,install,post}/`.
- **Install playbook.** `haproxy-install.yaml`.
- **Namespace.** `haproxy-lb`.
- **Releases.** `haproxy-pre`, `haproxy`, `haproxy-post`.
- **External Helm repo.** `https://haproxytech.github.io/helm-charts` → chart `haproxytech/kubernetes-ingress`, version `haproxy_helm_chart_version` (default `1.49.0`). HTTP↔OCI switchable via `haproxy_helm_is_oci`.
- **Required vars.** `haproxy_namespace`, `haproxy_helm_values`, tolerations/nodeSelector/resources, TLS/ingress vars. Kustomize patches (default `[]`): `haproxy_pre_kustomize_patches`, `haproxy_post_kustomize_patches`.
- **ESO integration.** Yes (via `eso_vault_integration_haproxy` in `hosts-vars/haproxy.yaml`; base `_secrets` empty — users fill via `_extra`).
- **ServiceMonitor.** Yes.
- **Dependencies.** Cilium, cert-manager, external-secrets, vault, traefik.
- **Non-install playbooks.** `haproxy-restart.yaml`.
- **Notes.** This is the **in-cluster** HAProxy ingress — NOT to be confused with the systemd-level apiserver LB in `playbook-system/haproxy-apiserver-lb.yaml`.

## 6. `traefik`

- **Chart path.** `charts/traefik/{pre,install,post}/`.
- **Install playbook.** `traefik-install.yaml`.
- **Namespace.** `traefik-lb` (NOT `traefik`).
- **Releases.** `traefik-pre`, `traefik`, `traefik-post`.
- **External Helm repo.** `https://traefik.github.io/charts` → chart `traefik/traefik`, version `traefik_helm_chart_version` (default `39.0.5`, app version `v3.6.2`). HTTP↔OCI switchable via `traefik_helm_is_oci`.
- **Required vars.** `traefik_namespace`, `traefik_version`, `traefik_helm_chart_version`, `traefik_web_entrypoint`, `traefik_websecure_entrypoint`, `traefik_prometheus_port` (9200), `traefik_dashboard_domain`, DaemonSet tolerations `[{operator: "Exists"}]`. Kustomize patches (default `[]`): `traefik_pre_kustomize_patches`, `traefik_post_kustomize_patches`.
- **ESO integration.** Yes (via `eso_vault_integration_traefik` in `hosts-vars/traefik.yaml`; base `_secrets` empty — users add via `_extra` for custom TLS / basic-auth).
- **ServiceMonitor.** Yes.
- **Dependencies.** Cilium, cert-manager.
- **Non-install playbooks.** `traefik-restart.yaml`.
- **Notes.** Ingress class is `traefik-lb`, **not** `traefik`. `post/` creates middlewares: `vpn-only` (ipAllowList from `vpn_ips`), `http-to-https`, `http-www-to-https`.

## 7. `longhorn`

- **Chart path.** `charts/longhorn/{pre,install,post}/`.
- **Install playbook.** `longhorn-install.yaml`.
- **Namespace.** `longhorn-system` — **fixed upstream, cannot rename**.
- **Releases.** `longhorn-pre`, `longhorn`, `longhorn-post`.
- **External Helm repo.** `https://charts.longhorn.io` → chart `longhorn/longhorn`, version `longhorn_helm_chart_version` (default `1.11.1`). HTTP↔OCI switchable via `longhorn_helm_is_oci`.
- **Required vars.** `longhorn_namespace`, `longhorn_helm_chart_version`, `longhorn_storage_classes` (list — empty by default; populate in overrides), `longhorn_helm_values`, tolerations/nodeSelector/resources. Kustomize patches (default `[]`): `longhorn_pre_kustomize_patches`, `longhorn_post_kustomize_patches`.
- **ESO integration.** Yes (via `eso_vault_integration_longhorn` in `hosts-vars/longhorn.yaml`; base `_secrets` empty — S3 backup creds added via `_extra`).
- **ServiceMonitor.** Yes.
- **Dependencies.** Cilium, cert-manager, external-secrets, vault. Node prep via `playbook-system/longhorn-prepare.yaml` (kernel modules `iscsi_tcp`, `dm_crypt`; packages `open-iscsi`, `nfs-common`, `cryptsetup`, `dmsetup`).
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

- **Chart path.** `charts/argocd/{crds,gitops,install,post,pre}/`.
- **Install playbook.** `argocd-install.yaml`.
- **Namespace.** `argocd` (default; configurable via `argocd_namespace` — namespace handled by `helm template --namespace` при render'е chart templates, см. [`playbook-conventions.md`](playbook-conventions.md) §21).
- **Releases.** `argocd-crds`, `argocd-pre`, `argocd`, `argocd-post`, `argocd-gitops`.
- **External Helm repo.** No — local chart with kustomize render of pristine upstream `install.yaml` on master_manager_fact before helm install (see [`playbook-conventions.md`](playbook-conventions.md) §21).
- **Required vars.** `argocd_namespace`, `argocd_ui_domain`, `argocd_rpc_domain`, `argocd_external_url`, `argocd_ingress_class_name` (`traefik-lb`), `argocd_cert_manager_issuer` (object `{enabled, body}`). Kustomize patches (default `[]`): `argocd_pre_kustomize_patches`, `argocd_install_kustomize_patches` (strategic merge patches на argocd-cm и argocd-cmd-params-cm с пользовательскими customization'ами), `argocd_post_kustomize_patches`, `argocd_gitops_kustomize_patches`.
- **ESO integration.** Yes (via `eso_vault_integration_argocd` in `hosts-vars/argocd.yaml`; admin password + git-ops repo credentials). The same `_secrets` list carries both types: plain admin-password entries and git-ops repo entries (which set `body.target.template.metadata.labels: argocd.argoproj.io/secret-type: repo-creds` or `repository` to let ArgoCD recognise them as repository credentials).
- **ServiceMonitor.** Yes.
- **Dependencies.** Cilium, cert-manager, external-secrets, vault, traefik.
- **Non-install playbooks.** `argocd-configure.yaml` (one-off admin-password resolve/rotate + validation against ArgoCD API), `argocd-restart.yaml`.
- **Notes.** Install phase renders pristine upstream `install.yaml` через kustomize (`argocd_install_kustomize_patches`) на master_manager_fact перед helm install — см. [`playbook-conventions.md`](playbook-conventions.md) §21. 7 ConfigMaps из upstream (`argocd-cm`, `argocd-cmd-params-cm`, `argocd-gpg-keys-cm`, `argocd-notifications-cm`, `argocd-rbac-cm`, `argocd-ssh-known-hosts-cm`, `argocd-tls-certs-cm`) принадлежат Helm release `argocd` (не `argocd-pre`); customization через strategic merge patches сохраняет upstream defaults автоматически. The `argocd-install.yaml` playbook ships with an additional `[gitops]` tag that runs after `[post]` and creates AppProject + Application(s) from `argocd_git_ops_apps` using `charts/argocd/gitops/` (separate Helm release `argocd-gitops` in the same namespace).

## 11. `gitlab`

- **Chart path.** `charts/gitlab/{pre,postgresql,redis,gitlab,post}/` (no top-level `install/` — the main workload chart lives in `gitlab/`).
- **Install playbook.** `gitlab-install.yaml`.
- **Namespace.** `gitlab`.
- **Releases.** `gitlab-pre`, `gitlab-postgresql`, `gitlab-redis`, `gitlab`, `gitlab-post`.
- **External Helm repo.** `https://charts.gitlab.io` → chart `gitlab/gitlab`, version `gitlab_helm_chart_version` (default `8.11.8`, GitLab 17.11). HTTP↔OCI switchable via `gitlab_helm_is_oci`.
- **Required vars.** `gitlab_namespace`, `gitlab_helm_chart_version`, per-sibling (`gitlab_postgresql_*`, `gitlab_redis_*`) storage class + size + tolerations/nodeSelector/resources + credentials via ESO + per-sibling image tags. Postgres credentials parametrized via `gitlab_postgresql_username`, `gitlab_postgresql_database_name`, `gitlab_postgresql_secret_key_username`, `gitlab_postgresql_secret_key_password` (chart `gitlab/postgresql/` consumes them through `credentials:` nested block + `databaseName:` field in values). Domain vars (`gitlab_domain`, `gitlab_registry_domain`). S3 backend config (replaces MinIO sub-chart): `gitlab_s3_endpoint`, `gitlab_s3_region`, `gitlab_s3_path_style`, 5 bucket name vars (`gitlab_registry_bucket` / `_artifacts_bucket` / `_uploads_bucket` / `_packages_bucket` / `_backups_bucket`), Vault field name vars (`gitlab_s3_secret_key_username` / `_access_key` / `_secret_key`, values `"username"` / `"accessKey"` / `"secretKey"` — standardized identity-distribute Layer 3 fixed keys). Kustomize patches (default `[]`): `gitlab_pre_kustomize_patches`, `gitlab_postgresql_kustomize_patches`, `gitlab_redis_kustomize_patches`, `gitlab_post_kustomize_patches`.
- **ESO integration.** Yes (via `eso_vault_integration_gitlab` in `hosts-vars/gitlab.yaml`) — Postgres password, Redis password, S3 storage creds (single path `/gitlab/s3-storage`, fields `username`/`accessKey`/`secretKey` standardized identity-distribute Layer 3), GitLab root password, optional PAT tokens. Complex secrets (registry connection YAML for GitLab registry sub-chart, backup s3 connection YAML for backups) use `body.target.template.data.*` with ESO template placeholders wrapped in `{% raw %}...{% endraw %}`. S3 creds provisioning — две альтернативы: (A) SeaweedFS sync (opt-in template в `hosts-vars/seaweedfs-sync.yaml` SECTION 1+2 commented blocks); (B) cloud S3 — operator вручную `vault kv put <path>` для тех же resolved paths. GitLab playbook (`gitlab-install.yaml`) валидирует S3 creds существование в Vault fail-fast перед install.
- **ServiceMonitor.** Yes.
- **Dependencies.** Cilium, cert-manager, external-secrets, vault, traefik, longhorn.
- **Non-install playbooks.** `gitlab-configure.yaml` (rotate root password, regenerate PAT, etc.).
- **Notes.** Uses `tasks-helm-upgrade-async.yaml` for the main `gitlab` release (synchronous Ansible command times out on the multi-release GitLab chart). Cross-ns NP к SeaweedFS S3 backend (`allow-seaweedfs-s3` egress в gitlab ns + `gitlab-allow-seaweedfs-s3` ingress в seaweedfs ns) — hardcoded в `gitlab/pre/templates/network-policy.yaml`, без opt-in переменной; SeaweedFS — invariant L5 dependency для GitLab L7 deployment'а; см. [`networking.md`](networking.md) §8.

## 12. `gitlab-runner`

- **Chart path.** `charts/gitlab-runner/{pre,install}/` (no `post/`).
- **Install playbook.** `gitlab-runner-install.yaml`.
- **Namespace.** `gitlab-runner` (separate from `gitlab` — runners can scale independently).
- **Releases.** `gitlab-runner-pre`, `gitlab-runner`.
- **External Helm repo.** `https://charts.gitlab.io` → chart `gitlab/gitlab-runner`, version `gitlab_runner_helm_chart_version` (default `0.78.0`, gitlab-runner 17.11). HTTP↔OCI switchable via `gitlab_runner_helm_is_oci`.
- **Required vars.** `gitlab_runner_namespace`, `gitlab_runner_helm_chart_version`, `gitlab_runner_helper_image`, `gitlab_runner_dind_image`, `gitlab_runner_dind_dind_image`, `gitlab_runner_helm_values`, tolerations/nodeSelector/resources. Kustomize patches (default `[]`): `gitlab_runner_pre_kustomize_patches`.
- **ESO integration.** Yes (via `eso_vault_integration_gitlab_runner` in `hosts-vars/gitlab-runner.yaml`) — registration token + S3 cache creds. The runner-token secret uses `body.target.template.data.*` with ESO template placeholders wrapped in `{% raw %}...{% endraw %}`.
- **ServiceMonitor.** No (runner itself doesn't expose metrics worth scraping).
- **Dependencies.** `gitlab` (for runner registration token).
- **Notes.** Cross-ns NP к SeaweedFS S3 backend (`To SeaweedFS S3` egress entries в `allow-gitlab-runner` + `allow-job-pod` NPs + `gitlab-runner-allow-seaweedfs-s3` ingress в seaweedfs ns с двумя `from` entries — runner pods + job pods) — hardcoded в `gitlab-runner/pre/templates/network-policy.yaml`, без opt-in переменной; SeaweedFS — invariant L5 dependency; см. [`networking.md`](networking.md) §8.

## 13. `zitadel`

- **Chart path.** `charts/zitadel/{pre,postgresql,install,post}/`.
- **Install playbook.** `zitadel-install.yaml`.
- **Namespace.** `zitadel`.
- **Releases.** `zitadel-pre`, `zitadel-postgresql`, `zitadel`, `zitadel-post`.
- **External Helm repo.** `https://charts.zitadel.com` → chart `zitadel/zitadel`, version `zitadel_helm_chart_version` (default `9.30.0`). HTTP↔OCI switchable via `zitadel_helm_is_oci`.
- **Required vars.** `zitadel_namespace`, `zitadel_helm_chart_version`, `zitadel_postgresql_image` (full URI:tag), `zitadel_postgresql_*` (storage, creds via ESO), `zitadel_domain`, `zitadel_masterkey` (in Vault via ESO). Postgres credentials parametrized via `zitadel_postgresql_username`, `zitadel_postgresql_database_name`, `zitadel_postgresql_secret_key_username`, `zitadel_postgresql_secret_key_password` (chart `zitadel/postgresql/` consumes them through `credentials:` nested block; main ZITADEL chart references them in `configmapConfig.Database.Postgres` + env secretKeyRef.key). Kustomize patches (default `[]`): `zitadel_pre_kustomize_patches`, `zitadel_postgresql_kustomize_patches`, `zitadel_post_kustomize_patches`.
- **ESO integration.** Yes (via `eso_vault_integration_zitadel` in `hosts-vars/zitadel.yaml`) — Postgres password, `masterkey`.
- **ServiceMonitor.** Yes.
- **Dependencies.** Cilium, cert-manager, external-secrets, vault, traefik, longhorn.

## 14. `teleport`

- **Chart path.** `charts/teleport/{pre,install,post,configure}/`.
- **Install playbook.** `teleport-install.yaml` + companion `teleport-ssh-agent-install.yaml` (non-k8s, installs the Teleport SSH agent as a systemd unit on arbitrary hosts).
- **Namespace.** `teleport`.
- **Releases.** `teleport-pre`, `teleport`, `teleport-post`, `teleport-configure`.
- **External Helm repo.** `https://charts.releases.teleport.dev` → chart `teleport/teleport-cluster`, version `teleport_helm_chart_version` (default `18.7.2`). HTTP↔OCI switchable via `teleport_helm_is_oci`.
- **Required vars.** `teleport_namespace`, `teleport_helm_chart_version` (image versions auto-set by chart `appVersion`), `teleport_cluster_name`, `teleport_proxy_domain`, etc. All declarative resources in `hosts-vars/teleport-configure.yaml` (each as `teleport_configure_<resource>` + `_extra`): `roles`, `users`, `bots`, `apps`, `databases`, `oidc`, `saml`, `access_lists`, `trusted_clusters`. Kustomize patches (default `[]`): `teleport_pre_kustomize_patches`, `teleport_post_kustomize_patches`, `teleport_configure_kustomize_patches`.
- **ESO integration.** No.
- **ServiceMonitor.** Yes.
- **Dependencies.** Cilium, cert-manager, traefik.
- **Notes.** `configure/` phase runs after the server is up and applies the declarative resource list.

## 16. `metrics-server`

- **Chart path.** `charts/metrics-server/{pre,install}/` (no `post/`).
- **Install playbook.** `metrics-server-install.yaml`.
- **Namespace.** `metrics-server`.
- **Releases.** `metrics-server-pre`, `metrics-server`.
- **External Helm repo.** `https://kubernetes-sigs.github.io/metrics-server/` → chart `metrics-server/metrics-server`, version `metrics_server_helm_chart_version` (default `3.13.0`). HTTP↔OCI switchable via `metrics_server_helm_is_oci`.
- **Required vars.** `metrics_server_helm_chart_version`, tolerations/nodeSelector/resources. Kustomize patches (default `[]`): `metrics_server_pre_kustomize_patches`.
- **ESO integration.** No.
- **ServiceMonitor.** No.
- **Dependencies.** Cilium.

## 16.5 `linstor`

- **Chart path.** `charts/linstor/{pre,install-operator,install-cluster,post}/`.
- **Install playbook.** `linstor-install.yaml`.
- **Namespace.** `piraeus-datastore` (upstream Piraeus convention — не переименовываем).
- **Releases.** `linstor-pre`, `piraeus-operator`, `linstor-cluster`, `linstor-post`.
- **External Helm repos.** **Два OCI chart'a:**
  - `oci://ghcr.io/piraeusdatastore/piraeus-operator/piraeus`, version `piraeus_operator_helm_chart_version` (default `2.10.6`) — Piraeus operator (управляющий).
  - `oci://ghcr.io/piraeusdatastore/helm-charts/linstor-cluster`, version `linstor_cluster_helm_chart_version` (default `1.1.1`) — Datastore (`LinstorCluster` + `LinstorSatelliteConfiguration` + `LinstorNodeConnection` + monitoring + StorageClasses).
- **Required vars.** `linstor_namespace`, `linstor_rollout_timeout`, `linstor_pre_helm_timeout`, `piraeus_operator_helm_*` (chart vars для operator), `linstor_cluster_helm_*` (chart vars для cluster), `linstor_pre_helm_values`, `piraeus_operator_helm_values` (`installCRDs: true`, `tls.autogenerate`, `tls.renew`), `linstor_cluster_helm_values` (включает `linstorCluster.tolerations: [{operator: Exists}]`, `linstorCluster.properties` с 11 entries — 4 DrbdOptions/PeerDevice/c-* для sync rate tuning + 7 durability/quorum: `DrbdOptions/Net/data-integrity-alg`, `DrbdOptions/Net/verify-alg`, `DrbdOptions/Resource/quorum`, `DrbdOptions/Resource/on-no-quorum`, `DrbdOptions/Resource/on-suspended-primary-outdated`, `DrbdOptions/Resource/on-no-data-accessible`, `DrbdOptions/auto-add-quorum-tiebreaker`, `linstorSatelliteConfigurations` с `fileThinPool` pools per tier, 6 `storageClasses`). Kustomize patches (default `[]`): `linstor_pre_kustomize_patches`, `linstor_post_kustomize_patches`.
- **ESO integration.** No.
- **ServiceMonitor.** Yes — через `linstor_cluster_helm_values.monitoring.enabled: true` (Piraeus operator деплоит свои ServiceMonitor resources). **Также** post phase добавляет custom ServiceMonitor (`linstor-controller`) + PodMonitor'ы (`linstor-satellite`, `linstor-affinity-controller`), параметризованные через `linstor_post_helm_values` (operator переопределяет dict целиком; по умолчанию все 3 monitor'а enabled, interval `30s`, scrapeTimeout `15s`). **Внимание оператору:** проверить отсутствие duplicate scrape jobs между piraeus embedded monitoring и post phase monitors.
- **Dependencies.** Cilium (CNI). Host prep через `playbook-system/linstor-prepare.yaml` (kernel-headers `linux-headers-$(uname -r)` + `apt-mark hold` + verify `/lib/modules/$(uname -r)/build` symlink) — Piraeus operator сам собирает DRBD module через kmod-loader Pod (init-container в satellite), на хосте `drbd-dkms` НЕ ставится.
- **Non-install playbooks.** `linstor-restart.yaml` (rollout-restart 8 workloads из `linstor_restart_resources`).
- **Notes.** 6 storageClasses (3 tier × 2 modes): tier prefix `lnstr-manager-*` (only managers), `lnstr-major-*` (cross-tier via multi-pool `"lnstr-file-thin-manager lnstr-file-thin-worker"`), `lnstr-worker-*` (only workers); modes `*-local` (replica=1, strict-local), `*-multi-sync` (replica=2 Protocol C). Tier filtering — через pool name per `LinstorSatelliteConfiguration` (Path B — единственный надёжный absolute-filter mechanism; `--replicas-on-same Aux/key=value` syntax не whitelisted на controller). DRBD sync rate tuning через namespace `DrbdOptions/PeerDevice/c-*` (не `Net/`, не `Disk/` — оба rejected с "not whitelisted" error). `fileThinPool` driver (sparse files на root FS — extra disk не требуется). Альтернатива Longhorn'у в L2 storage tier. DRBD durability stack (фиксирован в `linstor_cluster_helm_values.linstorCluster.properties`): `DrbdOptions/Net/data-integrity-alg: crc32c` (per-write end-to-end CRC, защита от silent network bit-flip) + `DrbdOptions/Net/verify-alg: crc32c` (alg для on-demand scrubbing) + `DrbdOptions/Resource/quorum: majority` + `DrbdOptions/Resource/on-no-quorum: suspend-io` + `DrbdOptions/Resource/on-suspended-primary-outdated: force-secondary` (авто-демоция устаревшего бывшего-Primary при возврате после failover) + `DrbdOptions/Resource/on-no-data-accessible: suspend-io` (заморозка I/O при полной потере доступа к данным вместо I/O-error) + `DrbdOptions/auto-add-quorum-tiebreaker: True` (split-brain protection для replica=2). `DrbdOptions/Resource/on-no-quorum` и `DrbdOptions/auto-add-quorum-tiebreaker` совпадают с дефолтами Piraeus operator'а, но зафиксированы явно в Helm values для защиты от изменения upstream-default'ов.

## 17. `mon-system`

Consolidated monitoring stack: Prometheus Operator + Prometheus + Alertmanager + Grafana + Loki + Vector + node-exporter + kube-state-metrics. All eight workloads share namespace `mon-system`, one inventory file, one chart tree, and one install playbook. Per-component enable flags gate each phase.

- **Chart path.** `charts/mon-system/{crds,pre,prometheus-operator,prometheus,alertmanager,node-exporter,ksm,loki,vector,grafana-postgresql,grafana,post}/` — 12 subdirs.
- **Install playbook.** `mon-system-install.yaml`.
- **Namespace.** `mon-system` (single).
- **Helm releases.** Eleven releases: `mon-system-pre`, `mon-system-prometheus-operator`, `mon-system-prometheus`, `mon-system-alertmanager`, `mon-system-node-exporter`, `mon-system-ksm`, `mon-system-loki`, `mon-system-vector`, `mon-system-grafana-postgresql`, `mon-system-grafana`, `mon-system-post`. Plus the `crds` phase which is deployed via `kubectl create -f` (not Helm) — same pattern as the legacy `mon-prometheus-operator/crds/` chart.
- **Tags.** `crds`, `pre`, `prometheus-operator`, `prometheus`, `alertmanager`, `node-exporter`, `ksm`, `loki`, `vector`, `grafana-postgresql`, `grafana`, `post`. Plus `always` for pre-checks and verification.
- **Per-component enable flags.** All boolean, default `true`:
  `mon_system_prometheus_operator_enabled`, `mon_system_prometheus_enabled`, `mon_system_alertmanager_enabled`, `mon_system_node_exporter_enabled`, `mon_system_ksm_enabled`, `mon_system_loki_enabled`, `mon_system_vector_enabled`, `mon_system_grafana_enabled`. Composite gate: if `mon_system_prometheus_operator_enabled: false`, both prometheus and alertmanager phases are skipped regardless of their own flags.
- **Required vars.** Single inventory file `hosts-vars/mon-system.yaml` (~950 lines) with unified `mon_system_<c>_*` prefix for all per-component primitives, plus 11 helm phase timeouts (`mon_system_<phase>_helm_timeout`), 9 helm-values dicts (`mon_system_<phase>_helm_values` and `mon_system_<c>_helm_values`), and the ESO integration block (see §20). Grafana-Postgres credentials parametrized via `mon_system_grafana_postgresql_username`, `mon_system_grafana_postgresql_database_name`, `mon_system_grafana_postgresql_secret_key_username`, `mon_system_grafana_postgresql_secret_key_password` (chart `mon-system/grafana-postgresql/` consumes them through `credentials:` nested block; the Grafana consumer chart references them in `mon_system_grafana_helm_values.database.{credentialsSecretName,usernameKey,passwordKey}` — both `GF_DATABASE_USER` and `GF_DATABASE_PASSWORD` flow through secretKeyRef). Block scalars: `mon_system_loki_config_yaml`, `mon_system_vector_config_yaml`, `mon_system_prometheus_spec`, `mon_system_alertmanager_spec`, `mon_system_alertmanager_root_config_spec`, `mon_system_prometheus_system_services` (list), `mon_system_prometheus_system_service_monitors` (list). Kustomize patches (default `[]`): `mon_system_pre_kustomize_patches`, `mon_system_prometheus_operator_kustomize_patches`, `mon_system_prometheus_kustomize_patches`, `mon_system_alertmanager_kustomize_patches`, `mon_system_node_exporter_kustomize_patches`, `mon_system_ksm_kustomize_patches`, `mon_system_loki_kustomize_patches`, `mon_system_vector_kustomize_patches`, `mon_system_grafana_postgresql_kustomize_patches`, `mon_system_grafana_kustomize_patches`, `mon_system_post_kustomize_patches`.
- **ESO integration.** Yes (single `eso_vault_integration_mon_system` object — only Grafana consumes ESO inside the namespace). See [`secrets-and-eso.md`](secrets-and-eso.md) for full contract.
- **ServiceMonitor.** Three SMs in `mon-system/post/` (loki, ksm, node-exporter), plus 6 system-component SMs (kube-apiserver, kubelet, kube-controller-manager, kube-scheduler, etcd, coredns) in `system-service-monitors.yaml` always-rendered. Vector by design has no SM (no metrics endpoint). Grafana and Prometheus-Operator self-SMs are not currently shipped.
- **Ingress + Certificate.** UI Ingresses for grafana, prometheus, alertmanager rendered in `post/` with composite gates (operator + per-UI flag for prometheus/alertmanager; just grafana flag for grafana). Per-UI VPN allow-list flags: `mon_system_<c>_vpn_only_enabled`.
- **Dependencies.** Cilium, cert-manager, external-secrets, vault (for grafana ESO), traefik (for UIs), longhorn (for Prometheus + grafana-postgresql + Loki PVCs), zitadel (optional — for grafana OIDC).
- **Non-install playbooks.** None.
- **Notes.** Prometheus-operator phase renders pristine upstream `prometheus-operator.yaml` через kustomize (`mon_system_prometheus_operator_kustomize_patches`) на master_manager_fact перед helm install — см. [`playbook-conventions.md`](playbook-conventions.md) §21. Single namespace eliminates the cross-namespace coupling that previously required: `vector-allow-loki` cross-ns NetworkPolicy in the `loki` namespace; `grafana-allow-prometheus` / `grafana-allow-alertmanager` cross-ns NetworkPolicies in the `mon` namespace; cross-ns Vector→Loki DNS endpoint. The consolidated NetworkPolicy in `mon-system/pre/` covers all intra-namespace traffic with a single `allow-internal-traffic` rule plus per-component egress rules (operator/ksm to apiserver, vector to apiserver:443, grafana external HTTP/HTTPS), and one cross-ns NetworkPolicy in `traefik-lb` for UI ingress.

---

## 17.5. `seaweedfs`

- **Chart path.** `charts/seaweedfs/{pre,postgresql,post,quota-cron}/` — четыре LOCAL_CUSTOM chart'а. Install phase — **upstream chart напрямую** (не локальный chart subdir).
- **Install playbook.** `seaweedfs-install.yaml` (содержит pre + postgresql + user-sync + identity-distribute + helm install + post + quota-cron + bucket-sync + verify).
- **Namespace.** `seaweedfs`.
- **Releases.** `seaweedfs-pre`, `seaweedfs-postgresql`, `seaweedfs` (upstream chart), `seaweedfs-post`, `seaweedfs-quota-cron`.
- **External Helm repo.** `https://seaweedfs.github.io/seaweedfs/helm` → chart `seaweedfs/seaweedfs`, version `seaweedfs_helm_chart_version` (default `4.29.0`). HTTP↔OCI switchable via `seaweedfs_helm_is_oci`.
- **Tags.** `pre`, `postgresql`, `user-sync`, `identity-distribute`, `install`, `post`, `quota-cron`, `bucket-sync` + `always` (pre-check + verify). Default запуск — все теги последовательно.
- **Required vars.** `seaweedfs_namespace`, `seaweedfs_s3_domain`, `seaweedfs_admin_ui_domain`, `seaweedfs_helm_chart_version`, `seaweedfs_postgresql_*` (image, storage class, size, creds field names), `seaweedfs_helm_values` (большой dict — master/volume/filer/s3 enabled + replicas: 3 для s3 HA + antiAffinity + nodeSelector + storage + filer postgres connection + s3.existingConfigSecret pointing на K8s Secret materialized by ESO), `seaweedfs_*_helm_values` для каждой фазы, `seaweedfs_cert_manager_issuer`, `seaweedfs_s3_ingress_config` + `seaweedfs_admin_ui_ingress_config`, `seaweedfs_service_monitor`, `seaweedfs_quota_cron_*` (enabled/schedule/image/helm_timeout/helm_values/kustomize_patches/extra_objects). Inventory `hosts-vars/seaweedfs-sync.yaml` (отдельный файл) добавляет declarative sync state: `seaweedfs_identities`/_extra (each identity optionally with `extra_vault_paths` for Layer 3 distribution), `seaweedfs_sync_buckets`/_extra (each bucket optionally with `policy` AWS IAM document inside).
- **ESO integration.** Yes (via `eso_vault_integration_seaweedfs` в `hosts-vars/seaweedfs.yaml`) — два ESO secrets: PostgreSQL creds (simple `dataFrom.extract`) + S3 identities (`seaweedfs_secret_s3_identities`: ESO template reading single Vault field `config` from `/seaweedfs/s3-config/all` rendering K8s Secret `seaweedfs-s3-identities` с ключом `seaweedfs_s3_config` consumed через upstream chart's `existingConfigSecret`).
- **Architecture v4 — 3 layers (см. [`secrets-and-eso.md`](secrets-and-eso.md) §11 migration note):**
  - **Layer 1 — Identities (admin + users + anonymous):** declarative source-of-truth в `seaweedfs_identities`/_extra → sync = diff vs Vault combined JSON → CREATE/UPDATE/DELETE per-identity (generate 20-char AK + 40-char SK для CREATE non-anonymous) → vault-put combined JSON → ESO sync materializes K8s Secret → mount through `existingConfigSecret` in S3 pods.
  - **Layer 2 — Buckets + quotas + policies + immutable settings (объединённые):** declarative `seaweedfs_sync_buckets` (`{name, collection, replication, quota?, policy?}` — collection и replication обязательные) → diff vs K8s ConfigMap `seaweedfs-sync-buckets-state` → **pre-phase fail-fast ASSERT** (после compute calls, до Phase A): если ЛЮБОЕ из immutable полей (collection или replication) changed на kept bucket vs state → sync aborts с detailed error message, cluster intact. → 6-phase sync order (A delete стейл buckets via `s3.bucket.delete` → B delete стейл policies у kept buckets via `aws s3api delete-bucket-policy` (через helper Deployment `seaweedfs-aws-cli-helper` — см. [`secrets-and-eso.md`](secrets-and-eso.md) §11 v9) → C create new buckets via `s3.bucket.create -owner={{ seaweedfs_identity_admin.name }}` → C2 apply fs.configure для new buckets с combined `-locationPrefix=/buckets/<n> -collection=<c> -replication=<r> -apply` → D apply policies (kept + new merged) via `aws s3api put-bucket-policy` (через helper Deployment `seaweedfs-aws-cli-helper`, full replace, idempotent) → E apply quotas via `s3.bucket.quota -op=set|disable -sizeMB=<X>` с MiB/GiB/TiB conversion) → update ConfigMap. Self-contained admin creds fetch (conditional — только если any policy op). Quota enforcement via K8s CronJob `seaweedfs-quota-enforce` (every 5 min). Persistence — filer Postgres metadata (включая `entry.Extended["s3-bucket-policy"]` для policies). NOTE: `s3.bucket.delete` не имеет флага `-force`, но сам делает hard delete через CollectionDelete + filer Remove (bucket с объектами удаляется; единственное препятствие — Object Lock с locked objects).
  - **Layer 3 — Identity credentials distribution:** declarative `seaweedfs_identities[].extra_vault_paths` (list of full Vault paths с mount engine prefix, optional per-identity) → sync читает credentials из combined JSON (записанного Layer 1) → diff vs K8s ConfigMap `seaweedfs-sync-identity-distributions-state` → Phase A vault-put для каждой (identity, path) target pair с fixed keys `username`/`accessKey`/`secretKey` (HARD-CODED, не configurable) → Phase B vault-delete для каждой state path не в target paths (idempotent) → Phase C update ConfigMap. Anonymous identity с `extra_vault_paths` → fail на sync (нет creds). Источник истины для credentials — Layer 1 combined JSON; этот layer только distribute'ит в дополнительные Vault slots для consumer-компонентов (которые потребляют через свой `eso_vault_integration_<consumer>_secrets_extra`).
- **Sync as task includes (не standalone playbook):** `playbook-app/tasks/seaweedfs/tasks-seaweedfs-user-sync.yaml` + `tasks-seaweedfs-identity-secret-distribute.yaml` + `tasks-seaweedfs-bucket-sync.yaml`. Invoked from `seaweedfs-install.yaml` via tags `[user-sync]` + `[identity-distribute]` (before helm install; identity-distribute читает combined JSON свежий) и `[bucket-sync]` (after install + post + quota-cron). user-sync includes conditional rollout restart `deployment/seaweedfs-s3` (when identities changed AND deployment exists). Convention: `dto_label_name` passed only at playbook-level invocation, nested includes inherit via Ansible scope.
- **Python compute layer (v6 + v7, stateless filter API).** Compute logic (diff, JSON building, validation, AK/SK generation, immutable settings violation detection) вынесена в `filter_plugins/seaweedfs_sync.py` — **11 stateless public filters** (10 v6 + 1 v7 `seaweedfs_buckets_immutable_violations`; auto-discovered via repo-root `ansible.cfg`'s `[defaults] filter_plugins = filter_plugins`) + 15 private helpers (internal primitives). Каждая public функция принимает raw inputs (Vault JSON string + target list + ConfigMap raw string) и делает всю compute-работу внутри Python; промежуточных Ansible-facts между Python-вызовами нет. AK/SK generation Python-side через `secrets.choice` с inventory-параметрами `seaweedfs_sync_access_key_length` / `_secret_key_length` / `_access_key_charset` / `_secret_key_charset`. v7 immutable settings validation (`_validate_buckets_have_collection_and_replication` + `_validate_replication_format` regex `^[0-9]{3}$`) called by every Layer 2 public filter — fail-fast. Pytest unit tests в `tests/python/test_seaweedfs_sync.py` (47 cases — 6 shared + 6 Layer 1 + 9 Layer 3 + 18 Layer 2 + 8 v7 immutable settings), shared fixtures в `tests/python/conftest.py`; pytest — Layer 3 в `make test`. Admin creds extraction в bucket-sync.yaml — Ansible-native chain (`from_json` + `selectattr` + `first`). См. [`secrets-and-eso.md`](secrets-and-eso.md) §11 migration notes v6 + v7.
- **Policy-only principals (official SeaweedFS design):** identity без `actions[]` доступна **ТОЛЬКО через bucket policy Allow** (auth_credentials.go:1481 — "bypasses IAM checks to support cross-account access and policy-only principals"). Admin = special с `actions=["Admin"]` (bypass identity check via isAdmin()). Anonymous = special name (для public-read через `Principal: "*"`).
- **Non-AWS deviations (SeaweedFS 4.29):**
  - Principal — ТОЛЬКО flat string или array, НЕ dict `{AWS: ...}` (policy_engine/types.go:55-77).
  - Resource — must reference this bucket (cross-bucket запрещён; s3api_bucket_policy_handlers.go:340-366).
  - Only Allow statements (explicit Deny блокирует и admin; auth_credentials.go:1487). Rely on default-deny.
  - Account ID — wildcard `*` в Principal ARN (no per-user account binding).
- **ServiceMonitor.** Yes — 4 SM (master/volume/filer/s3), каждый gated per-component через `seaweedfs_service_monitor.<c>.enabled`. Endpoint port — named `metrics` из upstream Service templates.
- **Dependencies.** Cilium (CNI), cert-manager (TLS Ingress), external-secrets (ESO), vault (Vault), linstor (PVC), traefik (IngressRoute в post phase).
- **Non-install playbooks.** None. Sync invoked via install playbook tags. Admin safety guard — warning only (sync playbook не hard-fail if no `actions=[Admin]` in target identities; operator решает).
- **Admin identity inventory pattern.** Admin identity вынесена в отдельную inventory переменную `seaweedfs_identity_admin` (object `{name, actions}`) в `hosts-vars/seaweedfs-sync.yaml` — устраняет hardcoded `'admin'` string в `tasks-seaweedfs-bucket-sync.yaml` (admin creds lookup + `-owner=` для `weed shell s3.bucket.create`). `seaweedfs_identities` base array содержит ссылку `"{{ seaweedfs_identity_admin }}"` как один из элементов. Operator может переименовать admin без поиска по коду — по аналогии с GitLab pattern (`gitlab_postgresql_username`).
- **GitLab + GitLab-Runner S3 backend opt-in (v8).** `hosts-vars/seaweedfs-sync.yaml` SECTION 1 + SECTION 2 содержат **commented opt-in блоки** для GitLab/runner identities (2 entries) + buckets (6 entries — 5 GitLab + 1 runner-cache, collection=`gitlab`/`gitlab-runner`, replication=`001`, full-access policies). Operator копирует в свой `hosts-vars-override/seaweedfs-sync.yaml` (через `seaweedfs_identities_extra` + `seaweedfs_sync_buckets_extra`) если использует SeaweedFS как S3 backend для GitLab. Vault paths в `extra_vault_paths` используют variable references из `gitlab.yaml` / `gitlab-runner.yaml` ESO secrets (single source of truth — `{{ eso_vault_integration_gitlab.kv_engine_path }}{{ gitlab_secret_s3_creds.vault_path }}`). Альтернатива: cloud S3 — operator вручную `vault kv put` для тех же resolved paths без SeaweedFS sync.
- **Notes.** S3 HA — 3 replicas + Kubernetes Deployment default RollingUpdate strategy (zero downtime при rollout restart). Master 3 replicas с `podAntiAffinity` (3 узла). Volume server'ы — только на worker'ах через `nodeSelector`. Filer metadata в локальном PostgreSQL chart. **Identity model не verified empirically** — Модель 2 (policy-only principals) + identity-Secret mounted persistence требуют PoC на dev cluster для full confidence; см. [`secrets-and-eso.md`](secrets-and-eso.md) §11. Cross-namespace ingress NPs в seaweedfs ns (например `gitlab-allow-seaweedfs-s3`, `gitlab-runner-allow-seaweedfs-s3`) owned by **consumer chart's pre/ release** (`gitlab/pre`, `gitlab-runner/pre`), не `seaweedfs/pre` — поэтому 10 NPs в `charts/seaweedfs/pre/` не покрывают полный runtime список NPs в namespace; см. [`networking.md`](networking.md) §8.

### Erasure Coding Migration Playbook (operational reference)

SeaweedFS allows hot data tier на replication, cold data tier на erasure coding (EC) — без full rebuild кластера. EC profile'ы имеют минимальное требование к числу volume server'ов, поэтому правильная стратегия — **начинать с replication, добавлять EC по мере роста кластера**.

**RS profile comparison:**

| RS profile | Min volume servers | Storage overhead | Tolerance (server failures) |
|---|---|---|---|
| `replication=2` (Phase 1) | 2 | 2.0× | 1 |
| `RS-3-2` | 5 | 1.67× | 2 |
| `RS-6-3` (Phase 2) | 9 | 1.5× | 3 |
| `RS-10-4` (Phase 3) | 14 | 1.4× | 4 |
| `RS-14-4` | 18 | 1.29× | 4 |

`RS-d-p` notation: `d` data shards + `p` parity shards. Толерантность к падению `p` volume server'ов.

**Phase 1 — старт (2-5 worker'ов, ~100 GB):**
- Default replication=2 для всех volume'ов через `defaultReplication: "001"` в `seaweedfs_helm_values.master.defaultReplication`.
- Толерантность к 1 падению. Overhead ×2.
- EC tier не нужен — кластер слишком мал.

**Phase 2 — рост (~9 worker'ов, ~1 TB):**
- Replication=2 для hot / write-heavy volume'ов.
- **RS-6-3** для cold tier (read-mostly, age > N days). Overhead 50% вместо 100%. Толерантность к 3 падениям.
- Конвертация warm volumes в EC через `weed shell`:
  ```
  $ kubectl -n seaweedfs exec -it deploy/seaweedfs-s3 -- weed shell
  > volume.ec.encode -collection=<bucket> -fullPercent=95 -quietFor=24h
  ```
  Конвертирует все volumes (заполненные >95% и не модифицированные >24h) в EC шарды.

**Phase 3 — production (~14+ worker'ов, ~10 TB):**
- Wider EC profile'ы — `RS-10-4` (overhead 40%, толерантность 4), `RS-14-4` (overhead 29%).
- Полноценный tiering: hot = replication, warm = `RS-6-3`, cold/archive = `RS-10-4` или wider.
- Custom EC profile задаётся через `weed shell` (parameters command-specific).

**Ключевые свойства SeaweedFS под elastic growth:**
- EC encoding **не требует rebuild** кластера. Берёшь warm read-only volume, конвертируешь в EC, продолжаешь работать.
- **Добавление volume server'а — drop-in.** Поднимаешь pod на новой worker-ноде → регистрируется в master'е → начинает получать трафик. Без rebalancing-окна, без downtime.
- **Удаление volume server'а** — через `weed shell volume.fix.replication` + `volume.balance -force` для миграции данных перед удалением.

**Что НЕ делать:**
- Не выбирать узкий EC profile (`RS-3-2`) на 5 worker'ах с расчётом «потом мигрирую». Лишний re-encoding cycle при росте.
- Phase 1 = чистый replication до 9+ worker'ов. EC появляется естественно с ростом.

## 18. Namespaces Matrix

| Namespace | Owners | Fixed by upstream? |
|---|---|---|
| `cilium` | cilium | no |
| `cert-manager` | cert-manager | no |
| `external-secrets` | external-secrets | no |
| `vault` | vault | no |
| `haproxy-lb` | haproxy | no |
| `traefik-lb` | traefik | no |
| `longhorn-system` | longhorn, longhorn-s3-restore | **yes** — cannot rename |
| `argocd` | argocd | no (configurable via `argocd_namespace` — see §9) |
| `gitlab` | gitlab | no |
| `gitlab-runner` | gitlab-runner | no |
| `zitadel` | zitadel | no |
| `teleport` | teleport | no |
| `kube-system` | metrics-server (exceptional) | upstream |
| `piraeus-datastore` | linstor (Piraeus operator + LinstorCluster + satellites + CSI + HA controller + affinity controller + NFS server) | **yes** — upstream Piraeus convention |
| `seaweedfs` | seaweedfs (central S3 storage: master, volume, filer, s3 gateway + filer's PostgreSQL backend) | no |
| `mon-system` | mon-system (consolidated: prometheus-operator, prometheus, alertmanager, grafana, loki, vector, node-exporter, kube-state-metrics) | no |

## 19. Cross-cutting Dependency Order

Install in roughly this order (first → last). Parallel installation within a dependency tier is safe.

```
L0  cilium
L1  cert-manager   external-secrets
L2  longhorn       linstor       metrics-server
L3  vault
L4  traefik        haproxy
L5  mon-system     seaweedfs
L6  zitadel
L7  argocd    gitlab    teleport
L8  gitlab-runner
```

`linstor` и `longhorn` — оба storage tier; устанавливается **только один** из двух в кластере (выбор оператора), не оба параллельно.

The `argocd` component's `[gitops]` tag (AppProject + Applications) also runs in L7 as part of `argocd-install.yaml` — no separate playbook.

## 20. ESO-integrated Components (9)

Only these have `eso_vault_integration_<c>` objects and are validated by `tasks-eso-verify.yaml`:

`traefik`, `haproxy`, `longhorn`, `gitlab`, `gitlab_runner`, `zitadel`, `argocd`, `mon_system`, `seaweedfs`

Each integration object + `_secrets` list + `_secrets_extra` list lives in the corresponding `hosts-vars/<c>.yaml`.

See [`secrets-and-eso.md`](secrets-and-eso.md) for the per-component secret paths, `SecretStore` layout, and canonical `body` item format.

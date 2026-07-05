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
- **Non-install playbooks.** cert-manager-restart.yaml.
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
- **Required vars.** `vault_namespace`, `vault_image` (Vault server image — full URI:tag), `vault_operator_helm_chart_version` (bank-vaults operator chart), `vault_storage_class`, `vault_storage_size`, `vault_key_shares` (3), `vault_key_threshold` (2), `vault_policies` / `_extra`, `vault_auth_kubernetes_roles` / `_extra`, `vault_creds_host_path`. Kustomize patches (default `[]`): `vault_pre_kustomize_patches`, `vault_cr_kustomize_patches`, `vault_post_kustomize_patches`.
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
- **Notes.** This is the **in-cluster** HAProxy ingress — NOT to be confused with (1) the systemd-level apiserver LB in `playbook-system/install-haproxy-apiserver-lb.yaml`, nor (2) the **external** bastion edge-proxy in `playbook-system/bastion-proxy-install.yaml` (see [`bastion-proxy.md`](bastion-proxy.md)).

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
- **Dependencies.** Cilium, cert-manager, external-secrets, vault. Node prep via `playbook-system/prepare-longhorn.yaml` (kernel modules `iscsi_tcp`, `dm_crypt`; packages `open-iscsi`, `nfs-common`, `cryptsetup`, `dmsetup`).
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
- **Required vars.** `argocd_namespace`, `argocd_ui_domain`, `argocd_rpc_domain`, `argocd_external_url`, `argocd_ingress_class_name` (`traefik-lb`), `argocd_cert_manager_issuer` (object `{enabled, name, spec}`). Kustomize patches (default `[]`): `argocd_pre_kustomize_patches`, `argocd_install_kustomize_patches` (computed: `argocd_kustomize_patches_base` — operator base argocd-cm/argocd-cmd-params-cm patches + generated accounts/RBAC patches — concatenated with operator-override `argocd_install_kustomize_patches_extra`, e.g. argocd-ssh-known-hosts-cm), `argocd_post_kustomize_patches`, `argocd_gitops_kustomize_patches`.
- **ESO integration.** Yes (via `eso_vault_integration_argocd`) — but ONLY for git-ops repo credentials, added by the operator through `eso_vault_integration_argocd_secrets_extra` (entries set `body.target.template.metadata.labels: argocd.argoproj.io/secret-type: repo-creds` or `repository` so ArgoCD recognises them as repository credentials). The base `eso_vault_integration_argocd_secrets` list is empty. Local-account passwords (incl. the custom admin) are NOT synced via ESO — see **Declarative local accounts** below.
- **ServiceMonitor.** Yes.
- **Dependencies.** Cilium, cert-manager, external-secrets, vault, traefik.
- **Enable flag.** `argocd_enabled` (opt-in, default `false`): guards install/configure; argocd's cross-ns NPs to gitlab / gitlab-runner gated by `gitlab_enabled` / `gitlab_runner_enabled`. See [`networking.md`](networking.md) §8.5.
- **Non-install playbooks.** `argocd-restart.yaml`. (The former `argocd-configure.yaml` was removed once admin-password management became declarative — local accounts are reconciled in-place by `argocd-install.yaml --tags accounts-sync`.)
- **Declarative local accounts.** `argocd_local_accounts` (list of `{name, passwordMtime, enabled, capabilities}`, real values in `hosts-vars-override/`) declares local users. Per-account `accounts.<name>: <capabilities>` (required CSV of `login`/`apiKey`) + `accounts.<name>.enabled` (required bool) + `admin.enabled` render into `argocd-cm`, and `argocd_policy_csv_list` (Casbin lines, incl. `g, <admin>, role:admin`) into `argocd-rbac-cm` — both inside `argocd_kustomize_patches_base`, consumed by the install phase via the computed `argocd_install_kustomize_patches` (= base + operator-override `argocd_install_kustomize_patches_extra`). Passwords are generated at runtime by the `accounts-sync` reconcile (`tasks-argocd-accounts-sync.yaml`): bcrypt → `argocd-secret` (`accounts.<name>.password`/`.passwordMtime`, only these keys), plaintext mirror → Vault `eso-secret/argocd/accounts/creds`. Per-project RBAC (`AppProject.roles/groups`) lives in the external git-ops repo and only references these usernames. Default `AppProject` lockdown via `argocd_gitops_default_project_update` (raw `kubectl apply` in the gitops phase — ArgoCD auto-creates `default` and forbids its deletion). **Invariant:** `argocd-secret` must stay empty (no `data:`) in every helm render, else helm prunes the out-of-band `accounts.*` + `server.secretkey` keys.
- **Account secret distribution.** `argocd_local_accounts[].vault_paths` (optional list of FULL Vault paths с mount-engine prefix) → reconcile `accounts-distribute` (`tasks-argocd-accounts-distribute.yaml`, tag `[accounts-distribute]`, STEP 3.6, **after** `accounts-sync`) раздаёт creds аккаунта (fixed keys `username` = account name / `password` = plaintext из Vault-зеркала `eso-secret/argocd/accounts/creds`) в каждый объявленный путь. State — per-item ConfigMaps `argocd-accounts-distributions-<account>` (label `argocd-accounts-state=distributions`, content `{account_name, vault_paths, passwordMtime}`); change-detection vs state → vault-put только new/rotated пути, vault-delete стейл, apply только изменённые CM, prune CM. Прогон без изменений = 0 записей. Compute — `filter_plugins/argocd_accounts_distribute.py`. Аккаунт с `vault_paths` обязан существовать в зеркале (запусти `accounts-sync` первым), иначе fail-fast. Раздаёт creds в доп. Vault-slots для consumer-компонентов (их собственный ESO читает путь) — паттерн аналогичен seaweedfs Layer 3 (§17.5).
- **Notes.** Install phase renders pristine upstream `install.yaml` через kustomize (`argocd_install_kustomize_patches`) на master_manager_fact перед helm install — см. [`playbook-conventions.md`](playbook-conventions.md) §21. 7 ConfigMaps из upstream (`argocd-cm`, `argocd-cmd-params-cm`, `argocd-gpg-keys-cm`, `argocd-notifications-cm`, `argocd-rbac-cm`, `argocd-ssh-known-hosts-cm`, `argocd-tls-certs-cm`) принадлежат Helm release `argocd` (не `argocd-pre`); customization через strategic merge patches сохраняет upstream defaults автоматически. The `argocd-install.yaml` playbook ships with an additional `[gitops]` tag that runs after `[post]` and creates AppProject + Application(s) from `argocd_git_ops_apps` using `charts/argocd/gitops/` (separate Helm release `argocd-gitops` in the same namespace).

## 11. `gitlab`

- **Chart path.** `charts/gitlab/{pre,postgresql,redis,post}/` (no vendored workload chart — the main `gitlab` release is pulled from the remote Helm repo; local charts are only the phase wrappers + sidecar postgres/redis).
- **Install playbook.** `gitlab-install.yaml`.
- **Namespace.** `gitlab`.
- **Releases.** `gitlab-pre`, `gitlab-postgresql`, `gitlab-redis`, `gitlab`, `gitlab-post`.
- **External Helm repo.** `https://charts.gitlab.io` → chart `gitlab/gitlab`, version `gitlab_helm_chart_version` (default `8.11.8`, GitLab 17.11). HTTP↔OCI switchable via `gitlab_helm_is_oci`.
- **Required vars.** `gitlab_namespace`, `gitlab_helm_chart_version`, per-sibling (`gitlab_postgresql_*`, `gitlab_redis_*`) storage class + size + tolerations/nodeSelector/resources + credentials via ESO + per-sibling image tags. Postgres credentials parametrized via `gitlab_postgresql_username`, `gitlab_postgresql_database_name`, `gitlab_postgresql_secret_key_username`, `gitlab_postgresql_secret_key_password` (chart `gitlab/postgresql/` consumes them through `credentials:` nested block + `databaseName:` field in values). Domain vars (`gitlab_domain`, `gitlab_registry_domain`). S3 backend config (replaces MinIO sub-chart): `gitlab_s3_endpoint`, `gitlab_s3_region`, `gitlab_s3_path_style`, 5 bucket name vars (`gitlab_registry_bucket` / `_artifacts_bucket` / `_uploads_bucket` / `_packages_bucket` / `_backups_bucket`), Vault field name vars (`gitlab_s3_secret_key_username` / `_access_key` / `_secret_key`, values `"username"` / `"accessKey"` / `"secretKey"` — standardized identity-distribute Layer 3 fixed keys). Kustomize patches (default `[]`): `gitlab_pre_kustomize_patches`, `gitlab_postgresql_kustomize_patches`, `gitlab_redis_kustomize_patches`, `gitlab_post_kustomize_patches`.
- **Per-component sizing.** Each umbrella component (`webservice`, `sidekiq`, `registry`, `gitlab-shell`, `gitaly`, `gitlab-pages`, `gitlab-exporter`, `toolbox`, `migrations`) exposes granular `gitlab_helm_values_<c>_resources` / `_node_selector` / `_tolerations` knobs; the local sidecars use `gitlab_postgresql_*` / `gitlab_redis_*`. Base (`hosts-vars/gitlab.yaml`) is demo-scale — ×1 replicas + small active resource caps (never `{}`, avoids BestEffort QoS) + `nodeSelector` pinning to worker nodes. Per-cluster prod HA (2× stateless replicas + prod resources) lives in `hosts-vars-override/<cluster>/gitlab.yaml`, overriding only the needed knobs without redefining the whole `gitlab_helm_values` tree. Replica bounds: `gitlab_helm_values_<c>_min_replicas` / `_max_replicas`. Mirrors the seaweedfs knob pattern.
- **ESO integration.** Yes (via `eso_vault_integration_gitlab` in `hosts-vars/gitlab.yaml`) — Postgres password, Redis password, S3 storage creds (single path `/gitlab/s3-storage`, fields `username`/`accessKey`/`secretKey` standardized identity-distribute Layer 3), GitLab root password. Complex secrets (registry connection YAML for GitLab registry sub-chart, object-store connection YAML for consolidated object storage, s3cmd `.s3cfg` for toolbox backups) use `body.target.template.data.*` with ESO template placeholders wrapped in `{% raw %}...{% endraw %}`. S3 creds provisioning — две альтернативы: (A) SeaweedFS sync (opt-in template в `hosts-vars/seaweedfs-sync.yaml` SECTION 1+2 commented blocks); (B) cloud S3 — operator вручную `vault kv put <path>` для тех же resolved paths. GitLab playbook (`gitlab-install.yaml`) валидирует S3 creds существование в Vault fail-fast перед install.
- **ServiceMonitor.** Yes.
- **Dependencies.** Cilium, cert-manager, external-secrets, vault, traefik, longhorn.
- **Enable flag.** `gitlab_enabled` (opt-in, default `false`): guards install/configure; gates cross-ns NPs that argocd + gitlab-runner render into the gitlab namespace. See [`networking.md`](networking.md) §8.5.
- **Non-install playbooks.** `gitlab-configure.yaml` (rotate root password).
- **Backups.** GitLab backup/restore runs in the `toolbox` pod (`backup-utility` → s3cmd). `gitlab.toolbox.backups.objectStorage` (via `gitlab_helm_values_toolbox_backups`) → backend `s3` + an s3cmd `.s3cfg` secret rendered by ESO (`gitlab_secret_backup_s3cfg` → `eso-gitlab-backup-s3cfg`, from `/gitlab/s3-storage`; `host_base`/`use_https` derived from `gitlab_s3_endpoint`). Tarball → `gitlab_backups_bucket`; `gitlab_backups_stage_bucket` (`appConfig.backups.tmpBucket`, default `gitlab-backups-stage`) stages object-storage data. Manual only (no cron) + no PVC by design (ephemeral working dir). Tarball = DB + Gitaly repos only — object storage (artifacts/uploads/packages) and the container registry are NOT included. The staging bucket + its `gitlab-rw` grant live in the per-cluster `seaweedfs-sync` override (not base). **Restore:** same GitLab version + install GitLab first + transplant `gitlab-rails-secret` (encryption keys) before restore, else encrypted DB data (2FA / CI vars / tokens) is unrecoverable; with object-storage backup off, artifacts/uploads/packages + registry buckets must be migrated to the target S3 separately.
- **Notes.** Uses `tasks-helm-upgrade-async.yaml` for the main `gitlab` release (synchronous Ansible command times out on the multi-release GitLab chart). Cross-ns NP к SeaweedFS S3 backend (`allow-seaweedfs-s3` egress в gitlab ns + `gitlab-allow-seaweedfs-s3` ingress в seaweedfs ns) — в `gitlab/pre/templates/network-policy.yaml`, gated by `seaweedfs_enabled` (skipped when SeaweedFS disabled, e.g. cloud-S3); см. [`networking.md`](networking.md) §8.5.

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
- **Enable flag.** `gitlab_runner_enabled` (opt-in, default `false`): guards install; gitlab-runner's cross-ns NPs to gitlab gated by `gitlab_enabled`. See [`networking.md`](networking.md) §8.5.
- **Notes.** Cross-ns NP к SeaweedFS S3 backend (`To SeaweedFS S3` egress entries в `allow-gitlab-runner` + `allow-job-pod` NPs + `gitlab-runner-allow-seaweedfs-s3` ingress в seaweedfs ns с двумя `from` entries — runner pods + job pods) — в `gitlab-runner/pre/templates/network-policy.yaml`, gated by `seaweedfs_enabled` (skipped when SeaweedFS disabled, e.g. cloud-S3); см. [`networking.md`](networking.md) §8.5.

## 13. `zitadel`

- **Chart path.** `charts/zitadel/{pre,postgresql,install,post}/`.
- **Install playbook.** `zitadel-install.yaml`.
- **Namespace.** `zitadel`.
- **Releases.** `zitadel-pre`, `zitadel-postgresql`, `zitadel`, `zitadel-post`.
- **External Helm repo.** `https://charts.zitadel.com` → chart `zitadel/zitadel`, version `zitadel_helm_chart_version` (default `9.30.0`). HTTP↔OCI switchable via `zitadel_helm_is_oci`.
- **Required vars.** `zitadel_namespace`, `zitadel_helm_chart_version`, `zitadel_postgresql_image` (full URI:tag), `zitadel_postgresql_*` (storage, creds via ESO), `zitadel_domain`, `zitadel_masterkey` (in Vault via ESO). Postgres credentials parametrized via `zitadel_postgresql_username`, `zitadel_postgresql_database_name`, `zitadel_postgresql_secret_key_username`, `zitadel_postgresql_secret_key_password` (chart `zitadel/postgresql/` consumes them through `credentials:` nested block; main ZITADEL chart references them in `configmapConfig.Database.Postgres` + env secretKeyRef.key). Kustomize patches (default `[]`): `zitadel_pre_kustomize_patches`, `zitadel_postgresql_kustomize_patches`, `zitadel_post_kustomize_patches`.
- **ESO integration.** Yes (via `eso_vault_integration_zitadel` in `hosts-vars/zitadel.yaml`) — Postgres password, `masterkey`, first human-admin creds (`eso-zitadel-admin-creds`, username + password).
- **First human-admin.** Bootstrapped once at install (greenfield) via the chart's `FirstInstance` + Vault. `zitadel_org_name`, `zitadel_admin_username`, `zitadel_admin_email` are explicit HELM params in `configmapConfig.FirstInstance.Org` (`Name` / `Human.Username` / `Human.Email` with `Verified: true`); the password is generated policy-compliant (class-guaranteed) into Vault `/zitadel/admin/creds` and injected via `ZITADEL_FIRSTINSTANCE_ORG_HUMAN_PASSWORD` env (username comes from config, not env). Loginname `<zitadel_admin_username>@<org-label>.<zitadel_domain>` (org-label = lowercased `Org.Name`); real-email login also works. No rotation (`vault-exists` guard).
- **ServiceMonitor.** Yes.
- **Dependencies.** Cilium, cert-manager, external-secrets, vault, traefik, longhorn.
- **Enable flag.** `zitadel_enabled` (opt-in, default `false`): guards install.

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
- **Enable flag.** `teleport_enabled` (opt-in, default `false`): guards install + restart.
- **Non-install playbooks.** teleport-restart.yaml.
- **Notes.** `configure/` phase runs after the server is up and applies the declarative resource list.

## 15. `stakater-reloader`

- **Chart path.** `charts/stakater-reloader/pre/` (NetworkPolicies only; no local `install/` or `post/` — the controller is the external Stakater chart).
- **Install playbook.** `stakater-reloader-install.yaml`.
- **Namespace.** `stakater-reloader`.
- **Releases.** `stakater-reloader-pre`, `stakater-reloader`.
- **External Helm repo.** `https://stakater.github.io/stakater-charts` → chart `stakater/reloader`, version `stakater_reloader_helm_chart_version` (default `2.2.12`, appVersion `v1.4.17`). HTTP↔OCI switchable via `stakater_reloader_helm_is_oci`.
- **Required vars.** `stakater_reloader_namespace`, `stakater_reloader_helm_chart_version`, `stakater_reloader_helm_values_replica_count`. Upstream chart values under `stakater_reloader_helm_values.reloader` (off-by-default: `autoReloadAll: false`; `reloadStrategy: annotations`; `podMonitor.enabled: true`; `netpol.enabled: false`). Kustomize patches (default `[]`): `stakater_reloader_pre_kustomize_patches`.
- **ESO integration.** No.
- **ServiceMonitor.** PodMonitor (chart built-in, scrapes pod `:9090/metrics`); no ServiceMonitor.
- **Dependencies.** Cilium.
- **Notes.** Off-by-default — reloads nothing unless a workload opts in via annotation: broad `reloader.stakater.com/auto: "true"` (all referenced CM/Secrets) or narrow `configmap.reloader.stakater.com/reload: "<name>"` / `secret.reloader.stakater.com/reload: "<name>"` (only the named resources — the standard for explicit control). `reloadStrategy: annotations` patches the fixed pod-template annotation `reloader.stakater.com/last-reloaded-from` (key hardcoded, not configurable). Under ArgoCD, exclude it via `ignoreDifferences` jqPathExpression `.spec.template.metadata.annotations."reloader.stakater.com/last-reloaded-from"` (group `apps`) together with `RespectIgnoreDifferences=true` in `syncPolicy.syncOptions` so `selfHeal` does not revert it.

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
- **Namespace.** `linstor` (configurable via `linstor_namespace`).
- **Releases.** `linstor-pre`, `piraeus-operator`, `linstor-cluster`, `linstor-post`.
- **External Helm repos.** **Два OCI chart'a:**
  - `oci://ghcr.io/piraeusdatastore/piraeus-operator/piraeus`, version `piraeus_operator_helm_chart_version` (default `2.10.6`) — Piraeus operator (управляющий).
  - `oci://ghcr.io/piraeusdatastore/helm-charts/linstor-cluster`, version `linstor_cluster_helm_chart_version` (default `1.1.1`) — Datastore (`LinstorCluster` + `LinstorSatelliteConfiguration` + `LinstorNodeConnection` + monitoring + StorageClasses).
- **Required vars.** `linstor_namespace`, `linstor_rollout_timeout`, `linstor_pre_helm_timeout`, `piraeus_operator_helm_*` (chart vars для operator), `linstor_cluster_helm_*` (chart vars для cluster), `linstor_pre_helm_values`, `piraeus_operator_helm_values` (`installCRDs: true`, `tls.autogenerate`, `tls.renew`), `linstor_cluster_helm_values` (включает `linstorCluster.tolerations: [{operator: Exists}]`, `linstorCluster.properties` с 14 entries — 4 DrbdOptions/PeerDevice/c-* для sync rate tuning + 7 durability/quorum: `DrbdOptions/Net/data-integrity-alg`, `DrbdOptions/Net/verify-alg`, `DrbdOptions/Resource/quorum`, `DrbdOptions/Resource/on-no-quorum`, `DrbdOptions/Resource/on-suspended-primary-outdated`, `DrbdOptions/Resource/on-no-data-accessible`, `DrbdOptions/auto-add-quorum-tiebreaker` + 3 auto-evict: `DrbdOptions/AutoEvictAfterTime`, `DrbdOptions/AutoEvictMaxDisconnectedNodes`, `DrbdOptions/AutoEvictAllowEviction`, `linstorSatelliteConfigurations` с `fileThinPool` pools per tier, 6 `storageClasses`). Kustomize patches (default `[]`): `linstor_pre_kustomize_patches`, `linstor_post_kustomize_patches`.
- **Scheduling/resources knobs (per-workload, inline-стиль).** Operator override через `hosts-vars-override/linstor.yaml` (не Tier 1 suffix vars как cilium/cert-manager — осознанный выбор сохранить heavy-memo документацию рядом с полями в inventory). Распределение по 8 workload'ам:
  - **Operator chart pod:** `piraeus_operator_helm_values.{nodeSelector, affinity, tolerations, operator.resources}`. `tolerations: []` — явный override (chart-default = 2 DRBD-rules `drbd.linbit.com/lost-quorum` + `force-io-error`; operator-pod не должен быть привязан к DRBD-troubled узлам).
  - **LinstorCluster sub-components (6):** `linstor_cluster_helm_values.linstorCluster.<sub>.podTemplate.spec.{nodeSelector, affinity}` + `.containers[name=<container>].resources`. Container names: `controller→linstor-controller`, `csiController→linstor-csi`, `csiNode→linstor-csi`, `highAvailabilityController→ha-controller`, `affinityController→linstor-affinity-controller`, `nfsServer→nfs-server` (DaemonSet называется `linstor-csi-nfs-server`, но main container — `nfs-server`).
  - **Satellites:** `linstor_cluster_helm_values.linstorSatelliteConfigurations[].podTemplate.spec.{nodeSelector, affinity}` + `.containers[name=linstor-satellite].resources`. Не путать config-level `nodeSelector` (выбор узлов где запустить DaemonSet) и `podTemplate.spec.nodeSelector` (pod-level scheduling override) — два разных поля в одном config'е.
  - **`tolerations` в podTemplate.spec для sub-components + satellites НАМЕРЕННО опущено.** Empirically verified (`kubectl kustomize`): пустой `[]` в `podTemplate.spec.tolerations` REPLACE'ит operator-defaults (DaemonSet eviction-tolerations + `HAControllerTolerations` DRBD + cluster-wide `[{operator: Exists}]`), что ломает scheduling. Operator override через `hosts-vars-override/` добавляет поле когда нужно переопределить.
  - **Cluster-wide:** `linstor_cluster_helm_values.linstorCluster.{nodeSelector, tolerations}` (заметка: `linstorSatelliteConfigurations` — sibling `linstorCluster` на уровне `linstor_cluster_helm_values`, не parent). Cluster-wide nodeSelector/affinity REPLACE per-component podTemplate; cluster-wide tolerations MERGE с per-component через operator's `MergeTolerations()` (later wins).
- **ESO integration.** No.
- **ServiceMonitor.** Yes — через `linstor_cluster_helm_values.monitoring.enabled: true` (Piraeus operator деплоит свои ServiceMonitor resources). **Также** post phase добавляет custom ServiceMonitor (`linstor-controller`) + PodMonitor'ы (`linstor-satellite`, `linstor-affinity-controller`), параметризованные через `linstor_post_helm_values` (operator переопределяет dict целиком; по умолчанию все 3 monitor'а enabled, interval `30s`, scrapeTimeout `15s`). **Внимание оператору:** проверить отсутствие duplicate scrape jobs между piraeus embedded monitoring и post phase monitors.
- **Controller GUI (Ingress).** LINSTOR controller отдаёт GUI (REST API + `/ui/`) на `linstor-controller:3370`. Post phase рендерит plain `Ingress` `linstor-ui` (паттерн longhorn-ui): ACME-TLS терминируется на Traefik (`websecure`), backend `linstor-controller:3370`, домен `linstor_ui_domain` (`linstor-ui-k8s-v2.drawapp.ru`), cert через per-component `linstor_cert_manager_issuer` (Let's Encrypt HTTP-01) + `Certificate`. VPN-only middleware — toggle `linstor_ui_vpn_only_enabled` (default `false`). У GUI **нет своей авторизации** (open-source LINSTOR — только mTLS client-cert на 3371 либо сетевое ограничение); доступ закрывается VPN. Pre phase рендерит `Issuer` + ACME HTTP-01 solver-NP pair + `linstor-allow-traefik` egress NP (traefik → `linstor-controller:3370`).
- **Dependencies.** Cilium (CNI), cert-manager (TLS для controller GUI Ingress), traefik (controller GUI Ingress). Host prep через `playbook-system/prepare-linstor.yaml` (kernel-headers `linux-headers-$(uname -r)` + `apt-mark hold` + verify `/lib/modules/$(uname -r)/build` symlink) — Piraeus operator сам собирает DRBD module через kmod-loader Pod (init-container в satellite), на хосте `drbd-dkms` НЕ ставится.
- **Non-install playbooks.** `linstor-restart.yaml` (rollout-restart 8 workloads из `linstor_restart_resources`).
- **Notes.** 6 storageClasses (3 tier × 2 modes): tier prefix `lnstr-manager-*` (only managers), `lnstr-major-*` (cross-tier via multi-pool `"lnstr-file-thin-manager lnstr-file-thin-worker"`), `lnstr-worker-*` (only workers); modes `*-local` (replica=1, strict-local), `*-multi-sync` (replica=2 Protocol C). Tier filtering — через pool name per `LinstorSatelliteConfiguration` (Path B — единственный надёжный absolute-filter mechanism; `--replicas-on-same Aux/key=value` syntax не whitelisted на controller). DRBD sync rate tuning через namespace `DrbdOptions/PeerDevice/c-*` (не `Net/`, не `Disk/` — оба rejected с "not whitelisted" error). `fileThinPool` driver (sparse files на root FS — extra disk не требуется). Альтернатива Longhorn'у в L2 storage tier. DRBD durability stack (фиксирован в `linstor_cluster_helm_values.linstorCluster.properties`): `DrbdOptions/Net/data-integrity-alg: crc32c` (per-write end-to-end CRC, защита от silent network bit-flip) + `DrbdOptions/Net/verify-alg: crc32c` (alg для on-demand scrubbing) + `DrbdOptions/Resource/quorum: majority` + `DrbdOptions/Resource/on-no-quorum: suspend-io` + `DrbdOptions/Resource/on-suspended-primary-outdated: force-secondary` (авто-демоция устаревшего бывшего-Primary при возврате после failover) + `DrbdOptions/Resource/on-no-data-accessible: suspend-io` (заморозка I/O при полной потере доступа к данным вместо I/O-error) + `DrbdOptions/auto-add-quorum-tiebreaker: True` (split-brain protection для replica=2). `DrbdOptions/Resource/on-no-quorum` и `DrbdOptions/auto-add-quorum-tiebreaker` совпадают с дефолтами Piraeus operator'а, но зафиксированы явно в Helm values для защиты от изменения upstream-default'ов. Auto-evict (`DrbdOptions/AutoEvict*`, дефолты зафиксированы явно) — при offline ноды >60 мин переназначает её replica≥2 на живые ноды; для replica-1 не применим.

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
- **Loki storage (S3, stateless).** Loki хранит chunks + TSDB-индекс в S3 (default in-cluster SeaweedFS, bucket `loki-logs`; внешний S3 — смена `mon_system_loki_s3_endpoint`); Deployment stateless на `emptyDir` (PVC убран). Creds из Vault через ESO secret `eso-mon-system-loki-s3-creds` → env `CUSTOM_LOKI_STORE_S3_ACCESS_KEY_ID`/`_SECRET_ACCESS_KEY` (`-config.expand-env=true`). Egress — always-on NP `allow-loki` (→ seaweedfs S3 8333 + external 443/80). Detail + provisioning — [`observability.md`](observability.md) §5.3.
- **ServiceMonitor.** Three SMs in `mon-system/post/` (loki, ksm, node-exporter), plus 6 system-component SMs (kube-apiserver, kubelet, kube-controller-manager, kube-scheduler, etcd, coredns) in `system-service-monitors.yaml` always-rendered. Vector by design has no SM (no metrics endpoint). Grafana and Prometheus-Operator self-SMs are not currently shipped.
- **Ingress + Certificate.** UI Ingresses for grafana, prometheus, alertmanager rendered in `post/` with composite gates (operator + per-UI flag for prometheus/alertmanager; just grafana flag for grafana). Per-UI VPN allow-list flags: `mon_system_<c>_vpn_only_enabled`.
- **Dependencies.** Cilium, cert-manager, external-secrets, vault (for grafana ESO + Loki S3 creds), traefik (for UIs), longhorn (for Prometheus + grafana-postgresql PVCs), seaweedfs (default Loki S3 object store), zitadel (optional — for grafana OIDC).
- **Non-install playbooks.** mon-system-restart.yaml.
- **Notes.** Prometheus-operator phase renders pristine upstream `prometheus-operator.yaml` через kustomize (`mon_system_prometheus_operator_kustomize_patches`) на master_manager_fact перед helm install — см. [`playbook-conventions.md`](playbook-conventions.md) §21. Single namespace eliminates the cross-namespace coupling that previously required: `vector-allow-loki` cross-ns NetworkPolicy in the `loki` namespace; `grafana-allow-prometheus` / `grafana-allow-alertmanager` cross-ns NetworkPolicies in the `mon` namespace; cross-ns Vector→Loki DNS endpoint. The consolidated NetworkPolicy in `mon-system/pre/` covers all intra-namespace traffic with a single `allow-internal-traffic` rule plus per-component egress rules (operator/ksm to apiserver, vector to apiserver:443, grafana external HTTP/HTTPS, loki to SeaweedFS S3 + external S3), and one cross-ns NetworkPolicy in `traefik-lb` for UI ingress.

---

## 17.5. `seaweedfs`

- **Chart path.** `charts/seaweedfs/{pre,postgresql,post}/` — три LOCAL_CUSTOM chart'а. Install phase — **upstream chart напрямую** (не локальный chart subdir).
- **Install playbook.** `seaweedfs-install.yaml` (содержит pre + postgresql + install + policy-sync + user-sync + identity-distribute + bucket-sync + post + verify; весь sync **ПОСЛЕ** install — filer-driven, `weed shell` live-reload).
- **Namespace.** `seaweedfs`.
- **Releases.** `seaweedfs-pre`, `seaweedfs-postgresql`, `seaweedfs` (upstream chart), `seaweedfs-post`.
- **External Helm repo.** `https://seaweedfs.github.io/seaweedfs/helm` → chart `seaweedfs/seaweedfs`, version `seaweedfs_helm_chart_version` (default `4.36.0`). HTTP↔OCI switchable via `seaweedfs_helm_is_oci`.
- **Tags.** `pre`, `postgresql`, `install`, `policy-sync`, `user-sync`, `identity-distribute`, `bucket-sync`, `post` + `always` (pre-check + verify). Default запуск — все теги последовательно (sync-теги ПОСЛЕ install — `weed shell` требует running filer).
- **Required vars.** `seaweedfs_namespace`, `seaweedfs_s3_domain` + `seaweedfs_master_ui_domain` + `seaweedfs_filer_ui_domain` + `seaweedfs_admin_ui_domain` (S3 endpoint + три раздельных UI-поддомена), `seaweedfs_helm_chart_version`, `seaweedfs_postgresql_*` (image, storage class, size, creds field names, `seaweedfs_postgresql_create_table_template` — postgres2 createTable-шаблон), `seaweedfs_admin_ui_username` + `seaweedfs_admin_ui_secret_key_user`/`_password` (admin UI login + Vault/K8s Secret field names), `seaweedfs_helm_values` (большой dict — master/volume/filer/s3/admin/worker enabled + replicas: 3 для s3 HA + antiAffinity + nodeSelector + storage + filer postgres2 connection + s3.existingConfigSecret + admin.secret.existingSecret + admin PVC + worker jobType), `seaweedfs_*_helm_values` для каждой фазы, `seaweedfs_cert_manager_issuer`, `seaweedfs_s3_ingress_config` + `seaweedfs_master_ui_ingress_config` + `seaweedfs_filer_ui_ingress_config` + `seaweedfs_admin_ui_ingress_config`, `seaweedfs_service_monitor`. Inventory `hosts-vars/seaweedfs-sync.yaml` (отдельный файл) добавляет declarative sync state: `seaweedfs_managed_policies`/_extra (managed IAM policies `{name, document}`, Layer P), `seaweedfs_identities`/_extra (each identity с `keys: [{access_key, vault_paths?}]` operator-chosen access_key + optional `policy_names` для attach managed policy; per-key `vault_paths` → Layer 3 distribution), `seaweedfs_sync_buckets`/_extra (each bucket с `owner` identity + optional `rack`/`dataCenter`/`quota_size`).
- **ESO integration.** Yes (via `eso_vault_integration_seaweedfs` в `hosts-vars/seaweedfs.yaml`) — три ESO secrets: PostgreSQL creds (simple `dataFrom.extract`) + S3 bootstrap config (`seaweedfs_secret_s3_bootstrap`: ESO template reading single Vault field `config` from `/seaweedfs/s3-config/bootstrap` rendering K8s Secret `eso-seaweedfs-s3-bootstrap` с ключом `seaweedfs_s3_config` = `{"identities":[]}`, consumed через upstream chart's `existingConfigSecret` — форсит filer-driven Replace-режим) + admin UI creds (`seaweedfs_secret_admin_ui_creds`: simple `dataFrom.extract` из `/seaweedfs/admin-ui/creds`, поля `adminUser`/`adminPassword` → K8s Secret `eso-seaweedfs-admin-ui-creds`, потребляется admin StatefulSet через `admin.secret.existingSecret`). v17: combined identity JSON key-store удалён — S3 identities живут ТОЛЬКО в filer (`/etc/iam/identities/`), R/W через `weed shell s3.configure`. Bootstrap field name — plain-var `seaweedfs_s3_bootstrap_vault_field` (`"config"`).
- **UI & admin/worker components (chart 4.36.0).** Четыре раздельных доступа, каждый — свой Ingress (Host-only, **без** path-prefix), все ACME-TLS, VPN выключен в тестовой фазе: **S3 endpoint** (`seaweedfs_s3_domain`) + **master UI** (`seaweedfs-master:swfs-master`/9333) + **filer UI** (`seaweedfs-filer:swfs-filer`/8888) + **admin UI** (`seaweedfs-admin:http`/23646). Заменяет прежний совмещённый `adminUiIngressConfig` (path-routing master через `PathPrefix(/master)` + filer catch-all на одном FQDN). Post chart: `ingress-{master,filer,admin}.yaml` (вместо `ingress-admin-ui.yaml`) + `certificate.yaml` с 4 cert-блоками; inventory — три `seaweedfs_{master,filer,admin}_ui_ingress_config` + `post_helm_values` ключи `masterIngressConfig`/`filerIngressConfig`/`adminIngressConfig`.
  - **admin** (`admin.enabled`, StatefulSet, 1 replica) — панель управления + координатор worker'ов. Login/password через ESO: `seaweedfs_secret_admin_ui_creds` (Vault `/seaweedfs/admin-ui/creds`, поля `adminUser`/`adminPassword`) → `admin.secret.existingSecret` → чарт инжектит `WEED_ADMIN_USER`/`WEED_ADMIN_PASSWORD`. Seed — `seaweedfs-install.yaml` тег `[install]` ПЕРЕД main helm (vault-get → generate-if-missing → vault-put → eso-force-sync → wait-secret; зеркалит postgres-seed). **Persistence — только PVC:** admin хранит session keys + maintenance/task config + историю задач на FS через `-dataDir=/data` (SQL-бэкенда у admin НЕТ — verified `sources/seaweedfs/weed/admin/dash/config_persistence.go`); `data.type: persistentVolumeClaim` (lnstr-major-multi-sync, 2Gi) → чарт авто-выставляет `-dataDir`. Логи в stdout (`logs.type: ""`).
  - **worker** (`worker.enabled`, Deployment, 1 replica) — background-job runner (`jobType: "all"` — vacuum/volume_balance/ec_balance/admin_script + erasure_coding/iceberg_maintenance). **Требует admin** (чарт hard-fail'ит без `admin.enabled`); `adminServer` пустой → авто-коннект к in-cluster admin gRPC (33646). Stateless (emptyDir working dir), логи в stdout. NP не нужен — intra-namespace покрыт `allow-internal-traffic`.
  - **NetworkPolicy (pre chart).** Добавлен `seaweedfs-admin` NP (ingress от Traefik на 23646 + egress к apiserver); `seaweedfs-master` получил ingress от Traefik (9333); `<ns>-allow-traefik` egress расширен на master+admin. `adminHttpPort: 23646` в pre `values.yaml`. Итого 12 NP в `charts/seaweedfs/pre/` (после добавления `allow-for-monitoring` для Prometheus-скрейпа; см. ServiceMonitor).
- **Architecture v14 (filer-driven IAM, 4 layers) → v17 (filer = единственный источник истины; см. [`secrets-and-eso.md`](secrets-and-eso.md) §11 v14 + v17 + v18):** IAM применяется в живой filer через `weed shell` (live-reload, без рестарта S3); доступ identity-based (managed policy на identity, не bucket policy). v17: каждый sync-слой READ'ит состояние из живого filer (нет Vault combined JSON / ConfigMap-state кроме identity-distribute).
  - **Layer P — Managed policies:** declarative `seaweedfs_managed_policies`/_extra (`{name, document}` AWS IAM doc, одна policy на consumer) → diff vs живой filer (`weed shell s3.policy -list`; v17) → put changed/new + delete стейл via `s3.policy -put -name -file` / `-delete` → filer `/etc/iam/policies/`. Task `tasks-seaweedfs-policy-sync.yaml` (tag `policy-sync`, ДО user-sync — policy должна существовать до attach).
  - **Layer 1 — Identities (admin + users + anonymous):** declarative `seaweedfs_identities`/_extra (`{name, actions, policy_names?, keys?}`; `keys: [{access_key, vault_paths?}]` — operator-chosen plaintext access_key globally unique, REQUIRED непустой для named identity, отсутствует для anonymous) → sync = diff vs живой filer (`s3.configure` dump) → applies в живой filer 6 фазами: Phase A delete стейл (bare `-delete` = whole identity), Phase B create new (target не в filer → `keys[0].access_key` + сгенерированный 40-char SK + full actions/policies; anonymous → пустые creds; identity без creds/actions/policy_names скипается), Phase C grant (add-delta target−filer, `s3.configure -apply` аддитивен), Phase D revoke (remove-delta filer−target, `s3.configure -delete` с `-policies`/`-actions`, НИКОГДА bare), Phase E keys-add (inventory access_key не в filer → append credential + сгенерированный SK; brand-new identity keys[0] исключается — его делает create; no-rotation: AK уже в filer скипается), Phase F keys-delete (filer access_key не в target, identity kept → `-access_key=AK -delete`, single credential). Ключи НЕ ротируются (access_key уже в filer не re-apply'ится = перезапись секрета). Нет Vault combined JSON (filer = источник истины). Лог-подавление не используется (тест-фаза). `-actions` для admin, `-policies=<csv>` для consumers; anonymous с managed policy — без cred-флагов. `actions=[]` + `policy_names=[<p>]` = identity-based access через managed policy Allow.
  - **Layer 3 — Identity credentials distribution:** declarative `seaweedfs_identities[].keys[].vault_paths` (optional per-key) → читает creds из живого filer (`s3.configure` dump, per-key map `{access_key: secret_key}`) → diff vs per-item state ConfigMaps `seaweedfs-sync-identity-distributions-<identity>` (label `seaweedfs-sync-state=identity-distributions`; **СОХРАНЕНЫ в v17** — единственный ConfigMap-потребитель; content `{identity_name, keys: [{access_key, vault_paths}]}`) → vault-put/delete с fixed keys `username` (identity name) / `accessKey` (key.access_key) / `secretKey` (filer creds для (identity, access_key)). **Change-detection:** vault-put только new/rotated пары (`seaweedfs_distribute_paths_to_add` diff vs state), vault-delete стейл paths, apply ТОЛЬКО изменённые state CM (`seaweedfs_state_configmaps_to_apply_changed` diff vs live список → self-heals drift/manual-deletion), prune стейл CM. Прогон без изменений = 0 записей. has_target gate = `seaweedfs_distribute_configmaps_to_apply | length > 0`. Anonymous с ключом, несущим `vault_paths` → fail. distribute'ит creds ключа в дополнительные Vault slots для consumer-компонентов.
  - **Layer 2 — Buckets + quotas + owner:** declarative `seaweedfs_sync_buckets`/_extra (`{name, owner, replication, rack, dataCenter, quota_size?}` — owner/replication/rack/dataCenter обязательны, quota_size optional) → diff vs живой filer (`fs.configure` + `s3.bucket.list`, dual READ; нет ConfigMap-state) → **pre-phase fail-fast ASSERT** (immutable owner/replication/rack/dataCenter changed на kept bucket vs filer → abort, cluster intact) → phases: A delete стейл buckets (`s3.bucket.delete`) → B create new (`s3.bucket.create -owner=<owner>`) → C `fs.configure` (`-replication -rack -dataCenter -apply`, все три всегда) → D quota upsert (target с quota_size, чья квота отличается от filer — diff vs `s3.bucket.list` quota, unchanged скипаются → `s3.bucket.quota -op=set -sizeMB`) → E quota delete (target без quota_size, у кого в filer квота ЕСТЬ → `s3.bucket.quota -op=remove` → unlimited; уже-без-квоты скипаются; снятие НЕ сбрасывает уже-выставленный read-only). Owner immutable (owner-reconcile фаза удалена в v18). Bucket policies + per-bucket `policy` field + aws-cli helper удалены (доступ к данным — через managed policy на owner-identity; owner не влияет на policy-check). Quota enforcement — нативный SeaweedFS 4.31+ (s3-gateway, leader-locked, ~раз в минуту). Persistence — filer Postgres metadata. NOTE: `s3.bucket.delete` без `-force` делает hard delete через CollectionDelete (Object Lock с locked objects — единственное препятствие).
- **Sync as task includes (не standalone playbook):** `playbook-app/tasks/seaweedfs/tasks-seaweedfs-policy-sync.yaml` + `tasks-seaweedfs-user-sync.yaml` + `tasks-seaweedfs-identity-secret-distribute.yaml` + `tasks-seaweedfs-bucket-sync.yaml`. Invoked from `seaweedfs-install.yaml` via tags `[policy-sync]` → `[user-sync]` → `[identity-distribute]` → `[bucket-sync]`, все **ПОСЛЕ** helm install (`weed shell` требует running filer; live-reload без рестарта S3). user-sync применяет identities в живой filer (`s3.configure -apply`) — conditional rollout-restart `deployment/seaweedfs-s3` удалён (не нужен при live-reload). Convention: `dto_label_name` passed only at playbook-level invocation, nested includes inherit via Ansible scope.
- **Python compute layer (stateless filter API, v18 split → v20 per-key — 4 доменных файла).** Compute logic (diff, JSON building, validation, secret_key generation, immutable settings violation detection, filer-dump parsing, per-item ConfigMap reconstruction/apply/prune для identity-distribute) вынесена в 4 self-contained файла `filter_plugins/seaweedfs_{policy,user,bucket,distribute}.py` (v18 split монолита `seaweedfs_sync.py`, удалён) — **19 stateless public filters** (auto-discovered via repo-root `ansible.cfg`'s `[defaults] filter_plugins = filter_plugins`): Layer P `seaweedfs_policy.py` (`seaweedfs_policies_to_put`/`_to_delete`), Layer 1 `seaweedfs_user.py` (`seaweedfs_identities_to_delete`/`_to_create`/`_to_grant`/`_to_revoke` + v20 `seaweedfs_keys_to_add`/`seaweedfs_keys_to_delete`), Layer 2 `seaweedfs_bucket.py` (`seaweedfs_buckets_to_delete`/`_to_create`/`_immutable_violations`/`_quota_to_upsert`/`_quota_to_delete`), Layer 3 `seaweedfs_distribute.py` (`seaweedfs_distribute_paths_to_add`/`_to_delete` + generic state-ConfigMap `seaweedfs_state_configmaps_to_combined_json`/`_to_delete`/`_to_apply_changed` + `seaweedfs_distribute_configmaps_to_apply` — только identity-distribute сохранил ConfigMap-state). Каждый файл self-contained (нет cross-file import); private-хелпер `_parse_s3_configure_identities` намеренно дублируется в `seaweedfs_user.py` + `seaweedfs_distribute.py`, но v20 return shape расходится per-file (user → `access_keys` list; distribute → `{access_key: secret_key}` map; см. [`secrets-and-eso.md`](secrets-and-eso.md) §11 v18 + v20). Все diff-фильтры читают live-filer dump (signature raw-read + target). secret_key generation Python-side через `secrets.choice` с inventory-параметрами `seaweedfs_sync_secret_key_length` / `_secret_key_charset` (access_key — operator-chosen plaintext per key, v20; access_key gen-vars удалены). Pytest unit tests разбиты на 4 файла `tests/python/test_seaweedfs_{policy,user,bucket,distribute}.py` (11 + 30 + 32 + 36 = **109 cases**), shared fixtures в `tests/python/conftest.py`; pytest — Layer 3 в `make test`. См. [`secrets-and-eso.md`](secrets-and-eso.md) §11 v20.
- **Identity-based access (v14 design):** доступ к данным — через managed policy, прикреплённую к identity (`policy_names`, applied via `s3.configure -policies`). Identity с `actions=[]` + `policy_names=[<p>]` авторизуется через managed policy Allow. Admin = `actions=["Admin"]` (bypass identity check via isAdmin()). Anonymous = special name (public-read через managed policy на anonymous, опционально). Bucket policies удалены — owner бакета не влияет на policy-check (только ListBuckets / object ownership).
- **Managed policy document (SeaweedFS 4.36):**
  - Principal НЕ нужен — policy прикрепляется к identity (principal = эта identity).
  - Resource — bucket ARN(s): `arn:aws:s3:::<bucket>` + `arn:aws:s3:::<bucket>/*`.
  - Only Allow statements (explicit Deny блокирует и admin). Rely on default-deny.
- **Resources (per-component caps).** Active requests/limits on every component, opt-out via `{}`, as blast-radius protection after the 1520-tech-prod-1 incident (filer/master spun to ~2 cores under `resources:{}` and starved the node incl. CoreDNS). One var per component in `hosts-vars/seaweedfs.yaml`: `seaweedfs_helm_values_{master,filer,s3,admin,worker}_resources` (wired into each component block), `seaweedfs_helm_values_volume_resources` (referenced by every volume group — base + per-cluster override), `seaweedfs_postgresql_resources` (sidecar — required adding a `{{- with .Values.resources }}` guard to the local `postgresql/` chart, which the upstream components already ship). Override replaces wholesale (NOT `_extra`). cpu-limit caps runaway, mem-limit is OOMKill protection, requests give Burstable QoS.
- **ServiceMonitor.** Yes — **upstream** chart рендерит SM для всех компонентов через единый флаг `seaweedfs_helm_values.global.seaweedfs.monitoring.enabled: true`: master/filer/s3/worker + 2 volume тир-группы (port `metrics`/9327) + admin (port `http`/23646 — admin отдаёт `/metrics` на http-порту без auth). interval/scrapeTimeout захардкожены upstream (30s/5s). mon-system Prometheus (`serviceMonitorSelector: {}`) подхватывает их без лейблов. Скрейп разрешает NP `allow-for-monitoring` в `seaweedfs/pre/` (`podSelector: {}`, ingress 9327+23646, open-from-anywhere — паттерн vault/traefik/argocd/longhorn). Прежний самописный SM (`post/templates/service-monitor.yaml` + `seaweedfs_service_monitor`) удалён — он покрывал лишь 4 компонента и не работал (NP для скрейпа отсутствовал).
- **Dependencies.** Cilium (CNI), cert-manager (TLS Ingress), external-secrets (ESO), vault (Vault), linstor (PVC), traefik (Ingress в post phase).
- **Enable flag.** `seaweedfs_enabled` (opt-in, default `false`): guards install; gitlab/gitlab-runner gate their cross-ns SeaweedFS S3 NPs on it (skipped when disabled — cloud-S3 alternative). See [`networking.md`](networking.md) §8.5.
- **Non-install playbooks.** None. Sync invoked via install playbook tags. Admin safety guard — warning only (sync playbook не hard-fail if no `actions=[Admin]` in target identities; operator решает).
- **Admin identity inventory pattern.** S3 admin identity вынесена в отдельную inventory переменную `seaweedfs_identity_s3_admin` (object `{name, actions, keys}`; v20: name `s3-admin`, `keys: [{access_key: "s3-admin"}]`) в `hosts-vars/seaweedfs-sync.yaml` — устраняет hardcoded `'s3-admin'` string. `seaweedfs_identities` base array содержит ссылку `"{{ seaweedfs_identity_s3_admin }}"`. Используется в user-sync (admin safety warning по `actions=['Admin']`, не по имени). Operator может переименовать s3-admin без поиска по коду — по аналогии с GitLab pattern (`gitlab_postgresql_username`). НЕ путать с admin-UI login `seaweedfs_admin_ui_*` в `hosts-vars/seaweedfs.yaml` (другой admin). (v14: bucket `owner` = per-bucket consumer, не admin; admin-creds fetch в bucket-sync удалён вместе с bucket policies.)
- **GitLab + GitLab-Runner S3 backend opt-in (v8/v14).** `hosts-vars/seaweedfs-sync.yaml` SECTION 1 (managed policies + identities) + SECTION 3 (buckets) содержат **commented opt-in блоки** для GitLab/runner: managed policies `gitlab-rw`/`gitlab-runner-rw` (Layer P), identities `gitlab`/`gitlab-runner` (`policy_names` + `keys[].vault_paths`), 6 buckets (5 GitLab owner=`gitlab` + 1 runner-cache owner=`gitlab-runner`, replication=`001`, rack=`workers-1`, dataCenter=`dc-1`). Operator копирует в `hosts-vars-override/seaweedfs-sync.yaml` (через `seaweedfs_managed_policies_extra` + `seaweedfs_identities_extra` + `seaweedfs_sync_buckets_extra`). Vault paths в `keys[].vault_paths` — variable references из `gitlab.yaml`/`gitlab-runner.yaml` ESO secrets. Альтернатива: cloud S3 — operator вручную `vault kv put`.
- **Notes.** S3 HA — 3 replicas + Kubernetes Deployment default RollingUpdate strategy (zero downtime при rollout restart). Master 1 replica (HA отложена). Volume server'ы — тир-группы `volumes:` (managers-1-dc-1: 3×control-plane rack=managers-1; workers-1-dc-1: 5×worker rack=workers-1), dataCenter=dc-1; rack-метка = тир + номер физ.rack для soft tier-placement бакетов. Filer metadata — store **postgres2** (table-per-bucket: отдельная таблица на каждый S3 bucket + `filemeta` для не-bucket путей) в локальном PostgreSQL chart; filer создаёт `filemeta` при старте + per-bucket таблицы сам (env `WEED_POSTGRES2_CREATETABLE`, ручной psql-bootstrap не нужен; см. [`secrets-and-eso.md`](secrets-and-eso.md) §11 v21). Имя бакета `filemeta` **зарезервировано** (коллизия с дефолтной таблицей — не задавать в `seaweedfs_sync_buckets`); filer-юзеру нужны постоянные `CREATE`-права (owner БД из дефолтного chart удовлетворяет). **v14 IAM model не verified empirically** — filer-driven empty-config (§4.2 load-bearing) + `weed shell` флаги (`s3.policy -put -file` / `s3.configure -policies` / `s3.bucket.owner`) требуют PoC на dev cluster (PoC пропущен по решению оператора); см. [`secrets-and-eso.md`](secrets-and-eso.md) §11 v14. Cross-namespace ingress NPs в seaweedfs ns (например `gitlab-allow-seaweedfs-s3`, `gitlab-runner-allow-seaweedfs-s3`) owned by **consumer chart's pre/ release** (`gitlab/pre`, `gitlab-runner/pre`), не `seaweedfs/pre` — поэтому 12 NPs в `charts/seaweedfs/pre/` не покрывают полный runtime список NPs в namespace; см. [`networking.md`](networking.md) §8.

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

## 17.7. `filestash`

- **Chart path.** `charts/filestash/{pre,install,post}/` (local handwritten chart; no upstream Helm chart exists).
- **Install playbook.** `filestash-install.yaml`.
- **Namespace.** `filestash`.
- **Releases.** `filestash-pre`, `filestash`, `filestash-post`.
- **Image.** `filestash_image` (full URI:tag, default `docker.io/machines/filestash:latest` — pin per-cluster). Port `8334`.
- **Required vars.** `filestash_namespace`, `filestash_image`, `filestash_ui_domain`, `filestash_storage_class`, `filestash_storage_size`, `filestash_container_port`. securityContext: `filestash_run_as_user` / `_run_as_group` / `_fs_group` (default `1000`, image's filestash user), `filestash_read_only_root_fs` (default `true`). Ingress toggles: `filestash_cert_manager_issuer_enabled`, `filestash_ui_ingress_tls_enabled`, `filestash_ui_certificate_enabled`, `filestash_ui_vpn_only_enabled` (cert-manager ACME vs behind-Cloudflare). Kustomize patches (default `[]`): `filestash_pre_kustomize_patches`, `filestash_install_kustomize_patches`, `filestash_post_kustomize_patches`.
- **Workload.** StatefulSet (1 replica) + static RWO PVC at `/app/data` (state: `config.json`, embedded SQLite sessions/share/audit, search index) + emptyDir for `/tmp` (cache + state live under `/app/data` on the PVC); headless Service on `8334`. Hardened securityContext (runAsNonRoot, `readOnlyRootFilesystem: true`, seccomp RuntimeDefault, drop ALL caps). Probes: readiness `GET /healthz`, liveness `tcpSocket :8334`.
- **ESO integration.** Yes (via `eso_vault_integration_filestash`) — admin password only. Vault `eso-secret/filestash/app` holds `admin_password` (plaintext, operator reads for `/admin` login) + `admin_password_hash` (bcrypt); the ExternalSecret extracts ONLY the hash → env `ADMIN_PASSWORD` (→ `auth.admin`). Auto-generated at first install (seed-if-missing, like zitadel): the playbook generates a random plaintext, bcrypt-hashes it via the `password_hash('bcrypt')` filter (passlib), and stores both in Vault; operator reads the plaintext via `vault kv get`. No rotation (vault-exists guard). `general.secret_key` self-generates on first boot, persisted on PVC.
- **S3 connection.** NOT seeded declaratively. After first boot the admin logs into `/admin` and adds the SeaweedFS S3 connection once (endpoint `http://seaweedfs-s3.seaweedfs.svc.cluster.local:8333`, lives in PVC). Devs then log in with their own AK/SK (BYO-keys: Filestash proxies server-side, keys held transit-only in session, never stored). Per-dev S3 identities/buckets are operator-provisioned via `seaweedfs-sync` (out of this component's scope).
- **NetworkPolicy.** deny-all + DNS + intra-ns + ingress from traefik (`:8334`) + egress to seaweedfs-s3 (`:8333`, cross-ns ingress pair `filestash-allow-seaweedfs-s3` gated by `seaweedfs_enabled`) + ACME HTTP-01 solver pair (gated by issuer) + `filestash-allow-traefik` egress in traefik ns. No Vault egress (ESO operator talks to Vault).
- **Ingress.** Plain `kind: Ingress` (Traefik), like longhorn-ui/gitlab-ui — not an IngressRoute. Toggles: cert-manager ACME (`websecure` + `router.tls` + `spec.tls` + Certificate) or behind-Cloudflare (`web`, no TLS). `vpn-only` middleware via `router.middlewares` annotation.
- **ServiceMonitor.** No (no metrics).
- **Dependencies.** Cilium, cert-manager, external-secrets, vault, traefik, longhorn/linstor (storage), seaweedfs (S3 backend, functional).
- **Enable flag.** `filestash_enabled` (opt-in, default `false`): guards install + gates cross-ns NetworkPolicies.

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
| `stakater-reloader` | stakater-reloader | no |
| `linstor` | linstor (Piraeus operator + LinstorCluster + satellites + CSI + HA controller + affinity controller + NFS server) | no (configurable via `linstor_namespace`) |
| `seaweedfs` | seaweedfs (central S3 storage: master, volume, filer, s3 gateway + filer's PostgreSQL backend) | no |
| `mon-system` | mon-system (consolidated: prometheus-operator, prometheus, alertmanager, grafana, loki, vector, node-exporter, kube-state-metrics) | no |
| `filestash` | filestash | no |

## 19. Cross-cutting Dependency Order

Install in roughly this order (first → last). Parallel installation within a dependency tier is safe.

```
L0  cilium
L1  cert-manager   external-secrets
L2  longhorn       linstor       metrics-server   stakater-reloader
L3  vault
L4  traefik        haproxy
L5  mon-system     seaweedfs
L6  zitadel
L7  argocd    gitlab    teleport    filestash
L8  gitlab-runner
```

`linstor` и `longhorn` — оба storage tier; устанавливается **только один** из двух в кластере (выбор оператора), не оба параллельно.

The `argocd` component's `[gitops]` tag (AppProject + Applications) also runs in L7 as part of `argocd-install.yaml` — no separate playbook.

## 20. ESO-integrated Components (10)

Only these have `eso_vault_integration_<c>` objects and are validated by `tasks-eso-verify.yaml`:

`traefik`, `haproxy`, `longhorn`, `gitlab`, `gitlab_runner`, `zitadel`, `argocd`, `mon_system`, `seaweedfs`, `filestash`

Each integration object + `_secrets` list + `_secrets_extra` list lives in the corresponding `hosts-vars/<c>.yaml`.

See [`secrets-and-eso.md`](secrets-and-eso.md) for the per-component secret paths, `SecretStore` layout, and canonical `body` item format.

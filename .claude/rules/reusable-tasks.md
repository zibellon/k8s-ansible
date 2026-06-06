# Reusable Task Catalog

Every task include, split by repo half. Per task: **purpose**, **input vars**, **output facts / side effects**, **typical callers**, **idempotency notes**.

General rules for callers:

- Always use `include_tasks` (dynamic), never `import_tasks` — tag inheritance breaks with imports.
- Always pass `dto_label_name` matching the enclosing `[<c>-<action>-<phase>]` prefix — keeps logs aligned.
- Tag every include with the appropriate phase (`[always]`, `[pre]`, `[install]`, `[post]`, or a bootstrap-specific tag).
- Tasks that run `kubectl` / `helm` set `delegate_to: "{{ master_manager_fact }}"` + `run_once: true` internally — no need for the caller to set them.
- **Every task file starts with an `assert` block** that validates all required input parameters before doing any work. The assert is `tags: [always]` so it never silently skips under `--tags`. Reference: `tasks-k8s-secret-get.yaml`. Full pattern in `playbook-conventions.md` Rule 19.

---

## 1. `playbook-app/tasks/` (33 tasks)

The five vault task includes — `tasks-vault-put.yaml`, `tasks-vault-get.yaml`, `tasks-vault-delete.yaml`, `tasks-vault-distribute-creds.yaml`, `tasks-vault-config-verify.yaml` — live in the `playbook-app/tasks/vault/` subdirectory; include paths to them are `{{ project_root }}/playbook-app/tasks/vault/<name>.yaml`.

### 1.1 `tasks-pre-check.yaml`

- **Purpose.** Entry guard for every install playbook. Resolves `master_manager_fact` and asserts cluster is reachable.
- **Input.** `dto_label_name` (string — log prefix).
- **Validates (assert).** `dto_label_name` defined + non-empty. (No `delegate_to` — `master_manager_fact` not yet set at call time.)
- **Output.** Fact `master_manager_fact`. Fails play if no manager has `is_master: true`.
- **Callers.** Every `<c>-install.yaml`, also `-configure`, `-restart`, `-rotate` playbooks.
- **Idempotent.** Read-only; safe to call repeatedly.

### 1.3 `tasks-forbid-kube-system.yaml`

- **Purpose.** Guardrail — refuses to operate on `kube-system` namespace.
- **Input.** `dto_label_name`, `dto_target_namespace` (the component's target namespace).
- **Output.** Fails play if `dto_target_namespace == "kube-system"`.
- **Callers.** Every `<c>-install.yaml` at `tags: [always]`.
- **Idempotent.** Assertion only.

### 1.4 `tasks-copy-chart.yaml`

- **Purpose.** Package a local chart directory as tar.gz, copy to the master manager, extract. Faster and more idempotent than `synchronize` for many small files.
- **Input.** `dto_label_name`, `dto_chart_name` (release name — used for the temp archive file), `dto_chart_local_src` (**must** end with `/`), `dto_chart_remote_dest` (**must not** end with `/`).
- **Validates (assert).** `dto_label_name`, `dto_chart_name`, `dto_chart_local_src`, `dto_chart_remote_dest` all defined + non-empty.
- **Output.** Chart files at `{{ dto_chart_remote_dest }}/` on the master manager.
- **Callers.** Every phase of every install playbook.
- **Idempotent.** Re-extraction overwrites. Old files not pruned — if you rename a template, re-run `server-clean` on chart dir or `rm -rf` the remote dir.
- **Gotcha.** Missing trailing slash on `dto_chart_local_src` creates a nested dir.

### 1.4а `tasks-copy-helm-values.yaml`

- **Purpose.** Create a remote directory + write `values-override.yaml`. Used for external Helm chart install phases where `tasks-copy-chart.yaml` is not called (no local chart to ship).
- **Input.** `dto_label_name` (string), `dto_dir` (remote path, no trailing slash), `dto_filename` (string — name of file to create, typically `"values-override.yaml"`), `dto_content` (rendered string — the YAML content to write).
- **Validates (assert).** `dto_label_name`, `dto_dir`, `dto_filename`, `dto_content` all defined + non-empty.
- **Output.** Directory `{{ dto_dir }}` exists on master manager, file `{{ dto_dir }}/{{ dto_filename }}` written (mode 0644).
- **Callers.** Install-phase blocks in any playbook that uses an external Helm repo: `cilium-install.yaml`, `traefik-install.yaml`, `cert-manager-install.yaml`, `external-secrets-install.yaml`, `metrics-server-install.yaml`, `haproxy-install.yaml`, `longhorn-install.yaml`, `gitlab-install.yaml`, `gitlab-runner-install.yaml`, `zitadel-install.yaml`, `teleport-install.yaml`, `vault-install.yaml` (operator phase).
- **Idempotent.** Yes — `file: state=directory` and `copy` are both idempotent.
- **Gotcha.** `dto_content` is evaluated at include-time in the caller's variable scope. Always pass the fully-rendered string (e.g., `"{{ my_helm_values | to_nice_yaml }}"`).

### 1.4б `tasks-helm-template-kustomize-build.yaml`

- **Purpose.** На `master_manager_fact`: запустить `helm template` source chart → staging `<phase>-k-tmp/` → `kubectl kustomize` (with optional patches) → output `<phase>-k/templates/all.yaml`. Единый prepare-task для **всех** LOCAL-managed chart phase'ов (LOCAL_CUSTOM + KUSTOMIZE_WRAPPER). Helm install вызывает caller из `-k/` artifact. (см. [`playbook-conventions.md`](playbook-conventions.md) §21).
- **Input.** `dto_label_name` (string), `dto_release_name` (string — helm release name, used for `helm template`), `dto_chart_remote_dest` (string — source chart path on master, без `/`), `dto_values_file_path` (string — path to values-override.yaml for `helm template --values`; for KUSTOMIZE_WRAPPER — file with `{}`), `dto_kustomize_tmp_dir` (string — staging directory `<phase>-k-tmp`, без `/`), `dto_kustomize_final_dir` (string — output directory `<phase>-k`, без `/`), `dto_patches_list` (sequence — kustomize patches; empty `[]` valid), `dto_target_namespace` (string — passed to `helm template --namespace` for namespace context in rendered resources). **Optional:** `dto_kustomize_apply_namespace_transform` (bool, default `false`) — если `true`, в `kustomization.yaml` добавляется `namespace: <dto_target_namespace>` field (kustomize builtin transformer переписывает `metadata.namespace` namespaced ресурсов + `subjects[].namespace` ServiceAccount в (Cluster)RoleBinding). Используется только для KUSTOMIZE_WRAPPER phases (pristine upstream YAML с hardcoded namespaces). LOCAL_CUSTOM callers не передают — transformer disabled — multi-namespace templates не collapse'ятся в один namespace.
- **Validates (assert).** All 8 dto-params defined + non-empty (`dto_patches_list` — sequence; `[]` valid). Tag `[always]`.
- **Output.** `dto_kustomize_final_dir/Chart.yaml` + `dto_kustomize_final_dir/templates/all.yaml`. No facts exported. Both staging and output dirs not cleaned between runs — overwritten on re-run.
- **Internals.** 1) `file: state=directory dto_kustomize_tmp_dir`. 2) `shell: helm template <release> <source> --values <values_file> --namespace <namespace> > <tmp>/template-output.yaml`. 3) `copy: dest=<tmp>/kustomization.yaml` (renders `resources: [template-output.yaml]`, `patches: <list>`; **conditional** `namespace: <dto_target_namespace>` field — добавляется только если `dto_kustomize_apply_namespace_transform=true`, по default отключено). 4) `file: state=directory <final>/templates`. 5) `copy: src=<source>/Chart.yaml dest=<final>/Chart.yaml remote_src=yes`. 6) `shell: kubectl kustomize <tmp> > <final>/templates/all.yaml`.
- **Callers.** All 15 install playbooks for each LOCAL-managed phase: `cilium-install.yaml`, `cert-manager-install.yaml`, `external-secrets-install.yaml`, `vault-install.yaml`, `haproxy-install.yaml`, `traefik-install.yaml`, `longhorn-install.yaml`, `argocd-install.yaml`, `gitlab-install.yaml`, `gitlab-runner-install.yaml`, `zitadel-install.yaml`, `teleport-install.yaml`, `metrics-server-install.yaml`, `linstor-install.yaml`, `mon-system-install.yaml`.
- **Idempotent.** Yes — всe 6 шагов overwrite-safe; детерминированный output при одинаковых inputs.

### 1.5 `tasks-add-helm-repo.yaml`

- **Purpose.** Universal helm chart-source preparation for both HTTP repositories and OCI registries. For HTTP: `helm repo add` + `helm repo update`. For OCI: noop (Helm 3 supports `oci://` URL natively in `helm install`). In both cases exports a dynamic fact with the chart-source string (ready for substitution into `helm upgrade --install <release> <SOURCE> ...`).
- **Input (L1, always required).** `dto_label_name`, `dto_helm_is_oci` (bool), `dto_helm_url` (HTTP repo URL **or** full OCI chart URL), `dto_helm_chart_version`, `dto_helm_chart_source_res_fact_name` (output fact name — dynamic pattern).
- **Input (L2, required only when `dto_helm_is_oci=false`).** `dto_helm_repo_name`, `dto_helm_chart_name`.
- **Validates (assert).** L1 — все 5 параметров defined + non-empty (`dto_helm_is_oci is boolean`). L2 — `when: not dto_helm_is_oci`, оба параметра defined + `length > 0`.
- **Output (dynamic fact, name from `dto_helm_chart_source_res_fact_name`).**
  - HTTP (`is_oci=false`): `<dto_helm_repo_name>/<dto_helm_chart_name>` (например `cilium/cilium`).
  - OCI (`is_oci=true`): `<dto_helm_url>` (например `oci://ghcr.io/bank-vaults/helm-charts/vault-operator`).
  Fact propagated to all hosts (см. шаг "Propagate chart-source fact to all hosts").
- **Side effect (HTTP only).** `helm repo add <repo_name> <url> --force-update` + `helm repo update` on master manager. Skipped for OCI.
- **Callers (12 install playbooks).** `cilium-install.yaml`, `cert-manager-install.yaml`, `external-secrets-install.yaml`, `gitlab-install.yaml`, `gitlab-runner-install.yaml`, `haproxy-install.yaml`, `longhorn-install.yaml`, `metrics-server-install.yaml`, `teleport-install.yaml`, `traefik-install.yaml`, `vault-install.yaml` (vault-operator phase, is_oci=true), `zitadel-install.yaml`.
- **Idempotent.** `helm repo add` with same URL is no-op (HTTP); `helm repo update` always safe; OCI path is pure set_fact (always safe).
- **Pattern в caller'е.** Перед каждым external chart install вызвать task с 7 dto-параметрами (HTTP) или 5 dto-параметрами (OCI, без `dto_helm_repo_name`/`dto_helm_chart_name`); затем в helm install command подставить output fact: `helm upgrade --install <release> {{ <c>_helm_chart_source }} --version {{ <c>_helm_chart_version }} ...`. Один и тот же синтаксис для всех 12 callers, никаких Jinja-веток в playbook'ах.

### 1.6 `tasks-wait-crds.yaml`

- **Purpose.** Wait for CRDs to reach `Established` condition before applying workloads that depend on them.
- **Input.** `dto_label_name`, `dto_crds_list` (list of `"crd/<name>"` strings).
- **Validates (assert).** `dto_label_name` defined + non-empty; `dto_crds_list` defined, is sequence, non-empty.
- **Reads global var.** `crds_wait` (dict from `hosts-vars/k8s-base.yaml`: `timeout`/`retries`/`delay`) — controls `command --timeout`, `until.retries`, `until.delay`. Not a caller-passed param.
- **Output.** None. Fails on timeout.
- **Callers.** Install playbooks with `crds/` phases (`argocd`, `mon-system`), Vault operator, cert-manager.
- **Idempotent.** Read-only wait.

### 1.7 `tasks-wait-rollout.yaml`

- **Purpose.** `kubectl rollout status` for Deployments / DaemonSets / StatefulSets.
- **Input.** `dto_label_name`, `dto_rollout_namespace`, `dto_rollout_timeout` (e.g. `"120s"`), `dto_rollout_resources_list` (list of `"<kind>/<name>"`).
- **Validates (assert).** `dto_label_name`, `dto_rollout_namespace`, `dto_rollout_timeout` defined + non-empty; `dto_rollout_resources_list` defined, is sequence, non-empty.
- **Output.** None. Fails on timeout.
- **Callers.** End of `install` phase on every component; also `-restart` playbooks.
- **Idempotent.** Read-only wait.

### 1.8a `tasks-vault-config-verify.yaml`

- **Purpose.** Тонкий wrapper над Python-фильтром `vault_config_verify` (`filter_plugins/vault_config_verify.py`) — pre-check для Vault policies + roles. Wrapper: Rule-19 input-assert + `set_fact` `_local_error_item_list` (вызов фильтра с merged policies/roles) + `assert length == 0` (fail с полным отчётом). Фильтр возвращает `list[str]` нарушений, не кидает; raise — в wrapper'е. Каждый caller передаёт только `dto_label_name`; `vault_policies/_extra` и `vault_roles/_extra` мёрджатся в wrapper'е и передаются фильтру.
- **Input.** `dto_label_name` (required string).
- **Validates.**
  - Unique `name` в merged `vault_policies + (vault_policies_extra | default([]))`.
  - Unique `name` в merged `vault_roles + (vault_roles_extra | default([]))`.
  - Referential integrity: каждая role в merged_roles → каждая policy в `role.policies` существует в merged_policies (fail с указанием missing policy + role name).
- **Output.** `_local_error_item_list` (local throwaway fact — `list[str]` нарушений; `[]` = OK).
- **Callers.** `vault-install.yaml` + 9 ESO-install + 2 ESO-configure playbook'ов + `tests/helm-validate.yaml` (13 callers total) — вызывают **первым**, перед `tasks-eso-verify.yaml`.
- **Idempotent.** Read-only.

### 1.8b `tasks-eso-verify.yaml`

- **Purpose.** Тонкий wrapper над Python-фильтром `eso_verify` (`filter_plugins/eso_verify.py`) — pre-check для одного ESO-integrated компонента. Wrapper: Group A (input asserts) как Rule-19 assert в YAML + `set_fact` `_local_error_item_list` (вызов фильтра — Groups B/C/D) + `assert length == 0`. Фильтр возвращает `list[str]`, не кидает. Вызывается **после** `tasks-vault-config-verify.yaml` (две независимые task'и, не include task-from-task).
- **Input.**
  - `dto_label_name` (required string).
  - `dto_eso_secrets_list` (required sequence — финальный массив base + extra после Ansible Jinja resolution).
  - `dto_eso_integration_object` (required mapping — `eso_vault_integration_<c>` со всеми полями: `sa_name`, `role_name`, `secret_store_name`, `kv_engine_path`).
  - `dto_namespace` (required string — K8s namespace компонента).
- **Reads (inventory).** `vault_policies`, `vault_policies_extra`, `vault_roles`, `vault_roles_extra` (merge в wrapper'е, merged списки передаются фильтру).
- **Validates (4 groups).** A — в wrapper'е (Rule-19 assert); B/C/D — в Python-фильтре `eso_verify`.
  - **A. Input asserts.**
  - **B. SecretStore→Vault connectivity (scoped к role этого компонента):** role exists, SA binding, namespace binding, policies count > 0, each role.policies exists.
  - **C. ESO uniqueness:** `external_secret_name` + `body.target.name` unique в `dto_eso_secrets_list`.
  - **D. Policy path coverage (scoped к role's policies):** каждый item Vault path (`body.dataFrom[].extract.key` + `body.data[].remoteRef.key`) должен быть substring хотя бы одного path-prefix из policies этой role (после stripping `/*`).
- **Output.** `_local_error_item_list` (local throwaway fact — `list[str]` нарушений; `[]` = OK).
- **Callers.** 11 ESO-integrated install/configure playbook'ов (9 install + 2 configure). NOT called from `tests/helm-validate.yaml` (test driver рендерит upstream charts — нет component scope).
- **Idempotent.** Read-only.

### 1.10 `tasks-k8s-list-helm.yaml`

- **Purpose.** Utility task — run `helm list -n <ns>` and print the result to the Ansible log via the `debug` module. Tab characters in the output are replaced with spaces for readability.
- **Input.** `dto_label_name` (required string, log prefix). `dto_helm_namespace` (required string, K8s namespace).
- **Validates (assert).** `dto_label_name`, `dto_helm_namespace` — both defined + non-empty.
- **Output.** No facts exported. Stdout (`stdout_lines` of `helm list`, tabs replaced by spaces) printed via debug (`var:` form). Register fact `k8s_list_helm_result` and intermediate set_fact `k8s_list_helm_pretty` are local to the include scope.
- **Callers.** 15 playbooks in `playbook-app/` — `argocd-install.yaml`, `cert-manager-install.yaml`, `cilium-install.yaml`, `external-secrets-install.yaml`, `gitlab-install.yaml`, `gitlab-runner-install.yaml`, `haproxy-install.yaml`, `longhorn-install.yaml`, `longhorn-s3-restore-create.yaml`, `metrics-server-install.yaml`, `mon-system-install.yaml`, `teleport-install.yaml`, `traefik-install.yaml`, `vault-install.yaml`, `zitadel-install.yaml` (all in verify block, tag `[always]`). Also called by `tasks-cluster-info-namespace.yaml` (aggregator, from `cluster-info.yaml`).
- **Idempotent.** Read-only (`changed_when: false`).

### 1.11 `tasks-eso-force-sync.yaml`

- **Purpose.** Annotate ExternalSecrets with `force-sync=<epoch>` to trigger ESO reconciliation without waiting for `refreshInterval`.
- **Input.** `dto_label_name` (required). Optional: `dto_eso_sync_namespace`, `dto_eso_sync_es_name` — control targeting (single ES / all in ns / all namespaces).
- **Validates (assert).** `dto_label_name` defined + non-empty. Optional params are not validated (governed by `when:` conditions).
- **Output.** All targeted ExternalSecrets annotated.
- **Callers.** `eso-force-sync.yaml` standalone; also after `tasks-vault-put.yaml` (internal).
- **Idempotent.** Annotation bump is always safe.

### 1.12 `tasks-vault-get.yaml`

- **Purpose.** Read one KV v2 field from Vault into an Ansible fact (plus a caller-named `_exists` boolean fact).
- **Input.** `dto_label_name`, `dto_vault_get_path` (full KV path), `dto_vault_get_field` (field name), `dto_vault_get_res_fact_name` (output fact name for the field value), `dto_vault_get_res_exists_fact_name` (output fact name for the exists bool).
- **Validates (assert).** `dto_label_name`, `dto_vault_get_path`, `dto_vault_get_field`, `dto_vault_get_res_fact_name`, `dto_vault_get_res_exists_fact_name` all defined + non-empty.
- **Output.** Fact named by `dto_vault_get_res_fact_name` (field value) + fact named by `dto_vault_get_res_exists_fact_name` (bool). Missing fields set the exists fact `false` without failing the play.
- **Callers.** `-configure` playbooks (resolve current credentials before rotating), `-rotate` playbooks.
- **Idempotent.** Read-only.

### 1.13 `tasks-vault-put.yaml`

- **Purpose.** `vault kv put` to a KV v2 path (full replace). Nothing else — ESO force-sync (`tasks-eso-force-sync.yaml`) and the downstream K8s `Secret` wait (`tasks-wait-secret.yaml`) are the caller's responsibility, as separate steps.
- **Input.** `dto_label_name`, `dto_vault_put_path` (full KV path), `dto_vault_put_data` (non-empty dict of `{field: value}`).
- **Validates (assert).** `dto_label_name` defined + non-empty; `dto_vault_put_path` defined + non-empty; `dto_vault_put_data` defined, is mapping, non-empty.
- **Output.** Vault path written (full replace). Does NOT annotate any ExternalSecret or touch any K8s `Secret`.
- **Callers.** Seed + rotation flows (Postgres, Redis, GitLab root, ArgoCD admin, Vault admin-token, seaweedfs identity/bucket/admin seeds).
- **Idempotent.** Re-running re-puts identical values — safe (full replace, no side effects).

### 1.13a `tasks-vault-delete.yaml`

- **Purpose.** Hard-delete a Vault KV v2 path entirely (metadata stanza + all versions) via `vault kv metadata delete`. Not a soft tombstone (`vault kv delete`).
- **Input.** `dto_label_name`, `dto_vault_delete_path` (full KV path incl. mount, e.g. `eso-secret/seaweedfs/old-user`).
- **Validates (assert).** `dto_label_name` defined + non-empty; `dto_vault_delete_path` defined + non-empty.
- **Output.** Vault path removed. No facts exported.
- **Callers.** `tasks-seaweedfs-identity-secret-distribute.yaml` (Phase B — vault-delete стейл distribution paths). Generic Vault primitive — usable by any `<c>-sync.yaml` / `-rotate.yaml`.
- **Idempotent.** Yes — missing path treated as success. `failed_when` list accepts `rc != 0` only when stderr does NOT contain `'no value found'` or `'not found'` (case insensitive). `changed_when: rc == 0` so missing-path runs report unchanged.

### 1.14 `tasks-generate-secret.yaml`

- **Purpose.** Generate a random N-character secret into a named fact.
- **Input.** `dto_label_name`, `dto_generate_fact_name` (output fact name). Optional: `dto_generate_length` (default 32), `dto_generate_chars` (default `ascii_letters,digits`).
- **Validates (assert).** `dto_label_name`, `dto_generate_fact_name` both defined + non-empty. Optional params not asserted.
- **Output.** Fact with the generated secret.
- **Callers.** Bootstrap of first-run passwords (GitLab root, Vault admin, Grafana admin).
- **Idempotent.** No — regenerates on each call. Pair with `tasks-vault-get.yaml` + `_exists` check to avoid regenerating existing secrets.

### 1.15 `tasks-vault-distribute-creds.yaml`

- **Purpose.** Read the `vault-unsealer-secret` from the live cluster, decode, write to `/etc/kubernetes/vault-unseal.json` on managers.
- **Input.** `dto_label_name`.
- **Output.** Local file on each manager (0600, root:root).
- **Callers.** `manager-join.yaml` (so new managers have unseal keys), `vault-install.yaml` post-phase.
- **Idempotent.** Overwrites if secret changed.

### 1.15a `tasks-wait-secret.yaml`

- **Purpose.** Wait until a named K8s `Secret` exists in a given namespace. Typically called after `tasks-vault-put.yaml` / ESO force-sync to confirm ESO has materialized the secret before downstream tasks attempt to consume it.
- **Input.** `dto_label_name` (string), `dto_wait_secret_name` (string — secret name), `dto_wait_secret_namespace` (string — namespace).
- **Validates (assert).** All 3 params defined + non-empty. Tag `[always]`.
- **Reads global var.** `secret_wait` (dict from `hosts-vars/k8s-base.yaml`: `secret_wait.retries`, `secret_wait.delay`) — controls the `until` loop. Not a caller-passed param.
- **Output.** None — fails play if secret does not appear within the retry window.
- **Callers.** `gitlab-install.yaml`, `gitlab-runner-install.yaml`, `zitadel-install.yaml`, `mon-system-install.yaml` (grafana phase), `argocd-install.yaml`.
- **Idempotent.** Read-only wait (`changed_when: false`).

### 1.16 `tasks-helm-upgrade-async.yaml`

- **Purpose.** Run `helm upgrade --install` in async mode for charts that exceed Ansible's synchronous command timeout (notably the GitLab chart family).
- **Input.** `dto_label_name`, `dto_helm_command` (complete helm command string). Async timing from global var `helm_async` (dict from `hosts-vars/k8s-base.yaml`: `helm_async.timeout`, `helm_async.poll`).
- **Validates (assert).** `dto_label_name`, `dto_helm_command` both defined + non-empty.
- **Output.** Helm release updated.
- **Callers.** `gitlab-install.yaml`.
- **Idempotent.** Same semantics as synchronous helm; async is just about avoiding SSH timeouts.

### 1.17 `tasks-k8s-secret-get.yaml`

- **Purpose.** Read a single `.data` field from a K8s Secret into a named fact. Never fails on missing secret or field — callers branch on the caller-named exists fact.
- **Input.** `dto_label_name`, `dto_secret_namespace`, `dto_secret_name`, `dto_secret_field`, `dto_secret_res_fact_name`, `dto_secret_res_exists_fact_name` (all required).
- **Validates (assert).** All 6 dto params defined + non-empty. **Reference implementation** — this is the canonical example for the assert pattern.
- **Output (runtime facts, set on all hosts).**
  - `{{ dto_secret_res_fact_name }}` — decoded string value (`''` if missing).
  - `{{ dto_secret_res_exists_fact_name }}` — bool (true only if field is non-empty).
- **Callers.** `argocd-configure.yaml`, `gitlab-configure.yaml`, `gitlab-install.yaml`, `gitlab-runner-install.yaml`, `vault-install.yaml`.
- **Idempotent.** Read-only; safe to call repeatedly.

### 1.18 `tasks-k8s-list-pods.yaml`

- **Purpose.** Utility task — run `kubectl get pods -n <ns> -o wide` and print the result to the Ansible log via the `debug` module.
- **Input.** `dto_label_name` (required string, log prefix). `dto_pods_namespace` (required string, K8s namespace).
- **Validates (assert).** `dto_label_name`, `dto_pods_namespace` — both defined + non-empty.
- **Output.** No facts exported. Stdout (`stdout_lines` of `kubectl get pods`) printed via debug (`var:` form). Register fact `k8s_list_pods_result` is local to the include scope.
- **Callers.** 23 playbooks in `playbook-app/` — all `<c>-install.yaml` playbooks with a verify block (tag `[always]`), plus all `<c>-restart.yaml` playbooks (before/after the rollout; without tags). Also called by `tasks-cluster-info-namespace.yaml` (aggregator, from `cluster-info.yaml`).
- **Idempotent.** Read-only (`changed_when: false`).

### 1.19 `tasks-k8s-list-network-policy.yaml`

- **Purpose.** Utility task — run `kubectl get networkpolicies -n <ns>` and print the result to the Ansible log via the `debug` module.
- **Input.** `dto_label_name` (required string, log prefix). `dto_np_namespace` (required string, K8s namespace).
- **Validates (assert).** `dto_label_name`, `dto_np_namespace` — both defined + non-empty.
- **Output.** No facts exported. Stdout (`stdout_lines` of `kubectl get networkpolicies`) printed via debug (`var:` form). Register fact `k8s_list_network_policy_result` is local to the include scope.
- **Callers.** 14 playbooks in `playbook-app/` — `argocd-install.yaml`, `cert-manager-install.yaml`, `cilium-install.yaml`, `external-secrets-install.yaml`, `gitlab-install.yaml`, `gitlab-runner-install.yaml`, `haproxy-install.yaml`, `longhorn-install.yaml`, `metrics-server-install.yaml`, `mon-system-install.yaml`, `teleport-install.yaml`, `traefik-install.yaml`, `vault-install.yaml`, `zitadel-install.yaml` (all in verify block, tag `[always]`). Also called by `tasks-cluster-info-namespace.yaml` (aggregator, from `cluster-info.yaml`).
- **Idempotent.** Read-only (`changed_when: false`).

### 1.20 `tasks-k8s-list-service.yaml`

- **Purpose.** Utility task — run `kubectl get service -n <ns> -o wide` and print the result to the Ansible log via the `debug` module.
- **Input.** `dto_label_name` (required string, log prefix). `dto_service_namespace` (required string, K8s namespace).
- **Validates (assert).** `dto_label_name`, `dto_service_namespace` — both defined + non-empty.
- **Output.** No facts exported. Stdout (`stdout_lines` of `kubectl get service`) printed via debug (`var:` form). Register fact `k8s_list_service_result` is local to the include scope.
- **Callers.** `tasks-cluster-info-namespace.yaml` (aggregator, called from `cluster-info.yaml`).
- **Idempotent.** Read-only (`changed_when: false`, `failed_when: false`).

### 1.21 `tasks-k8s-list-deployment.yaml`

- **Purpose.** Utility task — run `kubectl get deployment -n <ns> -o wide` and print the result to the Ansible log via the `debug` module.
- **Input.** `dto_label_name` (required string, log prefix). `dto_deployment_namespace` (required string, K8s namespace).
- **Validates (assert).** `dto_label_name`, `dto_deployment_namespace` — both defined + non-empty.
- **Output.** No facts exported. Stdout (`stdout_lines` of `kubectl get deployment`) printed via debug (`var:` form). Register fact `k8s_list_deployment_result` is local to the include scope.
- **Callers.** `tasks-cluster-info-namespace.yaml` (aggregator, called from `cluster-info.yaml`).
- **Idempotent.** Read-only (`changed_when: false`, `failed_when: false`).

### 1.22 `tasks-k8s-list-statefulset.yaml`

- **Purpose.** Utility task — run `kubectl get statefulset -n <ns> -o wide` and print the result to the Ansible log via the `debug` module.
- **Input.** `dto_label_name` (required string, log prefix). `dto_statefulset_namespace` (required string, K8s namespace).
- **Validates (assert).** `dto_label_name`, `dto_statefulset_namespace` — both defined + non-empty.
- **Output.** No facts exported. Stdout (`stdout_lines` of `kubectl get statefulset`) printed via debug (`var:` form). Register fact `k8s_list_statefulset_result` is local to the include scope.
- **Callers.** `tasks-cluster-info-namespace.yaml` (aggregator, called from `cluster-info.yaml`).
- **Idempotent.** Read-only (`changed_when: false`, `failed_when: false`).

### 1.23 `tasks-k8s-list-ingress.yaml`

- **Purpose.** Utility task — run `kubectl get ingress -n <ns> -o wide` and print the result to the Ansible log via the `debug` module.
- **Input.** `dto_label_name` (required string, log prefix). `dto_ingress_namespace` (required string, K8s namespace).
- **Validates (assert).** `dto_label_name`, `dto_ingress_namespace` — both defined + non-empty.
- **Output.** No facts exported. Stdout (`stdout_lines` of `kubectl get ingress`) printed via debug (`var:` form). Register fact `k8s_list_ingress_result` is local to the include scope.
- **Callers.** `tasks-cluster-info-namespace.yaml` (aggregator, called from `cluster-info.yaml`).
- **Idempotent.** Read-only (`changed_when: false`, `failed_when: false`).

### 1.24 `tasks-k8s-list-secret.yaml`

- **Purpose.** Utility task — run `kubectl get secret -n <ns> -o wide` and print the result to the Ansible log via the `debug` module.
- **Input.** `dto_label_name` (required string, log prefix). `dto_secret_namespace` (required string, K8s namespace).
- **Validates (assert).** `dto_label_name`, `dto_secret_namespace` — both defined + non-empty.
- **Output.** No facts exported. Stdout (`stdout_lines` of `kubectl get secret`) printed via debug (`var:` form). Register fact `k8s_list_secret_result` is local to the include scope.
- **Callers.** `tasks-cluster-info-namespace.yaml` (aggregator, called from `cluster-info.yaml`).
- **Idempotent.** Read-only (`changed_when: false`, `failed_when: false`).

### 1.25 `tasks-k8s-list-certificate.yaml`

- **Purpose.** Utility task — run `kubectl get certificate -n <ns> -o wide` (cert-manager CRD) and print the result to the Ansible log via the `debug` module. `failed_when: false` keeps the task green if cert-manager CRDs are not installed.
- **Input.** `dto_label_name` (required string, log prefix). `dto_certificate_namespace` (required string, K8s namespace).
- **Validates (assert).** `dto_label_name`, `dto_certificate_namespace` — both defined + non-empty.
- **Output.** No facts exported. Stdout (`stdout_lines` of `kubectl get certificate`) printed via debug (`var:` form). Register fact `k8s_list_certificate_result` is local to the include scope.
- **Callers.** `tasks-cluster-info-namespace.yaml` (aggregator, called from `cluster-info.yaml`).
- **Idempotent.** Read-only (`changed_when: false`, `failed_when: false`).

### 1.26 `tasks-k8s-list-ingress-route.yaml`

- **Purpose.** Utility task — run `kubectl get IngressRoute -n <ns> -o wide` (Traefik CRD) and print the result to the Ansible log via the `debug` module. `failed_when: false` keeps the task green if Traefik CRDs are not installed.
- **Input.** `dto_label_name` (required string, log prefix). `dto_ingress_route_namespace` (required string, K8s namespace).
- **Validates (assert).** `dto_label_name`, `dto_ingress_route_namespace` — both defined + non-empty.
- **Output.** No facts exported. Stdout (`stdout_lines` of `kubectl get IngressRoute`) printed via debug (`var:` form). Register fact `k8s_list_ingress_route_result` is local to the include scope.
- **Callers.** `tasks-cluster-info-namespace.yaml` (aggregator, called from `cluster-info.yaml`).
- **Idempotent.** Read-only (`changed_when: false`, `failed_when: false`).

### 1.27 `tasks-k8s-list-tcp.yaml`

- **Purpose.** Utility task — run `kubectl get TCP -n <ns> -o wide` (HAProxy ingress controller CRD) and print the result to the Ansible log via the `debug` module. `failed_when: false` keeps the task green if HAProxy ingress controller CRDs are not installed.
- **Input.** `dto_label_name` (required string, log prefix). `dto_tcp_namespace` (required string, K8s namespace).
- **Validates (assert).** `dto_label_name`, `dto_tcp_namespace` — both defined + non-empty.
- **Output.** No facts exported. Stdout (`stdout_lines` of `kubectl get TCP`) printed via debug (`var:` form). Register fact `k8s_list_tcp_result` is local to the include scope.
- **Callers.** `tasks-cluster-info-namespace.yaml` (aggregator, called from `cluster-info.yaml`).
- **Idempotent.** Read-only (`changed_when: false`, `failed_when: false`).

### 1.28 `tasks-cluster-info-namespace.yaml`

- **Purpose.** Aggregator — for a given namespace, runs all 10 per-resource list tasks (pods, certificates, network policies, deployments, statefulsets, ingresses, services, TCPs, IngressRoutes, secrets) plus `helm list`, in a fixed order. Single composition primitive used by the `cluster-info.yaml` playbook.
- **Input.** `dto_label_name` (required string, log prefix). `dto_namespace_name` (required string, K8s namespace).
- **Validates (assert).** `dto_label_name`, `dto_namespace_name` — both defined + non-empty.
- **Output.** None — each underlying task prints its own debug output. Register facts from underlying tasks (e.g. `k8s_list_pods_result`) are visible inside the include scope.
- **Callers.** `playbook-app/cluster-info.yaml` only.
- **Idempotent.** Read-only.

### 1.29 `tasks-seaweedfs-user-sync.yaml`

- **Purpose.** Declarative SeaweedFS S3 identity sync (Layer 1, v17 filer-driven). READ current identities из **живого filer** (`s3.configure` dump, no_log — plaintext secretKey) vs target (`seaweedfs_identities + _extra`). Per target identity apply-payload сохраняет creds из filer (или генерит fresh AK/SK для new, skip 'anonymous') + считает actions/policies к ДОБАВЛЕНИЮ (set-diff vs filer). Phase A: delete стейл identities (`s3.configure -user=<n> -delete -apply`, имена из `seaweedfs_identities_to_delete`). Phase B: additive apply (creds + added actions/policies, no_log; anonymous с managed policy применяется без cred-флагов; identity не выдающая ничего — скип). **Phase C (v17 bugfix):** removal filer-лишних actions/policies (`s3.configure -user=X [-policies=<csv>][-actions=<csv>] -delete -apply`) — НИКОГДА bare `-delete` (фильтр отдаёт только entries с ≥1 непустым списком + `when`-guard). Live-reload — без рестарта S3. NO Vault combined JSON (удалён в v17 — filer = единственный источник истины). **Stateless filter API** (signature `(s3configure_raw, target_identities)`): `seaweedfs_identity_actions_to_apply` (creds + actions/policies к добавлению; kwargs key length/charset) + `seaweedfs_identity_actions_to_remove` (filer-лишние к снятию) + `seaweedfs_identities_to_delete` (стейл names).
- **Input.** `dto_label_name` (required string, log prefix). Convention: passed ONLY at playbook-level invocation; nested includes (weed-shell wrapper) inherit via Ansible variable scope.
- **Validates (assert).** `dto_label_name` defined + non-empty. Admin safety warning (debug) if target identities содержит нет entry с `actions=['Admin']`.
- **Output.** Identities реконсилены в живом filer (`/etc/iam/identities/`) через weed shell — стейл deleted (Phase A), creds + new grants applied (Phase B), filer-лишние grants removed (Phase C). Vault writes нет.
- **Callers.** `playbook-app/seaweedfs-install.yaml` STEP 4 SYNC (tag `[user-sync]`, **after** helm install — filer must be running).
- **Idempotent.** Yes — diff vs живой filer каждый run; `s3.configure -apply` аддитивен + Phase C снимает дельту; self-healing (застрявший дрейф реконсилится против реального filer).

### 1.30 `tasks-seaweedfs-identity-secret-distribute.yaml`

- **Purpose.** Declarative SeaweedFS Layer 3 identity credentials distribution. Reads identity credentials из **живого filer** (`s3.configure` dump, no_log — gated `when: has_target`; v17: Vault combined JSON key-store удалён, filer — источник creds); для каждой identity с непустым `extra_vault_paths` (inventory field, full Vault paths с mount engine prefix) distribute credentials через fixed keys `username` / `accessKey` / `secretKey` (HARD-CODED). State — **per-item ConfigMaps** `seaweedfs-sync-identity-distributions-<identity>` (СОХРАНЕНЫ в v17 — distribute единственный оставшийся потребитель ConfigMap-state; label `seaweedfs-sync-state=identity-distributions`): read = `kubectl get cm -l ... -o json` → reconstruct в combined-array JSON (`seaweedfs_state_configmaps_to_combined_json`) → diff. Phase A: vault-put per (identity, path) target pair (full replace, idempotent). Phase B: vault-delete per state path не в target. Phase C: apply per-item state ConfigMaps. Phase D: delete стейл per-item state ConfigMaps. **Stateless filter API** (signature `(s3configure_raw, target_identities, configmap_raw_json)`): `seaweedfs_distribute_paths_to_add` (creds из s3configure_raw, no_log) + `seaweedfs_distribute_paths_to_delete` (s3configure_raw игнор — diff target vs ConfigMap state) + state-CM filters (`seaweedfs_state_configmaps_to_combined_json`, `seaweedfs_distribute_configmaps_to_apply`, `seaweedfs_state_configmaps_to_delete`); validations (anonymous-no-extra-paths, paths-unique, creds-exist vs filer dump) внутри compute функций — fail-fast.
- **Input.** `dto_label_name` (required string, log prefix). Convention: passed ONLY at playbook-level invocation; nested includes (weed-shell wrapper, `tasks-vault-put`, `tasks-vault-delete`) inherit via Ansible variable scope.
- **Validates (assert).** `dto_label_name` defined + non-empty. Anonymous identity с непустым `extra_vault_paths` → fail (нет creds). Target paths unique across all identities. Each target identity (с extra_vault_paths) exists в filer s3.configure dump.
- **Output.** Identity credentials распределены в Vault paths (via `tasks-vault-put`, fixed keys), стейл state paths удалены (via `tasks-vault-delete`), ConfigMap state updated.
- **Callers.** `playbook-app/seaweedfs-install.yaml` STEP 4 SYNC (tag `[identity-distribute]`, after user-sync — **after** helm install).
- **Idempotent.** Yes — vault-put full replace, vault-delete idempotent на missing path, diff vs ConfigMap state.

### 1.31 `tasks-seaweedfs-bucket-sync.yaml`

- **Purpose.** Declarative SeaweedFS Layer 2 buckets + quotas + owner (v17 filer-driven; bucket policies удалены). Schema per bucket: `{name, owner, replication, rack?, dataCenter?, quota_size?}` (name/owner/replication обязательны, rack/dataCenter optional immutable). READ current state из **живого filer** двумя командами — `fs.configure` (location config: replication/rack/dataCenter) + `s3.bucket.list` (existence + owner) → diff vs target (`seaweedfs_sync_buckets + _extra`) by name primary key. Validate replication format + rack/dataCenter optional-string внутри каждой compute функции. **Pre-phase fail-fast ASSERT** (после compute calls, до Phase A): если ЛЮБОЕ из immutable полей (replication, rack или dataCenter) changed на kept bucket vs filer → sync aborts с detailed message, cluster intact. Sync order: A) delete стейл buckets via `s3.bucket.delete -name=<n>`; B) create new via `s3.bucket.create -name=<n> -owner=<owner>`; C) `fs.configure -locationPrefix=/buckets/<n> -replication=<r> [-rack][-dataCenter] -apply` для new; D) reconcile owner у kept via `s3.bucket.owner` (owner mutable); E) apply quotas всем target buckets via `s3.bucket.quota -op=set|remove -sizeMB=<n>` (size_mib pre-computed Python-side). NO ConfigMap state (удалён в v17). **Stateless filter API:** diff filters (`seaweedfs_buckets_to_delete`, `_to_create`, `_owners_to_set`, `_immutable_violations`, signature `(fs_configure_raw, bucket_list_raw, target_buckets)`) + `seaweedfs_buckets_quotas_to_apply` (signature `(target_buckets)` single-arg — annotates `_quota_op` + `_quota_size_mib`).
- **Input.** `dto_label_name` (required string, log prefix). Convention: passed ONLY at playbook-level invocation.
- **Validates (assert).** `dto_label_name` defined + non-empty. Immutable settings (replication/rack/dataCenter) fail-fast assert per `seaweedfs_buckets_immutable_violations` (после compute calls, до Phase A).
- **Output.** Buckets created/updated/deleted в filer Postgres metadata; owner set per-bucket (create + reconcile); quotas applied.
- **Callers.** `playbook-app/seaweedfs-install.yaml` STEP 4 SYNC (tag `[bucket-sync]`, **after** install — before post; filer must be running).
- **Idempotent.** Yes — diff vs живой filer каждый run. `s3.bucket.delete` без `-force` флага но делает hard delete (CollectionDelete sweeps все volumes/objects; Object Lock с locked objects — единственное препятствие).

### 1.32 `tasks-seaweedfs-policy-sync.yaml`

- **Purpose.** Declarative SeaweedFS Layer P managed-policy sync (v17 filer-driven). Diff target (`seaweedfs_managed_policies + _extra`, `{name, document}` AWS IAM doc) vs **живой filer**. READ current policies via `weed shell s3.policy -list` (имена + JSON-документы) → diff. Phase A: delete стейл policies via `s3.policy -delete -name=<n>`. Phase B: put changed/new policies (semantic doc-compare, order-insensitive — только changed/new, НЕ put-all) via `s3.policy -put -name=<n> -file=<tmp>` — document пишется в pod `/tmp` файл и применяется в одном `kubectl exec`. NO ConfigMap state (удалён в v17). Managed policies прикрепляются к identities через `policy_names` (user-sync, `s3.configure -policies`). **Stateless filter API** (signature `(s3policy_list_raw, target_policies)`): `seaweedfs_policies_to_put` + `seaweedfs_policies_to_delete`.
- **Input.** `dto_label_name` (required string, log prefix). Convention: passed ONLY at playbook-level invocation.
- **Validates (assert).** `dto_label_name` defined + non-empty. Per-policy `name` (required non-empty string) + `document` (required non-empty mapping, single-quote shell guard) внутри compute функций — fail-fast.
- **Output.** Managed policies put/deleted в filer `/etc/iam/policies/`.
- **Callers.** `playbook-app/seaweedfs-install.yaml` STEP 4 SYNC (tag `[policy-sync]`, **первый** в sync-блоке — ДО user-sync, после install; policy должна существовать до identity attach).
- **Idempotent.** Yes — diff vs живой filer каждый run; `s3.policy -put` idempotent overwrite (self-healing).

### 1.33 `tasks-seaweedfs-weed-shell.yaml`

- **Purpose.** Fail-fast обёртка вокруг одного `kubectl exec -i deploy/seaweedfs-s3 -- weed shell` вызова. `weed shell` из pipe ВСЕГДА завершается с exit 0 даже при ошибке команды (пишет `error: ` / `unknown command: ` в stderr и возвращает success — `sources/seaweedfs/weed/shell/shell_liner.go:144-151`), поэтому обёртка инспектирует stderr и валит таск на этих маркерах. Два режима: **APPLY** (мутации `s3.*`/`fs.*`) и **READ** (filer dump через `dto_weed_capture_fact`).
- **Input.** `dto_label_name` (log prefix, наследуется из scope caller'а), `dto_weed_command` (строка команды weed shell). **Optional (file-staging — для `s3.policy -put`, set BOTH):** `dto_weed_stdin_payload` + `dto_weed_stdin_path`. **Optional (READ mode):** `dto_weed_capture_fact` (имя caller-факта, в который публикуется stdout команды — для filer reads `s3.configure` / `s3.policy -list` / `fs.configure` / `s3.bucket.list`; caller читает через `lookup('vars', <name>)`). **Optional (security):** `dto_weed_no_log` (bool, default false) → `no_log` на exec + capture (plaintext secretKey в `s3.configure` dump + `-secret_key=` в apply) + redact имени таска (no_log сам имя не скрывает).
- **Validates (assert).** `dto_label_name`, `dto_weed_command` — defined + non-empty.
- **Output.** Команда выполняется в живом filer. `register` + `failed_when` валят таск, если stderr matches `(^|\n)error: ` или `(^|\n)unknown command: `. `changed_when: false`; `timeout: 120`. READ mode: capture-task публикует `.stdout` в `dto_weed_capture_fact` (`when: dto_weed_capture_fact is defined`).
- **Callers.** policy-sync (READ `s3.policy -list` + Phase A/B), user-sync (READ `s3.configure` + Phase A/B/C), bucket-sync (READ `fs.configure` + `s3.bucket.list` + Phase A-E), identity-distribute (READ `s3.configure`). APPLY — looped includes; READ — non-looped includes.
- **Idempotent.** N/A — выполняет переданную команду. **Invocation contract:** `run_once: true` ВСЕГДА на include (и APPLY-loop, и READ); inner-таски обёртки несут `delegate_to`, но НЕ `run_once` (inner `run_once` внутри looped dynamic include молча скипает все итерации). READ — только non-looped include (capture-fact mechanism).

---

## 2. `playbook-system/tasks/` (20 tasks)

### 2.1 Guard / preflight

#### `tasks-require-limit.yaml`

- **Purpose.** Fails immediately if `--limit` was not passed.
- **Input.** None.
- **Output.** Assertion.
- **Callers.** `node-install.yaml`, `cluster-init.yaml`, `manager-join.yaml`, `worker-join.yaml`, `node-remove.yaml`, `server-clean.yaml`.
- **Idempotent.** Yes (check-only).

#### `tasks-require-manager.yaml`

- **Purpose.** Asserts current host is in `managers` group.
- **Callers.** `cluster-init.yaml`, `manager-join.yaml`.
- **Idempotent.** Yes.

#### `tasks-require-worker.yaml`

- **Purpose.** Asserts current host is in `workers` group.
- **Callers.** `worker-join.yaml`.
- **Idempotent.** Yes.

### 2.2 Cluster state discovery

#### `tasks-set-master-manager.yaml`

- **Purpose.** Internal helper called by `tasks-pre-check.yaml`, каждым из 9 system playbook'ов которым нужны cluster state facts, и каждым из 5 playbook'ов с reboot (для resolve `bastion_host_fact` до вызова `tasks-reboot-cluster.yaml`). Iterates inventory `managers` group, picks the host with `is_master: true`. Additionally iterates `all` group and picks the host with `is_bastion: true` (optional on-node bastion — see [`variables.md`](variables.md) §2.14.1). Bastion finding co-located here as conscious tech-debt — to be split into separate task in a future refactor.
- **Input.** None.
- **Output.** `master_manager_fact` (string), `is_master_manager_exist` (bool), `bastion_host_fact` (string, undefined if no `is_bastion: true` host), `is_bastion_host_exist` (bool).
- **Callers.** `tasks-pre-check.yaml`; в начале `tasks:` каждого system playbook'а которому нужны cluster state facts — `cluster-init.yaml`, `manager-join.yaml`, `worker-join.yaml`, `apiserver-sans-update.yaml`, `etcd-key-rotate.yaml`, `haproxy-apiserver-lb-update.yaml`, `node-drain-on.yaml`, `node-drain-off.yaml`, `node-remove.yaml`; в начале `tasks:` каждого playbook'а который использует `tasks-reboot-cluster.yaml` — `set-hostname.yaml`, `node-prepare.yaml`, `cilium-prepare.yaml`, `longhorn-prepare.yaml`, `linux-service-configure.yaml`.
- **Idempotent.** Pure fact derivation.

#### `tasks-set-is-cluster-init.yaml`

- **Purpose.** Detects whether the cluster has been initialized (via `kubeadm token list` + `kubectl get nodes`).
- **Output.** `is_cluster_init` (bool).
- **Idempotent.** Yes.

#### `tasks-set-is-node-joined.yaml`

- **Purpose.** Detects whether the current host is in `kubectl get nodes` output.
- **Output.** `is_node_joined` (bool).
- **Idempotent.** Yes.

### 2.3 Bootstrap primitives

#### `tasks-kubeadm-config-create.yaml`

- **Purpose.** Render `{{ kubeadm_config_path }}` from `kubeadm_config_template` (in `hosts-vars/kubeadm-config.yaml`). Builds `certSANs` dynamically from every manager's `ansible_host`, `api_server_advertise_address`, `haproxy_apiserver_lb_host`, plus `localhost`.
- **Input.** (reads inventory + `k8s-base.yaml` + `kubeadm-config.yaml` vars).
- **Output.** File on disk.
- **Callers.** `cluster-init.yaml`, `apiserver-sans-update.yaml`.
- **Idempotent.** Template rewrite — content changes only when inventory/vars change.

#### `tasks-kubectl-configure.yaml`

- **Purpose.** Create `/root/.kube/config` on the host (copies `/etc/kubernetes/admin.conf` with correct ownership).
- **Callers.** `cluster-init.yaml`, `manager-join.yaml`.
- **Idempotent.** Copy is overwriting but content-stable after init.

#### `tasks-apply-node-labels.yaml`

- **Purpose.** `kubectl label node <host> <label>=<value>` for each entry in the host's `node_labels` list.
- **Callers.** `cluster-init.yaml`, `manager-join.yaml`, `worker-join.yaml`.
- **Idempotent.** `--overwrite` used.

#### `tasks-untaint-control-plane.yaml`

- **Purpose.** Remove `node-role.kubernetes.io/control-plane:NoSchedule` taint — for single-node / dev clusters where the master also runs workloads.
- **Callers.** `cluster-init.yaml` (conditional on a toggle).
- **Idempotent.** Remove-if-present.

#### `tasks-kubelet-health-wait.yaml`

- **Purpose.** Block until kubelet systemd service is `active (running)` and its health endpoint responds.
- **Callers.** After `kubeadm init`, after `kubeadm join`.
- **Idempotent.** Read-only wait.

#### `task-apiserver-restart.yaml`

- **Purpose.** Restart the apiserver static pod on the current host by moving `/etc/kubernetes/manifests/kube-apiserver.yaml` to `/tmp/`, waiting for `/healthz` to stop, moving it back, waiting for `/healthz` + `/readyz`.
- **Note.** Singular `task-` prefix (not `tasks-`) — historical, keep as-is.
- **Callers.** `apiserver-sans-update.yaml`, `etcd-key-rotate.yaml` — always under `serial: 1` to preserve quorum.
- **Idempotent.** Yes.

#### `tasks-reboot-cluster.yaml`

- **Purpose.** Bastion-aware ordered cluster reboot. Reboots non-bastion hosts first (parallel), then bastion host last. Avoids race condition where bastion-as-on-node-host kills ProxyJump tunnel during its own reboot.
- **Input.** `dto_label_name` (string, log prefix), `dto_reboot_when_condition` (bool — per-host pre-computed condition; caller evaluates fact-based logic and passes resulting bool).
- **Validates (assert).** Both required params defined + non-empty; `dto_reboot_when_condition is boolean`. Tag `[always]`.
- **Precondition.** Caller must resolve `bastion_host_fact` before invoking (typically by including `tasks-set-master-manager.yaml` as the first task in the playbook's `tasks:` block). This task does NOT auto-resolve facts — dependency is explicit at the playbook level (SRP).
- **Output.** Hosts rebooted in two-phase order (non-bastion parallel, then bastion). Reboot message hardcoded as `"Reboot"` in both phases. No facts modified.
- **Callers.** `set-hostname.yaml`, `node-prepare.yaml`, `cilium-prepare.yaml`, `longhorn-prepare.yaml`, `linux-service-configure.yaml`.
- **Idempotent.** Yes — reboot module is no-op if condition false.
- **Backward compat.** If `bastion_host_fact` is undefined (no `is_bastion: true` in inventory) — task 1 reboots ALL hosts in parallel (`inventory_hostname != ''` always true), task 2 skipped. Equivalent to old inline `reboot:` behavior.

### 2.4 HAProxy LB

#### `tasks-haproxy-lb-config-create.yaml`

- **Purpose.** Render `/etc/haproxy/haproxy.cfg` from inventory managers list. Managers use `[127.0.0.1 + other managers]` as backends; workers use `[all managers]`.
- **Input.** `hosts-vars/k8s-base.yaml` HAProxy vars + inventory.
- **Callers.** `node-install.yaml` (sub-play `haproxy-apiserver-lb.yaml`), `haproxy-apiserver-lb-update.yaml`.
- **Idempotent.** Template rewrite.

#### `tasks-haproxy-lb-restart.yaml`

- **Purpose.** `systemctl restart haproxy`. Use for forced restart (config reload is preferable; rolling updates via `haproxy-apiserver-lb-update.yaml` use `serial: 1`).
- **Callers.** `node-install.yaml`, `haproxy-apiserver-lb-update.yaml`.
- **Idempotent.** Yes.

#### `tasks-haproxy-lb-health-wait.yaml`

- **Purpose.** Wait until `haproxy_apiserver_lb_host:haproxy_apiserver_lb_port` accepts connections.
- **Callers.** `node-install.yaml`, `haproxy-apiserver-lb-update.yaml`.
- **Idempotent.** Read-only wait.

### 2.5 Package install

#### `tasks-deb-install.yaml`

- **Purpose.** Install a local `.deb` package on a target host: copy from control machine, refresh apt cache, install via `apt: deb:` (auto-resolves dependencies via standard Ubuntu repos), `apt-mark hold`, then cleanup. Designed as a reusable primitive for offline / AirGap-friendly installations of deb packages.
- **Input.** `dto_label_name` (string, log prefix), `dto_deb_local_path` (string, path relative to `project_root`), `dto_apt_mark_hold_pkg_name` (string, package name to hold).
- **Validates (assert).** All 3 params defined + non-empty. Tag `[always]`.
- **Output.** Package installed and held; `/tmp/<basename>.deb` removed after install.
- **Callers.** `playbook-system/haproxy-apiserver-lb.yaml` (when `haproxy_apiserver_lb_install_method: local_deb`).
- **Idempotent.** Yes — `apt: deb:` is a no-op if the same package version is already installed.

#### `tasks-tarball-install.yaml`

- **Purpose.** Install a single binary from a local `.tar.gz` tarball: copy from control machine, extract on the target host, copy the binary to its final install path with mode `0755`, then cleanup the tarball and the extracted directory. Designed as a reusable primitive for offline / AirGap-friendly installations of single-binary tarball-distributed tools.
- **Input.** `dto_label_name` (string, log prefix), `dto_tarball_local_path` (string, path relative to `project_root`), `dto_extract_dest` (string, directory on target host where to extract), `dto_binary_src_path` (string, full path to the binary on the target host after extraction), `dto_binary_install_path` (string, final install path), `dto_extracted_dir_to_cleanup` (string, top-level extracted directory to remove during cleanup).
- **Validates (assert).** All 6 params defined + non-empty. Tag `[always]`.
- **Output.** Binary installed at `dto_binary_install_path` with mode `0755`; tarball and extracted directory removed.
- **Callers.** `playbook-system/install-helm.yaml` (when `helm_install_method: local_tarball`).
- **Idempotent.** Yes — `copy` and `unarchive` overwrite if the source content differs; cleanup is `state: absent`.

### 2.6 `tasks-iperf3-server-start.yaml`

- **Purpose.** Start an iperf3 server on a given port in detached background (`nohup` + redirected stdout/stderr + `&`), then wait until the port begins listening. Uses `shell:` module (required for redirection and backgrounding) with `async: 0 poll: 0` — fire-and-forget so Ansible does not wait for the SSH channel to close on the backgrounded process.
- **Input.** `dto_label_name` (string, log prefix), `dto_iperf3_port` (int/string — port for the iperf3 server to listen on).
- **Validates (assert).** Both params defined + non-empty. Tag `[always]`.
- **Output.** iperf3 server process running in background; port `dto_iperf3_port` listening (verified via `wait_for state=started timeout=10`); log written to `/tmp/iperf3-server-<port>.log` on the host.
- **Callers.** `playbook-system/network-bandwidth-test.yaml` (Phase 5, `block:`).
- **Idempotent.** No — re-invocation spawns a second iperf3 server process. Caller must invoke `tasks-iperf3-server-stop.yaml` before re-starting, or use a different port.

### 2.7 `tasks-iperf3-server-stop.yaml`

- **Purpose.** Kill the iperf3 server process on a given port via `pkill -f "iperf3 -s -p <port>"` (narrow match by port — does not affect iperf3 servers on other ports), wait for the port to be free, then remove the `/tmp/iperf3-server-<port>.log` log file.
- **Input.** `dto_label_name` (string, log prefix), `dto_iperf3_port` (int/string — port of the iperf3 server to stop).
- **Validates (assert).** Both params defined + non-empty. Tag `[always]`.
- **Output.** iperf3 server process on `dto_iperf3_port` killed; port free (verified via `wait_for state=stopped timeout=10`); log file removed.
- **Callers.** `playbook-system/network-bandwidth-test.yaml` (Phase 5, `always:`).
- **Idempotent.** Yes — `pkill` with rc=1 («no process matched») accepted via `failed_when: rc not in [0,1]`. Repeated stop is a no-op.

### 2.8 `tasks-sync-managed-files.yaml`

- **Purpose.** Reconcile contents of a `*/conf.d/`-style directory with an Ansible-managed desired-state list. Pattern: inventory = single source of truth, server state brought to match it. Phases: assert inputs → ensure target directory exists → find existing prefixed files → compute orphans (existing − expected) → delete orphans → write desired files → set output fact (bool, true on any change). Caller-supplied `dto_filename_prefix` gates orphan detection.
- **Input.** `dto_label_name` (string, log prefix), `dto_target_dir` (string, absolute directory path), `dto_files_list` (sequence of `{filename, content}` items; empty list valid), `dto_filename_prefix` (string, prefix for managed files — e.g. `"ansible-"`), `dto_res_fact_changed` (string, name of output fact to set). Optional: `dto_file_mode` (default `'0644'`), `dto_file_owner` (default `'root'`), `dto_file_group` (default `'root'`).
- **Validates (assert).** All 5 required `dto_*` params defined + non-empty. `dto_target_dir` starts with `/`. `dto_files_list` is sequence. Per-item: `filename` + `content` non-empty; `filename` starts with `dto_filename_prefix` and contains no `/`. Unique filenames in list. Tag `[always]`.
- **Output.** Files in `dto_target_dir` matching `dto_files_list` (managed files written + orphans by `dto_filename_prefix` deleted). Output fact `{{ dto_res_fact_changed }}` set to `true` if any file was written or deleted, `false` otherwise.
- **Callers.** `playbook-system/linux-service-configure.yaml` (five calls — APT phase: three calls in `/etc/apt/{sources.list.d, apt.conf.d, preferences.d}/`; FAIL2BAN phase: one call in `/etc/fail2ban/jail.d/`; SSHD phase: one call in `/etc/ssh/sshd_config.d/`).
- **Idempotent.** Yes — on re-run with same `dto_files_list`, all `copy` and `file: state=absent` tasks are no-ops; output fact will be `false`.

---

## 3. Common Usage Templates

### 3.1 Beginning of an app install playbook

```yaml
- include_tasks: "{{ project_root }}/playbook-app/tasks/tasks-pre-check.yaml"
  vars:
    dto_label_name: "<c>-install-pre-check"
  tags: [always]

- include_tasks: "{{ project_root }}/playbook-app/tasks/tasks-forbid-kube-system.yaml"
  vars:
    dto_label_name: "<c>-install-pre-check"
    dto_target_namespace: "{{ <c>_namespace }}"
  tags: [always]

- include_tasks: "{{ project_root }}/playbook-app/tasks/vault/tasks-vault-config-verify.yaml"
  vars:
    dto_label_name: "<c>-install-init"
  tags: [always]

- include_tasks: "{{ project_root }}/playbook-app/tasks/tasks-eso-verify.yaml"
  vars:
    dto_label_name: "<c>-install-init"
    dto_eso_secrets_list: "{{ eso_vault_integration_<c>_secrets + (eso_vault_integration_<c>_secrets_extra | default([])) }}"
    dto_eso_integration_object: "{{ eso_vault_integration_<c> }}"
    dto_namespace: "{{ <c>_namespace }}"
  tags: [always]
```

### 3.2 Standard phase skeleton

```yaml
- include_tasks: "{{ project_root }}/playbook-app/tasks/tasks-copy-chart.yaml"
  vars:
    dto_label_name: "<c>-install-<phase>"
    chart_name: "<c>-<phase>"             # or just "<c>" for install phase
    chart_local_src: "{{ project_root }}/playbook-app/charts/<c>/<phase>/"   # trailing / required
    chart_remote_dest: "{{ remote_charts_dir }}/<c>/<phase>"    # no trailing /
  tags: [<phase>]

- name: "[<c>-install-<phase>] Create values-override.yaml"
  copy:
    content: |
      ...
    dest: "{{ remote_charts_dir }}/<c>/<phase>/values-override.yaml"
  delegate_to: "{{ master_manager_fact }}"
  run_once: true
  tags: [<phase>]

- name: "[<c>-install-<phase>] Helm upgrade"
  command: >
    helm upgrade --install <c>-<phase> {{ remote_charts_dir }}/<c>/<phase>
    --namespace {{ <c>_namespace }} --create-namespace
    --values {{ remote_charts_dir }}/<c>/<phase>/values-override.yaml
    --cleanup-on-fail --atomic --wait --wait-for-jobs
    --timeout {{ <c>_helm_timeout }}
  delegate_to: "{{ master_manager_fact }}"
  run_once: true
  tags: [<phase>]
```

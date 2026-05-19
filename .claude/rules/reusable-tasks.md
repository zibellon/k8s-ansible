# Reusable Task Catalog

Every task include, split by repo half. Per task: **purpose**, **input vars**, **output facts / side effects**, **typical callers**, **idempotency notes**.

General rules for callers:

- Always use `include_tasks` (dynamic), never `import_tasks` — tag inheritance breaks with imports.
- Always pass `dto_label_name` matching the enclosing `[<c>-<action>-<phase>]` prefix — keeps logs aligned.
- Tag every include with the appropriate phase (`[always]`, `[pre]`, `[install]`, `[post]`, or a bootstrap-specific tag).
- Tasks that run `kubectl` / `helm` set `delegate_to: "{{ master_manager_fact }}"` + `run_once: true` internally — no need for the caller to set them.
- **Every task file starts with an `assert` block** that validates all required input parameters before doing any work. The assert is `tags: [always]` so it never silently skips under `--tags`. Reference: `tasks-k8s-secret-get.yaml`. Full pattern in `playbook-conventions.md` Rule 19.

---

## 1. `playbook-app/tasks/` (31 tasks)

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

### 1.8a `tasks-vault-policies-roles-merge.yaml`

- **Purpose.** Merge base + `_extra` for Vault policies and roles. Validate internal consistency.
- **Input.** Reads from inventory: `vault_policies`, `vault_policies_extra`, `vault_roles`, `vault_roles_extra` (all optional — `_extra` default `[]`).
- **Output (runtime facts).**
  - `vault_policies_final` — `vault_policies + (vault_policies_extra | default([]))`.
  - `vault_roles_final` — `vault_roles + (vault_roles_extra | default([]))`.
- **Validates.**
  - Unique `name` within `vault_policies_final` — fails with the duplicate name.
  - Unique `name` within `vault_roles_final` — fails with the duplicate name.
  - Every role's `policies` list entries exist in `vault_policies_final` — fails with role name + missing policy.
- **Callers.** Only `playbook-app/vault-install.yaml` (at `tags: [always]`). Not called from component install playbooks.
- **Idempotent.** Pure merge + validation.

### 1.8b `tasks-eso-secrets-merge.yaml`

- **Purpose.** Merge per-component base + `_extra` secrets lists for all 8 ESO-integrated components. Validate uniqueness within each merged list.
- **Input.** Reads from inventory: `eso_vault_integration_<c>_secrets` and `eso_vault_integration_<c>_secrets_extra` for each of the 8 components: `traefik`, `haproxy`, `longhorn`, `gitlab`, `gitlab_runner`, `zitadel`, `argocd`, `mon_system`. `_extra` defaults to `[]` if absent.
- **Merge order.** Base + extra (extra appended at the end).
- **Output (runtime facts).**
  - `eso_vault_integration_<c>_secrets_merged` for each of the 8 components — list of ExternalSecret dicts.
- **Validates (per-component).** Unique `external_secret_name` across the merged list; unique `body.target.name` across the merged list. Fails with component name + duplicate value.
- **Callers.** Every ESO-integrated install/configure playbook at `tags: [always]`: `traefik-install.yaml`, `haproxy-install.yaml`, `longhorn-install.yaml`, `gitlab-install.yaml`, `gitlab-configure.yaml`, `gitlab-runner-install.yaml`, `argocd-install.yaml`, `argocd-configure.yaml`, `zitadel-install.yaml`, `mon-system-install.yaml`. Also `vault-install.yaml` (after `tasks-vault-policies-roles-merge.yaml`).
- **Idempotent.** Pure merge + validation.

### 1.9 `tasks-resolve-acme-solver.yaml`

- **Purpose.** Look up a `ClusterIssuer` by name in `cert_manager_cluster_issuers`, find the solver matching a given `ingressClass`, export its `podLabels` and the full ClusterIssuer/solver dicts to fixed-name global facts. Downstream NetworkPolicies use these labels to admit the cert-manager solver pod.
- **Input.** `dto_label_name`, `dto_cluster_issuer_name`, `dto_ingress_class_name`.
- **Validates (assert).** All 3 params defined + non-empty (assert block at top of file).
- **Output (runtime facts, fixed names — global, not per-component).** `acme_cluster_issuer_result_fact` (full ClusterIssuer dict), `acme_solver_result_fact` (full solver dict), `acme_pod_labels_result_fact` (`podLabels` dict). Only one ClusterIssuer/solver is resolved per playbook run, so global fact names cause no conflicts.
- **Callers.** Install playbooks of components with HTTPS ingress that triggers ACME HTTP-01 — typically at `tags: [always]`.
- **Idempotent.** Pure lookup.

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

- **Purpose.** Read one KV v2 field from Vault into an Ansible fact (plus an `_exists` boolean).
- **Input.** `dto_label_name`, `dto_vault_get_path` (full KV path), `dto_vault_get_field` (field name), `dto_vault_get_res_fact_name` (output fact name).
- **Validates (assert).** `dto_label_name`, `dto_vault_get_path`, `dto_vault_get_field`, `dto_vault_get_res_fact_name` all defined + non-empty.
- **Output.** `<fact>` + `<fact>_exists`. Missing fields set `_exists: false` without failing the play.
- **Callers.** `-configure` playbooks (resolve current credentials before rotating), `-rotate` playbooks.
- **Idempotent.** Read-only.

### 1.13 `tasks-vault-put.yaml`

- **Purpose.** `vault kv put`, then annotate the target ExternalSecret to force ESO sync, then wait for the downstream K8s `Secret` to appear.
- **Input.** `dto_label_name`, `dto_vault_put_path` (full KV path), `dto_vault_put_data` (non-empty dict of `{field: value}`).
- **Validates (assert).** `dto_label_name` defined + non-empty; `dto_vault_put_path` defined + non-empty; `dto_vault_put_data` defined, is mapping, non-empty.
- **Output.** Vault updated, K8s Secret updated.
- **Callers.** Rotation flows (Postgres, Redis, MinIO, GitLab root, ArgoCD admin, Vault admin-token).
- **Idempotent.** Re-running re-puts identical values — safe, just a noop in ESO (force-sync annotation bumps once more).

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

- **Purpose.** Read a single `.data` field from a K8s Secret into a named fact. Never fails on missing secret or field — callers branch on `<fact>_exists`.
- **Input.** `dto_label_name`, `dto_secret_namespace`, `dto_secret_name`, `dto_secret_field`, `dto_secret_res_fact_name` (all required).
- **Validates (assert).** All 5 dto params defined + non-empty. **Reference implementation** — this is the canonical example for the assert pattern.
- **Output (runtime facts, set on all hosts).**
  - `{{ dto_secret_res_fact_name }}` — decoded string value (`''` if missing).
  - `{{ dto_secret_res_fact_name }}_exists` — bool (true only if field is non-empty).
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

- include_tasks: "{{ project_root }}/playbook-app/tasks/tasks-eso-secrets-merge.yaml"
  tags: [always]

# If ingress uses ACME:
- include_tasks: "{{ project_root }}/playbook-app/tasks/tasks-resolve-acme-solver.yaml"
  vars:
    dto_label_name: "<c>-install-init"
    dto_cluster_issuer_name: "{{ <c>_cluster_issuer_name }}"
    dto_ingress_class_name: "{{ <c>_ingress_class_name }}"
  tags: [always]
# Output facts (global, used in downstream tasks/charts):
#   - acme_cluster_issuer_result_fact (full ClusterIssuer dict)
#   - acme_solver_result_fact (full solver dict)
#   - acme_pod_labels_result_fact (podLabels — typically referenced in NP charts)
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

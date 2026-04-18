# Reusable Task Catalog

Every task include, split by repo half. Per task: **purpose**, **input vars**, **output facts / side effects**, **typical callers**, **idempotency notes**.

General rules for callers:

- Always use `include_tasks` (dynamic), never `import_tasks` — tag inheritance breaks with imports.
- Always pass `label_name` matching the enclosing `[<c>-<action>-<phase>]` prefix — keeps logs aligned.
- Tag every include with the appropriate phase (`[always]`, `[pre]`, `[install]`, `[post]`, or a bootstrap-specific tag).
- Tasks that run `kubectl` / `helm` set `delegate_to: "{{ master_manager_fact }}"` + `run_once: true` internally — no need for the caller to set them.
- **Every task file starts with an `assert` block** that validates all required input parameters before doing any work. The assert is `tags: [always]` so it never silently skips under `--tags`. Reference: `tasks-k8s-secret-get.yaml`. Full pattern in `playbook-conventions.md` Rule 19.

---

## 1. `playbook-app/tasks/` (16 tasks)

### 1.1 `tasks-pre-check.yaml`

- **Purpose.** Entry guard for every install playbook. Resolves `master_manager_fact` and asserts cluster is reachable.
- **Input.** `label_name` (string — log prefix).
- **Validates (assert).** `label_name` defined + non-empty. (No `delegate_to` — `master_manager_fact` not yet set at call time.)
- **Output.** Fact `master_manager_fact`. Fails play if no manager has `is_master: true`.
- **Callers.** Every `<c>-install.yaml`, also `-configure`, `-restart`, `-rotate` playbooks.
- **Idempotent.** Read-only; safe to call repeatedly.

### 1.2 `tasks-set-master-manager.yaml`

- **Purpose.** Internal helper called by `tasks-pre-check.yaml`. Iterates inventory `managers` group, picks the host with `is_master: true`.
- **Input.** None.
- **Output.** `master_manager_fact`, `is_master_manager_exist` (bool).
- **Callers.** `tasks-pre-check.yaml`, rarely standalone.
- **Idempotent.** Pure fact derivation.

### 1.3 `tasks-forbid-kube-system.yaml`

- **Purpose.** Guardrail — refuses to operate on `kube-system` namespace.
- **Input.** `label_name`, `namespace_value` (the component's target namespace).
- **Output.** Fails play if `namespace_value == "kube-system"`.
- **Callers.** Every `<c>-install.yaml` at `tags: [always]`.
- **Idempotent.** Assertion only.

### 1.4 `tasks-copy-chart.yaml`

- **Purpose.** Package a local chart directory as tar.gz, copy to the master manager, extract. Faster and more idempotent than `synchronize` for many small files.
- **Input.** `label_name`, `chart_name` (release name — used for the temp archive file), `chart_local_src` (**must** end with `/`), `chart_remote_dest` (**must not** end with `/`).
- **Validates (assert).** `label_name`, `chart_name`, `chart_local_src`, `chart_remote_dest` all defined + non-empty.
- **Output.** Chart files at `{{ chart_remote_dest }}/` on the master manager.
- **Callers.** Every phase of every install playbook.
- **Idempotent.** Re-extraction overwrites. Old files not pruned — if you rename a template, re-run `server-clean` on chart dir or `rm -rf` the remote dir.
- **Gotcha.** Missing trailing slash on `chart_local_src` creates a nested dir.

### 1.4а `tasks-copy-helm-values.yaml`

- **Purpose.** Create a remote directory + write `values-override.yaml`. Used for external Helm chart install phases where `tasks-copy-chart.yaml` is not called (no local chart to ship).
- **Input.** `label_name` (string), `dto_dir` (remote path, no trailing slash), `dto_content` (rendered string — the YAML content to write).
- **Validates (assert).** `label_name`, `dto_dir`, `dto_content` all defined + non-empty.
- **Output.** Directory `{{ dto_dir }}` exists on master manager, file `{{ dto_dir }}/values-override.yaml` written (mode 0644).
- **Callers.** Install-phase blocks in any playbook that uses an external Helm repo: `cilium-install.yaml`, `traefik-install.yaml`, `cert-manager-install.yaml`, `external-secrets-install.yaml`, `metrics-server-install.yaml`, `haproxy-install.yaml`, `longhorn-install.yaml`, `gitlab-install.yaml`, `gitlab-runner-install.yaml`, `zitadel-install.yaml`, `teleport-install.yaml`, `vault-install.yaml` (operator phase).
- **Idempotent.** Yes — `file: state=directory` and `copy` are both idempotent.
- **Gotcha.** `dto_content` is evaluated at include-time in the caller's variable scope. Always pass the fully-rendered string (e.g., `"{{ my_helm_values | to_nice_yaml }}"`).

### 1.5 `tasks-add-helm-repo.yaml`

- **Purpose.** `helm repo add` + `helm repo update`. Used before installing official external charts.
- **Input.** `label_name`, `helm_repo_name`, `helm_repo_url`.
- **Validates (assert).** `label_name`, `helm_repo_name`, `helm_repo_url` all defined + non-empty.
- **Output.** Helm repo registered on the master manager.
- **Callers.** `cilium-install.yaml`, `traefik-install.yaml`, `cert-manager-install.yaml` (and any other playbook that installs from an external chart).
- **Idempotent.** `helm repo add` with same URL is no-op; `update` is always safe.

### 1.6 `tasks-wait-crds.yaml`

- **Purpose.** Wait for CRDs to reach `Established` condition before applying workloads that depend on them.
- **Input.** `label_name`, `crds_list` (list of `"crd/<name>"` strings), `crds_wait` (dict: `timeout`, `retries`, `delay`).
- **Validates (assert).** `label_name` defined + non-empty; `crds_list` defined, is sequence, non-empty; `crds_wait` defined, is mapping, with `timeout`/`retries`/`delay` subkeys defined.
- **Output.** None. Fails on timeout.
- **Callers.** Install playbooks with `crds/` phases (`argocd`, `mon-prometheus-operator`), Vault operator, cert-manager.
- **Idempotent.** Read-only wait.

### 1.7 `tasks-wait-rollout.yaml`

- **Purpose.** `kubectl rollout status` for Deployments / DaemonSets / StatefulSets.
- **Input.** `label_name`, `rollout_namespace`, `rollout_timeout` (e.g. `"120s"`), `rollout_resources` (list of `"<kind>/<name>"`).
- **Validates (assert).** `label_name`, `rollout_namespace`, `rollout_timeout` defined + non-empty; `rollout_resources` defined, is sequence, non-empty.
- **Output.** None. Fails on timeout.
- **Callers.** End of `install` phase on every component; also `-restart` playbooks.
- **Idempotent.** Read-only wait.

### 1.8 `tasks-eso-merge.yaml`

- **Purpose.** Merge base + `_extra` for all Vault/ESO inventory data. Validate consistency.
- **Input.** None (reads vars by convention).
- **Output (runtime facts).**
  - `vault_policies_final` — `vault_policies + vault_policies_extra`.
  - `vault_roles_final` — `vault_roles + vault_roles_extra`.
  - `eso_vault_integration_<c>_secrets_merged` for each of 8 components: `traefik`, `haproxy`, `longhorn`, `gitlab`, `gitlab_runner`, `zitadel`, `argocd`, `grafana`.
- **Validates.** Unique policy names; unique role names; every role's policies exist; SecretStore role exists; per-component uniqueness of `external_secret_name` / `target_secret_name`. `argocd` extras allow types `default`, `git_ops_repo_pattern`, `git_ops_repo_direct` (git-ops git-creds live in the same integration).
- **Callers.** Every ESO-integrated install playbook AND `vault-install.yaml`. Tag `[always]`.
- **Idempotent.** Pure merge + validation.

### 1.9 `tasks-resolve-acme-solver.yaml`

- **Purpose.** Look up a `ClusterIssuer` by name in `cert_manager_cluster_issuers`, find the solver matching a given `ingressClass`, export its `podLabels`. Downstream NetworkPolicies use these labels to admit the cert-manager solver pod.
- **Input.** `label_name`, `cluster_issuer_name`, `ingress_class_name`, `acme_cluster_issuer_result_var`, `acme_solver_result_var`, `acme_pod_labels_result_var` (three dynamic fact names).
- **Validates (assert).** All 6 params defined + non-empty (assert block at top of file).
- **Output (runtime facts, names from input).** Resolved `ClusterIssuer` name, full solver dict, `podLabels` dict.
- **Callers.** Install playbooks of components with HTTPS ingress that triggers ACME HTTP-01 — typically at `tags: [always]`.
- **Idempotent.** Pure lookup.

### 1.10 `tasks-verify-helm.yaml`

- **Purpose.** Assert a Helm release is in `deployed` status (not `failed`, `pending`, `uninstalling`).
- **Input.** `label_name`, `helm_namespace` (namespace of the release).
- **Validates (assert).** `label_name`, `helm_namespace` both defined + non-empty.
- **Output.** Fails if any release in the namespace is not `deployed`.
- **Callers.** End of install phase (optional, recommended).
- **Idempotent.** Read-only.

### 1.11 `tasks-eso-force-sync.yaml`

- **Purpose.** Annotate ExternalSecrets with `force-sync=<epoch>` to trigger ESO reconciliation without waiting for `refreshInterval`.
- **Input.** `label_name` (required). Optional: `dto_eso_sync_namespace`, `dto_eso_sync_es_name` — control targeting (single ES / all in ns / all namespaces).
- **Validates (assert).** `label_name` defined + non-empty. Optional params are not validated (governed by `when:` conditions).
- **Output.** All targeted ExternalSecrets annotated.
- **Callers.** `eso-force-sync.yaml` standalone; also after `tasks-vault-put.yaml` (internal).
- **Idempotent.** Annotation bump is always safe.

### 1.12 `tasks-vault-get.yaml`

- **Purpose.** Read one KV v2 field from Vault into an Ansible fact (plus an `_exists` boolean).
- **Input.** `label_name`, `dto_vault_get_path` (full KV path), `dto_vault_get_field` (field name), `dto_vault_get_res_fact_name` (output fact name).
- **Validates (assert).** `label_name`, `dto_vault_get_path`, `dto_vault_get_field`, `dto_vault_get_res_fact_name` all defined + non-empty.
- **Output.** `<fact>` + `<fact>_exists`. Missing fields set `_exists: false` without failing the play.
- **Callers.** `-configure` playbooks (resolve current credentials before rotating), `-rotate` playbooks.
- **Idempotent.** Read-only.

### 1.13 `tasks-vault-put.yaml`

- **Purpose.** `vault kv put`, then annotate the target ExternalSecret to force ESO sync, then wait for the downstream K8s `Secret` to appear.
- **Input.** `label_name`, `dto_vault_put_path` (full KV path), `dto_vault_put_data` (non-empty dict of `{field: value}`).
- **Validates (assert).** `label_name` defined + non-empty; `dto_vault_put_path` defined + non-empty; `dto_vault_put_data` defined, is mapping, non-empty.
- **Output.** Vault updated, K8s Secret updated.
- **Callers.** Rotation flows (Postgres, Redis, MinIO, GitLab root, ArgoCD admin, Vault admin-token).
- **Idempotent.** Re-running re-puts identical values — safe, just a noop in ESO (force-sync annotation bumps once more).

### 1.14 `tasks-generate-secret.yaml`

- **Purpose.** Generate a random N-character secret into a named fact.
- **Input.** `label_name`, `dto_generate_fact_name` (output fact name). Optional: `generate_length` (default 32), `generate_chars` (default `ascii_letters,digits`).
- **Validates (assert).** `label_name`, `dto_generate_fact_name` both defined + non-empty. Optional params not asserted.
- **Output.** Fact with the generated secret.
- **Callers.** Bootstrap of first-run passwords (GitLab root, Vault admin, Grafana admin).
- **Idempotent.** No — regenerates on each call. Pair with `tasks-vault-get.yaml` + `_exists` check to avoid regenerating existing secrets.

### 1.15 `tasks-vault-distribute-creds.yaml`

- **Purpose.** Read the `vault-unsealer-secret` from the live cluster, decode, write to `/etc/kubernetes/vault-unseal.json` on managers.
- **Input.** `label_name`.
- **Output.** Local file on each manager (0600, root:root).
- **Callers.** `manager-join.yaml` (so new managers have unseal keys), `vault-install.yaml` post-phase.
- **Idempotent.** Overwrites if secret changed.

### 1.16 `tasks-helm-upgrade-async.yaml`

- **Purpose.** Run `helm upgrade --install` in async mode for charts that exceed Ansible's synchronous command timeout (notably the GitLab chart family).
- **Input.** `label_name`, `helm_command` (complete helm command string). Async timing from global vars (`helm_async_timeout`, `helm_async_poll`).
- **Validates (assert).** `label_name`, `helm_command` both defined + non-empty.
- **Output.** Helm release updated.
- **Callers.** `gitlab-install.yaml`.
- **Idempotent.** Same semantics as synchronous helm; async is just about avoiding SSH timeouts.

### 1.17 `tasks-k8s-secret-get.yaml`

- **Purpose.** Read a single `.data` field from a K8s Secret into a named fact. Never fails on missing secret or field — callers branch on `<fact>_exists`.
- **Input.** `label_name`, `dto_secret_namespace`, `dto_secret_name`, `dto_secret_field`, `dto_secret_res_fact_name` (all required).
- **Validates (assert).** All 4 dto params defined + non-empty. **Reference implementation** — this is the canonical example for the assert pattern.
- **Output (runtime facts, set on all hosts).**
  - `{{ dto_secret_res_fact_name }}` — decoded string value (`''` if missing).
  - `{{ dto_secret_res_fact_name }}_exists` — bool (true only if field is non-empty).
- **Callers.** `argocd-configure.yaml`, `gitlab-configure.yaml`, `gitlab-install.yaml`, `gitlab-runner-install.yaml`, `vault-install.yaml`.
- **Idempotent.** Read-only; safe to call repeatedly.

---

## 2. `playbook-system/tasks/` (16 tasks)

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

#### `tasks-gather-cluster-facts.yaml`

- **Purpose.** The single cluster-state fact collector. Chains `tasks-set-master-manager.yaml` → `tasks-set-is-cluster-init.yaml` → `tasks-set-is-node-joined.yaml`. Never fails on missing cluster — returns bools.
- **Input.** None.
- **Output.** On every host: `master_manager_fact`, `is_master_manager_exist`, `is_cluster_init`, `is_node_joined`, `joined_node_ips` (list), `joined_node_hostnames` (list).
- **Callers.** Top of `manager-join.yaml`, `worker-join.yaml`, `apiserver-sans-update.yaml`, `etcd-key-rotate.yaml`, `haproxy-apiserver-lb-update.yaml`, `node-info.yaml`.
- **Idempotent.** Read-only.

#### `tasks-set-master-manager.yaml`

- **Purpose.** Pick the manager with `is_master: true` from inventory.
- **Output.** `master_manager_fact`, `is_master_manager_exist`.
- **Callers.** `tasks-gather-cluster-facts.yaml`.
- **Idempotent.** Yes.

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

---

## 3. Common Usage Templates

### 3.1 Beginning of an app install playbook

```yaml
- include_tasks: tasks/tasks-pre-check.yaml
  vars:
    label_name: "<c>-install-pre-check"
  tags: [always]

- include_tasks: tasks/tasks-forbid-kube-system.yaml
  vars:
    label_name: "<c>-install-pre-check"
    namespace_value: "{{ <c>_namespace }}"
  tags: [always]

- include_tasks: tasks/tasks-eso-merge.yaml
  tags: [always]

# If ingress uses ACME:
- include_tasks: tasks/tasks-resolve-acme-solver.yaml
  vars:
    label_name: "<c>-install-init"
    cluster_issuer_name: "{{ <c>_cluster_issuer_name }}"
    ingress_class_name: "{{ <c>_ingress_class_name }}"
    acme_cluster_issuer_result_var: "<c>_acme_cluster_issuer"
    acme_solver_result_var: "<c>_acme_solver"
    acme_pod_labels_result_var: "<c>_acme_solver_pod_labels"
  tags: [always]
```

### 3.2 Standard phase skeleton

```yaml
- include_tasks: tasks/tasks-copy-chart.yaml
  vars:
    label_name: "<c>-install-<phase>"
    chart_name: "<c>-<phase>"             # or just "<c>" for install phase
    chart_local_src: "{{ playbook_dir }}/charts/<c>/<phase>/"   # trailing / required
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

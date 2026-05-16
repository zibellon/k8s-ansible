# Playbook Conventions — Authoring Rules

Imperative rules for writing or modifying playbooks. For the *why* behind the rules, see `CLAUDE.md` §4, §8, §12. For per-component specifics, see [`components.md`](components.md). For reusable task contracts, see [`reusable-tasks.md`](reusable-tasks.md).

## 1. File Location & Naming

1.1 Node-scoped plays live in `playbook-system/`. Cluster-scoped plays live in `playbook-app/`.
1.2 File name pattern: `<component>-<action>.yaml`. Allowed actions: `install`, `configure`, `restart`, `rotate`, `sync`, `force-sync`, `tags-sync`, `create`, `delete`.
1.3 For system playbooks, file name is either a cluster-lifecycle verb (`cluster-init.yaml`, `manager-join.yaml`, `worker-join.yaml`, `node-install.yaml`, `node-remove.yaml`, `node-drain-on.yaml`, `node-drain-off.yaml`, `server-clean.yaml`, `server-prepare.yaml`, `set-hostname.yaml`, `setup-ssh-keys.yaml`, `node-info.yaml`) or `<subsystem>-<action>.yaml` (e.g. `apiserver-sans-update.yaml`, `etcd-key-rotate.yaml`, `haproxy-apiserver-lb-update.yaml`).
1.4 Task include file name pattern: `tasks-<verb>-<object>.yaml`. Exception: `task-apiserver-restart.yaml` (singular `task-` — keep as-is for historical reasons).

## 2. Play Header

2.1 `playbook-app/` plays MUST use `hosts: managers`, `become: true`, `gather_facts: false`.
2.2 `playbook-system/` plays target specific groups (`managers`, `workers`, or ad-hoc) and SHOULD use `gather_facts: false` — enable gathering only inside specific tasks that need it.
2.3 Include a top-of-file comment banner documenting purpose, steps, and `--tags` usage (see §11).

## 3. Required Guards (per play)

3.1 System playbooks that operate on individual nodes MUST call `tasks-require-limit.yaml` — fails the play if `--limit` is absent.
3.2 System playbooks that operate on a manager MUST also call `tasks-require-manager.yaml`; those on a worker MUST call `tasks-require-worker.yaml`.
3.3 App install playbooks MUST start with `tasks-pre-check.yaml` (tag `always`) to resolve `master_manager_fact`.
3.4 App install playbooks MUST call `tasks-forbid-kube-system.yaml` (tag `always`) with the component's namespace — refuses deployment into `kube-system`.

## 4. Fact Gathering

4.1 Cluster state facts come from `tasks-gather-cluster-facts.yaml`. Never reimplement its checks.
4.2 `master_manager_fact` is the only permitted source of truth for "the manager to run cluster ops on". Derive it via `tasks-pre-check.yaml` or `tasks-set-master-manager.yaml`.
4.3 Facts used later in the same play by downstream tags MUST be produced by `tags: [always]` tasks — otherwise `--tags post` runs will see undefined variables.

## 5. Delegation

5.1 Every `kubectl`, `helm`, or cluster API call in `playbook-app/` MUST include both:

```yaml
delegate_to: "{{ master_manager_fact }}"
run_once: true
```

5.2 Omitting `run_once: true` causes Ansible to run the task once per host in `hosts: managers` — Helm history duplicates, `kubectl create` double-runs. Always include it.
5.3 Never delegate to a hard-coded hostname. Always use `master_manager_fact`.
5.4 `playbook-system/` plays that perform cluster-wide `kubectl` (e.g. inside `cluster-init.yaml`, `manager-join.yaml`) also delegate to the master manager (the manager currently running the task is often itself the master on first init — that is allowed).

## 6. Three-Phase Install Structure

6.1 Every `<c>-install.yaml` MUST produce three Helm releases: `<c>-pre`, `<c>`, `<c>-post`. Exceptions (components with no post resources: `metrics-server`, `gitlab-runner`) skip only `<c>-post`. Components with extras (`<c>-crds`, `<c>-operator`, `<c>-prometheus`, ...) add extra releases BETWEEN the three standard ones; do not replace them.
6.2 Release name equals phase name: `<c>-pre`, `<c>`, `<c>-post` (no underscores). Namespace equals `{{ <c>_namespace }}`.
6.3 Each phase MUST be individually re-runnable via `--tags pre|install|post` and idempotent. A broken post phase MUST NOT rollback install.
6.4 Helm invocation for every phase MUST include these flags:

```
--cleanup-on-fail --atomic --wait --wait-for-jobs --timeout {{ <c>_helm_timeout }}
```

Add `--create-namespace` on the first release that targets the namespace (usually `<c>-pre`). Subsequent releases in the same namespace may omit it.

## 7. Task Naming

7.1 Human task name pattern: `[<c>-<action>-<phase>] <description>`. Examples: `[argocd-install-pre] Copy chart to remote`, `[vault-rotate] Generate new root token`, `[cluster-init] kubeadm init`.
7.2 `dto_label_name` parameter passed to every `include_tasks` MUST match the enclosing action/phase prefix. This is how logs line up across includes.

## 8. Include Strategy

8.1 Always use `include_tasks` (dynamic). Never `import_tasks` — tag inheritance breaks with imports.
8.2 Tag every include with the appropriate phase: `[always]`, `[pre]`, `[install]`, `[post]`. Extras use their own tag (`[crds]`, `[operator]`, `[cr]`, etc.).
8.3 All `include_tasks` paths MUST be absolute via `{{ project_root }}` — defined in `hosts-vars/ansible.yaml` as `{{ lookup('env', 'PWD') }}`. Format:
```yaml
include_tasks: "{{ project_root }}/playbook-app/tasks/<name>.yaml"      # for app
include_tasks: "{{ project_root }}/playbook-system/tasks/<name>.yaml"   # for system
```
Relative forms (`tasks/X.yaml`, `tasks-X.yaml` sibling) are an anti-pattern (see §17.10) — they implicitly depend on Ansible's `playbook_dir` resolution and break when a task is included from a different location (e.g. tests reusing production tasks).

8.4 **Exception — `import_playbook`**. `import_playbook` is resolved at **parse time**, before inventory variables are loaded. Therefore `{{ project_root }}` is `UNDEFINED` inside `import_playbook` and CANNOT be used. The relative form is correct here:
```yaml
import_playbook: setup-ssh-keys.yaml   # OK — sibling-form is the only working option
```
This is a fundamental Ansible limitation, not a project choice. The single user of `import_playbook` in this repo is `playbook-system/node-install.yaml`.

## 9. values-override.yaml Pattern

9.1 Each phase's values override is rendered inline with `copy: content: |` — a single task before the `helm upgrade` call.
9.2 Destination path: `{{ remote_charts_dir }}/<c>/<phase>/values-override.yaml`.
9.3 Object / dict values MUST go through `| to_json` (single-line) or `| to_nice_yaml | indent(N)` (block). Never paste raw Python repr or Jinja-rendered dicts into YAML.
9.4 Render conditional blocks with `{% if %}` inside the `content: |` string.

## 10. Chart Copy Pattern

10.1 Use `tasks-copy-chart.yaml` — it archives, ships, and extracts. Faster and more reliable than `synchronize` for large charts.
10.2 `chart_local_src` MUST end with `/` (trailing slash). `chart_remote_dest` MUST NOT end with `/`.
10.3 `dto_chart_name` MUST equal the phase subdirectory name (`pre`, `post`, `install`, `cr`, `gitops`, `configure`, `postgresql`, `redis`, `minio`, etc.). It is used as the temp archive file name on the operator's machine (e.g. `/tmp/pre.tgz`), not as the Helm release name. Helm release name is composed separately in the helm command as `<c>-<phase>`.

## 11. Comment Banner (install playbook template)

```yaml
# =============================================================================
# Install <Component> via local Helm charts
# Steps:
#   1. <c>/pre: NetworkPolicies + ESO resources
#   2. <c>/install: CRDs + main chart
#   3. <c>/post: Ingress + ServiceMonitor + post-install resources
#
# Usage:
#   ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/<c>-install.yaml
#   ansible-playbook ... --tags pre
#   ansible-playbook ... --tags install
#   ansible-playbook ... --tags post
# =============================================================================
```

Use `# === STEP N: <phase> ===` separators between phase blocks inside the tasks list.

## 12. ESO Integration (if component is ESO-enabled)

12.1 Add `eso_vault_integration_<c>` object in the component's vars file (see `secrets-and-eso.md` for schema).
12.2 Include `tasks-eso-secrets-merge.yaml` (tag `[always]`, no arguments). It merges `eso_vault_integration_<c>_secrets + _extra` and produces `eso_vault_integration_<c>_secrets_merged` facts.
12.3 In the `pre/` chart, render `ServiceAccount` and `SecretStore`. Copy the canonical `eso-external-secret.yaml` template from any existing component — it is identical across all 8 and uses `toYaml $secret.body | indent 2` to emit whatever body is defined in inventory.
12.4 If the component installs before Vault exists (bootstrap-time), gate ESO resources with `<c>_is_need_eso: false` in the chart templates and seed the secret via `tasks-vault-put.yaml` from a `-configure` playbook afterwards.

## 13. ACME / cert-manager Integration

13.1 Components that use an ingress with HTTP-01 challenge MUST include `tasks-resolve-acme-solver.yaml` (tag `always`) to derive the global fact `acme_pod_labels_result_fact` (set by the task; one ClusterIssuer/solver per playbook run, so the global fact name causes no conflicts).
13.2 `NetworkPolicy` rules allowing the cert-manager solver MUST reference `acme_pod_labels_result_fact`. Do not hard-code solver pod labels.

## 14. Rollout Verification

14.1 After `helm upgrade --install <c>`, include `tasks-wait-rollout.yaml` with the exact `kind/name` resources expected (`deployment/<x>`, `statefulset/<x>`, `daemonset/<x>`).
14.2 Where CRDs must be present before the main chart can deploy workloads, include `tasks-wait-crds.yaml` with the expected CRD list (`crd/<name>`).
14.3 After all phases, optional `tasks-k8s-list-helm.yaml` lists Helm releases in the component namespace for operator visibility.

## 15. Variables Contract

15.1 Use the suffix convention (see [`variables.md`](variables.md) §1). Do not invent new suffixes.
15.2 Arrays that users should be able to extend MUST have a `_extra` companion — base lives in `hosts-vars/`, extension in `hosts-vars-override/`. Merge at runtime via `{{ base + (extra | default([])) }}`.
15.3 Never reference `hosts-vars-override/` content from committed files. All extensibility goes through `*_extra`.

## 16. Non-Install Playbook Patterns

16.1 **`-configure`**: resolves (or rotates) credentials via `tasks-vault-get.yaml` / `tasks-vault-put.yaml`, then validates against the component's own API. Does not touch Helm.
16.2 **`-restart`**: reads target resources, runs `kubectl rollout restart`, then `tasks-wait-rollout.yaml`. Never use `kubectl delete pod` — always restart at the controller level.
16.3 **`-rotate`**: component-specific state mutation (e.g. Vault rekey). MUST be idempotent and resume-safe — use state files on disk (see `bootstrap-and-ha.md` §3).
16.4 **`-force-sync`**: wraps `tasks-eso-force-sync.yaml` — annotates ExternalSecrets with `force-sync=<epoch>` to trigger ESO reconciliation.

## 17. Anti-patterns (do not commit)

17.1 Inline `kubectl apply -f <url-or-heredoc>` — always wrap resources in a Helm chart so `--tags`, `--atomic`, and release history work.
17.2 Hard-coded pod labels for ACME solver `NetworkPolicy` — always resolve from `cert_manager_cluster_issuers`.
17.3 Bypassing `tasks-eso-secrets-merge.yaml` by hard-coding a secrets list inline in `values-override.yaml` — misses `_extra` and loses uniqueness validation.
17.3a Hard-coding a literal `kv_engine_path` string inside `body.dataFrom.extract.key` instead of using `{{ eso_vault_integration_<c>.kv_engine_path }}` — prevents override without editing every item.
17.4 `gather_facts: true` in `playbook-app/` plays.
17.5 Hard-coded hostnames in `delegate_to:` — always `"{{ master_manager_fact }}"`.
17.6 Helm release names that differ from the phase naming convention (`<c>-pre` / `<c>` / `<c>-post`).
17.7 Secrets in `hosts-vars/` (committed). Always `hosts-vars-override/`.
17.8 Editing chart templates without running through `--tags <phase>` afterwards — Helm diff may not detect the change.
17.9 Hard-coded numeric ports inside `NetworkPolicy` / `CiliumNetworkPolicy` / `CiliumClusterwideNetworkPolicy` templates — always source ports from chart `values.yaml` using camelCase, component-grouped keys (`vault.apiPort`, `argocd.serverPort`). Common ports (DNS, apiserver, ACME solver, external HTTP/HTTPS, kubelet) live in shared per-chart buckets (`dns.port: 53`, `apiserver.port: 6443`, etc.). Reference: `playbook-app/charts/teleport/pre/values.yaml`. See [`networking.md`](networking.md) §7 for the full convention.
17.10 Relative `include_tasks: tasks/X.yaml` or sibling-form `include_tasks: tasks-X.yaml` (no prefix) — always use `{{ project_root }}/<dir>/tasks/<name>.yaml` (rule §8.3). Relative forms silently depend on Ansible's `playbook_dir` resolution rule and break when a playbook is included from a different working directory (e.g. tests reusing a production task via `include_tasks` from `tests/` directory). Exception: `import_playbook` (see §8.4 — parse-time evaluation precludes variable use).

## 18. Checklist Before Commit

- [ ] Both inventories used in local test: `-i hosts-vars/ -i hosts-vars-override/`.
- [ ] Each phase re-runs cleanly with `--tags`.
- [ ] No new identifiers (vars, files, namespaces) that don't resolve against the repo.
- [ ] All `include_tasks` paths use `{{ project_root }}/<dir>/tasks/<name>.yaml` (rule §8.3); no relative `tasks/X.yaml` forms.
- [ ] `hosts-vars-override/` not committed.
- [ ] No secret literal in any committed file.
- [ ] If a new variable was added, it is documented (either in `variables.md` if global or in `components.md` if per-component).
- [ ] If a new task include was added, it is documented in `reusable-tasks.md`.
- [ ] If a new task include was added, it starts with an `assert` block validating all required params (Rule 19).
- [ ] `make test` зелёный (Docker — see [`testing.md`](testing.md)).

## 19. Parameter Validation in Task Includes

19.1 Every task include file (`tasks/*.yaml`) MUST start with an `assert` block that
validates ALL required input parameters. No exceptions — if a task takes a parameter,
it must validate it before doing anything else.

19.2 The assert block MUST be the FIRST task in the file — before any `set_fact`,
`command`, `shell`, `copy`, `file`, or `include_tasks`.

19.3 Tag the assert task `[always]` so it fires regardless of `--tags` on the caller.

19.4 Use `delegate_to: "{{ master_manager_fact }}" + run_once: true` if `master_manager_fact`
is guaranteed to be set when the task is called. For tasks that themselves set
`master_manager_fact` (e.g. `tasks-pre-check.yaml`), use only `run_once: true` without `delegate_to`.

19.5 Standard validation pattern per param type:
- Required string: `<param> is defined` + `<param> | length > 0`
- Required list:   `<param> is defined` + `<param> is sequence` + `<param> | length > 0`
- Required dict:   `<param> is defined` + `<param> is mapping` + `<param> | length > 0`
- Dict subkey:     `<param>.<field> is defined`

19.6 Reference implementation: `tasks-k8s-secret-get.yaml` — full example with 5 required string params.

19.7 Optional params (controlled by `when:` in the task body) do NOT need validation in the assert block. Only required params are asserted.

## 20. Testing Requirement

20.1 Any change to a playbook, chart, task include, inventory file, or test infrastructure requires a green `make test` before commit. The runner and configs are documented in [`testing.md`](testing.md).

20.2 Tooling versions are pinned in `tests/Dockerfile`. If a playbook starts using a module from an Ansible collection that is not yet installed in the test image, add a `RUN ansible-galaxy collection install <ns>.<col>:<X.Y.Z>` line to `tests/Dockerfile` (with a pinned version), rebuild the image, and re-run `make test`. See [`testing.md`](testing.md) §7.

## 21. Unified Helm Template + Kustomize Pattern (все LOCAL-managed chart phase'ы)

21.1 Применяется ко **всем** LOCAL-managed chart phase'ам — 35 LOCAL_CUSTOM phase'ов + 2 KUSTOMIZE_WRAPPER phase'а через 15 компонентов. Единый flow, единый task.

21.2 Output structure для каждой phase на `master_manager_fact`:

```
{{ remote_charts_dir }}/<c>/<phase>/         # source — copied by tasks-copy-chart.yaml
{{ remote_charts_dir }}/<c>/<phase>-k-tmp/   # staging: helm template output + kustomization.yaml
{{ remote_charts_dir }}/<c>/<phase>-k/       # output: Chart.yaml + templates/all.yaml (helm install из этого)
```

Обе staging и output директории НЕ удаляются между runs — overwrite при re-run. Operator может инспектировать `-k-tmp/kustomization.yaml` и `-k/templates/all.yaml` для debug.

21.3 Extension point: каждая phase имеет переменную `<c>_<phase>_kustomize_patches` (default `[]`) в `hosts-vars/<c>.yaml`. Без `_extra` companion — operator override полностью заменяет base (присваивание, не concat-merge). При `[]` kustomize output идентичен source chart — zero diff на production при первом выкате.

Каждый элемент: `{target: {kind, name}, patch: |- <strategic merge YAML or JSON Patch RFC 6902>}`. Upstream defaults сохраняются автоматически — не копировать их в patches.

21.4 Поток в `<c>-install.yaml` (одинаковый для LOCAL_CUSTOM и KUSTOMIZE_WRAPPER):

```yaml
- include_tasks: "{{ project_root }}/playbook-app/tasks/tasks-copy-chart.yaml"
  vars:
    dto_label_name: "<c>-install-<phase>"
    dto_chart_name: "<phase>"
    dto_chart_local_src: "{{ project_root }}/playbook-app/charts/<c>/<phase>/"
    dto_chart_remote_dest: "{{ remote_charts_dir }}/<c>/<phase>"
  tags: [<phase>]

- include_tasks: "{{ project_root }}/playbook-app/tasks/tasks-copy-helm-values.yaml"
  vars:
    dto_label_name: "<c>-install-<phase>"
    dto_dir: "{{ remote_charts_dir }}/<c>/<phase>"
    dto_filename: "values-override.yaml"
    dto_content: "{{ <c>_<phase>_helm_values | to_nice_yaml }}"   # LOCAL_CUSTOM
    # для KUSTOMIZE_WRAPPER: dto_content: "{}"
  tags: [<phase>]

- include_tasks: "{{ project_root }}/playbook-app/tasks/tasks-helm-template-kustomize-build.yaml"
  vars:
    dto_label_name: "<c>-install-<phase>"
    dto_release_name: "<c>-<phase>"
    dto_chart_remote_dest: "{{ remote_charts_dir }}/<c>/<phase>"
    dto_values_file_path: "{{ remote_charts_dir }}/<c>/<phase>/values-override.yaml"
    dto_kustomize_tmp_dir: "{{ remote_charts_dir }}/<c>/<phase>-k-tmp"
    dto_kustomize_final_dir: "{{ remote_charts_dir }}/<c>/<phase>-k"
    dto_patches_list: "{{ <c>_<phase>_kustomize_patches }}"
    dto_target_namespace: "{{ <c>_namespace }}"
  tags: [<phase>]

- include_tasks: "{{ project_root }}/playbook-app/tasks/tasks-helm-upgrade-async.yaml"
  vars:
    dto_label_name: "<c>-install-<phase>"
    dto_helm_command: >
      helm upgrade --install <c>-<phase> {{ remote_charts_dir }}/<c>/<phase>-k
      --namespace {{ <c>_namespace }} --create-namespace
      --cleanup-on-fail --atomic --wait --wait-for-jobs
      --timeout {{ <c>_<phase>_helm_timeout }}
  tags: [<phase>]
```

21.5 KUSTOMIZE_WRAPPER phase'ы (`argocd/install`, `mon-system/prometheus-operator`) — pristine upstream YAML без Jinja-вставок. Подают `dto_content: "{}"` в `tasks-copy-helm-values.yaml`; `helm template --namespace` рендерит namespace context в ресурсы. Patches работают поверх rendered output.

21.6 Канонические примеры:
- LOCAL_CUSTOM: `playbook-app/cilium-install.yaml` STEP 1+3 (pre/post) + `hosts-vars/cilium.yaml` `cilium_pre_kustomize_patches`.
- KUSTOMIZE_WRAPPER: `playbook-app/argocd-install.yaml` (install phase, `dto_content: "{}"`) + `hosts-vars/argocd.yaml` `argocd_install_kustomize_patches`. Также `playbook-app/mon-system-install.yaml` (prometheus-operator phase) + `hosts-vars/mon-system.yaml` `mon_system_prometheus_operator_kustomize_patches`. (см. [`components.md`](components.md) §9 ArgoCD и §17 mon-system).

21.7 **Opt-in namespace transformer** (только для KUSTOMIZE_WRAPPER). По default `tasks-helm-template-kustomize-build.yaml` НЕ применяет kustomize `namespace:` builtin transformer — LOCAL_CUSTOM charts намеренно multi-namespace (cilium-pre NPs в `kube-system` + `traefik-lb`, gitlab-runner-pre NPs в `gitlab` namespace, и т.п.), transformer бы collapse'нул всё в один namespace и сломал cross-namespace rules.

KUSTOMIZE_WRAPPER phases (pristine upstream YAML без Jinja) типично содержат hardcoded namespace в `metadata.namespace` и в `subjects[].namespace` ClusterRoleBinding (например argocd/install/install.yaml — `namespace: argocd` × 3; prometheus-operator.yaml — `namespace: default` × 4). Helm 3 **не** переопределяет explicit `metadata.namespace` через `--namespace`, поэтому такой pristine деплоится в неправильный namespace.

Для этого случая в `tasks-helm-template-kustomize-build.yaml` есть **optional bool param** `dto_kustomize_apply_namespace_transform` (default `false`). KUSTOMIZE_WRAPPER callers передают `true`:

```yaml
- include_tasks: "{{ project_root }}/playbook-app/tasks/tasks-helm-template-kustomize-build.yaml"
  vars:
    # ... 8 required params ...
    dto_kustomize_apply_namespace_transform: true
  tags: [install]
```

Kustomize transformer переписывает:
- `metadata.namespace` всех namespaced resources на `dto_target_namespace`.
- `subjects[].namespace` для `kind: ServiceAccount` в RoleBinding / ClusterRoleBinding.
- `webhooks[].clientConfig.service.namespace` в Mutating/ValidatingWebhookConfiguration.
- `spec.service.namespace` в APIService.

Cluster-scoped ресурсы (ClusterRole, CRD, и т.п.) не трогает. Cross-namespace references в data fields ConfigMap/Secret (например hardcoded DNS `<ns>.svc.cluster.local`) и в CR от CRD — **не** переписываются.

**Monitoring при upstream bumps:** после `helm template ... | kubectl kustomize` для KUSTOMIZE_WRAPPER chart'а — `grep "<old-namespace>"` на rendered output. Должны остаться только labels (`app.kubernetes.io/part-of`) и names (`name: argocd-cm`), не namespace declarations или DNS substrings. Если grep ловит unexpected occurrences — upstream добавил resource kind kustomize не handles или hardcoded DNS в data field — нужен manual JSON Patch в `<c>_<phase>_kustomize_patches`.

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
7.2 `label_name` parameter passed to every `include_tasks` MUST match the enclosing action/phase prefix. This is how logs line up across includes.

## 8. Include Strategy

8.1 Always use `include_tasks` (dynamic). Never `import_tasks` — tag inheritance breaks with imports.
8.2 Tag every include with the appropriate phase: `[always]`, `[pre]`, `[install]`, `[post]`. Extras use their own tag (`[crds]`, `[operator]`, `[cr]`, etc.).

## 9. values-override.yaml Pattern

9.1 Each phase's values override is rendered inline with `copy: content: |` — a single task before the `helm upgrade` call.
9.2 Destination path: `{{ remote_charts_dir }}/<c>/<phase>/values-override.yaml`.
9.3 Object / dict values MUST go through `| to_json` (single-line) or `| to_nice_yaml | indent(N)` (block). Never paste raw Python repr or Jinja-rendered dicts into YAML.
9.4 Render conditional blocks with `{% if %}` inside the `content: |` string.

## 10. Chart Copy Pattern

10.1 Use `tasks-copy-chart.yaml` — it archives, ships, and extracts. Faster and more reliable than `synchronize` for large charts.
10.2 `chart_local_src` MUST end with `/` (trailing slash). `chart_remote_dest` MUST NOT end with `/`.
10.3 `chart_name` MUST equal the Helm release name (used for the temp archive file name).

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
12.2 Include `tasks-eso-merge.yaml` (tag `always`, no arguments). It reads all `eso_vault_integration_*` objects and produces `eso_vault_integration_<c>_secrets_merged` facts.
12.3 In the `pre/` chart, render `ServiceAccount`, `SecretStore`, and `ExternalSecret` manifests from those merged facts — never hand-write secret lists inline in values-override.
12.4 If the component installs before Vault exists (bootstrap-time), gate ESO resources with `<c>_is_need_eso: false` in the chart templates and seed the secret via `tasks-vault-put.yaml` from a `-configure` playbook afterwards.

## 13. ACME / cert-manager Integration

13.1 Components that use an ingress with HTTP-01 challenge MUST include `tasks-resolve-acme-solver.yaml` (tag `always`) to derive `<c>_acme_solver_pod_labels`.
13.2 `NetworkPolicy` rules allowing the cert-manager solver MUST reference those resolved labels. Do not hard-code solver pod labels.

## 14. Rollout Verification

14.1 After `helm upgrade --install <c>`, include `tasks-wait-rollout.yaml` with the exact `kind/name` resources expected (`deployment/<x>`, `statefulset/<x>`, `daemonset/<x>`).
14.2 Where CRDs must be present before the main chart can deploy workloads, include `tasks-wait-crds.yaml` with the expected CRD list (`crd/<name>`).
14.3 After all phases, optional `tasks-verify-helm.yaml` confirms release status is `deployed`.

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
17.3 Bypassing `tasks-eso-merge.yaml` by hand-writing a secrets list — misses `_extra` and loses validation.
17.4 `gather_facts: true` in `playbook-app/` plays.
17.5 Hard-coded hostnames in `delegate_to:` — always `"{{ master_manager_fact }}"`.
17.6 Helm release names that differ from the phase naming convention (`<c>-pre` / `<c>` / `<c>-post`).
17.7 Secrets in `hosts-vars/` (committed). Always `hosts-vars-override/`.
17.8 Editing chart templates without running through `--tags <phase>` afterwards — Helm diff may not detect the change.

## 18. Checklist Before Commit

- [ ] Both inventories used in local test: `-i hosts-vars/ -i hosts-vars-override/`.
- [ ] Each phase re-runs cleanly with `--tags`.
- [ ] No new identifiers (vars, files, namespaces) that don't resolve against the repo.
- [ ] `hosts-vars-override/` not committed.
- [ ] No secret literal in any committed file.
- [ ] If a new variable was added, it is documented (either in `variables.md` if global or in `components.md` if per-component).
- [ ] If a new task include was added, it is documented in `reusable-tasks.md`.
- [ ] If a new task include was added, it starts with an `assert` block validating all required params (Rule 19).

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

19.6 Reference implementation: `tasks-k8s-secret-get.yaml` — full example with 4 required string params.

19.7 Optional params (controlled by `when:` in the task body) do NOT need validation in the assert block. Only required params are asserted.

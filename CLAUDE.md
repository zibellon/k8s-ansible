# k8s-ansible

Production bare-metal Kubernetes cluster automation. Two halves:

- **`playbook-system/`** — imperative, node-scoped bootstrap and lifecycle operations (kubeadm, HAProxy LB as systemd, ETCD encryption, node join/drain/remove).
- **`playbook-app/`** — declarative cluster-scoped application installs via local Helm charts, every component deployed through a consistent 3-phase pattern (`pre` → `install` → `post`).

This file is the **map** of the project. Detailed **catalogs** live under `.claude/rules/` — see §3 for the full index. Read section 1 (invariants) and section 2 (mental model) before editing anything.

---

## 0. Hard Invariants (do not violate)

Violating any of these will break the cluster or leak secrets.

- `longhorn-system` namespace is **upstream-fixed** — never rename. (Note: `argocd` was historically also fixed but is now configurable via `argocd_namespace` — namespace rebind handled by builtin kustomize transformer via `dto_target_namespace` in `tasks-helm-template-kustomize-build.yaml`; see [`components.md`](.claude/rules/components.md) §9.)
- `kube-proxy` is **disabled at `kubeadm init`** via declarative `proxy.disabled: true` in `ClusterConfiguration`. Cilium replaces it. Do not re-enable.
- `hosts-vars-override/` is **never committed**. It contains `ansible_password`, real IPs, Vault unseal keys, and all secrets.
- Always run Ansible with **both** inventories: `-i hosts-vars/ -i hosts-vars-override/`. Running with only one is always a bug.
- **Single-node system playbooks require `--limit`**. Forgetting `--limit` on `cluster-init.yaml` / `manager-join.yaml` / `worker-join.yaml` / `node-drain-on.yaml` / `node-drain-off.yaml` / `node-remove.yaml` / `server-clean.yaml` will fail a `tasks-require-limit.yaml` gate (by design). `node-install.yaml` is **bulk-friendly** by design: `--limit` is optional — use without `--limit` for bulk preparation of multiple nodes (sub-plays are idempotent: `apt`, kernel modules, sysctls, HAProxy on `127.0.0.1`); use with `--limit <host>` to prepare a single node added to an already-running cluster. Cluster-wide rolling-update plays (`apiserver-sans-update.yaml`, `etcd-key-rotate.yaml`, `haproxy-apiserver-lb-update.yaml`) intentionally have **no** `--limit` requirement — they iterate over all nodes via `serial: 1`.
- Exactly **one** manager in inventory must have `is_master: true`. That host becomes `master_manager_fact` — the single delegation target for every cluster-scope operation.
- **Optional:** if bastion-схема используется и bastion — один из узлов кластера (manager или worker), на нём ставится `is_bastion: true`. Этот хост становится `bastion_host_fact` и ребутится последним в `tasks-reboot-cluster.yaml` (избегаем self-kill ProxyJump tunnel'а). Подробности — [`variables.md`](.claude/rules/variables.md) §2.14.
- Before adding a new node to the cluster, run `playbook-app/cilium-install.yaml --tags post` first — it refreshes the Cilium host firewall with the new node's IPs, otherwise the join handshake is blocked.

---

## 1. Mental Model

After this section you should be able to classify any file in the repo and know which concept it belongs to.

### 1.1 Two orthogonal decompositions

The repo is structured along **two axes** that intersect at every component:

1. **Vertical layers** (bottom-up, what you build on top of what):
    ```
    L7 Observability        (Prometheus, Grafana, alerting, ServiceMonitors)
    L6 Applications         (ArgoCD, GitLab, Teleport, Zitadel, …)
    L5 Platform services    (Vault + ESO, cert-manager)
    L4 Storage              (Longhorn)
    L3 Ingress              (Traefik, HAProxy ingress)
    L2 CNI                  (Cilium — replaces kube-proxy, host firewall)
    L1 Control plane        (kubeadm, ETCD+encryption, HAProxy apiserver LB on systemd)
    L0 OS / node            (containerd, runc, kubelet, kernel modules, systemd)
    ```
2. **Horizontal phases** (per-component, ordering inside one install):
    ```
    pre   → install → post
    NP+ESO  workload   ingress/SM
    <c>-pre <c>       <c>-post
    ```
    Each phase is a **separate Helm release** so a single phase can be re-run in isolation.

### 1.2 Two "repos in one"

- `playbook-system/` is **imperative**. Most plays are **single-node** — they target a specific host and require `--limit <host>` (enforced by a gate). `node-install.yaml` is **bulk-friendly** — its sub-plays are idempotent and safe to run on multiple hosts at once, so `--limit` is optional. A few are **cluster-wide rolling-update** plays (`apiserver-sans-update.yaml`, `etcd-key-rotate.yaml`, `haproxy-apiserver-lb-update.yaml`) — they run on `hosts: all` with `serial: 1` and do not take `--limit`. All install packages, write `/etc/…` files, start systemd units.
- `playbook-app/` is **declarative-ish and cluster-scoped**. Plays always `hosts: managers` + `gather_facts: false`; all `kubectl`/`helm` work delegates to one manager (`master_manager_fact`) with `run_once: true`.

### 1.3 Two inventory layers

- `hosts-vars/` — base defaults, committed to git. Templates, public values, schema.
- `hosts-vars-override/` — real inventory, secrets, cluster-specific overrides. **Never committed**.

Merge order: `hosts-vars/` → `hosts-vars-override/` → inline play vars. Arrays with the `*_extra` suffix are **concatenated** across layers, not replaced (details in [`variables.md`](.claude/rules/variables.md) §1.5).

### 1.4 One delegation point

`master_manager_fact` is the hostname of the inventory host with `is_master: true`. Every `kubectl` / `helm` invocation in `playbook-app/` is `delegate_to: "{{ master_manager_fact }}" + run_once: true`. Consequence: only that host needs a kubeconfig, charts are rsync-copied there, helm runs locally against the copied chart. See [`playbook-conventions.md`](.claude/rules/playbook-conventions.md) §5 for the delegation pattern.

### 1.5 The "where am I?" stack

```
┌─────────────────────────────────────────────────────────────────┐
│  L7 Observability (mon-system — 8 workloads)                    │  playbook-app/mon-system-*
│  L6 Applications (argocd, gitlab, teleport, zitadel)            │  playbook-app/
│  L5 Platform    (vault, external-secrets, cert-manager)         │  playbook-app/
│  L4 Storage     (longhorn)                                      │  playbook-app/
│  L3 Ingress     (traefik @ traefik-lb, haproxy @ haproxy-lb)    │  playbook-app/
│  L2 CNI         (cilium — replaces kube-proxy, host firewall)   │  playbook-app/
│  L1 Control pl. (kubeadm, ETCD+encryption, HAProxy apiserver LB)│  playbook-system/
│  L0 OS / node   (containerd, runc, kubelet, modules, systemd)   │  playbook-system/
└─────────────────────────────────────────────────────────────────┘
```

### 1.6 The 3-phase install pattern — at a glance

Every `<c>-install.yaml` in `playbook-app/` produces three Helm releases:

- **`<c>-pre`** — NetworkPolicy, ServiceAccount, SecretStore, ExternalSecret (prerequisites)
- **`<c>`** — CRDs + workload (the actual thing)
- **`<c>-post`** — IngressRoute, ServiceMonitor, additional ConfigMaps (references running workload)

Each phase is an independent Helm release — re-runnable with `--tags pre|install|post`. Some components add extra phases (`crds/`, `prometheus/`, `postgresql/`, etc.). Full contract in [`playbook-conventions.md`](.claude/rules/playbook-conventions.md) §6.

### 1.7 Two verify tasks

- **`tasks-vault-config-verify.yaml`** — pure validation pre-check для Vault config: проверяет uniqueness `name` в merged `vault_policies + vault_policies_extra` и `vault_roles + vault_roles_extra`, а также referential integrity (`role.policies` → existing policy). Вызывается из `vault-install.yaml` и всех 10 ESO-integrated install/configure playbook'ов + `tests/helm-validate.yaml`.
- **`tasks-eso-verify.yaml`** — pure validation pre-check для одного компонента: 4 группы (input asserts, SecretStore→Vault connectivity scoped к role, ESO uniqueness `external_secret_name`/`body.target.name`, policy path coverage scoped к role's policies). Вызывается из всех 10 ESO-integrated install/configure playbook'ов.

Оба task'а pure read-only — не создают runtime facts. Inline merge `base + (extra | default([]))` выполняется прямо в местах использования (`<c>_pre_helm_values.eso.secrets`, `vault_spec.externalConfig.policies/roles`). Подробности — [`secrets-and-eso.md`](.claude/rules/secrets-and-eso.md) §3 и [`reusable-tasks.md`](.claude/rules/reusable-tasks.md) §1.8a–§1.8b.

---

## 2. Repository Anatomy

### 2.1 Top-level tree

```
k8s-ansible/
├── CLAUDE.md                  ← this file (map)
├── README.md, readme-*.md     ← human docs (not modified by Claude)
├── todo.md                    ← user's TODO list
├── hosts-extra.example.yaml   ← template for extensible *_extra arrays
├── Makefile                   ← test runner entry point (Docker-based)
├── .yamllint.yaml, .ansible-lint.yml ← lint configs
├── .claude/
│   ├── prompts/               ← cold-start prompts for manual chat workflow
│   └── rules/                 ← deep reference catalogs (atlas)
├── playbook-system/           ← node-scoped, imperative
│   └── tasks/
├── playbook-app/              ← cluster-scoped, declarative
│   ├── tasks/
│   └── charts/                ← 17 local Helm-chart dirs, one per component
├── tests/                     ← Docker-based test runner (Dockerfile + scripts)
├── hosts-vars/                ← base defaults (in git)
├── hosts-vars-override/       ← secrets + real inventory (gitignored)
├── hosts-vars-test/           ← synthetic test inventory (committed, no secrets)
├── docs/                      ← DO NOT TOUCH (user constraint)
└── sources/                   ← DO NOT TOUCH (user constraint)
```

### 2.2 Directory summaries

| Directory | Contents | Deep reference |
|---|---|---|
| `playbook-system/` | 27 playbooks (node prep, bootstrap, operational, rolling updates, diagnostics) | [`bootstrap-and-ha.md`](.claude/rules/bootstrap-and-ha.md) |
| `playbook-system/tasks/` | 21 reusable task includes (guards, cluster-facts, kubeadm, HAProxy, kubelet, package install, network diagnostics, file sync) | [`reusable-tasks.md`](.claude/rules/reusable-tasks.md) §2 |
| `playbook-app/` | 33 playbooks (17 install + 16 specials: configure, restart, rotate, sync, DR) | [`components.md`](.claude/rules/components.md) |
| `playbook-app/tasks/` | 33 reusable task includes (pre-check, copy-chart, helm, wait, Vault/ESO, k8s-list, cluster-info) | [`reusable-tasks.md`](.claude/rules/reusable-tasks.md) §1 |
| `playbook-app/charts/` | 17 local Helm-chart directories, one per component (mon-system has 11 phase subdirs; linstor has 4 phase subdirs: pre/, install-operator/, install-cluster/, post/; others have `pre/`, `install/`, `post/`) | [`components.md`](.claude/rules/components.md) per-component |
| `hosts-vars/` | 22 files — inventory skeleton, global settings, per-component vars, cross-cutting (vault, vpn-rules, teleport-configure) | [`variables.md`](.claude/rules/variables.md) |
| `hosts-vars-override/` | Mirror structure with real environment values. `ansible_password`, real IPs, Vault unseal keys, real domains — **gitignored** | — |

### 2.3 Inventory layering

- `hosts-vars/` — 22 files with defaults (schema + public values). In git.
- `hosts-vars-override/` — same structure, real values + secrets. Gitignored.
- `hosts-extra.example.yaml` — committed template documenting every `*_extra` extension point. Copy what you need into `hosts-vars-override/`.

---

## 3. Where to Go Next — `.claude/rules/` Index

This is the authoritative map of detailed documentation. Every topic beyond the invariants (§0) and mental model (§1) lives in one of these files.

| File | Owns |
|---|---|
| [`bootstrap-and-ha.md`](.claude/rules/bootstrap-and-ha.md) | Cluster lifecycle: 4-step bootstrap (node-install → cluster-init → manager-join → worker-join), ETCD key rotation with state-file resume, apiserver SANs update, HAProxy LB update, node drain/remove/clean, `serial: 1` pattern, recovery matrix |
| [`networking.md`](.claude/rules/networking.md) | Cilium as CNI + kube-proxy replacement, `CiliumClusterwideNetworkPolicy` host firewall, VPN allowlist middleware (`vpn_ips`, Traefik middleware), per-component cert-manager `Issuer` + ACME HTTP-01 solver NetworkPolicies |
| [`observability.md`](.claude/rules/observability.md) | Mon-system consolidated stack: install phases (`crds`, `prometheus-operator`, `prometheus`, `alertmanager`, `node-exporter`, `ksm`, `loki`, `vector`, `grafana`, `post`), centralized ServiceMonitors in `post/`, Grafana ESO integration (admin password from Vault), Alertmanager routing |
| [`components.md`](.claude/rules/components.md) | Per-component reference — chart path, namespace, releases, required vars, ESO integration, dependencies, ServiceMonitor, image-registry overrides. One section per component. Namespaces matrix + dependency tiers |
| [`playbook-conventions.md`](.claude/rules/playbook-conventions.md) | Imperative authoring rules for new playbooks (19 numbered rules): file location & naming, play header, required guards, fact gathering, delegation, 3-phase install structure, task naming, include strategy, values-override pattern, chart copy, ESO integration, ACME, rollout verification, variables contract, non-install patterns, anti-patterns, commit checklist, parameter validation assert blocks |
| [`reusable-tasks.md`](.claude/rules/reusable-tasks.md) | Full catalog of task includes (system + app) with purpose / input / validation / output / callers / idempotency for every one |
| [`variables.md`](.claude/rules/variables.md) | Variable patterns (Tier 1 — per-component suffix conventions, `*_extra` concat-merge, inventory precedence) and global cross-cutting catalog (Tier 2 — k8s-base, HAProxy LB, ETCD encryption, kubelet, kubeadm template, Vault, VPN, cert-manager, Teleport, inventory host vars, output facts) |
| [`secrets-and-eso.md`](.claude/rules/secrets-and-eso.md) | Vault + ESO topology, inventory contracts (`vault_policies`, `vault_roles`, `eso_vault_integration_<c>`, `<c>_secrets`), merge tasks, SecretStore + ExternalSecret templates, seed vs rotation flows, adding a new ESO-integrated component, per-component Vault paths, troubleshooting |
| [`commands-reference.md`](.claude/rules/commands-reference.md) | Canonical invocations — bootstrap sequence, app install order, single-phase re-runs, operational tasks (node add, drain, remove, ETCD rotation, SAN update, HAProxy update, Vault rotate, ESO force-sync), component restart, debugging one-liners, dry-run flags |
| [`report-formats.md`](.claude/rules/report-formats.md) | Канонические форматы DONE / BLOCKED / NEEDS_CLARIFICATION (§1) для отчётов DevOps/DevOps-docs → TeamLead. Запреты защитных добавок: для кода (§2 — `failed_when`/`ignore_errors`/zero-scope), для документации (§3 — лишние секции/таблицы/bullets). Строгие правила секций `Files changed`, `Verification`, `Side issues`. |
| [`team-workflow.md`](.claude/rules/team-workflow.md) | **Manual chat mode workflow** — как user переносит SUB-task спеки и отчёты между Opus (TeamLead) и Sonnet (DevOps / DevOps-docs) chat-окнами. Роли и границы, 10-шаговый жизненный цикл, формат SUB-спеки (§4), формат отчёта (§5), verify-протокол (§6), commit-протокол (§7), TeamLead self-discipline (§8, включая §8.7 «архитектурно, не заплатки»), escalation (§9), нерушимые принципы (§10) |
| [`testing.md`](.claude/rules/testing.md) | Layer 1 (yamllint + ansible-lint + ansible-playbook --syntax-check) and Layer 2 (helm template + kubeconform for upstream charts) test runners. Docker image (`tests/Dockerfile`), Makefile entry point, `.yamllint.yaml` / `.ansible-lint.yml` configs, `hosts-vars-test/` synthetic inventory, `tests/helm-validate.yaml` Layer 2 driver. Commands, pinned versions, debugging, known upstream issues (e.g. traefik skip). Deferred local-wrapper / CRD-bundle / variable-resolution / snapshot layers are out of scope. |

### 3.1 Manual chat workflow — entry point

Workflow: один долгоживущий Opus 4.7 chat (TeamLead) + новое чистое Sonnet 4.6 chat-окно на каждый SUB-task (DevOps или DevOps-docs). Bootstrap-промпт нужен только для TeamLead'а — Sonnet работает с холодным контекстом, всё что ему нужно для конкретной SUB живёт внутри SUB-промпта (mini-bootstrap-prefix через секцию «Контекст и правила» в skeleton'е [`team-workflow.md`](.claude/rules/team-workflow.md) §4.1).

| Файл | Вставлять куда | Когда |
|---|---|---|
| [`.claude/prompts/teamlead-cold-start.md`](.claude/prompts/teamlead-cold-start.md) | Opus 4.7 chat | При холодном старте — первое сообщение TeamLead-у |

Подробности workflow — в [`team-workflow.md`](.claude/rules/team-workflow.md) §3 (cold-start) и §4 (формат SUB-промпта).

### 3.2 Finding the right file (cheat sheet)

| Task | Start with |
|---|---|
| Understand where to put a new file | [`playbook-conventions.md`](.claude/rules/playbook-conventions.md) §1 |
| Add a new component | [`components.md`](.claude/rules/components.md) + [`playbook-conventions.md`](.claude/rules/playbook-conventions.md) |
| Add a Vault-backed secret | [`secrets-and-eso.md`](.claude/rules/secrets-and-eso.md) §7 |
| Bootstrap a fresh cluster | [`commands-reference.md`](.claude/rules/commands-reference.md) §2 + [`bootstrap-and-ha.md`](.claude/rules/bootstrap-and-ha.md) §1 |
| Add a node to existing cluster | [`bootstrap-and-ha.md`](.claude/rules/bootstrap-and-ha.md) §1.5 + [`networking.md`](.claude/rules/networking.md) §2 |
| Rotate a credential / ETCD key | [`bootstrap-and-ha.md`](.claude/rules/bootstrap-and-ha.md) §3 or [`secrets-and-eso.md`](.claude/rules/secrets-and-eso.md) §8 |
| Understand a variable suffix | [`variables.md`](.claude/rules/variables.md) §1 |
| Find a task include by function | [`reusable-tasks.md`](.claude/rules/reusable-tasks.md) |
| Debug a failing install | [`commands-reference.md`](.claude/rules/commands-reference.md) §5 + per-topic "Troubleshooting" tables in other files |
| Run tests / debug a lint failure | [`testing.md`](.claude/rules/testing.md) |
| Setup a manual chat session (TeamLead Opus + Sonnet per SUB) | §3.1 above + [`team-workflow.md`](.claude/rules/team-workflow.md) §3 |

### 3.3 Human-facing docs (not modified by Claude)

- `README.md` — quickstart in Russian.
- `readme-vault.md`, `readme-monitoring.md`, `readme-helpers.md` — topic deep-dives in Russian.
- `todo.md` — user's TODO list (planned Reloader install, Zitadel hardening, backup/rotation improvements).

### 3.4 Orthogonal files

- `docs/`, `sources/` — explicitly out of scope by user instruction.

---

## 4. Glossary

| Term | Definition |
|---|---|
| **bank-vaults** | Operator from Banzai Cloud that manages a Vault CR (install, unseal, configure policies/auth/secrets declaratively). This project installs Vault via it. |
| **bootstrap** | The first three playbooks in `playbook-system/`: `node-install`, `cluster-init`, `manager-join`/`worker-join`. |
| **CNI** | Container Network Interface. This project uses Cilium as its CNI and replaces kube-proxy. |
| **ESO** | [External Secrets Operator](https://external-secrets.io). Pulls secrets from Vault into K8s `Secret` objects. |
| **`ExternalSecret`** | ESO CR that declares: source (`SecretStore`), path in Vault, target K8s `Secret` name, refresh interval. |
| **host firewall** | Cilium feature: node-level eBPF firewall enforcing `CiliumClusterwideNetworkPolicy` on host traffic, not just pod traffic. |
| **`kubeadm`** | Upstream K8s tool used here for cluster init and node join. Installed as a pinned apt package. |
| **`master_manager_fact`** | Ansible fact = hostname of the inventory manager with `is_master: true`. All `kubectl`/`helm` delegates here. |
| **`*_extra`** | Naming convention for array variables that **concatenate** with their base. Base lives in `hosts-vars/`; override in `hosts-vars-override/`. |
| **phase release** | The Helm release created by one of the three standard phases: `<c>-pre`, `<c>`, `<c>-post`. |
| **`SecretStore`** | ESO CR describing how to authenticate to Vault (Kubernetes SA + role) for a specific namespace/component. |
| **`serial: 1`** | Ansible directive: run this play on one host at a time. Used for apiserver restarts, HAProxy reloads. |
| **VPN allowlist** | `vpn_ips` list in `hosts-vars/vpn-rules.yaml`, consumed by Traefik `ipAllowList` middleware (`vpn-only`) to gate internal-only ingresses. |
| **TeamLead / DevOps / DevOps-docs** | Agent team roles (see [`team-workflow.md`](.claude/rules/team-workflow.md)). TeamLead is the human-facing session (Opus 4.7), DevOps writes code (Sonnet 4.6), DevOps-docs writes documentation (Sonnet 4.6). |

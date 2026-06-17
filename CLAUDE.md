# k8s-ansible

Production bare-metal Kubernetes cluster automation. Two halves:

- **`playbook-system/`** — imperative, node-scoped bootstrap and lifecycle operations (kubeadm, HAProxy LB as systemd, ETCD encryption, node join/drain/remove).
- **`playbook-app/`** — declarative cluster-scoped application installs via local Helm charts, every component deployed through a consistent 3-phase pattern (`pre` → `install` → `post`).

This file is the **thin always-loaded map**. It holds the hard invariants (§0), the mental model (§1), a compact anatomy (§2), and an **index** (§3) pointing to detailed catalogs under **`reference/`**. The `reference/` files are **NOT auto-loaded** — read the specific one you need on-demand (§3 tells you which). This keeps every session's baseline context small.

---

## 0. Hard Invariants (do not violate)

Violating any of these will break the cluster or leak secrets.

- `longhorn-system` namespace is **upstream-fixed** — never rename. (Note: `argocd` was historically also fixed but is now configurable via `argocd_namespace` — namespace rebind handled by builtin kustomize transformer via `dto_target_namespace` in `tasks-helm-template-kustomize-build.yaml`; see [`components.md`](reference/components.md) §9.)
- `kube-proxy` is **disabled at `kubeadm init`** via declarative `proxy.disabled: true` in `ClusterConfiguration`. Cilium replaces it. Do not re-enable.
- `hosts-vars-override/` is **never committed**. It contains `ansible_password`, real IPs, Vault unseal keys, and all secrets.
- Always run Ansible with **both** inventories, pointing the override at the **target-cluster subdir**: `-i hosts-vars/ -i hosts-vars-override/<cluster>/`. Clusters live as subdirs under `hosts-vars-override/` (e.g. `test-1`, `1520-tech-prod-1`) — pointing `-i` at the bare parent loads every cluster at once (a bug). Running with only one inventory is always a bug.
- **Single-node system playbooks require `--limit`**. Forgetting `--limit` on `cluster-init.yaml` / `manager-join.yaml` / `worker-join.yaml` / `node-drain-on.yaml` / `node-drain-off.yaml` / `node-remove.yaml` / `node-clean.yaml` will fail a `tasks-require-limit.yaml` gate (by design). `full-node-install.yaml` is **bulk-friendly** by design: `--limit` is optional — use without `--limit` for bulk preparation of multiple nodes (sub-plays are idempotent: `apt`, kernel modules, sysctls, HAProxy on `127.0.0.1`); use with `--limit <host>` to prepare a single node added to an already-running cluster. Cluster-wide rolling-update plays (`apiserver-sans-update.yaml`, `etcd-key-rotate.yaml`, `haproxy-apiserver-lb-update.yaml`) intentionally have **no** `--limit` requirement — they iterate over all nodes via `serial: 1`.
- Exactly **one** manager in inventory must have `is_master: true`. That host becomes `master_manager_fact` — the single delegation target for every cluster-scope operation.
- **Optional:** if bastion-схема используется и bastion — один из узлов кластера (manager или worker), на нём ставится `is_bastion: true`. Этот хост становится `bastion_host_fact` и ребутится последним в `tasks-reboot-cluster.yaml` (избегаем self-kill ProxyJump tunnel'а). Подробности — [`variables.md`](reference/variables.md) §2.14.
- Before adding a new node to the cluster, run `playbook-app/cilium-install.yaml --tags post` first — it refreshes the Cilium host firewall with the new node's IPs, otherwise the join handshake is blocked.

---

## 1. Mental Model

### 1.1 Two orthogonal decompositions

1. **Vertical layers** (bottom-up — the "where am I?" stack):
    ```
    L7 Observability   (mon-system: Prometheus, Grafana, Loki, Vector, Alertmanager, node-exporter, ksm)  playbook-app/
    L6 Applications    (argocd, gitlab, teleport, zitadel)                                                playbook-app/
    L5 Platform        (vault + external-secrets, cert-manager, stakater-reloader)                        playbook-app/
    L4 Storage         (longhorn | linstor) + seaweedfs (S3)                                              playbook-app/
    L3 Ingress         (traefik @ traefik-lb, haproxy @ haproxy-lb)                                       playbook-app/
    L2 CNI             (cilium — replaces kube-proxy, host firewall)                                      playbook-app/
    L1 Control plane   (kubeadm, ETCD+encryption, HAProxy apiserver LB on systemd)                        playbook-system/
    L0 OS / node       (containerd, runc, kubelet, kernel modules, systemd)                               playbook-system/
    ```
2. **Horizontal phases** (per-component install ordering): `pre` (NP + ESO) → `install` (CRDs + workload) → `post` (ingress / ServiceMonitor). Each phase is a **separate Helm release**, re-runnable with `--tags pre|install|post`.

### 1.2 Two "repos in one"

- `playbook-system/` — **imperative**. Most plays single-node, require `--limit` (gate). `full-node-install.yaml` bulk-friendly. A few cluster-wide rolling-update plays run `hosts: all` + `serial: 1`, no `--limit`. Install packages, write `/etc/…`, start systemd units.
- `playbook-app/` — **cluster-scoped**. Always `hosts: managers` + `gather_facts: false`; all `kubectl`/`helm` delegate to `master_manager_fact` with `run_once: true`.

### 1.3 Two inventory layers

- `hosts-vars/` — base defaults, committed. Templates, public values, schema.
- `hosts-vars-override/` — real inventory + secrets. **Never committed**.

Merge: `hosts-vars/` → `hosts-vars-override/` → inline play vars. Arrays with `*_extra` suffix **concatenate** across layers (see [`variables.md`](reference/variables.md) §1.5).

### 1.4 One delegation point

`master_manager_fact` = hostname of the inventory host with `is_master: true`. Every `kubectl`/`helm` call in `playbook-app/` is `delegate_to: "{{ master_manager_fact }}" + run_once: true`. Only that host needs a kubeconfig; charts are copied there; helm runs locally. See [`playbook-conventions.md`](reference/playbook-conventions.md) §5.

---

## 2. Repository Anatomy

```
k8s-ansible/
├── CLAUDE.md                  ← this file (thin map, auto-loaded)
├── reference/                 ← deep catalogs (NOT auto-loaded — read on-demand, see §3)
├── README.md, readme-*.md     ← human docs (not modified by Claude)
├── todo.md                    ← user's TODO list
├── hosts-extra.example.yaml   ← template for extensible *_extra arrays
├── Makefile, .yamllint.yaml, .ansible-lint.yml  ← test runner + lint configs
├── .claude/prompts/           ← cold-start prompt for manual chat workflow
├── playbook-system/  (+ tasks/, benchmark/, utils/)  ← node-scoped, imperative
├── playbook-app/     (+ tasks/, charts/) ← cluster-scoped, declarative; charts/ = 18 local Helm-chart dirs
├── tests/                     ← Docker-based test runner
├── hosts-vars/                ← base defaults (in git)
├── hosts-vars-override/       ← secrets + real inventory (gitignored)
├── hosts-vars-test/           ← synthetic test inventory (committed, no secrets)
├── filter_plugins/            ← Python compute (seaweedfs sync, vault/eso verify)
└── docs/, sources/            ← DO NOT TOUCH (user constraint)
```

| Directory | Deep reference |
|---|---|
| `playbook-system/` + `tasks/` | [`bootstrap-and-ha.md`](reference/bootstrap-and-ha.md), [`reusable-tasks.md`](reference/reusable-tasks.md) §2 |
| `playbook-app/` + `tasks/` + `charts/` | [`components.md`](reference/components.md), [`reusable-tasks.md`](reference/reusable-tasks.md) §1 |
| `hosts-vars/` | [`variables.md`](reference/variables.md) |

---

## 3. Index — read the right `reference/` file on-demand

`reference/*.md` are **not** in your context by default. Pick the file matching your task and `Read` it.

| File | Owns |
|---|---|
| [`bootstrap-and-ha.md`](reference/bootstrap-and-ha.md) | Cluster lifecycle: 4-step bootstrap, ETCD key rotation (state-file resume), apiserver SANs update, HAProxy LB update, node drain/remove/clean, `serial: 1` pattern, recovery matrix |
| [`networking.md`](reference/networking.md) | Cilium CNI + kube-proxy replacement, `CiliumClusterwideNetworkPolicy` host firewall, VPN allowlist middleware, per-component cert-manager `Issuer` + ACME HTTP-01 solver NetworkPolicies, cross-namespace consumer-owned NP pattern |
| [`observability.md`](reference/observability.md) | mon-system consolidated stack: install phases, centralized ServiceMonitors in `post/`, Grafana ESO + Postgres, Loki S3, Alertmanager routing |
| [`components.md`](reference/components.md) | Per-component reference — chart path, namespace, releases, required vars, ESO, dependencies, ServiceMonitor. One section per component. Namespaces matrix + dependency tiers |
| [`playbook-conventions.md`](reference/playbook-conventions.md) | Authoring rules (numbered): file location/naming, play header, guards, delegation, 3-phase structure, include strategy, values-override, chart copy, ESO, ACME, rollout verify, anti-patterns, commit checklist, param-validation asserts, helm-template+kustomize pattern (§21), extraObjects (§22) |
| [`reusable-tasks.md`](reference/reusable-tasks.md) | Full catalog of task includes (system + app) — purpose / input / validation / output / callers / idempotency |
| [`variables.md`](reference/variables.md) | Tier 1 (per-component suffix conventions, `*_extra` concat-merge, inventory precedence, `_local_` facts) + Tier 2 (global cross-cutting catalog: k8s-base, HAProxy LB, ETCD, kubelet, kubeadm, Vault, VPN, cert-manager, Teleport, host vars, output facts, bastion) |
| [`secrets-and-eso.md`](reference/secrets-and-eso.md) | Vault + ESO topology, inventory contracts, verify tasks, SecretStore + ExternalSecret templates, seed vs rotation, adding a new ESO component, per-component Vault paths, troubleshooting |
| [`commands-reference.md`](reference/commands-reference.md) | Canonical invocations — bootstrap sequence, app install order, single-phase re-runs, operational tasks, component restart, debugging one-liners, dry-run flags |
| [`report-formats.md`](reference/report-formats.md) | Canonical report formats DONE / BLOCKED / NEEDS_CLARIFICATION (§1) + defensive-additions bans for code (§2) and docs (§3) |
| [`team-workflow.md`](reference/team-workflow.md) | **Manual chat mode workflow** — TeamLead (Opus) + Sonnet per SUB-task. Roles & boundaries, 10-step lifecycle, SUB-prompt format (§4), report format (§5), verify protocol (§6), commit protocol (§7), self-discipline (§8), escalation (§9), principles (§10) |
| [`testing.md`](reference/testing.md) | `make test` runner (Layers 1–3: yamllint + ansible-lint + syntax-check + helm/kubeconform + pytest). Docker image, pinned versions, debugging, known upstream issues |

### 3.1 Manual chat workflow — entry point

One long-lived Opus chat (TeamLead) + a fresh Sonnet chat per SUB-task (DevOps = code, DevOps-docs = docs). The cold-start prompt is for TeamLead only; Sonnet executors work cold — everything needed for a SUB lives inside the SUB-prompt (mini-bootstrap in [`team-workflow.md`](reference/team-workflow.md) §4.1). Cold-start file: [`.claude/prompts/teamlead-cold-start.md`](.claude/prompts/teamlead-cold-start.md). Details in [`team-workflow.md`](reference/team-workflow.md) §3–§4.

### 3.2 Finding the right file (cheat sheet)

| Task | Start with |
|---|---|
| Where to put a new file | [`playbook-conventions.md`](reference/playbook-conventions.md) §1 |
| Add a new component | [`components.md`](reference/components.md) + [`playbook-conventions.md`](reference/playbook-conventions.md) |
| Add a Vault-backed secret | [`secrets-and-eso.md`](reference/secrets-and-eso.md) §7 |
| Bootstrap a fresh cluster | [`commands-reference.md`](reference/commands-reference.md) §2 + [`bootstrap-and-ha.md`](reference/bootstrap-and-ha.md) §1 |
| Add a node to existing cluster | [`bootstrap-and-ha.md`](reference/bootstrap-and-ha.md) §1.5 + [`networking.md`](reference/networking.md) §2 |
| Rotate a credential / ETCD key | [`bootstrap-and-ha.md`](reference/bootstrap-and-ha.md) §3 or [`secrets-and-eso.md`](reference/secrets-and-eso.md) §8 |
| Understand a variable suffix | [`variables.md`](reference/variables.md) §1 |
| Find a task include by function | [`reusable-tasks.md`](reference/reusable-tasks.md) |
| Debug a failing install | [`commands-reference.md`](reference/commands-reference.md) §5 + per-topic Troubleshooting tables |
| Run tests / debug a lint failure | [`testing.md`](reference/testing.md) |
| Setup a manual chat session | §3.1 above + [`team-workflow.md`](reference/team-workflow.md) §3 |

### 3.3 Human-facing docs (not modified by Claude)

`README.md`, `readme-vault.md`, `readme-monitoring.md`, `readme-helpers.md` — Russian quickstart + deep-dives. `todo.md` — user's TODO. `docs/`, `sources/` — explicitly out of scope.

---

## 4. Glossary

| Term | Definition |
|---|---|
| **`master_manager_fact`** | Ansible fact = hostname of the inventory manager with `is_master: true`. All `kubectl`/`helm` delegate here. |
| **`*_extra`** | Array-var naming convention: values **concatenate** with their base. Base in `hosts-vars/`, override in `hosts-vars-override/`. |
| **phase release** | Helm release of one standard phase: `<c>-pre`, `<c>`, `<c>-post`. |
| **ESO** | [External Secrets Operator](https://external-secrets.io) — pulls secrets from Vault into K8s `Secret` objects. |
| **`SecretStore` / `ExternalSecret`** | ESO CRs: how to auth to Vault (SA + role) / what to pull (source path → target Secret). |
| **bank-vaults** | Banzai Cloud operator that installs + unseals + declaratively configures the Vault CR. |
| **host firewall** | Cilium node-level eBPF firewall enforcing `CiliumClusterwideNetworkPolicy` on host traffic. |
| **`serial: 1`** | Ansible directive: one host at a time. Used for apiserver restarts, HAProxy reloads. |
| **VPN allowlist** | `vpn_ips` in `hosts-vars/vpn-rules.yaml`, consumed by Traefik `ipAllowList` middleware (`vpn-only`). |
| **TeamLead / DevOps / DevOps-docs** | Manual-chat roles (see [`team-workflow.md`](reference/team-workflow.md)). TeamLead = Opus human-facing; DevOps = code (Sonnet); DevOps-docs = docs (Sonnet). |

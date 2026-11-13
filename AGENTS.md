# k8s-ansible (1520-tech-ansible)

Production bare-metal Kubernetes cluster automation via Ansible. Two halves: `playbook-system/` — imperative, node-scoped bootstrap/lifecycle ops (kubeadm, HAProxy LB as systemd, ETCD encryption, node join/drain/remove); `playbook-app/` — declarative cluster-scoped app installs via local Helm charts, 3-phase pattern `pre` → `install` → `post`, each phase a separate Helm release. No CI: tests (`make test`) and deployments (`ansible-playbook`) are operator-run from a workstation.

## Invariants

- Agents run static checks only (`make test*`, git/grep/read); any `ansible-playbook` against a real inventory — only on explicit operator instruction.
- `hosts-vars-override/**` = secrets (committed deliberately to this private repo) — never print/copy values.
- Always both inventories: `-i hosts-vars/ -i hosts-vars-override/<cluster>/` (never the bare override parent).
- Single-node system playbooks need `--limit` (gate-enforced).
- `longhorn-system` namespace and the two-namespace argo-workflows model are fixed.

Full list: [`docs/ai/INVARIANTS.md`](docs/ai/INVARIANTS.md).

## Anatomy

```
k8s-ansible/
├── playbook-system/       # imperative node ops: 14 playbooks, utils/ (lifecycle), benchmark/, tasks/ (22 includes)
├── playbook-app/          # declarative installs: 26 *-install.yaml, 23 *-restart.yaml, specials, tasks/ (28 includes)
│   └── charts/            # 26 local Helm chart dirs (phase charts)
├── hosts-vars/            # base defaults, committed (34 files)
├── hosts-vars-example/    # committed example inventory layer (55 files)
├── hosts-vars-override/   # real inventory + secrets per cluster subdir — committed (private repo), never print values
├── hosts-vars-test/       # synthetic test inventory for test-syntax/test-helm (committed, no secrets)
├── filter_plugins/        # 11 Python filter plugins (pytest-covered)
├── tests/                 # Docker-based test runner; Dockerfile pins all tool versions
├── reference/             # 13 deep catalogs — NOT auto-loaded (see index)
├── pkgs-sources/          # 8 vendored binaries
├── benchmark/             # under playbook-system/: cpu/disk-io/network/ram benchmarks
├── password-gen/          # password generation helpers
├── docs/, sources/        # human docs / upstream snapshots — OUT OF SCOPE for agents
├── Makefile, .yamllint.yaml, .ansible-lint.yml   # test runner + lint configs
└── README.md, readme-*.md, todo.md               # human docs (not modified by agents)
```

## Index

`docs/ai/*` and `reference/*` are **not auto-loaded** — read the file matching your task on demand.

### docs/ai/

| File | What's inside |
|---|---|
| [`CONTEXT.md`](docs/ai/CONTEXT.md) | architecture, delegation + inventory model, environments, module table |
| [`INVARIANTS.md`](docs/ai/INVARIANTS.md) | 15 hard rules with consequences |
| [`COMMANDS.md`](docs/ai/COMMANDS.md) | make targets, operator-only invocations, forbidden operations |
| [`DATA.md`](docs/ai/DATA.md) | data stores, secrets data model, sensitive-data zone |
| [`GOTCHAS.md`](docs/ai/GOTCHAS.md) | audit side issues, operational gotchas, deep-gotchas pointer |
| [`DECISIONS.md`](docs/ai/DECISIONS.md) | bootstrap interview answers + accepted decisions |

### reference/

| File | Owns |
|---|---|
| [`bastion-proxy.md`](reference/bastion-proxy.md) | external HAProxy edge proxy |
| [`bootstrap-and-ha.md`](reference/bootstrap-and-ha.md) | cluster lifecycle / HA / ETCD key rotation |
| [`commands-reference.md`](reference/commands-reference.md) | canonical invocations |
| [`components.md`](reference/components.md) | per-component catalog |
| [`networking.md`](reference/networking.md) | Cilium / NetworkPolicies / ACME |
| [`observability.md`](reference/observability.md) | mon-system stack |
| [`playbook-conventions.md`](reference/playbook-conventions.md) | numbered authoring rules |
| [`report-formats.md`](reference/report-formats.md) | project report formats |
| [`reusable-tasks.md`](reference/reusable-tasks.md) | task include catalog |
| [`secrets-and-eso.md`](reference/secrets-and-eso.md) | Vault / ESO |
| [`team-workflow.md`](reference/team-workflow.md) | legacy manual chat workflow |
| [`testing.md`](reference/testing.md) | make test layers |
| [`variables.md`](reference/variables.md) | variable conventions |

## Quick commands

- `make test` — all 5 stages (yamllint + ansible-lint + syntax-check + helm + pytest); final line `make test → exit 0 (all 5 stages passed)`.
- `make test-ansible-lint` — ansible-lint (profile moderate) over `playbook-system/` + `playbook-app/`.
- `make test-syntax` — `ansible-playbook --syntax-check` for every playbook and task include.

All other commands, including operator-only playbook invocations: [`docs/ai/COMMANDS.md`](docs/ai/COMMANDS.md).

## Glossary

| Term | Definition |
|---|---|
| `master_manager_fact` | inventory manager with `is_master: true`; all `playbook-app` kubectl/helm delegate to it (`run_once: true`) |
| `bastion_proxy` / `is_bastion` | `bastion_proxy` = inventory group of external HAProxy edge proxies; `is_bastion` = SSH ProxyJump host flag (host rebooted last). Different things — a `bastion_proxy` host never sets `is_bastion` |
| `*_extra` | array-var convention: override extras concatenate with the base array across inventory layers |
| phase release | Helm release of one standard phase: `<c>-pre`, `<c>`, `<c>-post` |
| ESO | External Secrets Operator — pulls secrets from Vault into K8s `Secret` objects |
| `serial: 1` | one host at a time; used by cluster-wide rolling ops (apiserver SANs, ETCD key rotate, HAProxy LB update) |
| host firewall | Cilium node-level eBPF firewall enforcing `CiliumClusterwideNetworkPolicy` on host traffic |

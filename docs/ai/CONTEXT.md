> Actual for commit: a451e66

# CONTEXT — k8s-ansible (1520-tech-ansible)

## Purpose

Production bare-metal Kubernetes cluster automation via Ansible. The repo drives real clusters: `playbook-system/` bootstraps and maintains nodes (OS prep, kubeadm, ETCD encryption, apiserver LB), `playbook-app/` installs and configures the entire in-cluster stack (CNI, storage, ingress, platform, apps, observability). Deployments are operator-run from a workstation; there is no CI.

## Architecture

### Two halves

- `playbook-system/` — **imperative, node-scoped**. Most plays single-node and gated by `--limit`; `full-node-install.yaml` bulk-friendly; a few cluster-wide rolling plays run `hosts: all` + `serial: 1` with no `--limit` by design. Installs packages, writes `/etc/…`, manages systemd units.
- `playbook-app/` — **declarative, cluster-scoped**. Always `hosts: managers` + `gather_facts: false`; all `kubectl`/`helm` delegate to `master_manager_fact` with `run_once: true`. 26 component install playbooks, 23 restart playbooks.

### Layer stack (L0–L7)

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

### Phase pattern

Horizontal install ordering per component: `pre` (NetworkPolicy + ESO) → `install` (CRDs + workload) → `post` (ingress / ServiceMonitor). Each phase is a **separate Helm release**, re-runnable with `--tags pre|install|post`. Some components add a `cfg` stage (RBAC/objects in foreign namespaces).

**Exception — argo-workflows (6 stages):** `crds → pre → pre-cfg → install → post → cfg`. `pre-cfg` strictly **before** `install` (controller CrashLoopBackOff / silent WaitForCacheSync hang otherwise — rationale header `playbook-app/argo-workflows-install.yaml:1-40`, `playbook-app/charts/argo-workflows/pre-cfg/templates/cr-namespace.yaml`).

## Delegation model

`master_manager_fact` = hostname of the inventory manager with `is_master: true`. Every `kubectl`/`helm` call in `playbook-app/` delegates there with `run_once: true`; only that host needs a kubeconfig, charts are copied there, helm runs locally. Enforcement: `playbook-app/tasks/tasks-pre-check.yaml:26-31` fails when no `is_master` host exists. Exceptions running `hosts: managers:workers`: `longhorn-tags-sync.yaml`, `teleport-ssh-agent-install.yaml`.

## Inventory model

Four layers:

- `hosts-vars/` — committed base defaults (34 files).
- `hosts-vars-example/` — committed example layer (55 files).
- `hosts-vars-test/` — synthetic test inventory for `test-syntax`/`test-helm` (committed, no secrets).
- `hosts-vars-override/<cluster>/` — real inventory + secrets, one subdir per cluster; committed deliberately to this private repo (owner decision 2026-10-25). Never print/copy values.

Merge: `-i hosts-vars/ -i hosts-vars-override/<cluster>/` — always both; pointing `-i` at the bare override parent loads every cluster at once (bug). Arrays with `*_extra` suffix **concatenate** across layers: pattern `"{{ base + (extra | default([])) }}"` (e.g. `hosts-vars/argo-events.yaml:191`).

## Environments

One active prod cluster: `1520-tech-prod-1` (override subdir with the fullest file set). `test-1` — dormant reserve test context (25 files, may lag; actualized on demand). No `when: cluster ==` conditionals anywhere — all cluster differences live in override files.

## External integrations

- **Vault** via bank-vaults operator — OCI chart `oci://ghcr.io/bank-vaults/helm-charts/vault-operator` (`hosts-vars/vault.yaml:226`); unseal keys in K8s Secret `vault-unseal-keys`, shares 3 / threshold 2 (`hosts-vars/vault.yaml:244-247`); rekey via `playbook-app/vault-rotate.yaml`.
- **ESO** — per-component `pre` charts render SecretStore/ExternalSecret; verify via `playbook-app/tasks/tasks-eso-verify.yaml` + `filter_plugins/eso_verify.py`.
- **GitLab** (+ runner) — root password Vault-reconciled by `playbook-app/tasks/gitlab/tasks-gitlab-config-root.yaml`.
- **ACME Let's Encrypt** — ClusterIssuer `acme-prod` (`hosts-vars/cert-manager.yaml:88-96`).
- **SeaweedFS** — central S3 (Loki + component buckets).
- **Teleport** (SQLite backend); **ArgoCD** (vendored upstream `namespace-install.yaml` v3.6.1); **Cilium** (CNI, replaces kube-proxy); **Longhorn/LINSTOR** (alternative storage); **Traefik**; **HAProxy** in three roles: node systemd apiserver LB, in-cluster chart, external `bastion_proxy` edge.

## Module table

| Path | Purpose | Key files |
|---|---|---|
| `playbook-system/` (14 plays) | node lifecycle | `utils/`: `manager-join.yaml`, `worker-join.yaml`, `node-drain-on/off.yaml`, `node-remove.yaml`, `node-clean.yaml`, `node-info.yaml`; rolling `serial: 1` (no `--limit` by design): `apiserver-sans-update.yaml`, `etcd-key-rotate.yaml`, `haproxy-apiserver-lb-update.yaml` |
| `playbook-system/` node prep | bulk/single node preparation | `full-node-install.yaml` (bulk-friendly 11-step import chain, no limit gate), `preflight.yaml`, `prepare-linux-pkgs.yaml`, `prepare-longhorn.yaml`, `prepare-linstor.yaml`, `prepare-cilium.yaml`, `install-main-components.yaml`, `install-haproxy-apiserver-lb.yaml`, `install-helm.yaml`, `install-k9s.yaml`, `setup-ssh-keys.yaml`, `set-hostname.yaml`, `bastion-proxy-install.yaml` |
| `playbook-system/benchmark/` | cpu / disk-io / network / ram benchmarks | — |
| `playbook-system/tasks/` | 22 task includes | — |
| `playbook-app/` | component installs | 26 `*-install.yaml` (incl. `teleport-ssh-agent-install.yaml`), 23 `*-restart.yaml` |
| `playbook-app/` specials | one-off operations | `cluster-info`, `eso-force-sync`, `vault-rotate`, `longhorn-s3-restore-create/delete`, `longhorn-tags-sync`, `teleport-user-reset` |
| `playbook-app/tasks/` | 28 task includes | subdirs `vault/`, `argocd/`, `gitlab/`, `seaweedfs/` |
| `playbook-app/charts/` | 26 local Helm chart dirs (phase charts) | — |
| `filter_plugins/` | 11 Python filter plugins, each with pytest coverage in `tests/python/` | one-liners below |

Filter plugins:

- `argocd_accounts.py` — diffs desired ArgoCD local accounts against the Vault mirror.
- `argocd_accounts_distribute.py` — distributes ArgoCD account credentials from the Vault mirror.
- `eso_verify.py` — ESO per-component validation (SecretStore↔Vault connectivity).
- `kargo_rbac_verify.py` — Kargo RBAC consistency checks.
- `password_generate.py` — CSPRNG password generation with exact character-class counts.
- `seaweedfs_bucket.py` — SeaweedFS bucket sync (live filer vs inventory).
- `seaweedfs_user.py` — SeaweedFS user/identity sync.
- `seaweedfs_policy.py` — SeaweedFS managed-policy sync.
- `seaweedfs_distribute.py` — distributes SeaweedFS identity credentials.
- `teleport_cfg_verify.py` — Teleport cfg-stage validation.
- `vault_config_verify.py` — Vault config validation (policies, roles, role→policy links).

## CI/CD and workstation

No CI (no `.gitlab-ci.yml`/`.github/` — verified absent across full history). Deployment = operator-run `ansible-playbook` from a workstation; canonical invocation in [`COMMANDS.md`](COMMANDS.md). Red/green discipline is fully on the operator running `make test` locally. Workstation: no hard version pins required; the reference set is the `tests/Dockerfile` pins (see [`COMMANDS.md`](COMMANDS.md)).

## Deep catalogs

Component-level depth lives in [`reference/components.md`](../../reference/components.md), [`reference/variables.md`](../../reference/variables.md) and [`reference/commands-reference.md`](../../reference/commands-reference.md) — do not duplicate it here.

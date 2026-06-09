# Commands Reference — Canonical Invocations

Complete command catalog for running, operating, and debugging the cluster. Copy-paste friendly; every command assumes working directory is repo root.

For the rationale behind these commands (why `--limit`, why both inventories, why `serial: 1`), see [`bootstrap-and-ha.md`](bootstrap-and-ha.md) and [`playbook-conventions.md`](playbook-conventions.md).

---

## 1. Canonical invocation pattern

```bash
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ \
  playbook-<system|app>/<name>.yaml [--limit <host>] [--tags <tag>]
```

**Rules (see `CLAUDE.md` §0 and [`playbook-conventions.md`](playbook-conventions.md) §3):**

- **Always** both inventory dirs: `-i hosts-vars/ -i hosts-vars-override/`
- `--limit` **required** for **node-scoped** system playbooks (enforced by `tasks-require-limit.yaml`); cluster-wide rolling-update plays (`apiserver-sans-update.yaml`, `etcd-key-rotate.yaml`, `haproxy-apiserver-lb-update.yaml`) run on all nodes via `serial: 1` and don't take `--limit`
- `--limit` **not required** for app playbooks (cluster-scoped, delegate to master manager)
- Use `--tags <phase>` to re-run a single phase (`pre`, `install`, `post`, etc.)

---

## 2. Bootstrap sequence (in order)

See [`bootstrap-and-ha.md`](bootstrap-and-ha.md) §1 for semantics. Run these in exactly this order for a fresh cluster.

### 2.1 Per-node preparation (all hosts)

```bash
# For each node (managers and workers):
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ \
  playbook-system/node-install.yaml --limit <host>
```

Can parallelize with comma-separated hosts: `--limit m1,m2,m3`.

### 2.2 First manager (cluster init)

```bash
# One-time, on the manager with is_master: true:
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ \
  playbook-system/cluster-init.yaml --limit <master>
```

### 2.3 Additional managers

```bash
# One at a time (quorum preservation):
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ \
  playbook-system/manager-join.yaml --limit <mgr2>

ansible-playbook -i hosts-vars/ -i hosts-vars-override/ \
  playbook-system/manager-join.yaml --limit <mgr3>
```

### 2.4 Workers

```bash
# Can be parallel:
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ \
  playbook-system/worker-join.yaml --limit <worker>
# Or multiple at once:
ansible-playbook ... --limit w1,w2,w3
```

### 2.5 Application stack (in dependency order)

```bash
for c in cilium cert-manager external-secrets vault traefik metrics-server longhorn \
         seaweedfs \
         mon-system \
         argocd gitlab gitlab-runner zitadel teleport haproxy; do
  ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/$c-install.yaml
done
```

**Dependency highlights** (see [`components.md`](components.md) §19 for full tier diagram):

- `cilium` first (CNI — nothing networks until it's up)
- `cert-manager` before anything with TLS
- `external-secrets` before anything with ESO
- `vault` before anything whose ESO pulls from it
- `traefik` before anything with ingress
- `zitadel` before `mon-system` (for Grafana OIDC inside mon-system stack)
- **Альтернатива** `longhorn` → `linstor` (Piraeus Operator + LINSTOR; ставится через `ansible-playbook ... playbook-app/linstor-install.yaml`). Только один из двух storage stack'ов в кластере, не оба параллельно. См. [`components.md`](components.md) §16.5.

---

## 3. App install re-runs (single-phase)

Each phase is idempotent and can be re-run independently:

```bash
# Full re-install (all phases):
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/<c>-install.yaml

# Single phase:
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/<c>-install.yaml --tags pre
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/<c>-install.yaml --tags install
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/<c>-install.yaml --tags post
```

**Extra phase tags (where applicable):**

- `--tags crds` — for `argocd`, `mon-system` (applies CRDs before main chart)
- `--tags prometheus-operator` — for `mon-system` (operator Deployment + RBAC + Service)
- `--tags prometheus` — for `mon-system` (Prometheus CR)
- `--tags alertmanager` — for `mon-system` (Alertmanager CR)
- `--tags node-exporter`, `--tags ksm`, `--tags loki`, `--tags vector`, `--tags grafana` — for `mon-system` (per-workload phases)
- `--tags cr` — for `vault` (Vault Custom Resource)
- `--tags configure` — for `teleport` (declarative resources)
- `--tags gitops` — for `argocd` (AppProjects + Applications)
- `--tags pre`, `--tags install-operator`, `--tags install-cluster`, `--tags post` — for `linstor` (LINSTOR / Piraeus install: pre/NetworkPolicy → Piraeus operator OCI chart → linstor-cluster OCI chart with CR'ы → post/ServiceMonitor + PodMonitor)

`tags: [always]` tasks (`tasks-pre-check`, `tasks-vault-config-verify`, `tasks-eso-verify`) run regardless of `--tags`.

---

## 4. Operational tasks

### 4.1 Node lifecycle

| Task | Command | See |
|---|---|---|
| Add new node | 1) update inventory; 2) run (4.1.a); 3) `node-install.yaml`; 4) `manager-join` or `worker-join` | [`bootstrap-and-ha.md`](bootstrap-and-ha.md) §1.5 |
| Drain for maintenance | `node-drain-on.yaml --limit <h>`, after maintenance `node-drain-off.yaml --limit <h>` | [`bootstrap-and-ha.md`](bootstrap-and-ha.md) §5 |
| Remove worker permanently | `node-drain-on.yaml` → `node-remove.yaml` → `server-clean.yaml` (all `--limit <h>`) | [`bootstrap-and-ha.md`](bootstrap-and-ha.md) §5 |
| Inspect cluster | `node-info.yaml` | read-only report |

**4.1.a — Cilium firewall refresh before node add:**

```bash
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ \
  playbook-app/cilium-install.yaml --tags post
```

Must run BEFORE joining the new node (see [`networking.md`](networking.md) §2).

### 4.2 Cluster-level rolling updates

| Task | Command |
|---|---|
| Rotate ETCD encryption key | `ansible-playbook ... playbook-system/etcd-key-rotate.yaml` |
| Add apiserver SAN (new manager IP/DNS) | `ansible-playbook ... playbook-system/apiserver-sans-update.yaml` |
| Update HAProxy LB backends (after manager change) | `ansible-playbook ... playbook-system/haproxy-apiserver-lb-update.yaml` |

All three use `serial: 1` internally to preserve quorum. Safe to run on a healthy cluster. See [`bootstrap-and-ha.md`](bootstrap-and-ha.md) §2–§4, §6.

### 4.3 Secret operations

| Task | Command | See |
|---|---|---|
| Rotate Vault root + unseal shares | `ansible-playbook ... playbook-app/vault-rotate.yaml` | [`secrets-and-eso.md`](secrets-and-eso.md) §8.1 |
| Force ESO re-sync (all / single) | `ansible-playbook ... playbook-app/eso-force-sync.yaml` | [`reusable-tasks.md`](reusable-tasks.md) §1.11 |
| Rotate `argocd` admin password | `ansible-playbook ... playbook-app/argocd-configure.yaml` | [`secrets-and-eso.md`](secrets-and-eso.md) §8.2 |
| Rotate `gitlab` creds | `ansible-playbook ... playbook-app/gitlab-configure.yaml` | same |

### 4.4 Component restart

```bash
# Rollout-restart wrappers for specific components:
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/argocd-restart.yaml
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/cilium-restart.yaml
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/external-secrets-restart.yaml
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/haproxy-restart.yaml
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/traefik-restart.yaml
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/linstor-restart.yaml
```

Each uses `kubectl rollout restart` on the target resources and waits for rollout to complete.

### 4.5 DR / sync helpers

| Task | Command |
|---|---|
| Create temporary S3 restore creds (Vault down) | `ansible-playbook ... playbook-app/longhorn-s3-restore-create.yaml` |
| Remove temporary S3 restore creds | `ansible-playbook ... playbook-app/longhorn-s3-restore-delete.yaml` |
| Sync Longhorn node tags from inventory | `ansible-playbook ... playbook-app/longhorn-tags-sync.yaml` |

### 4.6 Cluster diagnostics

Read-only dump of K8s state across selected (or all) namespaces. For each target namespace prints pods, certificates, network policies, deployments, statefulsets, ingresses, services, TCPs (HAProxy CRD), IngressRoutes (Traefik CRD), secrets, and `helm list`. Useful for triage and post-deploy verification.

```bash
# Dump all namespaces:
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/cluster-info.yaml

# Dump only specific namespaces:
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/cluster-info.yaml --tags vault,argocd
```

In this playbook `--tags` selects target *namespaces* by name, not Ansible task tags. Without `--tags` all cluster namespaces are dumped.

### 4.7 Network diagnostics

Measure real inter-node network bandwidth between exactly two cluster nodes via iperf3 (bidirectional, 30s per direction, 4 parallel streams). Pre/post-bootstrap; on a running cluster Cilium `CiliumClusterwideNetworkPolicy` already permits inter-node traffic on port 5201 — no firewall change needed.

```bash
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ \
  playbook-system/network-bandwidth-test.yaml \
  --limit <host_a>,<host_b>
```

Exactly **two distinct hosts** via `--limit` are required (the playbook asserts on count + uniqueness). Both must be in inventory (`managers:workers`) with `internal_ip` defined. Output is printed to stdout (no file).

After the test iperf3 server processes are killed on both hosts; the `iperf3` package stays installed.

### 4.8 Disk I/O diagnostics

Measure disk performance via `fio` (random write + random read, 8k blocks, iodepth=64, 2 minutes per direction) on 1...N selected nodes. Defaults configurable via `fio_read_*` and `fio_write_*` keys in `hosts-vars/linux-pkgs.yaml` (6 vars each: `_directory`, `_runtime`, `_size`, `_blocksize`, `_iodepth`, `_numjobs`). Used for DRBD/Longhorn replication rate capacity planning.

```bash
# All hosts in parallel (~4 min wall-clock independent of N):
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ \
  playbook-system/disk-io-test.yaml

# Subset via --limit:
ansible-playbook ... --limit k8s-manager-1,k8s-worker-2

# Force serial (avoid backend contention on shared storage):
ansible-playbook ... --forks 1

# Override test directory ad-hoc (both READ and WRITE; can override either independently):
ansible-playbook ... -e fio_read_directory=/data -e fio_write_directory=/data
```

Output: stdout, vertical per-host stanzas (IOPS, BW MiB/s, clat avg + p99) + cluster summary (min/max/avg per metric). Test files (`disk-io-test-randread.*`, `disk-io-test-randwrite.*`) cleaned via `always:` block regardless of failure; `fio` package stays installed.

### 4.9 SeaweedFS sync operations

Declarative sync invoked through `seaweedfs-install.yaml` tags (filer-driven IAM, v14→v20; см. [`components.md`](components.md) §17.5). No standalone `seaweedfs-sync.yaml` playbook — все sync logic в task includes под `playbook-app/tasks/seaweedfs/`. Порядок: policy-sync (Layer P) → user-sync (L1) → identity-distribute (L3) → bucket-sync (L2), все **после** helm install (`weed shell` требует running filer, live-reload).

```bash
# Full install (all phases including sync):
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/seaweedfs-install.yaml

# Re-sync managed policies only (Layer P — weed shell s3.policy, filer-driven diff):
ansible-playbook ... playbook-app/seaweedfs-install.yaml --tags policy-sync

# Re-sync identities only (Layer 1 — filer-driven, weed shell s3.configure live-reload, 6 phases):
ansible-playbook ... playbook-app/seaweedfs-install.yaml --tags user-sync

# Re-sync identity credentials distribution (Layer 3 — distribute creds per-key из identity.keys[].vault_paths):
ansible-playbook ... playbook-app/seaweedfs-install.yaml --tags identity-distribute

# Re-sync buckets + quotas + owner (Layer 2 — weed shell, filer-driven diff):
ansible-playbook ... playbook-app/seaweedfs-install.yaml --tags bucket-sync

# Re-sync everything (policy → identities → identity-distribute → buckets in order):
ansible-playbook ... playbook-app/seaweedfs-install.yaml --tags policy-sync,user-sync,identity-distribute,bucket-sync
```

**Quota enforcement** — нативный в SeaweedFS 4.31+: s3-gateway сам переключает bucket read-only в обе стороны (~раз в минуту, leader-locked на одной из реплик). Отдельного крона / ручного `s3.bucket.quota.enforce` не требуется — квоты задаёт `bucket-sync` (Phase D `s3.bucket.quota -op=set`), энфорсит gateway.

---

## 5. Debugging one-liners

Reach the cluster through the delegated manager without needing `kubectl` locally:

```bash
# Pod list cluster-wide:
ansible -i hosts-vars/ -i hosts-vars-override/ <master> -m command \
  -a 'kubectl get pods -A'

# Vault status:
ansible -i hosts-vars/ -i hosts-vars-override/ <master> -m command \
  -a 'kubectl -n vault exec vault-0 -- vault status'

# Force-sync a single ExternalSecret:
ansible -i hosts-vars/ -i hosts-vars-override/ <master> -m command \
  -a 'kubectl -n <ns> annotate externalsecret <name> force-sync=$(date +%s) --overwrite'

# Helm releases in a namespace:
ansible -i hosts-vars/ -i hosts-vars-override/ <master> -m command \
  -a 'helm list -n <namespace>'

# Check apiserver cert SANs:
ansible -i hosts-vars/ -i hosts-vars-override/ <master> -m command \
  -a 'openssl x509 -in /etc/kubernetes/pki/apiserver.crt -noout -text | grep -A1 "Subject Alternative Name"'

# ETCD member list:
ansible -i hosts-vars/ -i hosts-vars-override/ <master> -m command \
  -a 'kubectl -n kube-system exec etcd-<master-hostname> -- etcdctl --endpoints=https://127.0.0.1:2379 --cacert=/etc/kubernetes/pki/etcd/ca.crt --cert=/etc/kubernetes/pki/etcd/server.crt --key=/etc/kubernetes/pki/etcd/server.key member list'
```

---

## 6. Dry-run & syntax check

Before running any playbook against a real cluster:

```bash
# Syntax check (no connection):
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ \
  playbook-app/<c>-install.yaml --syntax-check

# List tags / hosts / tasks without running:
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ \
  playbook-app/<c>-install.yaml --list-tags
ansible-playbook ... --list-tasks
ansible-playbook ... --list-hosts

# Check mode (partial simulation — some modules don't support):
ansible-playbook ... --check --diff
```

---

## 7. Common pitfalls

See `CLAUDE.md` §0 (Hard Invariants) and [`playbook-conventions.md`](playbook-conventions.md) §17 (Anti-patterns) for the full list. Quick ones:

- **Forgetting `--limit` on a node-scoped system playbook** — fails the `tasks-require-limit.yaml` gate; cluster-wide rolling-update plays are exempt by design (see [`bootstrap-and-ha.md`](bootstrap-and-ha.md) §6)
- **Running with only one inventory** — defaults missing OR real hosts missing, both broken
- **Running `app/` with `--limit`** — harmless but pointless (app plays delegate to master regardless)
- **Parallel manager joins** — breaks ETCD quorum; always one at a time
- **Parallel HAProxy LB updates** — breaks every kubelet simultaneously; always `serial: 1` (playbook does this internally)

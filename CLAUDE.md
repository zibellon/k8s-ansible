# k8s-ansible

Production bare-metal Kubernetes cluster automation. Two halves:

- **`playbook-system/`** — imperative, node-scoped bootstrap and lifecycle operations (kubeadm, HAProxy LB as systemd, ETCD encryption, node join/drain/remove).
- **`playbook-app/`** — declarative cluster-scoped application installs via local Helm charts, every component deployed through a consistent 3-phase pattern (`pre` → `install` → `post`).

This file is the **map** of the project; detailed **catalogs** live under `.claude/rules/`. Read section 1 before editing anything.

---

## 0. Hard Invariants (do not violate)

Violating any of these will break the cluster or leak secrets.

- `argocd` and `longhorn-system` namespaces are **upstream-fixed** — never rename.
- `kube-proxy` is **disabled at `kubeadm init`** (`--skip-phases=addon/kube-proxy`). Cilium replaces it. Do not re-enable.
- `hosts-vars-override/` is **never committed**. It contains `ansible_password`, real IPs, Vault unseal keys, and all secrets.
- Always run Ansible with **both** inventories: `-i hosts-vars/ -i hosts-vars-override/`. Running with only one is always a bug.
- **System playbooks require `--limit`**. Forgetting `--limit` on `node-install.yaml` / `cluster-init.yaml` / `manager-join.yaml` / `worker-join.yaml` will fail a `tasks-require-limit.yaml` gate (by design).
- Exactly **one** manager in inventory must have `is_master: true`. That host becomes `master_manager_fact` — the single delegation target for every cluster-scope operation.
- Before adding a new node to the cluster, run `playbook-app/cilium-install.yaml --tags post` first — it refreshes the Cilium host firewall with the new node's IPs, otherwise the join handshake is blocked.

---

## 1. Mental Model in 50 Lines

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

- `playbook-system/` is **imperative and node-scoped**. Plays target specific hosts (`--limit <host>` is required by a gate). They install packages, write `/etc/…` files, start systemd units. Facts like `is_cluster_init` / `is_node_joined` come from `tasks-gather-cluster-facts.yaml`.
- `playbook-app/` is **declarative-ish and cluster-scoped**. Plays always `hosts: managers` + `gather_facts: false`; all `kubectl`/`helm` work delegates to one manager (`master_manager_fact`) with `run_once: true`.

### 1.3 Two inventory layers

- `hosts-vars/` — base defaults, committed to git. Templates, public values, schema.
- `hosts-vars-override/` — real inventory, secrets, cluster-specific overrides. **Never committed**.

Merge order: `hosts-vars/` → `hosts-vars-override/` → inline play vars. Arrays with the `*_extra` suffix are **concatenated** across layers, not replaced (see §7.2).

### 1.4 One delegation point

`master_manager_fact` is the hostname of the inventory host with `is_master: true`. Every `kubectl` / `helm` invocation in `playbook-app/` is `delegate_to: "{{ master_manager_fact }}" + run_once: true`. This means:

- Only that host needs a kubeconfig (`/root/.kube/config` is created by `tasks-kubectl-configure.yaml` at cluster-init).
- Charts are rsync-copied to that host's `{{ remote_charts_dir }}` (default `/opt/helm-charts/`), then Helm runs against the local filesystem.
- If the master manager goes down, `playbook-app/` operations stop working until you elect another `is_master: true`.

### 1.5 The "where am I?" stack

```
┌─────────────────────────────────────────────────────────────────┐
│  L7 Observability (mon, grafana, kube-state-metrics, node-exp)  │  playbook-app/mon-*
│  L6 Applications (argocd, gitlab, teleport, zitadel, medik8s)   │  playbook-app/
│  L5 Platform    (vault, external-secrets, cert-manager)         │  playbook-app/
│  L4 Storage     (longhorn)                                      │  playbook-app/
│  L3 Ingress     (traefik @ traefik-lb, haproxy @ haproxy-lb)    │  playbook-app/
│  L2 CNI         (cilium — replaces kube-proxy, host firewall)   │  playbook-app/
│  L1 Control pl. (kubeadm, ETCD+encryption, HAProxy apiserver LB)│  playbook-system/
│  L0 OS / node   (containerd, runc, kubelet, modules, systemd)   │  playbook-system/
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. Repository Anatomy

### 2.1 Top-level tree

```
k8s-ansible/
├── CLAUDE.md                  ← this file (map)
├── README.md, readme-*.md     ← human docs (not modified by Claude)
├── QWEN.md                    ← orthogonal LLM guidance (not touched)
├── todo.md                    ← user's TODO list
├── hosts-extra.example.yaml   ← template for extensible *_extra arrays
├── .claude/
│   └── rules/                 ← deep reference catalogs (atlas)
├── playbook-system/           ← node-scoped, imperative
│   └── tasks/
├── playbook-app/              ← cluster-scoped, declarative
│   ├── tasks/
│   └── charts/                ← 20 local Helm-chart dirs, one per component
├── hosts-vars/                ← base defaults (in git)
├── hosts-vars-override/       ← secrets + real inventory (gitignored)
├── docs/                      ← DO NOT TOUCH (user constraint)
└── sources/                   ← DO NOT TOUCH (user constraint)
```

### 2.2 `playbook-system/` — 21 playbooks

Grouped by role:

- **Node prep (called by `node-install.yaml` as sub-plays):** `setup-ssh-keys.yaml`, `set-hostname.yaml`, `server-prepare.yaml` (swap, kernel modules, sysctl, softdog), `longhorn-prepare.yaml` (iscsi_tcp, dm_crypt, cryptsetup), `cilium-prepare.yaml` (LLVM, clang, BPF mount), `main-components.yaml` (containerd/runc/CNI/kubeadm/kubelet/kubectl), `haproxy-apiserver-lb.yaml` (systemd LB), `install-helm.yaml`, `install-k9s.yaml`.
- **Bootstrap (run in this order):** `node-install.yaml` → `cluster-init.yaml` → `manager-join.yaml` → `worker-join.yaml`.
- **Operational:** `node-info.yaml` (read-only report), `node-drain-on.yaml`, `node-drain-off.yaml`, `node-remove.yaml`, `server-clean.yaml` (`kubeadm reset` + wipe).
- **Rolling updates:** `apiserver-sans-update.yaml` (regenerate apiserver cert with new SANs, `serial: 1`), `etcd-key-rotate.yaml` (8-step rekey with state-file resume), `haproxy-apiserver-lb-update.yaml` (refresh `/etc/haproxy/haproxy.cfg` on all nodes, `serial: 1`).

### 2.3 `playbook-system/tasks/` — 16 reusable includes

Key ones (full catalog in [`.claude/rules/reusable-tasks.md`](.claude/rules/reusable-tasks.md)):

- `tasks-gather-cluster-facts.yaml` — **the** fact aggregator. Produces `master_manager_fact`, `is_master_manager_exist`, `is_cluster_init`, `is_node_joined`, `joined_node_ips`, `joined_node_hostnames`. Internally calls `tasks-set-master-manager.yaml`, `tasks-set-is-cluster-init.yaml`, `tasks-set-is-node-joined.yaml`.
- `tasks-require-limit.yaml`, `tasks-require-manager.yaml`, `tasks-require-worker.yaml` — guard gates.
- `tasks-kubeadm-config-create.yaml` — renders `/etc/kubernetes/kubeadm-config.yaml` with dynamically built `certSANs`.
- `tasks-haproxy-lb-config-create.yaml` + `-restart.yaml` + `-health-wait.yaml` — HAProxy systemd LB lifecycle.
- `tasks-kubectl-configure.yaml` — sets up `/root/.kube/config`.
- `tasks-apply-node-labels.yaml`, `tasks-untaint-control-plane.yaml`.
- `tasks-kubelet-health-wait.yaml`, `task-apiserver-restart.yaml` (note the **singular** `task-` prefix — it restarts one apiserver by moving its static pod manifest out/in).

### 2.4 `playbook-app/` — 32 playbooks + 20 chart dirs

**Install playbooks (20)** — one per component, standard 3-phase shape:
`argocd`, `argocd-git-ops`, `cert-manager`, `cilium`, `external-secrets`, `gitlab`, `gitlab-runner`, `haproxy`, `longhorn`, `medik8s`, `metrics-server`, `mon-grafana`, `mon-kube-state-metrics`, `mon-node-exporter`, `mon-prometheus-operator`, `teleport`, `teleport-ssh-agent` (non-k8s, systemd), `traefik`, `vault`, `zitadel`.

**Non-install specials (12):**

- `-configure`: `argocd-configure.yaml`, `gitlab-configure.yaml` — reapply config without re-installing the chart.
- `-restart`: `argocd-restart.yaml`, `cilium-restart.yaml`, `external-secrets-restart.yaml`, `haproxy-restart.yaml`, `traefik-restart.yaml` — `kubectl rollout restart` wrappers with rollout wait.
- `-rotate`: `vault-rotate.yaml` — Vault rekey + root-token rotation.
- Sync/DR: `eso-force-sync.yaml`, `longhorn-s3-restore-create.yaml`, `longhorn-s3-restore-delete.yaml`, `longhorn-tags-sync.yaml`.

**Chart directories (20)** — each lives at `playbook-app/charts/<c>/` and contains one subdirectory per phase. Standard shape is `pre/`, `install/`, `post/`. Several components have extra phase dirs:

| Component | Extra phases | Why |
|---|---|---|
| `argocd` | `crds/` | Large CRDs applied via `kubectl create -f` before the main chart (avoids Helm CRD-hook timeouts). |
| `mon-prometheus-operator` | `crds/`, `prometheus/`, `alertmanager/` | Operator first, then Prometheus CR, then Alertmanager CR. |
| `vault` | `cr/` | bank-vaults operator first (in `install/`), then Vault Custom Resource in `cr/`. |
| `gitlab` | `postgresql/`, `redis/`, `minio/`, `gitlab/` | Stateful dependencies deployed as sibling releases before the main GitLab chart. |
| `zitadel` | `postgresql/` | Postgres sibling before Zitadel itself. |
| `teleport` | `configure/` | Declarative resources applied after the server is ready (users, roles, bots, apps, SSH servers, …). |
| `gitlab-runner`, `metrics-server` | no `post/` | Nothing to add after install (no ingress / ServiceMonitor). |
| `longhorn-s3-restore` | flat chart | Special DR helper, not a standard 3-phase component. |

### 2.5 `hosts-vars/` vs `hosts-vars-override/` vs `hosts-extra.example.yaml`

- `hosts-vars/` — 26 files. Inventory skeleton (`hosts.yaml`), Ansible/global settings (`ansible.yaml`, `k8s-base.yaml`, `kubeadm-config.yaml`), one file per component (`<c>.yaml`), cross-cutting files (`vault.yaml`, `vault-eso.yaml`, `vpn-rules.yaml`, `teleport-configure.yaml`).
- `hosts-vars-override/` — mirror structure for the specific environment. At minimum contains a real `hosts.yaml` with `ansible_host`, `ansible_user`, `ansible_password`, `internal_ip`, `is_master: true` on exactly one manager. Also: any `_extra` arrays, real domains, real cluster issuer names, real storage class names.
- `hosts-extra.example.yaml` — committed **template** documenting every `*_extra` extension point. Copy what you need into `hosts-vars-override/`.

---

## 3. Bootstrap Sequence

### 3.1 The four steps

```
1. playbook-system/node-install.yaml     --limit <host>     (run per node; orchestrates 9 sub-plays)
2. playbook-system/cluster-init.yaml     --limit <master>   (master_manager only, once per cluster)
3. playbook-system/manager-join.yaml     --limit <manager>  (per additional manager)
4. playbook-system/worker-join.yaml      --limit <worker>   (per worker)
```

Then in `playbook-app/`, install in dependency order (§11.2).

### 3.2 What each step creates

**`node-install.yaml`** runs these sub-plays, each idempotent, some with conditional reboot:
`setup-ssh-keys` → `set-hostname` → `server-prepare` → `longhorn-prepare` → `cilium-prepare` → `main-components` → `haproxy-apiserver-lb` → `install-helm` (managers only) → `install-k9s` (managers only).

After this the node has: containerd + runc + CNI plugins + kubelet + kubeadm + kubectl, required kernel modules and sysctls, `/sys/fs/bpf` mounted, HAProxy serving `127.0.0.1:16443 → <manager IPs>:6443`, Helm + k9s on managers.

**`cluster-init.yaml`** (master only, `--limit`):

1. Generates 32-byte ETCD encryption key → `/etc/kubernetes/pki/encryption-config.yaml` (key name `key<epoch>`, provider `aescbc` with `identity` fallback).
2. Renders `/etc/kubernetes/kubeadm-config.yaml` via `tasks-kubeadm-config-create.yaml` using the `kubeadm_config_template` from `hosts-vars/kubeadm-config.yaml`. `certSANs` built from every manager's `ansible_host` + `api_server_advertise_address` + `haproxy_apiserver_lb_host` + `localhost`.
3. Runs `kubeadm init --config … --skip-phases=addon/kube-proxy`.
4. Sets up `/root/.kube/config` (→ `tasks-kubectl-configure.yaml`), applies `node_labels`, optionally runs `tasks-untaint-control-plane.yaml`.

**`manager-join.yaml`** (one additional manager at a time):

1. `tasks-gather-cluster-facts.yaml` — confirm cluster initialized, discover `master_manager_fact`.
2. On master: `kubeadm init phase upload-certs --upload-certs` (cert-key valid ~2h).
3. **Distribute the ETCD encryption config**: slurp `/etc/kubernetes/pki/encryption-config.yaml` from master → write to joining manager (mode 0600). Also distributes `/etc/kubernetes/vault-unseal.json` if it exists (Vault integration).
4. On master: `kubeadm token create --print-join-command --certificate-key <key>`.
5. On joiner: run the join command + `--apiserver-advertise-address` + `--apiserver-bind-port`.
6. Post-join: kubelet health wait, kubectl config, node labels, wait until node visible in cluster API.

**`worker-join.yaml`** — same as manager-join but no cert-key, no encryption-config distribution.

### 3.3 Cilium host-firewall prerequisite

Cilium is installed with host firewall **on**. The host firewall policy (`CiliumClusterwideNetworkPolicy` in `cilium/post/`) is built from `nodeIps`, a list assembled from every inventory host's `ansible_host` + `internal_ip`.

Adding a node requires:

1. Add the host to `hosts-vars-override/hosts.yaml`.
2. Run **on the running cluster** `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/cilium-install.yaml --tags post` — this regenerates the host firewall policy so the new node's IP is in the allowlist.
3. Only now run `node-install.yaml` → `manager-join.yaml` / `worker-join.yaml` for the new host.

If you skip step 2, the kubelet handshake is blocked by Cilium and the join times out.

Alternative: bring up all nodes and join them **before** installing Cilium (no host firewall yet). Not recommended for production since the cluster is unprotected during window.

### 3.4 HAProxy apiserver LB

Runs as **systemd service** on every node (managers + workers), not as a pod. Reasons:

- `/etc/kubernetes/admin.conf` and kubelet need a stable `server:` endpoint **before** any pod exists.
- A pod-based LB would introduce a chicken-and-egg with apiserver availability.

Config (`/etc/haproxy/haproxy.cfg`) generated by `tasks-haproxy-lb-config-create.yaml`:

- Frontend: `bind {{ haproxy_apiserver_lb_host }}:{{ haproxy_apiserver_lb_port }}` (default `127.0.0.1:16443`), TCP mode.
- Healthz frontend: `*:{{ haproxy_apiserver_lb_healthz_port }}` (default `16444`) HTTP `/healthz`.
- Backend: `balance roundrobin` + `option tcp-check`, one `server` line per manager.
- On managers: backend list is `[127.0.0.1, <other managers>]`. On workers: `[<all managers>]`.

The `server:` endpoint in every kubeconfig and every kubelet points to `127.0.0.1:16443`, so HAProxy is the single point of truth for apiserver membership. Adding a manager requires `haproxy-apiserver-lb-update.yaml` (`serial: 1` for rolling, graceful `systemctl reload`).

Package version is pinned (`haproxy_apiserver_lb_package_version: "3.3"`) and held with `apt-mark hold`.

### 3.5 Dynamic certSANs

`kubeadm init` embeds a fixed set of SANs in the apiserver cert. When you add a manager, its IP/DNS must be added to the cert; otherwise `kubectl` via HAProxy to that manager fails TLS verification.

`apiserver-sans-update.yaml`:

1. `tasks-gather-cluster-facts.yaml` — find joined nodes only.
2. Regenerate `kubeadm-config.yaml` with the new SAN list.
3. Remove the old `apiserver.crt` / `apiserver.key`, run `kubeadm init phase certs apiserver`.
4. `task-apiserver-restart.yaml` (`serial: 1`) — move the apiserver static manifest out, wait for stop, move back, wait for `/healthz` + `/readyz`.
5. Verify SANs via `openssl x509 -text`.

### 3.6 `kubeadm init` specifics

From `hosts-vars/kubeadm-config.yaml` (rendered at cluster-init):

- `kubernetesVersion: v{{ k8s_full_version }}` — version pinned centrally.
- `controlPlaneEndpoint: "{{ haproxy_apiserver_lb_host }}:{{ haproxy_apiserver_lb_port }}"`.
- `apiServer.extraArgs.encryption-provider-config: "{{ etcd_encryption_config_path }}"` + mounted read-only.
- `apiServer.extraArgs.service-node-port-range: "{{ node_port_start }}-{{ node_port_end }}"` (default `1-50000`).
- `controllerManager.extraArgs.allocate-node-cidrs: "false"` — Cilium IPAM, not kube-controller-manager.
- `controllerManager.extraArgs.node-monitor-grace-period: "{{ node_monitor_grace_period }}"` (default `30s`, lower than upstream 40s).
- `networking.serviceSubnet: "10.128.0.0/12"`, `podSubnet: "10.64.0.0/10"`.
- `KubeletConfiguration` — cgroup `systemd`, eviction thresholds (soft + hard), image GC thresholds, `KubeletCrashLoopBackOffMax` feature gate on.
- `KubeProxyConfiguration.mode: ipvs` — informational only; `--skip-phases=addon/kube-proxy` means it's never installed. Cilium eBPF handles services.

### 3.7 Bootstrap flow (fact & state)

```
Master manager                 Manager 2..N                  Worker 1..M
─────────────                  ────────────                  ────────────
cluster-init:
  generate ETCD key ─┐
  write encryption-config
  kubeadm init       │
  /root/.kube/config │
  labels + untaint   │
                     │
                     ├──────► manager-join:
                     │         upload-certs on master
                     │         fetch encryption-config
                     │         (optional) vault-unseal.json
                     │         kubeadm join --control-plane
                     │         wait kubelet + node Ready
                     │
                     └────────────────────────────────► worker-join:
                                                         kubeadm join
                                                         wait kubelet + node Ready

After all nodes joined:
  haproxy-apiserver-lb-update.yaml         (serial: 1 refresh on every node)
  apiserver-sans-update.yaml    (if SANs changed; serial: 1 apiserver restart)
```

---

## 4. The 3-Phase Install Pattern

Every `<c>-install.yaml` in `playbook-app/` follows the same shape. Understanding the shape is sufficient to read any install playbook.

### 4.1 Why three phases

- **`pre`** must exist before the workload starts: `NetworkPolicy` that allows needed traffic, ESO resources (`ServiceAccount`, `SecretStore`, `ExternalSecret`) that materialize K8s `Secret`s the workload will consume.
- **`install`** is the workload itself: CRDs, the Deployment/StatefulSet/DaemonSet, operator.
- **`post`** layers on concerns that **reference** the running workload: `IngressRoute` (needs Service), `ServiceMonitor` (needs endpoint).

Each phase is a **separate Helm release**: `<c>-pre`, `<c>`, `<c>-post`. Benefits:

- Re-running one phase (`--tags pre|install|post`) only churns that release's resources.
- Helm history per phase is clean.
- A broken `post` never rolls back `install`.

### 4.2 Phase-contents table

| Phase | Release | Typical resources | Key task includes |
|---|---|---|---|
| `pre` | `<c>-pre` | `NetworkPolicy`, `ServiceAccount`, `SecretStore`, `ExternalSecret` | `tasks-pre-check`, `tasks-forbid-kube-system`, `tasks-eso-merge`, `tasks-resolve-acme-solver`, `tasks-copy-chart`, helm upgrade |
| `install` | `<c>` | CRDs (often in separate `crds/` phase), workload, operator, HPA, PDB | `tasks-copy-chart`, optional `tasks-add-helm-repo`, `tasks-wait-crds`, helm upgrade, `tasks-wait-rollout`, `tasks-verify-helm` |
| `post` | `<c>-post` | `IngressRoute`, `ServiceMonitor`, additional `ConfigMap`s, `Certificate`, `ClusterIssuer` | `tasks-copy-chart`, helm upgrade, optional `tasks-eso-force-sync` |

### 4.3 Optional extra phases

Seen in the wild; all use the same `tasks-copy-chart` + helm-upgrade pattern:

- **`crds/`** (argocd, mon-prometheus-operator) — applied with `kubectl create -f` or a dedicated Helm release before the main chart. Avoids Helm's CRD timeouts on large schemas.
- **`operator/` + `cr/`** (vault via bank-vaults) — install the operator, wait for CRDs, then apply the Custom Resource.
- **`prometheus/`, `alertmanager/`** (mon-prometheus-operator) — CRs applied after the operator.
- **`postgresql/`, `redis/`, `minio/`, `gitlab/`** (gitlab) — siblings deployed in the same play before the main workload. Each is its own Helm release.
- **`configure/`** (teleport) — declarative Teleport resources (roles, users, bots, apps, SSH servers, …) applied after the server is reachable.

### 4.4 Phase flow (what actually runs)

```
always:
  tasks-pre-check            → sets master_manager_fact, asserts cluster
  tasks-forbid-kube-system   → refuses <c>_namespace == kube-system
  tasks-eso-merge            → vault_policies_final / vault_roles_final / *_secrets_merged
  tasks-resolve-acme-solver  → (if ingress uses ACME) → <c>_acme_solver_pod_labels

--tags crds (if present):
  tasks-copy-chart (crds/)
  kubectl create -f / helm install <c>-crds
  tasks-wait-crds

--tags pre:
  tasks-copy-chart (pre/)
  render values-override.yaml (inline via `copy`)
  helm upgrade --install <c>-pre

--tags install:
  tasks-add-helm-repo        (only for external charts: cilium, traefik, jetstack, etc.)
  tasks-copy-chart (install/)
  render values-override.yaml
  helm upgrade --install <c>
  tasks-wait-rollout
  tasks-verify-helm

--tags post:
  tasks-copy-chart (post/)
  render values-override.yaml
  helm upgrade --install <c>-post
```

### 4.5 Naming invariants

- Release: `<c>-pre`, `<c>`, `<c>-post` (plus extras: `<c>-crds`, `<c>-operator`, …).
- Chart path on master: `{{ remote_charts_dir }}/<c>/<phase>/`.
- Values override file: `{{ remote_charts_dir }}/<c>/<phase>/values-override.yaml` — rendered by Ansible `copy` with `content: |` + Jinja2.
- Namespace: `{{ <c>_namespace }}`.
- Helm flags everywhere: `--cleanup-on-fail --atomic --wait --wait-for-jobs --timeout {{ <c>_helm_timeout }} --create-namespace` (except sibling-dependency releases that run before the main chart sometimes drop `--create-namespace`).

### 4.6 Re-running a single phase

All phases are idempotent. Re-run safely:

```bash
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/<c>-install.yaml --tags pre
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/<c>-install.yaml --tags install
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/<c>-install.yaml --tags post
```

`always`-tagged tasks (pre-check, eso-merge, acme-solver) run regardless of `--tags`, so facts are always up to date.

### 4.7 Non-install playbook patterns

- **`-configure`** (argocd-configure, gitlab-configure) — one-off play that resolves/rotates credentials via Vault (`tasks-vault-get` / `tasks-vault-put-and-sync`), then validates via the component's own API (`argocd` CLI, `gitlab-rails runner`).
- **`-restart`** — reads target resources, `kubectl rollout restart`, `tasks-wait-rollout` to confirm. No Helm operations.
- **`-rotate`** (`vault-rotate`) — component-specific state mutation (Vault rekey + root-token rotation, updates the K8s `Secret` used by the operator).
- **DR helpers** (`longhorn-s3-restore-create/delete`) — temporarily install a flat Helm chart that provides S3 credentials as K8s `Secret`s when Vault is unavailable.
- **Sync helpers** (`eso-force-sync`, `longhorn-tags-sync`) — poke ESO (annotation bump) or sync node tags to `nodes.longhorn.io` CRD.

---

## 5. Networking: Cilium & Host Firewall

### 5.1 Why Cilium replaces kube-proxy

At `kubeadm init` we pass `--skip-phases=addon/kube-proxy`. No `kube-proxy` DaemonSet is ever created. Cilium's eBPF datapath implements Service IP routing. Gains:

- One fewer DaemonSet, no iptables NAT overhead.
- Host firewall becomes available (not possible with kube-proxy).
- Transparent encryption, L7 policy, and Hubble observability all on the same data plane.

`cilium_helm_values.kubeProxyReplacement: true` in `hosts-vars/cilium.yaml`. Cilium needs `k8sServiceHost` + `k8sServicePort` set to the HAProxy LB (`127.0.0.1:16443`) because there is no kube-proxy to proxy the apiserver VIP.

### 5.2 Host firewall (`CiliumClusterwideNetworkPolicy`)

Defined in `playbook-app/charts/cilium/post/`. The policy's `nodeIps` array is built from every inventory host's `ansible_host` + `internal_ip`. Allows:

- Kubelet (10250), apiserver (6443 via HAProxy), etcd, Hubble (4244), etc.
- All `nodeIps` entities talking to all other `nodeIps`.

When adding a node: update `hosts-vars-override/hosts.yaml`, re-run `cilium-install.yaml --tags post` **before** joining the new node. See §3.3.

### 5.3 Traefik + VPN allowlist

`hosts-vars/vpn-rules.yaml` defines:

- `vpn_ips` — L3 CIDR list (used in `NetworkPolicy` and Traefik middleware).
- `vpn_traefik_middlewares` — L7 Traefik `Middleware` resources (`ipAllowList.sourceRange: {{ vpn_ips }}`).
- `vpn_ingress_middlewares` — string reference for standard K8s Ingress (`"<traefik-ns>-vpn-only@kubernetescrd"`).
- `vpn_ingress_route_middlewares` — list of middleware refs for Traefik `IngressRoute` CRD.

Enable per-component via `<c>_vpn_only_enabled: true` in its vars file. The component's `post/` chart then conditionally attaches the middleware to its ingress.

### 5.4 ACME HTTP-01 solver label resolution

Traefik HTTP-01 challenges create solver pods that must be matched by `NetworkPolicy` (pre phase allows challenge traffic). The solver's pod labels are defined on the `ClusterIssuer` (cert-manager), not globally.

`tasks-resolve-acme-solver.yaml` reads the `cert_manager_cluster_issuers` list, finds the entry by name (`{{ <c>_cluster_issuer_name }}`), picks the solver matching `{{ <c>_ingress_class_name }}`, and exports three dynamic facts:

- `{{ acme_cluster_issuer_result_var }}` — the resolved ClusterIssuer name.
- `{{ acme_solver_result_var }}` — the full solver dict.
- `{{ acme_pod_labels_result_var }}` — the `podLabels` to match in `NetworkPolicy`.

This means cert-manager config (list of issuers + solvers) is the single source of truth; downstream components derive labels from it instead of hardcoding.

---

## 6. Secrets Architecture: Vault + ESO

The most complex subsystem. Read `.claude/rules/secrets-and-eso.md` for depth.

### 6.1 Topology

```
              +-----------------------------+
              | bank-vaults operator (vault │
              | ns) — installs Vault CR     │
              +-----------------------------+
                           │
                           ▼
                   +---------------+          two KV v2 engines:
                   | Vault (single)|◄── Raft  • secret      (admin use)
                   |  Shamir 3/2   |          • eso-secret  (ESO read-only)
                   +---------------+
                           │ Kubernetes auth
                           │ (role → policy → path)
                           ▼
               +------------------------+
               | External Secrets (ESO) |    cluster-wide Deployment
               +------------------------+    in external-secrets ns
                           │
                           ▼  creates/refreshes K8s Secret objects
                    per-component ns
               ┌──────────────┬─────────────┬────────────┐
               ▼              ▼             ▼            ▼
           traefik-lb      gitlab         argocd      grafana, ...
           +Secret         +Secret        +Secret     +Secret
           (mounted/env)
```

- Vault is installed via `vault-install.yaml` using the bank-vaults operator. Three Vault unseal keys + root token live in `/etc/kubernetes/vault-unseal.json` on every manager (copied by `tasks-vault-distribute-creds.yaml` during `manager-join.yaml`).
- Two KV engines: `secret/` (human/admin use, `vault-admin` policy can write) and `eso-secret/` (ESO-consumable, per-component policies are read-only on their subtree).
- Nine components consume secrets from Vault via ESO (see §6.3).

### 6.2 The `tasks-eso-merge.yaml` contract

Called from every ESO-integrated component's install playbook with `tags: [always]`. Zero arguments.

**Reads from inventory:**

- `vault_policies` + `vault_policies_extra` — Vault ACL policies.
- `vault_roles` + `vault_roles_extra` — Vault Kubernetes auth roles (bind SA+namespace → policies).
- `eso_vault_integration_<c>` object — declares `sa_name`, `role_name`, `secret_store_name`, `kv_engine_path`, `is_need_eso` for each of the 9 components (`<c>` ∈ {`traefik`, `haproxy`, `longhorn`, `gitlab`, `gitlab_runner`, `zitadel`, `argocd`, `argocd_git_ops`, `grafana`}).
- `eso_vault_integration_<c>_secrets` + `eso_vault_integration_<c>_secrets_extra` — list of secrets per component.

**Produces runtime facts:**

- `vault_policies_final` = `vault_policies + vault_policies_extra`
- `vault_roles_final` = `vault_roles + vault_roles_extra`
- `eso_vault_integration_<c>_secrets_merged` for each of the 9 components (base + extra)

**Validates:**

- Unique policy names, unique role names within `_final`.
- Every role's policies exist in `vault_policies_final`.
- Each `secret_store_name`'s `role_name` exists in `vault_roles_final`.
- For `argocd` + `argocd_git_ops` (same namespace): no duplicate secret names across the two.

### 6.3 The nine ESO-integrated components

| Component | Namespace | Purpose of ESO-managed secrets |
|---|---|---|
| `traefik` | `traefik-lb` | `_extra` only (custom TLS, basic-auth) |
| `haproxy` | `haproxy-lb` | `_extra` only |
| `longhorn` | `longhorn-system` | S3 backup creds (in `_extra`) |
| `gitlab` | `gitlab` | PostgreSQL, Redis, MinIO root, MinIO registry, GitLab root password & PAT |
| `gitlab-runner` | `gitlab-runner` | Runner registration token, S3 cache creds |
| `zitadel` | `zitadel` | PostgreSQL password, `masterkey` |
| `argocd` | `argocd` | Admin password (root) |
| `argocd-git-ops` | `argocd` | Git repo credentials (SSH keys or tokens), pattern + direct refs |
| `grafana` | `grafana` | Admin password, datasource credentials (OIDC via Zitadel) |

### 6.4 The five Vault/ESO tasks

| Task include | Purpose | Typical caller |
|---|---|---|
| `tasks-vault-get.yaml` | Read one KV v2 field → named Ansible fact + `<fact>_exists` flag. | `-configure` playbooks (resolve current creds before using them) |
| `tasks-vault-put.yaml` | `vault kv put`, then annotate the target `ExternalSecret` to force sync, then wait for the downstream K8s `Secret` to appear. | secret-rotation flows |
| `tasks-generate-secret.yaml` | Generate random N-char secret → named fact. | bootstrap of first-run passwords |
| `tasks-eso-force-sync.yaml` | Annotate ExternalSecret(s) with `force-sync={{ now }}` to trigger ESO reconciliation (used after Vault put, or standalone via `eso-force-sync.yaml`). | every rotation; also used standalone |
| `tasks-vault-distribute-creds.yaml` | Reads `vault-unsealer-secret` from the cluster, decodes, writes `/etc/kubernetes/vault-unseal.json` on all managers. | `manager-join.yaml`, `vault-install.yaml` post |

### 6.5 Secret flow — seed vs. rotation

**First seed (example: GitLab root password at install):**

```
1. tasks-generate-secret.yaml       → gitlab_root_password fact
2. tasks-vault-put.yaml    → vault kv put eso-secret/gitlab/gitlab-root
                                    → annotate ExternalSecret "gitlab-root"
                                    → wait for Secret "gitlab-root" in gitlab ns
3. gitlab chart install             → pods mount/env the now-present Secret
```

**Rotation (example: rotating GitLab Postgres password):**

```
1. tasks-generate-secret.yaml       → new_pg_password fact
2. put on server in /tmp (optional)
3. ALTER USER in live postgres
4. tasks-vault-put.yaml    → vault kv put eso-secret/gitlab/postgresql
                                    → ESO rewrites K8s Secret gitlab-postgresql
5. (optional) Reloader restarts pods that mount the Secret
6. delete /tmp file
```

### 6.6 Adding a new ESO-integrated component

1. Add `eso_vault_integration_<c>` in `hosts-vars/vault-eso.yaml` (or a new file) with `sa_name`, `role_name`, `secret_store_name`, `kv_engine_path: "eso-secret"`, `is_need_eso: true`.
2. Define `eso_vault_integration_<c>_secrets` (base) and allow `_secrets_extra` in `hosts-extra.example.yaml`.
3. Add the role + policy to `vault_policies` / `vault_roles` in `hosts-vars/vault.yaml`.
4. In the component's `pre/` chart: render `ServiceAccount`, `SecretStore` (references `sa_name` + `role_name`), `ExternalSecret` objects from `eso_vault_integration_<c>_secrets_merged`.
5. In the component's install playbook, include `tasks-eso-merge.yaml` (`tags: [always]`) — no arguments required; it auto-handles the new component if added to the merge list.
6. Re-run `vault-install.yaml --tags install` so the new Vault policy/role is applied by the bank-vaults operator.

---

## 7. Variables & Inventory Merge

### 7.1 Per-component variable convention

Every component's `hosts-vars/<c>.yaml` follows a suffix pattern. Pattern recognition is more useful than an exhaustive list (full list in `.claude/rules/variables.md` + `components.md`).

| Suffix | Purpose |
|---|---|
| `_namespace` | Target K8s namespace. Some are upstream-fixed (`argocd`, `longhorn-system`). |
| `_chart_version` | Helm chart version (e.g., `traefik_chart_version`). |
| `_helm_values` | Full Helm values dict, often large, usually lives inline in vars file. |
| `_tolerations`, `_node_selector`, `_affinity`, `_resources` | Scheduling + resource controls; always rendered via `to_json` into values-override. |
| `_helm_timeout` | Helm `--timeout` (e.g., `5m`). |
| `_rollout_timeout`, `_daemonset_rollout_timeout` | `kubectl rollout status` timeout. |
| `_domain`, `_ui_domain`, `_rpc_domain` | Ingress domains. |
| `_https_secret_name`, `_cluster_issuer_name`, `_ingress_class_name` | TLS + cert-manager + Traefik wiring. |
| `_vpn_only_enabled` | If true, attach `vpn-only` Traefik middleware. |
| `_service_monitor_enabled`, `_service_monitor_interval`, `_service_monitor_scrape_timeout`, `_service_monitor_additional_labels` | Prometheus scrape config; applied in `post/`. |
| `_image_registry`, `_image_repository` | Air-gap override for the image registry host. |
| `_is_need_eso` | Whether the component uses ESO (in its `eso_vault_integration_<c>` object). |

### 7.2 The `*_extra` extensibility pattern

Array variables have two layers: a base defined in `hosts-vars/<file>.yaml` and an optional `*_extra` defined in `hosts-vars-override/` (or `hosts-extra.example.yaml` as template). At runtime the two are **concatenated**:

```yaml
vault_policies_final: "{{ vault_policies + (vault_policies_extra | default([])) }}"
```

Common extension points:

- `vault_policies_extra`, `vault_roles_extra`
- `eso_vault_integration_<c>_secrets_extra` — per component
- `argocd_cm_*_extra` — ConfigMap extensions (rbac, cmd-params, notifications, gpg-keys, ssh-known-hosts, tls-certs)
- `teleport_configure_<resource>_extra` — roles, users, bots, apps, databases, OIDC, SAML, access lists, trusted clusters, ...
- `longhorn_storage_classes` — not `_extra`-suffixed but empty by default; replaced in overrides.
- `cert_manager_cluster_issuers` — replaced in overrides.

Contract: `*_extra` always concat-merges; non-`_extra` arrays in overrides **replace** the base.

### 7.3 Inventory precedence

Ansible's standard precedence applies, with this project's convention:

1. `hosts-vars/` group/host vars (lowest)
2. `hosts-vars-override/` group/host vars (override)
3. Inline `vars:` in a play (highest)

Always invoke with **both** inventory dirs:

```bash
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ <playbook>
```

`hosts-vars/` alone gives you schema and defaults but no real hosts — bootstrap will fail at the first connection attempt. `hosts-vars-override/` alone is missing all the defaults — plays will fail on undefined vars.

### 7.4 Air-gap image registry

Every component with images has `<c>_image_registry` (and sometimes `<c>_image_registry_host`, `<c>_tools_image_registry`). Defaults point upstream (`docker.io`, `quay.io`, `ghcr.io`, `registry.k8s.io`). Override in `hosts-vars-override/` to point at a private registry.

Components with explicit listed image paths also document which image tags must be mirrored for air-gap (comments in the vars file).

---

## 8. Facts, Delegation, the Manager Node

### 8.1 `master_manager_fact`

Resolved by `tasks-set-master-manager.yaml`:

```
for host in groups['managers']:
  if hostvars[host].is_master is true:
    master_manager_fact = host
    break
```

Exactly one manager must set `is_master: true`. Output fact is consumed via `delegate_to: "{{ master_manager_fact }}"` throughout `playbook-app/` and the cluster-scope parts of `playbook-system/`.

### 8.2 `tasks-gather-cluster-facts.yaml` outputs

Called at the top of any play that needs cluster state. Produces on all hosts:

| Fact | Type | Source |
|---|---|---|
| `master_manager_fact` | string | `tasks-set-master-manager.yaml` |
| `is_master_manager_exist` | bool | ditto |
| `is_cluster_init` | bool | `/etc/kubernetes/admin.conf` exists on master + `kubectl get nodes` works |
| `is_node_joined` | bool | this host's `internal_ip` ∈ `joined_node_ips` |
| `joined_node_ips` | list[str] | `kubectl get nodes -o jsonpath={.items[*].status.addresses[?(@.type=="InternalIP")].address}` |
| `joined_node_hostnames` | list[str] | same but `.metadata.name` |

These facts never fail — missing data just sets bools to `false`. Idempotent-by-design.

### 8.3 Why `gather_facts: false`

Almost every play sets `gather_facts: false`. Reasons:

- **Speed** — `setup` gathers thousands of facts per host; we use maybe 5.
- **Predictability** — explicit fact tasks document dependencies.
- **Delegation** — `playbook-app/` plays don't care about worker OS details; only `master_manager_fact` matters.

Plays that need kernel details (`node-install.yaml` sub-plays) enable fact gathering explicitly inside their tasks.

### 8.4 Delegation pattern

Standard shape for any `playbook-app/` operation:

```yaml
- name: "[<c>-install] Install or upgrade via Helm"
  command: >
    helm upgrade --install <c> {{ remote_charts_dir }}/<c>/install
    --namespace {{ <c>_namespace }} --create-namespace
    --values {{ remote_charts_dir }}/<c>/install/values-override.yaml
    --cleanup-on-fail --atomic --wait --wait-for-jobs
    --timeout {{ <c>_helm_timeout }}
  delegate_to: "{{ master_manager_fact }}"
  run_once: true
  tags: [install]
```

Never omit `run_once: true` when delegating cluster-wide ops — otherwise Ansible invokes the command once per host in `hosts: managers`, duplicating the work.

---

## 9. Observability

### 9.1 Prometheus Operator

Namespace: `mon` (value of `prometheus_operator_namespace`). Installed by `playbook-app/mon-prometheus-operator-install.yaml` with extra phases:

```
crds/         CRDs first (Helm timeout on hook-crd avoidance)
pre/          NetworkPolicies + ESO (if any)
install/      the operator itself
prometheus/   Prometheus CR (retention, storage, selectors)
alertmanager/ Alertmanager CR
post/         ingress for Prometheus/Alertmanager UIs
```

Prometheus storage: PVC on `lh-major-single-best-effort` (Longhorn), retention default 60d. `ServiceMonitor` selector is cluster-wide — it discovers any `ServiceMonitor` in any namespace.

### 9.2 Per-component ServiceMonitor

`ServiceMonitor` lives in the component's `post/` phase chart so it is applied only after the workload's Service exists. Controlled by:

- `<c>_service_monitor_enabled: true` — gate.
- `<c>_service_monitor_interval`, `<c>_service_monitor_scrape_timeout` — scrape config.
- `<c>_service_monitor_additional_labels` (or `_labels` on some components) — for Prometheus operator selector matching.

Components that ship their own exporter(s) use the same pattern for kube-state-metrics and node-exporter.

### 9.3 Grafana + Alertmanager

Grafana in its own `grafana` namespace. ESO-integrated:

- Admin password → from Vault `eso-secret/grafana/admin`.
- OIDC via Zitadel → client-secret pulled via ESO.
- Datasource credentials pulled via ESO, rendered into provisioning configmap.

Alertmanager CR lives with the Prometheus Operator chart. Routing rules are defined declaratively in the `alertmanager/` chart values.

---

## 10. Rolling & HA Operations

### 10.1 `serial: 1` apiserver restart

Used by `etcd-key-rotate.yaml` and `apiserver-sans-update.yaml`. Pattern:

```yaml
- hosts: managers
  serial: 1
  gather_facts: false
  tasks:
    - include_tasks: tasks/task-apiserver-restart.yaml
```

`task-apiserver-restart.yaml` moves `/etc/kubernetes/manifests/kube-apiserver.yaml` to `/tmp/`, waits until `/healthz` stops responding, moves it back, waits for `/healthz` then `/readyz`. `serial: 1` ensures only one apiserver is down at a time — quorum preserved.

### 10.2 `etcd-key-rotate.yaml` state-file resume

Rotation is multi-step and partially destructive (it re-encrypts every `Secret` and `ConfigMap`). If interrupted, the cluster could be in a mixed-key state. State files solve this:

```
/etc/kubernetes/pki/etcd-rotation-state-step1.yaml   new key generated
/etc/kubernetes/pki/etcd-rotation-state-step2.yaml   new key is second (read-only)
/etc/kubernetes/pki/etcd-rotation-state-step4.yaml   new key is first (writes)
```

Format: YAML with `{new_key_name, new_key_secret, current_key_name, current_key_secret}`. Rerunning the playbook detects which state files exist and resumes from the matching step. All state files are cleaned up after successful completion.

Steps: generate → add new as 2nd → restart apiservers (serial: 1) → promote new to 1st → restart apiservers → re-encrypt all secrets+configmaps (`kubectl get … -o json | kubectl replace -f -`) → drop old key → final restart.

### 10.3 Node lifecycle

| Playbook | Purpose | Safety |
|---|---|---|
| `node-drain-on.yaml` | `kubectl cordon` + `kubectl drain --ignore-daemonsets --delete-emptydir-data --timeout={{ node_drain_timeout }}`. | Warns (non-fatal) if Longhorn still has replicas to evict. |
| `node-drain-off.yaml` | `kubectl uncordon`, wait for node `Ready`. | Always safe. |
| `node-remove.yaml` | `kubectl delete node`. | Refuses to delete `master_manager_fact`. |
| `server-clean.yaml` | `kubeadm reset --force` + wipe `/etc/cni/net.d`, `/etc/kubernetes`, `/var/lib/kubelet`, `/var/lib/etcd`, `/root/.kube`. | Destructive — requires `--limit`. |

---

## 11. Command Reference

### 11.1 Canonical invocation

```bash
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ \
  playbook-<system|app>/<name>.yaml [--limit <host>] [--tags <tag>]
```

Always both inventories. `--limit` required for system playbooks (enforced by `tasks-require-limit.yaml`).

### 11.2 Bootstrap (in this order)

```bash
# For each node:
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/node-install.yaml --limit <host>

# First manager (initializes cluster):
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/cluster-init.yaml --limit <master>

# Additional managers, one at a time:
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/manager-join.yaml --limit <mgr2>

# Workers (can be parallel by specifying multiple with --limit m1,m2):
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/worker-join.yaml --limit <worker>

# Apps — install in this order:
for c in cilium cert-manager external-secrets vault traefik metrics-server longhorn \
         mon-prometheus-operator mon-grafana mon-kube-state-metrics mon-node-exporter \
         argocd gitlab gitlab-runner zitadel teleport medik8s haproxy; do
  ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/$c-install.yaml
done
```

Dependency highlights: `cilium` first (CNI), `cert-manager` before anything with TLS, `external-secrets` before anything with ESO, `vault` before anything whose ESO pulls from it, `traefik` before anything with ingress.

### 11.3 App install re-runs

```bash
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/<c>-install.yaml              # full
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/<c>-install.yaml --tags pre
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/<c>-install.yaml --tags install
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/<c>-install.yaml --tags post
```

### 11.4 Operational

| Task | Command |
|---|---|
| Adding a new node | 1) update inventory; 2) `cilium-install.yaml --tags post`; 3) `node-install.yaml --limit <h>`; 4) `manager-join` or `worker-join` |
| Rotate ETCD key | `playbook-system/etcd-key-rotate.yaml` |
| Add apiserver SAN | edit `certSANs` → `playbook-system/apiserver-sans-update.yaml` |
| Update HAProxy LB after manager change | `playbook-system/haproxy-apiserver-lb-update.yaml` |
| Drain for maintenance | `playbook-system/node-drain-on.yaml --limit <h>` ; then `node-drain-off.yaml` |
| Remove worker permanently | `playbook-system/node-drain-on.yaml` → `playbook-system/node-remove.yaml --limit <h>` → `playbook-system/server-clean.yaml --limit <h>` |
| Force ESO re-sync | `playbook-app/eso-force-sync.yaml` |
| Rotate Vault root / unseal | `playbook-app/vault-rotate.yaml` |
| Inspect cluster | `playbook-system/node-info.yaml` |

### 11.5 Debugging one-liners

```bash
# Via delegated manager (don't need local kubectl):
ansible -i hosts-vars/ -i hosts-vars-override/ <master> -m command -a 'kubectl get pods -A'

# Vault status:
ansible -i hosts-vars/ -i hosts-vars-override/ <master> -m command \
  -a 'kubectl -n vault exec vault-0 -- vault status'

# ESO force-sync a single ExternalSecret:
ansible -i hosts-vars/ -i hosts-vars-override/ <master> -m command \
  -a 'kubectl -n <ns> annotate externalsecret <name> force-sync=$(date +%s) --overwrite'
```

---

## 12. Conventions, Gotchas, Anti-patterns

### 12.1 Naming recap

- Playbook files: `<component>-<action>.yaml` — actions: `install`, `configure`, `restart`, `rotate`, `sync`, `force-sync`, `tags-sync`.
- Helm release names: `<c>-pre`, `<c>`, `<c>-post`, plus `<c>-crds`, `<c>-operator`, etc.
- Task includes: `tasks-<verb>-<object>.yaml` (singular `task-` only for `task-apiserver-restart.yaml`).
- Task names (human): `[<c>-<action>-<phase>] <description>`.

### 12.2 Gotchas

- **Missing `--limit`** — system playbooks fail at `tasks-require-limit.yaml`. If it "works" without `--limit`, you are not running a system playbook.
- **Missing override inventory** — plays fail on undefined vars, or worse, use `hosts-vars/hosts.yaml` skeleton (no real hosts).
- **Editing a chart template without bumping the Helm release** — the next `--tags <phase>` run may not detect the change if templates differ but `values-override.yaml` is identical. Use `--atomic` + `--wait` (already standard) + test with a release bump if unsure.
- **Touching `argocd` / `longhorn-system` namespaces** — upstream-hardcoded in ClusterRoleBindings. Cluster breaks silently.
- **Adding a node without pre-updating Cilium host firewall** — join handshake hangs. Run `cilium-install.yaml --tags post` first.
- **Deleting ETCD rotation state files mid-rotation** — loses the ability to resume safely. If interrupted, **always** let the next run pick up via state file; never `rm` them manually.
- **Forgetting `run_once: true` on delegated tasks** — helm/kubectl runs N times (once per manager in `hosts: managers`), producing confusing Helm history.
- **`tasks-pre-check.yaml` + `tasks-eso-merge.yaml` are `tags: [always]`** — they run regardless of `--tags`. If you add new tasks that derive facts, tag them `[always]` or they will silently skip in single-phase runs.

### 12.3 Anti-patterns

- Inline `kubectl apply -f ...` in a playbook instead of a Helm chart. Use charts; they integrate with `--tags`, `--atomic`, and release history.
- Hard-coded pod labels for ACME solver `NetworkPolicy`. Always resolve via `tasks-resolve-acme-solver.yaml`.
- Bypassing `tasks-eso-merge.yaml` (e.g., manually writing `secrets:` in a values file). You'll miss `_extra` merges and validation.
- Putting secrets in `hosts-vars/`. Always `hosts-vars-override/`, never committed.
- Adding `gather_facts: true` to `playbook-app/` plays. Use `tasks-gather-cluster-facts.yaml` instead — deterministic and cached.

---

## 13. Where to Go Next

### 13.1 Deep reference — `.claude/rules/`

| File | Owns |
|---|---|
| [`components.md`](.claude/rules/components.md) | Per-component reference (one section per component): chart path, namespace, releases, required vars, ESO integration, dependencies, ServiceMonitor, image-registry overrides. |
| [`playbook-conventions.md`](.claude/rules/playbook-conventions.md) | Imperative authoring rules for new playbooks (crisp numbered rules; no prose). |
| [`reusable-tasks.md`](.claude/rules/reusable-tasks.md) | Full catalog of task includes (system + app), with inputs/outputs/callers. |
| [`variables.md`](.claude/rules/variables.md) | Variable patterns (Tier 1) + global/cross-cutting catalog (Tier 2). Per-component vars are in `components.md`. |
| [`secrets-and-eso.md`](.claude/rules/secrets-and-eso.md) | Vault + ESO deep dive: merge contract, `SecretStore`/`ExternalSecret` template, rotation procedure, adding a new ESO component. |
| [`bootstrap-and-ha.md`](.claude/rules/bootstrap-and-ha.md) | Cluster-lifecycle ops: bootstrap prereqs, ETCD rekey state-file format, SAN update sequencing, HAProxy LB update triggers, node drain/remove safety. |

### 13.2 Human-facing docs (not modified by Claude)

- `README.md` — quickstart in Russian.
- `readme-vault.md`, `readme-monitoring.md`, `readme-helpers.md` — topic deep-dives in Russian.
- `todo.md` — user's TODO list (includes planned Reloader install, Zitadel hardening, backup/rotation improvements).

### 13.3 Orthogonal files

- `QWEN.md` — guidance for a different LLM assistant. Do not modify.
- `docs/`, `sources/` — explicitly out of scope by user instruction.

---

## 14. Glossary

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

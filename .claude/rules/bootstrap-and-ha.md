# Bootstrap & HA Operations — Deep Reference

Lifecycle procedures for a bare-metal cluster: bootstrapping from scratch, joining nodes, rotating sensitive material, and rolling updates that require quorum-preserving sequencing.

For the high-level mental model, see `CLAUDE.md` §3 and §10. For the individual task includes used below, see [`reusable-tasks.md`](reusable-tasks.md) §2.

---

## 1. Four-step Bootstrap

### 1.1 Overview

```
step 1   node-install.yaml       per node (managers + workers)   --limit <host>
step 2   cluster-init.yaml       on the first manager            --limit <master>   (once)
step 3   manager-join.yaml       each additional manager         --limit <mgrN>
step 4   worker-join.yaml        each worker                     --limit <workerN>
```

All four require `--limit` (enforced by `tasks-require-limit.yaml`). Run both inventories every time: `-i hosts-vars/ -i hosts-vars-override/`.

### 1.2 `node-install.yaml` (step 1)

**Role.** Prepare a host for kubeadm join. Orchestrates nine sub-plays, each idempotent.

| Sub-play | What it does |
|---|---|
| `setup-ssh-keys.yaml` | Ensures `authorized_keys` contains the operator's pubkey. |
| `set-hostname.yaml` | Persists `hostname` + `/etc/hosts` entry. |
| `server-prepare.yaml` | Disable swap, load kernel modules (`br_netfilter`, `overlay`, `softdog`), sysctls (`net.ipv4.ip_forward`, `net.bridge.bridge-nf-call-*`, IPv6 counterparts), time sync. Blacklisted modules re-enabled via unit-service wrapping `modprobe` (survives reboots despite blacklists). |
| `longhorn-prepare.yaml` | Packages (`open-iscsi`, `nfs-common`, `cryptsetup`, `dmsetup`), modules (`iscsi_tcp`, `dm_crypt`). |
| `cilium-prepare.yaml` | LLVM + clang + libbpf prerequisites, mount `/sys/fs/bpf`. |
| `main-components.yaml` | Install pinned versions of `containerd`, `runc`, CNI plugins, `kubeadm`, `kubelet`, `kubectl` (apt-mark hold to prevent dist-upgrade drift). |
| `haproxy-apiserver-lb.yaml` | Install HAProxy (apt, pinned version `haproxy_apiserver_lb_package_version`, held), render `/etc/haproxy/haproxy.cfg`, enable+start service. |
| `install-helm.yaml` | Managers only — install the `helm` binary. |
| `install-k9s.yaml` | Managers only — install `k9s`. |

**Pre-conditions.**

- SSH-reachable host with sudo.
- Entry for the host in `hosts-vars-override/hosts.yaml` under the correct group (`managers` or `workers`).
- `ansible_host`, `internal_ip`, `ansible_user`, `ansible_password` (or key) set in overrides.
- If this is a new node being added to a running cluster, the cluster's Cilium host-firewall policy must **already** include this node's IPs. See §1.5.

**Post-condition.** Host has containerd running, HAProxy serving `127.0.0.1:16443 → <all managers>:6443`, all kernel prerequisites in place.

**Idempotency.** All sub-plays are idempotent. Some trigger a reboot if kernel modules or grub cmdline changed; the play waits for the host to come back.

### 1.3 `cluster-init.yaml` (step 2)

**Role.** Initialize the first control plane on the `is_master: true` host.

**Sequence.**

1. `tasks-require-limit` + `tasks-require-manager`.
2. `tasks-gather-cluster-facts` — reports `is_cluster_init: false`.
3. Generate 32-byte ETCD encryption key → write `/etc/kubernetes/pki/encryption-config.yaml`. Key name `key{{ lookup('pipe','date +%s') }}`. Provider `aescbc` with `identity` fallback (read-compat).
4. `tasks-kubeadm-config-create` — render `/etc/kubernetes/kubeadm-config.yaml` from `kubeadm_config_template`. `certSANs` = every manager's `ansible_host` + `api_server_advertise_address` + `haproxy_apiserver_lb_host` + `localhost`.
5. `kubeadm init --config /etc/kubernetes/kubeadm-config.yaml --skip-phases=addon/kube-proxy`. Note: kube-proxy never installed — Cilium will replace it.
6. `tasks-kubectl-configure` — set up `/root/.kube/config`.
7. `tasks-apply-node-labels` — apply per-host `node_labels`.
8. (optional) `tasks-untaint-control-plane` — remove `node-role.kubernetes.io/control-plane:NoSchedule` if the cluster is small / dev.

**Pre-conditions.**

- `node-install.yaml` completed on this host.
- Exactly one manager has `is_master: true`.
- HAProxy on this host is listening on `127.0.0.1:16443` even with zero backends alive — kubelet uses `127.0.0.1:16443` as its bootstrap apiserver endpoint via kubeadm's `controlPlaneEndpoint`.

**Post-condition.**

- Cluster reachable at `127.0.0.1:16443` via HAProxy.
- `/etc/kubernetes/pki/encryption-config.yaml` present on master (will be distributed to additional managers at join).
- Node `Ready` (or waiting for CNI — Cilium will make it Ready after `cilium-install.yaml`).

**Rollback.** `server-clean.yaml --limit <master>` wipes the master back to a pre-init state (destructive).

### 1.4 `manager-join.yaml` (step 3)

**Role.** Add one additional manager at a time.

**Sequence.**

1. `tasks-require-limit` + `tasks-require-manager`.
2. `tasks-gather-cluster-facts` — reports `is_cluster_init: true`, identifies `master_manager_fact`.
3. On master: `kubeadm init phase upload-certs --upload-certs` — prints a cert key valid ~2h.
4. **Distribute ETCD encryption config**: slurp `/etc/kubernetes/pki/encryption-config.yaml` from master → write on joiner, mode 0600.
5. **Distribute Vault unseal creds (if Vault installed)**: `tasks-vault-distribute-creds.yaml` writes `/etc/kubernetes/vault-unseal.json`.
6. On master: `kubeadm token create --print-join-command --certificate-key <key>`.
7. On joiner: run the join command **plus** `--apiserver-advertise-address` + `--apiserver-bind-port` from the joiner's host vars.
8. `tasks-kubelet-health-wait`.
9. `tasks-kubectl-configure`, `tasks-apply-node-labels`.
10. Wait until this node appears in `kubectl get nodes`.

**Pre-conditions.**

- `node-install.yaml` completed on the joiner.
- Cilium host firewall already includes the joiner's IPs (§1.5).
- HAProxy on the joiner is serving `127.0.0.1:16443 → <all existing managers>:6443`.

**Post-conditions.**

- Joiner registered as a control-plane node.
- ETCD encryption shared (key identical on all managers).
- HAProxy on every other node needs to be updated afterwards so its backend list includes the new manager: run `haproxy-apiserver-lb-update.yaml`.
- If this new manager should appear in apiserver certSANs: run `apiserver-sans-update.yaml`.

**Rollback.** `node-drain-on.yaml` → `node-remove.yaml` → `server-clean.yaml`, all with `--limit <joiner>`.

### 1.5 Cilium host-firewall prerequisite (critical)

Cilium runs with host firewall on. The policy `CiliumClusterwideNetworkPolicy` (in `charts/cilium/post/`) builds `nodeIps` from every inventory host's `ansible_host` + `internal_ip`. Traffic from IPs outside that list is dropped at the host interface.

**Consequence.** Joining a new node requires the Cilium policy to already include that node's IPs, otherwise the kubelet ↔ apiserver handshake is blocked.

**Correct order.**

```
1. Edit hosts-vars-override/hosts.yaml to add the new host (ansible_host + internal_ip).
2. ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/cilium-install.yaml --tags post
   (This re-renders the cluster-wide policy with the new IPs.)
3. ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/node-install.yaml --limit <new-host>
4. manager-join.yaml or worker-join.yaml for the new host.
```

Skipping step 2 causes join timeouts that appear as "TLS handshake timeout" in kubelet logs — easy to misdiagnose as a network or cert issue.

**Alternative (not recommended for prod).** Pre-stage all nodes before installing Cilium. Because the host firewall policy doesn't exist yet, joins work. But the cluster is unprotected for the whole window.

### 1.6 `worker-join.yaml` (step 4)

**Role.** Add a worker node.

**Sequence.**

1. `tasks-require-limit` + `tasks-require-worker`.
2. `tasks-gather-cluster-facts`.
3. On master: `kubeadm token create --print-join-command` (no cert-key — workers don't get control-plane certs).
4. On joiner: run the join command.
5. `tasks-kubelet-health-wait`, `tasks-apply-node-labels`.

**Pre-condition.** Same Cilium host-firewall requirement as managers (§1.5).

---

## 2. Apiserver SANs Update

**When.**

- You added a manager (its `ansible_host` must be in the apiserver cert SANs, otherwise HAProxy backend TLS verification fails).
- You added a DNS name that clients use to reach the apiserver.

**Playbook.** `playbook-system/apiserver-sans-update.yaml`.

**Sequence (per manager, `serial: 1`).**

1. `tasks-gather-cluster-facts` — confirm cluster init, skip un-joined hosts.
2. `tasks-kubeadm-config-create` — regenerate `/etc/kubernetes/kubeadm-config.yaml` with the new SAN list. `certSANs` built from current inventory (all joined managers' `ansible_host` + `api_server_advertise_address` + `haproxy_apiserver_lb_host` + `localhost`).
3. Remove `/etc/kubernetes/pki/apiserver.{crt,key}`.
4. `kubeadm init phase certs apiserver` — generates a fresh apiserver cert with the new SANs.
5. `task-apiserver-restart.yaml` — move `/etc/kubernetes/manifests/kube-apiserver.yaml` out → wait for `/healthz` to stop → move back → wait for `/healthz` then `/readyz`.
6. Verify with `openssl x509 -in /etc/kubernetes/pki/apiserver.crt -noout -text` — confirm SAN list.

**Why `serial: 1`.** Only one apiserver offline at a time — the other N-1 maintain quorum.

**Recovery on failure.** If `task-apiserver-restart` leaves the manifest in `/tmp`, the play stops and alerts. Manually move it back to `/etc/kubernetes/manifests/` to restore the apiserver on that host.

---

## 3. ETCD Key Rotation — State-file Resume

**When.** Regular rotation (recommended: quarterly); after a key is suspected leaked; before a security audit.

**Playbook.** `playbook-system/etcd-key-rotate.yaml`.

**Complexity.** The rotation is multi-step and partially destructive — it re-encrypts every `Secret` and `ConfigMap` in the cluster. If interrupted, `encryption-config.yaml` can be in a mixed-key state where the first-listed provider differs across managers.

### 3.1 State files

Written under `{{ etcd_rotation_state_dir }}` (default `/etc/kubernetes/pki`).

| File | Meaning |
|---|---|
| `etcd-rotation-state-step1.yaml` | New key generated, not yet installed anywhere. |
| `etcd-rotation-state-step2.yaml` | New key installed as the **second** provider (read-only) on all managers. |
| `etcd-rotation-state-step4.yaml` | New key promoted to **first** provider (writes go to new key). |

Content (YAML):

```yaml
new_key_name: "key<epoch-new>"
new_key_secret: "<base64-32-bytes>"
current_key_name: "key<epoch-prev>"
current_key_secret: "<base64-32-bytes>"
```

State files are removed at step 8 after successful completion.

### 3.2 The 8 steps

| Step | Action | State file after |
|---|---|---|
| 1 | Generate new key. Write state-step1. | step1 |
| 2 | Update `encryption-config.yaml` on all managers: `[current, new]` (current still first = still used for writes; new only for reads). Distribute + restart apiservers (`serial: 1`). | step2 |
| 3 | (no state) Verify every apiserver is healthy with the new config. | step2 |
| 4 | Promote new to first: `encryption-config.yaml` becomes `[new, current]`. Distribute + restart apiservers (`serial: 1`). Now writes go to the new key. | step4 |
| 5 | Re-encrypt all Secrets + ConfigMaps: `kubectl get <kind> -A -o json | kubectl replace -f -`. This triggers an etcd write with the now-primary new key. | step4 |
| 6 | Update `encryption-config.yaml` to contain only the new key + the identity fallback. Distribute + restart apiservers (`serial: 1`). | step4 |
| 7 | Verify every Secret+ConfigMap is readable (end-to-end). | step4 |
| 8 | Clean up: rm all state files. | — |

### 3.3 Resume

On re-run, the playbook inspects the state-file directory:

- If `step4.yaml` present → resume at step 5.
- Else if `step2.yaml` present → resume at step 3 (verification).
- Else if `step1.yaml` present → resume at step 2.
- Else → start from scratch (step 1).

**Never delete state files manually.** If you do, you have no way to know which `encryption-config.yaml` generation is currently live across managers.

---

## 4. HAProxy Apiserver LB Update

**When.**

- Added or removed a manager (backend list changed).
- Changed `haproxy_apiserver_lb_*` ports or bind host.
- Upgraded HAProxy package version.

**Playbook.** `playbook-system/haproxy-apiserver-lb-update.yaml`.

**Sequence (per node, `serial: 1`, targets both managers and workers).**

1. `tasks-haproxy-lb-config-create` — regenerate `/etc/haproxy/haproxy.cfg` with current manager list.
2. `systemctl reload haproxy` (graceful — in-flight connections drain; or `restart` when the pinned version changed).
3. `tasks-haproxy-lb-health-wait` — confirm port `16443` listening + `/healthz` on `16444`.

**Why `serial: 1`.** All kubelets on all nodes talk to `127.0.0.1:16443`. Rolling one HAProxy at a time ensures each node still has a working local LB (and thus apiserver connectivity) while the next is reloading.

---

## 5. Node Lifecycle Operations

### 5.1 `node-drain-on.yaml`

- `kubectl cordon <host>` — mark unschedulable.
- `kubectl drain <host> --ignore-daemonsets --delete-emptydir-data --timeout={{ node_drain_timeout }}`.
- Longhorn check: warn (non-fatal) if any `Volume` still has replicas on this node — eviction must be triggered separately via Longhorn UI/CRD.

**Safety gate.** Does NOT refuse to drain the master manager, but a drain on the only manager will break cluster-scope operations. Always have ≥2 managers before draining any of them.

### 5.2 `node-drain-off.yaml`

- `kubectl uncordon <host>`.
- Wait for node `Ready`.

Always safe.

### 5.3 `node-remove.yaml`

- `kubectl delete node <host>`.

**Safety gate.** Refuses to delete `master_manager_fact`. If you need to remove the current master, first rotate `is_master: true` to another manager in inventory, redeploy the `-install` bits that read that fact, then remove the old master.

After `node-remove`:

- `haproxy-apiserver-lb-update.yaml` — so other nodes stop dialing the removed manager.
- `apiserver-sans-update.yaml` — so the removed manager's IP no longer appears in apiserver certSANs (optional but hygiene).

### 5.4 `server-clean.yaml`

**Destructive.** Brings a host back to its pre-`node-install` state.

- `kubeadm reset --force`.
- `rm -rf /etc/cni/net.d/* /etc/kubernetes/* /var/lib/kubelet/* /var/lib/etcd/* /root/.kube/*`.
- Does NOT uninstall containerd / HAProxy / kubelet (package-level). Re-running `node-install.yaml` after `server-clean` is cheap.

**Requires `--limit`.** Running on all nodes by accident is catastrophic.

---

## 6. The `serial: 1` Pattern

Used in three places:

| Playbook | What's rolled | Why |
|---|---|---|
| `apiserver-sans-update.yaml` | apiserver static-pod manifest | Quorum: 1 apiserver down at a time |
| `etcd-key-rotate.yaml` (steps 2, 4, 6) | apiserver (via `task-apiserver-restart`) | Quorum + consistent `encryption-config.yaml` transition |
| `haproxy-apiserver-lb-update.yaml` | HAProxy systemd service | Every kubelet depends on its local HAProxy; one node offline at a time |

All three rely on `tasks-gather-cluster-facts.yaml` at the top to skip un-joined hosts.

---

## 7. Recovery Matrix (quick reference)

| Situation | Recovery |
|---|---|
| Master manager unreachable | Elect a new `is_master: true` in inventory, rerun any app playbook — `master_manager_fact` resolves to the new host. |
| Vault sealed on all managers | On any manager: `kubectl -n vault exec vault-0 -- vault operator unseal <key>` three times (or `vault_key_threshold` times) using shares from `/etc/kubernetes/vault-unseal.json`. |
| ETCD rotation interrupted | Re-run `etcd-key-rotate.yaml`; state files make it resume-safe. Never `rm` state files manually. |
| Apiserver SANs wrong after manager add | `apiserver-sans-update.yaml`. |
| HAProxy backend list stale after manager change | `haproxy-apiserver-lb-update.yaml`. |
| New node join hangs at TLS handshake | Cilium host firewall missing the new IP — run `cilium-install.yaml --tags post` with the updated inventory, then retry join. |
| `/etc/kubernetes/pki/encryption-config.yaml` missing on a manager | Re-run `manager-join.yaml` for that manager (distributes from the master); or manually `scp` from master (mode 0600). |
| `/etc/kubernetes/vault-unseal.json` missing on a manager | Re-run `tasks-vault-distribute-creds.yaml` (standalone by running `vault-install.yaml --tags post` or via `manager-join.yaml`). |

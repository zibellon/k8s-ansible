# Networking — Cilium, Host Firewall, VPN, ACME

Depth reference for the networking layer: Cilium as CNI + kube-proxy replacement, host firewall policy, VPN allowlist middleware, and ACME HTTP-01 solver label resolution.

For the big picture, see `CLAUDE.md` §1 (mental model, L2 CNI layer). For component-specific Cilium details, see [`components.md`](components.md) §1. For how ESO-integrated components consume network policy facts, see [`secrets-and-eso.md`](secrets-and-eso.md).

---

## 1. Why Cilium replaces kube-proxy

At `kubeadm init` the kubeadm config sets `proxy.disabled: true` in `ClusterConfiguration` (kubeadm.k8s.io/v1beta4, available since Kubernetes 1.31). No `kube-proxy` DaemonSet is ever created. Cilium's eBPF datapath implements Service IP routing. Gains:

- One fewer DaemonSet, no iptables NAT overhead.
- Host firewall becomes available (not possible with kube-proxy).
- Transparent encryption, L7 policy, and Hubble observability — all on the same data plane.

**Key values** (`hosts-vars/cilium.yaml`):

- `cilium_helm_values.kubeProxyReplacement: true`
- `cilium_helm_values.k8sServiceHost: 127.0.0.1` — required because there's no kube-proxy to proxy the apiserver VIP; points at the systemd HAProxy LB
- `cilium_helm_values.k8sServicePort: 16443` — same reason

**Hard invariant:** `kube-proxy` must remain disabled. Re-enabling it breaks Cilium's eBPF takeover and duplicates work (see `CLAUDE.md` §0).

---

## 2. Host firewall (`CiliumClusterwideNetworkPolicy`)

Defined in `playbook-app/charts/cilium/post/`. The policy's `nodeIpsList` array is built from every inventory host's `ansible_host` + `internal_ip`.

**What it allows:**

- Kubelet API (port 10250) between all `nodeIpsList` members
- Apiserver (6443, via HAProxy 127.0.0.1:16443 → backend managers:6443)
- ETCD peer + client (2379, 2380) between managers
- Hubble observability (4244)
- All `nodeIpsList` → all `nodeIpsList` for inter-node cluster traffic

**What it blocks:**

- Traffic from IPs outside `nodeIpsList` at the host interface level
- Includes: random external IPs, even the public internet

**Critical for node lifecycle.** Adding a new node requires updating the policy BEFORE the new node tries to join the cluster, otherwise the kubelet↔apiserver handshake is blocked at L3 by Cilium. See [`bootstrap-and-ha.md`](bootstrap-and-ha.md) §1.5 for the exact ordering.

**Adding a node — correct procedure:**

```
1. Edit hosts-vars-override/hosts.yaml to add the new host
   (ansible_host + internal_ip).
2. ansible-playbook -i hosts-vars/ -i hosts-vars-override/ \
     playbook-app/cilium-install.yaml --tags post
   (Re-renders CiliumClusterwideNetworkPolicy with the new IPs.)
3. ansible-playbook ... playbook-system/node-install.yaml --limit <new-host>
4. manager-join.yaml or worker-join.yaml for the new host.
```

Skipping step 2 causes join timeouts that appear as "TLS handshake timeout" in kubelet logs — easy to misdiagnose as a network or cert issue.

---

## 3. Traefik + VPN allowlist

Internal-only ingresses (admin UIs, internal services) are gated by a VPN allowlist — Traefik L7 middleware that restricts `X-Forwarded-For` to configured CIDRs.

### 3.1 Variables (`hosts-vars/vpn-rules.yaml`)

| Variable | Purpose |
|---|---|
| `vpn_ips` | L3 CIDR list (used in `NetworkPolicy` and Traefik middleware) |
| `vpn_traefik_middlewares` | L7 Traefik `Middleware` resources (`ipAllowList.sourceRange: {{ vpn_ips }}`) |
| `vpn_ingress_middlewares` | String reference for standard K8s Ingress (`"<traefik-ns>-vpn-only@kubernetescrd"`) |
| `vpn_ingress_route_middlewares` | List of middleware refs used by Traefik `IngressRoute` CRD |

### 3.2 Enabling per component

Set `<c>_vpn_only_enabled: true` in the component's vars file. The component's `post/` chart conditionally attaches the middleware to its ingress:

```yaml
{% if <c>_vpn_only_enabled %}
spec:
  routes:
    - match: Host(`{{ <c>_domain }}`)
      middlewares:
        {{ vpn_ingress_route_middlewares | to_json }}
{% endif %}
```

---

## 4. ACME HTTP-01 solver label resolution

Traefik HTTP-01 challenges create solver pods that must be matched by `NetworkPolicy` in the `pre` phase (allow challenge traffic ingress). The solver's pod labels are defined on the `ClusterIssuer` (cert-manager), not globally — so downstream components can't hardcode them.

### 4.1 The resolver task

`tasks-resolve-acme-solver.yaml` (see [`reusable-tasks.md`](reusable-tasks.md) §1.9):

1. Reads the `cert_manager_cluster_issuers` list from inventory.
2. Finds the entry by name (`{{ dto_cluster_issuer_name }}`).
3. Picks the solver matching `{{ dto_ingress_class_name }}`.
4. Exports three fixed global facts (only one ClusterIssuer/solver is resolved per playbook run, so global fact names cause no conflicts):
   - `acme_cluster_issuer_result_fact` — full ClusterIssuer dict
   - `acme_solver_result_fact` — full solver dict
   - `acme_pod_labels_result_fact` — `podLabels` to match in `NetworkPolicy`

### 4.2 Usage in install playbooks

In the `pre` phase, include the resolver with `tags: [always]` so facts are available for every phase run:

```yaml
- include_tasks: "{{ project_root }}/playbook-app/tasks/tasks-resolve-acme-solver.yaml"
  vars:
    dto_label_name: "<c>-install-init"
    dto_cluster_issuer_name: "{{ <c>_cluster_issuer_name }}"
    dto_ingress_class_name: "{{ <c>_ingress_class_name }}"
  tags: [always]
```

Then the `pre/` chart's `NetworkPolicy` template uses `{{ acme_pod_labels_result_fact | to_json }}` as the allow-selector for incoming HTTP-01 challenge traffic.

### 4.3 Why this indirection matters

`cert-manager` config (list of issuers + solvers) is **the single source of truth**. Downstream components derive labels from it instead of hardcoding. When you change a solver's pod labels (upgrade, reconfigure), every consumer picks it up on next install run — no coordinated multi-file edits.

**Anti-pattern:** hard-coding solver pod labels in a component's `NetworkPolicy`. Always resolve via `tasks-resolve-acme-solver.yaml` (see [`playbook-conventions.md`](playbook-conventions.md) §17.2).

---

## 5. Service routing & CIDRs

From `hosts-vars/k8s-base.yaml` (see [`variables.md`](variables.md) §2.1):

| Variable | Default | Meaning |
|---|---|---|
| `service_subnet` | `10.128.0.0/12` | Kubernetes Service CIDR — ClusterIPs allocated from here |
| `pod_subnet` | `10.64.0.0/10` | Pod CIDR — Cilium IPAM allocates from this range |
| `cluster_dns_domain` | `cluster.local` | Cluster DNS suffix |
| `node_port_start`, `node_port_end` | `1`, `50000` | NodePort range (apiserver `service-node-port-range`) |

**Why `controllerManager.extraArgs.allocate-node-cidrs: "false"`:** Cilium handles IPAM, not kube-controller-manager. Setting `false` lets Cilium own the pod CIDR allocation per node.

---

## 6. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| New node join times out at "TLS handshake" | Cilium host firewall policy doesn't include the new node's IPs | Run `cilium-install.yaml --tags post` with updated inventory first (§2) |
| `NetworkPolicy` blocks ACME HTTP-01 challenge | Hardcoded solver pod labels; cert-manager config changed | Use `tasks-resolve-acme-solver.yaml` instead of hardcoding (§4) |
| Ingress works inside VPN but not outside (expected) or outside but not inside (not expected) | Middleware attachment inverted, or `vpn_ips` misconfigured | Check `hosts-vars/vpn-rules.yaml` and `<c>_vpn_only_enabled` flag |
| Cilium agent pods fail with "kube-proxy conflict" | Someone re-enabled kube-proxy addon | Re-run `kubeadm init` after flipping `proxy.disabled` to `false`? — NO, remove kube-proxy DaemonSet instead. See `CLAUDE.md` §0 |
| `kubectl` to apiserver fails with TLS verification error | certSANs missing a manager's IP/DNS | `apiserver-sans-update.yaml` (see [`bootstrap-and-ha.md`](bootstrap-and-ha.md) §2) |

---

## 7. Port configuration in NetworkPolicy templates

Every `NetworkPolicy` / `CiliumNetworkPolicy` / `CiliumClusterwideNetworkPolicy` template under `playbook-app/charts/<c>/<phase>/templates/` references ports from the chart's `values.yaml` rather than embedding numeric literals. This avoids duplicating the same port (e.g. apiserver `6443`) across 13+ files and gives a single source of truth per chart.

### 7.1 Convention

- **camelCase keys, component-grouped** — matches Helm idiom and the rest of `values.yaml`.
- **Common ports** live in shared buckets in every chart that uses them:
  - `dns.port: 53`
  - `apiserver.port: 6443`
  - `acmeSolver.port: 8089`
  - `external.httpPort: 80`, `external.httpsPort: 443`
  - `kubelet.port: 10250` (only in charts that dial kubelet)
- **Component-specific ports** live under a bucket named after the component, with a `<role>Port` suffix:
  - `vault.apiPort: 8200`
  - `argocd.serverPort: 8080`, `argocd.serverMetricsPort: 8083`
  - `cilium.hubblePeerPort: 4244`
- A chart only declares buckets it actually uses — no dead keys.
- **Same port number with different semantics gets separate keys.** Example: `kubelet.port: 10250` vs `metricsServer.servicePort: 10250` in `metrics-server`; `external.httpsPort: 443` vs `cilium.hubblePeerServicePort: 443` in `cilium`.

### 7.2 Usage in templates

For native Kubernetes `NetworkPolicy`, the value substitutes as an integer:

```yaml
ports:
  - protocol: TCP
    port: {{ .Values.vault.apiPort }}
```

For Cilium `CiliumClusterwideNetworkPolicy`, the CRD types `port` as a string — wrap the substitution in quotes:

```yaml
toPorts:
  - ports:
      - port: "{{ .Values.external.sshPort }}"
        protocol: TCP
```

### 7.3 Reference

Canonical example: [`playbook-app/charts/teleport/pre/values.yaml`](../../playbook-app/charts/teleport/pre/values.yaml) (component-grouped + common buckets, all NP literals templated). Anti-pattern: hard-coded numeric port inside any NP (see [`playbook-conventions.md`](playbook-conventions.md) §17.9).

### 7.4 Out of scope

- `playbook-app/charts/argocd/install/templates/install.yaml` — vendored upstream ArgoCD chart; embedded NP ports remain hardcoded (modifying upstream chart would break sync). Tracked in `todo.md`.
- Migration of values from chart `values.yaml` to inventory `hosts-vars/<c>.yaml` (so operators can override per environment) — separate future task.

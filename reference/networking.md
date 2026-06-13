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

## 4. ACME HTTP-01 solver — per-component Issuer

Traefik HTTP-01 challenges create solver pods that must be admitted by `NetworkPolicy` in the `pre` phase (allow challenge traffic ingress). Each ingress component owns a namespaced cert-manager `Issuer` and derives the solver allow-rules from it — there is no global cluster-wide resolution step.

### 4.1 Per-component Issuer

Each ingress component `<c>` defines `<c>_cert_manager_issuer` in `hosts-vars/<c>.yaml` — a single object `{enabled, body}`:

- `enabled` (`true`/`false`) — the toggle. `true` → the `pre/` chart renders the `Issuer` + solver-NetworkPolicies, and the `Certificate` becomes available in `post/`. `false` → no `Issuer`, no solver-NetworkPolicies, no `Certificate` (e.g. when TLS is terminated upstream by Cloudflare).
- `body` — `{name, spec}` where `spec` is the verbatim cert-manager `Issuer` spec (ACME `server`, `email`, `privateKeySecretRef`, `solvers[]`).

The object is passed verbatim into `<c>_pre_helm_values` and `<c>_post_helm_values` as a single `issuer` key.

### 4.2 issuer.yaml + solver-loop in pre/

Each component's `pre/` chart contains:

- `templates/issuer.yaml` — renders the namespaced `Issuer` under `{{- if .Values.issuer.enabled }}`, `body.spec` dumped via `toYaml`. The template is byte-identical across all components.
- the `NetworkPolicy` template — a solver-loop: iterates `.Values.issuer.body.spec.acme.solvers[]` and, for each `http01` solver, emits a pair of NetworkPolicies — (1) ingress in the component namespace (traefik → solver pod, port `acmeSolver.port`); (2) egress in the traefik namespace (traefik → solver pod). The pod selector uses the solver's `http01.ingress.podTemplate.metadata.labels` (falling back to `acme.cert-manager.io/http01-solver: "true"`). The whole block is gated by `issuer.enabled` + presence of `body.spec.acme`.

### 4.3 Certificate + ingress in post/

The `post/` chart drives each domain through an `ingress_config` object — `<c>_ingress_config` (single-domain) or `<c>_<unit>_ingress_config` (multi-domain) — with independent toggles (see [`variables.md`](variables.md) §1.2):

- `Certificate` — rendered in a dedicated `templates/certificate.yaml`, gated by `issuer.enabled` AND `<unit>.certificate.enabled`. If the issuer is disabled the `Certificate` is silently skipped (no error).
- `Ingress` / `IngressRoute` — rendered in its own per-domain template, gated by `<unit>.ingress.enabled`. `<unit>.ingress.tlsEnabled` selects the `websecure` (TLS) vs `web` (plain HTTP) entrypoint and whether the `tls` block is emitted.

The toggles are independent: e.g. `ingress.enabled: true` + `ingress.tlsEnabled: false` + `certificate.enabled: false` yields a plain HTTP ingress with no `Certificate` — useful when TLS is terminated upstream.

### 4.4 Global ClusterIssuers

`cert_manager_cluster_issuers` (`hosts-vars/cert-manager.yaml`) still defines cluster-wide raw `ClusterIssuer` resources — they remain available as operator infrastructure, but the standard ingress components no longer consume them.

**Anti-pattern:** hard-coding solver pod labels in a component's `NetworkPolicy`. Always derive them from the component's own `<c>_cert_manager_issuer` solver definition (see [`playbook-conventions.md`](playbook-conventions.md) §17.2).

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
| `NetworkPolicy` blocks ACME HTTP-01 challenge | Solver pod labels in `<c>_cert_manager_issuer` don't match the rendered solver pod | Align `http01.ingress.podTemplate.metadata.labels` in `<c>_cert_manager_issuer` with the actual solver pod labels (§4) |
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

Canonical example: [`playbook-app/charts/teleport/pre/values.yaml`](../playbook-app/charts/teleport/pre/values.yaml) (component-grouped + common buckets, all NP literals templated). Anti-pattern: hard-coded numeric port inside any NP (see [`playbook-conventions.md`](playbook-conventions.md) §17.9).

### 7.4 Out of scope

- `playbook-app/charts/argocd/install/templates/install.yaml` — vendored upstream ArgoCD chart; embedded NP ports remain hardcoded (modifying upstream chart would break sync). Tracked in `todo.md`.
- Migration of values from chart `values.yaml` to inventory `hosts-vars/<c>.yaml` (so operators can override per environment) — separate future task.

---

## 8. Cross-namespace cluster-internal access — consumer-owned pattern

Когда workload в namespace `A` должен достучаться до service в namespace `B`, и **оба** namespace имеют baseline `deny-all` NetworkPolicy, cross-ns доступ требует пару NPs (egress в `A` + ingress в `B`). По проектной convention эту пару рендерит **consumer chart** (тот что в namespace `A`), не backend chart (в `B`).

### 8.1 Rationale

- **Dependency order.** Backend (seaweedfs, traefik, gitlab и т.п.) ставится первым в bootstrap sequence — он не знает о future consumer'ах.
- **Single source of truth.** Consumer объявляет свои dependencies в своём chart'е; backend остаётся agnostic к consumer list.
- **No coupling.** Добавление нового consumer'а не требует правки backend chart'а.

### 8.2 Precedents в репо

| Consumer chart | Target backend | Pair of NPs |
|---|---|---|
| consumer's `pre/` (range loop по `issuer.body.spec.acme.solvers`) | `traefik` (ACME HTTP-01) | `allow-acme-solver-<i>` в consumer ns + `<consumer-ns>-allow-acme-solver-<i>` в traefik ns |
| `gitlab/pre` | `traefik` | egress entries в `{namespace}-allow-traefik` (NP в traefik ns) |
| `gitlab/pre` | `haproxy` | `{namespace}-allow-haproxy` в haproxy ns |
| `gitlab-runner/pre` | `gitlab` (webservice, shell) | `{namespace}-allow-gitlab-webservice` + `{namespace}-allow-gitlab-shell` в gitlab ns |
| `seaweedfs/pre` | `traefik` | embedded в seaweedfs's own `allow-traefik` block |
| `gitlab/pre` | `seaweedfs` S3 | `allow-seaweedfs-s3` в gitlab ns + `gitlab-allow-seaweedfs-s3` в seaweedfs ns |
| `gitlab-runner/pre` | `seaweedfs` S3 | `To SeaweedFS S3` egress entries в `allow-gitlab-runner` + `allow-job-pod` NPs + `gitlab-runner-allow-seaweedfs-s3` ingress в seaweedfs ns |
| `mon-system/pre` (loki) | `seaweedfs` S3 + external S3 | `allow-loki` в mon-system ns (egress → seaweedfs S3 8333 + external 443/80) + `mon-system-allow-loki` ingress в seaweedfs ns |

### 8.3 Naming convention

- **NP в consumer ns** (egress allow): короткое имя `allow-<target-name>` (например `allow-seaweedfs-s3`).
- **NP в backend ns** (ingress allow): префикс consumer namespace для уникальности — `{consumer-ns}-allow-<target-name>` (например `gitlab-allow-seaweedfs-s3`, `gitlab-runner-allow-seaweedfs-s3`).
- Префикс предотвращает коллизии когда несколько consumer'ов рендерят NP к одному backend.

### 8.4 Anti-pattern

**Не делать**: backend chart рендерит NP «allow ingress from gitlab/gitlab-runner» в своём `pre/` — это hard-codes список consumer'ов в backend, нарушает dependency order. См. также [`playbook-conventions.md`](playbook-conventions.md) §17.11.

### 8.5 Enable-flag gating (порядко-независимая установка)

Cross-ns доступ к компоненту `<c>` (argocd / gitlab / gitlab-runner / seaweedfs) обёрнут в `{{- if .Values.<c>.enabled }} … {{- end }}` и рендерится только при `<c>_enabled: true`. Гейтятся два вида объектов: **(1)** целые cross-ns NP, создаваемые в namespace цели; **(2)** cross-ns правила (from/to-селекторы на namespace цели) внутри own-ns NP — own-ns NP при этом остаётся, исчезает только правило (исключение: `allow-repo-server` состоит целиком из gitlab-egress → гейтится как целая NP). Прочие правила (apiserver / external / dind / traefik) не гейтятся. Если цель ещё не развёрнута (`enabled: false`), доступ к ней пропускается — install consumer'а не падает из-за несуществующего namespace. Это делает порядок установки этих компонентов **независимым**. Когда цель включают позже, отложенные объекты создаются повторным прогоном `<consumer>-install --tags pre` (идемпотентно). Тот же `<c>_enabled` служит start-guard'ом install/configure плейбука компонента (fail если false). Дефолт `false` (opt-in).

| Chart | Gated element | Gate |
|---|---|---|
| `argocd/pre` | NP `allow-gitlab-shell` (в gitlab ns) | `gitlab.enabled` |
| `argocd/pre` | NP `allow-gitlab-runner-job` (в gitlab-runner ns) | `gitlabRunner.enabled` |
| `argocd/pre` | NP `allow-repo-server` целиком (egress → gitlab) | `gitlab.enabled` |
| `argocd/pre` | `allow-argocd-server`: ingress-правило от gitlab-runner | `gitlabRunner.enabled` |
| `gitlab-runner/pre` | NP `allow-gitlab-webservice` + `allow-gitlab-shell` (в gitlab ns) | `gitlab.enabled` |
| `gitlab-runner/pre` | `allow-gitlab-runner` + `allow-job-pod`: egress-правила к gitlab webservice/shell | `gitlab.enabled` |
| `gitlab/pre` | NP `allow-seaweedfs-s3` + `gitlab-allow-seaweedfs-s3` (в seaweedfs ns) | `seaweedfs.enabled` |
| `gitlab-runner/pre` | `allow-gitlab-runner` + `allow-job-pod`: egress-правила к SeaweedFS S3 + cross-ns `gitlab-runner-allow-seaweedfs-s3` NP | `seaweedfs.enabled` |

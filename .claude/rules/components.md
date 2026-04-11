# Installed Components

## Component Map

| Component | Namespace | Vars file | Helm chart source | Has ESO |
|-----------|-----------|-----------|-------------------|---------|
| Cilium (CNI) | cilium | cilium.yaml | helm.cilium.io | no |
| Cilium Hubble | cilium | cilium.yaml | (same chart) | no |
| Traefik | traefik-lb | traefik.yaml | traefik.github.io/charts | yes |
| cert-manager | cert-manager | cert-manager.yaml | charts.jetstack.io | no |
| External Secrets (ESO) | external-secrets | external-secrets.yaml | no | no |
| ArgoCD | argocd | argocd.yaml | local | yes |
| ArgoCD Git-Ops | argocd | argocd-git-ops.yaml | local | yes |
| Vault | vault | vault.yaml | local (HashiCorp) | no |
| Longhorn | longhorn-system | longhorn.yaml | local | yes |
| GitLab | gitlab | gitlab.yaml | local | yes |
| GitLab Runner | gitlab | gitlab-runner.yaml | local | yes |
| Prometheus Operator | monitoring | prometheus-operator.yaml | local | no |
| Grafana | monitoring | grafana.yaml | local | yes |
| Kube State Metrics | monitoring | kube-state-metrics.yaml | local | no |
| Node Exporter | monitoring | node-exporter.yaml | local | no |
| HAProxy (API LB) | — (systemd) | haproxy.yaml | local | yes |
| Metrics Server | kube-system | metrics-server.yaml | local | no |
| Medik8s | medik8s | medik8s.yaml | local | no |
| Zitadel | zitadel | zitadel.yaml | local | yes |

## Namespace Constraints (cannot change)

- `longhorn-system` — hardcoded in Longhorn upstream
- `argocd` — hardcoded in ArgoCD upstream manifests

## Cilium

- Version: `1.19.1`
- Deployed as **DaemonSet** on all nodes
- Tolerations: `[{operator: "Exists"}]` — runs on every node including managers
- Replaces kube-proxy (skipped at cluster-init)
- `cilium_mask_size: 21` — controls pods-per-node IPAM allocation
- Key variable groups: agent, operator, envoy, hubble-relay, hubble-ui — each has own tolerations/nodeSelector/resources/replicas

**Cilium host firewall** blocks unknown node IPs. Must update policies via `--tags post` before adding nodes.

**Hubble metrics** (all enabled): DNS, drop, TCP, flow, flows-to-world, port-distribution, ICMP, HTTP
- Hubble UI domain: `cilium_hubble_ui_domain`

## Traefik

- Chart version: `38.0.2`, app version: `v3.6.2`
- Namespace: `traefik-lb` (NOT `traefik`)
- Ingress class: `traefik-lb` (NOT `traefik`)
- Deployed as **DaemonSet** with `tolerations: [{operator: "Exists"}]`
- Entry points: `traefik_web_entrypoint` (web/80), `traefik_websecure_entrypoint` (websecure/443)
- Prometheus port: `traefik_prometheus_port` (9200)
- Dashboard domain: `traefik_dashboard_domain`

Middlewares created in post phase: `vpn-only` (ipAllowList), `http-to-https`, `http-www-to-https`.

VPN IPs defined in global var `vpn_ips` (used by ipAllowList middleware).

## cert-manager

- Deploys 3 sub-components: controller, cainjector, webhook (+ startupapicheck job)
- Each sub-component has own tolerations/nodeSelector/affinity/resources variables
- ClusterIssuers defined as array in `cert_manager_cluster_issuers`:
  ```yaml
  cert_manager_cluster_issuers:
    - name: "letsencrypt-prod"
      server: "https://acme-v02.api.letsencrypt.org/directory"
      email: "..."
      privateKeySecretName: "..."
      solvers:
        - ingressClass: "traefik-lb"
          ingressAnnotations: {}
          podLabels: {}
  ```
- Separate config helm release (`cert-manager-pre` chart) before official chart install

## ArgoCD

- Namespace: `argocd` — **cannot change**
- Ingresses: UI (`argocd_ui_domain`) + RPC/gRPC (`argocd_rpc_domain`) — separate IngressRoutes
- External URL: `argocd_external_url` (used in argocd-cm ConfigMap)
- 7 ConfigMaps created in pre phase (not install): argocd-cm, argocd-cmd-params-cm, argocd-gpg-keys-cm, argocd-notifications-cm, argocd-rbac-cm, argocd-ssh-known-hosts-cm, argocd-tls-certs-cm
- Session duration: `argocd_session_duration: "120h"`
- Reconciliation timeout: `argocd_reconciliation_timeout: "30s"`

## Vault

- Image: `hashicorp/vault:1.21.2`
- Shamir seal: `vault_key_shares: 3`, `vault_key_threshold: 2`
- Storage: PVC via `vault_storage_class` / `vault_storage_size`
- Internal URL: `http://vault.{{ vault_namespace }}.svc.{{ cluster_dns_domain }}:8200`
- Vault credentials on disk: `/etc/kubernetes/vault-unseal.json` (managers only)
- Auto-unseal CronJob: runs every `vault_auto_unseal_schedule` on control-plane nodes only
- KV engines: `secret` (admin use), `eso-secret` (ESO use)

## Longhorn

- Namespace: `longhorn-system` — **cannot change**
- Requires kernel modules on nodes: `iscsi_tcp`, `dm_crypt`
- Requires packages: `open-iscsi`, `nfs-common`, `cryptsetup`, `dmsetup`
- Node tags used for storage class targeting: `lh-manager`, `lh-major`, `lh-worker`, `lh-minor`
- Storage class: `lh-major-single-best-effort` (used by Vault PVC)

## HAProxy API Server Load Balancer

- Runs as **systemd service** on ALL nodes (managers + workers)
- Listens: `127.0.0.1:16443`
- Healthz: `127.0.0.1:16444`
- Config regenerated dynamically from inventory managers list
- Updated with `serial: 1` for HA (rolling update)
- Package version pinned: `3.3` (via apt-mark hold)
- When adding a manager: must also run `apiserver-sans-update.yaml` to update TLS SANs

## Prometheus / Monitoring Stack

- Prometheus Operator namespace: `monitoring`
- ServiceMonitor resources created per-component in their post phase (or pre phase charts)
- ServiceMonitor enabled/disabled per component via `<c>_service_monitor_enabled`
- All components report to Prometheus in `monitoring` namespace

## Cilium Post Policy (network policies)

Cilium post chart creates `cilium-clusterwide-network-policy` using `nodeIps` array.
`nodeIps` is built from `ansible_host` (public IP) + `internal_ip` for each host in inventory.
This must be updated before adding any new node.

## ESO Components with Vault Integration

9 components have Vault ESO integration configured in `hosts-vars/vault.yaml`:
`traefik`, `haproxy`, `longhorn`, `gitlab`, `gitlab-runner`, `zitadel`, `argocd`, `argocd-git-ops`, `grafana`

Each maps to a Vault Kubernetes auth role with:
- Kubernetes namespace + ServiceAccount allowed to authenticate
- Vault policy granting read access to specific `eso-secret/` paths

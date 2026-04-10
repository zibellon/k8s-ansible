# k8s-ansible

Production Kubernetes cluster automation: bare-metal bootstrap, node lifecycle, and full application stack deployment via Ansible + local Helm charts.

## Directory Structure

```
playbook-system/        # Cluster infrastructure (init, join, drain, ETCD, HAProxy)
  tasks/                # Reusable task includes for system operations
playbook-app/           # Application installs — 3-phase Helm-based pattern
  tasks/                # Reusable task includes for app operations
  charts/               # Local Helm charts per component (pre/install/post)
hosts-vars/             # Base variable defaults — committed to git
hosts-vars-override/    # Runtime secrets, real IPs, cluster-specific — NOT in git
docs/                   # Documentation
```

## Running Playbooks

```bash
# Always use both inventory directories
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/<component>-install.yaml

# Run specific phase
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/<component>-install.yaml --tags pre
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/<component>-install.yaml --tags install
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/<component>-install.yaml --tags post

# System playbooks require --limit
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/node-install.yaml --limit <hostname>
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/cluster-init.yaml --limit <master-hostname>
```

## Cluster Bootstrap Order

1. `playbook-system/node-install.yaml --limit <node>` — prepare each node (SSH, OS, packages, kubeadm, HAProxy)
2. `playbook-system/cluster-init.yaml --limit <master>` — init first manager (ETCD encryption + kubeadm init)
3. `playbook-system/manager-join.yaml --limit <new-manager>` — join additional managers
4. `playbook-system/worker-join.yaml --limit <worker>` — join workers
5. `playbook-app/cilium-install.yaml` — CNI (required before anything else)
6. `playbook-app/cert-manager-install.yaml` — TLS (required before ingress)
7. `playbook-app/traefik-install.yaml` — ingress controller
8. `playbook-app/vault-install.yaml` — secrets management
9. `playbook-app/external-secrets-install.yaml` — ESO
10. Remaining apps in any order

**Before adding a new node**: run `playbook-app/cilium-install.yaml --tags post` first (updates Cilium host firewall policies).

## Variable Hierarchy

```
hosts-vars/<component>.yaml           ← base defaults (in git)
hosts-vars-override/<component>.yaml  ← real values, passwords, IPs (NOT in git)
playbook inline vars                  ← highest priority
```

## Critical Operational Notes

- **Namespace constraints**: `longhorn-system` and `argocd` cannot be changed (upstream restriction)
- **Cilium firewall**: Run `cilium-install.yaml --tags post` before adding any new node
- **HAProxy LB**: systemd service on all nodes at `127.0.0.1:16443`; update with `serial: 1` for HA
- **is_master flag**: Exactly one manager in inventory must have `is_master: true` — becomes `master_manager_fact`
- **ETCD encryption**: Auto-generated at cluster-init at `/etc/kubernetes/pki/encryption-config.yaml`; must be copied to joining managers (handled automatically by manager-join.yaml)
- **Vault credentials**: Stored at `/etc/kubernetes/vault-unseal.json` on all managers; copied automatically during manager-join
- **Kube-proxy**: Disabled at init — Cilium eBPF handles service routing (IPVS mode configured but Cilium takes over)
- **hosts-vars-override/**: Never commit — contains `ansible_password`, real IPs, vault tokens, credentials

## Key File Locations (runtime, on nodes)

| Path | Purpose |
|------|---------|
| `/etc/kubernetes/kubeadm-config.yaml` | Cluster init configuration |
| `/etc/kubernetes/pki/encryption-config.yaml` | ETCD at-rest encryption keys |
| `/etc/kubernetes/vault-unseal.json` | Vault unseal keys (managers only) |
| `/etc/haproxy/haproxy.cfg` | API server LB config (auto-generated) |
| `/root/.kube/config` | kubectl config (auto-generated post-init) |

## Current Versions (hosts-vars/k8s-base.yaml)

| Component | Version |
|-----------|---------|
| Kubernetes | 1.34 / v1.34.0 |
| containerd | 2.2.1 |
| runc | 1.4.0 |
| CNI plugins | 1.9.0 |
| Helm | 3.19.2 |
| k9s | 0.50.18 |
| Cilium | 1.19.1 |
| Traefik | v3.6.2 (chart 38.0.2) |
| Vault | 1.21.2 |

## Networking

| Setting | Value |
|---------|-------|
| service_subnet | 10.128.0.0/12 |
| pod_subnet | 10.64.0.0/10 |
| dns_domain | cluster.local |
| node_port range | 1–50000 |
| HAProxy LB | 127.0.0.1:16443 |
| HAProxy healthz | 127.0.0.1:16444 |

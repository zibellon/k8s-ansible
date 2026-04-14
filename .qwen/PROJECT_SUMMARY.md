The user wants me to generate a comprehensive project summary based on the conversation history. Let me analyze everything that was done and create a detailed markdown summary.

Key things from the conversation:
1. Project: k8s-ansible — Kubernetes cluster management via Ansible
2. User set up QWEN.md as context file
3. Created .qwen/rules/ directory with 6 rule files
4. Created .qwen/skills/k8s-wizard.md
5. Added async helm upgrade wrapper (tasks-helm-upgrade-async.yaml)
6. Wrapped all 60 helm upgrade commands in async
7. Extracted HAProxy config to hosts-vars
8. Extracted kubeadm config to hosts-vars
9. Added image registry + tag variables for AirGapped support (16 components)
10. Fixed ArgoCD image templating (3 image groups)
11. Fixed multiple image components (Cilium 6 images, Longhorn 13 images)
12. Verified and corrected all image versions against official charts
13. Added Teleport CRDs wait after install
14. Single mode (not HA) for all components

Let me create the comprehensive summary.# Project Summary: k8s-ansible

## Overall Goal
Production Kubernetes cluster bootstrap and lifecycle management via Ansible + Helm charts, supporting AirGapped deployments with full secrets management (Vault + ESO), infrastructure access (Teleport), and GitOps (ArgoCD).

## Key Knowledge

### Architecture
- **Kubernetes**: 1.34+ (kubeadm, bare-metal)
- **CNI**: Cilium 1.19.1 (eBPF, kube-proxy replacement)
- **Ingress**: Traefik v3.6.12 + HAProxy 3.3.6
- **TLS**: CertManager 1.20.2 (Let's Encrypt ACME)
- **Secrets**: Vault 1.21.2 (bank-vaults operator v1.32.1) + External Secrets Operator v2.3.0
- **Storage**: Longhorn 1.11.0
- **Access**: Teleport 18.7.2
- **CI/CD**: ArgoCD + GitLab 18.3.3 + GitLab Runner
- **Monitoring**: Prometheus Operator + Grafana

### Critical Rules
- **Single mode only** — all components run with 1 replica, NEVER HA
- `updateStrategy: Recreate` where possible
- Namespace `longhorn-system` and `argocd` CANNOT be changed
- `is_master: true` — exactly ONE manager must have this flag
- **All launches from project root**: `project_root: "{{ lookup('env', 'PWD') }}"`
- **docs/ directory**: NEVER use unless explicitly requested

### Playbook Architecture (Tags)
- **pre** → NetworkPolicy, ESO resources, RBAC
- **install** → Main component (Helm chart)
- **configure** → Internal configuration (Teleport CRDs, Vault policies)
- **post** → Ingress (always last)
- Special: `crds` (ArgoCD), `operator`/`vault-cr` (Vault)
- Separate configure playbooks: `gitlab-configure.yaml`, `argocd-configure.yaml`, `longhorn-tags-sync.yaml`

### Variable Hierarchy
```
hosts-vars/<component>.yaml           ← base defaults (in git)
hosts-vars-override/<component>.yaml  ← real values, secrets, IPs (NEVER commit)
playbook inline vars                  ← highest priority
```

### Directory Structure
```
playbook-system/        # Cluster infrastructure (init, join, drain, ETCD)
playbook-app/           # App installs (3-phase pattern)
  tasks/                # Reusable tasks
  charts/               # Local Helm charts
hosts-vars/             # Base defaults
hosts-vars-override/    # Runtime secrets (git-ignored)
sources/                # Official chart sources (git-ignored)
.qwen/rules/            # Automated rules for task types
.qwen/skills/           # Interactive wizard
```

### Async Helm Upgrades
All 60 `helm upgrade --install` commands wrapped in `tasks-helm-upgrade-async.yaml`:
- `helm_async_timeout: 1800` (30 min)
- `helm_async_poll: 5` (5 sec)
- Resilient to SSH disconnects

### Image Registry (AirGapped Support)
All 16 components support custom registry via variables:
- Single image: `<component>_image_registry`, `_repository`, `_tag`
- Multi-image (Cilium 6, Longhorn 13, cert-manager 3, ArgoCD 3): per-component variables
- Pattern: `{{ registry }}/{{ repository }}:{{ tag }}`

### Vault + ESO Integration
- 9 components integrated: traefik, haproxy, longhorn, gitlab, gitlab-runner, argocd, argocd-git-ops, grafana, zitadel
- Pattern: `vault_policies` + `vault_roles` → `vault-policy-sync.yaml` → `eso_vault_integration_*`
- `is_need_eso: false` = Vault only, no k8s Secret
- ArgoCD Git-Ops: separate SA `eso-git-ops` in same namespace

### Teleport CRDs
20+ CRDs created by Teleport Operator. Playbook now waits for CRDs via `tasks-wait-crds.yaml` after Helm install, before rollout wait.

### HAProxy apiserver-lb
- Config extracted to `hosts-vars/haproxy-apiserver-lb.yaml` as template
- Placeholder `__MANAGER_SERVER_IP_LIST__` replaced at runtime with manager IPs
- Systemd service on all nodes: `127.0.0.1:16443`, graceful reload

### Kubeadm Config
- Extracted to `hosts-vars/kubeadm-config.yaml` as template
- All if/else removed; eviction configs are dict variables
- `kubelet_eviction_soft`, `kubelet_eviction_hard`, `kubelet_eviction_soft_grace_period`

### Rules System (.qwen/rules/)
6 automated rules triggered by keywords or `/rule <name>`:
1. `add-component.md` — New component installation
2. `update-component.md` — Component version update
3. `add-vault-policy.md` — ESO/Vault integration
4. `update-teleport.md` — Teleport CRD configuration
5. `update-prometheus.md` — Prometheus/Alertmanager config
6. `add-argocd-project.md` — ArgoCD Git-Ops project

### Custom Skill
- `.qwen/skills/k8s-wizard.md` — Interactive wizard (`/k8s-wizard`)
- Asks questions → collects config → generates files/commands

## Recent Actions

### [DONE] Image Registry + Tag for All Components
- Added image variables for 12 new components (cert-manager, cilium, traefik, haproxy, longhorn, teleport, argocd, gitlab, gitlab-runner, metrics-server, zitadel, vault)
- Fixed multi-image components:
  - **Cilium**: 6 images (agent, operator, envoy, hubble-relay, hubble-ui-backend, hubble-ui-frontend)
  - **Longhorn**: 13 images (7 longhorn + 6 CSI)
  - **ArgoCD**: 3 image groups (main, dex, redis)
  - **cert-manager**: 3 images (controller, cainjector, webhook)
- ArgoCD template fixed: `_helpers.tpl` not needed, direct `.Values.global.image.*` usage

### [DONE] Version Verification + Correction
Compared all hosts-vars against sources/ official charts and fixed:
- **HAProxy**: repository `kubernetes-ingress` → `haproxy-alpine`, tag `1.15.0` → `3.3.6`, chart `1.49.0` → `1.28.1`
- **External Secrets**: `v1.2.1` → `v2.3.0`
- **Traefik**: chart `38.0.2` → `39.0.5`, image `v3.6.2` → `v3.6.12`
- **Longhorn**: chart `1.10.1` → `1.11.0`, all images `v1.10.2` → `v1.11.0`, CSI tags updated with date suffix
- **Cert-manager**: `1.19.2` → `1.20.2`
- **GitLab Runner**: chart `0.78.0` → `0.80.0`, image `alpine-v18.2.1` → `alpine-v18.3.0`
- **Node-exporter**: `v1.11.0` → `v1.11.1`

### [DONE] Documentation Added
- "Как проверять image версии" section in QWEN.md
- Version verification steps in `.qwen/rules/update-component.md`
- Version checking in `.qwen/skills/k8s-wizard.md`

### [DONE] Teleport CRDs Wait
Added `tasks-wait-crds.yaml` call after Helm install, before rollout wait, for 20+ Teleport CRDs.

### [DONE] Config Extraction
- HAProxy config → `hosts-vars/haproxy-apiserver-lb.yaml` (template with placeholder)
- Kubeadm config → `hosts-vars/kubeadm-config.yaml` (no if/else, dict variables)

### [DONE] Async Helm Upgrades
All 60 helm upgrade commands wrapped in async wrapper for SSH disconnect resilience.

## Current Plan

All major tasks completed. Future work should follow established patterns:

1. [TODO] When adding new components → follow `.qwen/rules/add-component.md`
2. [TODO] When updating versions → follow version verification steps in rules
3. [TODO] Monitor for new official chart releases and update sources/ + hosts-vars/
4. [TODO] Consider adding remaining components (medik8s, zitadel) when ready
5. [TODO] Regular Vault policy sync when adding new ESO integrations

### Known Gaps
- **Teleport/Zitadel**: No local chart in sources/ for verification (use external charts)
- **medik8s**: Marked NOT_READY
- **zitadel**: Marked NOT_READY (image variables added but component not fully configured)

---

## Summary Metadata
**Update time**: 2026-04-14T09:42:33.005Z 

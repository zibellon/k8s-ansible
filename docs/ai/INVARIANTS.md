> Actual for commit: a451e66

# INVARIANTS — k8s-ansible (1520-tech-ansible)

Hard rules of the project. Each rule carries the consequence of violating it.

1. Agents: static checks only — `make test*`, git/grep/read. `ansible-playbook`/`kubectl`/`helm` against real clusters only on explicit operator instruction. Consequence: this repo drives the prod cluster; an unauthorized run can break it.
2. Secrets: `hosts-vars-override/**` is committed deliberately (private repo, owner decision 2026-10-25). Agents never read/print/copy secret values into reports, docs, or chat; new secrets never go anywhere outside that directory. (Supersedes the stale legacy-CLAUDE.md "never committed" claim.) Consequence: leaking values into reports/docs/chat exposes production credentials.
3. Two-inventory runs always: `-i hosts-vars/ -i hosts-vars-override/<cluster>/`. Pointing `-i` at the bare override parent loads every cluster at once. Consequence: a wrong inventory targets the wrong cluster (or all of them at once).
4. `--limit` for single-node system plays (gate `playbook-system/tasks/tasks-require-limit.yaml:14-22`); `full-node-install.yaml` is bulk-friendly; `serial: 1` rolling plays are exempt. Consequence: forgetting `--limit` fails the gate by design; bypassing it can hit many nodes at once.
5. Exactly one manager with `is_master: true` → `master_manager_fact`. Known gap: multiple masters silently last-wins (documented, backlog 2026-10-25). Consequence: two `is_master` hosts make cluster-scope ops land on an arbitrary one.
6. `longhorn-system` namespace is upstream-fixed, never rename; `argocd` namespace is configurable via `argocd_namespace`. Consequence: renaming breaks Longhorn upstream assumptions.
7. `kube-proxy` is disabled at kubeadm init (`hosts-vars/kubeadm-config.yaml:48-49`); Cilium replaces it — do not re-enable. Consequence: re-enabling kube-proxy conflicts with Cilium's replacement dataplane.
8. Before adding a node to a running cluster: run `cilium-install.yaml --tags post` first (host firewall refresh), else the join handshake is blocked. Consequence: new node joins hang on the Cilium host firewall.
9. ArgoCD `argocd-secret` must stay empty (no `data:`) in every helm render — helm's 3-way merge would prune out-of-band keys (helm#12886). Consequence: templating any `data:` key loses local-account passwords and breaks auth.
10. GitLab root password is Vault-source-of-truth, managed only via ansible (`gitlab-install.yaml --tags config-root`); never via ESO; `passwordMtime` is a mandatory rotation trigger. Consequence: managing it through ESO/other paths desynchronizes Vault and breaks root login.
11. ArgoCD is namespace-scoped (vendored `namespace-install.yaml` v3.6.1; cluster rights only via the `cfg` stage; `argocd-restart.yaml` is mandatory after install — open watches survive RBAC revocation). Consequence: skipping the restart leaves revoked rights acting.
12. argo-workflows: exactly two namespaces (`argo_workflows_namespace` + `argo_workflows_cr_namespace`, assert-gated); no `kind: Workflow` in git-ops; `pre-cfg` strictly before `install`. Consequence: single-namespace or wrong order breaks install (CrashLoopBackOff / silent WaitForCacheSync hang); git-ops Workflows loop forever via TTL + selfHeal.
13. Objects rendered into foreign namespaces must carry unique owner-prefixed names (`argocd-managed-*`, `kargo-custom-*`, `teleport-managed-*`) — a name collision fails the whole release on `invalid ownership metadata`; `--take-ownership` is never used. Consequence: one colliding object takes down the entire helm release.
14. `is_bastion` (SSH ProxyJump host flag, rebooted last) ≠ `bastion_proxy` (inventory group = external HAProxy edge); `bastion_proxy` hosts never set `is_bastion`. Consequence: the collision breaks the `Find bastion host` lookup in `tasks-set-master-manager.yaml`.
15. ETCD encryption-at-rest (aescbc) is generated at cluster init; key rotation only via `playbook-system/utils/etcd-key-rotate.yaml` (state-file resume, `serial: 1`). Consequence: rotating keys by hand risks data encrypted with a lost key.

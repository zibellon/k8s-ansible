# Reusable Task Files

## playbook-app/tasks/

### tasks-pre-check.yaml
Auto-detects `master_manager_fact` from inventory, validates environment is ready.

```yaml
- include_tasks: tasks/tasks-pre-check.yaml
  vars:
    label_name: "<c>-install-pre-check"
  tags: [always]
```
**Output fact**: `master_manager_fact` (hostname of the manager with `is_master: true`)

---

### tasks-forbid-kube-system.yaml
Asserts that the target namespace is NOT `kube-system`.

```yaml
- include_tasks: tasks/tasks-forbid-kube-system.yaml
  vars:
    label_name: "<c>-install-pre-check"
    namespace_value: "{{ <c>_namespace }}"
  tags: [always]
```

---

### tasks-copy-chart.yaml
Archives local chart as tar.gz, copies to remote node, extracts. Faster than rsync for large charts.

```yaml
- include_tasks: tasks/tasks-copy-chart.yaml
  vars:
    label_name: "<c>-install-pre"
    chart_name: "<c>-pre"                              # used for temp archive name
    chart_local_src: "{{ playbook_dir }}/charts/<c>/pre/"   # trailing slash REQUIRED
    chart_remote_dest: "{{ remote_charts_dir }}/<c>/pre"    # no trailing slash
  tags: [pre]
```
**Gotcha**: `chart_local_src` must have trailing `/`. `chart_remote_dest` must NOT.

---

### tasks-add-helm-repo.yaml
Runs `helm repo add` + `helm repo update`. Used before installing official charts.

```yaml
- include_tasks: tasks/tasks-add-helm-repo.yaml
  vars:
    label_name: "<c>-install"
    helm_repo_name: "jetstack"
    helm_repo_url: "https://charts.jetstack.io"
  tags: [install]
```

---

### tasks-wait-crds.yaml
Waits for CRDs to reach `Established` condition using `kubectl wait`, with retry logic.

```yaml
- include_tasks: tasks/tasks-wait-crds.yaml
  vars:
    label_name: "<c>-install"
    crds_list:
      - "crd/applications.argoproj.io"
      - "crd/appprojects.argoproj.io"
    crds_wait:
      timeout: "{{ crd_wait_timeout }}"     # default "60s" from k8s-base.yaml
      retries: "{{ crd_wait_retries }}"     # default 15
      delay: "{{ crd_wait_delay }}"         # default "5s"
  tags: [install]
```
**Format**: `crd/<name>` (with `crd/` prefix, no slashes before).

---

### tasks-wait-rollout.yaml
Waits for Deployment / DaemonSet / StatefulSet rollout to complete.

```yaml
- include_tasks: tasks/tasks-wait-rollout.yaml
  vars:
    label_name: "<c>-install"
    rollout_namespace: "{{ <c>_namespace }}"
    rollout_timeout: "{{ <c>_rollout_timeout }}"
    rollout_resources:
      - "deployment/argocd-server"
      - "deployment/argocd-repo-server"
      - "statefulset/argocd-application-controller"
  tags: [install]
```
**Format**: `deployment/<name>`, `daemonset/<name>`, `statefulset/<name>`

---

### tasks-eso-merge.yaml
Merges base + extra arrays for all `eso_vault_integration_*` variables. Validates consistency:
- Unique policy/role names
- All role policies exist in `vault_policies_final`
- SecretStore role_name exists in vault_roles_final
- For argocd + argocd_git_ops (same namespace): no collision on secret names

**No input vars needed** — reads all vars automatically.

**Output facts**:
- `vault_policies_final` — merged vault policies list
- `vault_roles_final` — merged vault roles list
- `eso_vault_integration_<component>_secrets_merged` — per component (9 total: traefik, haproxy, longhorn, gitlab, gitlab_runner, zitadel, argocd, argocd_git_ops, grafana)

```yaml
- include_tasks: tasks/tasks-eso-merge.yaml
  tags: [always]
```

---

### tasks-resolve-acme-solver.yaml
Looks up a ClusterIssuer by name in `cert_manager_cluster_issuers`, finds the solver matching the given `ingressClass`, extracts `podLabels`. Used to correctly label ACME challenge solver pods.

```yaml
- include_tasks: tasks/tasks-resolve-acme-solver.yaml
  vars:
    label_name: "<c>-install-init"
    cluster_issuer_name: "{{ <c>_cluster_issuer_name }}"
    ingress_class_name: "{{ <c>_ingress_class_name }}"
    acme_cluster_issuer_result_var: "<c>_acme_cluster_issuer"
    acme_solver_result_var: "<c>_acme_solver"
    acme_pod_labels_result_var: "<c>_acme_solver_pod_labels"
  tags: [always]
```
**Output**: Three dynamic facts set by variable name (passed as parameters).

---

### tasks-verify-helm.yaml
Checks that a Helm release is in `deployed` status.

```yaml
- include_tasks: tasks/tasks-verify-helm.yaml
  vars:
    label_name: "<c>-install"
    helm_namespace: "{{ <c>_namespace }}"
  tags: [install]
```

---

### tasks-eso-force-sync.yaml
Forces ESO to re-sync ExternalSecrets in a namespace.

```yaml
- include_tasks: tasks/tasks-eso-force-sync.yaml
  vars:
    label_name: "<c>"
    namespace: "{{ <c>_namespace }}"
  tags: [post]
```

---

### tasks-vault-distribute-creds.yaml
Copies Vault credentials from master manager to other nodes (used during manager-join).

```yaml
- include_tasks: tasks/tasks-vault-distribute-creds.yaml
  vars:
    label_name: "<c>-install"
```

---

### tasks-set-master-manager.yaml
Sets `master_manager_fact` from inventory. Called by `tasks-pre-check.yaml` — rarely needed standalone.

---

## playbook-system/tasks/

### tasks-require-limit.yaml
Asserts `--limit` flag was provided. Prevents accidental full-cluster runs.
Used in: `cluster-init.yaml`, `node-install.yaml`, `manager-join.yaml`, `worker-join.yaml`.

### tasks-require-manager.yaml
Asserts target host is in `managers` group.

### tasks-require-worker.yaml
Asserts target host is in `workers` group.

### tasks-gather-cluster-facts.yaml
Collects cluster state and sets facts on all hosts:
- `master_manager_fact` — from `is_master: true`
- `is_master_manager_exist` — boolean
- `is_cluster_init` — boolean (cluster initialized and healthy)
- `joined_node_ips` — list of joined node IPs
- `joined_node_hostnames` — list of joined node hostnames
- `is_node_joined` — boolean (this host is in cluster)

Never fails — missing facts reflect uninitialized state. Used for idempotent logic.

### tasks-set-master-manager.yaml
Sets `master_manager_fact` from `is_master: true` host var.

### tasks-set-is-cluster-init.yaml
Detects cluster initialization state via `kubeadm token list`.

### tasks-set-is-node-joined.yaml
Detects whether current host is already joined via `kubectl get nodes`.

### tasks-kubeadm-config-create.yaml
Generates `/etc/kubernetes/kubeadm-config.yaml` with:
- `controlPlaneEndpoint: {{ haproxy_apiserver_lb_host }}:{{ haproxy_apiserver_lb_port }}`
- `certSANs` built dynamically from all manager IPs (public + internal)
- ETCD encryption config reference
- KubeletConfiguration (eviction thresholds, logging)
- KubeProxyConfiguration: `mode: ipvs`
- IPAM: kube-controller `allocate-node-cidrs: false` (Cilium handles this)

### tasks-kubeadm-init.yaml
Runs `kubeadm init --config {{ kubeadm_config_path }} --skip-phases addon/kube-proxy`.
Idempotent — skips if `is_cluster_init` is true.

### tasks-kubectl-configure.yaml
Sets up `/root/.kube/config` from admin kubeconfig after cluster init.

### tasks-apply-node-labels.yaml
Applies `node_labels` list from host vars via `kubectl label node`.

### tasks-untaint-control-plane.yaml
Removes `node-role.kubernetes.io/control-plane:NoSchedule` taint (for single-node setups).

### tasks-kubelet-health-wait.yaml
Waits for kubelet systemd service to become active and healthy after start/restart.

### task-apiserver-restart.yaml
Restarts kube-apiserver by temporarily moving its static pod manifest.

### tasks-haproxy-lb-config-create.yaml
Generates `/etc/haproxy/haproxy.cfg` from inventory managers list.
Each manager becomes a backend server with `ansible_host:api_server_bind_port`.

### tasks-haproxy-lb-health-wait.yaml
Waits for HAProxy port `16443` to be listening.

### tasks-haproxy-lb-restart.yaml
Restarts HAProxy systemd service.

## General Usage Rules

1. Always use `include_tasks` (NOT `import_tasks`) — preserves tag inheritance
2. Always pass `label_name` matching the enclosing task's label prefix
3. Tag every include with the appropriate phase (`[always]`, `[pre]`, `[install]`, `[post]`)
4. Tasks that run kubectl/helm delegate automatically to `master_manager_fact`

# Playbook Conventions

## File Naming

Pattern: `<component>-<action>.yaml`

Actions: `install`, `configure`, `restart`, `sync`, `force-sync`

Examples: `argocd-install.yaml`, `traefik-restart.yaml`, `vault-configure.yaml`, `longhorn-s3-restore-create.yaml`

## Standard Playbook Header

```yaml
- name: Install <Component> via local Helm chart
  hosts: managers
  become: true
  gather_facts: false
```

Always `hosts: managers`. Never `gather_facts: true`.
All kubectl/helm tasks use `delegate_to: "{{ master_manager_fact }}"` + `run_once: true`.

## Task Naming Format

```
[<component>-<action>-<phase>] <Description>
```

Examples:
- `[argocd-install-pre-check] Validate target`
- `[argocd-install-pre-check] Forbid kube-system namespace`
- `[argocd-install-init] Merge ESO integrations`
- `[argocd-install-init] Resolve ACME solver`
- `[argocd-install-pre] Copy chart to remote`
- `[argocd-install-pre] Install NetworkPolicies via Helm`
- `[argocd-install] Create values-override.yaml`
- `[argocd-install] Install or upgrade via Helm`
- `[argocd-post] Create Ingress`

## 3-Phase Structure (always-tags pattern)

```yaml
tasks:
  # ===== ALWAYS: runs on every invocation =====

  - name: "[<c>-install-pre-check] Validate target"
    include_tasks: tasks/tasks-pre-check.yaml
    vars:
      label_name: "<c>-install-pre-check"
    tags: [always]

  - name: "[<c>-install-pre-check] Forbid kube-system namespace"
    include_tasks: tasks/tasks-forbid-kube-system.yaml
    vars:
      label_name: "<c>-install-pre-check"
      namespace_value: "{{ <c>_namespace }}"
    tags: [always]

  - name: "[<c>-install-init] Merge ESO integrations"
    include_tasks: tasks/tasks-eso-merge.yaml
    tags: [always]

  # Optional: if component uses ACME / cert-manager ingress
  - name: "[<c>-install-init] Resolve ACME solver"
    include_tasks: tasks/tasks-resolve-acme-solver.yaml
    vars:
      label_name: "<c>-install-init"
      cluster_issuer_name: "{{ <c>_cluster_issuer_name }}"
      ingress_class_name: "{{ <c>_ingress_class_name }}"
      acme_cluster_issuer_result_var: "<c>_acme_cluster_issuer"
      acme_solver_result_var: "<c>_acme_solver"
      acme_pod_labels_result_var: "<c>_acme_solver_pod_labels"
    tags: [always]

  # ===== PRE: NetworkPolicies + ESO resources =====

  - name: "[<c>-install-pre] Copy chart to remote"
    include_tasks: tasks/tasks-copy-chart.yaml
    vars:
      label_name: "<c>-install-pre"
      chart_name: "<c>-pre"
      chart_local_src: "{{ playbook_dir }}/charts/<c>/pre/"   # trailing slash required
      chart_remote_dest: "{{ remote_charts_dir }}/<c>/pre"
    tags: [pre]

  - name: "[<c>-install-pre] Create values-override.yaml"
    copy:
      content: |
        ...
      dest: "{{ remote_charts_dir }}/<c>/pre/values-override.yaml"
    delegate_to: "{{ master_manager_fact }}"
    run_once: true
    tags: [pre]

  - name: "[<c>-install-pre] Install NetworkPolicies via Helm"
    command: >
      helm upgrade --install <c>-pre {{ remote_charts_dir }}/<c>/pre
      --namespace {{ <c>_namespace }}
      --create-namespace
      --values {{ remote_charts_dir }}/<c>/pre/values-override.yaml
      --cleanup-on-fail --atomic --wait --wait-for-jobs
      --timeout {{ <c>_helm_timeout }}
    delegate_to: "{{ master_manager_fact }}"
    run_once: true
    tags: [pre]

  # ===== INSTALL: CRDs + Helm chart =====

  # Optional CRDs (if needed before main chart)
  - name: "[<c>-install] Wait for CRDs"
    include_tasks: tasks/tasks-wait-crds.yaml
    vars:
      label_name: "<c>-install"
      crds_list:
        - "crd/<crd-name>.example.com"
      crds_wait:
        timeout: "{{ crd_wait_timeout }}"
        retries: "{{ crd_wait_retries }}"
        delay: "{{ crd_wait_delay }}"
    tags: [install]

  - name: "[<c>-install] Copy chart to remote"
    include_tasks: tasks/tasks-copy-chart.yaml
    vars:
      label_name: "<c>-install"
      chart_name: "<c>"
      chart_local_src: "{{ playbook_dir }}/charts/<c>/install/"
      chart_remote_dest: "{{ remote_charts_dir }}/<c>/install"
    tags: [install]

  - name: "[<c>-install] Create values-override.yaml"
    copy:
      content: |
        tolerations: {{ <c>_tolerations | to_json }}
        nodeSelector: {{ <c>_node_selector | to_json }}
        affinity: {{ <c>_affinity | to_json }}
        resources: {{ <c>_resources | to_json }}
      dest: "{{ remote_charts_dir }}/<c>/install/values-override.yaml"
    delegate_to: "{{ master_manager_fact }}"
    run_once: true
    tags: [install]

  - name: "[<c>-install] Install or upgrade via Helm"
    command: >
      helm upgrade --install <c> {{ remote_charts_dir }}/<c>/install
      --namespace {{ <c>_namespace }}
      --create-namespace
      --values {{ remote_charts_dir }}/<c>/install/values-override.yaml
      --cleanup-on-fail --atomic --wait --wait-for-jobs
      --timeout {{ <c>_helm_timeout }}
    delegate_to: "{{ master_manager_fact }}"
    run_once: true
    tags: [install]

  - name: "[<c>-install] Wait for rollout"
    include_tasks: tasks/tasks-wait-rollout.yaml
    vars:
      label_name: "<c>-install"
      rollout_namespace: "{{ <c>_namespace }}"
      rollout_timeout: "{{ <c>_rollout_timeout }}"
      rollout_resources:
        - "deployment/<c>"
    tags: [install]

  # ===== POST: Ingress + config =====

  - name: "[<c>-post] Copy chart to remote"
    include_tasks: tasks/tasks-copy-chart.yaml
    vars:
      label_name: "<c>-post"
      chart_name: "<c>-post"
      chart_local_src: "{{ playbook_dir }}/charts/<c>/post/"
      chart_remote_dest: "{{ remote_charts_dir }}/<c>/post"
    tags: [post]

  - name: "[<c>-post] Create values-override.yaml"
    copy:
      content: |
        ...
      dest: "{{ remote_charts_dir }}/<c>/post/values-override.yaml"
    delegate_to: "{{ master_manager_fact }}"
    run_once: true
    tags: [post]

  - name: "[<c>-post] Install post resources via Helm"
    command: >
      helm upgrade --install <c>-post {{ remote_charts_dir }}/<c>/post
      --namespace {{ <c>_namespace }}
      --values {{ remote_charts_dir }}/<c>/post/values-override.yaml
      --cleanup-on-fail --atomic --wait --wait-for-jobs
      --timeout {{ <c>_helm_timeout }}
    delegate_to: "{{ master_manager_fact }}"
    run_once: true
    tags: [post]
```

## Helm Release Names

Each component creates exactly 3 Helm releases:
- `<component>-pre` — NetworkPolicies + ESO resources
- `<component>` — main chart
- `<component>-post` — Ingress + post-install config

Examples: `argocd-pre`, `argocd`, `argocd-post`

## External Helm Charts (add repo first)

For charts from official repos, use `tasks-add-helm-repo.yaml` before install:
- Cilium: `https://helm.cilium.io/` — release `cilium/cilium`
- Traefik: `https://traefik.github.io/charts` — release `traefik/traefik`
- Cert-manager: `https://charts.jetstack.io` — release `jetstack/cert-manager` with `v` prefix on version

## values-override.yaml Creation Pattern

```yaml
- name: "[label] Create values-override.yaml"
  copy:
    content: |
      # Simple scalar
      replicaCount: {{ component_replica_count }}
      
      # Object via to_json
      tolerations: {{ component_tolerations | to_json }}
      nodeSelector: {{ component_node_selector | to_json }}
      affinity: {{ component_affinity | to_json }}
      resources: {{ component_resources | to_json }}
      
      # Nested YAML via to_nice_yaml + indent
      config:
        {{ component_config | to_nice_yaml | indent(8) }}
      
      # ESO secrets
      eso:
        roleName: "{{ eso_vault_integration_component.role_name }}"
        secrets: {{ eso_vault_integration_component_secrets_merged | to_json }}
    dest: "{{ remote_charts_dir }}/<c>/install/values-override.yaml"
  delegate_to: "{{ master_manager_fact }}"
  run_once: true
```

## Playbook Comment Header (always include)

```yaml
# =============================================================================
# Install <Component> via local Helm charts
# Steps:
#   1. <c>/pre: NetworkPolicies + ESO (ServiceAccount, SecretStore, ExternalSecret)
#   2. <c>/install: CRDs + main manifests
#   3. <c>/post: Ingress + post-install resources
#
# Usage (tags):
#   ansible-playbook <c>-install.yaml              # full install
#   ansible-playbook <c>-install.yaml --tags pre
#   ansible-playbook <c>-install.yaml --tags install
#   ansible-playbook <c>-install.yaml --tags post
# =============================================================================
```

## Section Separators in Playbooks

```yaml
    # =========================================================================
    # STEP N: COMPONENT/PHASE (description)
    # =========================================================================
```

Use these section separators to visually group phases. They're present in all existing playbooks.

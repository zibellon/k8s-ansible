# Medik8s Post

Configuration manifests are created dynamically by Ansible playbook:
- self-node-remediation-config.yaml (SelfNodeRemediationConfig CR)
- self-node-remediation-template.yaml (SelfNodeRemediationTemplate CR)
- nhc-workers.yaml (NodeHealthCheck for worker nodes)
- nhc-control-plane.yaml (NodeHealthCheck for control-plane nodes)

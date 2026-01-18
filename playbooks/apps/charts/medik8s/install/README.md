# Medik8s Install

OLM Subscriptions are created dynamically by Ansible playbook:
- subscription-nhc.yaml (Node Health Check Operator)
- subscription-snr.yaml (Self Node Remediation)

Waits for CSV status "Succeeded" before proceeding.

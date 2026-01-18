# Infisical Install

This phase uses the official Infisical Helm chart.

**Repository:** `infisical-helm-charts`  
**Chart:** `infisical-helm-charts/infisical-standalone`  
**URL:** https://dl.cloudsmith.io/public/infisical/helm-charts/helm/charts/

The chart is installed directly via `helm upgrade --install` command in the playbook.
No local chart files are needed - the official chart is fetched from the Helm repository.

**Configuration:**
- `kubeSecretRef: "infisical-secrets"` - uses secrets created in `install-secrets` phase
- `postgresql.enabled: false` - external PostgreSQL from `install-pg` phase
- `redis.enabled: false` - external Redis from `install-redis` phase
- `ingress.enabled: false` - ingress is configured in `post` phase

# Monitoring: ServiceMonitor Overview

Полный список компонентов — какие ServiceMonitor можно создать, какие нельзя.

ServiceMonitor создаётся **позже**, после установки Prometheus Operator. Сейчас — только Services.

---

## ServiceMonitor — МОЖНО создать

### Cilium (`kube-system`)

| Service | Порт | Имя порта |
|---------|------|-----------|
| `cilium-agent-metrics` | 9962 | `prometheus` |
| `cilium-operator-metrics` | 9963 | `prometheus` |
| `hubble-relay-metrics` | 9966 | `metrics` |
| `hubble-metrics` | 9965 | — (headless) |

---

### Cert-manager (`cert-manager`)

| Service | Порт |
|---------|------|
| `cert-manager` | 9402 |
| `cert-manager-cainjector` | 9402 |
| `cert-manager-webhook` | 9402 |

---

### External Secrets (`external-secrets`)

| Service | Порт |
|---------|------|
| `external-secrets-metrics` | 8080 |
| `external-secrets-webhook-metrics` | 8080 |
| `external-secrets-cert-controller-metrics` | 8080 |

---

### Traefik (`traefik-lb`)

| Service | Порт |
|---------|------|
| `traefik-metrics` | порт metrics entrypoint |

---

### HAProxy (`haproxy-lb`)

| Service | Порт | Имя порта |
|---------|------|-----------|
| `haproxy-lb` | 1024 | `stat` |

---

### Longhorn (`longhorn-system`)

| Service | Порт | Имя порта |
|---------|------|-----------|
| `longhorn-backend` | 9500 | `manager` |

---

### GitLab (`gitlab`)

| Service | Порт | Имя порта |
|---------|------|-----------|
| `gitlab-webservice-default` | 8083 | `http-metrics-ws` |
| `gitlab-webservice-default` | 9229 | `http-metrics-wh` |
| `gitlab-gitaly` | 9236 | `http-metrics` |
| `gitlab-gitlab-pages-metrics` | 9235 | `metrics` |
| `gitlab-gitlab-exporter` | 9168 | `http-metrics` |
| `gitlab-gitlab-shell` | 9122 | `http-metrics` |
| `gitlab-registry` | 5001 | `http-metrics` |

---

### Vault (`vault`)

| Service | Порт | Имя порта | Особенность |
|---------|------|-----------|-------------|
| `vault` | 8200 | `http` | path: `/v1/sys/metrics`, params: `format=prometheus` |

---

### GitLab Runner (`gitlab-runner`)

| Service | Порт | Имя порта |
|---------|------|-----------|
| `gitlab-runner` | 9252 | `metrics` |

---

### ArgoCD (`argocd`)

| Service | Порт | Имя порта |
|---------|------|-----------|
| `argocd-metrics` | 8082 | `metrics` |
| `argocd-server-metrics` | 8083 | `metrics` |
| `argocd-repo-server` | 8084 | `metrics` |
| `argocd-applicationset-controller` | 8080 | `metrics` |
| `argocd-dex-server` | 5558 | `metrics` |
| `argocd-notifications-controller-metrics` | 9001 | `metrics` |

---

## ServiceMonitor — НЕЛЬЗЯ создать (нет Service)

| Компонент | Порт | Причина | Что можно |
|-----------|------|---------|-----------|
| **GitLab Sidekiq** | 3807 | Chart не создаёт Service для метрик — только pod annotations | **PodMonitor** |
| **Cilium Envoy** | 9964 | `metricsService` не включён, нет шаблона Service | Добавить `envoy.prometheus.metricsService: true` или **PodMonitor** |

Как работают network policy

- разрешения накладываются именно на POD
- То есть: в разрешениях участвует именно порт пода, а не сервиса или чего-то ещё. Это важно

---

Интересная особенность работы политик с mode=host

---

Какие вопрос по политикам

CiliumClusterwideNetworkPolicy - ограничение исходящего трафика. Работает ли на pods, которые запущены в режиме hostNetwork: false ?

Как ограничить поход из NOHOST -> HOST ... ? То есть не только порт но и ещё что-то ? Мне нужно разрешить трафик но только внутри кластера

Общее правило такое
- clusterWideNetworkPolicy = полное ограничение на вход и на выход. Разрешаем полный комплект для in-cluster (вход и выход). +22 (вход), +SMTP (вход|выход), +ICMP(вход|выход)

---

# Network Policy в Kubernetes с Cilium

## Оглавление

- [Введение](#введение)
- [Типы политик](#типы-политик)
- [hostNetwork и NetworkPolicy](#hostnetwork-и-networkpolicy)
- [Как работает DNAT и порты](#как-работает-dnat-и-порты)
- [Таблица контроля трафика](#таблица-контроля-трафика)
- [Как ограничивать трафик](#как-ограничивать-трафик)
- [podSelector и hostNetwork](#podselector-и-hostnetwork)
- [Примеры политик](#примеры-политик)
- [Источники](#источники)

---

## Введение

В Kubernetes с Cilium для полного контроля сетевого трафика необходимо комбинировать **две** типа политик:

| Политика | Контролирует | Применяется к |
|----------|-------------|---------------|
| `NetworkPolicy` | Ingress/Egress подов | Поды с `hostNetwork: false` |
| `CiliumClusterwideNetworkPolicy` | Ingress/Egress на ноды | Поды с `hostNetwork: true` + процессы на хосте |

---

## Типы политик

### NetworkPolicy (стандартная Kubernetes)

- Применяется к **подам с собственным network namespace**
- Работает на уровне Pod IP (из pod CIDR)
- Поддерживает `podSelector`, `namespaceSelector`, `ipBlock`

### CiliumClusterwideNetworkPolicy (Cilium extension)

- Применяется к **нодам** (host namespace)
- Работает через `hostFirewall.enabled=true`
- Использует `nodeSelector` и `entities` (cluster, host, world, remote-node)
- Контролирует hostNetwork поды и процессы на хосте (sshd, kubelet, etc.)

---

## hostNetwork и NetworkPolicy

### Официальная позиция Kubernetes

**Источник:** https://kubernetes.io/docs/concepts/services-networking/network-policies/

> **"The behavior of NetworkPolicies concerning `hostNetwork` pods is undefined and depends on the network plugin's capabilities. Some plugins may apply NetworkPolicies to `hostNetwork` pods similarly to regular pods, while others may treat them as node traffic, ignoring NetworkPolicy rules."**

**Из секции "What you can't do":**

> **"Blocking loopback or host traffic: NetworkPolicies cannot prevent loopback access or incoming traffic from the host node. Pods cannot block localhost access or traffic from their resident node."**

### Дополнительное подтверждение (Red Hat OpenShift)

**Источник:** https://docs.redhat.com/en/documentation/openshift_container_platform/4.18/html/network_security/network-policy

> **"Pods configured with `hostNetwork: true` share the host's network namespace. As a result, standard Kubernetes network policies do not apply to these pods."**

### Вывод для Cilium

| Утверждение | Статус |
|-------------|--------|
| NetworkPolicy работает на `hostNetwork: true` | ❌ НЕТ |
| NetworkPolicy работает на `hostNetwork: false` | ✅ ДА |
| CiliumClusterwideNetworkPolicy работает на `hostNetwork: true` | ✅ ДА |
| podSelector может матчить `hostNetwork: true` под | ❌ НЕТ (у него node IP, не pod IP) |

---

## Как работает DNAT и порты

### Cilium + kubeProxyReplacement

Когда `kubeProxyReplacement=true`, Cilium сам выполняет DNAT и **проверяет NetworkPolicy ПОСЛЕ DNAT**.

**Источник:** https://github.com/cilium/cilium/issues/12545

> **"When Cilium defers service translation to kube-proxy, the host firewall applies policies before DNAT occurs. This means policies are enforced on service addresses rather than on backend endpoints. This behavior is observed when the host firewall is enabled and `kubeProxyReplacement` is NOT fully enabled."**

### Схема обработки трафика

```
Pod → Service:443 (ClusterIP)
        ↓
   Cilium DNAT
        ↓
   Endpoint:6443 (реальный pod/node)
        ↓
   NetworkPolicy CHECK ← проверка ЗДЕСЬ (видит endpoint port!)
        ↓
   Destination
```

### Практический пример

```yaml
# Service
apiVersion: v1
kind: Service
metadata:
  name: kubernetes
  namespace: default
spec:
  ports:
    - port: 443        # ← Service port
      targetPort: 6443 # ← Endpoint port (реальный)
```

**Какой порт указывать в NetworkPolicy?**

| kubeProxyReplacement | DNAT делает | Policy видит | Указывать в NetworkPolicy |
|---------------------|-------------|--------------|---------------------------|
| `true` | Cilium | Endpoint port | **6443** (targetPort) |
| `false` / `partial` | kube-proxy | Service port | **443** (port) |

### Проверка на практике

```bash
# Из пода с разрешением egress на порт 6443
curl -k https://kubernetes.default.svc/healthz
# → Работает

# Из пода БЕЗ разрешения на порт 6443
curl -k https://kubernetes.default.svc/healthz
# → Connection refused / timeout
```

---

## Таблица контроля трафика

### Матрица: источник → назначение

| Источник | Назначение | NetworkPolicy на источнике (egress) | NetworkPolicy на назначении (ingress) | CiliumClusterwideNetworkPolicy |
|----------|------------|-------------------------------------|--------------------------------------|-------------------------------|
| `hostNetwork: true` | `hostNetwork: true` | ❌ Не работает | ❌ Не работает | ✅ host-firewall ingress |
| `hostNetwork: true` | `hostNetwork: false` | ❌ Не работает | ⚠️ Работает, но `from: podSelector` не матчит | ✅ host-firewall egress (если включен) |
| `hostNetwork: false` | `hostNetwork: true` | ✅ Работает, но `to: podSelector` не матчит | ❌ Не работает | ✅ host-firewall ingress |
| `hostNetwork: false` | `hostNetwork: false` | ✅ Полностью работает | ✅ Полностью работает | ❌ Не применяется |

### Что использовать для каждого сценария

| Сценарий | Инструмент |
|----------|-----------|
| host ↔ host | `CiliumClusterwideNetworkPolicy` (ingress + egress) |
| host → no_host | `CiliumClusterwideNetworkPolicy` egress + `NetworkPolicy` ingress (без podSelector в from) |
| no_host → host | `NetworkPolicy` egress (только ports, без to) + `CiliumClusterwideNetworkPolicy` ingress |
| no_host ↔ no_host | `NetworkPolicy` (podSelector работает полностью) |

### Двойной контроль для host → no_host

Для максимальной безопасности можно комбинировать **обе** политики:

```
┌─────────────────────────────────────────────────────────────────┐
│  cilium-agent (hostNetwork: true)                               │
│                    │                                            │
│                    ▼                                            │
│  ┌──────────────────────────────────────┐                      │
│  │ CiliumClusterwideNetworkPolicy       │                      │
│  │ egress: toEntities: cluster          │  ← Проверка 1        │
│  └──────────────────────────────────────┘                      │
│                    │                                            │
└────────────────────┼────────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│  ┌──────────────────────────────────────┐                      │
│  │ NetworkPolicy                        │                      │
│  │ ingress: ports: [53]                 │  ← Проверка 2        │
│  └──────────────────────────────────────┘                      │
│                    │                                            │
│                    ▼                                            │
│  coredns (hostNetwork: false)                                   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Как ограничивать трафик

### 1. hostNetwork ↔ hostNetwork

**Инструмент:** `CiliumClusterwideNetworkPolicy`

```yaml
apiVersion: cilium.io/v2
kind: CiliumClusterwideNetworkPolicy
metadata:
  name: host-firewall-base
spec:
  nodeSelector:
    matchLabels: {}
  enableDefaultDeny:
    ingress: true
    egress: false
  ingress:
    - fromEntities:
        - host        # localhost
        - remote-node # другие ноды
      toPorts:
        - ports:
            - port: "2379"  # etcd
            - port: "6443"  # apiserver
```

### 2. hostNetwork → обычный под

**Инструменты:** 
- `CiliumClusterwideNetworkPolicy` egress (контроль на источнике)
- `NetworkPolicy` ingress (контроль на назначении)

**Проблема:** `from: podSelector` в NetworkPolicy не матчит hostNetwork под (у него node IP)

```yaml
# CiliumClusterwideNetworkPolicy egress (на ноде)
# Разрешает egress внутрь кластера
egress:
  - toEntities:
      - cluster
      - host
      - health
      - remote-node

---
# NetworkPolicy ingress (на обычном поде)
ingress:
  # Вариант 1: разрешить с любого источника на конкретный порт
  - ports:
      - port: 53
  
  # Вариант 2: разрешить только от других обычных подов (hostNetwork не попадёт!)
  - from:
      - podSelector:
          matchLabels:
            app: myapp
    ports:
      - port: 8080
```

### 3. Обычный под → hostNetwork

**Инструмент:** `NetworkPolicy` egress + `CiliumClusterwideNetworkPolicy` ingress

```yaml
# NetworkPolicy egress (на обычном поде)
# ВАЖНО: to: podSelector НЕ РАБОТАЕТ для hostNetwork!
egress:
  - ports:
      - port: 4244  # hubble-peer на cilium-agent
      - port: 6443  # API server

---
# CiliumClusterwideNetworkPolicy ingress (на нодах)
ingress:
  - fromEntities:
      - cluster
    toPorts:
      - ports:
          - port: "4244"
          - port: "6443"
```

### 4. Обычный под ↔ обычный под

**Инструмент:** `NetworkPolicy` (полный контроль)

```yaml
# Egress на источнике
egress:
  - to:
      - podSelector:
          matchLabels:
            app: destination
    ports:
      - port: 8080

# Ingress на назначении
ingress:
  - from:
      - podSelector:
          matchLabels:
            app: source
    ports:
      - port: 8080
```

---

## podSelector и hostNetwork

### Почему podSelector не работает для hostNetwork подов

**Причина:** `podSelector` работает с **pod IP** из pod CIDR. hostNetwork под имеет **node IP**, не pod IP.

### Наглядный пример

```bash
# Обычный под (hostNetwork: false)
kubectl get pod coredns-xxx -o wide
# IP: 10.64.15.241 (из pod CIDR 10.64.0.0/10) ✅

# hostNetwork под (hostNetwork: true)
kubectl get pod cilium-xxx -o wide
# IP: 162.19.249.154 (это NODE IP!) ❌
```

### Практические последствия

```yaml
# ❌ НЕ работает (cilium-agent не будет найден)
egress:
  - to:
      - podSelector:
          matchLabels:
            k8s-app: cilium
    ports:
      - port: 4244

# ✅ Работает (разрешаем порт без указания to)
egress:
  - ports:
      - port: 4244
```

---

## Примеры политик

### Компоненты kube-system

| Компонент | hostNetwork | Контролируется через |
|-----------|-------------|---------------------|
| cilium-agent | `true` | CiliumClusterwideNetworkPolicy |
| cilium-envoy | `true` | CiliumClusterwideNetworkPolicy |
| cilium-operator | `true` | CiliumClusterwideNetworkPolicy |
| kube-apiserver | `true` | CiliumClusterwideNetworkPolicy |
| etcd | `true` | CiliumClusterwideNetworkPolicy |
| kube-controller-manager | `true` | CiliumClusterwideNetworkPolicy |
| kube-scheduler | `true` | CiliumClusterwideNetworkPolicy |
| coredns | `false` | NetworkPolicy |
| hubble-relay | `false` | NetworkPolicy |
| hubble-ui | `false` | NetworkPolicy |

### CiliumClusterwideNetworkPolicy (host-firewall)

```yaml
apiVersion: cilium.io/v2
kind: CiliumClusterwideNetworkPolicy
metadata:
  name: host-firewall-base
spec:
  nodeSelector:
    matchLabels: {}
  
  enableDefaultDeny:
    ingress: true
    egress: true  # Контролируем egress с нод

  ingress:
    # =========================================================================
    # Cluster internal traffic
    # =========================================================================
    - fromEntities:
        - cluster
        - host
        - health
        - remote-node

    # =========================================================================
    # SSH from external
    # =========================================================================
    - fromEntities:
        - world
      toPorts:
        - ports:
            - port: "22"
              protocol: TCP

    # =========================================================================
    # Mail server (SMTP, IMAP, POP3)
    # =========================================================================
    - fromEntities:
        - world
      toPorts:
        - ports:
            # SMTP
            - port: "25"
              protocol: TCP
            - port: "465"
              protocol: TCP
            - port: "587"
              protocol: TCP
            # IMAP
            - port: "143"
              protocol: TCP
            - port: "993"
              protocol: TCP
            # POP3
            - port: "110"
              protocol: TCP
            - port: "995"
              protocol: TCP

    # =========================================================================
    # ICMP for debugging
    # =========================================================================
    - fromEntities:
        - world
      icmps:
        - fields:
            - type: 8

  egress:
    # =========================================================================
    # Cluster internal traffic (free)
    # =========================================================================
    - toEntities:
        - cluster
        - host
        - health
        - remote-node

    # =========================================================================
    # External traffic (HTTP, HTTPS, SSH, DNS, Mail)
    # =========================================================================
    - toEntities:
        - world
      toPorts:
        - ports:
            # Web
            - port: "80"
              protocol: TCP
            - port: "443"
              protocol: TCP
            # SSH
            - port: "22"
              protocol: TCP
            # DNS
            - port: "53"
              protocol: UDP
            - port: "53"
              protocol: TCP
            # SMTP
            - port: "25"
              protocol: TCP
            - port: "465"
              protocol: TCP
            - port: "587"
              protocol: TCP
            # IMAP
            - port: "143"
              protocol: TCP
            - port: "993"
              protocol: TCP
            # POP3
            - port: "110"
              protocol: TCP
            - port: "995"
              protocol: TCP

    # =========================================================================
    # ICMP for debugging
    # =========================================================================
    - toEntities:
        - world
      icmps:
        - fields:
            - type: 8
```

### Разрешённые порты (внешний трафик)

| Порт | Протокол | Назначение |
|------|----------|------------|
| **Web** | | |
| 80 | HTTP | Web трафик |
| 443 | HTTPS | Secure web |
| **SSH** | | |
| 22 | SSH | Управление |
| **DNS** | | |
| 53 | DNS | Резолвинг доменов |
| **SMTP** | | |
| 25 | SMTP | Приём/отправка между серверами |
| 465 | SMTPS | SMTP over SSL |
| 587 | Submission | Отправка от клиентов |
| **IMAP** | | |
| 143 | IMAP | Чтение почты |
| 993 | IMAPS | IMAP over SSL |
| **POP3** | | |
| 110 | POP3 | Скачивание почты |
| 995 | POP3S | POP3 over SSL |

### NetworkPolicy для kube-system

```yaml
# Default deny
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: deny-all
  namespace: kube-system
spec:
  podSelector: {}
  policyTypes:
    - Ingress
    - Egress

# CoreDNS
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-coredns
  namespace: kube-system
spec:
  podSelector:
    matchLabels:
      k8s-app: kube-dns
  policyTypes:
    - Ingress
    - Egress
  ingress:
    - ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
  egress:
    - ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
        - protocol: TCP
          port: 6443

# Hubble Relay
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-hubble-relay
  namespace: kube-system
spec:
  podSelector:
    matchLabels:
      k8s-app: hubble-relay
  policyTypes:
    - Ingress
    - Egress
  ingress:
    - from:
        - podSelector:
            matchLabels:
              k8s-app: hubble-ui
      ports:
        - protocol: TCP
          port: 4245
    - ports:
        - protocol: TCP
          port: 4222
  egress:
    - ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
        - protocol: TCP
          port: 4244

# Hubble UI
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-hubble-ui
  namespace: kube-system
spec:
  podSelector:
    matchLabels:
      k8s-app: hubble-ui
  policyTypes:
    - Ingress
    - Egress
  ingress:
    - ports:
        - protocol: TCP
          port: 8081
  egress:
    - to:
        - podSelector:
            matchLabels:
              k8s-app: hubble-relay
      ports:
        - protocol: TCP
          port: 4245
    - ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
```

---

## Источники

1. **Kubernetes Network Policies**
   - https://kubernetes.io/docs/concepts/services-networking/network-policies/

2. **Cilium Network Policy**
   - https://docs.cilium.io/en/stable/security/policy/

3. **Cilium Host Firewall**
   - https://docs.cilium.io/en/stable/security/host-firewall/

4. **Cilium kube-proxy replacement**
   - https://docs.cilium.io/en/stable/network/kubernetes/kubeproxy-free/

5. **GitHub Issue: Policy enforcement and DNAT**
   - https://github.com/cilium/cilium/issues/12545

6. **Red Hat OpenShift Network Policy**
   - https://docs.redhat.com/en/documentation/openshift_container_platform/4.18/html/network_security/network-policy

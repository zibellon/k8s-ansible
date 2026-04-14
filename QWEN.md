# QWEN.md - Контекст проекта k8s-ansible

## О проекте
Production Kubernetes cluster для bare-metal инфраструктуры. Автоматизация: bootstrap кластера, lifecycle нод, деплой приложений через Ansible + Helm charts.

## Ключевые технологии
- **Kubernetes**: 1.34 (kubeadm)
- **CNI**: Cilium 1.19.1 (eBPF)
- **Ingress**: Traefik v3.6.2 + HAProxy
- **TLS**: CertManager 1.19.2 (Let's Encrypt ACME)
- **Secrets**: Vault 1.21.2 (bank-vaults operator v1.32.1) + External Secrets Operator v1.2.1
- **Storage**: Longhorn
- **Access**: Teleport 18.7.2 (SSH/K8s access platform)
- **CI/CD**: ArgoCD + GitLab + GitLab Runner
- **Monitoring**: Prometheus Operator + Grafana
- **Identity**: ZITADEL

## Структура проекта

```
playbook-system/        # Инфраструктура кластера (init, join, drain, ETCD, HAProxy)
  tasks/                # Переиспользуемые таски
playbook-app/           # Установка приложений (3-фазный паттерн: pre/install/post)
  tasks/                # Переиспользуемые таски
  charts/               # Локальные Helm charts
hosts-vars/             # Базовые переменные/defaults (в git)
hosts-vars-override/    # Runtime секреты, реальные IP, специфика кластера (НЕ в git)
sources/                # Исходники компонентов + официальные Helm charts (git-ignored)
docs/                   # Документация
```

## Директория `sources/` — Исходники и Helm Charts

Здесь хранятся **полные исходники** официальных проектов и их Helm charts. Все пути — **git-ignored** (локальные копии).

### Структура

| Директория | Что внутри | Назначение |
|------------|-----------|-----------|
| **bank-vaults/** | bank-vaults CLI (Go исходники) | Утилита для работы с Vault (unseal, auto-configuration) |
| **bank-vaults-helm-charts/** | Helm chart: `vault/` | Официальный chart bank-vaults (не operator) |
| **bank-vaults-operator-helm/** | Vault Operator (Go + deploy/) | Исходники оператора + RBAC, CRDs |
| **vault-helm-charts/** | HashiCorp official chart | Templates, values.yaml для Vault |
| **cert-manager/** | cert-manager (полные исходники) | Официальный репозиторий jetstack/cert-manager |
| **cilium/** | Cilium (полные исходники) | Официальный репозиторий cilium/cilium |
| **external-secrets/** | ESO (полные исходники) | Официальный репозиторий external-secrets |
| **teleport-charts/** | Teleport (Go + Helm charts) | Официальный репозиторий teleport |
| **traefik-charts/** | Traefik Helm charts | `traefik/`, `traefik-crds/` |
| **haproxy-charts/** | HAProxy Helm charts | `haproxy/`, `haproxy-unified-gateway/` |
| **gitlab-charts/** | GitLab official chart | Полный Helm chart GitLab |
| **gitlab-runner-charts/** | GitLab Runner chart | `templates/`, `Chart.yaml` |
| **longhorn-charts/** | Longhorn charts | `charts/` — зависимости Longhorn |
| **metrics-server-charts/** | Metrics Server | Официальный chart metrics-server |
| **prometheus-community/** | Prometheus community charts | Сборник charts для monitoring stack |
| **prometheus-operator/** | Prometheus Operator (исходники) | Полные исходники + Documentation |
| **grafana-helm-charts/** | Grafana Helm chart | Официальный chart Grafana |
| **node-healthcheck-operator/** | NHC Operator (исходники) | Node Health Check Operator |
| **self-node-remediation/** | SNR (исходники) | Self Node Remediation Operator |

### Как используются

1. **Обновление версий компонентов**
   - Скачать новый релиз → заменить содержимое в `sources/<component>/`
   - Изучить изменения → обновить локальные charts в `playbook-app/charts/`
   - Пример: обновление ArgoCD, Vault, Cilium

2. **Анализ дефолтных конфигов**
   - Посмотреть `sources/<component>/values.yaml` → понять дефолты
   - Сравнить с `playbook-app/charts/<component>/install/values-override.yaml`

3. **Изучение CRDs**
   - `sources/<component>/deploy/crds/` → какие CRDs добавятся
   - Добавить их ожидание в `tasks-wait-crds.yaml`

4. **Понимание внутренней логики**
   - Go исходники (bank-vaults, teleport) → как работает auto-unseal, operator
   - Templates charts → как генерируются ресурсы

### Важно
- **НЕ в git** — это локальные копии для разработки (git-ignored)
- При обновлении компонента → **сначала** обновить `sources/`, **потом** `playbook-app/charts/`
- Для некоторых компонентов (Vault) — charts взяты из `sources/` и переработаны под локальный Helm

## Иерархия переменных

```
hosts-vars/<component>.yaml           ← базовые defaults (в git)
hosts-vars-override/<component>.yaml  ← реальные значения, пароли, IP (НЕ в git)
playbook inline vars                  ← наивысший приоритет
```

---

## Архитектура playbook-ов: Стадии (Tags)

### Зачем разделение на стадии

Каждый playbook установки компонента разбит на **теги (tags)**. Это позволяет:
- Запускать **только нужную стадию** через `--tags <tag>`
- Обновлять отдельные части без переустановки всего
- Контроровать порядок зависимостей (Ingress всегда после всех остальных компонентов)
- Делать точечные исправления (обновить NetworkPolicy, не трогая компонент)

### Основные стадии

```
pre → install → configure → post
```

#### **pre** — Подготовка
**Что ставится:**
- NetworkPolicy (изоляция namespace)
- ESO ресурсы (SecretStore, ServiceAccount)
- RBAC (Role, ClusterRole, ServiceAccount)
- Namespace (create-namespace)

**Когда запускать:**
- При добавлении нового компонента с ESO интеграцией
- При обновлении NetworkPolicy
- Перед install (как подготовка)

```bash
# Только pre
ansible-playbook ... playbook-app/cert-manager-install.yaml --tags pre
```

#### **install** — Установка компонента
**Что ставится:**
- Основной компонент (Deployment, StatefulSet, DaemonSet)
- CRDs (через helm или отдельно)
- ConfigMaps, Services

**Когда запускать:**
- Первоначальная установка компонента
- Обновление версии (новый Helm chart)
- Обновление конфигурации

```bash
# Только install
ansible-playbook ... playbook-app/cert-manager-install.yaml --tags install
```

#### **configure** — Конфигурация внутри компонента
**Что делает:**
- Настройка внутренних параметров компонента
- Создание пользователей, ролей, политик
- Инициализация данных через API/Operator

**Когда запускать:**
- После install для первичной настройки
- При изменении конфигурации (новые роли в Teleport, новые политики в Vault)
- Не требует перезапуска компонента

```bash
# Только configure
ansible-playbook ... playbook-app/teleport-install.yaml --tags configure
```

#### **post** — Ingress (всегда последняя стадия)
**Что ставится:**
- Ingress (Traefik или HAProxy)
- Certificate (cert-manager)
- External DNS (если используется)

**Почему всегда последний:**
- Ingress зависит от ClusterIssuer (должен быть готов cert-manager)
- Ingress зависит от работающего сервиса (должен быть готов компонент)
- Certificate зависит от Ingress resource

```bash
# Только post (Ingress)
ansible-playbook ... playbook-app/vault-install.yaml --tags post
```

### Специальные теги (разновидности install/configure)

Некоторые компоненты имеют **дополнительные/разделённые** теги для более гранулярного контроля:

#### **Vault: `operator` + `vault-cr`**
```
pre → operator → vault-cr → post
```
- **operator** — bank-vaults operator (OCI Helm chart)
- **vault-cr** — Vault Custom Resource (StatefulSet + sidecars)

```bash
# Только operator
ansible-playbook ... playbook-app/vault-install.yaml --tags operator

# Только Vault CR
ansible-playbook ... playbook-app/vault-install.yaml --tags vault-cr
```

**Зачем разделение:**
- operator ставится один раз на весь кластер
- vault-cr можно пересоздать без переустановки operator
- operator ждёт CRDs → vault-cr ждёт operator

#### **ArgoCD: `crds` (отдельно)**
```
crds → pre → install → post
```
- **crds** — CRDs ArgoCD (~24k строк, ставятся отдельно)

```bash
# Только CRDs
ansible-playbook ... playbook-app/argocd-install.yaml --tags crds
```

**Зачем разделение:**
- CRDs тяжёлые, могут ставиться долго
- При обновлении — CRDs обновляются отдельно от ресурсов
- Можно проверить совместимость CRDs перед обновлением

#### **Teleport: `configure` (CRDs sync)**
```
pre → install → post → configure
```
- **configure** — все CRD через Teleport Operator (Roles, Users, Apps, Databases, и т.д.)

```bash
# Только CRDs sync
ansible-playbook ... playbook-app/teleport-install.yaml --tags configure
```

**Зачем разделение:**
- configure можно запускать многократно при изменении ролей/пользователей
- Не требует переустановки компонента
- Operator работает только CRD → Teleport (односторонняя синхронизация)

#### **Prometheus Operator: `prometheus` + `alertmanager`**
```
crds → pre → install (prometheus + alertmanager)
```
- Установка разделена внутри install на Prometheus и Alertmanager
- Можно установить только Prometheus, без Alertmanager
- Можно обновить отдельно Prometheus или Alertmanager

```bash
# Весь install (prometheus + alertmanager)
ansible-playbook ... playbook-app/mon-prometheus-operator-install.yaml --tags install

# Можно управлять через переменные (что включать/отключать)
```

### Таблица: Все теги по компонентам

| Компонент | Теги (порядок) | Специальные |
|-----------|---------------|-------------|
| cert-manager | pre → install → post | — |
| external-secrets | pre → install | — |
| cilium | pre → install → post | pre/post ставятся ОТДЕЛЬНО от install |
| traefik | pre → install → post | — |
| haproxy | pre → install → post | — |
| metrics-server | pre → install | — |
| **vault** | pre → **operator** → **vault-cr** → post | operator, vault-cr |
| **teleport** | pre → install → post → **configure** | configure (CRDs) |
| **argocd** | **crds** → pre → install → post | crds, separate: argocd-configure.yaml |
| **prometheus-operator** | crds → pre → install → post | ~80k строк CRDs |
| longhorn | pre → install → post | separate: longhorn-tags-sync.yaml |
| gitlab | pre → install → post | separate: gitlab-configure.yaml |
| gitlab-runner | pre → install | — |
| grafana | pre → install → post | — |
| **argocd-git-ops** | pre → install → post | ESO + AppProject/Application |
| **node-exporter** | pre → install | — |
| **kube-state-metrics** | pre → install | — |
| zitadel | pre → install | NOT_READY |
| medik8s | — | NOT_READY |

### Типичные сценарии использования

#### **Сценарий 1: Добавить ESO интеграцию для существующего компонента**
```bash
# 1. Обновить политики/роли в Vault
vault_policies_extra: [...]
vault_roles_extra: [...]
ansible-playbook ... playbook-app/vault-policy-sync.yaml

# 2. Добавить ExternalSecret
eso_vault_integration_XXX_secrets_extra: [...]

# 3. Только pre (ESO ресурсы) — не переустанавливать весь компонент
ansible-playbook ... playbook-app/component-install.yaml --tags pre
```

#### **Сценарий 2: Обновить Ingress для компонента**
```bash
# Изменить конфиг Ingress в hosts-vars-override/

# Только post (Ingress) — не трогать компонент
ansible-playbook ... playbook-app/vault-install.yaml --tags post
```

#### **Сценарий 3: Обновить CRDs без переустановки компонента**
```bash
# Только CRDs
ansible-playbook ... playbook-app/argocd-install.yaml --tags crds

# Перезапустить компонент (если нужно)
ansible-playbook ... playbook-app/argocd-restart.yaml
```

#### **Сценарий 4: Добавить роли в Teleport без переустановки**
```bash
# Добавить в hosts-vars-override/
teleport_configure_roles_extra: [...]

# Только configure (CRDs sync)
ansible-playbook ... playbook-app/teleport-install.yaml --tags configure
```

#### **Сценарий 5: Обновить версию компонента**
```bash
# 1. Изменить версию в hosts-vars/
component_version: "X.Y.Z"

# 2. Полный install (обновит через helm upgrade)
ansible-playbook ... playbook-app/component-install.yaml --tags install

# 3. Если Ingress изменился — post
ansible-playbook ... playbook-app/component-install.yaml --tags post

# 4. Если нужен restart
ansible-playbook ... playbook-app/component-restart.yaml
```

### Компоненты с отдельными configure playbook-ами

Некоторые компоненты имеют **отдельный configure playbook** (не tag!):

| Компонент | Configure Playbook | Что делает |
|-----------|-------------------|-----------|
| **GitLab** | `gitlab-configure.yaml` | Достать пароль админа → положить в Vault → создать PersonalAccessToken → положить в Vault |
| **ArgoCD** | `argocd-configure.yaml` | Сбросить права default-project → достать пароль admin → положить в Vault |
| **Teleport** | `--tags configure` | CRDs sync через Operator (Roles, Users, Apps, и т.д.) |
| **Longhorn** | `longhorn-tags-sync.yaml` | Синхронизация node-tags на Nodes (CRD: nodes.longhorn.io) |

### Правила порядка запуска

1. **pre всегда до install** (NetworkPolicy, ESO должны быть готовы до компонента)
2. **install до configure** (компонент должен работать до конфигурации)
3. **configure до post** (сервис должен работать до создания Ingress)
4. **post всегда последний** (Ingress зависит от работающего сервиса + cert-manager)
5. **crds (если есть) самый первый** (CRDs должны быть до любого ресурса)

---

## Стандартные команды запуска

### System playbooks
```bash
# Информация о нодах
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/node-info.yaml

# Инициализация ноды (на всех или конкретной)
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/node-install.yaml
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/node-install.yaml --limit k8s-manager-1

# Инициализация кластера (СТРОГО на master-manager)
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/cluster-init.yaml --limit k8s-manager-1

# Join нод
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/worker-join.yaml --limit k8s-worker-1
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/manager-join.yaml --limit k8s-manager-2
```

### App playbooks
```bash
# Установка компонента (все фазы)
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/<component>-install.yaml

# Отдельные фазы
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/<component>-install.yaml --tags pre
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/<component>-install.yaml --tags install
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/<component>-install.yaml --tags post

# Перезапуск (если нет автоподхвата конфигов)
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/<component>-restart.yaml
```

## Порядок bootstrap кластера

1. `node-install.yaml --limit <node>` — подготовка каждой ноды (SSH, OS, packages, kubeadm, HAProxy)
2. `cluster-init.yaml --limit <master>` — инициализация первого manager (ETCD encryption + kubeadm init)
3. `manager-join.yaml --limit <new-manager>` — подключение дополнительных managers
4. `worker-join.yaml --limit <worker>` — подключение workers
5. `cilium-install.yaml` — CNI (обязательно перед всем остальным)
6. `cert-manager-install.yaml` — TLS (обязательно перед ingress)
7. `traefik-install.yaml` — ingress controller
8. `vault-install.yaml` — secrets management
9. `external-secrets-install.yaml` — ESO
10. Остальные приложения в любом порядке

**ВАЖНО**: Перед добавлением новой ноды → запустить `cilium-install.yaml --tags post` (обновляет Cilium host firewall)

## Критичные моменты

### Режим Single (НЕ HA)
- **ВСЕ компоненты** запускаются в режиме **Single** (1 реплика)
- **НЕ использовать** High Available режим
- Если есть возможность — настроить `updateStrategy: Recreate`
- `replica_count: 1` для всех компонентов (cert-manager, external-secrets, vault-operator, и т.д.)

### Namespace ограничения
- `longhorn-system` — НЕЛЬЗЯ менять (ограничение upstream)
- `argocd` — НЕЛЬЗЯ менять (ограничение upstream)

### Cilium firewall
- **ВАЖНО**: Cilium install может зависнуть на `TASK [[cilium-post] Install or upgrade via Helm]`
  - Причина: когда Cilium берёт под контроль сеть на node — все соединения обрываются (в том числе SSH)
  - Решение: Ctrl+C (остановить процесс) + запустить заново
- **Установка cilium**: изначально только `--tags install`
  - pre + post ставятся **ПОЗЖЕ**, после cert-manager, ESO, Traefik, HAproxy
  - Команда: `ansible-playbook ... cilium-install.yaml --tags pre,post`
- Запускать `cilium-install.yaml --tags post` ПЕРЕД добавлением новой ноды
  - Иначе join завершится по timeout (Cilium блокирует трафик от неизвестных нод)
  - `--tags post` обновляет cilium-host-firewall с новыми IP адресами

### is_master флаг
- Ровно ОДИН manager должен иметь `is_master: true` в inventory
- Это становится `master_manager_fact`

### ETCD encryption
- Автогенерация при cluster-init: `/etc/kubernetes/pki/encryption-config.yaml`
- Копируется на joining managers автоматически через `manager-join.yaml`

### kubeadm config
- Конфиг вынесен в `hosts-vars/kubeadm-config.yaml` (переменная `kubeadm_config_template`)
- `/etc/kubernetes/kubeadm-config.yaml` генерируется из шаблона
- Eviction: `kubelet_eviction_soft`, `kubelet_eviction_hard` — dict переменные (без if/else)
- `cert_sans` собирается динамически из inventory

### Vault credentials
- Хранятся: `/etc/kubernetes/vault-unseal.json` на всех managers
- Копируются автоматически при `manager-join.yaml`

### Kube-proxy
- Отключён при init — Cilium eBPF берёт на себя service routing

### HAProxy LB
- systemd service на всех нодах: `127.0.0.1:16443`
- Обновление с `serial: 1` для HA
- **haproxy-apiserver-lb**: балансировка между всеми manager-node
  - Версия пакета заморожена через `apt-mark hold` (`haproxy_apiserver_lb_package_version`)
  - **Конфиг** вынесен в `hosts-vars/haproxy-apiserver-lb.yaml` (переменная `haproxy_apiserver_lb_config_template`)
  - Placeholder `__MANAGER_SERVER_IP_LIST__` — **НЕ удалять**! На это место подставляются IP manager нод
  - Чтобы переопределить: создать `hosts-vars-override/haproxy-apiserver-lb.yaml` и переопределить шаблон целиком, сохранив placeholder
  - Обновление конфига: `ansible-playbook ... playbook-system/haproxy-apiserver-lb-update.yaml`
  - Graceful reload (`systemctl reload haproxy`), без разрыва TCP соединений
  - При добавлении нового manager: обновить SANS → haproxy-apiserver-lb → node-install → manager-join

### project_root — Запуск из корня проекта
- **ВСЕ запуски** делать из директории проекта (где лежит README.md)
- `project_root: "{{ lookup('env', 'PWD') }}"` — используется во всей логике playbook-ов
- Если запустить из другой директории — пути к charts окажутся неверными

---

## Async Helm Upgrades

### Зачем
Все `helm upgrade --install` команды обёрнуты в async wrapper для устойчивости к обрывам SSH. Особенно критично для тяжёлых компонентов (GitLab 8-10+ минут).

### Как работает
```yaml
# В playbook-е:
- name: "[component] Install via Helm (async)"
  include_tasks: tasks/tasks-helm-upgrade-async.yaml
  vars:
    label_name: "component"
    helm_command: >
      helm upgrade --install ...
  tags: [install]

# Внутри wrapper (tasks-helm-upgrade-async.yaml):
# 1. command: {{ helm_command }}  async: {{ helm_async_timeout }}  poll: {{ helm_async_poll }}
# 2. async_status: jid: ...  until: finished  retries: ...  delay: ...
# 3. fail: если не finished
```

### Параметры (hosts-vars/k8s-base.yaml)
```yaml
helm_async_timeout: 1800    # 30 минут макс время
helm_async_poll: 5          # 5 секунд интервал
helm_async_retries: 360     # кол-во проверок
helm_async_delay: 5         # 5 секунд задержка
```

### Особенности
- `delegate_to: master_manager_fact` — жёстко внутри wrapper (НЕ передавать снаружи)
- `run_once: true` — жёстко внутри wrapper (НЕ передавать снаружи)
- Передавать только `label_name` + `helm_command`
- ВСЕ 60 helm upgrade команд во всех playbook-ах используют async

### Отладка async
Если async задача зависла:
```bash
# Проверить статус async задачи
kubectl get pods -n <namespace>
helm list -n <namespace>

# Если helm upgrade всё ещё выполняется — подождать
# Если упало — проверить логи wrapper задачи в ansible output
```

---

## Файлы runtime (на нодах)

| Путь | Назначение |
|------|-----------|
| `/etc/kubernetes/kubeadm-config.yaml` | Конфигурация кластера |
| `/etc/kubernetes/pki/encryption-config.yaml` | Ключи шифрования ETCD |
| `/etc/kubernetes/vault-unseal.json` | Ключи Vault unseal + root token (только managers) |
| `/etc/haproxy/haproxy.cfg` | Конфиг LB для API server |
| `/root/.kube/config` | kubectl config |

---

## CertManager (cert-manager)

### Назначение
Автоматическая выдача TLS сертификатов через Let's Encrypt (ACME protocol).

### Архитектура
- **Источник**: Официальный Helm chart (`jetstack/cert-manager`)
- **Компоненты**: `cert-manager`, `cert-manager-cainjector`, `cert-manager-webhook`
- **Namespace**: `cert-manager` (можно менять)

### Фазы установки
1. **pre** — NetworkPolicies для namespace cert-manager
2. **install** — Официальный Helm chart + CRDs
3. **post** — ClusterIssuer (Let's Encrypt ACME solver)

### Конфигурация
```yaml
# hosts-vars/cert-manager.yaml
cert_manager_cluster_issuers:
  - name: "acme-prod"
    email: "some-email@gmail.com"
    server: "https://acme-v02.api.letsencrypt.org/directory"
    privateKeySecretName: "cluster-issuer-acme-prod-account-key"
    solvers:
      - ingressClass: "traefik-lb"
        ingressAnnotations: ...
        podLabels: ...
```

### Ключевые моменты
- **CRDs ждут**: `tasks-wait-crds.yaml` — 6 CRD (Certificate, CertificateRequest, ClusterIssuer, Issuer, Challenge, Order)
- **Rollout ждут**: 3 deployment (cert-manager, cainjector, webhook)
- **ServiceMonitor**: автоматическое создание для Prometheus (включено по умолчанию)
- **ClusterIssuer**: привязан к ingress class (traefik-lb или haproxy-lb)
- **ACME solver**: HTTP-01 challenge через ingress annotations

### Команды
```bash
# Установка/обновление
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/cert-manager-install.yaml

# Отдельные фазы
ansible-playbook ... --tags pre
ansible-playbook ... --tags install
ansible-playbook ... --tags post  # ClusterIssuer
```

---

## External Secrets Operator (ESO)

### Назначение
Синхронизация секретов из Vault → Kubernetes Secrets автоматически.

### Архитектура
- **Источник**: Официальный Helm chart (`external-secrets/external-secrets`)
- **Компоненты**: `external-secrets`, `external-secrets-webhook`, `external-secrets-cert-controller`
- **Namespace**: `external-secrets` (можно менять)
- **CRDs**: 24+ CRD (ExternalSecret, SecretStore, ClusterSecretStore, PushSecret, Generators)

### Фазы установки
1. **pre** — NetworkPolicies
2. **install** — Официальный Helm chart (--skip-crds, CRDs ставятся отдельно)

### Конфигурация
```yaml
# hosts-vars/external-secrets.yaml
external_secrets_image_tag: "v1.2.1"
eso_controller_config:
  client_burst: 200
  concurrent: 10
  enable_managed_secrets_caching: false
  store_requeue_interval: "1m0s"
```

### Как работает ESO + Vault интеграция
```
Vault (KV v2) → SecretStore (CRD) → ExternalSecret (CRD) → k8s Secret
                     ↑                      ↑
              SA + Role (Vault)       Vault Policy
```

**Структура на каждый компонент:**
1. **ServiceAccount** — в namespace компонента
2. **SecretStore** — CRD, подключается к Vault через Kubernetes Auth
3. **ExternalSecret** — CRD, забирает секрет из Vault → k8s Secret

**Переменные для интеграции:**
```yaml
eso_vault_integration_traefik:
  sa_name: "eso-main"                    # SA в namespace traefik-lb
  role_name: "traefik.eso-main"          # Role в Vault
  secret_store_name: "eso-main.vault"    # SecretStore CRD name
  kv_engine_path: "eso-secret"           # KV engine в Vault

eso_vault_integration_traefik_secrets:   # ExternalSecrets
  - external_secret_name: "eso-xxx"
    target_secret_name: "k8s-secret-xxx"
    vault_path: "/traefik/xxx"
    type: "default"
    is_need_eso: true  # false = только Vault, без k8s Secret
```

### ESO Merge & Validation
**Файл**: `playbook-app/tasks/tasks-eso-merge.yaml`

**Что делает:**
1. Объединяет `vault_policies + vault_policies_extra` → `vault_policies_final`
2. Объединяет `vault_roles + vault_roles_extra` → `vault_roles_final`
3. Проверяет дубликаты (policy names, role names)
4. Проверяет, что все policies из ролей существуют
5. Объединяет секреты для каждого компонента (`*_secrets + *_secrets_extra` → `*_secrets_merged`)
6. Проверяет уникальность `external_secret_name` и `target_secret_name`
7. Проверяет, что SecretStore → Role → Policies существуют

**Вызывается в:** vault-install.yaml, все *-install.yaml (traefik, haproxy, longhorn, gitlab, argocd, zitadel, grafana)

### Force Sync
```bash
# Перезапустить синхронизацию всех ExternalSecrets
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/eso-force-sync.yaml

# Только для конкретного namespace
ansible-playbook ... --tags gitlab
ansible-playbook ... --tags argocd
ansible-playbook ... --tags vault
```

### Компоненты с ESO интеграцией
- traefik, haproxy, longhorn, zitadel
- gitlab, gitlab-runner
- argocd, argocd-git-ops (два отдельных SA в одном namespace!)
- grafana

### Команды
```bash
# Установка
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/external-secrets-install.yaml

# Перезапуск
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/external-secrets-restart.yaml
```

---

## Vault (Bank-vaults Operator)

### Назначение
Управление секретами, policies, Kubernetes auth roles. Хранение всех секретов для ESO.

### Архитектура
- **Оператор**: bank-vaults (`oci://ghcr.io/bank-vaults/helm-charts/vault-operator`)
- **Vault CR**: playbook-app/charts/vault/cr/ (Custom Resource)
- **Хранилище**: Raft backend (PVC через Longhorn)
- **Namespace**: `vault` (можно менять)
- **Версии**: Vault 1.21.2, bank-vaults v1.32.1
- **⚠️ Региональная блокировка**: Официальный helm HashiCorp не работает из РФ
  - Решение: github.com/hashicorp/vault-helm → Releases → скачать ZIP → достать templates, Chart.yaml, values.yaml
  - RBAC от bank-vaults operator ставится отдельно (через kustomize → перенесено в local helm)

### Фазы установки
1. **pre** — NetworkPolicies, ESO resources
2. **operator** — bank-vaults operator через OCI Helm chart
3. **vault-cr** — Vault CR (StatefulSet + sidecars: vault + bank-vaults unsealer)
4. **post** — Ingress (Vault UI)

### Как работает bank-vaults
```
1. vault-operator → создает CRD: vaults.vault.banzaicloud.com
2. Vault CR → оператор создает StatefulSet (vault-0)
3. bank-vaults sidecar → auto-unseal через K8s Secret
4. K8s Secret (vault-unseal-keys) → хранит unseal keys + root token
5. Распределение → /etc/kubernetes/vault-unseal.json на всех managers
```

### Конфигурация
```yaml
# hosts-vars/vault.yaml
vault_key_shares: 3        # Сколько частей разделить unseal key
vault_key_threshold: 2     # Сколько нужно для unseal

vault_storage_class: "lh-major-single-best-effort"
vault_storage_size: "2Gi"

# Vault Policies (HCL)
vault_policies:
  - name: traefik.eso-main
    rules: |
      path "eso-secret/data/traefik/*" { capabilities = ["read", "list"] }
      path "eso-secret/metadata/traefik/*" { capabilities = ["read", "list"] }

# Vault Roles (Kubernetes Auth)
vault_roles:
  - name: traefik.eso-main
    bound_service_account_namespaces: traefik-lb
    bound_service_account_names: eso-main
    policies: [traefik.eso-main]
    ttl: "1h"
```

### Vault Distribute Credentials
**Файл**: `playbook-app/tasks/tasks-vault-distribute-creds.yaml`

**Что делает:**
1. Читает K8s Secret `vault-unseal-keys` → декодирует base64
2. Распределяет на все manager ноды → `/etc/kubernetes/vault-unseal.json`
3. Формат: `{"vault-root": "...", "vault-unseal-0": "...", "vault-unseal-1": "..."}`

**Вызывается в:** vault-install.yaml, vault-rotate.yaml, manager-join.yaml

### Vault Rotate (Rekey)
**Файл**: `playbook-app/vault-rotate.yaml`

**Что делает:**
1. Проверяет статус Vault (initialized, unsealed)
2. Читает текущие unseal keys из K8s Secret
3. Инициализирует rekey (`vault operator rekey -init`)
4. Подает текущие ключи → получает новые
5. Заменяет K8s Secret новыми ключами
6. Распределяет на все managers

```bash
# Rotate с параметрами по умолчанию (из hosts-vars)
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/vault-rotate.yaml

# Переопределить параметры
ansible-playbook ... -e vault_rekey_shares=5 -e vault_rekey_threshold=3
```

### Vault Policies & Roles — Структура

**Policy** (HCL rules):
- `name` — уникальное в рамках всего Vault
- `rules` — path + capabilities (read, list, create, update, delete)

**Role** (Kubernetes Auth):
- `name` — уникальное в рамках всего Vault
- `bound_service_account_namespaces` — какие namespace могут auth
- `bound_service_account_names` — какие SA могут auth
- `policies` — список политик
- `ttl` — время жизни токена

**SecretStore** (ESO CRD):
- `sa_name` — ServiceAccount для подключения
- `role_name` — Vault role для auth
- `secret_store_name` — имя CRD
- `kv_engine_path` — путь к KV engine

**ExternalSecret** (ESO CRD):
- `external_secret_name` — имя ExternalSecret ресурса
- `target_secret_name` — имя k8s Secret, который будет создан
- `vault_path` — путь к секретам в Vault
- `type` — тип секрета (default, postgresql, redis, minio_root, и т.д.)
- `is_need_eso` — создавать ли ESO ресурсы (false = только Vault)

### Паттерн добавления нового компонента с ESO

**Пример 1: Добавить новые k8s.secrets для gitlab**
```bash
# 1. Добавить в hosts-vars-override/
eso_vault_integration_gitlab_secrets_extra:
  - external_secret_name: "eso-gitlab-xxx"
    target_secret_name: "k8s-gitlab-xxx"
    vault_path: "/gitlab/xxx"
    type: "default"

# 2. Вызвать
ansible-playbook ... playbook-app/gitlab-install.yaml --tags pre
```

**Пример 2: Добавить секреты из новых путей Vault**
```bash
# 1. Добавить политику
vault_policies_extra:
  - name: gitlab.eso-extra
    rules: |
      path "eso-secret/data/gitlab/extra/*" { capabilities = ["read", "list"] }

# 2. Добавить роль
vault_roles_extra:
  - name: gitlab.eso-extra
    bound_service_account_namespaces: gitlab
    bound_service_account_names: eso-main
    policies: [gitlab.eso-extra]
    ttl: "1h"

# 3. Синхронизировать Vault
ansible-playbook ... playbook-app/vault-policy-sync.yaml

# 4. Добавить ExternalSecret
eso_vault_integration_gitlab_secrets_extra:
  - external_secret_name: "eso-gitlab-extra"
    vault_path: "/gitlab/extra"
    type: "default"

# 5. Применить
ansible-playbook ... playbook-app/gitlab-install.yaml --tags pre
```

### Vault UI
- **URL**: `https://vault-k8s-v2.drawapp.ru` (настраивается через `vault_ui_domain`)
- **Ingress**: traefik-lb (ACME HTTPS)
- **VPN-only**: `vault_ui_vpn_only_enabled` (доступ только из VPN)

### Команды
```bash
# Установка + конфигурация
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/vault-install.yaml

# Отдельные фазы
ansible-playbook ... --tags pre
ansible-playbook ... --tags operator
ansible-playbook ... --tags vault-cr
ansible-playbook ... --tags post

# Rotate unseal keys
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/vault-rotate.yaml
```

---

## Teleport

### Назначение
Платформа управления доступом: SSH, Kubernetes, Databases, Applications, Web UI.

### Архитектура
- **Источник**: Официальный Helm chart (`teleport/teleport-cluster`)
- **Режим**: standalone (SQLite backend на Longhorn PVC)
- **Компоненты**: `teleport-auth`, `teleport-proxy`, `teleport-operator`
- **Namespace**: `teleport` (можно менять)
- **Версия**: chart 18.7.2

### Фазы установки
1. **pre** — ESO resources, NetworkPolicies
2. **install** — Официальный Helm chart (auth + proxy + operator)
3. **post** — Ingress (Traefik HTTPS) + HAProxy TCP (SSH + tunnel ports)
4. **configure** — Все CRD через Teleport Operator (Roles, Users, Apps, и т.д.)

### Порты
| Порт | Назначение |
|------|-----------|
| 3080 | Web UI (внутри кластера) |
| 443 | Web UI (public, через ingress) |
| 3023 | SSH proxy |
| 3024 | Reverse tunnel |
| 3025 | Auth ↔ Proxy internal |
| 3026 | Kubernetes API |

### Конфигурация
```yaml
# hosts-vars/teleport.yaml
teleport_cluster_name: "{{ teleport_domain }}"  # IMMUTABLE!
teleport_kube_cluster_name: "drawapp-k8s-v2"

teleport_data_storage_class: "lh-major-single-best-effort"
teleport_data_storage_size: "10Gi"
```

```yaml
# hosts-vars/teleport-configure.yaml
teleport_configure_roles:
  - name: superadmin
    spec:
      allow:
        logins: [root, ubuntu, admin]
        kubernetes_groups: [system:masters]
        kubernetes_resources:
          - kind: '*'
            name: '*'
            namespace: '*'
            verbs: ['*']

teleport_configure_users:
  - name: superadmin
    spec:
      roles: [superadmin]
      traits:
        logins: [root, ubuntu, admin]
        kubernetes_groups: [system:masters]
```

### CRDs через Operator
**Все ресурсы управляются через CRD (Operator → Teleport API):**
- Roles (TeleportRoleV8), Users (TeleportUser), Bots (TeleportBotV1)
- ProvisionTokens, Apps, Databases
- GitHub/OIDC/SAML Connectors
- AccessLists, TrustedClusters, LoginRules
- WorkloadIdentities, InferenceModels/Policies/Secrets
- AutoUpdate Configs/Versions

**Паттерн расширения:**
```yaml
# hosts-vars-override/teleport-configure.yaml
teleport_configure_roles_extra:
  - name: developer
    spec:
      allow:
        logins: [developer]
        node_labels: {'*': ['*']}
```

### Важные нюансы
⚠️ **teleport_cluster_name** — IMMUTABLE после первого деплоя!
⚠️ **CRDs wait**: После Helm install — playbook ждёт CRDs через `tasks-wait-crds.yaml` (20+ CRD)
  - Место: после Helm install, **до** rollout wait
  - CRDs: roles, users, apps, databases, bots, connectors, и т.д.
⚠️ **Operator certificate expiry** — сертификат на 1 час, healthcheck НЕ отслеживает
  - Симптом: `tls: expired certificate`, `broken pipe`
  - Лечение: `kubectl rollout restart deployment/teleport-operator -n teleport`
⚠️ **MFA=OTP** — для консоли работает только OTP (PassKey не работает)
⚠️ **Operator direction** — только CRD → Teleport. UI changes НЕ сохраняются

### Доступ после установки
```bash
# Получить ссылку для сброса пароля superadmin
kubectl exec -n teleport deploy/teleport-auth -- tctl users reset superadmin

# Проверить роли
kubectl exec -n teleport deploy/teleport-auth -- tctl get role/superadmin

# Список пользователей
kubectl exec -n teleport deploy/teleport-auth -- tctl users ls
```

### Teleport SSH Agent
**Файл**: `playbook-app/teleport-ssh-agent-install.yaml`
- Устанавливается на КАЖДУЮ ноду кластера
- Позволяет доступ к нодам через Teleport

### Команды
```bash
# Установка + конфигурация
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/teleport-install.yaml

# Отдельные фазы
ansible-playbook ... --tags pre
ansible-playbook ... --tags install
ansible-playbook ... --tags post
ansible-playbook ... --tags configure  # CRDs sync

# SSH Agent на все ноды
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/teleport-ssh-agent-install.yaml
```

---

## Image Registry + Tag (AirGapped support)

### Зачем
Подстановка собственного registry для всех образов (AirGapped режим — без доступа в интернет).

### Переменные (в hosts-vars/<component>.yaml)

**Один образ на компонент (большинство):**
| Компонент | Registry | Repository | Tag |
|-----------|----------|-----------|-----|
| **traefik** | `ghcr.io` | traefik/traefik | v3.6.12 |
| **teleport** | `public.ecr.aws` | gravitational/teleport | 18.7.2 |
| **vault** | `docker.io` | hashicorp/vault | 1.21.2 |
| **gitlab-runner** | `registry.gitlab.com` | gitlab-org/gitlab-runner | alpine-v18.3.0 |
| **haproxy** | `docker.io` | haproxytech/haproxy-alpine | 3.3.6 |
| **metrics-server** | `registry.k8s.io` | metrics-server/metrics-server | v0.8.0 |
| **zitadel** | `ghcr.io` | zitadel/zitadel | 2.77.3 |

**GitLab (global.image — registry + tag, repository внутренне):**
| Компонент | Registry | Tag |
|-----------|----------|-----|
| **gitlab** | `registry.gitlab.com` | 18.3.3 |

**ArgoCD (repository = полный путь + sub-компоненты):**
| Компонент | withRegistry | Tag |
|-----------|----------|-----|
| **argocd (main)** | `quay.io/argoproj/argocd` | v3.2.3 |
| **dex-server** | `ghcr.io/dexidp/dex` | v2.41.1 |
| **redis** | `redis` | 7.2.7-alpine |

**Cilium (6 отдельных образов):**
| Компонент | Registry | Repository | Tag |
|-----------|----------|-----------|-----|
| agent | `quay.io` | cilium/cilium | v1.19.1 |
| operator | `quay.io` | cilium/operator | v1.19.1 |
| envoy | `quay.io` | cilium/cilium-envoy | v1.36.5 |
| hubble-relay | `quay.io` | cilium/hubble-relay | v1.19.1 |
| hubble-ui-backend | `quay.io` | cilium/hubble-ui-backend | v0.13.3 |
| hubble-ui-frontend | `quay.io` | cilium/hubble-ui | v0.13.3 |

**Longhorn (13 отдельных образов — 7 longhorn + 6 csi):**
| Компонент | Registry | Repository | Tag |
|-----------|----------|-----------|-----|
| engine | `docker.io` | longhornio/longhorn-engine | v1.11.0 |
| manager | `docker.io` | longhornio/longhorn-manager | v1.11.0 |
| ui | `docker.io` | longhornio/longhorn-ui | v1.11.0 |
| instanceManager | `docker.io` | longhornio/longhorn-instance-manager | v1.11.0 |
| shareManager | `docker.io` | longhornio/longhorn-share-manager | v1.11.0 |
| backingImageManager | `docker.io` | longhornio/backing-image-manager | v1.11.0 |
| supportBundleKit | `docker.io` | longhornio/support-bundle-kit | v0.0.79 |
| csi-attacher | `docker.io` | longhornio/csi-attacher | v4.10.0-20251226 |
| csi-provisioner | `docker.io` | longhornio/csi-provisioner | v5.3.0-20251226 |
| csi-node-driver-registrar | `docker.io` | longhornio/csi-node-driver-registrar | v2.15.0-20251226 |
| csi-resizer | `docker.io` | longhornio/csi-resizer | v2.0.0-20251226 |
| csi-snapshotter | `docker.io` | longhornio/csi-snapshotter | v8.4.0-20251226 |
| csi-livenessProbe | `docker.io` | longhornio/livenessprobe | v2.17.0-20251226 |

**cert-manager (3 отдельных образа):**
| Компонент | Registry | Repository | Tag |
|-----------|----------|-----------|-----|
| controller | `quay.io` | jetstack/cert-manager-controller | v1.20.2 |
| cainjector | `quay.io` | jetstack/cert-manager-cainjector | v1.20.2 |
| webhook | `quay.io` | jetstack/cert-manager-webhook | v1.20.2 |

**Уже покрыты:** external-secrets, grafana, node-exporter, kube-state-metrics

### Как переопределить для AirGapped
```yaml
# hosts-vars-override/<component>.yaml
<component>_image_registry: "my-registry.local:5000"
<component>_image_repository: "my-image"
<component>_image_tag: "X.Y.Z"
```

### Паттерн в playbook values
```yaml
image:
  repository: "{{ <component>_image_registry }}/{{ <component>_image_repository }}"
  tag: "{{ <component>_image_tag }}"
```

---

## Как проверять image версии компонентов

### Где смотреть официальные версии:
1. **Локальные sources/** — `sources/<component>/Chart.yaml` (`version` = chart version, `appVersion` = image tag)
2. **Artifact Hub** — https://artifacthub.io (поиск по chart name)
3. **Официальный GitHub** — releases раздела chart

### Что проверять:
1. **Chart version** (в hosts-vars: `<component>_chart_version` или `<component>_version`)
   - Должна совпадать с `Chart.yaml` → `version`
2. **Image tag** (в hosts-vars: `<component>_image_tag`)
   - Должна совпадать с `Chart.yaml` → `appVersion`
3. **Image repository** (в hosts-vars: `<component>_image_repository`)
   - Должна совпадать с `sources/<component>/values.yaml` → `image.repository`

### Команда для быстрой проверки:
```bash
# Сравнить hosts-vars с официальным chart
cat hosts-vars/<component>.yaml | grep image_tag
cat sources/<component>/Chart.yaml | grep appVersion
cat sources/<component>/values.yaml | grep -A2 'image:'
```

### Таблица источников для проверки:
| Компонент | Chart файл | Values файл | Chart version var | Image tag var |
|-----------|-----------|-------------|-------------------|---------------|
| cert-manager | sources/cert-manager/Chart.yaml | values.yaml | cert_manager_version | cert_manager_image_tag |
| cilium | sources/cilium/Chart.yaml | values.yaml | cilium_version | cilium_*_image_tag |
| traefik | sources/traefik-charts/Chart.yaml | values.yaml | traefik_chart_version | traefik_image_tag |
| longhorn | sources/longhorn-charts/Chart.yaml | values.yaml | longhorn_chart_version | longhorn_*_image_tag |
| gitlab-runner | sources/gitlab-runner-charts/Chart.yaml | values.yaml | gitlab_runner_chart_version | gitlab_runner_image_tag |
| metrics-server | sources/metrics-server-charts/Chart.yaml | values.yaml | metrics_server_version | metrics_server_image_tag |
| haproxy | sources/haproxy-charts/Chart.yaml | values.yaml | haproxy_chart_version | haproxy_image_tag |
| external-secrets | sources/external-secrets/Chart.yaml | values.yaml | external_secrets_image_tag | external_secrets_image_tag |

### Известные расхождения (осознанный выбор):
- **Cilium Envoy**: используется `v1.36.5` вместо официального build-hash (упрощение для AirGapped)
- **HAProxy**: используется `haproxytech/kubernetes-ingress` (это отдельный продукт, НЕ haproxy-alpine)
- Версии компонентов могут отставать от официальных — это осознанный выбор для стабильности

## Текущие версии (hosts-vars/k8s-base.yaml)

| Компонент | Версия |
|-----------|--------|
| Kubernetes | 1.34 / v1.34.0 |
| containerd | 2.2.1 |
| runc | 1.4.0 |
| CNI plugins | 1.9.0 |
| Helm | 3.19.2 |
| k9s | 0.50.18 |
| Cilium | 1.19.1 |
| Traefik | v3.6.2 (chart 38.0.2) |
| CertManager | 1.19.2 |
| External Secrets | v1.2.1 |
| Vault | 1.21.2 |
| bank-vaults | v1.32.1 |
| Teleport | 18.7.2 |

## Networking

| Параметр | Значение |
|----------|----------|
| service_subnet | 10.128.0.0/12 |
| pod_subnet | 10.64.0.0/10 |
| cluster_dns_domain | cluster.local |
| node_port range | 1–50000 |
| HAProxy LB | 127.0.0.1:16443 |
| HAProxy healthz | 127.0.0.1:16444 |

## Vault + ESO — Важные правила

### is_need_eso флаг
- `is_need_eso: true` — создавать SecretStore + ExternalSecret
- `is_need_eso: false` — только сохранить в Vault, k8s Secret НЕ создавать
- Пример: `eso-gitlab-root`, `eso-argocd-root` — root creds, хранятся только в Vault

### Компоненты с ESO интеграцией
`traefik`, `haproxy`, `longhorn`, `gitlab`, `gitlab-runner`, `argocd`, `argocd-git-ops`, `grafana`, `zitadel`

### ArgoCD Git-Ops ESO — особая структура
- Отдельный SA `eso-git-ops` в namespace `argocd` (не `eso-main`!)
- Отдельный SecretStore `eso-git-ops.vault`
- 1 репозиторий = 1 ESO secret + 1 k8s secret + 1 vault secret
- Типы секретов: `git_ops_repo_pattern`, `git_ops_repo_direct`, `git_ops_repo_direct_userpass`, `git_ops_repo_pattern_userpass`, `helm_repo`, `helm_repo_oci`
- **Последовательность установки**:
  1. Настроить конфиги `argocd_git_ops_apps` + `eso_vault_integration_argocd_git_ops_extra`
  2. Установить `argocd-git-ops-install.yaml`
  3. Создать SSH keys → положить в Vault
  4. Создать репозитории → добавить deploy-keys

### Записи в Vault НЕ удаляются автоматически
- При удалении k8s компонентов → секреты в Vault остаются
- При повторной установке → пароль из Vault НЕ обновляется
- Ручное удаление: зайти в Vault UI → удалить путь

### Полная синхронизация Vault (vault-policy-sync)
- **policy-add** — добавить новые политики
- **policy-update** — обновить текущие
- **policy-delete** — удалить те, которых нет в ansible
- **role-add/update/delete** — аналогично для ролей

Без тегов = выполняет все 6 операций (полная синхронизация)

## GitLab нюансы

- `gitlab-exporter` — hardcoded `1` в официальном helm chart
- `gitaly` — StatefulSet, по умолчанию 1 реплика. RollingUpdate = убить потом создать
  - Если > 1 реплики — возня с Praefect (репликация git-данных между узлами)
- MinIO (S3) — устанавливается вместе с GitLab, отдельные ingress для API и Console
- SSH ingress — отдельный HAProxy TCP ingress для git SSH
- **gitlab-configure.yaml**: достать пароль админа → положить в Vault → создать PersonalAccessToken → положить в Vault
- Регистрация runner на GitLab — **в ручном режиме** (создать instance-runner → получить токен → сохранить в Vault)

## GitLab-Runner нюансы

- Установка helm-chart **без конфигурации** в GitLab-instance
- Регистрация раннера — **в ручном режиме**
  1. Зайти в GitLab → создать `instance-runner` + получить токен
  2. Сохранить токен в VAULT (путь указан в ESO) в переменную `token`
  3. Поправить конфиг (`hosts-vars/` + `hosts-vars-override/`) — там полный TOML файл
  4. Установить gitlab-runner (helm)
- Minio (s3-cache) данные — создаются автоматически

## Longhorn нюансы

- `namespace: longhorn-system` — **НЕЛЬЗЯ менять** (ограничение upstream)
- Автоматически подхватывает конфиг из ConfigMap (обновил → сразу использовал)
- Секреты для бэкапа в S3 → используют CRD от ESO (но секреты появятся в VAULT позже)
  - Для создания секретов → определить в `hosts-vars-override/` (пример в `hosts-vars-override/.example`)
- **node-tags** — автоматическая установка на Nodes через отдельный playbook:
  - `ansible-playbook ... longhorn-tags-sync.yaml`
  - Синхронизация CRD объекта: `nodes.longhorn.io`
  - Вызывать: после установки longhorn, после добавления node, после изменения node-tags

## Prometheus Operator нюансы

- **CRDs очень большие**: ~80,000 строк в `playbook-app/charts/prometheus-operator/crds/crds.yaml`
- **Обновление версии**:
  1. Скачать новый yaml: `https://github.com/prometheus-operator/prometheus-operator/releases`
  2. Разнести на файлы:
     - `crds/crds.yaml` — все CRDs (~80k строк)
     - `install/templates/prometheus-operator.yaml` — Deployment, RBAC, Service
  3. Не затереть изменения в дефолтных конфигах!
- Версия НЕ указывается в `hosts-vars/` — версия будет в `*.yaml`
- Можно установить только CRDs: `--tags crds`

## ArgoCD нюансы

- Требуется `ssh-keyscan` для git репозиториев → `argocd_cm_ssh_known_hosts_extra`
- Нет автоподхвата новых конфигов → ручной restart после обновления
- Версия фиксируется в скачанном `install.yaml` (не в переменных)
- Два ingress: UI (HTTP/HTTPS) + h2c-grpc (для CLI)
- **Обновление версии**:
  1. Скачать новый yaml: `https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml`
  2. Разнести на файлы:
     - `playbook-app/charts/argocd/crds/crds.yaml` — только CRD (~24k строк)
     - `playbook-app/charts/argocd/pre/templates/configmaps.yaml` — 7 ConfigMaps + сохранить расширения через HELM
     - `playbook-app/charts/argocd/install/templates/argocd.yaml` — всё, кроме CRD и configmaps
  3. Не затереть изменения в дефолтных конфигах!
- **argocd-configure.yaml**: сбросить права у default-project → достать пароль admin → положить в Vault

## QWEN: Как мне работать с этим проектом

### При запросе на модификацию:
1. Проверить `hosts-vars/` для defaults
2. Проверить `hosts-vars-override/` для реальных значений (если есть .example)
3. Изучить соответствующий playbook в `playbook-app/` или `playbook-system/`
4. Проверить tasks в `playbook-*/tasks/` на переиспользуемую логику
5. При изменении playbook — убедиться в корректности фаз (pre/install/post)
6. Запустить с `--check` mode если возможно

### При запросе на деплой:
1. Уточнить, на каких нодах запускать (нужен ли `--limit`)
2. Проверить зависимости (Cilium → cert-manager → ingress → остальное)
3. Для новых нод — напомнить про `cilium-install.yaml --tags post`
4. Проверить, нужны ли Vault/ESO секреты

### При работе с Vault + ESO:
1. Добавить политики → `vault_policies_extra`
2. Добавить роли → `vault_roles_extra`
3. Синхронизировать → `vault-policy-sync.yaml`
4. Положить секреты в Vault (вручную или через ansible)
5. Добавить ExternalSecret → `eso_vault_integration_XXX_secrets_extra`
6. Применить компонент → `component-install.yaml --tags pre`
7. Проверить синхронизацию → `kubectl get externalsecret -n <namespace>`

### При troubleshoot:
1. Проверить `node-info.yaml` для статуса нод
2. Проверить health нод: `tasks-kubelet-health-wait.yaml`, `tasks-haproxy-lb-health-wait.yaml`
3. Для K8s проблем — проверить `kubectl get nodes`, `kubectl get pods -A`
4. Для Vault — проверить unseal статус и ключи на managers
5. Для ESO — проверить `kubectl describe externalsecret -n <namespace>`
6. Для Teleport — проверить `kubectl logs -n teleport deploy/teleport-operator`

### Безопасность:
- НИКОГДА не коммитить `hosts-vars-override/`
- Не логировать пароли, токены, encryption keys
- При работе с Vault — использовать ansible-vault если нужно закоммитить секреты

### Правила работы с директориями:
- **`docs/`** — НЕ использовать при выполнении задач, если пользователь явно не попросил
  - Это справочная документация, не влияет на логику playbook-ов
  - Все нужные данные уже есть в `QWEN.md`, `README.md`, `playbook-*/`, `hosts-vars/`
  - Если пользователь скажет "посмотри в docs/" — тогда можно

# Rule: Add New Component
# Trigger keywords: добавь компонент, новый компонент, установить компонент, add component, new component
# Manual trigger: /rule add-component

## Когда применяется
Пользователь просит добавить новый компонент в кластер (например: "добавь kube-state-metrics", "установи external-dns").

## Чеклист шагов

### Шаг 1: Определить тип компонента
- **Есть ли официальный Helm chart?** → Использовать его
- **Нет официального Helm?** → Действовать по схеме ArgoCD/Prometheus-operator:
  1. Скачать официальный YAML/manifest
  2. Разнести на файлы: CRDs + pre (ConfigMaps) + install (Deployment/RBAC/Service)
  3. Положить в `playbook-app/charts/<component>/`

### Шаг 2: Скачать в sources/
```bash
# Для Helm chart:
cd sources/
git clone <repo> <component>-charts
# ИЛИ скачать ZIP релиза

# Для YAML manifests:
wget <url> -O sources/<component>/manifest.yaml
```

### Шаг 3: Изучить Helm chart / манифесты
**Обязательно проверить:**
- `values.yaml` — какие переменные принимаются
- `templates/` — какие ресурсы создаются
- `Chart.yaml` — зависимости, версии
- CRDs — какие CRDs добавляются
- **Валидация**: зайти в `sources/<component>/` → проверить, что переменные, которые планируешь использовать, реально принимаются chart-ом и правильно названы

### Шаг 4: Создать переменные в hosts-vars/
```yaml
# hosts-vars/<component>.yaml
all:
  vars:
    # Basics
    <component>_namespace: "<component>"
    <component>_version: "X.Y.Z"
    
    # Tolerations / NodeSelector / Affinity
    <component>_tolerations: []
    <component>_node_selector: {}
    <component>_affinity: {}
    
    # Resources
    <component>_resources: {}

    # Replicas — Single mode (НЕ HA!)
    <component>_replica_count: 1
    # Если есть возможность — настроить updateStrategy: Recreate

    # Image configuration (AirGapped support)
    # Дефолт = официальный registry + repository + tag (из sources/<component>/Chart.yaml)
    # Для компонентов с ОДНИМ образом:
    <component>_image_registry: "docker.io"
    <component>_image_repository: "<image-path>"
    <component>_image_tag: "X.Y.Z"

    # Для компонентов с НЕСКОЛЬКИМИ образами (Cilium, Longhorn, cert-manager):
    # Добавить переменные для КАЖДОГО компонента:
    #   <component>_<component_name>_image_registry
    #   <component>_<component_name>_image_repository
    #   <component>_<component_name>_image_tag
    # Пример (Cilium):
    #   cilium_agent_image_registry: "quay.io"
    #   cilium_agent_image_repository: "cilium/cilium"
    #   cilium_agent_image_tag: "v1.19.1"
    #   cilium_operator_image_registry: "quay.io"
    #   ... и так далее для каждого образа

    # Для ArgoCD (есть sub-компоненты с отдельными образами):
    # argocd_image_main_with_registry: "quay.io/argoproj/argocd"
    # argocd_image_main_tag: "v3.2.3"
    # argocd_image_dex_with_registry: "ghcr.io/dexidp/dex"
    # argocd_image_dex_tag: "v2.41.1"
    # argocd_image_redis_with_registry: "redis"
    # argocd_image_redis_tag: "7.2.7-alpine"

    # Helm timeouts
    <component>_helm_timeout: "5m"
    <component>_rollout_timeout: "180s"
    
    # Prometheus metrics (если есть)
    <component>_service_monitor_enabled: true
    <component>_service_monitor_interval: "30s"
```

### Шаг 5: Создать local Helm chart
```
playbook-app/charts/<component>/
  ├── Chart.yaml
  ├── pre/
  │   ├── Chart.yaml
  │   └── templates/
  │       └── networkpolicy.yaml
  ├── install/
  │   ├── Chart.yaml
  │   └── templates/
  │       └── <component>.yaml  (или использовать официальный chart)
  └── post/
      ├── Chart.yaml
      └── templates/
          └── ingress.yaml
```

### Шаг 6: Создать playbook
```yaml
# playbook-app/<component>-install.yaml
# Теги: pre → install → post
```

**Структура:**
- **pre**: NetworkPolicy, Namespace
- **install**: Helm chart (официальный или локальный)
- **post**: Ingress (Traefik/HAProxy) + Certificate

### Шаг 7: Если нужен Vault + ESO
1. Добавить политику → `vault_policies_extra` в `hosts-vars/vault.yaml`
2. Добавить роль → `vault_roles_extra` в `hosts-vars/vault.yaml`
3. Добавить интеграцию → `eso_vault_integration_<component>` в `hosts-vars/vault-eso.yaml`
4. Вызвать: `ansible-playbook ... vault-policy-sync.yaml`
5. Добавить в playbook тег `pre` для создания ESO ресурсов

### Шаг 8: Установить компонент
```bash
# Полный install
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/<component>-install.yaml

# Отдельные фазы
ansible-playbook ... --tags pre
ansible-playbook ... --tags install
ansible-playbook ... --tags post
```

### Async Helm Upgrade (ВСЕ helm команды)

Все `helm upgrade --install` команды обёрнуты в async wrapper для устойчивости к обрывам SSH.

**Как использовать:**
```yaml
- name: "[component-install] Install via Helm (async)"
  include_tasks: tasks/tasks-helm-upgrade-async.yaml
  vars:
    label_name: "component-install"
    helm_command: >
      helm upgrade --install component repo/chart
      --namespace {{ namespace }}
      --values {{ values_file }}
      --cleanup-on-fail --atomic --wait --timeout {{ timeout }}
  tags: [install]
```

**Параметры async (из hosts-vars/k8s-base.yaml):**
- `helm_async_timeout: 1800` — 30 минут макс время
- `helm_async_poll: 5` — проверка каждые 5 сек
- `helm_async_retries: 360` — кол-во проверок
- `helm_async_delay: 5` — задержка 5 сек

**ВАЖНО:**
- НЕ добавлять `delegate_to` или `run_once` — wrapper сам это делает
- Передавать только `label_name` + `helm_command`

## Валидация после установки

### Проверить CRDs (если компонент создаёт CRDs через Operator)
**Обязательно для компонентов с Operator (Teleport, Vault, и т.д.):**
```bash
# Проверить, что CRDs создались
kubectl get crds | grep <component>

# В playbook добавить:
- name: "[component] Wait for CRDs"
  include_tasks: tasks/tasks-wait-crds.yaml
  vars:
    label_name: "component"
    crds_list:
      - crd/<crd1>.<group>.dev
      - crd/<crd2>.<group>.dev
```

**Место вставки:** После Helm install, **до** rollout wait.

### Проверить pods
```bash
kubectl get pods -n <component>_namespace
kubectl rollout status deployment/<component> -n <component>_namespace
```

### Проверить Helm release
```bash
helm list -n <component>_namespace
```

### Проверить CRDs (если есть)
```bash
kubectl get crds | grep <component>
```

### Проверить Ingress (если есть post)
```bash
kubectl get ingress -n <component>_namespace
```

### Проверить ESO (если есть)
```bash
kubectl get externalsecret -n <component>_namespace
kubectl get secretstore -n <component>_namespace
```

## Важные правила

1. **ВСЕ запуски** из корня проекта (project_root = PWD)
2. **Сначала sources/** → потом playbook-app/charts/
3. **Валидация переменных**: зайти в sources/<component>/values.yaml → проверить, что переменные существуют
4. **Namespace**: НЕ менять для `longhorn-system` и `argocd`
5. **CRDs ждать**: добавить в `tasks-wait-crds.yaml` если новые CRDs
6. **Rollout ждать**: добавить в `tasks-wait-rollout.yaml` deployment/statefulset

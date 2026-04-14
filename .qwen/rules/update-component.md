# Rule: Update Component Version
# Trigger keywords: обнови версию, обновить компонент, update version, upgrade component
# Manual trigger: /rule update-component

## Когда применяется
Пользователь просит обновить версию существующего компонента (например: "обнови Cilium до 1.19.2", "обнови Traefik").

## Чеклист шагов

### Шаг 1: Определить тип обновления

**Тип A: Helm chart (большинство компонентов)**
- cert-manager, external-secrets, cilium, traefik, haproxy, vault, teleport, gitlab, longhorn, и т.д.

**Тип B: YAML manifests (ArgoCD, Prometheus-operator)**
- ArgoCD: скачать install.yaml → разнести на CRDs + pre + install
- Prometheus-operator: скачать release → разнести на CRDs + install

### Шаг 2: Обновить sources/

**Для Helm chart:**
```bash
cd sources/<component>-charts/
git pull
# ИЛИ
# Скачать новый ZIP релиза с GitHub
```

**Для YAML manifests:**
```bash
# ArgoCD
wget https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml -O sources/argocd/install.yaml

# Prometheus-operator
wget https://github.com/prometheus-operator/prometheus-operator/releases/download/vX.Y.Z/bundle.yaml -O sources/prometheus-operator/bundle.yaml
```

### Шаг 3: Изучить изменения

**Обязательно:**
1. Сравнить `values.yaml` (старый vs новый)
2. Проверить CHANGELOG на breaking changes
3. Проверить, есть новые CRDs?
4. Проверить, есть изменения в templates?
5. **Валидация**: зайти в `sources/<component>/` → проверить, что переменные, которые используются в `hosts-vars/<component>.yaml`, всё ещё принимаются новым chart-ом

### Шаг 4: Обновить playbook-app/charts/

**Для Helm chart:**
```bash
# Скопировать новые templates из sources/ в playbook-app/charts/
# Сохранить кастомные изменения (не затереть!)
```

**Для YAML manifests (ArgoCD/Prometheus):**
```bash
# ArgoCD:
# 1. sources/argocd/install.yaml → разнести:
#    - CRDs → playbook-app/charts/argocd/crds/crds.yaml (~24k строк)
#    - ConfigMaps → playbook-app/charts/argocd/pre/templates/configmaps.yaml (7 ConfigMaps)
#    - Остальное → playbook-app/charts/argocd/install/templates/argocd.yaml
# 2. Вернуть кастомные изменения в дефолтные конфиги!

# Prometheus-operator:
# 1. sources/prometheus-operator/bundle.yaml → разнести:
#    - CRDs → playbook-app/charts/prometheus-operator/crds/crds.yaml (~80k строк)
#    - Остальное → playbook-app/charts/prometheus-operator/install/templates/prometheus-operator.yaml
# 2. Вернуть кастомные изменения!
```

### Шаг 5: Обновить версию в hosts-vars/

```yaml
# hosts-vars/<component>.yaml
<component>_version: "X.Y.Z"  # Новая версия
```

**Для ArgoCD/Prometheus**: версия НЕ указывается (встроена в YAML)

### Шаг 6: Запустить обновление

**Для Helm chart:**
```bash
# Полный install (helm upgrade)
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/<component>-install.yaml

# Отдельные фазы
ansible-playbook ... --tags install    # Основной компонент
ansible-playbook ... --tags post       # Если Ingress изменился
```

**Для ArgoCD/Prometheus:**
```bash
# CRDs (если новые)
ansible-playbook ... playbook-app/argocd-install.yaml --tags crds

# Install
ansible-playbook ... playbook-app/argocd-install.yaml --tags install

# Post (если Ingress изменился)
ansible-playbook ... playbook-app/argocd-install.yaml --tags post

# Restart (если нет автоподхвата конфигов)
ansible-playbook ... playbook-app/argocd-restart.yaml
```

### Шаг 7: Проверить rollout

```bash
# Проверить pods
kubectl get pods -n <namespace>

# Проверить rollout
kubectl rollout status deployment/<component> -n <namespace>
kubectl rollout status statefulset/<component> -n <namespace>

# Проверить Helm release
helm list -n <namespace>

# Проверить CRDs (если новые)
kubectl get crds | grep <component>
```

### Шаг 8: Если есть ESO — проверить

```bash
# Проверить ExternalSecrets
kubectl get externalsecret -n <namespace>

# Проверить Secrets
kubectl get secret -n <namespace>

# Force sync (если нужно)
ansible-playbook ... playbook-app/eso-force-sync.yaml --tags <namespace>
```

## Важные правила

### Режим Single (НЕ HA)
- Все компоненты — **1 реплика** (Single mode)
- НЕ использовать High Available
- `replica_count: 1` для всех компонентов
- Если есть возможность — `updateStrategy: Recreate`

### Async Helm Upgrade

ВСЕ helm upgrade команды обёрнуты в async wrapper (`tasks-helm-upgrade-async.yaml`) для устойчивости к обрывам SSH.

**Параметры (из hosts-vars/k8s-base.yaml):**
- `helm_async_timeout: 1800` — 30 минут макс
- `helm_async_poll: 5` — каждые 5 сек
- `helm_async_retries: 360` — проверок
- `helm_async_delay: 5` — задержка 5 сек

**При обновлении компонента:**
- wrapper автоматически использует async/poll/retries/delay из переменных
- НЕ нужно менять эти значения вручную
- Если нужно увеличить timeout — изменить `helm_async_timeout` в k8s-base.yaml

1. **Сначала sources/** → потом playbook-app/charts/
2. **Не затереть кастомные изменения** в дефолтных конфигах!
3. **HAProxy apiserver-lb конфиг** — вынесен в `hosts-vars/haproxy-apiserver-lb.yaml`
   - Переменная `haproxy_apiserver_lb_config_template`
   - Placeholder `__MANAGER_SERVER_IP_LIST__` — НЕ удалять (туда подставляются IP manager нод)
   - При переопределении — сохранить placeholder
4. **kubeadm config** — вынесен в `hosts-vars/kubeadm-config.yaml`
   - Переменная `kubeadm_config_template`
   - if/else удалены из шаблона
   - Eviction: `kubelet_eviction_soft`, `kubelet_eviction_hard`, `kubelet_eviction_soft_grace_period` — dict переменные
5. **Image Registry** — прокинуты для 12 компонентов (cert-manager, cilium, traefik, longhorn, teleport, vault, argocd, gitlab, gitlab-runner, haproxy, metrics-server, zitadel)
   - Переменные: `<component>_image_registry`, `<component>_image_repository`, `<component>_image_tag`
   - При обновлении версии — обновить `<component>_image_tag`
6. **Валидация переменных**: проверить, что переменные из hosts-vars/ всё ещё работают в новом chart
7. **CRDs ждать**: если новые CRDs → добавить в `tasks-wait-crds.yaml`
8. **Restart**: если компонент НЕ подхватывает новые конфиги автоматически → вызвать restart playbook
9. **Backup**: перед обновлением критичных компонентов (Vault, Cilium) — сделать backup

### Проверка версий перед обновлением
1. Открыть `sources/<component>/Chart.yaml`
2. Сравнить:
   - `version` → обновить `<component>_chart_version` в hosts-vars
   - `appVersion` → обновить `<component>_image_tag` в hosts-vars
3. Проверить `sources/<component>/values.yaml` → `image.repository` / `image.tag`
4. Если chart использует несколько образов (Cilium, Longhorn, ArgoCD, cert-manager) — проверить **КАЖДЫЙ** образ
5. После обновления hosts-vars → обновить QWEN.md таблицу Image Registry

**Пример проверки:**
```bash
# Chart version
cat sources/cilium/Chart.yaml | grep version
cat hosts-vars/cilium.yaml | grep cilium_version

# Image tag
cat sources/cilium/Chart.yaml | grep appVersion
cat hosts-vars/cilium.yaml | grep cilium_agent_image_tag

# Image repository
cat sources/cilium/values.yaml | grep -A2 'image:'
cat hosts-vars/cilium.yaml | grep cilium_agent_image_repository
```

## Компоненты с особенностями

| Компонент | Особенности обновления |
|-----------|----------------------|
| **ArgoCD** | Разнести YAML на 3 файла, вернуть кастомные конфиги, restart |
| **Prometheus-operator** | ~80k строк CRDs, разнести YAML, вернуть кастомные конфиги |
| **Cilium** | Может зависнуть на cilium-post (SSH обрывается) → Ctrl+C + restart |
| **Vault** | Regional block (РФ), RBAC отдельно через kustomize |
| **Teleport** | Operator cert expiry 1h, может отвалиться → restart operator |
| **GitLab** | gitaly StatefulSet (1 реплика), gitlab-exporter hardcoded |

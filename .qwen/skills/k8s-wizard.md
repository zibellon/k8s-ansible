# Skill: k8s-ansible Wizard
# Invoke: /k8s-wizard

## Описание

Интерактивный wizard для быстрого выполнения задач в проекте k8s-ansible.
Задаю вопросы → собираю конфигурацию → генерирую файлы/команды → выполняю.

## Когда использовать

- Пользователь не уверен, с чего начать
- Нужно быстро выполнить типовую задачу
- Хочешь интерактивный режим вместо ручного ввода команд

## Как вызвать

```
/k8s-wizard
```

## Доступные задачи

После вызова предлагаю выбрать задачу:

1. **Добавить новый компонент**
2. **Обновить версию компонента**
3. **Добавить ESO/Vault интеграцию**
4. **Обновить конфигурацию Teleport**
5. **Обновить конфиги Prometheus/Alertmanager**
6. **Добавить проект в ArgoCD Git-Ops**
7. **Другое (свободный ввод)**

## Workflow для каждой задачи

### 1. Добавить новый компонент

**Вопросы:**
1. Название компонента?
2. Есть ли официальный Helm chart? (да/нет, ссылка)
3. Namespace? (по умолчанию: <component>)
4. Нужен ли Vault + ESO? (да/нет)
5. Нужен ли Ingress? (да/нет, domain)
6. Версия? (по умолчанию: последняя)

**Действия:**
1. Создать `hosts-vars/<component>.yaml` с базовыми переменными
2. Создать структуру `playbook-app/charts/<component>/` (pre/install/post)
3. Создать `playbook-app/<component>-install.yaml`
4. Если Vault+ESO → добавить integration в `vault-eso.yaml`
5. Показать команды для установки

### 2. Обновить версию компонента

**Вопросы:**
1. Какой компонент обновить?
2. Новая версия?
3. Есть ли sources/<component>/ уже? (да/нет)

**Действия:**
1. Проверить sources/<component>/
2. Показать команды для обновления sources
3. Обновить версию в `hosts-vars/<component>.yaml`
4. Показать команды для установки

### 3. Добавить ESO/Vault интеграцию

**Вопросы:**
1. Для какого компонента?
2. Что добавить? (политика / роль / external secret / всё вместе)
3. Vault path для секретов?
4. Namespace компонента?
5. SA name? (по умолчанию: eso-main)

**Действия:**
1. Добавить политику в `vault_policies_extra`
2. Добавить роль в `vault_roles_extra`
3. Добавить integration в `eso_vault_integration_<component>`
4. Показать команды для синхронизации

### 4. Обновить конфигурацию Teleport

**Вопросы:**
1. Что добавить? (role / user / app / database / connector / другое)
2. Название ресурса?
3. Конфигурация (зависит от типа)?

**Действия:**
1. Добавить в `teleport_configure_<type>_extra`
2. Показать команды для `--tags configure`

### 5. Обновить конфиги Prometheus/Alertmanager

**Вопросы:**
1. Что обновить? (alert rules / receivers / scrape configs / recording rules)
2. Конфигурация?

**Действия:**
1. Добавить в `prometheus_rules_extra` / `prometheus_alertmanager_config` / etc.
2. Показать команды для установки

### 6. Добавить проект в ArgoCD Git-Ops

**Вопросы:**
1. Название проекта?
2. Repo URL?
3. Path в репозитории?
4. Target namespace?
5. Sync policy? (auto-sync / manual)
6. SSH keys готовы? (да/нет)

**Действия:**
1. Добавить ESO integration в `argocd_git_ops_extra`
2. Добавить AppProject + Application
3. Показать команды для установки

## Чеклист валидации (после любой задачи)

После выполнения задачи предлагаю проверить:

```bash
# Проверить pods
kubectl get pods -n <namespace>

# Проверить rollout
kubectl rollout status deployment/<component> -n <namespace>

# Проверить Helm release
helm list -n <namespace>

# Проверить ESO (если есть)
kubectl get externalsecret -n <namespace>
kubectl get secretstore -n <namespace>

# Проверить CRDs (если есть)
kubectl get crds | grep <component>
```

## Правила

### Режим Single (НЕ HA)
- Все компоненты — **1 реплика** (Single mode)
- НЕ использовать High Available
- `replica_count: 1` для всех компонентов
- Если есть возможность — `updateStrategy: Recreate`

### Image Registry (AirGapped)
- ВСЕГДА добавлять: `<component>_image_registry`, `<component>_image_repository`, `<component>_image_tag`
- Дефолт = официальный registry (из sources/<component>/Chart.yaml)
- В values-override: `repository: "{{ registry }}/{{ repository }}"` + `tag: "{{ tag }}"`

### Проверка версий (при обновлении компонента)
- Chart version: `sources/<component>/Chart.yaml` → version
- Image tag: `sources/<component>/Chart.yaml` → appVersion
- Image repository: `sources/<component>/values.yaml` → image.repository
- Multi-image компоненты (Cilium, Longhorn, ArgoCD, cert-manager) — проверять **КАЖДЫЙ** образ
- После обновления → обновить QWEN.md таблицу Image Registry

### Async Helm Upgrade

- ВСЕ helm upgrade команды используют `tasks-helm-upgrade-async.yaml`
- НЕ использовать прямой `command: helm upgrade --install ...`
- При генерации нового playbook — ВСЕГДА использовать async wrapper
- Параметры async берутся из hosts-vars/k8s-base.yaml (НЕ хардкодить)
- Передавать только `label_name` + `helm_command` в wrapper

1. **ВСЕГДА** работать из корня проекта
2. **ВСЕГДА** валидировать переменные перед генерацией
3. **ВСЕГДА** показать команды, которые пользователь должен запустить
4. **НИКОГДА** не коммитить hosts-vars-override/
5. **ВСЕГДА** проверять, что sources/ обновлён перед изменением charts/

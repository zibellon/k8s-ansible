# ---------
# ---Как и откуда запускать
# ---------
## Важно: все запуски делать из директории проекта
## В playbook, оченеь много логики зависит от корневой директории (откуда был сделан запуск)
## Чтобы определить эту директорию корректно, запуск нужно производить из директории, где находится проект
## Как выглядит переменная: `project_root: "{{ lookup('env', 'PWD') }}"`

# ---------
# ---Конфигурация
# ---------
## `hosts-vars/` - тут лежат все доступные переменные, которые можно использовать
## НО - менять переменные в этой директории не рекомендуется. Эта директория находится под контролем GIT
## Если нужно переопредеелить какую-то переменную - нужно создать новую директорию (любую)
## Например: `hosts-vars-override/*` и там создать `xxx.yaml` файл в котором определить нужную переменную
## Пример запуска: `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/utils/node-info.yaml`
## То есть: сначала берем все базовые переменные, а потом сверху накладываем переменные из override
## ---
## Как идет установка, всего, что НЕ ЕСТЬ ОФИЦИАЛЬНЫЙ helm-chart
## 1. Копирование local-helm-chart на сервер (Chart.yaml, values-override.yaml, templates/...)
## 2. Helm-template. Чтобы получить один финальный файл для установки без шаблонизации
## 3. Наложить на этот файл него патчи kustomize -> на выходе опять один файл
## 4. копируем Chart.yaml + templates (.Files.Get "raw/all.yaml") + raw/all.yaml
## 5. Устанавливаем его через helm. Чтобы нормально управлять ЖЦ компонента
## ---
## Как модифицировать
## 1. При шаблонизации есть возможность подсунуть `<component>_<phase>_extra_objects`
##    Это массив из объектов, которые примет helm-chart при установка
##    Это позволяет добавлять новые объекты в установку
## 2. kustomize. Если требуется изменить что-то в установке
##    `<component>_<phase>__kustomize_patches` - пример названия такого массива
##    Это позволяет удалять или модифицировать существующие объекты

# ---------
# ---Pre-check + Prepare
# ---------
## `./readme-pre-check.md`. Тут есть полное описание, что нужно сделать ПЕРЕД УСТАНОВКОЙ


# ---------
# ---AirGap (на серверах нет доступа в интернет)
# ---------
## `./readme-local-pkgs.md`. Тут есть полное описание, как к этому подготовиться


# ---------
# ---INIT
# ---------

# Инициализация Node (Установка компонентов)
## Инициализация Node
## Если вызывать без `--limit` - инициализация производится на всех Node сразу
## Если вызвать с `--limit` - инициализация произойдет только на указанной node
##
- Без лимита: `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/full-node-install.yaml`
- С лимитом: `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/full-node-install.yaml --limit k8s-manager-1`

# Инициализация кластера (в первый раз)
## Инициализация кластера
## Именно команда: `kubeadm init ...`
## `--limit XXX` - обязательно надо указывать
##
- `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/cluster-init.yaml --limit k8s-manager-1`

# ---------
# ---JOIN
# ---------

# ------
# WORKER_NODE
# ------
## Добавить в `hosts-vars-override/` нового worker
## Если уже был установлен Cilium - смотрим `Подготовка_2`
## Если уже был установлен и настроен Longhorn - смотрим `longhorn/tags-sync`
##
- `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/full-node-install.yaml --limit k8s-worker-1`
  - Инициализация ноды
- `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/utils/worker-join.yaml --limit k8s-worker-1`
  - Получение токена и вызов команды `kubeadm join ...`

# ------
# MANAGER_NODE
# ------
## Добавить в `hosts-vars-override/` нового manager
## Если уже был установлен Cilium - смотрим `Подготовка_2`
## Если уже был установлен и настроен Longhorn - смотрим `longhorn/tags-sync`
## Обновить SANS для api-server
## Обновить haproxy-apiserver-lb
##
- `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/utils/apiserver-sans-update.yaml`
  - Это обновит CANS в сертификатах для api-server (добавит туда нового manager-ip)
  - Вызывать нужно БЕЗ `--limit`. Конфиг - нужно обновить на ВСЕХ текущих managers
  - Обновит - только текущие managers
  - Перезапуск api-server - производится последовательно, для каждого managers
- `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/utils/haproxy-apiserver-lb-update.yaml`
  - Обновить конфиг для `haproxy-apiserver-lb` на всех текущих Node (manager + worker)
  - Обновление производится по одному за раз, через playbook.serial: 1
  - То есть: перезапуск производится последовательно, для обеспечения HA доступности
- `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/full-node-install.yaml --limit k8s-manager-2`
  - Инициализация ноды
  - Указываем `--limit` - так как это добавление конкретной Node
- `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/utils/manager-join.yaml --limit k8s-manager-2`
  - Загрузка сертификатов в k8s.secrets
  - получение токена
  - вызов команды `kubeadm join ...`

# ---------
# ---------
# Компоненты === Приложения
# ---------
# ---------

## ---
## Prometheus-operator-CRD
## ---
## Для всех компонентов, при установке создается сущность `ServiceMonitor` и `PodMonitor`
## Их создание можно отключить: через флаги в `hosts-vars`
## Если не создать prometheus-operator-CRD = то установки компонентов упадут с ошибкой
##
- установка
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/mon-system-install.yaml --tags crds`

## ---
## Cilium. Официальный helm
## ---
## Если изменились конфиги = ConfigMap, Cilium их не подцепит автоматически
## Если посмотреть, что генерируется при `helm template ...` - у Deployment/DaemonSet нет checksum, на основе ConfigMap
## Можно сделать предположение, что для применения новых ConfigMap - надо сделать ручной restart
## Ожидание готовности deployment/daemonset - `kubectl rollout status ...`
## Есть ожидание готовности CRDs. Если добавляются новые CRDs - их ожидание надо добавить в `playbook-app/cilium-install.yaml`
## ---
## Важно_1. Установка изначально производится только с тагом `--tags install`
## pre + post = станавливаются позже. После cert-manager, ESO, Traefik, Haproxy
## ---
## `--tags pre, install, post`
## ---
##
- установка
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/cilium-install.yaml --tags install`
- обновление (версия, конфиг)
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/cilium-install.yaml`
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/cilium-restart.yaml`

## ---
## metrics-server. Официальный helm
## ---
## Ожидание готовности deployment/daemonset - `kubectl rollout status ...`
## ---
## `--tags pre, install`
## ---
##
- установка + обновление (версия, конфиг)
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/metrics-server-install.yaml`

## ---
## cert-manager. Официальный helm
## ---
## Ожидание готовности deployment/daemonset - `kubectl rollout status ...`
## Есть ожидание готовности CRDs. Если добавляются новые CRDs - их ожидание надо добавить в `playbook-app/cert-manager-install.yaml`
## ---
## Важно_1: Сейчас, через этот ansible - можно настроить только ClusterIssuer (Переменная: cert_manager_cluster_issuers)
## ---
## `--tags pre, install, post`
## ---
## 
- установка + обновление (версия, конфиг)
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/cert-manager-install.yaml`
- Есть дополнительный playbook, для перезапуска
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/cert-manager-restart.yaml`

## ---
## ExternalSecret. Официальный helm
## ---
## Ожидание готовности deployment/daemonset - `kubectl rollout status ...`
## Есть ожидание готовности CRDs. Если добавляются новые CRDs - их ожидание надо добавить в `playbook-app/external-secrets-install.yaml`
## ---
## `--tags pre, install`
## ---
##
- установка + обновление (версия, конфиг)
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/external-secrets-install.yaml`
- Есть дополнительный playbook, для перезапуска
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/external-secrets-restart.yaml`

## ---
## StakaterReloader. Официальный helm
## ---
## Ожидание готовности deployment/daemonset - `kubectl rollout status ...`
## ---
## `--tags pre, install`
## ---
##
- установка + обновление (версия, конфиг)
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/stakater-reloader-install.yaml`
- Есть дополнительный playbook, для перезапуска
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/stakater-reloader-restart.yaml`

## ---
## traefik (ingress-1). Официальный helm
## ---
## Параметры (конфиг) для работы - в cli (как аргументы при запуске)
## Есть dashboard, который доступен по URL -> требуется Certificate (cert-manager-CRD)
## Ожидание готовности deployment/daemonset - `kubectl rollout status ...`
## Есть ожидание готовности CRDs. Если добавляются новые CRDs - их ожидание надо добавить в `playbook-app/traefik-install.yaml`
## Есть работа с `vault + ESO`
## ---
## `--tags pre, install, post`
## ---
##
- установка + обновление (версия, конфиг)
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/traefik-install.yaml`
- Есть дополнительный playbook, для перезапуска
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/traefik-restart.yaml`

## ---
## haproxy (ingress-2). Официальный helm
## ---
## Автоматически подхватывает конфиг, который генерируется через CRD
## Ожидание готовности deployment/daemonset - `kubectl rollout status ...`
## Есть ожидание готовности CRDs. Если добавляются новые CRDs - их ожидание надо добавить в `playbook-app/haproxy-install.yaml`
## Есть работа с `vault + ESO`
## ---
## `--tags pre, install, post`
## ---
##
- установка + обновление (версия, конфиг)
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/haproxy-install.yaml`
- Есть дополнительный playbook, для перезапуска
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/haproxy-restart.yaml`

## ---
## cilium (pre + post). yaml -> helm
## ---
## Есть hubble-ui, который доступен по URL -> требуется Certificate (cert-manager-CRD)
## Это просто дополнительная конфигурация
## Тут не запускается никаких контейнеров
## Устанавливается: NetworkPolicy, kube-system (NetworkPolicy), CiliumClusterWideNetworkPolicy
## ---
##
- установка + обновление (версия, конфиг)
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/cilium-install.yaml --tags pre,post`
  - Ставится: network-policy (для kube-system), ingress (hubble-ui)

## ---
## Linstor. (Piraeus-operator) Официальный helm (два helm-chart)
## ---
## Автоматически подхватывает конфиг, который генерируется через CRD
## Ожидание готовности deployment/daemonset - `kubectl rollout status ...`
## Есть ожидание готовности CRDs. Если добавляются новые CRDs - их ожидание надо добавить в `playbook-app/linstor-install.yaml`
## ---
## Важно_1: может работать в абсолютно разных условиях
## 1. VPD/VDS + 1 диск с OS = используется sparse-file (fileThinPool / filePool)
## 2. VPD/VDS + 1 диск с OS + N диск RAW = sparse-file (fileThinPool / filePool) + lvmThinPool / lvmPool (для RAW устройств)
## 3. BareMetal + N диск RAW = lvmThinPool / lvmPool
## ---
## `--tags pre, install-operator, install-cluster, post`
## ---
##
- установка + обновление (версия, конфиг)
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/linstor-install.yaml`
  - Ставится: network-policy, operator, controller, satellite
  - Все конфиги, ставятся через CR (как Vault): StorageClasses, storegaPool, настройки
- Обновление NetworkPolicy
  - Есть официальный набор: https://github.com/piraeusdatastore/piraeus-operator/tree/v2/config/extras/monitoring
  - Это версия для kustomize, не для helm
  - НО - она не используется, написана своя версия NetworkPolicy
- обновление мониторинг
  - Есть официальный набор: https://github.com/piraeusdatastore/piraeus-operator/tree/v2/config/extras/monitoring
  - Это версия для kustomize, не для helm
  - Три файла xxxxx-monitor = они нам нужны
  - скачать эти файлы, адаптировать под Helm (буквально несколько строк)

## ---
## DEPRECATED
## longhorn. Официальный helm
## ---
## Есть UI, который доступен по URL -> требуется Certificate (cert-manager-CRD)
## Автоматически подхватывает конфиг. Обновили конфиг в `ConfigMap` -> сразу подхватил и начал использовать
## `namespace: longhorn-system`, МЕНЯТЬ НЕЛЬЗЯ. Так написано в документации
## Пример обновленного конфига - `docs/longhorn/other/...`
## Ожидание готовности deployment/daemonset - `kubectl rollout status ...`
## Есть ожидание готовности CRDs. Если добавляются новые CRDs - их ожидание надо добавить в `playbook-app/longhorn-install.yaml`
## Есть создание секретов для БЭКАПА в S3 -> использует CRD от ESO (Но секреты сразу работать не будут, так как они появляются в VAULT, позже)
## Есть работа с `vault + ESO`
## ---
## Важно_1. Для создания секретов для работы с backup - их нужно определить в `hosts-vars-override/` (пример в `hosts-vars-override/.example`)
## После определния они будут использоваться при установке `longhorn-install.yaml`
## ---
## Важно_2. `node-tags`: для их автоматической установки на Nodes используется отдельный playbook `... playbook-app/longhorn-tags-sync.yaml`
## Синхронизация `node-tags` вызывается отдельно
## То есть: после установки longhorn, после добавления node, после изменения `node-tags` в `hosts-vars-xxx`
## ---
## `--tags pre, install, post`
## ---
## 
- установка + обновление (версия, конфиг)
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/longhorn-install.yaml`
  - Ставится: longhorn, network-policy, ingress (longhorn-ui)
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/longhorn-tags-sync.yaml`
  - синхронизация всех node-tags. Именно у CRD объекта: nodes.longhorn.io

## ---
## Теперь, можно запускать что-то, что требует volume (PVC)
## ---

## ---
## Vault. (Bank-vaults) Официальный helm
## ---
## ЕСТЬ проблема: официальный helm не работает из РФ (Региональная блокировка)
## Решение: зайти на github (https://github.com/hashicorp/vault-helm) в раздел с релизами
## Скачать ZIP архив последнего релиза, достать все templates, Chart.yaml и values.yaml
## ---
## Важно_1. Установка идет через helm-chart: bank-vaults (https://bank-vaults.dev, https://github.com/bank-vaults)
## Для хранения ключей используется `k8s-secret`
## Есть playbook, для доставки ключей k8s.Secrets -> manager-nodes (как json файл)
## ---
## Важно_2. Работа с конфигурацией идет через Operator + CRDs
## все политики, роли, методы авторизации и так далее - определяются в Vault (CRDs)
## Для их синхронизации с Vaul-instance, надо вызвать `... playbook-app/vault-install.yaml --tags vault-cr`
## ---
## Есть web-ui, который доступен по URL -> требуется Certificate (cert-manager-CRD)
## Есть volume -> требуется работа с СХД (dynamic PVC)
## Ожидание готовности deployment/daemonset
## ---
## `--tags pre, operator, vault-cr, post`
## ---
##
- установка (обновление) + конфигурация + синхронизация политик = ОДИН playbook
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/vault-install.yaml`
  - Ставится: operator, vault-0 (CRDs, StatefulSet)
- Обновление
  - все устанавливается через официальный helm-chart
  - НО RBAC: почему-то решили ставить отдельно. Почему - загадка
  - собрать официальный yaml: `kubectl kustomize https://github.com/bank-vaults/vault-operator/deploy/rbac > vault-rbac-official.yaml`
  - поправить содержимое под HELM
  - перенести в `playbook-app/charts/vault/pre`

## ---
## Теперь, можно запускать что-то, что требует secrets
## ---
## В файле `hosts-vars/` + `hosts-vars-override/` есть отдельная структуры для управления VAULT (какие политики, роли, аккаунты и пути для секретов)
## Пример: `./readme-vault.md`
## Вызов синхронизации VAULT: `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/vault-install.yaml --tags vault-cr`
## План, при добавлении чего-то в VAULT
## 1. добавить в `hosts-vars-override/` новые данные (policy + role)
## 2. Вызвать синхронизацию
## 3. Уже отдельно (ArgoCD или как-то иначе) загрузить в kubernetes: Namespace, ServiceAccount, SecretStore (CRD), ExternalSecret (CRD)
## ВАЖНО: синхронизация полностью синхронизирует структуру в VAULT (добавить, обновить, удалить)
## ---

## ---
## SeaweedFS (S3). Официальный helm-chart
## ---
## В файле `hosts-vars/` + `hosts-vars-override/` есть отдельная структуры для управления SeaweedFS-S3-API
## Есть web-ui (очень странный и неинтересный), который доступен по URL -> требуется Certificate (cert-manager-CRD)
## Есть s3-api, доступен по URL -> требуется Certificate (cert-manager-CRD)
## Есть volume -> требуется работа с СХД
## Ожидание готовности deployment/daemonset
## ---
## Важно_1. Отдельна работа с: policy, user, bucket, identity-distribute
##   все описывается и синхронизируется полностью декларативно
##   переменные: `seaweedfs_managed_policies_extra`, `seaweedfs_identities_extra`, `seaweedfs_sync_buckets_extra`
##   вызвать playbook: `... playbook-app/seaweedfs-install.yaml --tags policy-sync,user-sync,bucket-sync,identity-distribute`
##   Результат: политики созданы, Creds созданы в S3-api, доставлены в указнные vault-path, бакеты созданы
## ---
## `--tags pre, postgresql, install, policy-sync, user-sync, identity-distribute, bucket-sync, post`
## ---
##
- установка + обновление (версия + конфиг)
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/seaweedfs-install.yaml`
  - Установится: ...
- Есть отдельный playbook для перезапуска
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/seaweedfs-restart.yaml`

## ---
## Teleport. Официальный helm-chart
## ---
## что устанавливается: (proxy + auth + operator)
## ---
## Важно_1. Все ресурсы teleport управляются через CRD. То есть: если надо добавить новую роль или нового пользователя
## Добавляем новые значения в hosts-vars-override и вызываем `... playbook-app/teleport-install.yaml --tags configure`
## Оператор работает только в одном направлении: CRD -> Teleport. Если что-то добавить в Teleport-UI - оно не появится в CRD
## Если через UI обновить что-то, что есть в CRD - то через 1 минуту оно вернется к состоянию CRD
## То есть: через UI, мы только смотрим и ничего не создаем
## ---
## Важно_2. После установки - надо получить ссылку на сброс и установку пароля для пользователя `superadmin`
## `kubectl exec -n teleport deploy/teleport-auth -- tctl users reset superadmin`
## Перейти по ссылке и установить пароль через UI
## ---
## Важно_3. Как проверить, что operator - все синхронизировал
## `kubectl exec -n teleport deploy/teleport-auth -- tctl get role/superadmin`
## `kubectl exec -n teleport deploy/teleport-auth -- tctl users ls`
## ---
## Важно_4. Для авторизации через консоль (например для kubectl) - работает только MFA=OTP (через PassKey - не получилось)
## ---
## Важно_5. Operator, может Отвалиться по преколу. У него сертификат на 1 час, и если он его не обновит - то отвалится
## почему это не прописано в healthcheck = загадка
## что искать в логах (operator + auth)
- current time 2026-04-12T09:32:38Z is after 2026-04-12T08:36:50Z
- write tcp 10.64.15.71:49648->10.132.251.113:3025: write: broken pipe
- tls: expired certificate
## Чинится простым перезапуском: `kubectl rollout restart deployment/teleport-operator -n teleport`
## ---
- установка
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/teleport-install.yaml`
  - Установится: auth, proxy, operator
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/teleport-ssh-agent-install.yaml`
  - установится на КАЖДУЮ node агента, для доступа к node по SSH
- Есть отдельный playbook для перезапуска
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/teleport-restart.yaml`

## ---
## gitlab. Официальный helm
## ---
## Есть UI + API, доступны по URL -> требуется Certificate (cert-manager-CRD)
## Есть UI, доступен по URL -> требуется Certificate (cert-manager-CRD)
## Ожидание готовности deployment/daemonset - `kubectl rollout status ...`
## Есть дополнительный файл для `vault + ESO`
## ---
## Важно_1: про компоненты
## - `gitlab-exporter` - hardcoded 1 в шаблоне (в самом официальном helm)
## - `gitaly` - StatefulSet, количество реплик определяется через global.gitaly.internal.names. По дефолту = 1. RollingUpdate + StatefulSet = убить, а потом создать
##   - Но если их больше чем 1 - там какая-то возня начинается с Praefect (репликация git-данных между узлами)
## ---
## `--tags pre, postgresql, redis, minio, install, post`
## ---
## 
- установка + обновление (версия + конфиг)
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/gitlab-install.yaml`
  - Ставится: gitlab-minio, ingress (minio-api, minio-console-ui)
  - Ставится: gitlab, ingress (UI, git, pages, registry, ssh-tcp)
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/gitlab-configure.yaml`
  - конфигурация (Достать пароль админа, положить его в vault, создать PersonalAccessToken для админа и положить его в vault)

## ---
## Gitlab-Runner. официальный helm
## ---
## Ожидание готовности deployment/daemonset - `kubectl rollout status ...`
## Есть дополнительный файл для `vault + ESO`
## ---
## Важно_1: тут производится именно установка helm-chart, без конфигурации в Gitlab-instance
## То есть: Регистрация раннера на GitLab - производится в ручном режиме
## Порядок действий для установки
## - Зайти в GitLab и создать `instance-runner` + получить его токен
## - Сохранить токен в VAULT (по правильному пути - указан в `ESO`) в переменную `token`
## - Попраить конфиг (`hosts-vars/` + `hosts-vars-override/`). Там полный toml файл
## - установить gitlab-runner (helm)
## ---
## Важно_2
## - все данные для minio (s3-cache) = будут созданы автоматически
## ---
## `--tags pre, install`
## 
- установка + обновление (версия + конфиг)
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/gitlab-runner-install.yaml`

## ---
## argocd. yaml -> helm
## ---
## Есть UI, доступен по URL -> требуется Certificate (cert-manager-CRD)
## Нет автоматической обработки новых конфигов (Как у Cilium). То есть: После обновления конфигов - ручной restart
## Есть ожидание готовности CRDs. Если добавляются новые CRDs - их ожидание надо добавить в `playbook-app/argocd-install.yaml`
## Ожидание готовности deployment/daemonset - `kubectl rollout status ...`
## Есть дополнительный файл для `vault + ESO`
## ---
## Важно_1: нужно выполнить команду `ssh-keyscan` на те git-репозитории, которые планируется использовать для argocd
## Добавить их публичные ключи в `hosts-vars-override/<cluster_name> (argocd_cm_ssh_known_hosts_extra)`. Это массив из строк
## Без этого, argocd не сможет к ним подключиться (недоверенный host)
## ---
## Важно_2: Аккаунты + политики. У ArgoCD есть механика локальных аккаунтов. Она состоит из трех частей
## ConfigMap=argocd-cm. Список аккаунтов, их capabilities и время смены пароля
## ConfigMap=argocd-rbac-cm. ОБЩИЕ политики для всего ArgoCD. какой аккаунт, какие права имеет в том или ином проекте
## k8s.Secret=argocd-secret. Парлли в формате bcrypt для каждого аккаунта
## Эта логика зашита в stage = `accounts-sync`
## управляется переменными: argocd_local_accounts (список аккаунтов) + argocd_policy_csv_list (список политик)
## пароли для аккаунтов (состояние) - хранится в VAULT. в одном JSON объекта по одному пути. Для всех аккаунтов сразу
## Какая логика синхронизации
## - account: есть локально но его нет в VAULT = сгенерировать пароль, положить в VAULT, положить в k8s.secret=argocd-secret
## - account: нет локально, но есть в VAULT = удалить из VAULT, удалить из k8s.secret=argocd-secret
## - account: есть и там и там = приоритет отдается VAULT. Он точка правды. то есть: если у аккаунтов разливается bcrypt пароля в VAULT и в k8s.secret=argocd-secret = то в k8s.secret=argocd-secret будет. положен bcrypt из VAULT
## - account: есть и там и там, проверка поле passwordMTime. Если изменилось = новый пароль и положить в vault + k8s.secret=argocd-secret
## После внесения изменений в политики и аккаунты надо сделать
## - `ansible-playbook -i ... playbook-app/argocd-install.yaml --tags install` - синхронизировать аккаунты и политики (ConfigMap)
## - `ansible-playbook -i ... playbook-app/argocd-install.yaml --tags accounts-sync` - синхронизировать пароли от аккаунтов в VAULT + k8s.secret
## ---
## `--tags crds, pre, install, accounts-sync, post, gitops`
## ---
##
- установка + конфигурация
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/argocd-install.yaml`
  - Ставится: argocd, network-policy, ingress (argocd-ui, h2c-grpc), lockdown default-project (gitops), локальные аккаунты (accounts-sync)
  - Локальные аккаунты (login + пароль, включая custom-admin) — декларативно через `argocd_local_accounts` в `hosts-vars-override/`; пароли генерятся в рантайме и кладутся в Vault `eso-secret/argocd/accounts/creds`. Ротация: bump `passwordMtime` у аккаунта → `argocd-install.yaml --tags accounts-sync`.
  - Контракт для внешнего git-ops repo: имена из `argocd_local_accounts` ссылаются в `AppProject.spec.roles[].groups` как есть (строка-username ArgoCD биндит её к роли проекта через Casbin). Custom-admin получает глобальный `role:admin` через `argocd_policy_csv_list` здесь.
- обновление (версия)
  - Скачать новый yaml. https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
  - Разнести yaml на несколько файлов
    - `playbook-app/charts/argocd/crds/crds.yaml` - только CRD (там примерно 24к строк)
    - `playbook-app/charts/argocd/install/templates/argocd.yaml` - все, кроме CRD
  - Версия не указывается в `hosts-vars/` | `hosts-vars-override/` -> так как версия будет в `*.yaml`
  - Пример обновленного конфига - `docs/arocd/...`
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/argocd-install.yaml`
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/argocd-restart.yaml`
- обновление (конфиг)
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/argocd-install.yaml`
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/argocd-restart.yaml`

## ---
## argocd-git-ops. yaml -> helm
## ---
## Установка всех необходимых ресурсов k8s - для git-ops паттерна
## Тут нет запуска компонентов (Deployment, CronJob и так далее)
## Это создание ресурсов k8s (AppProject, Application)
## Есть дополнительный файл для `vault + ESO`
## ---
## Важно_1: сгенерировать ssh-keys + положить их в vault
## Чтобы argocd смог подключиться к репозиторию - нужен k8s.secret (который создается через ESO, который смотрит в VAULT)
## Эти два шага нужно выполнять в `РУЧНОМ РЕЖИМЕ`
## ---
## Важно_2: Создать репозиторий + добавить к нему deploy-keys (которые были созданы в пункте выше)
## Эти два шага нужно выполнять в `РУЧНОМ РЕЖИМЕ`
## ---
## Важно_3: Столько ESO для Argocd, сколько нужно разных репозиториев. Не ключей - а именно репозиториев
## В теории и на практике - можно создать хоть 10 k8s.secret с одинаковым repoUrl. В этом случае - argocd возьмет тот, который первый вернется в ответе от kube-api
## чтобы избежать такой путаницы: 1 (repo_url + ESO.secret + vault.secret + k8s.secret)
## ---
## Важно_4: последовательность установки
## - настроить необходимые конфиги для argo-cd-git-ops (`hosts-vars-override/`)
## - `argocd_git_ops_apps` (какие проекты и приложения нужно создать)
## - `eso_vault_integration_argocd_extra`
## - секреты типа: `git_ops_repo_pattern`/`git_ops_repo_direct`
## - установить `argocd-git-ops` + проверить что все ресурсы установились корректно
## - Создать ssh-keys (private + public) + положить их в Vault (ESO - создаст из них k8s.secret)
## - Создать репозитории (URL которых указаны в `argocd_git_ops_apps`) + добавить к ним deploy-keys (чтобы argocd имел к ним доступ)
## ---
## Параметры в `hosts-vars/` + `hosts-vars-override/`
## ---
##
- установка + обновление (конфиг)
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/argocd-install.yaml --tags gitops`
  - Ставится: argo-proj + argo-application

## ---
## mon-system
## prometheus-operator + prometheus + alertmanager + node-exporter + ksm + loki + vector + grafana. yaml -> helm
## ---
## Есть UI, доступен по URL -> требуется Certificate (cert-manager-CRD)
## Есть ожидание готовности CRDs. Если добавляются новые CRDs - их ожидание надо добавить в `playbook-app/mon-system-install.yaml`
## Ожидание готовности deployment/daemonset - `kubectl rollout status ...`
## Grafana - Есть дополнительный файл для `vault + ESO`
## ---
## Важно_1. Через указание --tags crds = можно установить только CRDs
## ---
## `--tags crds, pre, prometheus-operator, prometheus, alertmanager, node-exporter, ksm, loki, vector, grafana, post`
## ---
## 
- установка
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/mon-system-install.yaml`
- обновление (версия - prometheus-operator)
  - Скачать новый yaml. https://github.com/prometheus-operator/prometheus-operator/releases
  - Разнести yaml на несколько файлов
    - `playbook-app/charts/mon-system/crds/crds.yaml` - сюда все CRD, (примерно 80_000 строк)
    - `playbook-app/charts/mon-system/prometheus-operator/templates/prometheus-operator.yaml` - вся установка (Deplyment, RBAC, Service)
  - Есть изменения в дефолтных конфигах. Их надо не затерепть. То есть: после вставки нового `*.yaml` -> надо вернуть обновленные дефолиные конфиги
  - Версия указывается в `hosts-vars/` | `hosts-vars-override/` -> внутри `*.yaml` надо не потерять щаблонизацию
- обновление (версия: node-exporter, ksm, loki, vector, grafana)
  - просто обновить версии в hosts-vars

## ---------------------
## ---------------------
## ---------------------

# ---------
# ЕЩЕ НЕ ГОТОВО
# ---------

## Zitadel. официальный helm
## ---
## Параметры в `hosts-vars/` + `hosts-vars-override/`
## ---
Логин и пароль после установки
Логин:  zitadel-admin@zitadel.zitadel-k8s-v2.drawapp.ru
Пароль: Password1!

## ---------
## ---Secrets-Rotation
## ---------

## ---
## Добавить ротацию пароля
## ...
## ...
## ...
## ---

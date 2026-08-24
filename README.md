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
# bastion-proxy
# ---------
# ---------
## Задача: в кластере установлены docker-registry + S3 хранилище, к ним нужно дать доступ по DNS, НО - не светить реальный IP адрес кластера
## Это можно сделать через Cloudflare или ему подобных, WAF. НО - там есть лимиты на размер файла
## У Cloudflare, на САМОМ дорогом тарифе - лимит в 500мб. А контейнер может весить 2-3-10гб. Не вариант
## ---
## Покупается отдельный сервер, назовем его bastion-proxy-1. Он никак не связан с кластером, вообще
## На этом сервере устанавливается haproxy и включается `proxy-protocol-v2`
## Все запросы приняты на: 80, 443, 10_000-12_000 = проксируются на указанный IP адрес, маскируя конечный адрес назначения
## Все DNS направляются на этот сервер, в режиме серого облачка (DNS-only). Сертификаты - выпукаем через Cert-Manager
## Такие запросы принимает Traefik-ingress | Haproxy-ingress
## ---
## Теперь надо снять proxy-protocol-v2. И тут есть особенность
## - Traefik. Просто добавляем в конфиг entrypoint = `proxyProtocol.insecure=true`. Traefik - принимает и proxy-protocol и обычные прямые запросы
## - Haproxy. Тут так нельзя. ИЛИ - proxy-protocol-v2 ИЛИ прямые запросы. тут нет опции = принимать все и работать
## ---
## `--tags node-install, haproxy-install, haproxy-config, verify`
## ---
##
- установка
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/bastion-proxy-install.yaml`

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
## Есть web-ui, который доступен по URL -> требуется Certificate (cert-manager-CRD)
## Есть volume -> требуется работа с СХД (dynamic PVC)
## Ожидание готовности deployment/daemonset
## ---
## Важно_1. Установка идет через helm-chart: bank-vaults (https://bank-vaults.dev, https://github.com/bank-vaults)
## Для хранения ключей используется `k8s-secret`
## Есть playbook, для доставки ключей k8s.Secrets -> manager-nodes (как json файл)
## ---
## Важно_2. Работа с конфигурацией идет через `Operator + CRDs`
## все политики, роли, методы авторизации и так далее - определяются в Vault (CRDs)
## Для их синхронизации с Vaul-instance, надо вызвать `... playbook-app/vault-install.yaml --tags vault-cr`
## ---
## Важно_3. Пользоваться root-token = нужно только один раз, для создания админа
## Есть политика - `vault-admin` (`./hosts-vars/vault.yaml`). Она дает все права, для всего
## после установки vault, зайти в UI или CLI под root-token, открыть AuthMethod = UserPass (он уже активирован)
## Добавить нового пользователя: Например `admin-rw`, создать пароль и указать политику `vault-admin`
## Выйти из `root-token` и авторизоваться под новый пользователем: `admin-rw`
## ---
## Важно_4. Добавление пользователей с userpass (Auth method)
## - зашли в UI (под root token)
## - добавили нового пользователя + указали ему пароль (без этого - нельзя создать пользоватяеля)
## - переали логин + пароль сотруднику и просим его сменить пароль
## - ОШИБКА. недостаточно прав
## Это происходит из-за того, что при создании пользователя - ему крепится default политика. А в ней - нет разрешения на сброс пароля
## Нужно такое разрешение: `path "auth/userpass/users/{{identity.entity.aliases.<ACCESSOR>.name}}/password" { capabilities = ["update"] }`
## Что такое ACCESSOR ? Когда содается (включается) метод userpass_auth = он получае уникальный ID = это и есть ACCESSOR (если по простому)
## Нужно помнить правило: ACCESSOR стабилен пока `userpass` не пересоздан или не отключен и влкючен заново
## Чтобы его узнать, есть два варианта
## - зайти в UI -> access -> userpass -> confugure. и там будет поле типа: auth_userpass_1q2w3e4r. Это ACCESSOR
## - зайти в CLI и выполнить команду: `vault read -field=accessor sys/auth/userpass`
## Пример политики: `user-self-service` (`./hosts-vars/vault.yaml`)
## ---
## Важнл_5. Как работать с токенов в vault-cli
## авторизоваться: `vault login` -> потом он попросит ввести токен
## все успешно, авторизация прошла. Можем выполнять команды, в соответствии нашему токену
## чтобы выйти: НЕТ команды `vault logout` или что-то такое
## Нужно делать так: `rm -f ~/.vault-token` и потом проверить `vault token lookup`
## ---
## Важно_6. как настроить OIDC
## основная проблема = `clientSecret`. Чтобы он появился в kubernetes (k8s-secret) = нужен рабочий VAULT + ESO интеграция
## а если vault включить OIDC и указать неверный secret = то он не запустится
## Получается такая логика
## - Запустить полностью VAULT без OIDC (`vault_oidc_enabled = false`)
## - зайти в vault под root token и положить нужные clientSecret + clientId
## - переопределить `vault_oidc_enabled = true`
## - перезапустить установку: `ansible-playbook ... --tags pre,vault-cr`
## - добавится: ExternalSecret (для oidc.clientSecret), конфиги для vault-operator, и сам VAULT перезапустится
##
## ---
## `--tags pre, operator, vault-cr, unseal-keys, post`
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
## Zitadel. официальный helm
## ---
## Параметры в `hosts-vars/` + `hosts-vars-override/`
## ---
## Важно_1. если в момент установки не указать login + password, то дефолтные данные для входа будут
##   Логин: `zitadel-admin@zitadel.zitadel-k8s-v2.drawapp.ru` (zitadel-admin@<ORG_NAME>.<Zitadel_domain>)
##   Пароль: `Password1!`
## ---
## Важно_2. Пароль для первого `instance-admin`. Этот пароль задается СТРОГО ОДИН раз в момент установки
## Кладется в VAULT, срабатывает ESO -> запускаем саму ZITADEL
## Никаких автоматическиз механизмов смены этого пароля в k8s-ansible = не предусмотрено
## Зайти под этим `instance-admin`, сменить пароль, чтобы не забыть пароль - зайти в VAULT, и положить этот пароль в VAULT
## То есть: тут нет механики как у GitLab - что можно ротировать пароль повторным запуском k8s-ansible
## ---
## `--tags pre, postgresql, install, post`
## ---
##
- установка + обновление (версия + конфиг)
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/zitadel-install.yaml`
- Есть отдельный playbook для перезапуска
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/zitadel-restart.yaml`

## ---
## Теперь, можно установить OIDC (SSO) для компонентов
## ---
## Обий принцип такой
##   Зайти в ZITADEL + Создать организацию + Создать там проекты + все это настроить
##   Получить clientId | clientSecret | что-то еще и сохранить это в VAULT
##   Перед запуском нового компонента, который может работать по OIDC = подготовить конфиг
##   Достать organization_id, создать пользователя в ZITADEL и добавить его в проект
##   Запустить компонент

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
- Есть отдельный playbook для перезапуска
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/seaweedfs-restart.yaml`

## ---
## FileStash (S3). Официальный helm-chart
## ---
## Есть web-ui (/admin) + web-ui (клиентский, на корне). Доступен по URL -> требуется Certificate (cert-manager-CRD)
## Есть volume -> требуется работа с СХД
## Ожидание готовности `StatefulSet`
## ---
## Важно_1. Установка - НЕ ДЕКЛАРАТИВНАЯ. Декларативно задается (через ENV) только: пароль админа и external-url для доступа
## - ADMIN_PASSWORD = из ESO-секрета (bcrypt-хэш)
## - APPLICATION_URL = значение из hosts-vars
## Эти env Filestash сам на старте пишет в config.json (auth.admin и general.host). НА КАЖДОМ СТАРТЕ
## То есть: поменяли пароль админа в VAULT, сработал ESO, перезапустили контейнер = новый пароль
## Все остальные настройки: подключения, плагины и так далее - нужно делать в UI
## Config (config.json) где хранятся все настройки - лежит в PVC, и мутируется приложением (при старте и при любых изменениях в admin-ui)
## ---
## `--tags pre, install, post`
## ---
##
- установка + обновление (версия + конфиг)
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/filestash-install.yaml`

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
## Важно_2: про root user password
## - сразу после установки GitLab - создается k8s.secret = `gitlab-initial-root-password`. Это создает сам HELM-CHART
## - этот пароль сразу же удаляется. Получить доступ к gitlab = нельзя никак. На текущий момент
## - запускает tag = `rppt-config`. Он проверяет - есть ли пароль для root в VAULT
## - если в VAULT нет пароля или его passwordMTume !== ansibleConfig.gitlab.passwordMTime = генерируется новый пароль
## - потом этот пароль проверяется в GitLab = подходит или нет
## - Если не подходит, то пароль в GitLab обновляется на этот пароль
## - Если root user, поменяет пароль в Gitlab-UI = то при следующем прогоне ansible = пароль сбросится на тот, который в VAULT
## То есть правило: Пароль root-user, управляется полностью через ansible
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
## `--tags crds, pre, install, post, cfg` + точечные `accounts-sync`, `accounts-distribute`
## Стадий `rbac` и `gitops` больше нет - обе слились в единую `cfg`
## ---
##
- установка + конфигурация
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/argocd-install.yaml`
  - Ставится: argocd, network-policy, ingress (argocd-ui, h2c-grpc), lockdown default-project (cfg), локальные аккаунты (accounts-sync)
  - Локальные аккаунты (login + пароль, включая custom-admin) — декларативно через `argocd_local_accounts` в `hosts-vars-override/`; пароли генерятся в рантайме и кладутся в Vault `eso-secret/argocd/accounts/creds`. Ротация: bump `passwordMtime` у аккаунта → `argocd-install.yaml --tags accounts-sync`.
  - Контракт для внешнего git-ops repo: имена из `argocd_local_accounts` ссылаются в `AppProject.spec.roles[].groups` как есть (строка-username ArgoCD биндит её к роли проекта через Casbin). Custom-admin получает глобальный `role:admin` через `argocd_policy_csv_list` здесь.
- обновление (версия)
  - Скачать новый yaml. ВАЖНО: нужен `namespace-install.yaml` КОНКРЕТНОГО тега, а не `stable/manifests/install.yaml`
    - `git -C sources/argo-cd show v<version>:manifests/namespace-install.yaml > playbook-app/charts/argocd/install/templates/install.yaml`
  - Разнести yaml на несколько файлов
    - `playbook-app/charts/argocd/crds/crds.yaml` - только CRD (там примерно 24к строк)
    - `playbook-app/charts/argocd/install/templates/install.yaml` - все, кроме CRD
  - Версия не указывается в `hosts-vars/` | `hosts-vars-override/` -> так как версия будет в `*.yaml`
  - Пример обновленного конфига - `docs/arocd/...`
  - ArgoCD ставится namespace-scoped: в `namespace-install.yaml` нет трех ClusterRole и трех ClusterRoleBinding
    - Файл `stable/manifests/install.yaml` - это cluster-install, он вернет ArgoCD права cluster-admin. Брать нельзя
  - Рестарт после установки ОБЯЗАТЕЛЕН: helm upgrade не пересоздает под контроллера, а открытые watch переживают отзыв RBAC
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/argocd-install.yaml`
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/argocd-restart.yaml`
- обновление (конфиг)
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/argocd-install.yaml`
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/argocd-restart.yaml`

## ---
## argocd. стадия `cfg` (бывшие `rbac` + `gitops`). yaml -> helm
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
## - `argocd_git_ops_app_projects` + `argocd_git_ops_applications` (какие проекты и приложения нужно создать)
## - `eso_vault_integration_argocd_extra`
## - секреты типа: `git_ops_repo_pattern`/`git_ops_repo_direct`
## - прогнать стадию `cfg` + проверить что все ресурсы установились корректно
## - Создать ssh-keys (private + public) + положить их в Vault (ESO - создаст из них k8s.secret)
## - Создать репозитории (URL которых указаны в `argocd_git_ops_applications`) + добавить к ним deploy-keys (чтобы argocd имел к ним доступ)
## ---
## Параметры в `hosts-vars/` + `hosts-vars-override/`
## ---
##
- установка + обновление (конфиг)
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/argocd-install.yaml --tags cfg`
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/argocd-restart.yaml`
  - Ставится: ClusterRole `argocd-managed-*` + RoleBinding по namespace, lockdown default-project, AppProject, Application
  - ⚠️ Порядок: `kargo --tags cfg` -> `argocd --tags cfg`. RoleBinding едет в namespace Kargo-проектов, а их создает стадия kargo/cfg

## ---
## argo-rollouts. yaml -> helm
## ---
##
- Установка + конфигурация
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/argo-rollouts-install.yaml`
- обновление (версия)
  - Скачать новый yaml. https://raw.githubusercontent.com/argoproj/argo-rollouts/refs/tags/v1.9.1/manifests/install.yaml
  - Разнести yaml на несколько файлов
    - `playbook-app/charts/argo-rollouts/crds/crds.yaml` - только CRD (там много строк)
    - `playbook-app/charts/argo-rollouts/install/templates/install.yaml` - все, кроме CRD
  - Версия не указывается в `hosts-vars/` | `hosts-vars-override/` -> так как версия будет в `*.yaml`
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/argo-rollouts-install.yaml`
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/argo-rollouts-restart.yaml`
- есть отдельный playbook для перезапуска
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/argo-rollouts-restart.yaml`

## ---
## argo-events. yaml -> helm
## ---
## Компонент отвечает ТОЛЬКО за контроллер и вебхук. Прикладной контур (шина,
## EventSource, Sensor) может жить либо в том же namespace, либо в ЧУЖОМ.
## ---
## Куда смотрит контроллер — одна переменная `argo_events_cr_namespace`
## - пусто (дефолт) = апстримный режим, контроллер видит CR в СВОЁМ namespace
## - задано = к манифесту подмешивается патч `--managed-namespace`, и контроллер
##   смотрит ТУДА ВМЕСТО своего. Отдельного bool-тумблера нет: непустая строка и
##   есть признак включения
## ⚠️ Контроллер видит CR РОВНО В ОДНОМ namespace. Это ограничение продукта:
##    `--managed-namespace` принимает одно значение и кладётся единственным
##    ключом в кэш контроллера. Значит включение чужого namespace — это ПЕРЕНОС
##    реконсиляции, а не расширение: CR в своём namespace перестанут обновляться
## ---
## ⚠️ Порядок стадий: `crds -> pre -> pre-cfg -> install -> post -> cfg`
##    `pre-cfg` идёт ПЕРЕД `install` не для красоты. На холодном старте без прав
##    в cr-namespace контроллер НЕ деградирует, а УМИРАЕТ: кэш не
##    синхронизируется, controller-runtime не дожидается за CacheSyncTimeout
##    (2 минуты) и процесс завершается -> CrashLoopBackOff.
##    Отказ ОТЛОЖЕННЫЙ: readiness это healthz.Ping, отвечает 200 независимо от
##    кэша, поэтому под успевает стать Ready и `helm --wait` считает install
##    успешным. Падение начинается уже после того, как playbook отрапортовал ОК.
##    Работает перестановка потому, что субъекты в биндингах Kubernetes НЕ
##    валидируются: RoleBinding создаётся раньше, чем появится сам SA.
## ---
## Что берётся из cluster-base — НИЧЕГО
## - namespace `argo-events` компонент создаёт сам (`--create-namespace`)
## - cr-namespace (если задан) компонент создаёт сам же — стадия `pre-cfg`,
##   шаблон `charts/argo-events/pre-cfg/templates/cr-namespace.yaml`. Объявлять
##   его в `cluster-base` НЕЛЬЗЯ: получится второй владелец объекта
## ---
## Что кладёт ansible в cr-namespace — РОВНО ДВЕ вещи
## - права контроллера: Role + RoleBinding, шаблон `charts/argo-events/pre-cfg/
##   templates/controller-rbac.yaml`, рендерится автоматически при непустом
##   `argo_events_cr_namespace`. Объект ложится в ЧУЖОЙ namespace, а
##   субъект (SA контроллера) остаётся в СВОЁМ — путать нельзя
## - то, что оператор сам объявил в `argo_events_rbac_*` списках
## Всё остальное там (NetworkPolicy, ESO, PodMonitor, прикладные CR) — НЕ забота
## компонента. Кладёт владелец содержимого, у нас это git-ops через ArgoCD
## ---
- Установка + конфигурация
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/argo-events-install.yaml`
  - Отдельные стадии: `--tags crds|pre|pre-cfg|install|post|cfg`
- обновление (версия)
  - Вендоренный апстрим-манифест — `namespace-install.yaml` тега `vX.Y.Z` МИНУС три CRD
    - `git -C sources/argo-events show vX.Y.Z:manifests/namespace-install.yaml | sed -n '121,$p' > playbook-app/charts/argo-events/install/templates/install.yaml`
    - CRD ставит отдельная стадия `crds`. Внутри helm-релиза они означали бы, что
      `helm uninstall` каскадом снесёт все EventBus/EventSource/Sensor
  - Обновить `argo_events_version` в `hosts-vars/`
- EventBus — МАССИВ `argo_events_event_buses`, пустой по умолчанию
  - Шина не поднимется сама: элемент со спекой заводится в `hosts-vars-override/`
  - Профиль с пояснениями — закомментированным примером в `hosts-vars/argo-events.yaml`
  - ⚠️ `streamConfig.replicas` обязан совпадать с числом реплик JetStream:
    глобальный дефолт в апстримном ConfigMap равен 3, на одной реплике поток не создастся
  - ⚠️ `maxBytes` — ЧИСЛО В БАЙТАХ. Строка "6Gi" молча станет нулём, а ноль
    сервер нормализует в -1, то есть в ОТСУТСТВИЕ лимита
  - ⚠️ streamConfig применяется РОВНО ОДИН РАЗ при создании стрима, и создаёт
    его первый подключившийся EventSource или Sensor. Менять позже — только
    удалив стрим вручную
- есть отдельный playbook для перезапуска
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/argo-events-restart.yaml`
  - Перекатывает только два Deployment namespace контроллера. Шину, EventSource
    и Sensor не трогает: их создаёт контроллер, а перекат шины рвёт доставку
## ---
## Смена cr-namespace на живом кластере
## - Если в целевом namespace уже есть объекты от прошлой установки, они могут
##   принадлежать другому helm-релизу -> стадия `pre-cfg` упрётся в
##   `invalid ownership metadata`. Сначала снести старый релиз
##   (`helm -n <ns> uninstall <release>`), потом ставить
## - Между сносом и стадией `pre-cfg` контроллер останется без прав. Уже
##   ЗАПУЩЕННЫЙ контроллер это переживает (рефлекторы ретраят, процесс жив),
##   умирает только холодный старт

## ---
## kargo. официальный helm-chart
## ---
## Движок промоушенов. Стадии: `pre -> install -> post -> cfg`
## ---
## Что берётся из cluster-base — НИЧЕГО
## - namespace `kargo` компонент создаёт сам
## - namespace КАЖДОГО Kargo-проекта заводит стадия `cfg`, в одном релизе с
##   самим Project. Обязательную метку `kargo.akuity.io/project: "true"` шаблон
##   ставит сам; аннотация `kargo.akuity.io/keep-namespace: "true"` берётся из
##   элемента `kargo_projects` и уезжает и на namespace, и на Project
## - namespace `kargo-cluster-secrets`, `kargo-shared-resources`,
##   `kargo-system-resources` создаёт САМ апстримный чарт. В cluster-base их
##   объявлять НЕЛЬЗЯ — получится второй владелец и релиз упадёт
## ---
## Порядок онбординга нового проекта
## 1. `kargo --tags cfg` — namespace проекта + Project + права. Role
##    `kargo-viewer` в namespace проекта создаёт management-controller сам,
##    вручную не заводить
## 2. `argocd --tags cfg` — RoleBinding в этот namespace, если проектом
##    управляет ArgoCD. Порядок kargo -> argocd ОБЯЗАТЕЛЕН: namespace, в который
##    ArgoCD получает права, создаёт стадия kargo/cfg
## ---
- Установка + конфигурация
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/kargo-install.yaml`
  - Отдельные стадии: `--tags pre|install|post|cfg`
- Проекты и доступы — декларативно в `hosts-vars-override/`
  - `kargo_projects` — список проектов
  - `kargo_custom_users` — доступы. У Kargo НЕТ роли по умолчанию: права
    выдаются аннотацией с claims на любом ServiceAccount
- обновление (версия)
  - Обновить версию чарта в `hosts-vars/kargo.yaml`
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/kargo-install.yaml`
- есть отдельный playbook для перезапуска
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/kargo-restart.yaml`


## ---
## Portainer. Официальный helm-chart
## ---
## Есть web-ui, доступен по URL -> требуется Certificate (cert-manager-CRD)
## Есть volume -> требуется работа с СХД (там лежит вся БД Portainer: окружения, пользователи, стеки, настройки)
## Ожидание готовности deployment/daemonset - `kubectl rollout status ...`
## Есть дополнительный файл для `vault + ESO`
## ---
## Важно_1. Как создается пароль первого админа (login: `admin`)
## - playbook смотрит в VAULT: `eso-secret/portainer/admin-creds`, поле `password`
## - если пароля там нет = генерирует новый (32 символа) и кладет в VAULT. Если есть = НЕ ТРОГАЕТ
## - срабатывает ESO -> появляется k8s.secret = `eso-portainer-admin-creds`
## - helm-chart монтирует этот secret в под файлом и передает контейнеру `--admin-password-file=/run/portainer/admin-password`
## - Portainer читает файл, САМ хэширует значение и создает пользователя `admin`
## В VAULT пароль лежит ОТКРЫТЫМ ТЕКСТОМ - это единственный способ его узнать
##   `vault kv get eso-secret/portainer/admin-creds`
## Bcrypt на control-node не нужен (в отличие от filestash и kargo) - хэширование делает сам Portainer
## Побочный эффект: раз админ создан при старте, Portainer не требует setup-token. Ручная инициализация не нужна
## ---
## Важно_2. РОТАЦИИ ЭТОГО ПАРОЛЯ НЕТ
## Portainer применяет `--admin-password-file` ТОЛЬКО пока в его БД нет ни одного админа
## Если админ уже создан - файл игнорируется. В логах будет: `instance already has an administrator user defined, skipping admin password related flags.`
## То есть: поменять пароль в VAULT + перезапустить под = НИЧЕГО НЕ ПРОИЗОЙДЕТ. Пароль останется старым
## Тут нет механики как у GitLab - что можно ротировать пароль повторным запуском k8s-ansible
## Как менять пароль: зайти в UI под `admin`, сменить пароль там, и РУКАМИ положить новый пароль в VAULT (чтобы не забыть)
## После такой смены - значение в VAULT становится просто заметкой, ansible на него больше не смотрит
## Если пароль потерян: средствами k8s-ansible не восстанавливается
##   Гарантированный путь - удалить PVC `portainer` (namespace `portainer`) и поставить компонент с нуля
##   БД при этом теряется полностью (окружения, пользователи, стеки). Для UI-«посмотреть-что-в-кластере» это не страшно
## ---
## Важно_3. У Portainer есть ClusterRoleBinding на `cluster-admin`
## Это нужно, чтобы он управлял тем кластером, в котором сам работает. Управляется переменной `portainer_local_mgmt`
## Если поставить `false` = не будут созданы ServiceAccount и ClusterRoleBinding, под уедет на `default` SA
##   Portainer поднимется, UI откроется, но локальное окружение `Kubernetes` работать не будет (нет прав на kube-api)
## Следствие от `true`: кто зашел в UI - тот админ кластера. Поэтому на проде UI закрывается VPN
##   `portainer_ui_vpn_only_enabled: true`
## ---
## Важно_4. `portainer_trusted_origins` (защита от CSRF). По умолчанию = `portainer_ui_domain`
## Значение должно быть ГОЛЫМ ХОСТОМ: без `https://`, без порта, без пути. Иначе под падает на старте (`log.Fatal`)
## Если флаг вообще не передавать - UI за Traefik будет получать 403 на любых изменениях
##   Потому что TLS снимается на Traefik, а внутрь кластера идет обычный http, и origin-проверка не сходится
## ---
## Важно_5. Установка НЕ ДЕКЛАРАТИВНАЯ (декларативно задается только пароль админа)
## Окружения, пользователи, стеки, реестры и остальные настройки - делаются в UI и живут в БД на PVC
## Edge-агенты не используются: tunnel-порт наружу не публикуется
## ---
## `--tags pre, install, post`
## ---
##
- установка + обновление (версия + конфиг)
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/portainer-install.yaml`
- Есть отдельный playbook для перезапуска
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/portainer-restart.yaml`

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
- Есть отдельный playbook для перезапуска
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/mon-system-restart.yaml`

## ---
## cluster-base
## namespaces + RBAC. yaml -> helm
## ---
## Глобальный компонент: заводит ПРОДУКТОВЫЕ namespace'ы кластера и выдает в них права. Не привязан к приложению
## Нет workload, нет ESO, нет ingress -> поэтому два stage (namespaces + rbac), а не три фазы (pre + install + post)
## Два helm-релиза в двух своих namespace: cluster-base-namespaces (ns=cluster-namespaces) + cluster-base-rbac (ns=cluster-rbac)
## Объекты живут в ЧУЖИХ namespace или в cluster scope. Сам компонент своих подов не имеет
## Шесть списков объектов, по одному на тип: namespaces, ServiceAccount, Role, ClusterRole, RoleBinding, ClusterRoleBinding
## Элемент списка = ПОЛНАЯ спецификация объекта. Шаблон рендерит его как есть, ничего не вычисляя
## Базовые списки в hosts-vars/cluster-base.yaml ПУСТЫЕ. Все объекты задаются в hosts-vars-override/<cluster>/
## ---
## ⚠️ В stage `namespaces` по задумке лежат ТОЛЬКО ПРОДУКТОВЫЕ namespace - те, в которые раскатываются приложения
## Никаких СИСТЕМНЫХ namespace тут быть не должно: свои системные namespace заводят САМИ компоненты
##   - `argo-events-cfg` (cr-namespace) -> стадия `argo-events --tags pre-cfg`
##   - namespace каждого Kargo-проекта -> стадия `kargo --tags cfg`, одним релизом с самим Project
##   - `kargo-cluster-secrets` / `kargo-shared-resources` / `kargo-system-resources` -> сам апстримный чарт kargo
##   - namespace самих компонентов (argocd, kargo, argo-events, ...) -> их же helm-релиз (`--create-namespace`)
## Объявить такой namespace еще и здесь = ДВА владельца одного объекта -> stage падает на invalid ownership metadata
## ---
## Важно_1. Порядок stage. namespaces - до продуктов, rbac - после установки компонентов, чьи namespace он трогает
## RoleBinding нельзя создать в несуществующем namespace. Поэтому rbac идет последним (traefik-lb, seaweedfs, argocd, argo-events)
## ---
## Важно_2. Удаление элемента из cluster_base_namespaces_list = СНОС namespace вместе со всем содержимым на следующем прогоне
## Защиты нет. Аннотация argocd.argoproj.io/sync-options (Delete=false + Prune=false) защищает только от ArgoCD, но не от helm
## ---
## Важно_3. Имя объекта в чужом namespace обязано быть УНИКАЛЬНЫМ. Прием - префикс-маркер владельца (argocd-managed-*)
## Совпадение имени с объектом другого helm-релиза роняет ВЕСЬ stage: invalid ownership metadata
## Это штатная защита. Флаг --take-ownership намеренно НЕ используется
## Если перехват осознанный (объект создан ArgoCD, Kargo или руками) - усыновить вручную, проставив три метки владения
##   `kubectl label    <kind> <name> [-n <ns>] app.kubernetes.io/managed-by=Helm --overwrite`
##   `kubectl annotate <kind> <name> [-n <ns>] meta.helm.sh/release-name=<release> --overwrite`
##   `kubectl annotate <kind> <name> [-n <ns>] meta.helm.sh/release-namespace=<release-ns> --overwrite`
## Пары release/namespace: cluster-base-namespaces/cluster-namespaces + cluster-base-rbac/cluster-rbac
## После простановки меток - повторить прогон, helm примет объект как свой
## ---
## Важно_4. Список namespace для ArgoCD дублируется в VAULT: eso-secret/argocd/clusters/in-cluster, поле namespaces
## Namespace, которого нет в этом поле, ArgoCD не увидит даже при наличии RoleBinding - кеш контроллера в него не заглядывает
## Обновлять оба места сразу: hosts-vars-override/<cluster>/cluster-base.yaml + VAULT
## ---
## Важно_5. Заведение namespace для нового продукта - ДВА шага, и оба до Application в git-ops
## ArgoCD ставится namespace-scoped и создавать Namespace не может
## 1) запись в cluster_base_namespaces_list -> прогон `cluster-base --tags namespaces`
## 2) пара RoleBinding (deployer + ui) в `argocd_cfg_rbac_role_bindings` (hosts-vars-override/<cluster>/argocd.yaml)
##    -> прогон `argocd --tags cfg` + `argocd-restart`. С переходом на стадию cfg права ArgoCD уехали из cluster-base,
##    namespaced-элементов в stage rbac сейчас нет
## Объявлять Namespace в чарте продукта НЕ нужно - ArgoCD его не применит
## ---
## `--tags namespaces, rbac`
## ---
##
- установка + обновление (namespace + RBAC)
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/cluster-base-install.yaml`
- только namespace
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/cluster-base-install.yaml --tags namespaces`
- только RBAC
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/cluster-base-install.yaml --tags rbac`
- проверка прав после онбординга namespace
  - `kubectl auth can-i create deployment --as=system:serviceaccount:argocd:argocd-application-controller -n <new-ns>` -> yes
  - `kubectl auth can-i create namespace --as=system:serviceaccount:argocd:argocd-application-controller` -> no
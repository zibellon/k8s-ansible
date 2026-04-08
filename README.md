# Что тут есть
1. Есть playbooks которые запускаются ДО ИНИЦИАЛИЗАЦИИ кластера, а есть  которые СТРОГО_ПОСЛЕ инициализации
   1. Пример до: cluster-init.yaml
   2. Пример после: manager-join.yaml, worker-join.yaml, etcd-key-rotate.yaml

# Конфигурация: `hosts-vars/` + `hosts-vars-override/`
1. Сервера
   1. managers
      1. Добавить все сервера в `hosts-vars-override/hosts.yaml`
      2. ОБЯЗАТЕЛЬНО! Указать сервер, который является главным manager (master-manager) - `is_master: true`
   2. workers
      1. Добавить все сервера в `hosts-vars-override/hosts.yaml`

# Подготовка к конфигурации

## Создать директорию: `hosts-vars-override/`
Положить в неё файлы с реальными хостами и переопределениями переменных.
Все доступные переменные — в `hosts-vars/`.

## Выполнить команду: `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/node-info.yaml`
## Покажит основную информацию по всем node

# ------
# Подготовка_1
# ------
## Узнать, какой ip адрес принадлежит основному интерфейсу (ens_xxx | eth_xxx)
1. ip addr show
2. Пример вывода - ниже
3. Надо достать ip адрес. В данном случае: `10.129.0.27`

1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN group default qlen 1000
    link/loopback 00:00:00:00:00:00 brd 00:00:00:00:00:00
    inet 127.0.0.1/8 scope host lo
       valid_lft forever preferred_lft forever
    inet6 ::1/128 scope host noprefixroute 
       valid_lft forever preferred_lft forever
2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc mq state UP group default qlen 1000
    link/ether d0:0d:bd:92:06:81 brd ff:ff:ff:ff:ff:ff
    altname enp7s0
    inet 10.129.0.27/24 metric 100 brd 10.129.0.255 scope global dynamic eth0
       valid_lft 4294967229sec preferred_lft 4294967229sec
    inet6 fe80::d20d:bdff:fe92:681/64 scope link 
       valid_lft forever preferred_lft forever

# ------
# Подготовка_2
# ------
## занести внутренний `ip` в `hosts-vars-override/`
## его надо будет разрешить в `cilium-host-firewall`, чтобы можно было делать join
## есть два варианта
1. сначала ВСЕ join -> потом install cilium
   1. При таком сценарии - проблем нет
   2. cluster-init
   3. worker/manager join
   4. install cilium + cilium-host-firewall
   5. Все node уже внутри кластера и Cilium про них знает
2. cluster-init -> install cilium -> потом join
   1. install cilium + cilium-host-firewall
   2. Входящий трафик разрешается только от известных источников (внутри кластера, cilium.entities)
   3. Пока Node не добавлена в кластер = cilium видит ее как world (внешний мир)
   4. Трафик запрещен на уровне cilium-host-firewall
   5. при выполнении команды JOIN - timeout. Так как Node не может подключиться к кластеру
   6. Как решать
   7. Добавить новый сервер в hosts.yaml
   8. Обновить cilium-host-firewall
      1. Вызвать `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/cilium-install.yaml --tags post`
   9.  Это автоматически добавит в `cilium-host-firewall` новые ip адреса и обновит политику на сервере
   10. После этого делать: `... join ...`

# ------
# Важно про `namespace`
# ------
## Сменить namespace МОЖНО для любых компонентов
## Сменить namespace НЕЛЬЗЯ для некоторых компонентов. Так указано в официальной документации
- longhorn-system
- argocd

# ------
# Важно про `haproxy-apiserver-lb`
# ------
## В конфиге указаны ip адреса всех manager-node + балансировка между ними
## Запускается как `linux systemd service` (apt install haproxy) на каждой node в кластере
## Версия пакета зафиксирована в hosts.yaml (haproxy_apiserver_lb_package_version) и заморожена через apt-mark hold
## Чтобы обновить конфиг на всех нодах (например при добавлении нового manager):
- `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/haproxy-apiserver-lb-update.yaml`
  - Обновляет /etc/haproxy/haproxy.cfg и делает `systemctl reload haproxy` последовательно (serial: 1)
  - reload — graceful, без разрыва TCP соединений

# ------
# Важно про VAULT + ESO
# ------
## Во все конфиги ESO (SecretStore + ExternalSecret) добавен параметр is_need_eso: true | false
## Зачем: Это контроль - нужно ли создавать объекты ESO + vault-policy
## Например: есть GitLab.root (user + pass), их надо обязаиельно положить в vault. Но они не нужны как k8s-secret
## Чтобы положить в vault - нужен путь
## Но для таких секретов не нужно ESO -> политики доступа для них не нужны

# ---------
# ---INIT
# ---------

# Инициализация Node (Установка компонентов)
## Инициализация Node
## Если вызывать без `--limit` - инициализация производится на всех Node сразу
## Если вызвать с `--limit` - инициализация произойдет только на указанной node
##
- Без лимита: `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/node-install.yaml`
- С лимитом: `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/node-install.yaml --limit k8s-manager-1`

# Инициализация кластера (в первый раз)
## Инициализация кластера
## Именно команда: `kubeadm init ...`
## `--limit XXX` - обязательно надо указывать
##
- `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/cluster-init.yaml --limit k8s-manager-1`

# ---------
# ---JOIN
# ---------

# Присоединение worker-node
## Добавить в `hosts-vars-override/` нового worker
## Если уже был установлен Cilium - смотрим `Подготовка_2`
## Если уже был установлен и настроен Longhorn - смотрим `longhorn/tags-sync`
##
- `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/node-install.yaml --limit k8s-worker-1`
  - Инициализация ноды
- `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/worker-join.yaml --limit k8s-worker-1`
  - Получение токена и вызов команды `kubeadm join ...`

# Присоединение manager-node
## Добавить в `hosts-vars-override/` нового manager
## Если уже был установлен Cilium - смотрим `Подготовка_2`
## Если уже был установлен и настроен Longhorn - смотрим `longhorn/tags-sync`
## Обновить SANS для api-server
## Обновить haproxy-apiserver-lb
##
- `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/apiserver-sans-update.yaml`
  - Это обновит CANS в сертификатах для api-server (добавит туда нового manager-ip)
  - Вызывать нужно БЕЗ `--limit`. Конфиг - нужно обновить на ВСЕХ текущих managers
  - Обновит - только текущие managers
  - Перезапуск api-server - производится последовательно, для каждого managers
- `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/haproxy-apiserver-lb-update.yaml`
  - Обновить конфиг для `haproxy-apiserver-lb` на всех текущих Node (manager + worker)
  - Обновление производится по одному за раз, через playbook.serial: 1
  - То есть: перезапуск производится последовательно, для обеспечения HA доступности
- `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/node-install.yaml --limit k8s-manager-2`
  - Инициализация ноды
  - Указываем `--limit` - так как это добавление конкретной Node
- `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/manager-join.yaml --limit k8s-manager-2`
  - Загрузка сертификатов в k8s.secrets
  - получение токена
  - вызов команды `kubeadm join ...`

# ---------
# ---VAULT + ESO
# ---------
## Есть список компонентов, которые используют VAULT + ESO
## `traefik`, `haproxy`,  `longhorn`, `gitlab`, `argocd`, `argocd-git-ops`
## При удалении компонента через `kubectl delete ...` - удаляются ресурсы k8s
## НО: записи в vault не удаляются. Их нужно удалять руками
## Проблемная ситуация:
## - Установили argocd (через ansible), пароль админа сохранен в vault, удалили argocd из k8s
## - Записи в vault не удалились
## - При повторной установке argocd (через ansible), новый пароль админа не будет положен в vault

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
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/mon-prometheus-operator-install.yaml --tags crds`

## ---
## Cilium. Официальный helm
## ---
## Если изменились конфиги = ConfigMap, Cilium их не подцепит автоматически
## Если посмотреть, что генерируется при `helm template ...` - у Deployment/DaemonSet нет checksum, на основе ConfigMap
## Можно сделать предположение, что для применения новых ConfigMap - надо сделать ручной restart
## Ожидание готовности deployment/daemonset - `kubectl rollout status ...`
## Есть ожидание готовности CRDs. Если добавляются новые CRDs - их ожидание надо добавить в `playbook-app/cilium-install.yaml`
## ---
## Важно! При установке - может зависнуть на пункте: TASK [[cilium-post] Install or upgrade via Helm]
## Если такое произошло: Ctrl+C (Остановить процесс) + запустить заново
## Причина: когда cilium берет под контроль сеть на node - все соединения обрываются (в том числе и SSH)
## ---
## Параметры в `hosts-vars/` + `hosts-vars-override/`
## ---
## `--tags pre, install, post`
## ---
##
- установка
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/cilium-install.yaml`
- обновление (версия, конфиг)
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/cilium-install.yaml`
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/cilium-restart.yaml`

## ---
## metrics-server. Официальный helm
## ---
## Ожидание готовности deployment/daemonset - `kubectl rollout status ...`
## ---
## Параметры в `hosts-vars/` + `hosts-vars-override/`
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
## Параметры в `hosts-vars/` + `hosts-vars-override/`
## ---
## `--tags pre, install, post`
## ---
## 
- установка + обновление (версия, конфиг)
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/cert-manager-install.yaml`

## ---
## ExternalSecret. Официальный helm
## ---
## Ожидание готовности deployment/daemonset - `kubectl rollout status ...`
## Есть ожидание готовности CRDs. Если добавляются новые CRDs - их ожидание надо добавить в `playbook-app/external-secrets-install.yaml`
## ---
## Параметры в `hosts-vars/` + `hosts-vars-override/`
## ---
## `--tags pre, install`
## ---
##
- установка + обновление (версия, конфиг)
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/external-secrets-install.yaml`
- Есть дополнительный playbook, для перезапуска
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/external-secrets-restart.yaml`

## ---
## traefik (ingress-1). Официальный helm
## ---
## Параметры (конфиг) для работы - в cli (как аргументы при запуске)
## Есть dashboard, который доступен по URL -> требуется Certificate (cert-manager-CRD)
## Ожидание готовности deployment/daemonset - `kubectl rollout status ...`
## Есть ожидание готовности CRDs. Если добавляются новые CRDs - их ожидание надо добавить в `playbook-app/traefik-install.yaml`
## Есть работа с `vault + ESO`
## ---
## Параметры в `hosts-vars/` + `hosts-vars-override/`
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
## Параметры в `hosts-vars/` + `hosts-vars-override/`
## ---
## `--tags pre, install, post`
## ---
##
- установка + обновление (версия, конфиг)
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/haproxy-install.yaml`
- Есть дополнительный playbook, для перезапуска
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/haproxy-restart.yaml`

## ---
## cilium-hubble (относится к cilium). yaml -> helm
## ---
## Есть hubble-ui, который доступен по URL -> требуется Certificate (cert-manager-CRD)
## Это просто дополнительная конфигурация
## Тут не запускается никаких контейнеров
## ---
## Параметры в `hosts-vars/` + `hosts-vars-override/`
## ---
## `--tags pre, install`
## ---
##
- установка + обновление (версия, конфиг)
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/cilium-hubble-install.yaml`
  - Ставится: network-policy (для kube-system), ingress (hubble-ui)

## medik8s
## ---
## NOT_READY

## ---
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
## После определния они будут использоваться при установке `longhorn-install.yaml` + `vault-policy-sync.yaml`
## ---
## Важно_2. node-tags - для их автоматической установки на Nodes теперь используется отдельный playbook (аналогично - vault-policy-sync)
## Синхронизация node-tags вызывается отдельно. То есть: после установки longhorn, после добавления node, после изменения node-tags в ansible-hosts
## ---
## Параметры в `hosts-vars/` + `hosts-vars-override/`
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
## Vault. Официальный helm
## ---
## ЕСТЬ проблема: официальный helm не работает из РФ (Региональная блокировка)
## Решение: зайти на github (https://github.com/hashicorp/vault-helm) в раздел с релизами
## Скачать ZIP архив последнего релиза, достать все templates, Chart.yaml и values.yaml
## Есть web-ui, который доступен по URL -> требуется Certificate (cert-manager-CRD)
## Есть volume -> требуется Longhorn
## Ожидание готовности deployment/daemonset - `kubectl wait --for=jsonpath='{.status.phase}'=Running`
## Потому что проверка внутри пода: смотрит на готовность самого VAULT (что он инициализирован), а не просто на работоспособность контейнера
## ---
## Параметры в `hosts-vars/` + `hosts-vars-override/`
## ---
## `--tags pre, install, post`
## ---
## `vault-policy-sync --tags `
## `policy, policy-add, policy-delete`
## `role, role-add, role-delete`
## ---
##
- установка + конфигурация + синхронизация политик. Три отдельных playbook
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/vault-install.yaml`
  - Ставится: vault-0 (StatefulSet)
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/vault-configure.yaml`
  - конфигурация (unseal-keys, сохранить на manager и так далее)
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/vault-policy-sync.yaml`
  - Синхронизация политик
- обновление (версия, конфиг)
  - Устанавливается через официальный HELM, но через исходники с Github
  - Их нужно скачать и нужные положить в директорию с установкой vault (описано выше)
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/vault-install.yaml`
- Есть дополнительный playbook, для перезапуска
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/vault-restart.yaml`

## ---
## Теперь, можно запускать что-то, что требует secrets
## В файле `hosts-vars/` + `hosts-vars-override/` есть отдельная структуры для управления VAULT (какие политики, роли, аккаунты и пути для секретов)
## Пример: `./readme-vault.md`
## Вызов синхронизации VAULT: `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/vault-policy-sync.yaml`
## План, при добавлении чего-то в VAULT
## 1. добавить в `hosts-vars-override/` новые данные (policy + role)
## 2. Вызвать синхронизацию
## 3. Уже отдельно (ArgoCD или как-то иначе) - загрузить в kubernetes: Namespace, ServiceAccount, SecretStore (CRD), ExternalSecret (CRD)
## ВАЖНО: синхронизация полностью синхронизирует структуру в VAULT (добавить, обновить, удалить)
## ---

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
## Параметры в `hosts-vars/` + `hosts-vars-override/`
## ---
## `--tags pre, install, post`
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
## Параметры в `hosts-vars/` + `hosts-vars-override/`
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
## Добавить их публичные ключи в `hosts-vars-override/ (argocd_cm_ssh_known_hosts_extra)`. Это массив из строк
## Без этого, argocd не сможет к ним подключиться (недоверенный host)
## ---
## Параметры в `hosts-vars/` + `hosts-vars-override/`
## ---
## `--tags crds, pre, install, post`
## ---
##
- установка + конфигурация
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/argocd-install.yaml`
  - Ставится: argocd, network-policy, ingress (argocd-ui, h2c-grpc)
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/argocd-configure.yaml`
  - Конфигурация (сбросить права у default-project, достать пароль admin и положить его в Vault)
- обновление (версия)
  - Скачать новый yaml. https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
  - Разнести yaml на несколько файлов
    - `playbook-app/charts/argocd/crds/crds.yaml` - только CRD (там примерно 24к строк)
    - `playbook-app/charts/argocd/pre/templates/configmaps.yaml` - ссюда нужно перенести 7 ConfigMaps + сохранить возможности расширения через HELM
    - `playbook-app/charts/argocd/install/templates/argocd.yaml` - все, кроме CRD и configmaps
  - Есть изменения в дефолтных конфигах. Их надо не затерепть. То есть: после вставки нового `*.yaml` -> надо вернуть обновленные дефолиные конфиги
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
## - `eso_vault_integration_argocd_git_ops_extra`
## - секреты типа: `git_ops_repo_pattern`/`git_ops_repo_direct`/`git_ops_repo_direct_userpass`/`git_ops_repo_pattern_userpass`/`helm_repo`/`helm_repo_oci`
## - установить `argocd-git-ops` + проверить что все ресурсы установились корректно
## - Создать ssh-keys (private + public) + положить их в vault (ESO - создаст из них k8s.secret)
## - Создать репозитории (URL которых указаны в `argocd_git_ops_apps`) + добавить к ним deploy-keys (чтобы argocd - имел к ним доступ)
## ---
## Параметры в `hosts-vars/` + `hosts-vars-override/`
## ---
## `--tags pre, install, post`
## ---
##
- установка + обновление (конфиг)
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/argocd-git-ops-install.yaml`
  - Ставится: ESO + argo-proj, argo-application

## ---
## prometheus-operator + prometheus. yaml -> helm
## ---
## Есть UI, доступен по URL -> требуется Certificate (cert-manager-CRD)
## Есть ожидание готовности CRDs. Если добавляются новые CRDs - их ожидание надо добавить в `playbook-app/mon-prometheus-operator-install.yaml`
## Ожидание готовности deployment/daemonset - `kubectl rollout status ...`
## ---
## Важно_1. Через указание --tags crds = можно установить только CRDs
## ---
## `--tags crds, pre, install, post`
## ---
## 
- установка
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/mon-prometheus-operator-install.yaml`
- обновление (версия)
  - Скачать новый yaml. https://github.com/prometheus-operator/prometheus-operator/releases
  - Разнести yaml на несколько файлов
    - `playbook-app/charts/prometheus-operator/crds/crds.yaml` - сюда все CRD, (примерно 80_000 строк)
    - `playbook-app/charts/prometheus-operator/install/templates/prometheus-operator.yaml` - вся установка (Deplyment, RBAC, Service)
  - Есть изменения в дефолтных конфигах. Их надо не затерепть. То есть: после вставки нового `*.yaml` -> надо вернуть обновленные дефолиные конфиги
  - Версия не указывается в `hosts-vars/` | `hosts-vars-override/` -> так как версия будет в `*.yaml`
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/mon-prometheus-operator-install.yaml`

## ---
## node-exporter. yaml -> helm
## ---
## Ожидание готовности deployment/daemonset - `kubectl rollout status ...`
## ---
## `--tags pre, install`
## ---
##
- установка
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/mon-node-exporter-install.yaml`

## ---
## kube-state-metrics. yaml -> helm
## ---
## Ожидание готовности deployment/daemonset - `kubectl rollout status ...`
## ---
## `--tags pre, install`
## ---
##
- установка
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/mon-kube-state-metrics-install.yaml`

## ---
## grafana. yaml -> helm
## ---
## Есть UI, доступен по URL -> требуется Certificate (cert-manager-CRD)
## Ожидание готовности deployment/daemonset - `kubectl rollout status ...`
## Есть дополнительный файл для `vault + ESO`
## ---
## `--tags pre, install, post`
## ---
##
- установка
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/grafana.yaml`

## ---------------------
## ---------------------
## ---------------------

# ---------
# ЕЩЕ НЕ ГОТОВО
# ---------

## medik8s. Установка идет через kubectl apply -f ...
## ---
## Параметры в `hosts-vars/` + `hosts-vars-override/`
## ---
##
- установка
  - `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/medik8s-install.yaml`

## Zitadel. официальный helm
## ---
## Параметры в `hosts-vars/` + `hosts-vars-override/`
## ---

## ---------
## ---Secrets-Rotation
## ---------

## ---
## Добавить ротацию пароля
## ...
## ...
## ...
## ---


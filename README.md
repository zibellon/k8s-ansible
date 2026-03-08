# Что тут есть
1. Есть playbooks которые запускаются ДО ИНИЦИАЛИЗАЦИИ кластера, а есть  которые СТРОГО_ПОСЛЕ инициализации
   1. Пример до: cluster-init.yaml
   2. Пример после: manager-join.yaml, worker-join.yaml, etcd-key-rotate.yaml


# Как вызывать что-то по частям
--tags pre,install

# Конфигурация файла `hosts.yaml` + `hosts-extra.yaml`
1. Сервера
   1. managers
      1. Добавить все сервера, с которыми будет производится работа
      2. ОБЯЗАТЕЛЬНО! Указать сервер, который является главным manager (master-manager) - `is_master: true`
   2. workers
      1. Добавить все сервера, с которыми будет производится работа

# Подготовка к конфигураци

## Создать файл: `hosts-extra.yaml`
1. указать node-groups: managers, workers
2. Переопределить необходимые параметры (Все параметры есть в `hosts.yaml`)

## Выполнить команду: `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-system/node-info.yaml`
## Покажит основную информацию по всем node

# Подготовка_1
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

# Подготовка_2
## занести внутренний `ip` в `hosts-extra.yaml`
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
      1. Вызвать `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/cilium-install.yaml --tags post`
   9.  Это автоматически добавит в `cilium-host-firewall` новые ip адреса и обновит политику на сервере
   10. После этого делать: `... join ...`

# Важно про `namespace`
## Сменить namespace МОЖНО для любых компонентов
## Сменить namespace НЕЛЬЗЯ для некоторых компонентов. Так указано в официальной документации
- olm
- longhorn-system
- argocd

# Важно про `haproxy-apiserver-lb`
## В конфиге указаны ip адреса всех maanger-node + балансировка между ними
## Запускается как static-pod на каждой node в кластере
## Чтобы спровоцировать перезапуск
- Или обновить спецификацию Pod.yaml. kubelet - автоматически ее подцепит (отслеживает директорию со static-pod) и сделает перезапуск
- Или обновить конфиг, что в свою очередь спровоцирует обновление Pod.yaml
  - В аннотация указано: checksum/config: "{{ haproxy_config_content | hash('sha256') }}"
  - Это функционал ansible
  - Почему нельзя использовать helm ? Потому-что эот Pod запускается как static-pod
  - и отвечает за доступ к kube-api
  - То есть: пока-что он не запустится -> доступа к kube-api НЕТ -> через helm ничего создать нельзя

# Важно по VAULT + ESO
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
- Без лимита: `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-system/node-install.yaml`
- С лимитом: `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-system/node-install.yaml --limit k8s-manager-1`

# Инициализация кластера (в первый раз)
## Инициализация кластера
## Именно команда: `kubeadm init ...`
## `--limit XXX` - обязательно надо указывать
##
- `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-system/cluster-init.yaml --limit k8s-manager-1`

# Присоединение worker-node
## Добавить в `hosts-extra.yaml` нового worker
## Если уже был установлен Cilium - смотрим `Подготовка_2`
##
- `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-system/node-install.yaml --limit k8s-worker-1`
  - Инициализация ноды
- `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-system/worker-join.yaml --limit k8s-worker-1`
  - Получение токена и вызов команды `kubeadm join ...`

# Присоединение manager-node
## Добавить в `hosts-extra.yaml` нового manager
## Если уже был установлен Cilium - смотрим `Подготовка_2`
## Обновить SANS для api-server
## Обновить haproxy-apiserver-lb
##
- `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-system/apiserver-sans-update.yaml`
  - Это обновит CANS в сертификатах для api-server (добавит туда нового manager-ip)
  - Вызывать нужно БЕЗ `--limit`. Конфиг - нужно обновить на ВСЕХ текущих managers
  - Обновит - только текущие managers
  - Перезапуск api-server - производится последовательно, для каждого managers
- `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-system/haproxy-apiserver-lb-update.yaml`
  - Обновить конфиг для `haproxy-apiserver-lb` на всех текущих Node (manager + worker)
  - Обновление производится по одному за раз, через playbook.serial: 1
  - То есть: перезапуск производится последовательно, для обеспечения HA доступности
- `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-system/node-install.yaml --limit k8s-manager-2`
  - Инициализация ноды
  - Указываем `--limit` - так как это добавление конкретной Node
- `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-system/manager-join.yaml --limit k8s-manager-2`
  - Загрузка сертификатов в k8s.secrets
  - получение токена
  - вызов команды `kubeadm join ...`

# ---------
# ---HELPERS
# ---------

# Шифрование ETCD. Ротация ключей
## api-server, на каждой control-plane будет перезапущен 3 раза (так сказано в официальной документации)
## Это не самый быстрый процесс
## Делается через mv: manifests -> tmp, mv: tmp -> manifests (чтобы kubelet убил api-server и снова его восстановил)
## Этот процесс спровоцирует полную остановку api-server
##
- `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-system/etcd-key-rotate.yaml`

# SANS (api-server). Обновление имен (SANS) в сертификатах
## На каждой control-plane будет создан новый api-server.crt
## Каждый текущий api-server - будет перезапущен один раз
## Перезапуск - последовательный (по одному за раз)
##
- `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-system/apiserver-sans-update.yaml`

# Обслуживание сервера (cordon + drain) и возврат в работу
##
- `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-system/node-drain-on.yaml --limit k8s-worker-3`
  - Вывод ноды на обслуживание
- `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-system/node-drain-off.yaml --limit k8s-worker-3`
  - Вернуть ноду в работу

# Удаление node
## Отключение node от кластера
## Перед этим надо выполнить = `Вывод Node на обслуживание`
##
- `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-system/node-remove.yaml --limit k8s-worker-4`

# Очистка сервера, от всех компонентов k8s
1. `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-system/server-clean.yaml --limit k8s-worker-4`
   1. Выполнение команды  `kubeadm reset --force`
   2. Удаление директорий для k8s

# ---------
# ---VAULT + ESO
# ---------
## Есть список компонентов, которые используют VAULT + ESO
## `traefik`, `haproxy`,  `longhorn`, `vault`, `gitlab`, `argocd`, `argocd-git-ops`
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

## Cilium. Официальный helm
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
## Параметры в `hosts.yaml` + `hosts-extra.yaml`
## ---
## `--tags pre, install, post`
## ---
##
- установка
  - `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/cilium-install.yaml`
  - Что ставится: cilium, host-network-policy, kube-system-network-policy
- обновление (версия, конфиг)
  - `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/cilium-install.yaml`
  - `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/cilium-restart.yaml`

## metrics-server. Официальный helm
## Ожидание готовности deployment/daemonset - `kubectl rollout status ...`
## Чтобы работала команда `kubectl top ...`
## ---
## Параметры в `hosts.yaml` + `hosts-extra.yaml`
## ---
## `--tags pre, install`
## ---
##
- установка + обновление (версия, конфиг)
  - `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/metrics-server-install.yaml`

## cert-manager. Официальный helm
## Ожидание готовности deployment/daemonset - `kubectl rollout status ...`
## Есть ожидание готовности CRDs. Если добавляются новые CRDs - их ожидание надо добавить в `playbook-app/cert-manager-install.yaml`
## ---
## Важно_1: Сейчас, через этот ansible - можно настроить только ClusterIssuer (Переменная: cert_manager_cluster_issuers)
## ---
## Параметры в `hosts.yaml` + `hosts-extra.yaml`
## ---
## `--tags pre, install, post`
## ---
## 
- установка + обновление (версия, конфиг)
  - `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/cert-manager-install.yaml`
  - Что ставится: cert-manager, network-policy

## ExternalSecret. Официальный helm
## Ожидание готовности deployment/daemonset - `kubectl rollout status ...`
## Есть ожидание готовности CRDs. Если добавляются новые CRDs - их ожидание надо добавить в `playbook-app/external-secrets-install.yaml`
## ---
## Параметры в `hosts.yaml` + `hosts-extra.yaml`
## ---
## `--tags pre, install`
## ---
##
- установка + обновление (версия, конфиг)
  - `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/external-secrets-install.yaml`
  - Ставится: cert-controller, secrets-webhook, core
- Есть дополнительный playbook, для перезапуска
  - `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/external-secrets-restart.yaml`

## traefik (ingress-1). Официальный helm
## Параметры (конфиг) для работы - в cli (как аргументы при запуске)
## Есть dashboard, который доступен по URL -> требуется Certificate (cert-manager-CRD)
## Ожидание готовности deployment/daemonset - `kubectl rollout status ...`
## Есть ожидание готовности CRDs. Если добавляются новые CRDs - их ожидание надо добавить в `playbook-app/traefik-install.yaml`
## Есть дополнительный файл для `vault + ESO`
## ---
## Параметры в `hosts.yaml` + `hosts-extra.yaml`
## ---
## `--tags pre, install, post`
## ---
##
- установка + обновление (версия, конфиг)
  - `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/traefik-install.yaml`
  - Ставится: traefik, network-policy, ingress (dashboard)
- Есть дополнительный playbook, для перезапуска
  - `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/traefik-restart.yaml`

## haproxy (ingress-2). Официальный helm
## Автоматически подхватывает конфиг, который генерируется через CRD
## Ожидание готовности deployment/daemonset - `kubectl rollout status ...`
## Есть ожидание готовности CRDs. Если добавляются новые CRDs - их ожидание надо добавить в `playbook-app/haproxy-install.yaml`
## Есть дополнительный файл для `vault + ESO`
## ---
## Параметры в `hosts.yaml` + `hosts-extra.yaml`
## ---
## `--tags pre, install, post`
## ---
##
- установка + обновление (версия, конфиг)
  - `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/haproxy-install.yaml`
  - Ставится: haproxy-ingress, network-policy
- Есть дополнительный playbook, для перезапуска
  - `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/haproxy-restart.yaml`

## cilium-hubble (относится к cilium). yaml -> helm
## Есть hubble-ui, который доступен по URL -> требуется Certificate (cert-manager-CRD)
## Это просто дополнительная конфигурация
## Тут не запускается никаких контейнеров
## ---
## Параметры в `hosts.yaml` + `hosts-extra.yaml`
## ---
## `--tags pre, install`
## ---
##
- установка + обновление (версия, конфиг)
  - `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/cilium-hubble-install.yaml`
  - Ставится: network-policy (для kube-system), ingress (hubble-ui)

## olm. yaml -> helm
## Ожидание готовности deployment/daemonset - `kubectl rollout status ...`
## Есть ожидание готовности CRDs. Если добавляются новые CRDs - их ожидание надо добавить в `playbook-app/olm-v0-install.yaml`
## ---
## Параметры в `hosts.yaml` + `hosts-extra.yaml`
## ---
##
- установка
  - `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/olm-v0-install.yaml`
  - Ставится: немного компонентов
- обновление (версия + конфиг)
  - Скачать новый yaml. https://github.com/operator-framework/operator-lifecycle-manager/releases/latest/download/crds.yaml
  - Положить сожержимое в `playbook-app/charts/olm-v0/crds/crds.yaml`
  - Скачать новый yaml. https://github.com/operator-framework/operator-lifecycle-manager/releases/latest/download/olm.yaml
  - Положить сожержимое в `playbook-app/charts/olm-v0/templates/olm-v0-install.yaml`
  - Перенести содержимое namespace в `playbook-app/charts/olm-v0/namespaces.yaml` и удалить из оригинала
  - `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/olm-v0-install.yaml`

## medik8s. Установка идет через kubectl apply -f ...
## ---
## Параметры в `hosts.yaml` + `hosts-extra.yaml`
## ---
##
- установка
  - `ansible-playbook -i hosts.yaml playbook-app/medik8s-install.yaml`

## longhorn. Официальный helm
## Есть UI, который доступен по URL -> требуется Certificate (cert-manager-CRD)
## Автоматически подхватывает конфиг. Обновили конфиг в `ConfigMap` -> сразу подхватил и начал использовать
## `namespace: longhorn-system`, МЕНЯТЬ НЕЛЬЗЯ. Так написано в документации
## Пример обновленного конфига - `docs/longhorn/other/...`
## Ожидание готовности deployment/daemonset - `kubectl rollout status ...`
## Есть ожидание готовности CRDs. Если добавляются новые CRDs - их ожидание надо добавить в `playbook-app/longhorn-install.yaml`
## Есть создание секретов для БЭКАПА в S3 -> использует CRD от ESO (Но секреты сразу работать не будут, так как они появляются в VAULT, позже)
## Есть дополнительный файл для `vault + ESO`
## ---
## Важно_1. Для создания секретов для работы с backup - их нужно определить `hosts-extra.yaml` (пример в `hosts-extra.yaml.example`)
## После определния они будут использоваться при установке `longhorn-install.yaml` + `vault-install.yaml` + `vault-policy-sync.yaml`
## ---
## Важно_2. Восстановдение VAULT
## Ситуация: был кластер с vault + longhorn, есть backup, нужно восстановиться из бэкапа
## Бэкап лежит на S3 (все доступы есть), но правило такое: все секреты в кластере только через vault + ESO. Что делать ?
## Чтобы восстановить vault - нужно скачать бэкап через longhorn, чтобы скачать backup - нужно создать k8s: Secret (s3-creds)
## План действий
## - в `hosts-extra.yaml` определить все секреты для восстановления бэкапов (их может быть несколько). переменная: `longhorn_s3_restore_secrets`
## - запуск `ansible-playbook -i hosts.yaml -i hosts-extra.yaml longhorn-s3-restore-create.yaml`. Это создаст секреты в k8s
## - Чтобы восстановить PV + PVC = нужен namespace. Namespace - для VAULT
## - Запуск Vault, только одну стадию: `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/vault-install.yaml --tags pre`
## - Зайти в longhorn-ui, использовать секреты для скачивания и восстановления backups, восстановить volume для VAULT
## - запуск `ansible-playbook -i hosts.yaml -i hosts-extra.yaml longhorn-s3-restore-delete.yaml`. Это удалит секреты из k8s
## - запустить vault с восстановленным состоянием
## - сразу же подрубится ESO (из пункта 1 - Важно_1)
## ---
## Параметры в `hosts.yaml` + `hosts-extra.yaml`
## ---
## `--tags pre, install, post`
## ---
## 
- установка + обновление (версия, конфиг)
  - `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/longhorn-install.yaml`
  - Ставится: longhorn, network-policy, ingress (longhorn-ui)

## ---
## Теперь, можно запускать что-то, что требует volume (PVC)
## ---

## Vault. Официальный helm
## ЕСТЬ проблема: официальный helm не работает из РФ (Региональная блокировка)
## Решение: зайти на github (https://github.com/hashicorp/vault-helm) в раздел с релизами
## Скачать ZIP архив последнего релиза, достать все templates, Chart.yaml и values.yaml
## Есть web-ui, который доступен по URL -> требуется Certificate (cert-manager-CRD)
## Есть volume -> требуется Longhorn
## Ожидание готовности deployment/daemonset - `kubectl wait --for=jsonpath='{.status.phase}'=Running`
## Потому что проверка внутри пода: смотрит на готовность самого VAULT (что он инициализирован), а не просто на работоспособность контейнера
## Есть дополнительный файл для `vault + ESO`
## ---
## Параметры в `hosts.yaml` + `hosts-extra.yaml`
## ---
## `--tags pre, install, post`
## ---
##
- установка + конфигурация + синхронизация политик. Два отдельных playbook
  - `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/vault-install.yaml`
  - Ставится: vault-0 (StatefulSet)
  - `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/vault-configure.yaml`
  - конфигурация (unseal-keys, сохранить на manager и так далее)
  - `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/vault-policy-sync.yaml`
  - Синхронизация политик
- обновление (версия, конфиг)
  - Устанавливается через официальный HELM, но через исходники с Github
  - Их нужно скачать и нужные положить в директорию с установкой vault (описано выше)
  - `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/vault-install.yaml`
- Есть дополнительный playbook, для перезапуска
  - `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/vault-restart.yaml`

## ---
## Теперь, можно запускать что-то, что требует secrets
## В файле `hosts.yaml` + `hosts-extra.yaml` есть отдельная структура для управления VAULT (какие политики, роли, аккаунты и пути для секретов)
## Вызов синхронизации VAULT: `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/vault-policy-sync.yaml`
## План, при добавлении чего-то в VAULT
## 1. добавить в `hosts-extra.yaml` новые данные (policy + role + sa + namespoace + secret_path)
## 2. Вызвать синхронизацию
## 3. Уже отдельно (ArgoCD или как-то иначе) - загрузить в kubernetes: Namespace, ServiceAccount, SecretStore (CRD), ExternalSecret (CRD)
## ВАЖНО: синхронизация полностью синхронизирует структуру в VAULT (добавить, обновить, удалить)
## ---

## gitlab. Официальный helm
## Есть UI + API, доступны по URL -> требуется Certificate (cert-manager-CRD)
## Есть UI, доступен по URL -> требуется Certificate (cert-manager-CRD)
## Ожидание готовности deployment/daemonset - `kubectl rollout status ...`
## Есть дополнительный файл для `vault + ESO`
## ---
## Важно про компоненты
## - gitlab-exporter - hardcoded 1 в шаблоне (в самом официальном helm)
## - gitaly - StatefulSet, количество реплик определяется через global.gitaly.internal.names. По дефолту = 1. RollingUpdate + StatefulSet = убить, а потом создать
##   - Но если их больше чем 1 - там какая-то возня начинается с Praefect (репликация git-данных между узлами)
## ---
## Параметры в `hosts.yaml` + `hosts-extra.yaml`
## ---
## `--tags pre, install, post`
## ---
## 
- установка + обновление (версия + конфиг)
  - `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/gitlab-install.yaml`
  - Ставится: gitlab-minio, ingress (minio-api, minio-console-ui)
  - Ставится: gitlab, ingress (UI, git, pages, registry, ssh-tcp)
  - `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/gitlab-configure.yaml`
  - конфигурация. Отдельный playbook

## argocd. yaml -> helm
## Есть UI, доступен по URL -> требуется Certificate (cert-manager-CRD)
## Нет автоматической обработки новых конфигов (Как у Cilium). То есть: После обновления конфигов - ручной restart
## Есть ожидание готовности CRDs. Если добавляются новые CRDs - их ожидание надо добавить в `playbook-app/argocd-install.yaml`
## Ожидание готовности deployment/daemonset - `kubectl rollout status ...`
## Есть дополнительный файл для `vault + ESO`
## ---
## Важно_1: нужно выполнить команду `ssh-keyscan` на те git-репозитории, которые планируется использовать для argocd
## Добавить их публичные ключи в `hosts-extra.yaml (argocd_cm_ssh_known_hosts_extra)`. Это массив из строк
## Без этого, argocd не сможет к ним подключиться (недоверенный host)
## ---
## Параметры в `hosts.yaml` + `hosts-extra.yaml`
## ---
## `--tags pre, install, post`
## ---
##
- установка + конфигурация
  - `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/argocd-install.yaml`
  - Ставится: argocd, network-policy, ingress (argocd-ui, h2c-grpc)
  - `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/argocd-configure.yaml`
  - Конфигурация
- обновление (версия)
  - Скачать новый yaml. https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
  - Разнести yaml на несколько файлов
    - `playbook-app/charts/argocd/pre/templates/configmaps.yaml` - ссюда нужно перенести 7 ConfigMaps + сохранить возможности расширения через HELM
    - `playbook-app/charts/argocd/install/crds/crds.yaml` - только CRD (там примерно 24к строк)
    - `playbook-app/charts/argocd/install/templates/argocd.yaml` - все, кроме CRD и configmaps
  - Есть изменения в дефолтных конфигах. Их надо не затерепть. То есть: после вставки нового `*.yaml` -> надо вернуть обновленные дефолиные конфиги
  - Версия не указывается в `hosts.yaml` | `hosts-extra.yaml` -> так как версия будет в `*.yaml`
  - Пример обновленного конфига - `docs/arocd/...`
  - `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/argocd-install.yaml`
  - `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/argocd-restart.yaml`
- обновление (конфиг)
  - `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/argocd-install.yaml`
  - `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/argocd-restart.yaml`

## argocd-git-ops. yaml -> helm
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
## - настроить необходимые конфиги для argo-cd-git-ops (`hosts-extra.yaml`)
## - `argocd_git_ops_apps` (какие проекты и приложения нужно создать)
## - `eso_vault_integration_argocd_git_ops_extra`
## - секреты типа: `git_ops_repo_pattern`/`git_ops_repo_direct`/`git_ops_repo_direct_userpass`/`git_ops_repo_pattern_userpass`/`helm_repo`/`helm_repo_oci`
## - установить `argocd-git-ops` + проверить что все ресурсы установились корректно
## - Создать ssh-keys (private + public) + положить их в vault (ESO - создаст из них k8s.secret)
## - Создать репозитории (URL которых указаны в `argocd_git_ops_apps`) + добавить к ним deploy-keys (чтобы argocd - имел к ним доступ)
## ---
## Параметры в `hosts.yaml` + `hosts-extra.yaml`
## ---
## `--tags pre, install`
## ---
##
- установка + обновление (конфиг)
  - `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/argocd-git-ops-install.yaml`
  - Ставится: ESO + argo-proj, argo-application

## ---------
## ---------
## ---------

## ---------
## ---VAULT, логика синхронизации политик (`vault-policy-sync.yaml`)
## ---------
##
## - Vault ready — проверяет, что Vault не sealed (`vault status`)
## - Merge + validate (`tasks-eso-merge.yaml`). Подход: сначала мержим, потом валидируем:
##   - Валидация `type` у секретов из XXX_extra (до merge)
##   - Merge per-component: base + extra = `_merged` (vault/traefik/haproxy/longhorn/gitlab/argocd/argocd-git-ops)
##   - Проверка уникальности `external_secret_name` и `target_secret_name` в каждом `_merged`
##     - Ловит дубли внутри base, внутри extra и пересечения base/extra — всё одной проверкой
##   - Cross-component: argocd vs argocd_git_ops (единственный случай — один namespace `argocd`)
##     - fail если `external_secret_name` или `target_secret_name` пересекаются между ними
##   - Из всех `_merged` генерирует `_derived_policies` и `_derived_roles`
##   - Финальный merge: `vault_policies_final` = derived + manual + extra
##   - Финальная проверка уникальности `vault_policies_final` / `vault_roles_final` — fail если дубль
## - Удаляет из Vault политики, которых нет в финальном списке (кроме default/root)
## - Добавляет/Обновляет политики. `vault policy write ...`
## - Удаляет из Vault роли, которых нет в финальном списке
## - Добавляет/Обновляет роли. `vault write auth/kubernetes/role/...` (SA + namespace + policies)
## ---

## ---------
## ---ESO, насильная синхронизация ExternalSecrets
## ---------
# Все namespaces
`ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/eso-force-sync.yaml`

# Только определенный namespace. Например: gitlab
# Доступные namespace: все, для которых есть eso_vault_integration_XXX (Пример - hosts.yaml)
`ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/eso-force-sync.yaml --tags gitlab`

## ---------
## ---ArgoCD - git-ops, какие секреты должны быть в VAULT
## ---------
Vault пустой — закладываем правильные имена сразу, без миграции. Ключи совпадают с именами полей ArgoCD Secret напрямую:

1. git SSH (argocd_secret_type: repo-creds или repository)
   1. type: "git"
   2. url: "ssh://..." или "https://..."
   3. sshPrivateKey: "-----BEGIN..."
2. git userpass (argocd_secret_type: repo-creds или repository)
   1. type: "git"
   2. url: "https://..."
   3. username: "..."
   4. password: "..."
3. helm repo (argocd_secret_type: repository)
   1. type: "helm"
   2. name: "traefik"
   3. url: "https://traefik.github.io/charts"
   4. username: "..." (не добавлять если публичный)
   5. password: "..." (не добавлять если публичный)
4. helm OCI (argocd_secret_type: repository)
   1. те же поля что helm repo + enableOCI: "true"

Для helm_repo_oci просто добавить в Vault поле enableOCI: "true" — тогда оно появится в Secret, ArgoCD включит OCI. Для helm_repo — не добавлять, ArgoCD будет считать enableOCI = false.

## ---------
## ---Argocd, добавление нового приложения
## ---------
1. Вводные данные
   1. Весь кластер настроен, все работает исправно
2. Что нужно
   1. Новый проект: my-casino-app
   2. Компонены: Postygres, redis, Nats, back, front, cron
   3. Одно окружение: prod
3. Последовательность действий
   1. Через паттерн app-of-apps - создать AppProject + Application
   2. В git, создать новую директорию, где будут лежать все манифесты для этого проекта и контура (infra/.../my-casino-app/prod)
   3. Создать Chart.yaml + values.yaml + templates/namespace.yaml
   4. Запустить + дождаться синхронизации
   5. В файле `hosts-extra.yaml` - добавить необходимые политики для vault + ESO
      1. 1 role
      2. 4 политкии = postgres, redis, nats, common
   6. Синхронизировать vault-policy-sync
   7. Проверить, что в VAULT все политики и все роли создались успешно
   8. Сгенерировать все необходимые секреты (login, pass, url и так далее) и положить их в VAULT по правильным путям
      1. Правильные пути - те, которые были указаны в policy + role
   9.  Вернуться в git-ops репозиторий
   10. Создать SA + SecretStore + 4 ExternalSecret (postgres, redis, nats, common)
   11. Залить эти изменения и дождаться синхронизации
   12. Проверить, что все SecretStore + ExternalSecret + k8s.Secret = успешно созданы и готовы
   13. Запустить Postgres, redis, nats
       1.  Все ENV креды - берутся из секретов, которые были созданы через ESO
   14. Запустить back + front + cron
       1.  Все ENV креды - берутся из секретов, которые были созданы через ESO
   15. Готово
4.  Ротация creds (РУЧНОЙ РЕЖИМ)
    1.  поменять что-то в vault
    2.  Если надо, поменять в mount-volue (напрмиер - postgres, нужно выполнить команду внутри контейннера)
    3.  выполнить через ArgoCD-ui = sync + force + replace
    4.  Готово

## ---------
## ---Secrets-Rotation
## ---------

## ---
## Добавить ротацию пароля
## ...
## ...
## ...
## ---
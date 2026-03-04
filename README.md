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
## `traefik`, `haproxy`,  `longhorn`, `vault`, `gitlab`, `argocd`
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
- установка
  - `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/vault-install.yaml`
  - Ставится: cert-controller, secrets-webhook, core
- обновление (версия, конфиг)
  - Устанавливается через официальный HELM, но через исходники с Github
  - Их нужно скачать и нужные положить в директорию с установкой vault (описано выше)
  - `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/vault-install.yaml`
- Есть дополнительный playbook, для перезапуска
  - `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/vault-restart.yaml`
- Есть дополнительный playbook, для синхронизации политик и ролей. Внутренняя структу VAULT
  - `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/vault-policy-sync.yaml`

## ---
## Теперь, можно запускать что-то, что требует secrets
## В файле `hosts.yaml` + `hosts-extra.yaml` есть отдельная структура для управления VAULT (какие политики, роли, аккаунты и пути для секретов)
## Вызов синхронизации VAULT: `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/vault-policy-sync.yaml`
## План, при добавлении чего-то в VAULT
## 1. добавить в `hosts-extra.yaml` новые данные (policy + role + sa + namespoace + secret_path)
## 2. Вызвать синхронизацию
## 3. Уже отдельно (ArgoCD или как-то иначе) - загрузить в kubernetes: namespace, ServiceAccount, SecretStore (CRD), ExternalSecret (CRD)
## ВАЖНО: синхронизация полностью синхронизирует структуру в VAULT (добавить, обновить, удалить)
## ---

## gitlab. Официальный helm
## Есть UI + API, доступны по URL -> требуется Certificate (cert-manager-CRD)
## Есть UI, доступен по URL -> требуется Certificate (cert-manager-CRD)
## Ожидание готовности deployment/daemonset - `kubectl rollout status ...`
## Есть дополнительный файл для `vault + ESO`
## ---
## Важно про config
## 1. Для deployment используется `checksum/config: ...`
## 2. При обновлении конфига `/gitlab/templates/configmap.yaml` POD c GitLab будет перезапущен
## 3. checksum/config - вычисляется через HELM (include ...)
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

## argocd. yaml -> helm
## Есть UI, доступен по URL -> требуется Certificate (cert-manager-CRD)
## Нет автоматической обработки новых конфигов (Как у Cilium). То есть: После обновления конфигов - ручной restart
## Есть ожидание готовности CRDs. Если добавляются новые CRDs - их ожидание надо добавить в `playbook-app/argocd-install.yaml`
## Ожидание готовности deployment/daemonset - `kubectl rollout status ...`
## Есть дополнительный файл для `vault + ESO`
## ---
## Параметры в `hosts.yaml` + `hosts-extra.yaml`
## ---
##
- установка
  - `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/argocd-install.yaml`
  - Ставится: argocd, network-policy, ingress (argocd-ui, h2c-grpc)
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
## Установка всех необходимых ресурсов k8s - для git-ops паттерна. Создание необходимых групп и репозиториев в gitlab + создание ключей в VAULT
## Тут нет запуска каких-либо компонентов (Deployment, CronJob и так далее)
## Это именно создание ресурсов k8s для работы git-ops
## ---
## Параметры в `hosts.yaml` + `hosts-extra.yaml`
## ---
##
- установка + обновление (конфиг)
  - `ansible-playbook -i hosts.yaml -i hosts-extra.yaml playbook-app/argocd-git-ops-install.yaml`
  - Ставится: argo-proj, argo-application

## ---------
## ---Secrets-Rotation
## ---------

## ---
## Добавить ротацию пароля
## ...
## ...
## ...
## ---
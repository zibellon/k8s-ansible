# Подготовка к конфигураци
## Выполнить команду: `ansible-playbook -i hosts.yaml node-info.yaml`

# Подготовка_1
## Узнать, какой ip адрес принадлежит основному интерфейсу (ens_xxx)
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
## занести внутренний `ip` в `hosts.yaml`
## его надо будет разрешить в cilium-host-firewall, чтобы можно было делать join
## есть два варианта
1. сначала ВСЕ join -> потом install cilium
   1. При таком сценарии проблем нет
   2. cluster-init
   3. worker/manager join
   4. install cilium + cilium-host-firewall
   5. Все node уже внутри кластера и Cilium про них знает
2. install cilium -> потом join
   1. install cilium + cilium-host-firewall
   2. Входящий трафик разрешается только от известных источников (внутри кластера, cilium.entities)
   3. Пока Node не добавлена в кластер = cilium видит ее как world (внешний мир)
   4. Трафик запрещен на уровне cilium-host-firewall
   5. при выполнении команды JOIN - timeout. Так как Node не может подключиться к кластеру
   6. Как решать
   7. Добавить новый сервер в hosts.yaml
   8. Обновить cilium-host-firewall. Вызвать `hosts.yaml playbooks/apps/cilium-install.yaml`
   9. Это автоматически добавит в cilium-host-firewall новые ip адреса и обновит политику на сервере
   10. После этого сделать: `... join ...`

# Конфигурация файла hosts.yaml
1. Сервера
   1. managers
      1. Добавить все сервера, с которыми будет производится работа
      2. ОБЯЗАТЕЛЬНО! Указать сервер, который является главным manager (master-manager) - `is_master: true`
   2. workers
      1. Добавить все сервера, с которыми будет производится работа

# Важно про `namespace`
## Сменить namespace МОЖНО для любых компонентов
## Сменить namespace НЕЛЬЗЯ для. Так указано в официальной документации
- olm
- longhorn-system
- argocd

# Первичная инициализация кластера
1. `ansible-playbook -i hosts.yaml node-install.yaml --limit k8s-manager-1`
   1. Инициализация ноды
2. `ansible-playbook -i hosts.yaml playbooks/init-cluster.yaml --limit k8s-manager-1`
   1. Инициализация кластера. Именно команда: `kubeadm init ...`

# Присоединение worker-node
1. Добавить в hosts.yaml нового worker
2. `ansible-playbook -i hosts.yaml node-install.yaml --limit k8s-worker-1`
   1. Инициализация ноды
3. `ansible-playbook -i hosts.yaml playbooks/worker-join.yaml --limit k8s-worker-1`
   1. Получение токена и вызов команды `kubeadm join ...`

# Присоединение manager-node
1. Добавить в hosts.yaml нового manager
2. `ansible-playbook -i hosts.yaml playbooks/haproxy-apiserver-lb.yaml --limit k8s-manager-1`
   1. Обновить конфиг для `haproxy-apiserver-lb` на всех текущих Node (mamnegr + worker)
   2. По одному за раз
   3. Через `--limit ....` указать название Node
3. `ansible-playbook -i hosts.yaml node-install.yaml --limit k8s-manager-2`
   1. Инициализация ноды
4. `ansible-playbook -i hosts.yaml playbooks/manager-join.yaml --limit k8s-manager-2`
   1. Загрузка сертификатов в k8s.secrets, получение токена и вызов команды `kubeadm join ...`

# Обслуживание сервера (cordon + drain) и возврат в работу
1. `ansible-playbook -i hosts.yaml playbooks/node-drain-on.yaml --limit k8s-worker-3`
   1. Вывод ноды на обслуживание
2. `ansible-playbook -i hosts.yaml playbooks/node-drain-off.yaml --limit k8s-worker-3`
   1. Вернуть ноду в работу

# Удаление node
1. `ansible-playbook -i hosts.yaml playbooks/node-remove.yaml --limit k8s-worker-4`
   1. Отключение node от кластера
   2. Перед этим - надо выполнить `ansible-playbook -i hosts.yaml playbooks/node-drain-on.yaml --limit k8s-worker-3`

# Очистка сервера, от всех компонентов k8s
1. `ansible-playbook -i hosts.yaml playbooks/server-clean.yaml --limit k8s-worker-4`
   1. Выполнение команды  `kubeadm reset --force`
   2. Удаление директорий для k8s

# haproxy-apiserver-lb
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

# Компоненты === Приложения

## Cilium. Официальный helm
## Если изменились конфиги = ConfigMap, Cilium их не подцепит автоматически
## Если посмотреть, что генерируется при `helm template ...` - у Deployment/DaemonSet нет checksum, на основе ConfigMap
## Можно сделать предположение, что для применения новых ConfigMap - надо сделать ручной restart
## Ожидание готовности deployment/daemonset - `kubectl rollout status ...`
## Есть ожидание готовности CRDs. Если добавляются новые CRDs - их ожидание надо добавить в `playbooks/apps/cilium-install.yaml`
##
- установка
  - Только параметры в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/cilium-install.yaml --limit k8s-manager-1`
  - Что ставится: cilium, host-network-policy, kube-system-network-policy
- обновление (версия, конфиг)
  - Только параметры в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/cilium-install.yaml --limit k8s-manager-1`
  - `ansible-playbook -i hosts.yaml playbooks/apps/cilium-restart.yaml --limit k8s-manager-1`

## cert-manager. Официальный helm
## Ожидание готовности deployment/daemonset - `kubectl rollout status ...`
## Есть ожидание готовности CRDs. Если добавляются новые CRDs - их ожидание надо добавить в `playbooks/apps/cert-manager-install.yaml`
## 
- установка
  - Только параметры в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/cert-manager-install.yaml --limit k8s-manager-1`
  - Что ставится: cert-manager, network-policy
- обновление (версия, конфиг)
  - Только параметры в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/cert-manager-install.yaml --limit k8s-manager-1`

## ExternalSecret. Официальный helm
## Ожидание готовности deployment/daemonset - `kubectl rollout status ...`
## Есть ожидание готовности CRDs. Если добавляются новые CRDs - их ожидание надо добавить в `playbooks/apps/external-secrets-install.yaml`
##
- установка
  - Параметры в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/external-secrets-install.yaml --limit k8s-manager-1`
  - Ставится: cert-controller, secrets-webhook, core
- обновление (версия, конфиг)
  - Параметры в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/external-secrets-install.yaml --limit k8s-manager-1`
- Есть дополнительный playbook, для перезапуска
  - `ansible-playbook -i hosts.yaml playbooks/apps/external-secrets-restart.yaml --limit k8s-manager-1`

## traefik (ingress). yaml -> helm
## Параметры (конфиг) для работы - в cli (как аргументы при запуске)
## Есть dashboard, который доступен по URL -> требуется Certificate (cert-manager-CRD)
## Ожидание готовности deployment/daemonset - `kubectl rollout status ...`
## Есть ожидание готовности CRDs. Если добавляются новые CRDs - их ожидание надо добавить в `playbooks/apps/traefik-install.yaml`
##
- установка
  - Параметры в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/traefik-install.yaml --limit k8s-manager-1`
  - Ставится: traefik, network-policy, ingress (dashboard)
- обновление (версия)
  - Руками обновить CRDs. https://raw.githubusercontent.com/traefik/traefik/v3.6/docs/content/reference/dynamic-configuration/kubernetes-crd-definition-v1.yml
  - Руками обновить RBAC. https://raw.githubusercontent.com/traefik/traefik/v3.6/docs/content/reference/dynamic-configuration/kubernetes-crd-rbac.yml
  - Параметры в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/traefik-install.yaml --limit k8s-manager-1`
- обновление (конфиг)
  - Параметры в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/traefik-install.yaml --limit k8s-manager-1`
- Есть дополнительный playbook, для перезапуска
  - `ansible-playbook -i hosts.yaml playbooks/apps/traefik-restart.yaml`

## haproxy (ingress-2). Официальный helm
## Автоматически подхватывает конфиг, который генерируется через CRD
## Ожидание готовности deployment/daemonset - `kubectl rollout status ...`
## Есть ожидание готовности CRDs. Если добавляются новые CRDs - их ожидание надо добавить в `playbooks/apps/haproxy-install.yaml`
##
- установка
  - Только параметры в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/haproxy-install.yaml --limit k8s-manager-1`
  - Ставится: haproxy-ingress, network-policy
- обновление (версия)
  - Только параметры в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/haproxy-install.yaml --limit k8s-manager-1`
- обновление (конфиг)
  - Только параметры в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/haproxy-install.yaml --limit k8s-manager-1`
  - `ansible-playbook -i hosts.yaml playbooks/apps/haproxy-restart.yaml --limit k8s-manager-1`
- Дополнительно
  - Есть команда для перезапуска DaemonSet
  - `ansible-playbook -i hosts.yaml playbooks/apps/haproxy-restart.yaml`

## cilium-post (относится к cilium). yaml -> helm
## Есть hubble-ui, который доступен по URL -> требуется Certificate (cert-manager-CRD)
## Это просто дополнительная конфигурация
## Тут не запускается никаких контейнеров
##
- установка
  - Только параметры в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/cilium-hubble-install.yaml --limit k8s-manager-1`
  - Ставится: network-policy (для kube-system), ingress (hubble-ui)
- обновление (Версия + конфиг)
  - Только параметры в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/cilium-hubble-install.yaml --limit k8s-manager-1`

## olm. yaml -> helm
## Ожидание готовности deployment/daemonset - `kubectl rollout status ...`
## Есть ожидание готовности CRDs. Если добавляются новые CRDs - их ожидание надо добавить в `playbooks/apps/olm-v0-install.yaml`
##
- установка
  - Никаких переменных в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/olm-v0-install.yaml --limit k8s-manager-1`
  - Ставится: немного компонентов
- обновление (версия + конфиг)
  - Никаких переменных в `hosts.yaml`
  - Скачать новый yaml. https://github.com/operator-framework/operator-lifecycle-manager/releases/latest/download/crds.yaml
  - Положить сожержимое в `playbooks/apps/charts/olm-v0/crds/crds.yaml`
  - Скачать новый yaml. https://github.com/operator-framework/operator-lifecycle-manager/releases/latest/download/olm.yaml
  - Положить сожержимое в `playbooks/apps/charts/olm-v0/templates/olm-v0-install.yaml`
  - Перенести содержимое namespace в `playbooks/apps/charts/olm-v0/namespaces.yaml` и удалить из оригинала
  - `ansible-playbook -i hosts.yaml playbooks/apps/olm-v0-install.yaml --limit k8s-manager-1`

## medik8s. Установка идет через kubectl apply -f ...
##
- установка
  - Много переменных в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/medik8s-install.yaml --limit k8s-manager-1`

## longhorn. Официальный helm
## Есть UI, который доступен по URL -> требуется Certificate (cert-manager-CRD)
## Автоматически подхватывает конфиг. Обновили конфиг в ConfigMap -> сразу подхватил и начал использовать
## `namespace: longhorn-system`, МЕНЯТЬ НЕЛЬЗЯ. Так написано в документации
## Пример обновленного конфига - `docs/longhorn/other/...`
## Есть ожидание готовности CRDs. Если добавляются новые CRDs - их ожидание надо добавить в `playbooks/apps/longhorn-install.yaml`
## Есть создание секретов для БЭКАПА в S3 -> использует CRD от ESO (Но секреты сразу работать не будут, так как они появляются в VAULT, позже)
## 
- установка
  - Параметры в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/longhorn-install.yaml --limit k8s-manager-1`
  - Ставится: longhorn, network-policy, ingress (longhorn-ui)
- обновление (версия)
  - Параметры в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/longhorn-install.yaml --limit k8s-manager-1`
- обновление (конфиг)
  - Параметры в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/longhorn-install.yaml --limit k8s-manager-1`

## ---
## Теперь, можно запускать что-то, что требует volume (PVC)
## ---

## Vault. Официальный helm. ЕСТЬ проблема: официальный helm не работает из РФ
## Решение: зайти на github (https://github.com/hashicorp/vault-helm) в раздел с релизами
## Скачать ZIP архив последнего релиза, достать все templates, Chart.yaml и values.yaml
## Есть web-ui, который доступен по URL -> требуется Certificate (cert-manager-CRD)
## Есть точка монтирования -> требуется Longhorn
##
- установка
  - Параметры в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/vault-install.yaml --limit k8s-manager-1`
  - Ставится: cert-controller, secrets-webhook, core
- обновление (версия, конфиг)
  - Параметры в `hosts.yaml`
  - Устанавливается через официальный HELM, но через исходники с Github
  - `ansible-playbook -i hosts.yaml playbooks/apps/vault-install.yaml --limit k8s-manager-1`
- Перезапуск
  - Есть дополнительный playbook, для перезапуска
  - `ansible-playbook -i hosts.yaml playbooks/apps/vault-restart.yaml --limit k8s-manager-1`
- Важный момент: в hosts.yaml есть полная структура ВСЕХ policy + role + sa + secret_path
  - `ansible-playbook -i hosts.yaml playbooks/apps/tasks/tasks-vault-sync.yaml --limit k8s-manager-1`

## ---
## Теперь, можно запускать что-то, что требует secrets
## В файле `hosts.yaml` есть отдельная структура для управления VAULT (какие политики, роли, аккаунты и пути для секретов)
## Вызов синхронизации VAULT, на основе файла: `ansible-playbook -i hosts.yaml playbooks/apps/vault-sync.yaml --limit k8s-manager-1`
## План, при добавлении чего-то в VAULT
## 1. добавить в hosts.yaml новые данные;
## 2. Вызвать синхронизацию;
## 3. Уже отдельно (ArgoCD или как-то иначе) - загрузить в kubernetes: namespace, ServiceAccount, SecretStore (CRD), ExternalSecret (CRD)
## ВАЖНО: синхронизация только добавляет и обновляет структуру в VAULT. Удалять что-то - нужно руками
## ---

## gitlab. yaml -> helm
## Есть UI + API, доступны по URL -> требуется Certificate (cert-manager-CRD)
## Есть UI, доступен по URL -> требуется Certificate (cert-manager-CRD)
## Важный момент про config
1. Для deployment используется `checksum/config: ...`
2. При обновлении конфига `/gitlab/templates/configmap.yaml` POD c GitLab будет перезапущен
3. checksum/config - вычисляется через HELM (include ...)
## 
- установка
  - Только параметры в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/gitlab-install.yaml --limit k8s-manager-1`
  - Ставится: gitlab-minio, ingress (minio-api, minio-console-ui)
  - Ставится: gitlab, ingress (UI, git, pages, registry, ssh-tcp)
- обновление (версия + конфиг)
  - Только параметры в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/gitlab-minio-install.yaml --limit k8s-manager-1`

## argocd. yaml -> helm
## Есть UI, доступен по URL -> требуется Certificate (cert-manager-CRD)
## Нет автоматической обработки новых конфигов (Как у Cilium). То есть: После обновления конфигов - ручной restart
## Есть ожидание готовности CRDs. Если добавляются новые CRDs - их ожидание надо добавить в `playbooks/apps/argocd-install.yaml`
##
- установка
  - Только параметры в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/argocd-install.yaml --limit k8s-manager-1`
  - Ставится: argocd, network-policy, ingress (argocd-ui, h2c-grpc), git-ops
- обновление (версия)
  - Только параметры в `hosts.yaml`
  - Скачать новый yaml. https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
  - Разнести yaml на два файла
    - `playbooks/apps/charts/argocd/templates/argocd.yaml` - все, кроме CRD
    - `playbooks/apps/charts/argocd/crds/crds.yaml` - только CRD (там примерно 24к строк)
  - Есть изменения в дефолтных конфигах. Их надо не затерепть. То есть: после вставки нового `*.yaml` -> надо вернуть обновленные дефолиные конфиги
  - Версия не указывается в `hosts.yaml` -> так как версия будет в `*.yaml`
  - Пример обновленного конфига - `docs/arocd/...`
  - `ansible-playbook -i hosts.yaml playbooks/apps/argocd-install.yaml --limit k8s-manager-1`
  - `ansible-playbook -i hosts.yaml playbooks/apps/argocd-restart.yaml --limit k8s-manager-1`
- обновление (конфиг)
  - Только параметры в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/argocd-install.yaml --limit k8s-manager-1`
  - `ansible-playbook -i hosts.yaml playbooks/apps/argocd-restart.yaml --limit k8s-manager-1`

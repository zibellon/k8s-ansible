# Конфигурация файла hosts.yaml
1. Сервера
   1. managers
      1. Добавить все сервера, с которыми будет производится работа
      2. ОБЯЗАТЕЛЬНО! Указать сервер, который является главным manager (master-manager) - `is_master: true`
   2. workers
      1. Добавить все сервера, с которыми будет производится работа

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
## Если посмотреть в то, что генерируется при `helm template` - то у Deployment/DaemonSet - нет checksum, на основе ConfigMap
## Можно сделать предположение, что для применения новых ConfigMap - надо сделать ручной restart
##
- установка
  - Только параметры в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/cilium-install.yaml --limit k8s-manager-1`
  - Что ставится: cilium, host-network-policy, kube-system-network-policy
- обновление (версия)
  - Только параметры в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/cilium-install.yaml --limit k8s-manager-1`
  - `ansible-playbook -i hosts.yaml playbooks/apps/cilium-restart.yaml --limit k8s-manager-1`
- обновление (конфиг)
  - Только параметры в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/cilium-install.yaml --limit k8s-manager-1`
  - `ansible-playbook -i hosts.yaml playbooks/apps/cilium-restart.yaml --limit k8s-manager-1`

## cert-manager. Официальный helm
## 
- установка
  - Только параметры в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/cert-manager-install.yaml --limit k8s-manager-1`
  - Что ставится: cert-manager, network-policy
- обновление (версия)
  - Только параметры в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/cert-manager-install.yaml --limit k8s-manager-1`
- обновление (конфиг)
  - Только параметры в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/cert-manager-install.yaml --limit k8s-manager-1`

## traefik (ingress). yaml -> helm
## Есть dashboard, который доступен по URL -> требуется Certificate (cert-manager-CRD)
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
  - Параметры - перечисляются в traefik-cli (как аргументы при запуске)
  - Параметры в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/traefik-install.yaml --limit k8s-manager-1`

## cilium-post (относится к cilium). yaml -> helm
## Есть hubble-ui, который доступен по URL -> требуется Certificate (cert-manager-CRD)
## Это просто дополнительная конфигурация
##
- установка
  - Только параметры в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/cilium-post-install.yaml --limit k8s-manager-1`
  - Ставится: network-policy (для kube-system), ingress (hubble-ui)
- обновление (Версия + конфиг)
  - Только параметры в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/cilium-post-install.yaml --limit k8s-manager-1`

## haproxy (ingress-2). Официальный helm
## Автоматически подхватывает конфиг, который генерируется через CRD
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

## longhorn. yaml -> helm
## Есть UI, который доступен по URL -> требуется Certificate (cert-manager-CRD)
## Автоматически подхватывает конфиг. Обновили конфиг в ConfigMap -> сразу подхватил и начал использовать
## `namespace: longhorn-system`, МЕНЯТЬ НЕЛЬЗЯ. Так написано в документации
## 
- установка
  - Параметры в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/longhorn-install.yaml --limit k8s-manager-1`
  - Ставится: longhorn, network-policy, ingress (longhorn-ui)
- обновление (версия)
  - Параметры в `hosts.yaml`
  - Скачать новый yaml. https://raw.githubusercontent.com/longhorn/longhorn/v1.10.1/deploy/longhorn.yaml
  - Удалить `kind: Namespace`. Так как - helm его сам создаст в момент установки
  - Есть изменения в дефолтных конфигах. Их надо не потерять
  - То есть: после вставки нового `*.yaml` -> надо вернуть обновленные дефолиные конфиги
  - Версия не указывается в `hosts.yaml` -> так как версия будет в `*.yaml`
  - Пример обновленного конфига - `docs/longhorn/other/...`
  - `ansible-playbook -i hosts.yaml playbooks/apps/longhorn-install.yaml --limit k8s-manager-1`
- обновление (конфиг)
  - Параметры в `hosts.yaml`
  - Обновить необходимые параметры в `playbooks/apps/charts/longhorn/...`
  - `ansible-playbook -i hosts.yaml playbooks/apps/longhorn-install.yaml --limit k8s-manager-1`

## gitlab-config. yaml -> helm
##
- установка
  - Только параметры в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/gitlab-config-install.yaml --limit k8s-manager-1`
  - Ставится: network-policy
- обновление (версия + конфиг)
  - `ansible-playbook -i hosts.yaml playbooks/apps/gitlab-config-install.yaml --limit k8s-manager-1`

## gitlab-minio. yaml -> helm
## Есть UI + API, доступны по URL -> требуется Certificate (cert-manager-CRD)
## 
- установка
  - Только параметры в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/gitlab-minio-install.yaml --limit k8s-manager-1`
  - Ставится: gitlab-minio, ingress (minio-api, minio-console-ui)
- обновление (версия + конфиг)
  - Только параметры в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/gitlab-minio-install.yaml --limit k8s-manager-1`

## gitlab. yaml -> helm
## Есть UI, доступен по URL -> требуется Certificate (cert-manager-CRD)
## Важный момент про config
1. Для deployment используется `checksum/config: ...`
2. При обновлении конфига `/gitlab/templates/configmap.yaml` POD c GitLab будет перезапущен
3. checksum/config - вычисляется через HELM (include ...)
## 
- установка
  - Только параметры в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/gitlab-install.yaml --limit k8s-manager-1`
  - Ставится: gitlab, ingress (UI, git, pages, registry, ssh-tcp)
- обновление (версия + конфиг)
  - Только параметры в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/gitlab-install.yaml --limit k8s-manager-1`

## argocd. yaml -> helm
## Есть UI, доступен по URL -> требуется Certificate (cert-manager-CRD)
## Нет автоматической обработки новых конфигов (Как у Cilium). То есть: После обновления конфигов - ручной restart
- установка
  - Только параметры в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/argocd-install.yaml --limit k8s-manager-1`
  - Ставится: argocd, network-policy, ingress (argocd-ui, h2c-grpc), git-ops
- обновление (версия)
  - Только параметры в `hosts.yaml`
  - Скачать новый yaml. https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
  - Поменять ВСЕ вхождения `namespace: argocd` -> `namespace: {{ .Values.namespace }}` (там 3 вхождения, для RoleBinding)
  - Есть изменения в дефолтных конфигах. Их надо не затерепть. То есть: после вставки нового `*.yaml` -> надо вернуть обновленные дефолиные конфиги
  - Версия не указывается в `hosts.yaml` -> так как версия будет в `*.yaml`
  - Пример обновленного конфига - `docs/arocd/...`
  - `ansible-playbook -i hosts.yaml playbooks/apps/argocd-install.yaml --limit k8s-manager-1`
  - `ansible-playbook -i hosts.yaml playbooks/apps/argocd-restart.yaml --limit k8s-manager-1`
- обновление (конфиг)
  - Только параметры в `hosts.yaml`
  - `ansible-playbook -i hosts.yaml playbooks/apps/argocd-install.yaml --limit k8s-manager-1`
  - `ansible-playbook -i hosts.yaml playbooks/apps/argocd-restart.yaml --limit k8s-manager-1`

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
   1. Обновить конфиг для `haproxy-apiserver-lb` на всех текущих manager
   2. По одному за раз
   3. Через `--limit ....` указать manager
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

# В каком порядке устанавливать
1. cilium
   1. `ansible-playbook -i hosts.yaml playbooks/apps/cilium-install.yaml --limit k8s-manager-1`
   2. Ставится
      1. cilium
      2. host-network-policy
      3. kube-system-network-policy
2. cert-manager
   1. `ansible-playbook -i hosts.yaml playbooks/apps/cert-manager-install.yaml --limit k8s-manager-1`
   2. Ставится
      1. cert-manager
      2. network-policy
3. traefik
   1. `ansible-playbook -i hosts.yaml playbooks/apps/traefik-install.yaml --limit k8s-manager-1`
   2. Есть dashboard, который доступен по URL -> требуется Certificate (cert-manager-CRD)
   3. Ставится
      1. traefik
      2. network-policy
      3. ingress (dashboard)
4. cilium-post
   1. `ansible-playbook -i hosts.yaml playbooks/apps/cilium-post-install.yaml --limit k8s-manager-1`
   2. Есть hubble-ui, который доступен по URL -> требуется Certificate (cert-manager-CRD)
   3. Ставится
      1. network-policy (для kube-system)
      2. ingress (hubble-ui)
5. haproxy-ingress
   1. `ansible-playbook -i hosts.yaml playbooks/apps/haproxy-install.yaml --limit k8s-manager-1`
   2. Ставится
      1. haproxy-ingress
      2. network-policy
6. longhorn
   1. `ansible-playbook -i hosts.yaml playbooks/apps/longhorn-install.yaml --limit k8s-manager-1`
   2. Есть UI, который доступен по URL -> требуется Certificate (cert-manager-CRD)
   3. Ставится
      1. longhorn
      2. network-policy
      3. ingress (longhorn-ui)
7. gitlab-config
   1. `ansible-playbook -i hosts.yaml playbooks/apps/gitlab-config-install.yaml --limit k8s-manager-1`
   2. Ставится
      1. network-policy
8. gitlab-minio
   1. `ansible-playbook -i hosts.yaml playbooks/apps/gitlab-minio-install.yaml --limit k8s-manager-1`
   2. Есть UI + API, доступны по URL -> требуется Certificate (cert-manager-CRD)
   3. Ставится
      1. gitlab-minio
      2. ingress (minio-api, minio-console-ui)
9. gitlab
   1. `ansible-playbook -i hosts.yaml playbooks/apps/gitlab-install.yaml --limit k8s-manager-1`
   2. Есть UI, доступен по URL -> требуется Certificate (cert-manager-CRD)
   3. Ставится
      1. gitlab
      2. ingress (git, pages, registry, ssh-tcp)
10. argocd
    1. `ansible-playbook -i hosts.yaml playbooks/apps/argocd-install.yaml --limit k8s-manager-1`
    2. Есть UI, доступен по URL -> требуется Certificate (cert-manager-CRD)
    3. Ставится
       1. argocd
       2. network-policy
       3. ingress (argocd-ui, h2c-grpc)
       4. git-ops

# Как и что обновлять
1. cilium
   1. Установка идет через официальный helm
   2. Только параметры в `hosts.yaml`
2. cert-manager
   1. Установка идет через официальный helm
   2. Только параметры в `hosts.yaml`
3. traefik
   1. Установка через yaml -> helm
   2. Руками обновить CRDs. https://raw.githubusercontent.com/traefik/traefik/v3.6/docs/content/reference/dynamic-configuration/kubernetes-crd-definition-v1.yml
   3. Руками обновить RBAC. https://raw.githubusercontent.com/traefik/traefik/v3.6/docs/content/reference/dynamic-configuration/kubernetes-crd-rbac.yml
   4. Версия в `hosts.yaml`
4. cilium-post
   1. Установка через yaml -> helm
   2. Только параметры в `hosts.yaml`
5. haproxy
   1. Установка идет через официальный helm
   2. Только параметры в `hosts.yaml`
6. longhorn
   1. Установка через yaml -> helm
   2. Скачать новый yaml. https://raw.githubusercontent.com/longhorn/longhorn/v1.10.1/deploy/longhorn.yaml
   3. Важно! Установка идет через HELM -> надо из yaml манифеста удалить `kind: Namespace`. Уначе будет ошибка
   4. Поменять ВСЕ вхождения `namespace: longhorn-system` -> `namespace: {{ .Values.namespace }}` (там около 20 вхождения)
   5. Есть изменения в дефолтных конфигах. Их надо не затерепть. То есть: после вставки нового `*.yaml` -> надо вернуть обновленные дефолиные конфиги
   6. Версия не указывается в `hosts.yaml` -> так как версия будет в `*.yaml`
7. gitlab + gitlab-minio
   1. Установка через yaml -> helm
   2. Только параметры в `hosts.yaml`
8. argocd
   1. Установка через yaml -> helm
   2. Скачать новый yaml. https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
   3. Поменять ВСЕ вхождения `namespace: argocd` -> `namespace: {{ .Values.namespace }}` (там 3 вхождения, для RoleBinding)
   4. Есть изменения в дефолтных конфигах. Их надо не затерепть. То есть: после вставки нового `*.yaml` -> надо вернуть обновленные дефолиные конфиги
   5. Версия не указывается в `hosts.yaml` -> так как версия будет в `*.yaml`
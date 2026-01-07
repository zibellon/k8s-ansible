# Что делать после запуска кластера
## Запуск кластера = все запустили и настроили CNI plugin

## Только после настройки СХД - можно устанавливать что-то, где требуется `volume`

## Добавить lbael для Node
## `kubectl get nodes --show-labels` - вывести список Node
## `kubectl label nodes <node-name> node-role.kubernetes.io/worker=""` - добавить label
## Для manager node - уже есть label: `node-role.kubernetes.io/control-plane=`

## Добавить annotation для Node - нужно для Longhorn
## Какие tags: смотреть в `./longhorn`
## `kubectl annotate nodes <node-name> node.longhorn.io/default-node-tags='tag_1,tag_2,tag_3' --overwrite`

## Руками настроить
## Руками = зайти на сервер, создать нужные YAML файлы и сделать `kubectl apply -f`
- cert-manager (`./cert-manager`)
- Traefik + ingress-controller (`./traefik`). L7 (http | https | grpc)
- Haproxy + ingress-controller (`./haproxy`). L3 | L4 (TCP | UDP)
- Longhorn (`./longhorn`)
- gitlab-minio (`./gitlab-minio`)
- gitlab (`./gitlab`)
- argo-cd (`./argo-cd`)

## Зайти на Longhorn-UI и настроить СХД
## Настроить СХД = разметить Nodes / Disks. Добавить `tags` (скорее всего не надо, так как автоматически будет размечено через `annotations`)
## Как и что указывать написано в `./longhorn`

## Запустить `gitlab-minio` (S3). GitLab - будет хранить тут контейнеры (blobs)
## Зайти в UI и настроить bucket, ключи доступа. Только приватный доступ
## Ключей будет два:
- registry
- runner_cache
## Бакетов будет достаточно много. Основные: `registry`, `runner-cache`

## Запустить GitLab (`./gitlab`)
## Переключить registry на s3 (Тут будут хранится docker-image-blobs)
## Переключить все большие данные на S3

## Запустить argocd (`./argo-cd`)
## Настроить git репозиторий для хранения `*.yaml` манифестов
## Настроить паттерн app-of-apps (через *.yaml)

## ---
## После установки и настрйоки ArgoCD - ВСЕ, что попадает в кластер, идет только через APP-OF-APPS
## ---

## Запустить gitlab-runner (`./gitlab-runner`)
## Подключить к GitLab

## Portainer + longhorn-storage-class (`./portainer`)
Для корректной работы Portainer - требуется `volume` (где хранится информация о чем-то из portainer)

---ОСТАНОВИЛСЯ ТУТ---

## Запустить argocd-image-updater (`./argocd-image-updater`)
## Присоединить к gitlab-docker-registry
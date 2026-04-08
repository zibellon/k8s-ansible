# ---
# Шифрование ETCD. Ротация ключей
# ---
## api-server, на каждой control-plane будет перезапущен 3 раза (так сказано в официальной документации)
## Это не самый быстрый процесс
## Делается через mv: manifests -> tmp, mv: tmp -> manifests (чтобы kubelet убил api-server и снова его восстановил)
## Этот процесс спровоцирует полную остановку api-server
##
- `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/etcd-key-rotate.yaml`

# ---
# SANS (api-server). Обновление имен (SANS) в сертификатах
# ---
## На каждой control-plane будет создан новый api-server.crt
## Каждый текущий api-server - будет перезапущен один раз
## Перезапуск - последовательный (по одному за раз)
##
- `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/apiserver-sans-update.yaml`

# ---
# Обслуживание сервера (cordon + drain) и возврат в работу
# ---
##
- `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/node-drain-on.yaml --limit k8s-worker-3`
  - Вывод ноды на обслуживание
- `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/node-drain-off.yaml --limit k8s-worker-3`
  - Вернуть ноду в работу

# ---
# Удаление node
# ---
## Отключение node от кластера
## Перед этим надо выполнить = `Вывод Node на обслуживание`
##
- `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/node-remove.yaml --limit k8s-worker-4`

# ---
# Очистка сервера, от всех компонентов k8s
# ---
##
1. `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/server-clean.yaml --limit k8s-worker-4`
   1. Выполнение команды  `kubeadm reset --force`
   2. Удаление директорий для k8s

# ---
# ESO, force синхронизация ExternalSecrets
# ---
## Все namespaces
- `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/eso-force-sync.yaml`

## Только определенный namespace. Например: gitlab
## Доступные namespace: все, для которых есть eso_vault_integration_XXX (Пример - hosts.yaml)
- `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/eso-force-sync.yaml --tags gitlab`

# ---
# Longhorn + Restore (from backup)
# ---
## Ситуация: был кластер с longhorn + all-system-components, есть backup, нужно восстановиться из бэкапа
## Бэкап лежит на S3 (все доступы есть). Правило такое: все секреты в кластере только через vault + ESO
## А чтобы запустить VAULT (Где лежат секреты) - нужно восстановить его volume из бэкапа
## Чтобы восстановить что-то из бэкапа - нужно скачать бэкап через longhorn
## чтобы скачать backup - нужно создать k8s: Secret (s3-creds)
## Получается: Замкнуты круг. Что делать ?
## ---
## План действий
## - в `hosts-vars-override/` определить все секреты для восстановления бэкапов (их может быть несколько). Переменная: `longhorn_s3_restore_secrets`
## - запуск `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ longhorn-s3-restore-create.yaml`. Это создаст секреты в k8s
## - Чтобы восстановить PV + PVC = нужен namespace.
## - Чтобы создать namespace - нужно для каждого системного компонента вызвать его установку + `--tags pre`
## - Это создаст namespace + какие-то части для работы компонента (NetworkPolicy + ESO)
## - Зайти в longhorn-ui, использовать секреты для скачивания и восстановления backups, восстановить volume для каждого компонента
## - запуск `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ longhorn-s3-restore-delete.yaml`. Это удалит секреты из k8s
## - запустить производить запуск каждого компонента, с восстановленным состоянием

# ---
# ArgoCD - git-ops, какие секреты должны быть в VAULT
# ---
## Правило_1: ВСЕ ключи из vault - кладутся в секрет в k8s.secret
## Правило_2: существует всего два ТИПА секретов
## - git_ops_repo_pattern === `argocd.argoproj.io/secret-type: repo-creds`
## - git_ops_repo_direct === `argocd.argoproj.io/secret-type: repository`
## 
## Примеры

1. git SSH
   1. type: "git"
   2. url: "ssh://..." или "https://..."
   3. sshPrivateKey: "-----BEGIN..."
2. git userpass
   1. type: "git"
   2. url: "https://..."
   3. username: "..."
   4. password: "..."
3. helm repo
   1. type: "helm"
   2. name: "traefik"
   3. url: "https://traefik.github.io/charts"
   4. username: "..." (не добавлять если публичный)
   5. password: "..." (не добавлять если публичный)
4. helm OCI
   1. type: "helm"
   2. enableOCI: "true"
      1. Для helm_repo — не добавлять, ArgoCD будет считать enableOCI = false
   3. name: "traefik"
   4. url: "https://traefik.github.io/charts"
   5. username: "..." (не добавлять если публичный)
   6. password: "..." (не добавлять если публичный)

# ---
# Argocd, добавление нового приложения
# ---
##
1. Вводные данные
   1. Весь кластер настроен, все работает исправно
2. Что нужно
   1. Новый проект: my-casino-app
   2. Компонены: Postygres, redis, Nats, back, front, cron
   3. Одно окружение: prod
3. Последовательность действий
   1. Настрйока VAULT (Эта часть делается через Ansible)
      1. В файле `hosts-vars-override/` - добавить необходимые политики для vault
         1. 1 role
         2. 1 политика. Для чтения всего содержимого по пути (kv_engine/.../my-casino-app-prod/*)
      2. Синхронизировать vault-policy-sync (можно с тагами - policy-add, role-add. так как это только новые политики)
      3. Проверить, что в VAULT все политики и все роли создались успешно
   2. Настройка приложения - через AppOfApps
   3. Через паттерн app-of-apps - создать AppProject + Application
   4. В git, создать новую директорию, где будут лежать все манифесты для этого проекта + контур (infra/.../my-casino-app/prod)
   5. Создать Chart.yaml + values.yaml + templates/namespace.yaml
   6. Запустить + дождаться синхронизации
   7. Сгенерировать все необходимые секреты (login, pass, url и так далее) и положить их в VAULT по правильным путям
      1. Правильные пути - те, которые были указаны в policy + role
   8.  Вернуться в git-ops репозиторий
   9.  Создать SA + SecretStore + 4 ExternalSecret (postgres, redis, nats, common)
   10. Залить эти изменения и дождаться синхронизации
   11. Проверить, что все SecretStore + ExternalSecret + k8s.Secret = успешно созданы и готовы
   12. Запустить Postgres, redis, nats
       1.  Все ENV креды - берутся из секретов, которые были созданы через ESO
   13. Запустить back + front + cron
       1.  Все ENV креды - берутся из секретов, которые были созданы через ESO
   14. Готово
4.  Ротация creds (РУЧНОЙ РЕЖИМ)
    1.  поменять что-то в vault
    2.  Если надо, поменять в mount-volue (напрмиер - postgres, нужно выполнить команду внутри контейннера)
    3.  выполнить через ArgoCD-ui = sync + force + replace
    4.  Готово

# ---------
# Alertmanager
# ---------
Alertmanager CRD
└── alertmanagerConfiguration.name → alertmanager-root-config (root, один)
    └── route: receiver=null (fallback)
        ├── [auto-injected] namespace=mon → AlertmanagerConfig "alertmanager-root-config"
        ├── [auto-injected] namespace=gitlab → AlertmanagerConfig "gitlab-alerts"
        ├── [auto-injected] namespace=backend → AlertmanagerConfig "backend-alerts"
        └── [auto-injected] namespace=... → любое количество
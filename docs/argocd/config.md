1. Какие флаги можно передать в КАЖДЫЙ отдельный компонент
   1. https://argo-cd.readthedocs.io/en/stable/operator-manual/server-commands/argocd-server/
   2. https://argo-cd.readthedocs.io/en/stable/operator-manual/server-commands/argocd-application-controller/
   3. https://argo-cd.readthedocs.io/en/stable/operator-manual/server-commands/argocd-repo-server/
   4. https://argo-cd.readthedocs.io/en/stable/operator-manual/server-commands/argocd-dex/
   5. https://argo-cd.readthedocs.io/en/stable/operator-manual/server-commands/additional-configuration-method/
2. Какие есть настройки у Project
   1. https://github.com/argoproj/argo-cd/blob/master/docs/operator-manual/project.yaml
   2. https://argo-cd.readthedocs.io/en/stable/operator-manual/project-specification/
3. Какие есть настройки у Application
   1. https://github.com/argoproj/argo-cd/blob/master/docs/operator-manual/application.yaml
   2. https://argo-cd.readthedocs.io/en/stable/user-guide/application-specification/

# Некоторые параметры
## ---
## Ставится на `Application`
1. `metadata.finalizers: resources-finalizer.argocd.argoproj.io | resources-finalizer.argocd.argoproj.io/background`
   1. Если удаляется `Application` -> все ресурсы, под управлением Application - удаляются
2. `automated.prune`
   1. Влияет на авто-синхронизацию
   2. Если ресурс удален из Git -> удали его из кластера (чистка)
   3. Можно запретить некоторые ресурсы от удаления через: `argocd.argoproj.io/sync-options: Prune=false`
3. `automated.selfHeal`
   1. Влияет на авто-синхронизацию
   2. Ресурс был применен через ArgoCD -> он находится под контролем ArgoCD
   3. Руками внесли изменение в этот ресурс - через `kubectl apply -f ...`
   4. ArgoCD это увидит и вернет в исходное состояние - как есть в Git
4. `syncOptions.PrunePropagationPolicy=background`
   1. Каким образом удалять ресурсы, которые надо удалить
   2. В фоне (background) или в синхронном режиме (foregraound)
5. `syncOptions.ApplyOutOfSyncOnly`
   1. Чтобы синкать только нужные части
   2. По дефолту - синкает ВСЕ ресурсы, которые есть под управлением этого `Application`
   3. Если там 100+ ресурсов (Например) - это может создать лишнюю нагрузку на api-server
6. `syncOptions.Replace=true`
   1. Если что-то очень большое и не лезет в apply
   2. kubectl create - если ресурса еще НЕТ
   3. kubectl replace - если ресурс уже ЕСТЬ
7. `syncOptions.SkipDryRunOnMissingResource=true`
   1. Не проверяй готовность CRD
   2. CRD - часть sync процесса (находится рядом с остальными ресурсами). Проблем никаких нет, ArgoCD все сам разрулит
   3. CRD - были созданы отдельны или СОЗДАЮТСЯ отдельно. ArgoCD не может их найти в процессе текущего SYNC и падает с ошибкой
   4. Если поставить этот флаг в TRUE -> ArgoCD пропустит этот этап и создаст все как есть
8. `orphanedResources: warn: true` - Потерянные ресурсы
   1. Это ресурсы, которые относятся к namespace
   2. Есть `Application` (argocd), у него есть destination - namespace
   3. Если там есть ресурсы, которые не управлтся этим application - будет `warn`
## ---
## Ставится на `resource`, через `annotations`
1. `argocd.argoproj.io/sync-options: Delete=false`
   1. При удалении `Application` - все ресурсы удаляются. Если выставлена настройка у `Application`
   2. Через эту аннотацию, можно запретить удаление ресурса
   3. ВАЖНО. Для `kind: Pvc` - это условно-обязательный параметр
2. `argocd.argoproj.io/sync-options: Prune=false`
   1. При удалении ресурса из git - удаляется из кластера
   2. Через эту аннотацию, можно запретить удаление ресурса
   3. ВАЖНО. Для `kind: Pvc` - это условно-обязательный параметр
3. `argocd.argoproj.io/sync-options: Replace=true`
   1. Если что-то очень большое и не лезет в apply
   2. kubectl create - если ресурса еще НЕТ
   3. kubectl replace - если ресурс уже ЕСТЬ
4. `argocd.argoproj.io/sync-options: Force=true`
   1. kubectl delete + kubectl create

## Важно про конфиги. Есть НЕСКОЛЬКО конфигов
## Основные, которые надо изменить перед запуском
1. `argocd-cm`
   1. https://github.com/argoproj/argo-cd/blob/master/docs/operator-manual/argocd-cm.yaml
2. `argocd-cmd-params-cm`
   1. https://github.com/argoproj/argo-cd/blob/master/docs/operator-manual/argocd-cmd-params-cm.yaml
3. `argocd-ssh-known-hosts-cm`
   1. https://github.com/argoproj/argo-cd/blob/master/docs/operator-manual/argocd-ssh-known-hosts-cm.yaml
## Остальные, которые можно не трогать
1. `argocd-gpg-keys-cm`
2. `argocd-notifications-cm`
3. `argocd-rbac-cm`
4. `argocd-tls-certs-cm`

## Ingress, доступ к UI
## Общее правило: `The API server should be run with TLS disabled`
## Открыть и прочитать статью: https://argo-cd.readthedocs.io/en/stable/operator-manual/ingress/
## Так как используется traefik: https://argo-cd.readthedocs.io/en/stable/operator-manual/ingress/#ingressroute-crd
## ---
## Есть два варианта, как отключить TLS на уровне ArgoCD-server
1. Отредактировать `kind: Deployment` && `name: argocd-server`
   1. Добавить флаг `--insecure` к аргументам запуска
   2. В разделе `containers`, в разделе `args`
2. Отредактировать `kind: ConfigMap` && `name: argocd-cmd-params-cm`
   1. Прочитать инструкцию: https://argo-cd.readthedocs.io/en/stable/operator-manual/server-commands/additional-configuration-method/
   2. Посмотреть на пример: https://argo-cd.readthedocs.io/en/stable/operator-manual/argocd-cmd-params-cm.yaml
   3. Открыть файл `install.yaml` и отредактировать: `kind: ConfigMap` && `name: argocd-cmd-params-cm`
   4. Добавить параметр: `server.insecure: "true"`

## Автоматическая синхронизация - https://argo-cd.readthedocs.io/en/stable/user-guide/auto_sync/#automated-sync-semantics
## По дефолту: 120 sec (2 min) + jitter 60 sec (1 min) = 180 sec (3 min)
## От 120 секунд до 180 секунд
## ---
## Где изменить эти параметры
## В файле `install.yaml`, в ConfigMap `app.kubernetes.io/name: argocd-cm`
## Есть два основных параметра
1. `timeout.reconciliation: 30s`
   1. Интервал запроса в git
2. `timeout.reconciliation.jitter: 10s`
   1. В течение какого времени запустить обновления из git (если есть расхождения)
## ---
## Как изменить эти параметры
## Два варианта обновления этих параметров
1. Перед первым запуском
2. После первого запуска

## Вечный progressing у ресурса `king: Ingress`
## https://github.com/argoproj/argo-cd/issues/14607
## https://argo-cd.readthedocs.io/en/latest/operator-manual/health/#custom-health-checks
## ---
## Лечится через модификацию правил проверки для ресурса `Ingress`
## В файле `install.yaml`, в Configmap `app.kubernetes.io/name: argocd-cm`
```
data:
  resource.customizations: |
    networking.k8s.io/Ingress:
      health.lua: |
        hs = {}
        hs.status = "Healthy"
        hs.message = "Skip health check for Ingress"
        return hs
```

## Отключить анонимный доступ + увеличить время жизни токена для сессии пользователя
## Где изменить эти параметры
## В файле `install.yaml`, в ConfigMap `app.kubernetes.io/name: argocd-cm`
1. `users.anonymous.enabled: "false"`
2. `users.session.duration: "120h"`

## Как получить пароль для входа в argocd-UI
## После запуска, пароль для пользователя `admin` генерируется в автоматическом режиме
## Пароль хранится в k8s.secret = `argocd-initial-admin-secret`, в поле `password`

## Как получить пароль через kubectl
`kubectl get secret argocd-initial-admin-secret -n argocd -o jsonpath='{.data.password}' | base64 --decode`

## Важный момент про изменение пароля
## Есть всего два варианта изменения пароля
## Через Secret - его сменить нельзя (Через yaml - нельзя)
1. Через UI - зайти с текущим паролем на GUI и в настрйоках его сменить
2. Через CLI (argo cli)

## Обновление параметров (ConfigMap)
1. Изменить что нужно в ConfigMap
   1. Скорее всего будем менять в `install.yaml`
2. Применить ConfigMap: `kubectl apply -n argocd -f install.yaml`
3. Теперь ВАЖНО
   1. `Нужно перезапустить ВСЕ компоненты ArgoCD`
   2. ArgoCD в автоматическом режиме не применяет новые конфиги
   3. Если делается изменение напрямую в ConfigMap
   4. Если делается что-то через UI -> изменения применяются АВТОМАТИЧЕСКИ
4. Получить список запущенных компонентов: `kubectl get pod -n argocd`
5. Руками перезапустить компоненты argocd: `kubectl delete pod <POD_NAME> -n argocd`
   1. argocd-application-controller
   2. argocd-applicationset-controller
   3. argocd-dex-server
   4. argocd-repo-server
   5. argocd-server

## Настроить доверенные `known_hosts`
## Есть Три варианта настройки
1. ДО запуска
   1. В `install.yaml` есть `kind: ConfigMap`, `name: argocd-ssh-known-hosts-cm`
2. ПОСЛЕ запуска (UI)
   1. настройка через UI
   2. Эта настрйока будет влиять на ConfigMap
3. ПОСЛЕ запуска (ConfigMap)
   1. Описано выше, как работает

## В этот список нужно добавить SSH_KNOWH_HOST от того `git` хранилища, которое будет использоваться
## В этом примере - от GitLab
1. `ssh-keyscan HOST_NAME` - получить все отпечатки (PublicKey)
   1. Особенность, если ПОРТ !== 22 -> обязательно указать в команде `ssh-keyscan`
   2. ssh-keyscan -p <PORT_NUMBER> <HOST_NAME>
2. ВСЕ полученные отпечатки - добавить в файл
3. Обязательно, с указанием порта
4. Пример
   1. [ssh.github.com]:443
   2. github.com
   3. gitlab.com
   4. [some-git-host.com]:2345 - Вот это, если порт !== 22

## Настройка проекта
1. У проекта `default` - отключить ВСЕ разрешения
   1. через `kubectl apply -f ...` - пример файл в `./git-ops-install.yaml`
2. Создать новый проект + ВСЕ разрешения + КРЕДЫ от репозитория + корневое (главное приложение)
   1. через `kubectl apply -f ...` - пример файл в `./git-ops-install.yaml`
3. Создать репозиторий на git со следуюущей структурой
   1. `git-ops/argocd-system` -> тут храняться ресурсы `kind: AppProject` и `kind: Application`. Для самого argoCD
      1. Все ресурсы должны находиться именно в директории `git-ops/argocd-system` (В корне)
      2. Если сделать `git-ops/argocd-system/some-app-123/app.yaml` -> Такой ресурсы не будет обработан
      3. Как изменить логику: добавить флаг `recurse` (Пример ниже)
      4. Для каждого нового проекта - создается ОДИН файл `SOME_PROJECT_NAME.yaml`. В нем лижет все вместе
         1. `kind: AppProject` - один
         2. `kind: Application` - сколько необходимо
         3. Каждый `kind: Application` смотрит на свою директорию по пути `git-ops/argocd-client/PROJECT_NAME/APP_NAME`
         4. Еще что-то полезное, если оно нужно ArgoCD
   2. `git-ops/argocd-client` -> тут хранятся ресурсы для приложений (Deployment, Service, ConfigMap, ...)
      1. Для каждого приложения `kind: Application` - отдельная директория внутри `git-ops/argocd-client/PROJECT_NAME/...`
      2. `git-ops/argocd-client/nginx-test/back-test` - Для запуска сервиса `back-test` в проекте `nginx-test`
      3. То есть
         1. `kind: AppProject` = nginx-test
         2. `kind: Application` = back-test

## Recursive (recurse) - https://argo-cd.readthedocs.io/en/stable/user-guide/directory/#enabling-recursive-resource-detection
## argocd - смотрит за ресурсами только в корневой директории, указанной в настройках `path`
## Чтобы изменить эту логику - добавляем такое свойство
## Важный момент: работает ТОЛЬКО с голыми `*.yaml` манифестами. `Helm / Kustomize === ERROR`
```
apiVersion: argoproj.io/v1alpha1
kind: Application
spec:
  source:
    directory:
      recurse: true
```

## Паттерн - APP_OFF_APPS
## Одно приложение - через которое создаются ВСЕ остальные приложения
https://argo-cd.readthedocs.io/en/stable/operator-manual/cluster-bootstrapping/

# ---
# Как запускать в ПЕРВЫЙ раз (ArgoCD + GitLab)
# ---
1. Запускаем GitLab + ArgoCD
2. Создаем репозиторий: git-ops (приватный)
3. Создаем новый SSH ключ + добавляем его в этот репозиторий с правами только ЧТЕНИЕ
   1. ArgoCD - нужны права только на чтение
4. В этот репозиторий заливаем структуру
   1. /argocd-system/readme.md
   2. /argocd-client/readme.md
5. Через kubectl apply -f ... -> активируем манифест для основных настроек ArgoCD
   1. убрать права для project default
   2. Создать новый project + МАКСИМАЛЬНЫЕ права (Для app-of-apps)
   3. Создать новое Application + направить его на `/git-ops/argocd-system` (Для app-of-apps)
   4. Создать SSH ключ (как k8s-secret) + указать к нему правильные labels
6. Добавляем новый проект + приложения: `bottle_service`
7. Написали рабочий код и залили его в GIT для проекта
   1. Прошел CI-CD, сборка, заливка в registry
   2. deploy (через argocd-api) = ERROR
   3. Почему упал deploy: Еще нет - ничего для ArgoCD
8. ОДНИМ коммитом заливаем в репозиторий `/git-ops`
   1. /git-ops/argocd-system/bottle_service.yaml
      1. `kind: AppProject` + policy
      2. `kind: Application`. Их будет много
   2. /git-ops/argocd-client/bottle_service
      1. `/back-dev/install.yaml`. bottle_service_back_dev
      2. `/web-dev/install.yaml`. bottle_service_web_dev
      3. `/db-dev/*.yaml`. bottle_service_db_dev. вот тут будет redis + postgres (два файла в одной директории)
9.  argoCD подхватит
    1.  Project + policy + Application
    2.  Создаст их в кластере
    3.  После создания - они подхватят все из дочерних директорий в директории `/git-ops/argocd-client/bottle_service`
10. Через argoCD-UI (cli. UI - проще) - получаем ТОКЕН-ы для выполнения команды sync для web-dev | back-dev
    1.  Это два отдельных Application - каждое смотрит на свою директорию
    2.  для каждого Application - свой токен доступа
11. Указываем эти токены в gitLab-ci переменных
    1.  Или просто, как plaintext в `.gitlab-ci.yaml`
12. В GitLab-CI.yaml - настраиваем curl для вызова ARGO-CD (пример ниже)
13. Вручную запускаем CI/CD pipeline

# ---
# Момент про namespace
# ---
1. /back-dev/install.yaml | /web-dev/install.yaml /db-dev/*.yaml -> будет запускаться в ХЗ какой последовательности через ArgoCD
2. В КАЖДОМ файле указываем в верхней части `kind: Namespace` + полностью его расписываем
3. То есть: будет дублирование, но только в моменте с namespace

# ---
# Момент про namespace-2
# ---
1. Если сделать по варианту выше - то в UI ArgoCD будет висеть предупреждение
   1. У вас в нескольких местах Один и тот же ресурс определен, WARNING
2. Чтобы избежать этой ситуации, выделяется ОДНО приложение, которое называется: `bottle_service_shared`
   1. Смотрит на отдельную директорию
   2. В этой диреткории лежат только те ресурсы, которые ОБЩИЕ для всех приложений в этом проекте
   3. Например: Namespace, Regcred, Minio (Если он общий) и ТД

# ---
# Момент про app-of-apps + argo-labels (Delete | Prune)
# ---
1. Какая иерархия зависимостей
   1. `root-prj` + `root-app`. Создали через .yaml
   2. `client-prj-a|b|c` + `client-app-a|b|c` (много. Ресурсы для `root-app`)
   3. deployment | service | ConfigMap | ... (много. Ресурсы для `client-app-a|b|c`)
2. У всех `xxx-app` установлены такие параметры для auto-sync
   1. prune: true
   2. selfHeal: true
   3. allowEmpty: true
   4. SkipDryRunOnMissingResource=true
   5. ApplyOutOfSyncOnly=true
   6. PrunePropagationPolicy=background
3. Основное внимание на параметр: `prune: true`
   1. Если ресурс в GIT был переименован или перемещен -> ArgoCD посчитает что
      1. Надо удалить СТАРЫЙ ресурс
      2. Создать НОВЫЙ ресурс
   2. Это не должно вызывать никаких проблем, КРОМЕ случаев с `PVC`
      1. На все PVC устанавливаем защитный label
      2. `annotations: argocd.argoproj.io/sync-options: Delete=false,Prune=false`
      3. Это значит, что такой ресурс НИКОГДА не будет удален автоматически через ArgoCD
4. Если удалить App -> оно потянет за собой ВСЕ ресурсы (Которые находятся под его контролем)
   1. На все `client-prj-a|b|c` + `client-app-a|b|c` устанавливаем защитный label
      1. `annotations: argocd.argoproj.io/sync-options: Delete=false`
   2. Все `client-prj-a|b|c` + `client-app-a|b|c`, которые были созданы через `root-app` (app-of-apps) - не будут удалены в случае удаления root-app
   3. На `root-app` не ставим этот label, так как его создали руками, *.yaml

# ---
# Момент про ssh-keys, repository | repo-creds
# ---
1. У ArgoCD есть два типа репозиториев: `repository` | `repo-creds`
2. `repository` === project_scope. То есть - он не виден за пределами ArgoProject
3. То есть: в паттерне app-of-apps (Project + Apps), если использовать `repository` при попытке вызвать РУЧНОЙ sync на Application, созданный через app-of-apps -> получаем ошибку: Cannot create AUTH_SOCKET ...
4. Чтобы это исправить - надо создать `repo-creds`
   1. Эта штука - не привязана к проекту и видна для всех

# ---
# Момент про: Политики и права + GitLab-CI
# ---

## На примере просто сервиса (Back, Web, Pg, Redis)
## Сервис называется: bottle_service
1. Создаем проект: `kind: AppProject`. Название - bottle_service
   1. Добавляем role + policy (Только sync на нужные `kind: Application`):
   2. `p, proj:bottle_service:gitlab-sync, applications, sync, bottle_service/bottle_service_back_dev, allow`
   3. `p, proj:bottle_service:gitlab-sync, applications, get, bottle_service/bottle_service_back_dev, allow`
   4. `p, proj:bottle_service:gitlab-sync, applications, sync, bottle_service/bottle_service_web_dev, allow`
   5. `p, proj:bottle_service:gitlab-sync, applications, get, bottle_service/bottle_service_web_dev, allow`
   6. Интересная проблема:
      1. Нам нужна роль - только с правами на sync
      2. Чтобы оно работало - нужно дать еще права на get
      3. Иначе - всегда будет лететь permission-deniedЕсли при попытке выполнить SYNC через API 
2. Создаем токен для роли
   1. Зайти в UI -> projects -> ${PRJ_NAME} -> roles -> ещлут-скуфеу
   2. Токен будет показан только ОДИН РАЗ - надо сразу сохранить
   3. ВАЖНО: `By default, the cli creates them without an expirations date. Even if a token has not expired, it cannot be used if the token has been revoked`
   4. ВАЖНО: `Since the JWT token is associated with a role's policies, any changes to the role's policies will immediately take effect for that JWT token.`
3. То есть
   1. ОДНА роль`gitlab-sync`
   2. в проекте `bottle_service`
   3. имеет права на команду `sync`
   4. в `kind: Application` = (`bottle_service_back_dev`, `bottle_service_web_dev`)
   5. ВАЖНО: до `kind: Deployment` внутри приложения ограничить нельзя
   6. Токен имеет права - ограниченные только ДО APplication

## argocd-api: CURL запрос
curl -k -sS \
   -H "Authorization: Bearer $ARGOCD_API_TOKEN" \
   -H "Content-Type: application/json" \
   -X POST "$ARGOCD_API_URL/api/v1/applications/bottle_service_back_dev/sync" \
   -d '{"prune":true,"strategy":{"apply":{"force":true}}}'

## Дополнительная информация

roles:
- name: gitlab-sync
   description: "Только sync для приложения test_app_123 из GitLab CI"
   policies:
   - p, proj:test_prj_123:gitlab-sync, applications, sync, test_prj_123/test_app_123, allow
   - p, proj:test_prj_123:gitlab-sync, applications, get, test_prj_123/test_app_123, allow

# A role which provides sync privileges to only the guestbook-dev application, e.g. to provide
# sync privileges to a CI system
- name: ci-role
   description: Sync privileges for guestbook-dev
   policies:
   - p, proj:my-project:ci-role, applications, sync, my-project/guestbook-dev, allow

   # NOTE: JWT tokens can only be generated by the API server and the token is not persisted
   # anywhere by Argo CD. It can be prematurely revoked by removing the entry from this list.
   jwtTokens:
   - iat: 1535390316

---ВОТ_ТУТ---

## argocd-image-updater

## ссылка на GitHub
https://github.com/argoproj-labs/argocd-image-updater/blob/stable/manifests/install.yaml

## Скачать файл
https://raw.githubusercontent.com/argoproj-labs/argocd-image-updater/refs/tags/stable/manifests/install.yaml
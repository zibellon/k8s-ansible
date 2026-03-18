# Как оно работает

# Какие системные сервисы участвуют в vault + ESO
- traefik
- haproxy
- longhorn
- zitadel
- gitlab
- gitlab-runner
- argocd + argocd-git-ops
- argocd-git-ops

# какая структура variables
## внутри каждого namespace = есть SA + SecretStore (для подключения в VAULT) + N количество ExternalSecret
## eso_vault_integration_XXX = отвечает за создание SA + SecretStore
## eso_vault_integration_XXX_secrets = отвечает за создание необходимого количества ExternalSecret (есть особенность работы с полем type)
## eso_vault_integration_XXX_secrets_extra = отвечает за дополнительные секреты для определенного компонента
## 
- traefik
  - eso_vault_integration_traefik
  - eso_vault_integration_traefik_secrets
  - eso_vault_integration_traefik_secrets_extra
- haproxy
  - eso_vault_integration_haproxy
  - eso_vault_integration_haproxy_secrets
  - eso_vault_integration_haproxy_secrets_extra
- longhorn
  - eso_vault_integration_longhorn
  - eso_vault_integration_longhorn_secrets
  - eso_vault_integration_longhorn_secrets_extra
- gitlab
  - eso_vault_integration_gitlab
  - eso_vault_integration_gitlab_secrets
  - eso_vault_integration_gitlab_secrets_extra
- gitlab-runner
  - eso_vault_integration_gitlab_runner
  - eso_vault_integration_gitlab_runner_secrets
  - eso_vault_integration_gitlab_runner_secrets_extra
- zitadel
  - eso_vault_integration_zitadel
  - eso_vault_integration_zitadel_secrets
  - eso_vault_integration_zitadel_secrets_extra
- argocd
  - eso_vault_integration_argocd
  - eso_vault_integration_argocd_secrets
  - eso_vault_integration_argocd_secrets_extra
- argocd-git-ops (ВАЖНО: тот же namespace что и у argocd)
  - eso_vault_integration_argocd_git_ops
  - eso_vault_integration_argocd_git_ops_secrets
  - eso_vault_integration_argocd_git_ops_secrets_extra
##
- vault_policies + vault_policies_extra
- vault_roles + vault_roles_extra

## Немного терминов и правил
1. vault-policy
   1. состоит из: name, path, actions
   2. название, уникальное в рамках всего VAULT
   3. path - на какой путь распространяется
   4. actions - какие действия доступны
2. vault-role
   1. На данный момент, используем только один вариант - kubernetes-token-auth
   2. состоит из: name, SA, namespace, policy-list
   3. name - уникальное, в рамках всего VAULT
3. SecretStore
   1. CRD (от externalSecretsOperator)
   2. Отвечает за подключение к VAULT (какой SA использовать, какой role, какой URL)
   3. В каждом NS - может быть несолько таких штук
   4. Например: argocd + argocd-git-ops (два отдельныйх SecretStore + SA, но NS = один)

# merge-eso. Как работает
1. Соединяет политики (два массива) = vault_policies + vault_policies_extra
2. Првоерка на дубликаты
   1. по полю = name
   2. Если есть дубликаты = ошибка
3. Соединяет роли (два массива) = vault_roles + vault_roles_extra
4. Првоерка на дубликаты
   1. по полю = name
   2. Если есть дубликаты = ошибка
5. Проверка, что ВСЕ политики из вложенного массива политик (в каждом элементе role) - есть в массиве с политиками (vault_policies + vault_policies_extra)
   1. Если чего-то нет - ошибка
6. Проверка, что для всех объектов eso_vault_integration_traefik_XXX - есть нужная роль в vault
   1. проверка именно на роль
   2. как проверяется: должно совпасть три поля
      1. name === role_name
      2. namespace === COMPONENT.namespace
      3. sa_name === sa_name
   3. То есть: Если добавить новую интеграцию для нового компонента или поменять что-то в текущей (Напрмиер SA_name) и не поменять в массиве с политиками: то будет ошибка, до запуска
7. Соединяет массивы для создания ESO - externalSecret (только их можно расширить)
   1. Соединяет по два массива для каждого компонента
   2. например traefik
      1. eso_vault_integration_traefik
      2. eso_vault_integration_traefik_secrets
      3. eso_vault_integration_traefik_secrets_extra
8. Проверка на уникальность: название k8s.secret OR название externalSecret
   1. В рамках одного namespace - нельзя создать два ExternalSecret или k8s.Secret с одинаковым названием

## VAULT-POLICY-SYNC (как работает)
1. Можно запустить через --tags
   1. policy-add - только добавить новые политики
   2. policy-update - только обновить текущие
   3. policy-delete - только удалить то, которых более нет в ansible
   4. role-add - только добавить новые
   5. role-update - только обновить текущие
   6. role-delete - только удалить то, которых более нет в ansible
2. Если запустить без --tags
   1. Добавляем все новые политики
   2. Обновляем текущие политики
   3. удаляем те, которых нет в ansible
   4. Добавляем все новые Role
   5. Обновляем текущие Role
   6. удаляем те, которых нет в ansible

# Логика, запуска нового проекта (продукта)
1. Вся система работат исправно, все настроено
2. Надо запустить новый проект: my-wallet-app
   1. У этого проекта будет 3 окружения: prod, dev, shared
   2. каждое окружение === отдельный namespace
3. Правила разделения секретов в VAULT
   1. Делим по namespace
   2. да, можно разделить как угодно, хоть hash использовать
   3. Но по изначальным правилам: prefix === namespace
4. В каждом namespace - будет свой: SA + SecretStore + ExternalSecret
   1. shared: 1 SA + 1 SecretStore + 1 ExternalSecret
   2. dev: 1 SA + 1 SecretStore + 2 ExternalSecret
   3. prod: 1 SA + 1 SecretStore + 2 ExternalSecret
5. Политики VAULT
   1. 1 role === 1 SA + 1 namespace + NNN policy
6. Сначала, надо подготовить vault
   1. Знаем, какие нужны namespace - их 3 штуки
   2. Добавляем политики в массив = vault_policies_extra
   3. Добавляем роли в массив = vault_roles_extra
   4. Вызываем синронизацию политик VAULT - только с --tags ADD
   5. все, vault готов
7. Vault + значения секретов
   1. По нужным путям, для каждого namespace - кладем в vault секреты (postgres, redis, и так далее ...)
8. Теперь gitops
   1. все подготовили в ArgoCD + gitops (AppProject + 3 Application)
   2. Заливаем Namespace + SA + SecretStore + ExternalSecret
   3. Ждем синхронизацию
   4. Запускаем остальные компоненты с использованием секретов, которые были созданы через ESO
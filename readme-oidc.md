# Как настроить OIDC для тех компонентов, которые это поддерживают

## Список компонентов
- Vault (HashiCorp vault, bank-vaults)
- Gitlab
- ArgoCD
- Kargo
- Grafana
- Outline (docs, wiki)

# Все примеры и объяснения будут на основе Zitadel (этот id-provider используется в этой конфигурации)

## Метод для разведки конфигурации
https://<zitadel_domain>/.well-known/openid-configuration

## Особенность установки Vault + OIDC
- Для установки Zitadel = нужен рабочий Vault
  - Так как требуется интеграция с ESO (пароль для Postgres, masterKey для Zitadel и так далее)
  - То есть: порядок установки Vault -> Zitadel
- НО: Если в Vault мы хотим включить OIDC (через Zitadel) = к моменту запуска VAULT, zitadel уже должна быть доступна
- А настроить Zitadel, ДО запуска VAULT - нельзя

## Как решаем эту проблему
- Устанавливаем VAULT первый раз + OIDC=false
- VAULT запустился, все окей
- Запускаем ZITADEL
- ZITADEL запустилась, все окей
- Заходим в Zitadel и настраиваем Organization + Project + Application для VAULT
- кладем секреты куда требуется (в сам VAULT)
- Меняем конфиг установки vault на OIDC=true
- запускаем --tags vault-cr повторно
- все отлично, vault + OIDC = работают

# общий принцип настройки
- создать новую организацию, системная организация. Например: `cluster-tools`
  - Проверить ее настройки и поправить если требуется
- внутри этой организации создать Project + Application
  - 1 компонент = 1 Project = 1 Application
  - поправить настройки каждого Project + Application
- Создать нужных пользователей + emailVerified=true + задать им DefaultPassword
- Выдать права на доступ User <-> Project

## Пункт 1: Org / Project / Application (в ZITADEL Console, под instance-админом)

- Переключатель org (слева вверху) -> Add Organization -> имя `cluster-tools` -> создать + зайти в неё
- Projects -> Create -> имя `grafana` -> Зайти в проект
- Внутри проекта -> Applications -> New
- Роли/гранты -> НЕ создавать
- Users -> создать staff-юзеров
- Для каждого: поле Email заполнено и отмечено verified

## Пункт 2. Положить секрет в Vault (ключи ровно `clientId` / `clientSecret`)
- path = eso-secret/mon-system/grafana/oidc
  - clientId: <ClientID из шага A4>
  - clientSecret: <Client Secret из шага A4>

## Интересная настройка. ORG - project - application
`Back-Channel Logout URI` = OIDC Back-Channel Logout — механизм, когда IdP (ZITADEL) уведомляет приложение о завершении сессии сервер-к-серверу (по «заднему каналу», в обход браузера)

# Последовательность действий, детально по каждому проекту

Перед установкой
- выбрать нормальные first Name и last name для админа (Сейчас = ZITADEL admin)

Установили

Зашли под первым админом (который instance-admin) и он привязан к организации самой первой
- Сразу запросили установку 2FA/MFA/U2F

Заходим в default settings в консоли
- `Add Organization Domain as suffix to loginnames` = TRUE
- `Force MFA for all users` = TRUE
- `User Registration allowed` = FALSE
- `Multifactor Init Check` = 0 (чтобы сразу запрашивал установку MFA)
- `OpenID Connect Settings, Access Token + ID Token` = access и refresh (1 час и 4 часа)
  - Настройка времени жизни токена

## Создаём организацию = `cluster-tools`

## Настройки организации
## Все эти настройки - НАСЛЕДУЮТСЯ от `DefaultSettings`
- `Add Organization Domain as suffix to loginnames` = TRUE
- `Force MFA for all users` = TRUE
- `Multifactor Init Check` = 0
- `User Registration allowed` = FALSE

## Для каждого системного приложения - создать СВОЙ проект (чтобы можно было права настроить)
- `vault`
- `gitlab`
- `argocd`
- `kargo`
- `grafana`
- `outline`

## настройки проекта
- `Only authorized users can authenticate` = TRUE
- `Authentication is restricted to users from organizations that have been granted access to this project` = TRUE

## Создаём application (по одному в каждом проекте)
- `vault`
- `argocd`
- `gitlab`
- `kargo`
- `grafana`
- `outline`

## Настройка каждого приложения
- `Include user's profile info in the ID Token` = TRUE (Без этого ArgoCD - не работает)

## Grafana
### zitadel-side

- Тип = Web
- Auth method = CODE / Basic (client secret)
- Redirect URI = https://<grafana_domain>/login/generic_oauth
- Refresh token grant = ДА (Grafana шлёт offline_access + use_refresh_token)
- User Info inside ID Token = НЕТ (Grafana читает claims из userinfo api_url)
- Post Logout URI = https://<grafana_domain> (можно не ставить)

### Grafana-side (env GF_AUTH_GENERIC_OAUTH_*, из mon_system_grafana_oidc)

- AUTH_URL = https://<grafana_domain>/oauth/v2/authorize?prompt=select_account
- TOKEN_URL = https://<grafana_domain>/oauth/v2/token
- API_URL = https://<grafana_domain>/oidc/v1/userinfo
- SCOPES = openid email profile offline_access urn:zitadel:iam:org:id:<ORG_ID>
- LOGIN_ATTRIBUTE_PATH = preferred_username
- EMAIL_ATTRIBUTE_PATH = email
- NAME_ATTRIBUTE_PATH = name
- SKIP_ORG_ROLE_SYNC = true
- ALLOW_SIGN_UP = true
- USE_REFRESH_TOKEN = true
- USE_PKCE = false
- CLIENT_ID / CLIENT_SECRET = secretKeyRef -> eso-mon-system-grafana-oidc-creds (из Vault)

## ArgoCD
### zitadel-side

- Тип = Web
- Auth method = Basic / CODE
- Redirect URI = https://<argocd_domain>/auth/callback
- Refresh token grant = ДА (offline_access + refreshTokenThreshold: 2h; держи 2h < ID Token Lifetime. Сейчас в настройках 4h)
- User Info inside ID Token = ДА (RBAC читает email из id_token)
- Post Logout URI = https://<argocd_domain> (можно не ставить)

### ArgoCD-side (kustomize-патчи)

oidc.config:
  name: ZITADEL
  issuer: https://<zitadel_domain>
  clientID: "<appId>" # КАВЫЧКИ обязательны (числовой ID) — в override argocd_oidc_client_id
  clientSecret: $eso-argocd-oidc-creds:clientSecret
  requestedScopes: ["openid","profile","email","offline_access","urn:zitadel:iam:org:id:<ORG_ID>"]
  refreshTokenThreshold: "2h"

argocd-rbac-cm:
  scopes: '[email]'
  policy.default: '' # deny-by-default
  policy.csv: g, <email>, role:admin # грант по email (в override argocd_policy_csv_list)

## GitLab
### zitadel-side

- Тип = Web
- Auth method = Basic / CODE
- Redirect URI = https://<gitlab_domain>/users/auth/openid_connect/callback
- Refresh token grant = нет (offline_access не запрашивается — login-only)
- User Info inside ID Token = нет (userinfo + discovery)
- Post Logout URI = нет https://<gitlab_domain> (можно не ставить)

### GitLab-side (omniauth provider + global.appConfig.omniauth)

provider:
  name/args.name = openid_connect
  label = ZITADEL
  scope = ["openid","profile","email","urn:zitadel:iam:org:id:<ORG_ID>"]
  response_type = code
  issuer = https://<zitadel_domain>
  discovery = true
  client_auth_method = basic
  uid_field = sub
  extra_authorize_params.prompt = select_account
  client_options.identifier/secret = "{{ .clientId }}"/"{{ .clientSecret }}" (ESO, закавычены)
appConfig.omniauth:
  enabled=true
  allowSingleSignOn=[openid_connect]
  blockAutoCreatedUsers=true
  autoLinkUser=[openid_connect]

## Outline
### zitadel-side

- Тип = Web
- Auth method = Basic / CODE
- Redirect URI = https://<outline_domain>/auth/oidc.callback
- Refresh token grant = да
- User Info inside ID Token = да
- Post Logout URI = нет https://<outline_domain> (можно не ставить)

## Outline-side

- URL = https://<outline_domain> — публичный домен, из него собирается redirect <URL>/auth/oidc.callback
- OIDC_CLIENT_ID / OIDC_CLIENT_SECRET = Vault eso-secret/outline/oidc (ключи clientId/clientSecret)
- OIDC_AUTH_URI = https://<zitadel_domain>/oauth/v2/authorize
- OIDC_TOKEN_URI = https://<zitadel_domain>/oauth/v2/token
- OIDC_USERINFO_URI = https://<zitadel_domain>/oidc/v1/userinfo
- OIDC_USERNAME_CLAIM = preferred_username
- OIDC_DISPLAY_NAME = ZITADEL (текст на кнопке входа)
- OIDC_SCOPES = openid profile email offline_access + org-pin urn:zitadel:iam:org:id:<ORG_ID>
- OIDC_DISABLE_REDIRECT = true

## Потом создаем Users
Вот тут два важных момента
- сразу указываем пароль + почта верифицирована. ПРи первом входе сотрудника его заставит сменить пароль и добавить MFA
- добавляем сотрудников в проекты. Без этого - сотрудник не сможет попасть внутрь OIDC приложения

сотрудник - может зайти в саму zitadel. именно в ее UI

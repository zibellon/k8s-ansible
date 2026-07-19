# ZITADEL — единый SSO/IdP для кластера master-k8s

## Кратко, что удалось выяснить и как оно работает

1. `Domains (именно ссылки в браузере)`
   1. `ExternalDomain` - задается один раз при первом запуске. Это первый доверенный домен - если так можно сказать
      1. Этот домен светится везде и всюду, если обращаться к нему для работы с токенами и так далее
   2. Можно добавить несколько (много) - дополнительных доменов (Instance Domains)
      1. Сделать это можно ТОЛЬКО через api-call, нельзя сделать через Console-UI
      2. Потом все просто: Ingress + Certificate и так далее. Zitadel уже готова принять этот домен
2. `Login Name Pattern`
   1. DefaultSettings - domains - Add Organization Domain as suffix to loginnames
   2. Эту настройку ОБЯЗАТЕЛЬНО надо включить
   3. Чтобы loginName = login@<org-domain>
   4. В этом случае: две организации `AAA` и `BBB`, пользователь со своим личным email делает регистрацию и там и там
   5. Это получается ДВА РАЗНЫХ пользователя, которые никак не связаны. И это крайне важно
3. SSO для технических сервисов
   1. self-registration ВЫКЛючена — staff-аккаунты заводит админ
4. SSO для технических сервисов (GitLab, Grafana, ArgoCD)
   1. Zitadel - устанавливается ПЕРВОЙ. До этих сервисов
   2. Заходим в Zitadel и настраиваем ORG = system-tools (например), Project = system-tools
   3. Applications: GitLab, Grafana, ArgoCD и так далее
   4. Потом запуск самих компонентов. Включаем авторизацию по SSO + указываем конфиги Zitadel
   5. Зайти через SSO под своей учеткой в каждый компонент: cluster-admin@master-domain.com (напрмиер)
      1. теперь внутри каждого компонента создана учетка, которая связана с Zitadel
   6. Потом выйти и зайти под admin | root - по логин + пароль и выдать максимальные права на новую учетку
   7. выйти из под admin | root
   8. Зайти снова через SSO - теперь ты админ


> Рабочая справка по проектированию и настройке ZITADEL как единого источника
> аутентификации для кластера: инфраструктурные тулзы (ArgoCD, Grafana, GitLab)
> и клиентские приложения (BFF).

---

## 0. Ключевые решения

- Один инстанс ZITADEL на весь кластер. Технический (системный) домен: `zitadel.master-domain.com`
- Issuer берётся из домена запроса (если это зарегистрированный instance-домен) Это позволяет prod-токенам клиентских приложений иметь `iss=https://auth.<project>` и не светить `master-domain.com`.
- Организация (org) — единственная жёсткая граница изоляции пользователей
  - `Company` — все сотрудники (staff)
  - Отдельная org на каждый проект и на каждое окружение для клиентов
- Staff-тулзы (ArgoCD/Grafana/GitLab): ZITADEL только аутентифицирует. Права раздаются вручную внутри каждого тула. Никакого маппинга ролей/групп из ZITADEL не нужно
- Teleport SSO (OIDC/SAML) только в Enterprise. Community → остаётся на локальных аккаунтах
- Клиентские приложения: паттерн BFF (front → back → zitadel-api), у каждого свой кастомный UI
- Email не уникален глобально — один email можно зарегистрировать в разных org. Уникален loginName = `username@orgdomain` (при включённом «Login Name Pattern» с суффиксом домена org)

---

## 1. Из чего состоит ZITADEL (модель объектов)

ZITADEL — OIDC/OAuth2/SAML-провайдер + хранилище пользователей + админ-консоль
Технически: один Go-бинарь (stateless) на PostgreSQL (event-sourcing внутри), масштабируется репликами.

Иерархия объектов

```
Instance                      ← вся инсталляция ZITADEL. ОДИН issuer (zitadel.master-domain.com)
│                                Владеет: instance-доменами, дефолтными политиками
│
├── Organization ("org")      ← ЕДИНСТВЕННАЯ жёсткая граница изоляции ПОЛЬЗОВАТЕЛЕЙ. Своя политика логина/MFA, свой брендинг, свой домен
│   ├── User                  ← human (интерактивный) ИЛИ machine (service account, PAT/JWT)
│   ├── Project               ← контейнер РОЛЕЙ и приложений (НЕ граница изоляции)
│   │   ├── Application        ← один клиент: тип OIDC | SAML | API
│   │   └── Role              ← строка-ключ: "grafana:admin", "player", "vip"
│   ├── User Grant           ← (= Authorization = Role Assignment): User × Project × [Roles]
│   ├── Managers             ← админ-RBAC над самим ZITADEL (ORG_OWNER и т.п.)
│   ├── IdP                  ← входящая федерация (Google/GitHub) — НЕ путать с «ZITADEL как IdP»
│   └── Policies             ← login / password / lockout / domain / branding
```

Три вещи, которые надо помнить

- Instance — единственный «арендатор» и единственный дефолтный issuer (`ExternalDomain`)
- Organization — «кто может залогиниться и как». Юзеры из org A не видят логин org B
- Project — «что этому юзеру разрешено в приложении X». Проект НЕ изолирует пользователей, он группирует роли и приложения

**Application** бывает трёх типов — это то, что создаётся под каждый Ingress/клиент:

- OIDC
  - ArgoCD, Grafana, GitLab, React/Node-приложения
  - Web = `Code`+secret (конфиденциальный); SPA = `PKCE` (публичный, без секрета)
- API
  - Node-бэкенд, который только валидирует токены (introspection)
  - `JWT (private key)` или `Basic`
- SAML
  - легаси-SP без OIDC
  - metadata/ACS

**Machine users** (service accounts) — для backend→ZITADEL API (создание юзеров, выдача ролей).
Аутентификация через **PAT** или **private-key-JWT**. Права даются как **Manager-роль** (узкий скоуп), не как project-роль.

**Actions/Flows** — JS-хуки (v1) или webhook-Target (v2) на триггерах вроде *Pre Userinfo / Pre Access Token*.
Ими формируют кастомные claims (например `groups`-массив или `subscription_tier`).

**Ключевое предложение:** роль оказывается в токене только если сошлись ТРИ условия:
- (1) Project определяет Role
- (2) User Grant связывает User→Role внутри org
- (3) Application «ассертит» роли
- И почти всегда сверху нужен **Action**, переупаковывающий роль в формат конкретного потребителя

---

## 2. Домены — самое важное

### 2.1 Три РАЗНЫХ «домена», не путать

1. Instance domain
   1. `ExternalDomain` + доп. custom-домены
   2. Instance
   3. Роутинг + issuer. Host, на который ZITADEL отвечает и из которого строит `iss`
2. Trusted domain
   1. trusted domain
   2. Instance
   3. ZITADEL принимает такой Host (не «Instance not found»), но не роутит/не issue. Для CDN/прокси
3. Organization domain
   1. verified org-домен (один primary)
   2. Organization
   3. Суффикс логина + брендинг + org-discovery Задаёт `username@casino.example.com`. Никогда не issuer

### ОЧЕНЬ ВАЖНО
> Кнопка «Add domain» внутри организации — это org-домен (слой 3), НЕ issuer
> Issuer задаётся instance-доменом (слой 1).

### 2.2 Issuer задаётся тремя параметрами при self-host

```yaml
# Helm values → zitadel.configmapConfig (env ZITADEL_EXTERNALDOMAIN / EXTERNALSECURE / EXTERNALPORT)
ExternalDomain: zitadel.master-domain.com
ExternalPort: 443
ExternalSecure: true       # → схема issuer = https
TLS:
  Enabled: false           # TLS терминируется на Traefik, а не внутри ZITADEL
```

Issuer: `https://zitadel.master-domain.com`. Discovery: `https://zitadel.master-domain.com/.well-known/openid-configuration`.

⚠️ **Issuer практически неизменяем после go-live.** Смена `ExternalDomain` — это не rolling-restart,
надо **пере-прогнать setup-Job** ZITADEL, и это ломает `iss` во всех токенах и redirect URI.
Выбрать домен один раз до онбординга.

⚠️ **`ExternalSecure: true` — про край, а не про листенер ZITADEL.** TLS у нас терминирует Traefik
(cert-manager) → `TLS.Enabled: false`. Рассинхрон → `http://`-issuer и invalid-issuer у клиентов.

### 2.3 Issuer следует за доменом запроса

ZITADEL строит issuer и все OIDC-эндпоинты из домена, на который пришёл запрос
если этот домен зарегистрирован как instance-домен. Дословно из доки Instance:

> «For any context based URL (e.g. OAuth, OIDC, SAML endpoints, links in emails, …) the requested domain will be used.»

Следствие: prod-бэкенд casino ходит в ZITADEL по `https://auth.casino.example.com`
каждый prod-токен получает `iss=https://auth.casino.example.com`, discovery/JWKS/письма — под этим хостом, `master-domain.com` не фигурирует. `ExternalDomain` (`zitadel.master-domain.com`) остаётся дефолтным/каноническим;
вторичные instance-домены работают параллельно, каждый как свой issuer «по запросу»

> Один ZITADEL спокойно отвечает на много Ingress (как SeaweedFS). Условие приёма запроса
> Host должен быть `ExternalDomain` ИЛИ instance-custom-домен ИЛИ trusted-домен
> Иначе — `Instance not found` (почти всегда виноват прокси, переписавший Host).

### 2.4 Что в токене может выдать технический домен

Требование «prod-токен не знает про `master-domain.com`» = проверить эти три места:

- `iss` (главное)
  - Если бэк ходит на `zitadel.master-domain.com` = да утечет
  - Instance-домен `auth.casino.example.com` = бэк ходит только на него
- `preferred_username` (loginName с суффиксом org-домена)
  - Да! По умолчанию `alice@<org>.zitadel.master-domain.com`
  - org primary-домен = клиентский (`casino.example.com`) |
- roles-claim `{role:{orgId: orgPrimaryDomain}}`
  - Да, если запрашиваешь scope ролей
  - В BFF роли обычно не запрашиваешь; иначе — тот же фикс primary-домена

`aud`/`azp` содержат `clientId@projectName` — имя проекта, не домен; про `master-domain.com` там ничего

> **Бонус BFF:** JWT лежит в **httpOnly-cookie**, но её значение видно в DevTools → Cookies
> Для строгого «никогда не светить» — включить шифрование значения cookie (AES-GCM)
> Тогда в браузер не попадает ни один claim — ни `iss`, ни `preferred_username`.

### 2.5 Как добавить instance-домен

Console === UI

Добавление instance-домена требует system-level прав и не может быть вызвано из instance-контекста.
Console работает в instance-admin (IAM_OWNER) контексте = кнопки «добавить» на уровне инстанса нет
Существующие instance-домены Console показывает (read-only). Добавить — только через API системным пользователем

Актуальный метод Instance Service v2 `AddCustomDomain`:

```bash
POST https://zitadel.master-domain.com/zitadel.instance.v2.InstanceService/AddCustomDomain
Authorization: Bearer <SYSTEM_USER_JWT>     # системный пользователь, НЕ обычный PAT из Console
Content-Type: application/json

{ "instanceId": "<instanceId>", "customDomain": "auth.casino.example.com" }
```

- `<SYSTEM_USER_JWT>` — токен системного пользователя из runtime-конфига ZITADEL (`SystemAPIUsers`,
  ключ настраивается при деплое). Тем же механизмом добавлялись первые instance-домены.
- Регистрация домена в ZITADEL — половина дела. Вторая половина в инфре:
  - Ingress + TLS-cert (cert-manager) на `auth.casino.example.com` → тот же ZITADEL-service
- Для staff-тулз новые instance-домены не нужны — они используют готовый `zitadel.master-domain.com`.
  Новые домены — только под prod/stage клиентских приложений

---

## 3. Топология для кластера

```
Instance: zitadel.master-domain.com          (дефолтный issuer)
│
├── Org: "Company"                    ← ВСЕ сотрудники (DevOps, тимлиды, разработчики)
│   └── Project(ы): cluster-tools     Applications: argocd / grafana / gitlab (+ окружения)
│                                     Роли НЕ используются (права раздаём в тулах руками)
│
├── Org: "casino-prod"   (домен auth.casino.example.com)       ← клиенты casino PROD
├── Org: "casino-stage"  (домен auth.stage.casino.example.com) ← клиенты casino STAGE
├── Org: "casino-dev"    (домен zitadel.master-domain.com общий)       ← клиенты casino DEV
├── Org: "crypto-swap-*" ...
├── Org: "vpn-bot-*" ...
├── Org: "onlyfans-*" ...
└── Org: "crypto-card-*" ...
```

- Изоляция пулов клиентов («у каждого стенда своя БД»)
  - отдельная org на окружение: `casino-prod`, `casino-stage`, `casino-dev`
  - Юзер prod физически не существует в org stage
  - Плюс своя Postgres приложения на каждый стенд (по `zitadel_sub`)
- Не-утечка домена
  - свой instance-домен там, где нельзя светить `master-domain.com`
  - общий `zitadel.master-domain.com` там, где можно (dev, staff)

> test/dev/prod для staff-тулз = отдельные Application в одном Project (не отдельные org),
> различаются только redirect URI и client secret
> Для клиентов окружения = отдельные org

---

## 4. Staff SSO: ArgoCD, Grafana, GitLab

### 4.1 Модель «только вход, права — руками»

ZITADEL здесь делает ровно одно: **аутентифицирует**. Раз права раздаются внутри каждого тула руками:

> НЕ нужен flatten-Action, НЕ нужны project-роли ZITADEL, НЕ нужен маппинг групп в RBAC

Каждому тулу нужны две настройки
- (а) при первом входе завести юзера с дефолтной/нулевой ролью
- (б) чтобы повторный OIDC-вход не перезатирал назначенную вручную роль

**Сторона ZITADEL:** org `Company` (self-registration ВЫКЛючена — staff-аккаунты заводит админ),
один project `cluster-tools`, в нём по OIDC-Application на тул (Web + Code). Роли не создаём.
«Check for Role Assignment on Authentication» — выключено (иначе вход упадёт)

### 4.2 ArgoCD

`argocd-cm.oidc.config` — обычный OIDC (issuer `https://zitadel.master-domain.com`
clientID/secret через `$eso-argocd-oidc-creds:clientSecret`)
⚠️ Секрет в ОТДЕЛЬНЫЙ секрет с меткой `app.kubernetes.io/part-of: argocd` через ESO, НЕ в `argocd-secret`
(инвариант: `argocd-secret` пустой)

`argocd-rbac-cm`:
```yaml
data:
  scopes: '[email]'        # сопоставлять пользователей по email (человекочитаемо)
  policy.default: ''       # DENY по умолчанию → новый SSO-юзер входит и НИЧЕГО не видит
  policy.csv: |
    # добавляешь руками по мере надобности, по email:
    # g, alice@master-domain.com, role:admin
    # p, bob@master-domain.com, applications, get, casino/*, allow
```
⚠️ «Сделать юзера админом» в ArgoCD = правка `argocd-rbac-cm` (ConfigMap/GitOps), не клик в UI.
Плюс: RBAC матчится по claim'у, а не по аккаунту → email можно вписать ДО первого входа.

### 4.3 Grafana

`[auth.generic_oauth]` без `role_attribute_path`, и главное:
```ini
skip_org_role_sync = true      # ← ключ: OAuth НЕ трогает роль при каждом входе
```
`api_url = https://zitadel.master-domain.com/oidc/v1/userinfo` (userinfo под `/oidc/v1/`, authorize/token — под `/oauth/v2/`).
Первый вход → `auto_assign_org_role` (Viewer). Повышение — в UI (**Administration → Users**),
и `skip_org_role_sync=true` не даёт следующему логину сбросить.

### 4.4 GitLab

OmniAuth `openid_connect` без group-маппинга. Для модели «я решаю, кто войдёт»:
```yaml
blockAutoCreatedUsers: true    # новый SSO-юзер создаётся, но заблокирован до approve админом
uid_field: "sub"               # НЕ email (email меняется; sub неизменен)
```
Повышение: Admin Area → Users → Edit → Administrator (или сначала Approve). Секрет — через ESO

### 4.5 Bootstrap-последовательность (работает для всех трёх)

1. Создал себе пользователя в ZITADEL (как админ ZITADEL).
2. Вошёл в тул через OIDC → аккаунт создался, прав нет.
3. Вышел, вошёл как локальный admin (login+pass).
4. Повысил свой OIDC-аккаунт до нужной роли.
5. Вышел, вошёл снова через OIDC → я админ

- Порядок важен для Grafana/GitLab: сначала OIDC-вход (создаёт теневой аккаунт), потом повышение.
  Для ArgoCD порядок неважен (RBAC по claim'у).
- **login+pass остаётся** во всех трёх (Grafana `/login`, ArgoCD `admin.enabled: true`, GitLab root).
- **Оставить минимум один локальный admin/root как break-glass** — не отключать, чтобы падение
  ZITADEL не заблокировало доступ.

---

## 5. Клиентские приложения (BFF)

### 5.1 Паттерн

`front → HTTP request → back → zitadel-api`. У каждого приложения свой кастомный UI
backend — «умный прокси» для запросов авторизации
Хостед-логин ZITADEL не используется (значит его брендинг/тексты не важны).

### 5.2 Модель и изоляция

- Org на окружение (`casino-prod`/`casino-stage`/`casino-dev`): изолированные пулы клиентов
- SPA (React): OIDC-app тип `User Agent + PKCE` (публичный, без секрета)
- Node backend: OIDC-app тип API (introspection)
  - ZITADEL-рекомендация — introspection (revocation-aware: уважает logout/бан), локальная JWT-проверка по JWKS — для дешёвых чтений
- Не-утечка домена: prod/stage = свой instance-домен (`auth.<...>`); dev → общий `zitadel.master-domain.com`.
- org primary-домен = клиентский (закрывает `preferred_username`); шифрование cookie (закрывает всё)

### 5.3 Изоляция prod/dev (гарантии)

1. Разные instance-домены → разный `iss` (prod без `master-domain.com`)
2. Разные org + apps → разные `client_id/secret`, разные пулы. Prod-юзер не существует в dev-org.
3. Разные redirect_uri → code одного окружения нельзя обменять в другом.
4. Cookie host-only на домене API → без `Domain=master-domain.com`.
5. Валидация на бэке: `iss === ZITADEL_ISSUER` и `aud содержит CLIENT_ID/PROJECT_ID` → чужой токен отвергается.

---

## 6. Email / loginName — модель уникальности

### 6.1 Проверенные факты

- Email НЕ уникален глобально
  - Один email можно использовать для разных пользователей в разных org
  - Регистрация одного email в двух org создаёт `два независимых пользователя`
- Уникален loginName = `username@orgdomain`.
  - За счёт суффикса домена org одинаковый username/email в разных org не коллизирует — при включённом «Login Name Pattern»
- Вход по «голому» email неоднозначен между org
  - ZITADEL не может определить организацию, если email есть в нескольких org
  - В BFF снимается тем, что бэк всегда задаёт org-контекст

### 6.2 «Login Name Pattern» = Domain Policy `userLoginMustBeDomain`

- ВКЛ (суффикс доменом org)
  - `vasya@casino.example.com`
  - Уникальность username = в рамках org
  - Same-email-в-двух-org = работает
- ВЫКЛ (голый username)
  - `vasya`
  - Уникальность username = на весь инстанс
  - Same-email-в-двух-org = ❌ второй `vasya` не создастся

> Держать ВКЛ
> Это то, что делает возможным «один email в двух проектах»
> Выключишь — схема ломается на второй регистрации.

- Настройка **instance-wide** (Default Settings). Один «ВКЛ» на инстанс.
- Суффикс = primary-домен каждой org:
  - `Company`(primary `master-domain.com`) = `alice@master-domain.com`
  - `casino-prod`(primary `casino.example.com`) = `vasya@casino.example.com`
- Менять задним числом больно (перегенерация loginName) — зафиксировать ВКЛ до онбординга

### 6.3 Staff email (alice, bob)

- Email задаёшь ты — делай реальные, уникальные, стабильные на `master-domain.com` (`alice@master-domain.com`).
- Это `identity-хэндл во всех staff-тулзах` (ArgoCD `scopes:'[email]'`, Grafana/GitLab)
- Именно его вписываешь в `policy.csv` и по нему повышаешь права
- loginName причёсывается через primary-домен org `Company`

### 6.4 Клиентский email (vasya в casino и vpn-bot)

- Оба регистрации проходят → два независимых пользователя с одним email
- Клиентские org: `userLoginMustBeDomain = true` (дефолт) + включить «Login with Email»
- В BFF каждый вызов ZITADEL должен быть org-scoped (не только create):
  - `searchUserByEmail` (dedup/миграция) — фильтр по orgId (иначе из vpn-bot найдёшь casino-шного vasya);
  - логин/сессия — резолвить `userId` в рамках org → `checks:{user:{userId}}` (или scope
    `urn:zitadel:iam:org:id:<orgId>` в authorize, чтобы весь flow был org-pinned).

## 7. Карта доменов (пример casino) + правило

- PROD
  - Домен приложения = `casino.example.com`
  - Домен ZITADEL (issuer) = `auth.casino.example.com` (свой instance-домен)
  - ZITADEL-org = `casino-prod`
  - клиентский домен, `master-domain.com` светить нельзя
- STAGE
  - Домен приложения = `stage.casino.example.com`
  - Домен ZITADEL (issuer)= `auth.stage.casino.example.com` (свой instance-домен)
  - ZITADEL-org = `casino-stage`
  - клиентский домен, `master-domain.com` светить нельзя
- DEV
  - Домен приложения = `casino-dev.master-domain.com`
  - Домен ZITADEL (issuer) = `zitadel.master-domain.com` (общий)
  - ZITADEL-org = `casino-dev`
  - dev и так на `master-domain.com` — утечки нет

> общий `zitadel.master-domain.com` — для staff-тулз и dev-стендов клиентских приложений
> Свой `auth.<project-domain>` — для любого стенда, где `iss` не должен светить технический домен (prod, stage)

Стоимость выделенного домена: DNS + cert-manager cert + Ingress-route + регистрация instance-домена (§2.5).

## 8. Чеклист настроек ZITADEL

**Инстанс (Default Settings):**
- [ ] `ExternalDomain=zitadel.master-domain.com`, `ExternalSecure=true`, `TLS.Enabled=false` (§2.2) — зафиксировать до go-live.
- [ ] **Login Name Pattern (`userLoginMustBeDomain`) = ВКЛ** (§6.2).
- [ ] Instance-домены под prod/stage клиентских проектов — через `AddCustomDomain` системным юзером (§2.5).

**Org `Company` (staff):**
- [ ] primary-домен = `master-domain.com` (или `staff.master-domain.com`) → красивый loginName.
- [ ] self-registration ВЫКЛ (аккаунты заводит админ).
- [ ] project `cluster-tools` + Applications argocd/grafana/gitlab (Web+Code). Роли не создаём.

**Org на каждый клиентский стенд (`<project>-<env>`):**
- [ ] primary-домен = клиентский (`casino.example.com`) — не технический.
- [ ] **«Login with Email»** ВКЛ; `userLoginMustBeDomain` ВКЛ (наследуется).
- [ ] login-policy: MFA/passkeys по вкусу; self-registration ВКЛ (если приложение регистрирует).
- [ ] Application: SPA (User Agent+PKCE) + API (introspection); redirect_uri = клиентский домен.
- [ ] Machine user + PAT (узкий Manager-скоуп на СВОЮ org) → в Vault/ESO.

---

## 9. Деплой в k8s-ansible

- **Chart:** официальный `zitadel/zitadel` (`https://charts.zitadel.com`) в `playbook-app/charts/`.
  При установке — Job'ы `zitadel-init` (БД/схема) и `zitadel-setup` (instance/дефолт-org/ключи).
- **БД:** только **PostgreSQL** (CockroachDB удалён, поддержка кончилась 2025-09-30). PG 14–18.
  Реальный SPOF — БД должна быть HA.
- **Masterkey:** ровно **32 байта ASCII**, шифрует секреты at-rest, **неизменяем** после init.
  Через `zitadel.masterkeySecretName` на pre-existing Secret (ESO из Vault). То же для DB-creds.
- **Ingress:** `zitadel.master-domain.com` через Traefik с **h2c** до пода (API = gRPC на `:8080`).
  ⚠️ Забыть h2c → Console грузится, но gRPC (логин/админка) падает. Host должен пройти цепочку неизменным.
- **HA:** `replicaCount ≥ 2` + PDB (stateless).
- **Топология как код:** Terraform-провайдер `zitadel/zitadel` — org/project/app/redirect/grants/Actions
  в git-ops. State держать шифрованным (там client-secret'ы).
- **Cilium:** egress с подов GitLab/Grafana/приложений к ZITADEL (host-firewall CCNP), иначе discovery не резолвится.
- Текущий конфиг: `hosts-vars/zitadel.yaml` (`zitadel_domain`, `zitadel_ui_ingress_config`,
  `zitadel_ui_certificate`), override — `hosts-vars-override/<cluster>/zitadel.yaml`.

---

## 10. Ключевые грабли (recap)

1. **Issuer практически неизменяем** после go-live — пинить `zitadel.master-domain.com` первым делом (§2.2).
2. **Issuer следует за доменом запроса** — этим и достигается не-утечка `master-domain.com` в prod (§2.3). ⚠️ Проверить curl'ом `.well-known` на новом домене.
3. Instance-домен добавляется **только system-level API**, кнопки в Console нет (§2.5).
4. **Login Name Pattern (`userLoginMustBeDomain`) держать ВКЛ** — иначе same-email-в-разных-org ломается (§6.2).
5. **Email не уникален глобально; вход по email неоднозначен без org-контекста** — в BFF скоупить ВСЕ вызовы по orgId (§6.4).
6. **Staff: права раздаём руками**, flatten-Action НЕ нужен; `skip_org_role_sync`(Grafana)/`policy.default:''`(ArgoCD)/`blockAutoCreatedUsers`(GitLab) (§4).
7. `argocd-secret` держать **пустым** — OIDC-секрет в отдельный `part-of: argocd` секрет через ESO (§4.2).
8. **Держать локальные admin/root как break-glass** во всех тулах (§4.5).
9. Redirect-URI матчатся **точно**, без wildcard; Development Mode OFF в prod.
10. За Cloudflare/bastion сохранять **Host** и `X-Forwarded-Proto: https`.
11. **Teleport OIDC = Enterprise**, у нас Community → закрыт (§4.6).

---

## 11. Справочник эндпоинтов / scopes / claims

**OIDC-эндпоинты** (база = домен запроса, напр. `https://zitadel.master-domain.com`):

| Назначение | Путь |
|---|---|
| Discovery | `/.well-known/openid-configuration` |
| Authorization | `/oauth/v2/authorize` |
| Token | `/oauth/v2/token` |
| Userinfo | `/oidc/v1/userinfo` |
| Introspection | `/oauth/v2/introspect` |
| JWKS | `/oauth/v2/keys` |
| End session | `/oidc/v1/end_session` |

**Зарезервированные scopes:**
- `urn:zitadel:iam:org:id:<orgId>` — привязать вход к конкретной org (BFF шлёт это).
- `urn:zitadel:iam:org:project:id:<projectId>:aud` — добавить проект в `aud` (для introspection на API).
- `urn:zitadel:iam:org:project:roles` — включить роли проекта.
- `urn:zitadel:iam:org:domain:primary:<domain>` — привязать к org по домену + брендинг.

**Нативный claim ролей** (вложенный объект, НЕ массив — потому downstream-тулзам нужен flatten-Action):
```json
"urn:zitadel:iam:org:project:roles": { "<roleKey>": { "<orgId>": "<orgPrimaryDomain>" } }
```

---

## 12. Что ещё не сделано / открытые вопросы

- Верифицировать curl'ом на стенде: ZITADEL выдаёт `iss` по вторичному instance-домену
  (`GET https://auth.<...>/.well-known/openid-configuration` → `issuer` = этот домен). Механика
  задокументирована (§2.3), но версия — переменная.
- Уточнить в v2 session API способ передачи org-контекста при резолве пользователя
  (`x-zitadel-orgid` header vs org-id scope в authorize) и закрепить в BFF-клиенте.
- SMTP (верификация email, сброс пароля по ссылке) и Google-OAuth — отложено.
- Отдельный prod-инстанс ZITADEL vs общий (defense-in-depth по management-plane) — по токенам разницы нет,
  общий инстанс покрывает требование не-утечки; отдельный сильнее по blast-radius. Решение открыто.


## 13. ВАЖНО

1. Клик «Войти» в приложении → приложение само строит URL (.../oauth/v2/authorize?...&scope=...org:id:<orgId>).
2. Браузер по этому URL уходит на ZITADEL.
3. Открывается страница ZITADEL — и она уже знает про org, потому что org-id пришёл в URL.
4. Вход происходит там → ZITADEL редиректит обратно в приложение с code.
 
Твоё прежнее представление («открою auth.casino.com и там сразу будет особая страница casino») — это как раз то, чего нет. Страница ZITADEL не существует «сама по себе» под доменом; она появляется только как шаг в flow, который инициировало приложение, и вся «особость» (какая org, какой брендинг) приходит параметрами в том самом URL.

Домен (auth.casino.com) → откуда взять iss
Приложение (через scope в URL) → в какую org логинить. Пользователь просто идёт по ссылке, которую собрал клиент.

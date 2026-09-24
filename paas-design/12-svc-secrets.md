# 12. Услуга «Секреты» (поверх Vault)

> Раздел дизайн-документа PaaS. Связанные: [02-tenancy-and-isolation.md](02-tenancy-and-isolation.md), [03-security-model.md](03-security-model.md), [04-control-plane-go.md](04-control-plane-go.md), [05-data-model.md](05-data-model.md), [06-delivery-pipeline.md](06-delivery-pipeline.md), [07-svc-compute.md](07-svc-compute.md), [09-svc-databases.md](09-svc-databases.md), [15-observability-and-operations.md](15-observability-and-operations.md).

## TL;DR

- **«Vault как сервис» не продаём.** Причины: Vault Namespaces только в Enterprise; `purgeUnmanagedConfig` владельца удалит любые mount'ы и политики, созданные из Go, при следующем рестарте Vault, а отключение KV-mount'а уничтожает данные; тенант получил бы урезанный Vault и прямой доступ к хранилищу всей платформы; BSL-лицензия. Продаём **«Секреты»**: наборы `KEY=value` с версиями, которые прикрепляются к приложению как env или файлы, плюс read-only системные креды (БД, S3, NATS, registry).
- **Раскладка:** один KV v2 `paas-tenants/<ns>/{u|sys}/<id>` (`max_versions=10`, `cas_required`), `transit-paas`, отдельный auth-mount `kubernetes-paas` с **одной** ролью `paas-eso` и **одной** templated-политикой по `service_account_namespace`. `t-A` не прочитает `t-B`: namespace берётся из TokenReview apiserver'а, а не из запроса клиента.
- **Статика и динамика разделены:** mount'ы, политики, роли и audit объявляет ansible через bank-vaults (двухпроходный rollout из-за accessor'а, `{% raw %}`, inline-assert'ы). Данные пишет только `paas-worker` через API. Go никогда не создаёт mount'ы и политики.
- **Поток:** paas-api шифрует значение через Transit (права только на encrypt, чтений ноль) → в Postgres, очереди и git открытого значения нет → worker пишет в Vault с CAS → в git уходит `ExternalSecret` с закреплённой версией, ESO материализует immutable `Secret`.
- **Рестарт при изменении — без Reloader:** новая версия = новое имя `sec-<id>-v<N>` (`refreshPolicy: OnChange`, wave -1, `PruneLast`). Это соответствует D3 («только git»), убирает гонку со старым значением, позволяет откат и не опрашивает Vault.
- **Нагрузка:** `OnChange` + `SecretStore.refreshInterval: 3600` дают ≈ 0.3 логина/с на 1 000 проектов. С настройками, как у системных компонентов сейчас, было бы ~100/с.
- **UX:** значения можно прочитать через step-up MFA (проверяет сам worker по JWT пользователя) и с записью в журнал; есть флаг «только запись»; откат создаёт новую версию; откат приложения секреты не откатывает; у удалённого секрета окно восстановления 7 дней; при удалении проекта KV чистится рекурсивно.
- **Падение Vault не останавливает приложения:** работающие поды, логин и автоскейл от Vault не зависят, блокируются только изменения. До первого платного клиента нужны снапшоты Raft с офсайтом, audit в stdout (⚠️ ограничения API после CVE-2025-6000), TLS на listener и закрытая NP 8200, PVC на multi-sync. Raft ×3 — этап 2.
- **Dynamic DB creds — никогда** для тенантов: Vault стал бы хабом с superuser-доступом во все БД и попал бы на путь данных. **Transit — да:** ключ `paas-inflight` для входящих значений и derived-ключ `paas-cp-fields` для полей control-plane БД. API-токены хранятся хэшем.

## 1. Почему не «Vault как сервис», а «Секреты как фича»

**Вердикт:** тенант никогда не получает Vault-токен, не видит Vault API и не знает, что за фичей стоит Vault. Мы продаём **«Секреты»** — именованные наборы `KEY=value`, которые прикрепляются к приложению как env или как файлы (модель Heroku config vars / Render secret files / Fly secrets). Vault — внутренний backend этой фичи, и только.

### 1.1 Сравнение подходов

| Вариант | Суть | Почему нет / почему да |
|---|---|---|
| A. «Vault как сервис» | тенанту выдаётся токен/роль, он ходит в Vault API сам | **Отвергнут.** Причины ниже (1.2) |
| B. k8s `Secret` напрямую | provisioner пишет `Secret` в `t-*` через API, без Vault | **Отвергнут.** Нет версий и отката; нет аудита чтений; при удалении namespace по ошибке значения теряются безвозвратно (source of truth = сам кластер); платформенные креды (БД, S3, NATS) негде хранить вне кластерных объектов |
| C. SOPS / SealedSecrets в git | зашифрованные значения коммитятся в tenant-репо | **Отвергнут.** Удалённый секрет навсегда остаётся в истории git (удалить по требованию клиента / 152-ФЗ нельзя без переписывания истории); ротация ключа шифрования = перешифровать всё; компрометация ключа = все тенанты. D3 прямо требует «в git — только ссылки» |
| **D. «Секреты» как фича поверх Vault + ESO** | UI → Go → Vault KV v2; ESO материализует `Secret` в `t-*`; в git — только `ExternalSecret`-ссылки | **Выбран (D9).** Версии и откат из KV v2, аудит, данные переживают удаление namespace, владелец уже эксплуатирует Vault+ESO для 15 системных компонентов |

### 1.2 Почему именно не «Vault как сервис»

1. **Vault Namespaces — только Enterprise.** Без них мультиаренда внутри одного Vault OSS — это per-tenant mount'ы + per-tenant политики + per-tenant роли auth. Тенант при этом всё равно не получает ни своих auth-методов, ни своих политик (это `sys/`) — то есть получает урезанный Vault, который хуже, чем просто «переменные окружения».
2. **Тысячи mount'ов.** Таблица mount'ов хранится одной записью storage и упирается в `max_entry_size` Raft (1 MiB по умолчанию) — практический потолок порядка тысяч mount'ов ⚠️ проверить по «Vault limits and maximums» для 1.21; каждый mount — ещё и память роутера. Mount нельзя переименовать/переместить.
3. **bank-vaults несовместим с динамикой (проверено по исходникам, см. memory владельца):**
   - `purgeUnmanagedConfig` у владельца включён для всех секций (`hosts-vars/vault.yaml`, `vault_purge_unmanaged_exclude` — все `false`). Любой mount или политика, созданные worker'ом через API, **будут удалены при следующем `Configure()`** — а он запускается при каждом рестарте/unseal пода Vault. Disable KV-mount'а = **безвозвратное удаление всех его данных**. Динамические per-tenant mount'ы/политики = бомба с часовым механизмом.
   - Гранулярность purge = mount/политика целиком, внутрь mount'а не заходит; исключений per-object нет. Выключить purge для секции = потерять строгий git-ops для системного Vault.
   - userpass-пароли переприменяются при каждом `Configure()` → «пользователи Vault для тенантов» декларативно невозможны.
   - `group-alias` на ещё не смонтированный auth-метод роняет **весь** `Configure()` до применения политик и mount'ов → per-tenant auth-методы превращают каждый рестарт Vault в лотерею.
4. **Поверхность атаки.** Vault API, доступный из tenant-подов или из интернета, — это кредо-хранилище всей платформы (там же `eso-secret/` с кредами GitLab, SeaweedFS, ZITADEL) на расстоянии одного бага. В 2025 у Vault была серия уязвимостей обхода аутентификации и RCE через audit-устройства (CVE-2025-6000 и соседние) ⚠️ проверить точный список. Сейчас tenant-egress CCNP (D4) запрещает поды/сервисы кластера — и Vault остаётся недостижим; «Vault как сервис» эту стену снёс бы.
5. **Шумный сосед.** Один под Vault на single-node Raft (факт: `vault_spec.size: 1`). Тенант с циклом `vault kv get` в CI кладёт секреты всей платформы.
6. **Лицензия.** Vault ≥ 1.15 — BSL 1.1: разрешено использование в production, **кроме** предложения третьим лицам продукта, конкурирующего с платными предложениями HashiCorp/IBM. «Managed Vault» — это прямо конкурирующий хостинг; фича «секреты приложения» внутри PaaS — существенно слабее аргумент против, но не нулевой. ⚠️ Не юридическая консультация — вопрос юристу (см. §16); страховка — OpenBao (форк Vault 1.14 под MPL-2.0, API-совместим), Go-клиент прячем за интерфейс, чтобы замена была дешёвой.
7. **Продукт.** Пользователь PaaS за 500 ₽/мес хочет `DATABASE_URL` в env, а не политики HCL. Prior art (Heroku, Render, Railway, Fly, Vercel) — везде «секреты приложения», нигде не «Vault для клиента».

### 1.3 Что тенант получает

- Секрет = имя (DNS-label, уникально в проекте) + до 100 пар `KEY=value` + до 10 версий.
- Привязка к приложению: все ключи как env; выбранные ключи с переименованием; или как файлы в каталоге.
- Версии, откат, журнал действий, «сохранить и перезапустить привязанные приложения».
- **Системные секреты** (read-only в UI): креды managed-БД, S3-ключи, NATS-creds, pull-секрет Harbor — лежат в том же Vault в зоне `sys/` и прикрепляются к приложению тем же механизмом (как add-on attach у Heroku).

## 2. Поток секрета: от поля в UI до env в поде

### 2.1 Схема

```mermaid
sequenceDiagram
    autonumber
    actor U as Пользователь (браузер)
    participant B as bastion-proxy (DE)<br/>TCP passthrough
    participant T as Traefik (traefik-lb)
    participant A as paas-api
    participant P as Postgres control plane<br/>(+ River)
    participant W as paas-worker
    participant V as Vault<br/>paas-tenants / transit-paas
    participant G as GitLab tenant-репо
    participant R as tenant-ArgoCD
    participant E as ESO controller
    participant K as apiserver / etcd
    participant Pod as Под приложения

    U->>B: HTTPS POST /secrets/{id}/versions (значения)
    B->>T: TLS как есть (send-proxy-v2), расшифровки нет
    T->>A: HTTP (TLS терминирован в Traefik)
    A->>V: transit-paas/encrypt/paas-inflight (context=intent_id)
    V-->>A: vault:vN:… (шифротекст)
    A->>P: TX: INSERT secret_write_intent(шифротекст) + River job(intent_id)
    A-->>U: 202 + intent_id (статус по SSE)
    P-->>W: job(intent_id)
    W->>V: transit-paas/decrypt/paas-inflight
    W->>V: PUT paas-tenants/data/t-xxx/u/<id> (cas=N-1)
    V-->>W: version=N
    W->>P: TX: DELETE intent; INSERT secret_version(N, хэш ключей, автор)
    W->>G: commit: ExternalSecret sec-<id>-vN + env-ссылки в Deployment (без значений)
    G->>R: webhook → refresh → sync
    R->>K: apply ExternalSecret sec-<id>-vN (wave -1)
    K-->>E: watch: новый ExternalSecret
    E->>K: TokenRequest SA paas-eso (aud=vault-paas)
    E->>V: login auth/kubernetes-paas (role paas-eso) → read data/t-xxx/u/<id>?version=N
    E->>K: create Secret sec-<id>-vN (immutable, ownerRef=ES)
    R->>K: apply Deployment (wave 0) → rolling update
    K->>Pod: kubelet: env / tmpfs-файлы из Secret
    A->>K: (read-only) Application.status.sync.revision == SHA && rollout healthy → SSE «применено»
```

Ключевые свойства:
- **Значение ни разу не лежит в открытом виде ни в Postgres, ни в очереди, ни в git.** В River-job — только `intent_id`; шифротекст — в отдельной таблице и удаляется после записи в Vault; остатки в WAL/бэкапах «криптошредятся» ротацией+trim ключа `paas-inflight` (§15).
- **paas-api не читает Vault вообще** (у его политики есть только `encrypt` на `paas-inflight`). RCE в интернет-facing процессе не даёт выгрузить существующие секреты.
- Каждая смена значения = новая версия KV = **новое имя** `ExternalSecret`/`Secret` (`sec-<id>-v<N>`). Почему так, а не Reloader — §8.

### 2.2 Кто что видит на каждом шаге

| # | Компонент | Видит значение? | В каком виде / где | Сколько живёт |
|---|---|---|---|---|
| 1 | Браузер | да | память вкладки; поле формы очищается после 202; **никогда** localStorage | до закрытия формы |
| 2 | bastion-proxy (Германия) | **нет** | TLS-поток, L4 passthrough, TLS не терминирует | — |
| 3 | Traefik | да | память процесса при терминации TLS; далее **открытый HTTP до paas-api** (Cilium без шифрования — факт `k8s-base.yaml`: «no IPsec encryption») ⚠️ см. §12.5 | миллисекунды |
| 4 | paas-api | да | память; тело запроса исключено из логов (allow-list полей в логгере) | миллисекунды |
| 5 | Postgres control plane | **нет** | шифротекст `vault:vN:…` в `secret_write_intent`, удаляется после записи; в `secret_version` — только имена ключей и HMAC | до записи в Vault; в бэкапах — до trim ключа (≤ 2 сут) |
| 6 | River (очередь) | **нет** | только `intent_id` | retention завершённых job |
| 7 | paas-worker | да | память на время записи/чтения | миллисекунды |
| 8 | Vault storage (Raft) | нет (зашифровано) | barrier AES-256-GCM; ключ под Shamir 3/2 | до удаления + срок хранения снапшотов |
| 9 | Vault audit | **нет** | HMAC-SHA256 значений (по умолчанию `log_raw=false`) | retention Loki (§13) |
| 10 | GitLab / tenant-репо | **нет** | путь в Vault, номер версии, имена ключей (если маппинг) | вечно (история git) — поэтому значения туда нельзя |
| 11 | tenant-ArgoCD | **нет** | то же, что в git; **в его Role нет ресурса `secrets` вообще** (§5.5) | — |
| 12 | ESO controller | да | память при материализации | миллисекунды |
| 13 | etcd | нет (зашифровано) | `Secret` под `aescbc` (encryption at rest, `cluster-init.yaml`) | пока жив `Secret` |
| 14 | kube-apiserver | да, по запросу | отдаёт тем, у кого `get secrets` в `t-*`: ESO, kubelet (node authorizer — только своим подам), cluster-admin | — |
| 15 | Нода (kubelet, root) | да | env процесса (`/proc/<pid>/environ`), файлы — `tmpfs`, не диск | время жизни пода |
| 16 | Приложение тенанта | да | env / файл | время жизни пода |
| 17 | Оператор платформы (staff) | **может** | root-токен / `vault-admin`, cluster-admin | — |

Строка 17 — честная: оператор PaaS технически всегда может прочитать секреты клиента (как у любого облака). Митигация — не «невозможно», а «аудируемо»: вход staff в Vault только через OIDC (ZITADEL), audit device (§13), break-glass root-токен офлайн. Формулировка в оферте — [16-legal-ru.md](16-legal-ru.md).

## 3. Раскладка Vault и зоны ответственности

### 3.1 Mount'ы и auth-методы

| Объект Vault | Тип | Кто объявляет | Кто пишет данные | Кто читает |
|---|---|---|---|---|
| `secret/` | kv-v2 | ansible (есть) | люди | люди |
| `eso-secret/` | kv-v2 | ansible (есть) | ansible, люди | 15 системных ESO-ролей |
| `eso-secret/paas/*` | путь в существующем mount'е | ansible | ansible / оператор | ESO в `paas-system`: токены интеграций paas-worker (GitLab, Harbor, SeaweedFS, Cloudflare, платёжка) |
| **`paas-tenants/`** | kv-v2, **новый** | ansible (bank-vaults) | **только paas-worker** | ESO через роль `paas-eso` (templated), paas-worker |
| **`transit-paas/`** | transit, **новый** | ansible (bank-vaults) | — (ключи) | paas-worker (encrypt/decrypt), paas-api (только encrypt `paas-inflight`) |
| `auth/kubernetes` | k8s auth (есть) | ansible | — | системные роли + новые `paas-worker`, `paas-api`, `vault-snapshot` |
| **`auth/kubernetes-paas`** | k8s auth, **новый** | ansible | — | **ровно одна роль** `paas-eso` |

**Почему отдельный auth-mount для тенантов.** Templated-политика привязана к accessor'у mount'а (§4.2): на отдельном mount'е в нём живёт только тенантская идентичность, аудит сразу показывает, чей это логин, а ошибка в тенантской роли физически не может задеть 15 системных ролей на `auth/kubernetes`. Цена — ещё один mount в конфиге.

### 3.2 Раскладка путей

```
paas-tenants/                           # KV v2, max_versions=10, cas_required=true
  data/t-<project_id>/
      u/<secret_id>                     # пользовательские секреты (UI)
      sys/pg-<db_id>                    # креды managed Postgres   → 09-svc-databases
      sys/s3-<bucket_id>                # ключи S3-identity        → 11-svc-object-storage-s3
      sys/nats-<account_id>             # NATS user creds          → 09-svc-databases
      sys/registry-pull                 # robot-аккаунт Harbor     → 10-svc-registry-harbor
```

Правила:
- **Первый сегмент = имя namespace** (`t-` + 10 символов `[a-z0-9]`, D2). Именно его подставляет templated-политика — отсюда вся изоляция.
- `<secret_id>` — 10 случайных `[a-z0-9]`, **никогда не имя от пользователя**. Имя живёт в control-plane БД. Нет инъекций в путь, нет утечки имён через аудит, переименование = одна строка SQL.
- **Никаких ведущих/двойных/хвостовых `/`.** Ловушка владельца: `/ns/x` в KV v2 рассинхронизирует `data` и `metadata` → `could not find version data`. Путь строит одна Go-функция с валидацией (§6.3).
- `u/` пишет пользователь через UI; `sys/` пишет только платформа, в UI — read-only.

### 3.3 Конфиг KV-mount'а

| Параметр | Значение | Зачем |
|---|---|---|
| `max_versions` | 10 | глубина отката; хранилище ограничено (10 × 256 KiB × секреты) |
| `cas_required` | `true` | каждая запись обязана нести `cas=<ожидаемая версия>` → два окна браузера не затрут друг друга молча |
| `delete_version_after` | 0 (выкл.) | версии не протухают сами; удаление — только явное (§10) |

### 3.4 Зоны ответственности: bank-vaults (статика) vs paas-worker (данные)

| Что | Где объявлено | Кто применяет | Когда меняется |
|---|---|---|---|
| mount'ы `paas-tenants`, `transit-paas`; auth-mount `kubernetes-paas` | `hosts-vars/vault.yaml` (+ override) | bank-vaults configurer | прогон `vault-install.yaml`; **никогда** из Go |
| политики `paas-tenant-eso`, `paas-worker`, `paas-api`, `vault-snapshot` | там же | bank-vaults | ansible |
| роли `paas-eso` (на `kubernetes-paas`), `paas-worker`, `paas-api`, `vault-snapshot` (на `kubernetes`) | там же | bank-vaults | ansible |
| transit-ключи `paas-cp-fields`, `paas-inflight` | там же ⚠️ (см. ниже) | bank-vaults | ansible |
| audit device | там же | bank-vaults | ansible |
| **данные** `paas-tenants/data/**` | — | **paas-worker через Vault API** | каждое действие пользователя |

Инвариант, вытекающий из purge: **Go-код никогда не создаёт mount'ы, политики, роли, auth-методы.** Всё, что создано в Vault мимо bank-vaults-конфига, будет вычищено при следующем рестарте пода Vault. Worker'у эти права и не выданы (§6).

### 3.5 Как это добавить в `hosts-vars/vault.yaml`

Сейчас `vault_spec_secrets` — плоский список, а `vault_spec_auth` уже собирается из кусков. Предлагаемая правка (база держит полную структуру, выключено; per-cluster значения — в override):

```yaml
# hosts-vars/vault.yaml — БАЗА
    # === SECTION: PaaS tenants (выключено по умолчанию) ===
    vault_paas_enabled: false
    vault_paas_kv_mount: "paas-tenants"
    vault_paas_transit_mount: "transit-paas"
    vault_paas_k8s_auth_mount: "kubernetes-paas"
    vault_paas_eso_audience: "vault-paas"
    # accessor mount'а kubernetes-paas. Per-cluster; известен только ПОСЛЕ первого прогона
    # (vault auth list -format=json | jq -r '."kubernetes-paas/".accessor').
    # Пусто → политика paas-tenant-eso НЕ рендерится (двухпроходный rollout, см. ниже).
    vault_paas_k8s_auth_accessor: ""
    vault_spec_secrets_paas:
      - path: "{{ vault_paas_kv_mount }}"
        type: kv
        description: "PaaS tenant secrets — data written ONLY by paas-worker via API"
        options:
          version: 2
        configuration:              # ⚠️ проверить: применяет ли bank-vaults configuration.config для kv-v2
          config:
            - max_versions: 10
              cas_required: true
      - path: "{{ vault_paas_transit_mount }}"
        type: transit
        description: "PaaS control-plane field encryption"
        configuration:              # ⚠️ проверить формат keys для transit в bank-vaults v1.33
          keys:
            - name: paas-cp-fields
              type: aes256-gcm96
              derived: true
              exportable: false
            - name: paas-inflight
              type: aes256-gcm96
              auto_rotate_period: 24h
    vault_spec_auth_paas:
      type: kubernetes
      path: "{{ vault_paas_k8s_auth_mount }}"
      config:
        kubernetes_host: "{{ vault_kubernetes_host }}"
      roles:
        - name: paas-eso
          bound_service_account_names: ["paas-eso"]
          bound_service_account_namespace_selector: '{"matchLabels":{"paas.1520.tech/tenant":"true"}}'
          audience: "{{ vault_paas_eso_audience }}"
          alias_name_source: serviceaccount_uid
          token_policies: ["paas-tenant-eso"]
          token_no_default_policy: true
          token_ttl: 10m
          token_max_ttl: 10m
    # Templated-политика. {% raw %} обязателен: {{identity.*}} — шаблон ДЛЯ VAULT, не для Jinja.
    # accessor вставляется Jinja между raw-блоками.
    vault_policy_paas_tenant_eso:
      name: paas-tenant-eso
      rules: |
        path "{{ vault_paas_kv_mount }}/data/{% raw %}{{identity.entity.aliases.{% endraw %}{{ vault_paas_k8s_auth_accessor }}{% raw %}.metadata.service_account_namespace}}{% endraw %}/*" {
          capabilities = ["read"]
        }
        path "auth/token/lookup-self" { capabilities = ["read"] }
        path "auth/token/revoke-self" { capabilities = ["update"] }
    vault_policies_paas: >-
      {{ ([vault_policy_paas_tenant_eso] if (vault_paas_k8s_auth_accessor | length > 0) else [])
         + [vault_policy_paas_worker, vault_policy_paas_api, vault_policy_vault_snapshot] }}

    # композиция (правка существующих ключей)
    vault_spec_secrets: >-
      {{ vault_spec_secrets_base + (vault_spec_secrets_paas if vault_paas_enabled | bool else []) }}
    vault_spec_auth: >-
      {{ [vault_spec_auth_kubernetes]
         + ([vault_spec_auth_paas] if vault_paas_enabled | bool else [])
         + ... }}   # остальное без изменений
```

Порядок раската (**двухпроходный** — accessor существует только после создания mount'а):
1. Override: `vault_paas_enabled: true`, accessor пуст → прогон `vault-install.yaml --tags install`: создаются mount'ы, auth-mount, роли, политики worker/api/snapshot; `paas-tenant-eso` пропущена.
2. `vault auth list -format=json | jq -r '."kubernetes-paas/".accessor'` → в override `vault_paas_k8s_auth_accessor`.
3. Проверка рендера **до** прогона — однострочник владельца из memory (jinja2 из python Ansible): вывод обязан содержать литерал `{{identity.entity.aliases.auth_kubernetes_…metadata.service_account_namespace}}`.
4. Второй прогон → политика применена. `vault policy read paas-tenant-eso` — глазами.
5. Inline-assert в `vault-install.yaml` (post, по стилю владельца — inline, не reusable): при `vault_paas_enabled` live-accessor `kubernetes-paas/` == `vault_paas_k8s_auth_accessor`, иначе **fail**. Accessor меняется при пересоздании mount'а (re-init Vault) — без assert'а ESO всех тенантов тихо получит 403.

**Главная опасность — purge.** Если `paas-tenants` или `transit-paas` исчезнут из итогового конфига (опечатка, `vault_paas_enabled: false`, неверная композиция), bank-vaults **отключит mount → все секреты тенантов / все transit-ключи уничтожены**. Защита в три слоя:
1. Рекомендация (решение владельца, §16): при включении PaaS ставить `vault_purge_unmanaged_exclude.secrets: true` — удаление mount'а из конфига перестаёт его отключать (выключение — только руками `vault secrets disable`). Это меняет сознательно выбранный владельцем строгий режим, поэтому не делаю молча.
2. Inline-assert: `vault_paas_enabled` ⇒ оба пути присутствуют в итоговом `vault_spec_secrets`.
3. Raft-снапшот **перед каждым** прогоном `vault-install.yaml` (pre-task, §12.4).

## 4. Kubernetes-auth: одна role + templated policy

### 4.1 Роль

Одна роль на весь PaaS (YAML — в §3.5). Ключевые поля:

| Поле | Значение | Смысл |
|---|---|---|
| `bound_service_account_names` | `paas-eso` | логиниться может только SA с этим именем |
| `bound_service_account_namespace_selector` | `matchLabels: paas.1520.tech/tenant=true` | …и только из namespace с тенантским label ⚠️ проверить |
| `audience` | `vault-paas` | токен, выпущенный для другой аудитории (apiserver, другой Vault-роли), сюда не подходит, и наоборот |
| `alias_name_source` | `serviceaccount_uid` (дефолт) | пересозданный ns с тем же именем получит **новую** entity (нам не грозит — id случайны) |
| `token_policies` | `paas-tenant-eso` | единственная политика |
| `token_no_default_policy` | `true` | без `default` (cubbyhole, wrapping и пр.); нужные ESO `lookup-self`/`revoke-self` добавлены явно |
| `token_ttl` / `token_max_ttl` | 10m | токенов в сторе мало; ESO всё равно отзывает токен после использования (`revokeTokenIfValid` в `providers/v1/vault/auth.go`) |

Тип токена — **service** (дефолт). Batch-токены не пишутся в Raft и выглядели бы привлекательно, но ESO после работы вызывает `lookup-self` + `revoke-self`, а batch-токен отозвать нельзя → ошибка в каждом цикле ⚠️ проверить; экономия не стоит риска при нашей нагрузке (§11).

### 4.2 Политика `paas-tenant-eso` (после рендера Jinja)

```hcl
# Единственная политика тенантского ESO. Шаблон вычисляет VAULT при каждом запросе.
path "paas-tenants/data/{{identity.entity.aliases.auth_kubernetes_1a2b3c4d.metadata.service_account_namespace}}/*" {
  capabilities = ["read"]
}
# нужно ESO: checkToken() → lookup-self, Close() → revoke-self
path "auth/token/lookup-self" { capabilities = ["read"] }
path "auth/token/revoke-self" { capabilities = ["update"] }
```

Чего в ней **нет** и почему:
- `list` на `metadata/` — ESO читает по точному ключу; без `list` тенант (даже получив токен) не перечислит собственные пути, а `dataFrom.find` не работает в принципе.
- `metadata/*` read — версии/авторы ESO не нужны.
- `create/update/delete` — ESO только читает; `PushSecret` из tenant-ns в Vault упрётся в 403 (плюс он запрещён RBAC, §5.5).

### 4.3 Почему `t-A` физически не прочитает `t-B`

Цепочка доверия:
1. ESO запрашивает у apiserver `TokenRequest` для SA `paas-eso` в `t-A` (namespaced `SecretStore` не может сослаться на SA в другом namespace — ESO это валидирует; `ClusterSecretStore` для тенантов запрещён, §5.5).
2. JWT подписан ключом apiserver; claim `kubernetes.io.namespace = t-A` **нельзя подделать** без ключа подписи SA-токенов.
3. Vault делает `TokenReview` (права `system:auth-delegator` у SA `vault` уже есть — `charts/vault/pre/templates/rbac.yaml`) и получает namespace **из ответа apiserver**, а не из запроса клиента. Это значение попадает в `alias.metadata.service_account_namespace`.
4. При каждом запросе Vault подставляет его в путь политики → `paas-tenants/data/t-A/*`. Запрос `paas-tenants/data/t-B/…` не совпадает ни с одним правилом → **403** (Vault default-deny).

Граничные случаи:
- **Префиксная коллизия** невозможна: в правиле `…/{{ns}}/*` слэш стоит сразу после namespace, поэтому `t-A/*` не матчит `t-AB/…`. Никогда не писать `…/{{ns}}*` (без слэша) — вот это была бы дыра.
- **Метаданные не разрешились** (логин через другой mount, пустой alias): Vault отбрасывает правило целиком, а не превращает его в `data//*` → доступа нет.
- **Кто может получить токен `paas-eso` в `t-B`?** Только тот, кто может создать `TokenRequest` или Pod с этим SA в `t-B`. У tenant-ArgoCD нет `serviceaccounts/token`; Pod с `serviceAccountName: paas-eso` запрещает VAP (правило в [03-security-model.md](03-security-model.md)); у тенантских подов `automountServiceAccountToken: false`, а egress в pod/service CIDR (т.е. к Vault) закрыт CCNP (D4). Остаётся ESO-контроллер: он умеет выпустить токен любого SA — **компрометация ESO = компрометация всех секретов** (так же, как компрометация apiserver). Это принятый доверенный компонент; держим его версию свежей и в системном namespace.
- Даже если бы кто-то получил токен `paas-eso` своего namespace — он прочитает только **свои** секреты. Кросс-тенантного эффекта нет.

### 4.4 Селектор namespace: ⚠️ проверить, и почему безопасность от него не зависит

`bound_service_account_namespace_selector` появился в плагине kubernetes-auth несколько релизов назад (по памяти — Vault 1.15) ⚠️ проверить на 1.21.4. Для него Vault должен **читать объекты Namespace**, а у SA `vault` сейчас только `system:auth-delegator`. Нужен дополнительный объект в `charts/vault/pre` (уникальное имя — инвариант §0):

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: vault-paas-namespace-reader
rules:
  - apiGroups: [""]
    resources: ["namespaces"]
    verbs: ["get"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: vault-paas-namespace-reader
roleRef: {apiGroup: rbac.authorization.k8s.io, kind: ClusterRole, name: vault-paas-namespace-reader}
subjects:
  - {kind: ServiceAccount, name: vault, namespace: vault}
```

Label `paas.1520.tech/tenant=true` на Namespace может поставить только `paas-provisioner` (VAP из D4 ограничивает имена ns и обязательные labels) и cluster-admin.

**Fallback**, если селектор не работает: `bound_service_account_namespaces: ["*"]`. Это **не дыра**: SA `paas-eso` в системном namespace залогинится, но прочитает `paas-tenants/data/<system-ns>/*`, где ничего нет. Изоляция держится на templated-пути; селектор — лишь сужение поверхности (и чистый аудит).

### 4.5 Audience

В `SecretStore` — `serviceAccountRef.audiences: ["vault-paas"]`, в роли — `audience: vault-paas`. По памяти, свежие версии Vault предупреждают о ролях без `audience` и собираются сделать его обязательным ⚠️ проверить в release notes 1.21. Системным ролям владельца (`<ns>.eso-main`) стоит добавить audience отдельной задачей — вне скоупа PaaS.

## 5. ESO в tenant-namespace: SecretStore и ExternalSecret

Версия API: в ESO 2.5.0 `SecretStore` и `ExternalSecret` — `external-secrets.io/v1` (так уже рендерит `charts/vault/pre/templates/eso-secret-store.yaml` в репо; `reference/secrets-and-eso.md` §5.2 с `v1beta1` устарел). ⚠️ проверить `kubectl get crd secretstores.external-secrets.io -o jsonpath='{.spec.versions[*].name}'` — `v1beta1` не использовать.

### 5.1 Что создаёт `paas-provisioner` напрямую (не git) при создании проекта

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: paas-eso
  namespace: t-7f3k2m9q1x
  labels:
    paas.1520.tech/managed-by: paas
    paas.1520.tech/tenant-ns: t-7f3k2m9q1x
automountServiceAccountToken: false      # токен выпускает только ESO через TokenRequest
---
apiVersion: external-secrets.io/v1
kind: SecretStore
metadata:
  name: paas-vault
  namespace: t-7f3k2m9q1x
  labels:
    paas.1520.tech/managed-by: paas
    paas.1520.tech/tenant-ns: t-7f3k2m9q1x
spec:
  refreshInterval: 3600          # сек; перевалидация стора раз в час, а не раз в минуту (§11)
  provider:
    vault:
      server: "http://vault.vault.svc.cluster.local:8200"   # → https после §12.5
      path: "paas-tenants"
      version: v2
      auth:
        kubernetes:
          mountPath: "kubernetes-paas"
          role: "paas-eso"
          serviceAccountRef:
            name: paas-eso
            audiences: ["vault-paas"]
```

Почему `SecretStore` делает provisioner, а не git: это объект, определяющий **куда и под какой ролью** ходит ESO. Если бы он жил в tenant-репо, баг рендера или скомпрометированный tenant-конвейер мог бы подменить `role`/`mountPath`/`server` (например, на внешний «Vault» злоумышленника — утечка через `ExternalSecret`, который ESO послушно пошлёт туда). SSA с `fieldManager: paas-provisioner`, VAP (§5.5) фиксирует все поля.

### 5.2 `ExternalSecret`, генерируемый backend'ом (коммитится в git)

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: sec-9x2kq7m4ab-v7                 # sec-<secret_id>-v<version>: новое имя на каждую версию
  namespace: t-7f3k2m9q1x
  labels:
    paas.1520.tech/managed-by: paas
    paas.1520.tech/tenant-ns: t-7f3k2m9q1x
    paas.1520.tech/secret-id: 9x2kq7m4ab
    paas.1520.tech/secret-version: "7"
  annotations:
    argocd.argoproj.io/sync-wave: "-1"    # раньше Deployment'а
spec:
  refreshPolicy: OnChange                 # версия закреплена → опрашивать Vault незачем
  secretStoreRef:
    kind: SecretStore
    name: paas-vault
  target:
    name: sec-9x2kq7m4ab-v7
    creationPolicy: Owner                 # ownerRef → Secret удалится вместе с ES (prune)
    deletionPolicy: Retain                # пропала версия в Vault → Secret в кластере не трогаем
    immutable: true                       # содержимое версии не меняется никогда
  dataFrom:
    - extract:
        key: t-7f3k2m9q1x/u/9x2kq7m4ab    # БЕЗ ведущего слэша
        version: "7"                      # KV v2 ?version=7 — ⚠️ проверить на стенде (§17)
```

| Поле | Почему именно так |
|---|---|
| `refreshPolicy: OnChange` | ESO идёт в Vault только когда меняется сам `ExternalSecret` (`shouldRefresh()` в `externalsecret_controller.go`) или когда `Secret` пропал/испорчен (`isSecretValid`). Нагрузка на Vault в покое — ноль |
| `version` закреплена | значение «сорвиголовой» не переедет в работающий под между деплоями; git = точное описание того, что крутится |
| `immutable: true` | kubelet не держит watch на immutable Secret'ы → меньше watch на apiserver при тысячах подов (штатная оптимизация k8s) |
| имя с `-v<N>` | новая версия = новый объект: нет гонки «под стартовал раньше, чем ESO обновил Secret» (§8) |
| только `dataFrom.extract` | без `find` (нужен `list`, которого нет), без `rewrite`, без generator'ов, без `target.template` |

### 5.3 Как приложение ссылается (фрагмент Deployment, тоже из git)

```yaml
spec:
  template:
    spec:
      securityContext:
        fsGroup: 10000                  # файлы секрета читаемы группой приложения
      containers:
        - name: app
          envFrom:                      # режим «все ключи как env»
            - secretRef: {name: sec-9x2kq7m4ab-v7}
          env:                          # режим «выбранный ключ под другим именем»
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef: {name: sys-pg-a1b2c3d4e5-v3, key: uri}
          volumeMounts:                 # режим «файлы»
            - {name: sec-tls, mountPath: /etc/paas/secrets/tls, readOnly: true}
      volumes:
        - name: sec-tls
          secret: {secretName: sec-4k2m8q1z0c-v2, defaultMode: 0440}
```

Файлы монтируются только под `/etc/paas/secrets/<name>` (совместимо с `readOnlyRootFilesystem`, проверяется VAP). Secret-volume — `tmpfs`, на диск ноды не пишется.

### 5.4 Рендер в Go и ловушка `{{ }}`

Ловушка владельца (memory): Helm съедает ESO-выражения `{{ .x }}` в `target.template` → Secret навсегда nil. У нас **нет шаблонизатора в конвейере** (D13: типы Go + `sigs.k8s.io/yaml`, никакого `text/template`; tenant-Application — plain directory source, не Helm) — значит, класс ошибки невозможен по построению, пока держатся два правила:
1. Tenant-`Application` **никогда** не переводится на Helm/kustomize-источник (Helm снова начнёт исполнять `{{ }}`).
2. Там, где ESO-шаблон реально нужен (например, `dockerconfigjson` для pull-секрета — это делает provisioner), строка `{{ .password }}` — литерал в Go-структуре, а golden-тест проверяет, что он дожил до YAML байт-в-байт.

```go
import (
    esv1 "github.com/external-secrets/external-secrets/apis/externalsecrets/v1" // отдельный Go-модуль .../apis — без тяжёлых зависимостей контроллера
    metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

func RenderExternalSecret(ref SecretRef, version int) (*esv1.ExternalSecret, error) {
    key, err := ref.VaultKey() // "t-xxxxxxxxxx/u/yyyyyyyyyy", валидирован
    if err != nil {
        return nil, err
    }
    name := fmt.Sprintf("%s-%s-v%d", ref.Prefix(), ref.ID, version) // sec-/sys-...
    return &esv1.ExternalSecret{
        TypeMeta:   metav1.TypeMeta{APIVersion: "external-secrets.io/v1", Kind: "ExternalSecret"},
        ObjectMeta: metav1.ObjectMeta{
            Name: name, Namespace: ref.Namespace,
            Labels:      ref.Labels(version),
            Annotations: map[string]string{"argocd.argoproj.io/sync-wave": "-1"},
        },
        Spec: esv1.ExternalSecretSpec{
            RefreshPolicy:  esv1.RefreshPolicyOnChange,
            SecretStoreRef: esv1.SecretStoreRef{Kind: "SecretStore", Name: "paas-vault"},
            Target: esv1.ExternalSecretTarget{
                Name: name, CreationPolicy: esv1.CreatePolicyOwner,
                DeletionPolicy: esv1.DeletionPolicyRetain, Immutable: true,
            },
            DataFrom: []esv1.ExternalSecretDataFromRemoteRef{{
                Extract: &esv1.ExternalSecretDataRemoteRef{Key: key, Version: strconv.Itoa(version)},
            }},
        },
    }, nil
}
```

Если модуль `.../apis` потянет лишнее — fallback: собственные минимальные структуры + валидация golden-манифестов `kubeconform` против CRD-схемы ESO (kubeconform уже есть в `make test` репо).

### 5.5 Ограждения: RBAC + VAP

**RBAC tenant-ArgoCD в `t-*`** (Role, которую provisioner привязывает RoleBinding'ом, D3):

| Ресурс | Права | Комментарий |
|---|---|---|
| `external-secrets.io/externalsecrets` | CRUD | единственный ESO-объект, который едет из git |
| `external-secrets.io/secretstores`, `clustersecretstores`, `pushsecrets`, `clusterexternalsecrets`, `generators.external-secrets.io/*` | **нет** | стор — только provisioner; push/generators тенантам не нужны (generator `Webhook` = SSRF из системного ns) |
| `core/secrets` | **нет вообще** | tenant-ArgoCD не создаёт и **не читает** Secret'ы; pull-секрет создаёт provisioner. Плюс в `argocd-cm` tenant-инстанса `resource.exclusions` для `Secret` — контроллер не держит их в кэше |
| `serviceaccounts/token` | нет | не может выпустить токен `paas-eso` |

**VAP для ESO-объектов в тенантских namespace** (дополняет общие политики из [03-security-model.md](03-security-model.md)):

```yaml
apiVersion: admissionregistration.k8s.io/v1
kind: ValidatingAdmissionPolicy
metadata:
  name: paas-tenant-externalsecret
spec:
  failurePolicy: Fail
  matchConstraints:
    namespaceSelector:
      matchLabels: {paas.1520.tech/tenant: "true"}
    resourceRules:
      - apiGroups: ["external-secrets.io"]
        apiVersions: ["*"]
        operations: ["CREATE", "UPDATE"]
        resources: ["externalsecrets"]
  variables:
    - name: isProvisioner
      expression: "request.userInfo.username == 'system:serviceaccount:paas-system:paas-provisioner'"
  validations:
    - expression: "object.spec.secretStoreRef.kind == 'SecretStore' && object.spec.secretStoreRef.name == 'paas-vault'"
      message: "tenant ExternalSecret may reference only SecretStore/paas-vault"
    - expression: "!has(object.spec.dataFrom) || object.spec.dataFrom.all(d, has(d.extract) && !has(d.find) && !has(d.rewrite) && !has(d.sourceRef))"
      message: "only dataFrom.extract is allowed"
    - expression: "!has(object.spec.dataFrom) || object.spec.dataFrom.all(d, d.extract.key.startsWith(namespaceObject.metadata.name + '/'))"
      message: "key must start with '<namespace>/'"
    - expression: "!has(object.spec.data) || object.spec.data.all(d, !has(d.sourceRef) && d.remoteRef.key.startsWith(namespaceObject.metadata.name + '/'))"
      message: "data[].remoteRef only, within own namespace path"
    - expression: "has(object.spec.target.immutable) && object.spec.target.immutable == true"
    - expression: "object.spec.refreshPolicy == 'OnChange'"
    - expression: "!has(object.spec.target.template) || variables.isProvisioner"
      message: "target.template is reserved for paas-provisioner"
---
apiVersion: admissionregistration.k8s.io/v1
kind: ValidatingAdmissionPolicy
metadata:
  name: paas-tenant-secretstore
spec:
  failurePolicy: Fail
  matchConstraints:
    namespaceSelector:
      matchLabels: {paas.1520.tech/tenant: "true"}
    resourceRules:
      - apiGroups: ["external-secrets.io"]
        apiVersions: ["*"]
        operations: ["CREATE", "UPDATE"]
        resources: ["secretstores"]
  validations:
    - expression: "request.userInfo.username == 'system:serviceaccount:paas-system:paas-provisioner'"
      message: "SecretStore in tenant namespaces is managed by paas-provisioner only"
    - expression: >-
        object.metadata.name == 'paas-vault' &&
        object.spec.provider.vault.path == 'paas-tenants' &&
        object.spec.provider.vault.auth.kubernetes.mountPath == 'kubernetes-paas' &&
        object.spec.provider.vault.auth.kubernetes.role == 'paas-eso' &&
        object.spec.provider.vault.auth.kubernetes.serviceAccountRef.name == 'paas-eso'
      message: "SecretStore fields are fixed by the platform"
```

Проверка префикса ключа избыточна (Vault всё равно отдаст 403), но стоит ноль и превращает баг рендера в понятную ошибку admission вместо «вечного SecretSyncedError». Биндинги обеих политик (`ValidatingAdmissionPolicyBinding`, `validationActions: [Deny]`) — в ansible рядом с остальными VAP.

### 5.6 Стык с tenant-ArgoCD

- **Материализованный `Secret` ArgoCD не присваивает.** В ArgoCD v3 трекинг по умолчанию — аннотация `argocd.argoproj.io/tracking-id`, в которой зашиты group/kind/name объекта; даже если ESO скопирует аннотации `ExternalSecret` на `Secret`, tracking-id не совпадёт с самим `Secret` → ArgoCD не считает его своим и не прунит. ⚠️ проверить на стенде (`argocd app resources` не должен показывать `Secret`).
- **Health `ExternalSecret`**: встроенная lua-проверка ArgoCD смотрит condition `Ready` ⚠️ проверить в v3.5.1. Для нового объекта (новое имя!) устаревшего `Ready=True` не бывает — wave -1 честно ждёт материализации.
- **`PruneLast=true`** в `syncOptions` Application: старый `ExternalSecret` (`-v6`) удаляется только после того, как новый Deployment стал Healthy, — поды старого ReplicaSet до конца rollout'а не теряют свой Secret.

## 6. Права paas-worker в Vault

### 6.1 Идентичность worker'а в Vault

- SA `paas-worker` в `paas-system`; projected-токен с `audience: vault` (не дефолтный SA-токен apiserver'а).
- Роль `paas-worker` на **системном** `auth/kubernetes` (платформенная идентичность, не тенантская; объявляется в секции PaaS базового `hosts-vars/vault.yaml` и подмешивается в роли при `vault_paas_enabled`), `token_ttl: 1h`, продление через `api.LifetimeWatcher`.
- Интеграционные токены worker'а (GitLab, Harbor, SeaweedFS-admin, Cloudflare, платёжка) — **не** в `paas-tenants`: они в `eso-secret/paas/*`, заводятся ansible, в под `paas-worker` приходят через ESO как у всех системных компонентов (`reference/secrets-and-eso.md` §7). Worker не может прочитать их из Vault сам — политика не даёт.

### 6.2 Политики (HCL, декларируются в bank-vaults, §3.5)

```hcl
# ---- paas-worker: данные тенантов + transit. Ничего больше. ----
path "paas-tenants/data/*"      { capabilities = ["create", "update", "read"] }   # запись версий (cas), чтение для reveal/rollback/diff
path "paas-tenants/subkeys/*"   { capabilities = ["read"] }                       # имена ключей БЕЗ значений — для списков в UI
path "paas-tenants/metadata/*"  { capabilities = ["read", "list", "delete"] }     # версии/авторы; list — для рекурсивного удаления; delete = hard delete всех версий
path "paas-tenants/destroy/*"   { capabilities = ["update"] }                     # безвозвратно уничтожить конкретные версии

path "transit-paas/encrypt/paas-cp-fields"   { capabilities = ["update"] }
path "transit-paas/decrypt/paas-cp-fields"   { capabilities = ["update"] }
path "transit-paas/rewrap/paas-cp-fields"    { capabilities = ["update"] }
path "transit-paas/decrypt/paas-inflight"    { capabilities = ["update"] }
path "transit-paas/keys/paas-inflight"       { capabilities = ["read"] }          # узнать latest_version для trim
path "transit-paas/keys/paas-inflight/trim"  { capabilities = ["update"] }        # криптошреддинг старых версий ключа

path "auth/token/lookup-self" { capabilities = ["read"] }
path "auth/token/renew-self"  { capabilities = ["update"] }

# ---- paas-api: только зашифровать входящее значение. Ни одного чтения. ----
path "transit-paas/encrypt/paas-inflight" { capabilities = ["update"] }
path "auth/token/lookup-self" { capabilities = ["read"] }
path "auth/token/renew-self"  { capabilities = ["update"] }

# ---- vault-snapshot: только снимок Raft (§12.4) ----
path "sys/storage/raft/snapshot" { capabilities = ["read"] }
```

Чего у worker'а **нет** (и почему это важно):

| Недоступно | Что это предотвращает при компрометации worker'а |
|---|---|
| `eso-secret/*`, `secret/*` | чтение кредов GitLab/ZITADEL/SeaweedFS и прочих системных компонентов |
| `sys/*` (policy, mounts, audit, auth) | создание себе новых прав, отключение аудита, «тихий» mount |
| `auth/*` (кроме своего токена) | создание ролей, привязка чужих SA |
| `identity/*` | подмена/удаление entity (в т.ч. staff OIDC) |
| `paas-tenants/config` | снижение `max_versions`, отключение `cas_required` |
| `transit-paas/keys/*` (кроме trim `paas-inflight`) | удаление/экспорт ключей → потеря зашифрованных полей |

Честно: скомпрометированный worker **может** прочитать и испортить секреты всех тенантов — он и есть их писатель. Уменьшаем радиус: отдельный процесс без интернет-входа, `NetworkPolicy` (ingress только от `paas-api` на internal-порт), аудит всех его запросов с `X-Paas-Request-Id` (§13), алерт на аномалии (read `data/*` без соответствующего события reveal в журнале платформы).

### 6.3 Go: клиент, пути, запись с CAS, идемпотентность

```go
package vaultkv

import (
    "context"
    "errors"
    "fmt"
    "regexp"

    vault "github.com/hashicorp/vault/api"          // модуль api — MPL-2.0
    k8sauth "github.com/hashicorp/vault/api/auth/kubernetes"
)

var (
    nsRe  = regexp.MustCompile(`^t-[a-z0-9]{10}$`)
    idRe  = regexp.MustCompile(`^[a-z0-9]{10}$`)
    sysRe = regexp.MustCompile(`^(pg|s3|nats)-[a-z0-9]{10}$|^registry-pull$`)
    ErrConflict = errors.New("secret was modified concurrently")
)

type Zone string // "u" — пользовательские, "sys" — платформенные

type SecretRef struct {
    Namespace string
    Zone      Zone
    ID        string // для sys: "<kind>-<id>", например "pg-a1b2c3d4e5"
}

// VaultKey — ЕДИНСТВЕННОЕ место, где строится путь. Никаких ведущих/двойных слэшей.
func (r SecretRef) VaultKey() (string, error) {
    if !nsRe.MatchString(r.Namespace) {
        return "", fmt.Errorf("bad namespace %q", r.Namespace)
    }
    switch r.Zone {
    case "u":
        if !idRe.MatchString(r.ID) {
            return "", fmt.Errorf("bad secret id %q", r.ID)
        }
    case "sys":
        if !sysRe.MatchString(r.ID) {
            return "", fmt.Errorf("bad sys id %q", r.ID)
        }
    default:
        return "", fmt.Errorf("bad zone %q", r.Zone)
    }
    return r.Namespace + "/" + string(r.Zone) + "/" + r.ID, nil
}

func NewClient(ctx context.Context, addr string) (*vault.Client, error) {
    cfg := vault.DefaultConfig()
    cfg.Address = addr
    c, err := vault.NewClient(cfg)
    if err != nil {
        return nil, err
    }
    auth, err := k8sauth.NewKubernetesAuth("paas-worker",
        k8sauth.WithServiceAccountTokenPath("/var/run/secrets/vault/token")) // projected, aud=vault
    if err != nil {
        return nil, err
    }
    if _, err := c.Auth().Login(ctx, auth); err != nil {
        return nil, err
    }
    return c, nil // + LifetimeWatcher в отдельной горутине
}

type Store struct{ kv *vault.KVv2 } // c.KVv2("paas-tenants")

// PutVersion пишет новую версию с оптимистической блокировкой.
// expect = версия, которую видел пользователь (0 — создание).
// Идемпотентность ретрая River: если CAS не сошёлся, но текущая версия — ровно наши данные,
// значит предыдущая попытка записала их и упала до коммита в БД → считаем успехом.
func (s *Store) PutVersion(ctx context.Context, ref SecretRef, data map[string]string, expect int) (int, error) {
    key, err := ref.VaultKey()
    if err != nil {
        return 0, err
    }
    payload := make(map[string]any, len(data))
    for k, v := range data {
        payload[k] = v
    }
    sec, err := s.kv.Put(ctx, key, payload, vault.WithCheckAndSet(expect))
    if err == nil {
        return sec.VersionMetadata.Version, nil
    }
    if !isCASMismatch(err) {
        return 0, err
    }
    cur, gerr := s.kv.Get(ctx, key)
    if gerr == nil && cur.VersionMetadata.Version == expect+1 && sameData(cur.Data, payload) {
        return cur.VersionMetadata.Version, nil
    }
    return 0, ErrConflict // UI: «секрет изменён в другом окне — обновите страницу»
}

// sameData: sha256 по отсортированным парам key\x00value\x00 (реализация опущена).
// isCASMismatch: *vault.ResponseError с кодом 400 и текстом "check-and-set parameter did not match".
```

Прочие операции — готовые методы `KVv2`: `GetVersion` (reveal/rollback), `GetMetadata` (список версий), `Rollback(ctx, key, toVersion)` (клиентский: читает версию N и пишет её как N+1 с CAS), `DeleteMetadata` (hard delete), `Destroy`. Список ключей без значений — `GET paas-tenants/subkeys/<key>` (эндпоинт KV v2 с 1.10) через `c.Logical().ReadWithContext`.

### 6.4 Reveal: почему worker проверяет пользователя сам

Reveal синхронный (пользователь нажал «показать»), поэтому это не job, а внутренний эндпоинт worker'а `POST /internal/v1/secrets/reveal` (NetworkPolicy: только от подов `paas-api`). paas-api передаёт **access-токен пользователя** (JWT от ZITADEL, получен BFF'ом), worker сам проверяет подпись по JWKS, `aud`, `exp`, `auth_time` (step-up, §7.2) и членство пользователя в проекте по БД.

Что это даёт: RCE в paas-api **не превращается** в «выгрузить секреты всех тенантов» — атакующему нужен свежий токен пользователя каждого проекта, т.е. он дотянется только до проектов тех, кто залогинится во время атаки. Запись (integrity) при этом авторизует paas-api — асимметрия осознанная: утечку не отменить, а порчу откатывает KV-версия и видно в журнале.

## 7. UX секретов

### 7.1 Модель и лимиты

| Параметр | Значение | Основание |
|---|---|---|
| Секрет | имя (DNS-label, уникально в проекте) + пары `KEY=value` | Heroku/Render-модель |
| Ключей в секрете | ≤ 100 | UI и размер `Secret` |
| Значение | ≤ 64 KiB, UTF-8 или бинарное (загрузка файла) | |
| Весь секрет (одна версия) | ≤ 256 KiB | запас до `max_entry_size` Raft (1 MiB, ⚠️ проверить) и лимита `Secret` в 1 MiB (base64 +33 %) |
| Секретов на проект | по тарифу (например 50 / 200) | считает control-plane БД; у Vault OSS квот на путь нет |
| Версий | 10 последних | `max_versions` |
| Изменений | ≤ 30/мин на проект | rate-limit в paas-api (защита Vault и GitLab от цикла в скрипте) |
| Имя ключа для env | `^[A-Za-z_][A-Za-z0-9_]{0,127}$`; запрещены `PORT`, `PAAS_*`, `KUBERNETES_*` | платформа сама инжектит эти имена |
| Имя ключа для файла | `^[A-Za-z0-9._-]{1,253}$` | ограничения ключа `Secret` |

### 7.2 «Показать значение»: читаемо, но с step-up (решение)

**Выбор: значения читаемы**, а не «показываются один раз».
- Почему: секреты тенанта — это его же данные (`DATABASE_URL`, ключи Stripe/Telegram). Write-only заставляет хранить копию «где-то ещё» (в заметках, в чате) — итоговая безопасность хуже, а поддержка получает поток «я забыл, что там было».
- Но чтение — отдельное привилегированное действие:

| Роль в проекте | Имена ключей, версии, привязки | Reveal `u/` | Reveal `sys/` | Запись |
|---|---|---|---|---|
| Owner / Admin | да | да, step-up | да, step-up | да |
| Developer | да | да, step-up | нет | да |
| Viewer | да | нет | нет | нет |

- **Step-up:** reveal требует `auth_time` в токене не старше 10 мин (иначе UI инициирует повторный вход с MFA через ZITADEL `prompt=login` / `max_age=600`). Одна переаутентификация открывает окно 10 мин.
- Reveal — **по одному ключу**, значение скрывается через 30 с, кнопка «копировать» вместо показа; rate-limit 30 reveal/мин на пользователя; каждое событие — в журнал проекта (кто, что, IP, user-agent).
- **Флаг «только запись»** (как Sensitive env у Vercel): ставится на секрет, **необратим**; после него reveal невозможен ни для кого из тенанта, только перезапись. Для тех, кому это важнее удобства.
- Экспорт `.env` — это reveal всех ключей: те же права и step-up, отдаётся как файл-ответ API, не хранится.

### 7.3 Версии, diff, откат

- Список версий: номер, время, автор (из control-plane БД, не из Vault — там автор всегда `paas-worker`), необязательный комментарий, **diff по именам ключей**: «изменён `DB_PASSWORD`, добавлен `SENTRY_DSN`, удалён `OLD_TOKEN`». Diff считает worker, сравнивая версии у себя в памяти; наружу — только имена.
- «Восстановить версию N» = новая версия N+1 с содержимым N (`KVv2.Rollback`) → дальше как обычное сохранение (§7.5). История не переписывается.
- «Уничтожить версию» (`destroy`) — для случая «вставил не то значение, например личный токен»: данные версии стираются безвозвратно, строка в истории остаётся с пометкой «уничтожена».
- **Откат приложения не откатывает секреты** (решение). Приложение всегда получает текущие версии привязанных секретов. Иначе откат релиза вернул бы старый пароль БД после ротации — и сломал приложение. Откат секрета — отдельное явное действие на самом секрете.

### 7.4 Привязка к приложению

| Режим | Что рендерится | Пример |
|---|---|---|
| Все ключи как env | `envFrom.secretRef` | секрет `app-config` → все его ключи |
| Выбранный ключ под своим именем | `env[].valueFrom.secretKeyRef` | `sys/pg-…` ключ `uri` → `DATABASE_URL` |
| Файлы | secret-volume в `/etc/paas/secrets/<имя>` | `tls.crt`, `tls.key` |

- Коллизии env-имён между несколькими привязанными секретами — **ошибка при привязке**, никакого «последний выигрывает».
- Одна привязка = одна строка в control-plane БД (`app_secret_binding`), рендер Deployment берёт из неё актуальную версию.
- Интерполяция вида `postgres://$(USER):$(PASS)@…` (k8s `$(VAR)`) — не в MVP.

### 7.5 Режимы сохранения

- **«Сохранить и перезапустить N приложений»** — по умолчанию; список затронутых приложений показан в диалоге. Один коммит (Commits API, несколько actions атомарно) переводит все привязанные приложения на новую версию.
- **«Сохранить без перезапуска»** — меняется только Vault и БД; приложения остаются на старой версии до следующего деплоя, в UI бейдж «использует v6, актуальна v7 — применить».
- Статус операции — по SSE: `queued → written(v7) → committed(sha) → rolling(app1 ✓, app2 …) → applied`. «Применено» — только когда `Application.status.sync.revision` == SHA и rollout здоров (D3).

### 7.6 Маскирование — что не показывается никогда

- Значения не возвращаются ни одним list/get-эндпоинтом, кроме reveal. Длину значения тоже не показываем (утечка энтропии).
- Значения не попадают в логи backend'а (логгер с allow-list полей; тела `…/secrets/*` не логируются вообще), в события k8s, в статус деплоя, в тексты ошибок (ошибки Vault пробрасываются без payload).
- Логи **приложения тенанта** мы не маскируем: если приложение печатает свой пароль в stdout, он окажется в Loki. Сканировать логи на известные значения не будем — это означало бы, что log-сервис знает все секреты. Сказано в документации.

## 8. Автоматический рестарт при изменении секрета

### 8.1 Решение: версионированные immutable-Secret'ы через git, без Reloader

stakater-reloader в кластере есть (`reloadStrategy: annotations`, `autoReloadAll: false`), и владелец уже живёт с ним под ArgoCD через `ignoreDifferences` + `RespectIgnoreDifferences` (memory). **Для тенантских приложений он не используется.** Рестарт при смене секрета — это обычный деплой: backend коммитит новое имя `sec-<id>-v<N+1>` в `ExternalSecret` и в ссылки Deployment'а.

| Критерий | Reloader (annotations) | Версионированный Secret через git (выбран) |
|---|---|---|
| Соответствие D3 («все изменения через git, прямой patch запрещён») | нет: Reloader патчит pod template мимо git | да |
| Нужны `ignoreDifferences` на каждом tenant-Application | да (иначе selfHeal откатит → двойной rollout) | нет |
| Гонка «под стартовал со старым значением» | да: Secret обновится только на следующем `refreshInterval` ESO (до 1 мин), Reloader среагирует на то, что успело | **нет**: новый под ссылается на Secret, которого до материализации нет → kubelet ждёт (`CreateContainerConfigError` → повтор), а wave -1 обычно успевает раньше |
| Статус «применено» | неизвестно, когда именно подхватилось | гейт D3: SHA + здоровый rollout |
| Откат | значение уже перезаписано | ссылка на прежнюю версию |
| Опрос Vault | `Periodic` на каждом ES | `OnChange`: ноль в покое |
| watch kubelet на Secret'ы | есть | нет (immutable) |
| Кластерный writer в tenant-ns | Reloader патчит Deployment'ы во всех ns | не нужен |

Reloader остаётся для системных компонентов владельца — как сейчас.

### 8.2 Последовательность одной смены

1. worker записал v7 → в одном коммите: `ExternalSecret sec-…-v7` (wave -1), `-v6` удалён из дерева, Deployment'ы привязанных приложений ссылаются на `-v7`.
2. tenant-ArgoCD: wave -1 → ESO материализует `Secret -v7` → health `ExternalSecret` = Healthy.
3. wave 0 → rolling update (maxUnavailable 0: старые поды работают до готовности новых).
4. `PruneLast=true` → после Healthy удаляется `ExternalSecret -v6` → его `Secret` уходит по ownerRef.
5. Бюджет: коммит 0.5 с + webhook/refresh 1–3 с + материализация ~1 с + старт пода (образ уже в кэше) 3–10 с ≈ **5–15 с**.

### 8.3 Крайние случаи

- **Vault недоступен в момент смены** → `ExternalSecret -v7` не станет Ready → wave 0 не начнётся → sync падает по таймауту, backend показывает «не применено: хранилище секретов недоступно». **Работающие поды на `-v6` не затронуты.** После восстановления — повтор sync (River retry).
- **CronJob/Job**: шаблон тоже ссылается на `-v<N>`; следующий запуск возьмёт новую версию, текущий запуск доработает со старой.
- **Один секрет в 10 приложениях**: один коммит, 10 rollout'ов параллельно — это осознанно (как `heroku config:set`). Нужна поэтапность — «Сохранить без перезапуска» + ручной деплой по одному.
- **Секрет изменён, но не привязан ни к чему** — коммита нет, только Vault + БД.
- **Hot reload без рестарта** (приложение перечитывает файл) — не поддерживаем в MVP: immutable Secret + новое имя = всегда рестарт. Честно в документации.

## 9. Ротация

| Класс | Кто ротирует | Как | Частота |
|---|---|---|---|
| Пользовательские `u/` | пользователь | новая версия (§7.5); платформа не может ротировать чужой Stripe-ключ | по желанию; позже — напоминание «не менялся > 180 дней» |
| Платформенные `sys/` (БД, S3, NATS, pull) | paas-worker по кнопке «ротировать» / по событию (утечка, увольнение сотрудника клиента) | generate → Vault v(N+1) → один коммит: потребитель (CNPG managed role, S3 identity, NATS user JWT) + привязанные приложения → гейт D3 | по запросу; детали окна недоступности — в [09](09-svc-databases.md) / [10](10-svc-registry-harbor.md) / [11](11-svc-object-storage-s3.md) |
| Интеграционные токены worker'а (`eso-secret/paas/*`) | оператор | паттерн владельца: `tasks-vault-put` + force-sync + рестарт `paas-worker` | раз в год / при инциденте |
| Transit `paas-cp-fields` | оператор (runbook) | `vault write -f transit-paas/keys/paas-cp-fields/rotate` → фоновый rewrap-job (§15) | раз в год |
| Transit `paas-inflight` | Vault сам (`auto_rotate_period: 24h`) + worker `trim` | trim оставляет 2 последние версии → старые шифротексты в WAL/бэкапах необратимы | сутки |
| Unseal-ключи (Shamir 3/2) | оператор | существующий `vault-rotate.yaml` (resume-safe rekey) | при смене людей / раз в год |
| Barrier-ключ | оператор | `vault operator rotate` — онлайн, дёшево | раз в год |
| Токены ESO / worker / api | автоматически | TTL 10 мин / 1 ч, projected SA-токены kubelet ротирует сам | — |

Два замечания по существующей схеме владельца, которые становятся важнее с приходом чужих данных:
- bank-vaults держит **root-токен и unseal-ключи** в `Secret` `vault-unseal-keys` (ns `vault`), а unseal-ключи ещё и открытым текстом в `/etc/kubernetes/vault-unseal.json` на каждом manager'е. Любой, у кого есть `get secrets` в ns `vault` или root на manager'е, = root Vault = все секреты всех тенантов. После перехода к 3 manager'ам (D12) это три копии. Проверить, что ни одна `RoleBinding` (включая ArgoCD `argocd-managed-*`) не даёт `secrets` в `vault`; офлайн-копия ключей и root-токена — у владельца (менеджер паролей), это же — условие DR (§12.6).
- Ротация `sys/`-кредов БД имеет окно, когда старые поды ещё коннектятся со старым паролем: активные сессии Postgres при `ALTER ROLE` не рвутся, новые коннекты старых подов падают до конца rollout'а. Схема «два пользователя по очереди» — фаза 2, решение — в [09](09-svc-databases.md).

## 10. Удаление

### 10.1 Удаление секрета пользователем

1. Если секрет привязан — UI предлагает отвязать от N приложений (это деплой, §7.5); без отвязки удалить нельзя.
2. Коммит: `ExternalSecret` убран из дерева → ArgoCD prune → `Secret` уходит по ownerRef.
3. В БД `secret.deleted_at = now()`. **Окно восстановления 7 дней**: данные в Vault не трогаем, «Восстановить» = снять `deleted_at`. Дёшево, а спасает от «удалил не тот» (у AWS Secrets Manager то же окно, 7–30 дней).
4. Через 7 дней job `secret.purge`: `DELETE paas-tenants/metadata/<ns>/u/<id>` — все версии и метаданные безвозвратно.

«Уничтожить версию» (`destroy`) — отдельное действие для одной версии (§7.3), без окна.

### 10.2 Удаление проекта

Шаг state machine проекта (порядок важен — Vault чистится **после** namespace, чтобы ESO не сыпал ошибками по живым `ExternalSecret`):

`suspend → удалить дерево проекта из git → дождаться prune → удалить namespace → vault.purge_project → Harbor / S3 / NATS → строки БД`.

У KV v2 **нет рекурсивного удаления** — только `LIST` + `DELETE metadata` по одному ключу:

```go
// PurgeProject идемпотентна: повторный запуск после сбоя дочищает остаток.
func (s *Store) PurgeProject(ctx context.Context, c *vault.Client, ns string) error {
    if !nsRe.MatchString(ns) {
        return fmt.Errorf("bad namespace %q", ns)
    }
    var walk func(prefix string) error
    walk = func(prefix string) error { // prefix: "t-xxx/" , "t-xxx/u/" ...
        sec, err := c.Logical().ListWithContext(ctx, "paas-tenants/metadata/"+prefix)
        if err != nil {
            return err
        }
        if sec == nil || sec.Data["keys"] == nil {
            return nil // уже пусто
        }
        for _, k := range sec.Data["keys"].([]any) {
            name := k.(string)
            if strings.HasSuffix(name, "/") {
                if err := walk(prefix + name); err != nil {
                    return err
                }
                continue
            }
            if err := s.kv.DeleteMetadata(ctx, prefix+name); err != nil {
                return err
            }
        }
        return nil
    }
    if err := walk(ns + "/"); err != nil {
        return err
    }
    // контрольный LIST: должно быть пусто, иначе шаг не завершён (River повторит)
    sec, err := c.Logical().ListWithContext(ctx, "paas-tenants/metadata/"+ns+"/")
    if err == nil && sec != nil && sec.Data["keys"] != nil {
        return fmt.Errorf("vault purge incomplete for %s", ns)
    }
    return err
}
```

### 10.3 Что остаётся после удаления (и что написать в оферте)

| Где | Что | Сколько |
|---|---|---|
| Raft-снапшоты | удалённые секреты (зашифрованы barrier-ключом) | срок хранения снапшотов, 30 дней (§12.4) |
| Vault audit | HMAC значений, пути | retention аудита (§13) |
| Журнал проекта в БД | события без значений | 1 год (журнал безопасности; не путать с 406-ФЗ «данные о клиенте» — [16](16-legal-ru.md)) |
| Vault identity | entity + alias SA `paas-eso` удалённого ns | до ручной чистки |

Entity-сироты: k8s-auth заводит entity на каждый SA, который логинился. Удалять их из worker'а — значит выдать ему `identity/*`, где лежат и staff-entity OIDC; не выдаём. Сирота весит ~1 KB (10 000 проектов ≈ 10 MB) — принимаем; раз в квартал оператор запускает скрипт чистки по `alias.metadata.service_account_namespace`, которого больше нет в кластере.

### 10.4 Неоплата (D10)

Grace и suspend секреты не трогают (suspend = `replicas: 0` через git, `ExternalSecret` остаются). Удаление — вместе с проектом через 30 дней. До удаления пользователю предлагается экспорт: `.env`-архив всех секретов — это reveal (§7.2), с step-up.

## 11. Нагрузка: тысячи ExternalSecret

### 11.1 Откуда берётся нагрузка

Каждое обращение ESO к Vault = `TokenRequest` в apiserver → `login` в Vault (Vault делает `TokenReview` в apiserver, пишет токен и lease в Raft) → чтение → `revoke-self` (ещё запись в Raft). То есть **логин дороже чтения**, а запись на single-node Raft на thin-LINSTOR — самый дорогой ресурс.

Два источника логинов:
1. Синхронизация `ExternalSecret` — при `Periodic` на каждом `refreshInterval`.
2. **Перевалидация `SecretStore`** — на каждом store-requeue. В кластере флаг `store-requeue-interval: 1m0s` (`hosts-vars/external-secrets.yaml`, дефолт ESO — 5m).

### 11.2 Числа (1 000 проектов, по 5 секретов = 5 000 `ExternalSecret`)

| Конфигурация | Логинов в Vault | Комментарий |
|---|---|---|
| Как у системных компонентов сейчас: ES `Periodic 1m`, store requeue 1m | 5 000/мин + 1 000/мин ≈ **100/с** постоянно | ≈ 300 записей/с в Raft; не выдержит и незачем |
| ES `OnChange` + версия закреплена, store requeue 1m | ~0 + 1 000/мин ≈ **17/с** | почти вся нагрузка — пустая перевалидация сторов |
| **Выбрано:** ES `OnChange` + `SecretStore.spec.refreshInterval: 3600` | ≈ **0.3/с** в покое + 1 логин на каждое изменение | в пике (массовый деплой 100 изменений) — десятки логинов, секунды |

Плюс флаг ESO `--enable-vault-token-cache` (есть в `providers/v1/vault/provider.go`; `--vault-token-cache-size` ≥ числа тенантских сторов): токен переиспользуется до истечения TTL вместо логина на каждую операцию ⚠️ проверить, как кэш уживается с `revoke-self` (поведение — в §17).

Остальные настройки ESO владельца (`concurrent: 10`, `client-burst: 200`, `enable-managed-secrets-caching: "false"` — меньше памяти ценой прямых GET'ов `Secret`) для этой нагрузки подходят без изменений.

### 11.3 Массовые события

| Событие | Что происходит | Опасно? |
|---|---|---|
| Рестарт ESO | реконсиляция всех ES; при `OnChange` + валидном `Secret` в Vault не ходит | нет |
| Рестарт/unseal Vault | ES не трогаются; сторы перевалидируются по расписанию | нет |
| Смена полей всех `SecretStore` (например `http` → `https`, §12.5) | provisioner патчит 1 000 сторов → 1 000 логинов | да: катить батчами (50 сторов/мин) |
| Пересоздание всех ES (смена схемы имён) | 5 000 логинов + чтений | да: только поэтапно по проектам |
| Цикл изменений у одного тенанта | упирается в rate-limit paas-api (30/мин) | нет |

Предохранитель на стороне Vault — path-based rate-limit quota (есть в OSS) на `auth/kubernetes-paas/login` и `paas-tenants/`, например 50 rps ⚠️ проверить синтаксис и что bank-vaults её не вычистит (quotas — не секция externalConfig; ставить отдельной задачей ansible).

### 11.4 Метрики и алерты

| Сигнал | Метрика | Порог |
|---|---|---|
| ES не синхронизируется | `externalsecret_status_condition{condition="Ready",status="False"}` по tenant-ns | > 0 дольше 5 мин |
| Стор не валиден | `secretstore_status_condition` (⚠️ имя проверить в 2.5.0) | > 0 |
| Ошибки ESO→Vault | `externalsecret_provider_api_calls_count{provider="HashiCorp/Vault",status="error"}` | рост |
| Логины Vault | `vault_core_handle_login_request_count` | > 5/с — что-то вернулось в `Periodic` |
| Размер token store | `vault_token_count` | тренд |
| Raft | `vault_raft_commitTime`, `vault_raft_storage_*` / диск PVC | p99 > 50 мс; диск > 70 % |
| Seal | `vault_core_unsealed` | == 0 → page |

## 12. Vault HA / DR и runbook

### 12.1 Что есть сейчас (факты из `hosts-vars/vault.yaml` и `charts/vault/pre`)

| Факт | Почему важно для PaaS |
|---|---|
| `vault_spec.size: 1`, single-node Raft | нет HA |
| PVC 2Gi на `lnstr-major-local` (autoPlace 1) | **нет ни Raft-, ни DRBD-репликации**: смерть диска = потеря Vault |
| `listener.tcp.tls_disable: true` | значения секретов ходят ESO↔Vault и worker↔Vault открытым HTTP по сети между нодами |
| NP `allow-for-monitoring`: ingress на 8200 **без `from`** | с точки зрения NetworkPolicy порт API открыт для любого пода кластера; от тенантов спасает только tenant-egress CCNP (D4) |
| `unauthenticated_metrics_access: "true"` | `/v1/sys/metrics` без токена |
| audit device — нет; автоматических Raft-снапшотов — нет | нечего предъявить при инциденте; нечего восстанавливать |
| auto-unseal: bank-vaults unsealer из `Secret` `vault-unseal-keys` | рестарт пода лечится сам за ~минуту |

### 12.2 Что значит «Vault упал» при нашей схеме

| Что | Работает? |
|---|---|
| Работающие приложения тенантов | **да** — `Secret`'ы уже материализованы, `OnChange` в Vault не ходит, `deletionPolicy: Retain` |
| Логин пользователей в консоль, API-токены | **да** — ZITADEL и хэши в БД, Vault не на пути аутентификации |
| Рестарт/перенос подов, автоскейл | **да** — `Secret` в etcd |
| Изменение секретов, reveal | нет → UI «хранилище секретов недоступно», job'ы ждут в River |
| Деплой с новой версией секрета / новое приложение с секретами | нет — wave -1 не станет Healthy, старые поды продолжают работать |
| Операции, которым нужен Transit-decrypt | нет (§15) |

Итого: Vault — зависимость **изменений**, не работы. Это главный аргумент, почему для первых платных клиентов достаточно одного пода с быстрым восстановлением, а не сразу Raft ×3.

### 12.3 Этапы (рекомендация)

| Этап | Когда | Состав |
|---|---|---|
| **1** | до первого платного клиента | один под; PVC 10Gi на `lnstr-major-multi-sync` (DRBD 2 реплики — под переедет на ноду с репликой); Raft-снапшоты раз в час + перед каждым `vault-install.yaml`; офсайт-копия раз в сутки; audit (§13); TLS на listener + закрытая NP (§12.5); DR-учения раз в квартал |
| **2** | SLA для клиентов / > ~50 платящих проектов / 3 manager'а готовы (D12) | Raft ×3, anti-affinity по нодам, SC `lnstr-*-local` (Raft реплицирует сам — без двойной репликации DRBD, тот же принцип, что CNPG Standard в D6), `retry_join` ⚠️ проверить формат HA-раскладки в bank-vaults |

Переход 1 → 2: снапшот → новый 3-узловой кластер → restore. Требует окна изменений (не простоя приложений, §12.2).

### 12.4 Снапшоты Raft

Vault OSS не умеет автоснапшоты (это Enterprise) — делаем CronJob в ns `vault`:

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: vault-raft-snapshot
  namespace: vault
spec:
  schedule: "17 * * * *"
  concurrencyPolicy: Forbid
  jobTemplate:
    spec:
      backoffLimit: 2
      activeDeadlineSeconds: 900
      template:
        spec:
          serviceAccountName: vault-snapshot
          restartPolicy: Never
          securityContext:
            runAsNonRoot: true
            runAsUser: 10001
            seccompProfile: {type: RuntimeDefault}
          volumes:
            - name: work
              emptyDir: {sizeLimit: 2Gi}
            - name: vault-token
              projected:
                sources:
                  - serviceAccountToken: {path: token, audience: vault, expirationSeconds: 600}
          initContainers:
            - name: snapshot
              image: docker.io/hashicorp/vault:1.21.4
              env:
                - {name: VAULT_ADDR, value: "http://vault.vault.svc.cluster.local:8200"}
                - {name: HOME, value: /work}
              command: ["/bin/sh", "-ec"]
              args:
                - |
                  export VAULT_TOKEN="$(vault write -field=token auth/kubernetes/login role=vault-snapshot jwt=@/var/run/vault/token)"
                  vault operator raft snapshot save "/work/vault-$(date -u +%Y%m%dT%H%M%SZ).snap"
                  vault token revoke -self
              volumeMounts:
                - {name: work, mountPath: /work}
                - {name: vault-token, mountPath: /var/run/vault, readOnly: true}
              securityContext: {allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities: {drop: ["ALL"]}}
          containers:
            - name: upload
              image: docker.io/rclone/rclone:<pinned>
              args: ["copy", "/work", "snap:vault-snapshots/1520-tech-prod-1/", "--include", "*.snap", "--s3-no-check-bucket"]
              envFrom:
                - secretRef: {name: eso-vault-snapshot-s3}   # RCLONE_CONFIG_SNAP_* из eso-secret/vault/snapshot-s3
              volumeMounts:
                - {name: work, mountPath: /work, readOnly: true}
              securityContext: {allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities: {drop: ["ALL"]}}
```

- `--s3-no-check-bucket` — потому что `HeadBucket` в SeaweedFS сломан (факт владельца). Ретеншн — отдельный шаг `rclone delete --min-age 30d`.
- Роль `vault-snapshot` на `auth/kubernetes` (SA `vault-snapshot`, ns `vault`, `audience: vault`), политика — `sys/storage/raft/snapshot` read (§6.2). NP: egress из job'а только к Vault и к S3.
- **Офсайт обязателен.** Системный SeaweedFS живёт в том же кластере — при потере кластера снапшот умрёт вместе с Vault. Раз в сутки — копия к другому провайдеру (`s3-global` за Cloudflare — это тот же кластер, не подходит). D12 говорит «снапшоты в S3» — это необходимо, но не достаточно (§17).
- Снапшот зашифрован barrier-ключом и без unseal-ключей бесполезен. Поэтому **снапшоты и unseal-ключи никогда не хранятся вместе** (и etcd-снапшоты, в которых лежит `Secret` `vault-unseal-keys`, — тоже отдельно от Vault-снапшотов).
- RPO = интервал снапшотов (1 ч). Секреты, изменённые после последнего снапшота, при восстановлении потеряны — их видно по `secret_version` в БД (§12.6, шаг 7).

### 12.5 TLS и сеть — доработка ansible до первого клиента

- Listener с TLS: сертификат от внутреннего `ClusterIssuer` (CA cert-manager), `tls_disable: false`; во всех `SecretStore` (15 системных + тенантские) — `https://` + `caProvider`. Затрагивает системные компоненты владельца → решение владельца (§16). Альтернатива, закрывающая заодно всё остальное межузловое, — прозрачное шифрование Cilium (WireGuard); сейчас не включено.
- NP `allow-vault` / `allow-for-monitoring`: 8200 только из `external-secrets` (ESO), `paas-system` (worker, api), `traefik-lb` (UI), `mon-system` (метрики), job'а `vault-snapshot`. Правило без `from` — убрать.

### 12.6 Runbook

| Ситуация | Признак | Действие |
|---|---|---|
| Рестарт пода | `vault_core_unsealed == 0` < 2 мин | ничего — unsealer bank-vaults распечатает |
| Не распечатывается | unsealer в ошибках, `sealed: true` | `kubectl -n vault exec vault-0 -- vault operator unseal <key>` × 2 (ключи: `Secret`, `/etc/kubernetes/vault-unseal.json`, офлайн-копия) |
| Нода с PVC умерла | под Pending | этап 1 (multi-sync): под поднимется на ноде с репликой DRBD; иначе — восстановление |
| Случайный purge mount'а / порча Raft | 404 на `paas-tenants/…`, ошибки storage | восстановление из снапшота (ниже) **немедленно** |
| Vault недоступен долго | алерты ESO/Vault | приложения работают; в консоли баннер; job'ы ждут |

**Восстановление из снапшота** (отрабатывается на test-1 раз в квартал):
1. `kubectl -n paas-system scale deploy/paas-worker --replicas=0` — остановить писателя, очередь копится.
2. Сохранить текущий `Secret` `vault-unseal-keys` в файл.
3. ⚠️ **Ловушка:** если storage пуст (новый PVC), bank-vaults сам сделает `init` **нового** Vault и **перезапишет** `vault-unseal-keys` новыми ключами ⚠️ проверить поведение на стенде. Снапшот же зашифрован **старым** barrier'ом.
4. `vault operator raft snapshot restore -force <snap>` (токеном нового root).
5. Vault после restore требует **старые** unseal-ключи: вернуть их (офлайн-копия) в `vault-unseal-keys` и в `/etc/kubernetes/vault-unseal.json` (`tasks-vault-distribute-creds.yaml`) → unseal. Без офлайн-копии старых ключей снапшот не восстановить — это и есть причина требования офлайн-копии.
6. Проверка: число ключей `paas-tenants/metadata` vs `secret` в БД; выборочно `ExternalSecret` нескольких тенантов → Ready.
7. Сверка: версии в `secret_version`, созданные после времени снапшота → пометить «требует повторного ввода», уведомить владельцев проектов. Поднять worker.

## 13. Аудит

### 13.1 Два журнала с разными задачами

| Журнал | Кто актор | Для чего |
|---|---|---|
| **Журнал платформы** (`audit_event` в control-plane БД) | пользователь (id, IP, UA), действие, проект, secret_id, версия, `request_id` | «кто посмотрел/изменил мой секрет» — показывается Owner'у проекта в UI; хранится 1 год |
| **Vault audit** | почти всегда `paas-worker` / ESO-роль | «что реально читалось из Vault»; расследование компрометации worker'а/ESO; доступ только staff |

Связка: worker передаёт `X-Paas-Request-Id`, заголовок включён в аудит `vault write sys/config/auditing/request-headers/X-Paas-Request-Id hmac=false` (задача ansible — у bank-vaults externalConfig нет такой секции ⚠️ проверить).

### 13.2 Audit device

```yaml
# hosts-vars/vault.yaml
vault_spec_audit:
  - type: file
    path: stdout
    description: "Vault audit → stdout → Vector → Loki"
    options:
      file_path: stdout
      log_raw: "false"               # значения — HMAC-SHA256, не открытым текстом
      hmac_accessor: "true"
      elide_list_responses: "true"   # ⚠️ проверить наличие опции в 1.21
# и в vault_spec_external_config: audit: "{{ vault_spec_audit }}"
```

**⚠️ Проверить до прода — может уронить весь Configure().** `audit` — **первый** шаг `Configure()` bank-vaults (audit → plugins → auth → groups → policies → secrets). По памяти: после исправления CVE-2025-6000 (HCSEC-2025-14) начиная с 1.20.x создание/изменение audit-устройств через API ограничено и требует явного разрешения в серверном конфиге. Если Vault 1.21.4 отклонит запрос configurer'а, прогон оборвётся **до** политик и mount'ов — тот же класс, что ловушка `group-alias`. Порядок: на test-1 под root `vault audit enable -path=stdout file file_path=stdout` → если отказ, найти в документации 1.21 точный параметр (в `vault_spec_config` или декларация audit в серверном конфиге) → только потом добавлять в bank-vaults.

### 13.3 Почему это не зальёт диск

- `stdout` → файлы логов контейнера, которые kubelet ротирует (`containerLogMaxSize` × `containerLogMaxFiles`) → диск ноды ограничен сверху.
- **Не** делаем file-устройство на PVC Vault: заполненный диск Raft = остановленный Vault, а Vault **блокирует все запросы**, если не может записать ни в одно audit-устройство.
- Loki: отдельный поток (`log_type="vault-audit"`), retention 90 дней (решение владельца), папка Grafana только для staff; tenant-логи (D14) этот поток не видят — принудительный матчер `namespace="t-…"`.
- Цена отказа Vector: при отставании дольше ротации — дыра в аудите, а не остановка Vault. Алерт на отставание/дропы Vector.
- Объём (оценка для 1 000 проектов в покое): ~0.3 логина/с × ~5 запросов в цикле ESO × 2 записи × ~2.5 KB ≈ 7–8 KB/с ≈ **0.6 GB/сутки несжато**, в Loki — десятки MB. Измерить на стенде.

## 14. Dynamic DB credentials (Vault database engine) — решение

**Решение: никогда — для тенантских managed-БД. Не фаза 2, не в roadmap.**

| Аргумент | Суть |
|---|---|
| Vault становится сетевым хабом во все тенантские БД | database engine коннектится к каждой БД под superuser'ом → нужны NP-дыры из ns `vault` во все `t-*` и superuser-креды каждой БД в Vault. Компрометация Vault = суперпользователь во всех БД. Противоречит изоляции D4 |
| Тысячи connection-конфигов и lease'ов | `database/config/<db>` на каждую БД; lease на каждый под → запись в Raft на каждый старт пода; при недоступности Vault новые поды не получают креды — **Vault попадает на путь данных**, чего мы в §12.2 сознательно избегали |
| Приложения к этому не готовы | большинство читает `DATABASE_URL` один раз при старте; истечение lease = авария в 3 часа ночи |
| Ценность для тенанта мала | БД в его же namespace, закрыта NetworkPolicy, креды видит только он |
| ESO `VaultDynamicSecret` generator | в tenant-ns запрещён VAP (§5.5: без `sourceRef`) |

**Вместо этого:** статические креды в `paas-tenants/<ns>/sys/pg-<id>`, генерирует worker, ротация по кнопке (§9), управление ролью в Postgres — у CNPG (детали — [09-svc-databases.md](09-svc-databases.md)). Для собственной БД control plane (CNPG в `paas-system`) dynamic creds тоже не нужны — CNPG управляет её секретом сам.

## 15. Transit для control-plane БД — да

**Решение: да.** Правило распределения:

| Что за секрет | Где хранится |
|---|---|
| Нужен **рантайму тенанта** (env/файл пода) | Vault KV `paas-tenants` (§3) |
| Нужен **только control plane** и должен быть расшифрован | колонка в Postgres, шифротекст Transit |
| Мы его **только проверяем** | хэш, не шифрование |

| Поле | Способ | Ключ / контекст |
|---|---|---|
| API-токены пользователей (`paas_…`, 32 случайных байта) | SHA-256 (энтропия высокая — argon2 не нужен), в БД — хэш + первые 8 символов для отображения | — |
| Входящее значение секрета (`secret_write_intent.ciphertext`) | Transit | `paas-inflight`, context = `intent_id`; paas-api — только encrypt |
| Seed'ы подписи NATS account (если хранятся в БД, см. [09](09-svc-databases.md)) | Transit | `paas-cp-fields`, context = `table:column:row_id` |
| Токен рекуррентного платежа, id сохранённого способа оплаты | Transit | `paas-cp-fields` |
| OAuth/refresh-токены сторонних интеграций, webhook-секреты (фаза 2) | Transit | `paas-cp-fields` |
| ПДн для идентификации по 406-ФЗ (если вообще храним) | Transit, отдельный ключ `paas-pii`, отдельный процесс-читатель | после юриста, [16](16-legal-ru.md) |
| Пароли, TOTP | не храним — это ZITADEL | — |

Почему Transit, а не AES-ключ в env приложения:
- ключ не покидает Vault: дамп БД / barman-бэкап в S3 / утёкший `pg_dump` бесполезны без Vault;
- `derived: true` + context привязывает шифротекст к строке → нельзя переставить шифротекст из одной строки в другую (например, чужой платёжный токен в свою запись);
- ротация + `rewrap` без знания открытого текста приложением, аудит каждого decrypt'а.

```go
type FieldCipher struct {
    c   *vault.Client
    key string // "paas-cp-fields"
}

func fieldCtx(table, column, rowID string) string { // rowID — UUIDv7, генерируется до INSERT
    return base64.StdEncoding.EncodeToString([]byte(table + ":" + column + ":" + rowID))
}

func (f *FieldCipher) Encrypt(ctx context.Context, pt []byte, table, column, rowID string) (string, error) {
    s, err := f.c.Logical().WriteWithContext(ctx, "transit-paas/encrypt/"+f.key, map[string]any{
        "plaintext": base64.StdEncoding.EncodeToString(pt),
        "context":   fieldCtx(table, column, rowID),
    })
    if err != nil {
        return "", err
    }
    return s.Data["ciphertext"].(string), nil // "vault:v3:…"
}

func (f *FieldCipher) Decrypt(ctx context.Context, ct, table, column, rowID string) ([]byte, error) {
    s, err := f.c.Logical().WriteWithContext(ctx, "transit-paas/decrypt/"+f.key, map[string]any{
        "ciphertext": ct,
        "context":    fieldCtx(table, column, rowID),
    })
    if err != nil {
        return nil, err
    }
    return base64.StdEncoding.DecodeString(s.Data["plaintext"].(string))
}
// Для списков — batch_input (одна поездка в Vault на страницу).
```

Rewrap после ротации ключа (фоновый River-job, батчами):

```sql
-- строки, зашифрованные не последней версией ключа ($1 = latest_version из transit-paas/keys/paas-cp-fields)
SELECT id, secret_ct
FROM integration_credential
WHERE secret_ct NOT LIKE 'vault:v' || $1 || ':%'
ORDER BY id
LIMIT 500;
-- rewrap через transit-paas/rewrap/paas-cp-fields (batch_input), затем
-- UPDATE integration_credential SET secret_ct = $new WHERE id = $id AND secret_ct = $old;  -- оптимистично
```

После полного rewrap оператор поднимает `min_decryption_version`. Открытый текст нигде не кэшируется дольше запроса; при недоступности Vault падают только операции, которым нужен decrypt (§12.2).

## 16. Решения, требующие владельца

1. **Модель reveal**: значения читаемы (step-up MFA ≤ 10 мин, по одному ключу, журнал) + необратимый флаг «только запись» — или write-only по умолчанию для всех? Рекомендация — первое (§7.2).
2. **`vault_purge_unmanaged_exclude.secrets: true`** при включении PaaS — меняет сознательно строгий режим владельца, но убирает сценарий «опечатка в конфиге → уничтожены все секреты тенантов и transit-ключи» (§3.5).
3. **Этапы HA Vault**: один под на `lnstr-major-multi-sync` + снапшоты до первого платного клиента, Raft ×3 — позже (§12.3). Альтернатива — Raft ×3 сразу вместе с 3 manager'ами (D12): надёжнее, но ещё одна распределённая система в эксплуатации соло.
4. **TLS на listener Vault + закрытие NP 8200** до первого клиента — затрагивает 15 системных `SecretStore` (§12.5). Либо Cilium WireGuard для всего межузлового трафика.
5. **Лицензия Vault (BSL 1.1) для коммерческой PaaS** — вопрос юристу; при сомнении — план перехода на OpenBao (MPL-2.0). Go-клиент уже за интерфейсом (§1.2). Не юридическая консультация.
6. **Офсайт-хранилище** для Vault-снапшотов (другой провайдер) и **офлайн-копия** unseal-ключей + root-токена у владельца (§12.4, §12.6). Без неё восстановление из снапшота невозможно.
7. **Политика доступа staff к секретам клиентов**: break-glass-процедура (кто, когда, с чьего согласия, запись в журнал) и формулировка в оферте (§2.2, строка 17).
8. **Сроки хранения**: окно восстановления удалённого секрета 7 дней, снапшоты 30 дней, Vault audit 90 дней, журнал платформы 1 год — всё это пункты оферты (§10.3, §13).
9. **Откат приложения не откатывает секреты** (§7.3) — подтвердить как продуктовое правило.

## 17. Открытые вопросы / что проверить на стенде

| # | Что проверить | Как |
|---|---|---|
| 1 | `bound_service_account_namespace_selector` в Vault 1.21.4 | на test-1: роль с селектором + ClusterRole `namespaces get` для SA `vault` (§4.4); логин из ns с label → ok, без label → 403; без RBAC — какой именно отказ |
| 2 | `audience` у роли: поведение и предупреждения | `vault write auth/kubernetes-paas/role/paas-eso audience=vault-paas …`; логин токеном с другой аудиторией → 403 |
| 3 | Создание audit-устройства через API на 1.21.4 (последствия CVE-2025-6000) | §13.2; до этого `audit:` в bank-vaults не добавлять |
| 4 | bank-vaults: `configuration.config` для kv-v2 и `configuration.keys` для transit | прогон на test-1, `vault read paas-tenants/config`, `vault read transit-paas/keys/paas-cp-fields` |
| 5 | ESO `dataFrom.extract.version` для Vault KV v2 | записать v1, v2; ES с `version: "1"` → в `Secret` значения v1 |
| 6 | ESO `OnChange` + `immutable`: восстанавливается ли удалённый руками `Secret` | `kubectl delete secret sec-…-v1` → ESO пересоздаёт (путь `isSecretValid`)? |
| 7 | `--enable-vault-token-cache` вместе с `revoke-self` | 100 тенантских сторов; число логинов/мин по audit-логу с кэшем и без |
| 8 | ArgoCD v3.5.1: health `ExternalSecret`, не присваивает ли он материализованный `Secret` | `argocd app get` — wave -1 ждёт Ready; `Secret` нет в ресурсах приложения |
| 9 | `max_entry_size` Raft и лимит 256 KiB на секрет | запись секрета 256 KiB и 900 KiB; поведение ESO/`Secret` |
| 10 | bank-vaults на пустом storage (auto-init и перезапись `vault-unseal-keys`) | DR-учение §12.6 на test-1 |
| 11 | Rate-limit quota на `auth/kubernetes-paas/login` в OSS; не вычищает ли её bank-vaults | `vault write sys/quotas/rate-limit/paas-login path=auth/kubernetes-paas/login rate=50` → рестарт пода Vault → квота на месте? |
| 12 | Имена метрик `secretstore_*` / `externalsecret_*` в ESO 2.5.0 | `curl` метрик контроллера |
| 13 | `subkeys`-эндпоинт KV v2 и права на него в 1.21 | `vault read paas-tenants/subkeys/<key>` под политикой worker'а |
| 14 | Согласованность с [09](09-svc-databases.md) / [10](10-svc-registry-harbor.md) / [11](11-svc-object-storage-s3.md) | зона `sys/` предполагает, что платформенные креды (CNPG `bootstrap.initdb.secret` / `managed.roles[].passwordSecret`, S3-ключи, NATS creds, robot Harbor) берутся из Vault, а не генерируются операторами прямо в `Secret`. Если соседний раздел решил иначе — reveal `sys/`-секретов в UI придётся делать через provisioner |
| 15 | **Сомнение по D9 (решению следую):** поток «paas-api → очередь → worker» в буквальном виде кладёт открытое значение в аргументы River-job'а, т.е. в Postgres, WAL и barman-бэкапы | закрыто encrypt-only Transit в paas-api + отдельная таблица интентов + trim ключа `paas-inflight` (§2, §15); проверить, что ни один лог/трейсинг middleware не пишет тело запроса |
| 16 | **Сомнение по D12 (решению следую):** «Vault Raft-снапшоты в S3» — системный S3 в том же кластере | нужен офсайт у другого провайдера (§12.4) |

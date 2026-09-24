# 17. Дорожная карта

> Оценки — в **человеко-неделях одного разработчика** (владелец: сильный в Ansible/k8s, Go — предполагается уверенный). Календарь условный: старт 2026-10-01. Юридический трек идёт параллельно технике и **может оказаться длиннее её**.

## TL;DR

- **MVP для первого платного клиента: ~41 человеко-неделя ≈ 9-10 месяцев соло** (7-8 при активной помощи ИИ-ассистентов в коде). Честная оценка, не маркетинговая.
- **Два гейта не зависят от кода:** юридический (реестр РКН, СОРМ за 45 дней до начала работы, платёжный провайдер с рекуррентами) и HA (3 manager'а). Без них платный запуск невозможен при любой готовности кода.
- **Порядок: фундамент (ansible) → control plane (Go) → консоль → услуги по одной → биллинг → бета.** Биллинг сознательно поздно: до беты он не нужен, а его модель лучше уточнить на реальном потреблении.
- **S3 и NATS — фаза 2**, если conformance-тест SeaweedFS не пройдёт чисто. S3 в MVP — только при зелёной матрице совместимости клиентов.

---

## 1. Этапы

```mermaid
gantt
  dateFormat YYYY-MM-DD
  axisFormat %b
  title PaaS — MVP (условный старт 2026-10-01)

  section Юр. трек (параллельно)
  Юрлицо, юрист, квалификация          :legal1, 2026-10-01, 30d
  Реестр РКН, СОРМ (≥45 дней), ГосСОПКА :legal2, after legal1, 75d
  Платёжка + рекурренты + касса        :legal3, after legal1, 45d
  Оферта, AUP, ПДн, SLA                :legal4, after legal1, 30d

  section Э1 Фундамент (ansible)
  3 manager'а + 2-й bastion            :e1a, 2026-10-01, 10d
  Tenant pool + paas-policies + userns :e1b, after e1a, 14d
  Tenant storage pool + SC lnstr-tenant :e1g, after e1b, 4d
  traefik-tenants + haproxy-tenants    :e1h, after e1g, 7d
  Cilium WireGuard + Egress Gateway    :e1i, after e1h, 7d
  kubelet-config-update + Corefile     :e1j, after e1i, 4d
  CNPG + conformance SeaweedFS         :e1c, after e1j, 7d
  argocd-tenants                       :e1d, after e1c, 7d
  Harbor + proxy-cache                 :e1e, after e1d, 10d
  Zitadel customer instance            :e1f, after e1e, 4d

  section Э2 Control plane (Go)
  Каркас, БД, River, OpenAPI, auth     :e2a, after e1f, 14d
  Orgs/Projects/RBAC + provisioner     :e2b, after e2a, 17d
  Apps: модель, Harden, рендер, golden :e2c, after e2b, 10d
  GitLab + deploy SM + sync watcher    :e2d, after e2c, 10d
  Логи/события/SSE + поддомены         :e2e, after e2d, 10d

  section Э3 Консоль
  Auth, каркас, orgs/projects          :e3a, after e2b, 10d
  Apps, прогресс деплоя, логи, ошибки  :e3b, after e2e, 18d

  section Э4 Домены, секреты, registry
  Custom domains + TXT + сертификаты   :e4a, after e3b, 12d
  Секреты (Vault/ESO)                  :e4b, after e4a, 8d
  Registry self-service                :e4c, after e4b, 8d

  section Э5 Managed-БД
  Postgres (Hobby/Standard) + backup   :e5a, after e4c, 18d
  Valkey + L4 внешний доступ           :e5b, after e5a, 10d

  section Э6 Биллинг
  Тарифы→квоты, платежи, suspend       :e6a, after e5b, 20d

  section Э7 Готовность
  Наблюдаемость, runbooks, DR-учения   :e7a, after e6a, 10d
  Security review + pentest-чеклист    :e7b, after e7a, 7d

  section Э8 Бета
  Закрытая бета (10 тенантов)          :e8a, after e7b, 35d
  Платный запуск                       :milestone, after e8a, 0d
```

### Этап 0 — Решения и юридический трек (параллельно, календарно 2-4 мес.)

| Задача | Выход | Блокирует |
|---|---|---|
| Ответы на вопросы из [18-risks-and-owner-decisions.md](18-risks-and-owner-decisions.md) (где физически кластер, форма юрлица, бюджет на железо) | зафиксированные решения | всё |
| Юрлицо, юрист, квалификация деятельности | заключение юриста | реестр |
| Включение в реестр провайдеров хостинга РКН | запись в реестре | платный запуск |
| СОРМ: заявление в ФСБ **не позднее 45 дней** до начала работы | план СОРМ | платный запуск |
| Платёжный провайдер: договор, одобрение рекуррентов, облачная касса | боевой магазин | Э6 |
| Оферта, AUP, политика ПДн, уведомление РКН об обработке ПДн | опубликованные документы | бета |

Подробно — [16-legal-ru.md](16-legal-ru.md).

### Этап 1 — Фундамент платформы (ansible) · ~10.5 чел.-нед.

| Работа | Оценка | Выход / критерий готовности |
|---|---|---|
| 3 manager'а: `cilium-install.yaml --tags post` → `full-node-install` → `manager-join` ×2 (инвариант репо: CCNP до join) | 1 | `etcdctl endpoint health` — 3/3; учение: выключить один manager, деплой проходит |
| Второй bastion-proxy (группа уже поддерживает N хостов) + DNS на оба | 0.5 | выключение одного bastion не роняет внешний доступ |
| Tenant pool: 2 воркера получают label + taint; системные поды уезжают | 0.5 | `kubectl get pods -A -o wide` — на tenant-нодах только DaemonSet'ы |
| `paas-policies`: VAP + bindings, CCNP tenant-baseline, ClusterIssuer'ы, PriorityClass'ы, ClusterRole'ы | 1.5 | набор «злых манифестов» (hostPath, privileged, чужой реестр, без limits, SA-токен) — **все отклонены** |
| Стенд-проверка user namespaces на ядре нод (`hostUsers: false`) | 0.5 | под с `hostUsers:false` стартует; внутри `uid 0` ≠ host uid 0 (`/proc/self/uid_map`) |
| Tenant storage pool (отдельный диск) + SC `lnstr-tenant-local` / `lnstr-tenant-multi-sync` (`Delete`); перенос DRBD-реплик **до** постановки taint | 0.5 | обе реплики тома тенанта — только на tenant-нодах |
| `traefik-tenants` + `haproxy-tenants`: параметризация компонентов под второй инстанс, отдельный IP на bastion, строгие `trustedIPs`, affinity на системные ноды | 1 | IngressRoute тенанта не может сослаться на системный Service; DDoS на тенантский IP не трогает консоль |
| Cilium: WireGuard + `bpf.masquerade` + Egress Gateway (сначала `test-1`, на проде — в окно) | 1 | `curl ifconfig.me` из пода тенанта = egress-IP, не IP ноды; `cilium encrypt status` — WireGuard активен |
| `kubelet-config-update.yaml` (rolling): `podPidsLimit`, `systemReserved`/`kubeReserved`; патч Corefile (PTR) | 0.5 | fork-бомба в поде упирается в лимит; PTR-перебор service CIDR ничего не отдаёт |
| `cnpg` + barman-cloud plugin + **conformance против SeaweedFS** (backup, WAL-архив, restore, PITR) | 1 | восстановление на момент времени проходит; иначе — решение по pgBackRest |
| `argocd-tenants` (по образцу `argocd`, свой namespace, пустой `argocd-secret`) | 1 | Application в `t-test` синхронизируется; запись в чужой ns — Forbidden |
| `harbor` + proxy-cache + **conformance distribution S3 × SeaweedFS** | 1.5 | push/pull многослойных и multi-arch образов, GC; иначе — PVC |
| Zitadel: виртуальный инстанс для клиентов, саморегистрация, MFA | 0.5 | регистрация тестового клиента, staff-инстанс не затронут |

### Этап 2 — Control plane (Go) · ~9 чел.-нед.

| Работа | Оценка | Критерий |
|---|---|---|
| Каркас: 3 бинаря, конфиг, pgx+sqlc, goose, River, OpenAPI, BFF-auth с Zitadel, `paas-control-plane` ansible-компонент | 2 | логин в пустую консоль на test-1 |
| Organizations / Projects / Members / роли / приглашения | 1 | RBAC-матрица покрыта тестами |
| `paas-provisioner`: жизненный цикл namespace (создание, смена тарифа, удаление) | 1.5 | envtest + kind: namespace создаётся со всеми объектами, VAP не мешает provisioner'у и мешает всем остальным |
| Apps: allow-list модель, `Harden()`, рендер типами, golden-тесты | 1.5 | любое изменение обвязки видно в golden-диффе |
| GitLab-адаптер, state machine деплоя, наблюдатель sync (revision + health) | 1.5 | e2e: create app → Running < 60 с на test-1 |
| Логи, события, статусы, SSE; платформенные поддомены (wildcard) | 1 | `https://<app>-<pid>.<apps-domain>` отвечает |

### Этап 3 — Консоль (React SPA) · ~5.5 чел.-нед.

Каркас, auth, orgs/projects; приложения: создание, деталь, прогресс деплоя по шагам, лог-вьюер, перевод k8s-ошибок на русский (таблица из [07-svc-compute.md](07-svc-compute.md)). Подробно — [14-frontend-console.md](14-frontend-console.md).

### Этап 4 — Домены, секреты, registry · ~4.5 чел.-нед.

Custom domains (TXT-верификация, CNAME/A, статус выпуска, Cloudflare-режим), секреты (Vault KV + ESO + reloader), Harbor self-service (robot-токены, квота, уязвимости).

### Этап 5 — Managed-БД · ~4.5 чел.-нед.

Postgres Hobby/Standard с backup и restore-на-момент, Valkey, L4-доступ через bastion (аллокатор портов, TLS, anti-bypass).

### Этап 6 — Биллинг · ~4 чел.-нед.

Тарифы → ResourceQuota, рекуррентные платежи, чеки 54-ФЗ, state machine неоплаты с suspend, идентификация клиента в онбординге, метеринг (информационный). Подробно — [13-billing-and-quotas.md](13-billing-and-quotas.md).

### Этап 7 — Готовность к эксплуатации · ~3 чел.-нед.

Алерты и дашборды платформы, внешняя статус-страница, runbooks, **учения**: восстановление control-plane БД, Vault из Raft-снапшота, etcd из снапшота, CNPG-тенанта на момент времени. Security review по чек-листу из [03-security-model.md](03-security-model.md).

### Этап 8 — Закрытая бета · календарно 4-6 недель

10 дружественных тенантов бесплатно; критерий выхода — 4 недели без инцидентов data plane и без ручных вмешательств в `t-*`.

---

## 2. Состав MVP

| Услуга | В MVP | Условие |
|---|---|---|
| Контейнеры (web, worker) | ✅ | — |
| Платформенный поддомен + wildcard TLS | ✅ | — |
| Custom domains + автосертификаты + Cloudflare-режим | ✅ | — |
| Секреты | ✅ | — |
| Registry (Harbor) | ✅ | S3-conformance или PVC |
| Managed Postgres (Hobby, Standard) | ✅ | barman × SeaweedFS conformance или pgBackRest |
| Valkey | ✅ | — |
| L4-доступ к БД снаружи | ✅ | — |
| Биллинг-подписка | ✅ | платёжный провайдер одобрил рекурренты |
| S3 | ⚠️ | **только при зелёной матрице совместимости клиентов**, иначе фаза 2 |
| NATS | ❌ фаза 2 | — |
| Выделенные IP | ❌ фаза 2 | ansible-провижининг IP-слотов |
| Cron-jobs, one-off jobs | ❌ фаза 2 | — |
| HPA / scale-to-zero | ❌ фаза 2 | — |
| exec в контейнер | ❌ фаза 2 | step-up MFA + запись сессии |

### 2.1. Если нужно быстрее: «MVP-минимум» (~20 чел.-нед.)

Контейнеры + поддомены + custom domains + секреты + Harbor + Postgres Hobby + ручной биллинг (счёт на юрлицо, без рекуррентов). Отсекается: Standard-Postgres, Valkey, L4-доступ, автоматические платежи, S3. Годится для первых 10-20 клиентов «по знакомству». Юридический гейт при этом **не отменяется**.

---

## 3. Фаза 2 (после запуска), в порядке ценности

| # | Работа | Почему в этом порядке |
|---|---|---|
| 1 | S3 self-service (если не вошёл в MVP) + отдельный инстанс `seaweedfs-tenants` | заявленная платная услуга; blast radius |
| 2 | Cron-jobs и one-off jobs (миграции БД) | первое, что спрашивают после «запустить контейнер» |
| 3 | HPA по CPU на старших тарифах | прямая монетизация |
| 4 | NATS (общий кластер, account на тенанта) | дёшево в эксплуатации благодаря accounts |
| 5 | Выделенные IP-слоты | платный аддон, требует ansible-провижининга слотов |
| 6 | Scale-to-zero для неактивных приложений | экономика flat-тарифа |
| 7 | Canary через Argo Rollouts (уже в кластере) | фича старших тарифов |
| 8 | Больше БД: MariaDB, ClickHouse, RabbitMQ, OpenSearch, FerretDB | по спросу |
| 9 | exec со step-up MFA и записью сессии | по спросу, риск |
| 10 | Сборка из git (buildpacks/BuildKit в изолированном пуле, gVisor) | большой отдельный продукт |
| 11 | gVisor-тариф для «недоверенных» (триал) | если появится абьюз |
| 12 | Gateway API вместо IngressRoute | когда Traefik упрётся в масштаб |

---

## 4. Критерии «можно брать деньги»

Все пункты обязательны:

1. ☐ Юрлицо в реестре провайдеров хостинга РКН; СОРМ согласован; оферта/AUP/ПДн опубликованы.
2. ☐ 3 manager'а; учение «выключить manager» пройдено.
3. ☐ 2 bastion-proxy; учение «выключить bastion» пройдено.
4. ☐ Набор «злых манифестов» отклоняется VAP/PSA при применении **в обход backend** (напрямую от имени tenant-ArgoCD SA).
5. ☐ Pentest-чеклист из [03-security-model.md](03-security-model.md): под тенанта не достаёт apiserver, kubelet, IP нод, metadata, чужие namespace, системные сервисы; нет SA-токена.
6. ☐ Учения восстановления пройдены: control-plane Postgres, Vault, etcd, CNPG-тенант на момент времени.
7. ☐ Бэкапы лежат **вне площадки**.
8. ☐ Внешняя статус-страница и канал уведомлений клиентов.
9. ☐ Рекуррентные платежи и чеки 54-ФЗ проверены на реальной карте.
10. ☐ Suspend/resume неплательщика проверен end-to-end без потери данных.
11. ☐ Бета: 4 недели без инцидентов data plane.

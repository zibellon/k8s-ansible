# Storage Comparison — Tables & Decision Matrix

Сводные таблицы для быстрого выбора storage solution и resource group под конкретный workload.

---

## 1. SYNC/ASYNC семантика — три уровня

Прежде чем сравнивать варианты, важно различать **где** мы говорим про sync.

| Уровень | Что значит «SYNC» | Что значит «ASYNC» |
|---|---|---|
| **L1: Приложение** (Postgres) | `synchronous_commit=on` + `synchronous_standby_names`: PG ждёт ack от standby | Streaming replication без sync standby |
| **L2: Блочное устройство** (DRBD/CEPH) | DRBD Proto C / CEPH `min_size=N`: ack после write на N реплик | DRBD Proto A: ack после local + сетевой буфер |
| **L3: Локальный диск** (ZFS, RAID) | ZFS mirror всегда sync; `sync=standard` уважает fsync приложения | `sync=disabled` — fsync игнорируется (потеря до 5 сек на crash) |

**В нашей инсталляции мы используем L2 + L3:**
- L2 SYNC через LINSTOR DRBD Proto C (для prod)
- L2 ASYNC через DRBD Proto A (для кэшей)
- L3 SYNC через ZFS `sync=standard` (всегда, без исключений)
- L1 в нашем дизайне НЕ обязателен — block-level sync прозрачен для PG

---

## 2. 4 варианта репликации (упрощённая таблица)

| # | Вариант | Local | Network | Защита от диска | Защита от ноды | RPO при отказе ноды | Когда применять |
|---|---|---|---|---|---|---|---|
| 1 | Local SINGLE | replica=1 | — | ❌ | ❌ | n/a | scratch, build cache, эфемерные данные |
| 2 | Local SYNC | mirror | — | ✅ | ❌ | n/a | non-critical VM, dev (опциональный RG) |
| 3 | Network ASYNC | — | replica=2 (Proto A) | ✅ | ⚠️ потери | ~10–100 мс | кэши, аналитика, метрики |
| 4 | **Network SYNC** | — | replica=2 (Proto C) | ✅ | ✅ | **0** | **prod БД, Kafka, Clickhouse** |

В нашей инсталляции вариант 2 (`rg-local-mirror`) — опционален, требует Layout-1 (см. [QUESTIONS.md](QUESTIONS.md) Q1).

---

## 3. LINSTOR Resource Groups — детальная матрица

| RG | LINSTOR replica | DRBD Proto | Backing | Diskless tiebreaker | Полезн. ёмкость | Latency write | Latency read | Защита |
|---|---|---|---|---|---|---|---|---|
| `rg-local-single` | 1 | — | ZFS stripe | нет | 100% raw | ~50 µs | ~30 µs | нет |
| `rg-local-mirror`* | 1 | — | ZFS mirror | нет | 50% raw | ~80 µs | ~30 µs | от диска |
| `rg-net-async` | 2 | A | ZFS stripe | да (3-я нода) | 50% cluster | ~80 µs | ~30 µs (local) | от диска и ноды (с потерями) |
| `rg-net-sync` | 2 | C | ZFS stripe | да (3-я нода) | 50% cluster | ~250 µs | ~30 µs (local) | от диска и ноды (zero loss) |

\* Только в Layout-1.

**Цифры latency** — приблизительные, на NVMe + 25 GbE direct, idle cluster. Под нагрузкой (95-percentile) могут быть в 2-5x больше.

---

## 4. Recommended RG по типу нагрузки

| Workload | Recommended RG | Почему |
|---|---|---|
| **Postgres (production data)** | `rg-net-sync` | Каждый коммит обязан быть на 2 нодах. RPO=0. |
| **Kafka (broker logs)** | `rg-net-sync` | Topic data — критично. Replication factor Kafka можно снизить до 1 (storage делает работу). |
| **Clickhouse (data parts)** | `rg-net-sync` | Append-only, но потеря данных недопустима. |
| **NATS Jetstream (persistent streams)** | `rg-net-sync` | То же что Kafka. |
| **Redis (cache mode)** | `rg-net-async` | Если данные восстанавливаются из источника. |
| **Redis (persistence mode, RDB+AOF)** | `rg-net-sync` | Если Redis — единственный store для данных. |
| **NATS Core (без Jetstream)** | `rg-net-async` или `rg-local-single` | Сообщения не персистентны изначально. |
| **Prometheus (TSDB)** | `rg-net-async` | Метрики восстанавливаются из remote_write / agent. RPO в секундах OK. |
| **Loki (chunks)** | `rg-net-async` | Логи — восстанавливаются из Vector buffer / S3 archive. |
| **Grafana (config DB, sqlite)** | `rg-net-sync` | Дашборды — полу-критичны, маленький volume, можно платить за sync. |
| **K8s etcd (внутри K8s manager VM)** | `rg-net-sync` | K8s state — критично. |
| **K8s container images cache** | `rg-local-single` | Re-pull из registry при необходимости. |
| **VM root disk (OS)** | `rg-net-async` | Можно пересоздать через cloud-init за 5 мин. |
| **VM `/var/log`** | `rg-net-async` или `rg-local-single` | Логи не критичны. |
| **Build artifacts / CI cache** | `rg-local-single` | Регенерируется. |

**Ключевая идея.** Не все данные одинаково важны. Использование одного `rg-net-sync` на всё — это переплата за overhead там, где он не нужен. Гранулярное распределение по RG экономит ёмкость + улучшает агрегированную latency.

---

## 5. Capacity planning — что у нас будет

### 5.1 Layout-2 (default)

```
3 ноды × 8 ТБ datapool = 24 ТБ raw
```

Распределение по RG (примерное, зависит от ваших VM):

| RG | Ёмкость | Используется на ноде | Эффективная для VM |
|---|---|---|---|
| `rg-net-sync` (prod) | 50% от total | равномерно | ~6 ТБ |
| `rg-net-async` (cache) | до 30% | равномерно | ~3 ТБ |
| `rg-local-single` (scratch) | до 20% | независимо на каждой ноде | до 5 ТБ × 3 = 15 ТБ ASCII |

С учётом headroom 30% (под recovery, snapshots, рост):

```
Безопасно используемая ёмкость для prod (rg-net-sync): ~4 ТБ
```

Для большинства K8s-инсталляций этого достаточно: 1 ТБ под Postgres основной БД, 1 ТБ под Kafka topics, 0.5 ТБ под Clickhouse, 1 ТБ под logs/metrics retention, 0.5 ТБ под misc.

### 5.2 Layout-1 (опционально)

```
3 ноды × 6 ТБ (4 ТБ stripe + 2 ТБ mirror) = 18 ТБ raw
```

Сложнее планировать — две независимые группы пулов.

---

## 6. LINSTOR vs CEPH — расширенная таблица

| Критерий | LINSTOR + DRBD + ZFS | CEPH (RBD) |
|---|---|---|
| **Replica=2 production-safe** | ✅ Да (с diskless tiebreaker) | ❌ Нет (требует size=3) |
| **Минимум нод для HA** | 3 (2 diskful + 1 diskless) | 5 (рекомендация) |
| **Полезная ёмкость replica=2** | 50% | unsafe → принудительно 33% (size=3) |
| **Latency write 4K (NVMe + 25GbE)** | ~250 µs | ~1-2 ms |
| **Latency read 4K** | ~30 µs (local) | ~200-500 µs |
| **Sequential write throughput** | 1.5+ GB/s | 700-1000 MB/s |
| **RAM per node** | ~8-12 ГБ | ~15-25 ГБ |
| **CPU baseline** | низкая | средняя-высокая |
| **Object storage (S3)** | ❌ нет (нужен MinIO) | ✅ нативно (RGW) |
| **POSIX shared FS** | ❌ только cluster FS поверх DRBD | ✅ нативно (CephFS) |
| **Snapshots** | ✅ ZFS native, дёшево | ✅ RBD snapshots, дороже |
| **Erasure coding** | ❌ только replication | ✅ k+m профили |
| **Geo-replication** | ⚠️ DRBD-A long distance, clunky | ✅ multi-site RGW + RBD mirror |
| **Auto-rebalance disks** | ⚠️ ZFS rebalance вручную | ✅ автоматический |
| **Скорость recovery после отказа диска** | быстро (DRBD bitmap, ZFS resilver) | средне (PG-level recovery) |
| **Operational complexity** | средняя | высокая |
| **Скорость bootstrap (с нуля)** | дни | недели |
| **Документация / комьюнити** | хорошая (LINBIT) | огромная (Red Hat, IBM) |
| **Vendor lock-in** | LINBIT (open-source, но коммерческая поддержка платная) | open-source, vendor-neutral |
| **Масштабирование на 50+ нод** | сложнее | родная стихия |
| **Стоимость лицензии (community)** | 0 ₽ | 0 ₽ |
| **Стоимость поддержки (enterprise)** | LINBIT subscription | Red Hat, SUSE, IBM |

---

## 7. Decision matrix — выбор по сценарию

### 7.1 Я в ситуации «3 ноды, replica=2, низкая latency, prod БД»

→ **LINSTOR + DRBD + ZFS** (наш дизайн).

### 7.2 Я в ситуации «5+ нод, разные нагрузки, нужен S3»

→ Рассмотреть **CEPH**. Возможно, hybrid (LINSTOR для VM + CEPH RGW для object storage).

### 7.3 Я в ситуации «1 нода, экспериментирование»

→ ZFS local, никакой репликации. LINSTOR overhead не оправдан на 1 ноде.

### 7.4 Я в ситуации «нужна geo-replication между DC»

→ CEPH RBD mirror (стандарт) или DRBD-A в long-distance (work but clunky).

### 7.5 Я в ситуации «Kubernetes-первичный кластер, без Proxmox»

→ Рассмотреть Rook/CEPH operator или OpenEBS / Longhorn / Piraeus (LINSTOR-based) operator.

---

## 8. Cost of one block — жизненный цикл

Чтобы понять реальные расходы, посчитаем стоимость одного 4K-блока в разных вариантах:

### 8.1 rg-net-sync (Postgres write)

```
1. App calls write(4K)
2. ZFS layer: записать в ZIL → fsync (NVMe write) — ~50 µs
3. DRBD layer: encrypted+compressed packet over network — ~30 µs
4. Remote ZFS layer: записать в ZIL → fsync — ~50 µs
5. ack обратно через DRBD — ~30 µs
6. ack приложению
Total: ~160 µs (best case), ~300-500 µs (95p под нагрузкой)
```

### 8.2 rg-net-async (Cache write)

```
1. App calls write(4K)
2. ZFS layer: записать в ZIL → fsync — ~50 µs
3. DRBD layer: положить в send buffer — ~5 µs
4. ack приложению
Total: ~55-100 µs
```

(Asynchronous network sync происходит фоном — но cluster видит ack уже на шаге 4.)

### 8.3 rg-local-single (scratch)

```
1. App calls write(4K)
2. ZFS layer: записать в ZIL → fsync — ~50 µs
3. ack приложению
Total: ~50 µs
```

Разница sync vs async = **~3-5x** в latency. Под 10k RPS Postgres это означает: latency 99p commit = ~500 µs vs ~150 µs. Для большинства приложений — приемлемо. Для real-time торговли — критично.

---

## 9. Quick decision flowchart

```
Нужны ли данные после reboot ноды?
├── НЕТ → rg-local-single (scratch, кэш в памяти-эквивалентный)
└── ДА
    │
    Допустима ли потеря данных за последние секунды при отказе ноды?
    ├── ДА (RPO в секундах OK) → rg-net-async
    └── НЕТ (zero RPO required)
        │
        Это production stateful workload (БД, message queue persistent)?
        ├── ДА → rg-net-sync
        └── НЕТ (system VM, configs) → rg-net-async (хватит)
```

---

## 10. См. также

- [README.md](README.md) — общая архитектура
- [03-linstor-and-drbd.md](03-linstor-and-drbd.md) — настройка RG
- [04-ceph.md](04-ceph.md) — почему отложили CEPH
- [QUESTIONS.md](QUESTIONS.md) — открытые решения по дизайну

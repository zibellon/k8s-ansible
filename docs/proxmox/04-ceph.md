# CEPH — Backlog

CEPH — распределённая object/block storage система от Red Hat (изначально UCSC/Inktank). Часто рассматривается как альтернатива LINSTOR/DRBD для distributed storage в Proxmox-кластере. Эта запись объясняет: что это, почему мы НЕ выбрали CEPH сейчас, и при каких условиях стоит вернуться к этому выбору.

---

## 1. Что такое CEPH в одном абзаце

CEPH — это распределённая storage-система, состоящая из:
- **OSD (Object Storage Daemon)** — процессы на каждой ноде, обслуживающие физические диски. Один OSD = один диск.
- **MON (Monitor)** — кластерные метаданные, кворум кластера. Минимум 3 для HA.
- **MGR (Manager)** — менеджер метрик, dashboard.
- **MDS (Metadata Server)** — для CephFS (POSIX-совместимая ФС).
- **RGW (RADOS Gateway)** — S3/Swift-совместимый шлюз.

Слой ниже всех — **RADOS** (Reliable Autonomic Distributed Object Store), object store. Поверх RADOS строятся:
- **RBD** (RADOS Block Device) — блочное хранилище, аналог DRBD-volume. Используется для VM в Proxmox.
- **CephFS** — POSIX-FS.
- **RGW** — S3-совместимый object storage.

**Распределение данных** — через CRUSH-алгоритм (Controlled Replication Under Scalable Hashing). Каждый объект хешируется → попадает в placement group → размещается на N OSD согласно CRUSH-правилам (`replicated` или `erasure-coded`).

---

## 2. Почему НЕ выбрали CEPH (для нашего сценария)

### 2.1 replica=2 in CEPH = unsafe

CEPH официально **не рекомендует** `size=2` для production replicated pool. Причины:

- При отказе одного OSD оставшаяся копия становится **единственной**. Если во время recovery начинает деградировать второй OSD (битый сектор, scrub-конфликт) — данные теряются.
- При попытке поставить `min_size=1` (чтобы продолжать писать при отказе) — split-brain risk при healing: какая копия истинная?
- Scrub-конфликты: при обнаружении checksum mismatch CEPH должен решить, какая из 2 копий — правильная. Без 3-й копии для голосования — guesswork.

Production-конфигурация CEPH = `size=3, min_size=2`. Это **66% оверхед на репликацию** против 50% у нас (LINSTOR replica=2). На 24 ТБ raw разница — это 4 ТБ полезной ёмкости (12 ТБ vs 8 ТБ при equal headroom).

### 2.2 5+ нод для нормальной HA

CEPH архитектурно рассчитан на масштаб 5+ нод. На 3 нодах:

- **MON quorum** требует 3 живых MON для надёжности. На 3-нодовом кластере отказ любой ноды = потеря MON → пограничное состояние.
- **OSD recovery** при отказе ноды ресинкается на оставшиеся 2 ноды → внезапная нагрузка, риск второго отказа.
- **Erasure coding** (k+m) экономит место vs replication, но требует ещё больше нод (минимум 4-5).

С 3 нодами CEPH работает, но living-on-the-edge. LINSTOR на 3 нодах живёт штатно, для него это normal scale.

### 2.3 Resource overhead

| Ресурс | LINSTOR/DRBD на 3 нодах | CEPH на 3 нодах |
|---|---|---|
| RAM на ноду | ZFS ARC ~8 ГБ + LINSTOR ~1 ГБ = **~9 ГБ** | OSD ~4 ГБ × 4 + MON ~2 ГБ + MGR ~1 ГБ = **~19 ГБ** |
| CPU baseline | низкая | средняя (RADOS+scrub непрерывно) |
| Network usage | только при writes/recovery | continuous (heartbeats, scrub, balancing) |

На нашем железе (62-128 ГБ RAM на ноду) разница не катастрофическая, но 10 ГБ RAM, отнятые у VM, — это минус 5-10 VM с 1-2 ГБ RAM каждая.

### 2.4 Latency

На NVMe + 25 GbE ожидаемые цифры (грубо, по данным сообщества и benchmarks):

| Операция | LINSTOR/DRBD Proto C | CEPH RBD replicated |
|---|---|---|
| Random 4K write | ~200-400 µs | ~1-2 ms |
| Random 4K read | ~50-100 µs | ~200-500 µs |
| Sequential write | ~1.5+ GB/s | ~700-1000 MB/s |

CEPH оверхед — RADOS + CRUSH lookup + журнал + сериализация через primary OSD. Для 10k RPS на Postgres это 5-10x более медленный write commit. Под наш SLO (zero RPO БД) latency матерится.

### 2.5 Operational complexity

CEPH — большой стек:
- 3-4 типа сервисов (MON, OSD, MGR, [MDS/RGW])
- Своя терминология (PG, CRUSH map, pools, profiles)
- `ceph` CLI с десятками подкоманд
- Recovery procedures сложные (PG inconsistent, OSD slow, MON quorum lost)
- Tuning: PG count, CRUSH rules, BlueStore options, cache tier

LINSTOR проще:
- 2 типа сервисов (controller, satellite)
- Прямая модель «node → storage pool → resource → DRBD volume»
- DRBD low-level CLI хорошо документирован, существует с 2001 года
- Recovery: split-brain handling документирован чётко, есть готовые скрипты

Для команды без выделенного storage-инженера CEPH — overhead в обучении и эксплуатации.

---

## 3. Чем CEPH сильнее LINSTOR (где он действительно лучше)

Чтобы не было однобокой картины:

| Фича | CEPH | LINSTOR/DRBD |
|---|---|---|
| Object storage (S3) | ✅ нативно через RGW | ❌ нет (нужен MinIO поверх) |
| CephFS (POSIX shared FS) | ✅ нативно | ❌ только через DRBD + cluster FS (gfs2/ocfs2) |
| Erasure coding | ✅ k+m профили, экономия места | ❌ только replication |
| Масштабирование на 50+ нод | ✅ родная стихия | ⚠️ ограничения на per-resource scaling |
| Geo-replication (multi-DC) | ✅ multi-site RGW, RBD mirroring | ⚠️ через DRBD-A в long-distance, но clunky |
| Auto-rebalance при добавлении дисков | ✅ автоматический | ⚠️ через ZFS rebalance + LINSTOR migrate |
| Multi-tenancy | ✅ pools с квотами | ⚠️ через ZFS-level quotas |

---

## 4. Триггеры для пересмотра решения

Возвращаться к CEPH стоит, если выполнено **хотя бы одно** из:

### 4.1 Кластер вырос до 5+ нод

При 5 нодах CEPH становится «безопасным»: replica=3 не требует жертвы половиной полезной ёмкости (33% на 5 нод vs 33% на 3 нод — но ESI overhead меньше при scaling), MON quorum имеет запас, OSD recovery более распределён.

### 4.2 Нужен S3-совместимый object storage

Если в K8s появляются нагрузки, требующие S3 (MinIO как замена, бэкапы Velero, container registry, ML-датасеты, медиа-файлы) — CEPH через RGW даёт нативный S3 без отдельного слоя. Альтернатива: MinIO в K8s поверх PVC (LINSTOR).

### 4.3 Нужна geo-репликация storage между DC

CEPH RBD mirroring + multi-site RGW — индустриальный стандарт для multi-DC. DRBD тоже умеет (Proto A long-distance), но управление сложнее.

### 4.4 Нужен POSIX shared filesystem из multiple VM

CephFS позволяет нескольким VM монтировать одну ФС с full POSIX semantics. Use case: shared media, build artifacts, configuration.

С DRBD это возможно через cluster FS (gfs2/ocfs2 поверх DRBD multi-primary), но настройка хрупкая.

### 4.5 Нагрузка такая, что DRBD-resync становится узким местом

При 100+ TB томов и частых node-restart'ах full DRBD resync 100 TB по 25 GbE = 9+ часов. CEPH ресинкается только delta'ой (per-object), что в среднем быстрее.

---

## 5. Если возвращаемся — варианты миграции

### 5.1 In-place migration (НЕ рекомендуется)

Установить CEPH рядом с LINSTOR, постепенно мигрировать VM. Сложно: оба storage-стека конкурируют за RAM/CPU, нужно дополнительное место под параллельные копии.

### 5.2 Параллельный новый кластер

Развернуть 5-нодовый кластер с CEPH с нуля, мигрировать VM через snapshot + send/receive. Чище, но требует двойного железа на время миграции.

### 5.3 Гибрид: CEPH для object storage, LINSTOR для VM

Установить CEPH только с RGW и MON/OSD, использовать его исключительно через S3-API из K8s (для бэкапов, MinIO-replacement). VM block storage остаётся на LINSTOR.

Это разумный пошаговый путь: добавляем то, что нужно, не ломая работающее.

---

## 6. Если хочется проверить CEPH перед решением

Proxmox имеет встроенный CEPH-installer:

```bash
# Web UI: Datacenter → Ceph → Install Ceph
# Выбрать версию (e.g., reef или squid)
# Install на каждой ноде
# Создать MON на 3 нодах
# Создать OSD на каждом диске
# Создать pool "test" с size=2/min_size=1 (чисто для теста, НЕ для prod)
# Создать RBD pool в Proxmox storage
# Создать тестовую VM на этом pool'е
# Прогнать fio benchmark, сравнить с LINSTOR
```

**Не используй этот тестовый CEPH в production.** size=2/min_size=1 — небезопасно, MON на 3 нодах с шатким кворумом — рискованно.

Полноценный CEPH-deploy требует отдельной планировки: количество PG, CRUSH-rules с failure domains, BlueStore tuning, отдельные NVMe для WAL/DB (или совмещение).

---

## 7. Полезные ссылки

- Документация CEPH: https://docs.ceph.com/
- Proxmox + CEPH: https://pve.proxmox.com/wiki/Deploy_Hyper-Converged_Ceph_Cluster
- Почему size=2 опасно: https://docs.ceph.com/en/latest/rados/operations/pools/#set-the-number-of-object-replicas
- LINSTOR vs CEPH benchmark (LINBIT): https://linbit.com/blog/linstor-vs-ceph-benchmark/
  *(Ссылка от вендора LINSTOR — учитывать bias.)*
- Comparison from neutral perspective (поищи актуальные benchmarks за 2024-2025).

---

## 8. Краткое резюме

| Аспект | Сейчас (LINSTOR) | Если вернуться к CEPH |
|---|---|---|
| Что выиграем | — | S3, CephFS, лучше для 5+ нод, geo-replication |
| Что потеряем | — | Производительность 5-10x, простоту, ~10 ГБ RAM/нод, полезную ёмкость 50% → 33% |
| Когда триггер | — | 5+ нод, или S3 потребность, или multi-DC |
| Сложность миграции | — | Высокая (новый кластер или гибрид) |
| Риск | — | Низкий, если строим параллельно; высокий при in-place |

**Решение:** не делать ничего, продолжать с LINSTOR. Пересматривать раз в квартал, проверяя 5 триггеров из §4.

---

См. также: [05-storage-comparison.md](05-storage-comparison.md) для расширенной таблицы сравнения.

# Storage Architecture — Deep Dive

Теоретическая подложка под выбранный стек: **ZFS + DRBD + LINSTOR**. Разобрано всё: алгоритмы, сценарии отказа, квалификации компонентов, итоговое решение.

---

## 0. Контекст железа

| Параметр | Значение |
|---|---|
| BareMetal узлов | 3 |
| SSD на узел | 4 × 4 ТБ NVMe |
| Сырой объём диска (всего) | 48 ТБ |
| RAM на узел | 128 ГБ |
| Сеть между узлами | 25 Gbit |
| K8s VM | 5 на каждый BareMetal = 15 узлов K8s суммарно |
| Workload | Postgres 5 ТБ / 1000 rps · ClickHouse 2 ТБ · Kafka · NATS |
| Репликация на уровне приложений | НЕ делаем |
| Репликация на уровне storage | ОБЯЗАТЕЛЬНА |
| Простой | Допустим |
| Скорость | Максимальная, близкая к bare metal |
| Бэкапы | Отдельный S3 |

---

## 1. ZFS RAIDZ1 — как parity распределён

### 1.1 Уровни ZFS

| Классический RAID | ZFS термин | Parity дисков | Usable из 4×4TB |
|---|---|---|---|
| RAID-0 | stripe | 0 | 16 TB (риск 100%) |
| RAID-1 | mirror | копия | 4 TB (1 пара) |
| RAID-10 | striped mirrors | копии | 8 TB (2 пары) |
| **RAID-5** | **RAIDZ1** | **1** | **12 TB** |
| RAID-6 | RAIDZ2 | 2 | 8 TB |
| RAID-7 | RAIDZ3 | 3 | 4 TB |

### 1.2 Создание RAIDZ1 на 4 SSD

```bash
zpool create -o ashift=12 \
  -O compression=lz4 \
  -O atime=off \
  -O xattr=sa \
  -O recordsize=128K \
  tank \
  raidz1 /dev/disk/by-id/nvme-SSD1 \
         /dev/disk/by-id/nvme-SSD2 \
         /dev/disk/by-id/nvme-SSD3 \
         /dev/disk/by-id/nvme-SSD4
```

> **ВАЖНО**: всегда используй `/dev/disk/by-id/...`, никогда `/dev/sda` — после reboot буквы могут перетасоваться.

### 1.3 Миф vs реальность parity

**❌ Миф (как RAID-3):**
> «3 диска под данные, 1 диск под parity»

**✅ Реальность RAIDZ1 (как RAID-5):**
> Parity **распределён** между всеми 4 дисками. Каждый диск содержит и данные, и parity для разных блоков. Просто из 4 дисков ёмкость 1-го «съедается» под parity суммарно.

### 1.4 Как это выглядит физически

Записываешь блок 12 КБ — ZFS режет его на 3 куска по 4 КБ + считает parity 4 КБ:

```
Stripe 1 (запись блока A):
  nvme0:  A1     ← данные
  nvme1:  A2     ← данные
  nvme2:  A3     ← данные
  nvme3:  P(A)   ← parity = A1 XOR A2 XOR A3

Stripe 2 (запись блока B):
  nvme0:  B1     ← данные
  nvme1:  B2     ← данные
  nvme2:  P(B)   ← parity ← на ДРУГОМ диске!
  nvme3:  B3     ← данные

Stripe 3 (запись блока C):
  nvme0:  C1
  nvme1:  P(C)   ← parity на nvme1
  nvme2:  C2
  nvme3:  C3
```

Parity «гуляет» по дискам ротацией. На каждом диске лежит ~25% parity и ~75% данных.

**Зачем так?** Если бы parity был на одном диске — он бы был самым нагруженным и износился бы первым. RAIDZ1 распределяет нагрузку равномерно.

### 1.5 Восстановление через XOR

Если nvme0 умирает:
- Для stripe 1: A1 = A2 XOR A3 XOR P(A) — восстанавливаем из остальных
- Для stripe 2: B1 = B2 XOR B3 XOR P(B) — то же самое

Это работает **независимо от того, какой именно диск умер**. С точки зрения RAIDZ1 **все диски равнозначны**.

### 1.6 Производительность RAIDZ1 vs Mirror

| Метрика | Mirror (RAID-10) | RAIDZ1 |
|---|---|---|
| **Random write IOPS** | IOPS × N (каждая mirror pair независима) | **IOPS одного диска** ⚠️ |
| **Random read IOPS** | IOPS × N (читает с любого диска пары) | IOPS × (N-1) — может читать параллельно |
| **Sequential write** | ~2× single SSD | ~3× single SSD (parity overhead) |
| **Sequential read** | ~3-4× single SSD | ~3× single SSD |
| **Resilver time** | Минуты (копирует только пару) | Часы (читает весь pool) |
| **Write amplification** | ×2 | ~×1.33 (parity), но padding съедает выгоду |
| **Расширение pool** | Добавить vdev (mirror pair) — легко | Только пересоздать pool ⚠️ |

### 1.7 Tuning под Postgres / ClickHouse

```bash
# Datasets под разные workload
zfs create -o recordsize=16K  -o logbias=throughput tank/postgres
zfs create -o recordsize=128K -o logbias=throughput tank/clickhouse
zfs create -o recordsize=128K tank/kafka

# Postgres zvol под DRBD
zfs create \
  -V 100G \
  -b 16K \                          # volblocksize = размер страницы Postgres × 2
  -o compression=lz4 \
  -o logbias=throughput \           # для DRBD-замиксованной нагрузки
  -o primarycache=metadata \        # Postgres сам кеширует — не дублируй в ARC
  -o sync=standard \                # НЕ ставь disabled, потеряешь данные
  tank/postgres-zvol
```

Для ClickHouse — `recordsize=128K` или больше, `primarycache=all`, `compression=zstd-3` (даёт ×3-5 степень сжатия на колоночных данных).

---

## 2. zpool — scope и сценарии отказа

### 2.1 Главное про zpool

> **ZFS работает ТОЛЬКО локально, на одной машине.** Никакой ZFS RAIDZ1 «по сети» не существует. zpool — это локальный объект, привязанный к физическим дискам **одного** хоста.

```
BareMetal_1                         BareMetal_2
├─ /dev/nvme0n1 (4TB) ─┐           ├─ /dev/nvme0n1 (4TB) ─┐
├─ /dev/nvme1n1 (4TB) ─┤           ├─ /dev/nvme1n1 (4TB) ─┤
├─ /dev/nvme2n1 (4TB) ─┤→ tank     ├─ /dev/nvme2n1 (4TB) ─┤→ tank
└─ /dev/nvme3n1 (4TB) ─┘ (RAIDZ1)  └─ /dev/nvme3n1 (4TB) ─┘ (RAIDZ1)
                       │                                  │
                       12 TB                              12 TB
                       (это два РАЗНЫХ пула,
                        у каждого своё имя в своём контексте)
```

`tank` на BareMetal_1 и `tank` на BareMetal_2 — это **разные** пулы. Связь между ними появится только когда сверху ляжет DRBD.

### 2.2 Структура zpool

```
pool "tank"
  └─ vdev "raidz1-0"           ← группа из 4 дисков с RAIDZ1 защитой
       ├─ nvme0n1
       ├─ nvme1n1
       ├─ nvme2n1
       └─ nvme3n1
```

- **pool** — логический контейнер
- **vdev** — единица отказоустойчивости. RAIDZ1 vdev переживает смерть одного диска **внутри этого vdev**
- ⚠️ Если **любой vdev** теряется целиком — теряется весь pool

### 2.3 Что внутри pool можно создавать

```
pool tank (12 TB usable из 16 TB raw)
  │
  ├─ Dataset (filesystem) tank/something
  │     └─ монтируется в /tank/something — обычная FS
  │
  └─ Zvol (block device) tank/postgres-vol
        └─ появляется как /dev/zvol/tank/postgres-vol
        └─ выглядит как блочное устройство — на нём можно сделать LVM, ext4, или
           отдать целиком в DRBD/k8s как PV
```

Для k8s/DRBD/Postgres используется **zvol**.

### 2.4 Сценарии отказа на одном узле (ZFS уровень)

| Сценарий | Состояние pool | Что делаешь |
|---|---|---|
| **Умер 1 любой диск из 4** | `DEGRADED`, данные доступны для чтения и записи | Заменить физически → `zpool replace tank /dev/disk/by-id/OLD-ID /dev/disk/by-id/NEW-ID` → ждать resilver (1-3 часа). Postgres продолжает работать |
| **«Умер диск с контрольными суммами»** | Такого сценария нет | Parity размазан. Какой бы диск ни умер — действие одно (см. строку выше) |
| **Умерли 2 диска одновременно** | `FAULTED`, pool потерян | Локально не восстановить. Спасение — только сетевая реплика (DRBD) или бэкап (S3) |
| **Умер весь BareMetal (мать сгорела, диски целы)** | Pool на дисках жив | Переставить 4 диска в новый сервер → `zpool import tank` (или `zpool import -f tank`). Pool сразу доступен |
| **Умер весь BareMetal (диски тоже сгорели)** | Pool потерян целиком | RAIDZ1 от этого не защитит. Только реплика с другого узла или бэкап |

### 2.5 Resilver — что происходит во время

После `zpool replace`:
- Pool работает, Postgres пишет/читает
- Производительность ~30-50% от нормы (диски заняты восстановлением)
- ⚠️ Если умрёт **второй** диск во время resilver — **pool потерян**. Это главный риск RAIDZ1

---

## 3. DRBD — сетевая репликация

### 3.1 Зачем нужен поверх ZFS

ZFS RAIDZ1 для cross-node репликации **физически не предназначен** — он работает на уровне локальных block devices, не имеет представления о сети.

DRBD (Distributed Replicated Block Device) — это **синхронная блочная репликация** между узлами по сети. Берёт два zvol на разных машинах и делает из них «зеркало».

### 3.2 Архитектура

```
BareMetal_1                              BareMetal_2

zpool tank (RAIDZ1)                      zpool tank (RAIDZ1)
  │                                        │
  └─ zvol tank/pg-vol (100GB)               └─ zvol tank/pg-vol (100GB)
        │                                          │
        └─→ /dev/zd0                              └─→ /dev/zd0
              │                                          │
              └─ DRBD resource "pg-vol"  ◄═══TCP═══►  DRBD resource "pg-vol"
                       │ (PRIMARY)                            (SECONDARY)
                       │
                       └─→ /dev/drbd0
                             │
                             └─→ k8s PV → Postgres pod
```

### 3.3 Принцип работы

- На обоих узлах создаётся zvol **одинакового размера**
- DRBD делает их «зеркалом» друг друга по сети
- Один узел = PRIMARY (на него пишет приложение), второй = SECONDARY (получает копию)
- Каждая запись в `/dev/drbd0` идёт **синхронно** на оба zvol — Postgres получает `ack` только когда ОБА подтвердили запись (DRBD протокол C)
- При смерти PRIMARY — SECONDARY делается PRIMARY (через LINSTOR), Postgres перезапускается

### 3.4 Latency на запись 4 КБ

```
Postgres pod на BM_1 (PRIMARY)
   │ write 4 KB
   ▼
DRBD primary
   │
   ├─→ ZFS zvol → SSD на BM_1                   [локально, ~150 µs]
   ├─→ TCP → DRBD secondary (BM_2)              [параллельно]
   │     └→ ZFS zvol → SSD на BM_2              [~150 µs]
   ◄── ack от secondary
   ▼
ack → Postgres
```

| Этап | Время |
|---|---|
| Локальная запись на BM_1 | ~150 µs |
| Сеть до secondary (RTT) | ~50 µs |
| Запись на secondary (параллельно) | ~150 µs |
| **Итого** | **~200-300 µs** |

**Read path** — полностью локальный: DRBD primary читает с локального ZFS, не лезет в сеть. Read latency ≈ raw SSD.

---

## 4. Split-brain и Quorum

### 4.1 Проблема split-brain

DRBD: «один PRIMARY пишет, второй SECONDARY повторяет». Что произойдёт, если узлы **перестанут видеть друг друга** (порвалась сеть, упал свитч), но при этом **оба узла живы**?

```
   нормальная работа:
   ┌─────────────┐      sync       ┌─────────────┐
   │ BM_1 PRIMARY│ ◄─────TCP─────► │BM_2 SECONDARY│
   │   Postgres  │                 │              │
   └─────────────┘                 └─────────────┘

   разрыв сети между узлами:
   ┌─────────────┐    ✗ ✗ ✗ ✗      ┌─────────────┐
   │ BM_1 PRIMARY│       ✗         │BM_2 SECONDARY│
   │   Postgres  │      разрыв     │              │
   └─────────────┘                 └─────────────┘
```

С точки зрения BM_2: «BM_1 умер, я должен стать PRIMARY».
С точки зрения BM_1: «BM_2 умер, я продолжаю как PRIMARY».

**Результат — оба узла стали PRIMARY одновременно.** Split-brain.

### 4.2 Что ломается

```
Время T+0 (разрыв сети)
   BM_1: Postgres пишет транзакцию X → принимает запись
   BM_2: новый Postgres pod стартует, пишет транзакцию Y → принимает запись

Время T+5 минут (сеть восстановилась)
   BM_1 zvol содержит: ..., X
   BM_2 zvol содержит: ..., Y
   ОНИ РАЗОШЛИСЬ. Какая версия правильная?
```

**Необратимая потеря данных.** Нужно вручную выбрать одну сторону и **выкинуть** изменения с другой стороны.

### 4.3 Почему 2 узла **не могут** решить проблему сами

> **Узел не может отличить «другой узел умер» от «сеть до другого узла порвалась».**

Это математически невозможно. В системе из 2 узлов нет правильного решения:
- Если оба остановят сервис при разрыве → каждый разрыв сети = downtime
- Если оба продолжат работать → split-brain

### 4.4 Решение — Quorum (большинство голосов)

Добавляем **третий голос**. Тогда любая «сторона разрыва» может посчитать голоса и определить — «я в большинстве» или «я в меньшинстве».

**Правило**: операции на запись разрешены только тому узлу, который видит **строгое большинство** голосов (для 3 узлов = 2 голоса).

```
3 узла, разрыв сети между BM_1 и {BM_2, BM_3}:

   ┌─────────────┐       ✗      ┌─────────────┐
   │ BM_1        │       ✗      │ BM_2        │ ◄── видит BM_3
   │ видит: я    │              │ видит: я+BM_3│
   │ голосов: 1  │              │ голосов: 2  │
   │ из 3        │              │ из 3        │
   └─────────────┘              └─────────────┘
                                       │
                                ┌─────────────┐
                                │ BM_3        │
                                │ видит: я+BM_2│
                                │ голосов: 2  │
                                │ из 3        │
                                └─────────────┘

   Решение:
   - BM_1 (1/3 — меньшинство) → ОСТАНАВЛИВАЕТ записи, переходит в read-only
   - BM_2 + BM_3 (2/3 — большинство) → продолжают работать
   - Когда сеть восстановится — BM_1 догоняет данные с BM_2/BM_3
```

Двух «большинств» не бывает. Меньшинство **знает**, что оно в меньшинстве, и блокирует запись само.

### 4.5 Diskless witness — оптимизация

Для quorum **достаточно голоса**, не обязательно полной копии данных.

При replica=2 (не хочется тратить место на 3-ю копию: 5 ТБ Postgres × 3 = 15 ТБ vs × 2 = 10 ТБ), но нужен 3-й голос → **diskless witness**.

```
BM_1: zvol pg-vol (100 GB реальные данные) + DRBD
BM_2: zvol pg-vol (100 GB реальная копия)   + DRBD
BM_3: diskless                               + DRBD (только meta + голос, без данных)
```

BM_3 знает «состояние» репликации (метаданные, кто primary, кто secondary), но **не хранит ни байта реальных данных**.

### 4.6 Per-volume layout у нас (3 BareMetal, replica=2 + witness)

```
vol-postgres:
  data replica на BM_1 + BM_2
  diskless witness на BM_3

vol-clickhouse:
  data replica на BM_1 + BM_3
  diskless witness на BM_2

vol-kafka:
  data replica на BM_2 + BM_3
  diskless witness на BM_1
```

LINSTOR делает это автоматически — auto-placement балансирует данные по узлам.

### 4.7 Сценарии отказа с quorum (3 узла, replica=2 + witness)

| Что сломалось | Голоса | Что происходит | Что ты делаешь |
|---|---|---|---|
| **Сеть от BM_1 к остальным (BM_1 изолирован)** | BM_1: 1/3, остальные: 2/3 | BM_1 уходит в read-only, реплики на BM_2 принимают запись (где BM_2 был secondary — становится primary) | Чинишь сеть. После восстановления BM_1 ловит изменения и догоняет |
| **Сеть рассыпалась полностью (все в изоляции)** | каждый: 1/3 | **ВСЕ узлы read-only** — никто не может стать primary | Чинишь сеть. После — кластер сам восстанавливается, тот узел который был primary остаётся primary |
| **Умер BM_1 (был primary для vol-postgres)** | BM_2+BM_3: 2/3, BM_1: 0/3 | Quorum есть → vol-postgres делает failover BM_2 в primary. Postgres pod перезапускается на BM_2. Запись идёт | Заменяешь BM_1, LINSTOR пересоздаёт реплику на новом BM_1 (или другом узле) |
| **Умер BM_3 (был witness для vol-postgres)** | BM_1+BM_2: 2/3 | Quorum сохранён, vol-postgres работает как ни в чём не бывало | Заменяешь BM_3, LINSTOR пересоздаёт witness |
| **Умерли BM_1 + BM_2 одновременно (на vol-postgres лежат именно там реальные данные)** | BM_3: 1/3 | **Данные vol-postgres потеряны** — обе реплики физически недоступны | Восстанавливаешь из S3 бэкапа |
| **Умерли BM_1 + BM_3 одновременно** | BM_2: 1/3 | Quorum нет → BM_2 уходит в read-only несмотря на то что **данные у него есть** | Это самое неприятное. Варианты: (a) дождаться восстановления любого из упавших узлов, (b) **вручную force quorum** на BM_2 (`drbdadm --force` через LINSTOR) — admin-решение, понимая что split-brain невозможен потому что других живых узлов нет |

⚠️ Последний сценарий — единственная ситуация когда «данные есть, но кластер их не отдаёт». Это плата за защиту от split-brain. Лучше пара минут downtime + ручное вмешательство, чем повреждение данных.

### 4.8 С quorum vs без quorum

| Сценарий | Без quorum (просто 2 узла) | С quorum (3 узла) |
|---|---|---|
| Сеть порвалась между узлами | **Split-brain → расхождение данных → ручное восстановление с потерями** | Меньшинство read-only, большинство работает, после починки всё само сходится |
| Узел умер | Failover работает, но если сеть была подозрительной → риск split-brain | Failover работает чисто, без риска |
| Оба узла живы и видят друг друга | OK | OK |
| Производительность записи | Sync на 1 партнёра | Sync на 1 партнёра (witness не участвует в записи данных, только meta) |

**Цена quorum**: 3-й узел в кластере. У нас он уже есть → **получаем бесплатно**.

---

## 5. LINSTOR — зачем нужен

### 5.1 Что без LINSTOR

ZFS + DRBD технически работают **без** LINSTOR — это два независимых инструмента. Но тогда **ВСЁ делается руками**.

#### Сценарий без LINSTOR: создать PVC на 100 GB для Postgres

```bash
# На BM_1:
zfs create -V 100G -b 16K tank/pg-postgres-vol

# На BM_2:
zfs create -V 100G -b 16K tank/pg-postgres-vol

# На BM_3:
# (тут witness — особая конфигурация DRBD без диска)

# На каждом узле создаём DRBD конфиг руками:
cat > /etc/drbd.d/pg-postgres.res <<EOF
resource pg-postgres {
  protocol C;
  on bm-1 {
    device    /dev/drbd0;
    disk      /dev/zvol/tank/pg-postgres-vol;
    address   10.0.0.1:7788;
    meta-disk internal;
  }
  on bm-2 {
    device    /dev/drbd0;
    disk      /dev/zvol/tank/pg-postgres-vol;
    address   10.0.0.2:7788;
    meta-disk internal;
  }
  on bm-3 {
    device    /dev/drbd0;
    disk      none;     # diskless witness
    address   10.0.0.3:7788;
    meta-disk internal;
  }
}
EOF

# На BM_1 (выбираем как initial primary):
drbdadm create-md pg-postgres
drbdadm up pg-postgres
drbdadm primary --force pg-postgres

# На BM_2, BM_3:
drbdadm create-md pg-postgres
drbdadm up pg-postgres

# Ждать пока initial sync завершится (часы)
drbdadm status pg-postgres

# Самостоятельно создать PV манифест:
cat > pv.yaml <<EOF
apiVersion: v1
kind: PersistentVolume
metadata:
  name: pv-pg-postgres
spec:
  capacity: { storage: 100Gi }
  accessModes: [ReadWriteOnce]
  hostPath: { path: /dev/drbd0 }       # ⚠️ привязан к BM_1 через nodeAffinity
  ...
EOF
```

**И это для одного volume.** Volume'ов будет 20+.

#### Сценарий без LINSTOR: BM_1 умер, нужен failover

```bash
# 1. Узнать что BM_1 действительно умер (alerting?)
# 2. Зайти на BM_2:
drbdadm primary --force pg-postgres   # принудительно делаем primary

# 3. Зайти на k8s control plane:
kubectl edit pv pv-pg-postgres
# изменить nodeAffinity с BM_1 на BM_2

# 4. Убить Postgres pod:
kubectl delete pod postgres-0 --force --grace-period=0

# 5. Pod перезапускается, k8s выбирает BM_2 (по affinity), Postgres стартует
```

**Каждый failover — ручная процедура из 4-5 шагов с риском человеческой ошибки.**

### 5.2 Что делает LINSTOR

LINSTOR — **control plane** над ZFS+DRBD. По сути «оркестратор хранилища» — то же что Kubernetes для контейнеров.

```
Уровни:

┌──────────────────────────────────────────────────┐
│  Kubernetes API + LINSTOR CSI driver             │  ← k8s видит обычный StorageClass
├──────────────────────────────────────────────────┤
│  LINSTOR Controller (Operator) — control plane   │  ← живёт как Deployment в k8s
│  - принимает заявки                              │
│  - выбирает на каких узлах разместить replicas   │
│  - управляет жизненным циклом ресурсов           │
├──────────────────────────────────────────────────┤
│  LINSTOR Satellite — agent на каждом BareMetal   │
│  - выполняет команды zfs / drbdadm локально      │
│  - репортит состояние обратно                    │
├──────────────────────────────────────────────────┤
│  ZFS + DRBD — то что ты уже знаешь               │
└──────────────────────────────────────────────────┘
```

#### Тот же сценарий с LINSTOR

```yaml
# Создаёшь StorageClass один раз:
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: linstor-replica2
parameters:
  storagePool: zfs-tank        # имя ZFS pool на узлах
  autoPlace: "2"               # 2 реплики данных
  diskless: "1"                # +1 diskless witness
provisioner: linstor.csi.linbit.com
```

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: postgres-data
spec:
  storageClassName: linstor-replica2
  accessModes: [ReadWriteOnce]
  resources:
    requests: { storage: 100Gi }
```

LINSTOR **сам**:
1. Выбирает 2 узла для данных + 1 для witness (по auto-placement политике)
2. Создаёт zvol на каждом
3. Генерирует DRBD конфиг
4. Запускает initial sync
5. Создаёт PV в k8s с правильным nodeAffinity
6. Когда pod стартует на узле — DRBD primary автоматически переезжает туда

### 5.3 LINSTOR vs ручное управление

| Операция | Без LINSTOR | С LINSTOR |
|---|---|---|
| Создать volume | ~10 команд × 3 узла = 30 шагов | `kubectl apply pvc.yaml` |
| Удалить volume | Удалить DRBD config + zfs destroy на всех узлах | `kubectl delete pvc` |
| Failover при смерти узла | Вручную, 4-5 команд | Автоматически, в течение секунд |
| Resize volume | Сложная ручная процедура (zfs + DRBD + filesystem) | Изменить `requests.storage` в PVC |
| Auto-placement | Сам выбираешь на каких узлах | LINSTOR балансирует по свободному месту, нагрузке |
| Snapshot | `zfs snapshot` на каждом узле + координация | `VolumeSnapshot` в k8s |
| Восстановление после смерти узла | Заново создать reources на новом узле | Автомат: LINSTOR пересоздаёт реплику на оставшемся узле |
| Quorum management | Руками править DRBD config | LINSTOR настраивает quorum политику |
| Интеграция с k8s | Вручную писать PV манифесты с hostPath | CSI driver — стандартные PVC |

---

## 6. Итоговая mental model

> **ZFS** = «как хранить данные надёжно на одной машине»
> **DRBD** = «как держать копию на другой машине синхронно»
> **Quorum** = «как не сломать данные при сетевых проблемах»
> **LINSTOR** = «кто-то должен всем этим командовать вместо тебя»

```
┌─────────────────────────────────────────────────────────┐
│                                                         │
│  ZFS         — локальная защита (RAIDZ1, snapshots)     │
│  DRBD        — сетевая репликация (replica=2)           │
│  Quorum      — защита от split-brain (3-й голос)        │
│  LINSTOR     — оркестрация (избавляет от ручной работы) │
│  CSI driver  — k8s интеграция (PVC «как обычно»)        │
│                                                         │
└─────────────────────────────────────────────────────────┘

Результат:
- разработчик пишет PVC «как обычно» в k8s
- LINSTOR сам всё настраивает на 3 узлах
- При падении 1 диска → ZFS resilver
- При падении 1 узла → DRBD failover через LINSTOR
- При сетевом разрыве → quorum предотвращает split-brain
```

---

## 7. Полное решение для нашего сетапа

```
┌──────────────────────────────────────────────────────────────────┐
│ BareMetal_1                          BareMetal_2                 │
│                                                                  │
│ 4 × NVMe SSD (4TB each)              4 × NVMe SSD (4TB each)     │
│         │                                    │                   │
│         ▼                                    ▼                   │
│ ┌──────────────────┐                 ┌──────────────────┐        │
│ │ zpool tank       │                 │ zpool tank       │        │
│ │ RAIDZ1 (12TB)    │                 │ RAIDZ1 (12TB)    │        │
│ │                  │                 │                  │        │
│ │ ┌──────────────┐ │                 │ ┌──────────────┐ │        │
│ │ │ zvol pg-vol  │ │ ◄──DRBD sync──► │ │ zvol pg-vol  │ │        │
│ │ │ 100 GB       │ │                 │ │ 100 GB       │ │        │
│ │ └──────────────┘ │                 │ └──────────────┘ │        │
│ │       │          │                 │       │          │        │
│ │ ┌──────────────┐ │                 │ ┌──────────────┐ │        │
│ │ │ zvol ch-vol  │ │ ◄──DRBD sync──► │ │ zvol ch-vol  │ │        │
│ │ │ 2 TB         │ │                 │ │ 2 TB         │ │        │
│ │ └──────────────┘ │                 │ └──────────────┘ │        │
│ └──────────────────┘                 └──────────────────┘        │
│         │                                    │                   │
│         └─ /dev/drbd0 (PRIMARY) ──── /dev/drbd0 (SECONDARY) ─    │
│              │                                                   │
│              └─→ k8s PV → Postgres pod                           │
│                                                                  │
│  + BareMetal_3 (diskless witness для quorum + replicas для       │
│    других volumes по auto-placement)                             │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### 7.1 Capacity math

| Layer | Per node | На 3 узла | После replica=2 (DRBD) |
|---|---|---|---|
| Raw SSD | 16 TB | 48 TB | — |
| После RAIDZ1 (-25%) | 12 TB | 36 TB | — |
| Usable для replicated volumes | — | — | **18 TB** |

Workload ~10 TB (5 PG + 2 CH + Kafka + остальное) → запас ~8 TB.

### 7.2 Цели достигнуты

> **«Я могу потерять 1 любой диск, и всё будет окей»**

✅ ZFS RAIDZ1 — pool в DEGRADED, работает, заменяешь и делаешь resilver

> **«Я могу потерять 1 BareMetal, и всё будет окей»**

✅ DRBD — failover на secondary, данные доступны на втором узле, арендуешь новый и DRBD ресинхронизирует

### 7.3 Полная сводка сценариев отказа

| Что сломалось | Что происходит | Что ты делаешь |
|---|---|---|
| 1 диск на BareMetal_1 | ZFS pool на BM_1 → DEGRADED. DRBD не замечает (zvol работает). Postgres работает | `zpool replace`, ждёшь resilver. Postgres продолжает работать |
| 2 диска на BareMetal_1 | ZFS pool на BM_1 → FAULTED. DRBD теряет primary | DRBD автоматически делает SECONDARY на BM_2 → PRIMARY. Postgres pod перезапускается на BM_2. Восстанавливаешь BM_1, заново синхронизируешь DRBD |
| Сгорел BareMetal_1 целиком (диски тоже) | То же что 2 диска — DRBD failover на BM_2 | Арендуешь новый BareMetal, ставишь ZFS, делаешь pool, LINSTOR создаёт новый replica на нём, DRBD заново синхронизирует данные с BM_2 → BM_NEW. **Ничего не потеряно** |
| Сгорели BareMetal_1 и BareMetal_2 одновременно | Данные потеряны на обоих узлах с репликами | Восстанавливаешь из S3 бэкапа. RPO = интервал бэкапа |
| Сетевой разрыв между BM_1 и (BM_2+BM_3) | BM_1 в меньшинстве (1/3) → read-only. BM_2+BM_3 (2/3) работают | Чинишь сеть. После восстановления BM_1 догоняет |
| Полный сетевой разрыв (все изолированы) | Все узлы в read-only (никто не имеет quorum) | Чинишь сеть. Кластер восстанавливается сам |

---

## 8. Дальнейшие темы (на проработку)

1. **Конкретный deploy LINSTOR в k8s** — какие операторы использовать (Piraeus), какие CRD создавать, как интегрируется в ArgoCD-подход
2. **Auto-placement политики** — как сказать LINSTOR «postgres держи на быстрых узлах», «kafka раскидывай равномерно», «не ставь две реплики на один rack»
3. **DRBD протоколы A/B/C** — sync vs async tradeoff. Для Postgres надо C (full sync), для ClickHouse можно подумать о B
4. **Детальный сценарий восстановления убитого узла** — пошагово что делать, какие команды, как LINSTOR ресинхронизирует
5. **Тюнинг Postgres под ZFS** (`full_page_writes=off` — на ZFS это безопасно благодаря COW)
6. **Endurance расчёт SSD** под нагрузку Postgres + RAIDZ1 padding overhead

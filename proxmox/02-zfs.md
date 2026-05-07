# ZFS Layout & Tuning

ZFS в нашей инсталляции выполняет две роли:
1. **rpool** — корневая файловая система Proxmox (root + swap + boot)
2. **datapool** — backing storage для VM, под управлением LINSTOR

---

## 1. ZFS — концепции в 2 минуты

Если ты не работал с ZFS — вот минимум, чтобы понимать остальной документ.

| Термин | Что это |
|---|---|
| **pool** (zpool) | Логический контейнер, объединяющий диски. Один pool = одна файловая система пространства имён. |
| **vdev** | Virtual device — группа дисков внутри pool с определённой топологией (mirror, raidz, stripe). |
| **mirror vdev** | RAID-1: два или больше диска, одинаковые копии. Выживает при отказе всех кроме одного. |
| **stripe vdev** | RAID-0 (один диск или несколько без избыточности). Pool из stripe vdevs = «JBOD-like». |
| **raidz1/2/3** | Аналог RAID-5/6/triple-parity. Не подходит для VM-нагрузки (плохой random IO). |
| **dataset** | Файловая система внутри pool. Иерархическая: `pool/foo/bar`. Имеет свои свойства (compression, sync, recordsize). |
| **zvol** | Блочное устройство внутри pool. Появляется в `/dev/zvol/<pool>/<name>`. Используется LINSTOR для VM. |
| **ARC** | Adaptive Replacement Cache — read-кэш в RAM. По умолчанию занимает до 50% RAM. |
| **ZIL** | ZFS Intent Log — журнал sync-writes. Может быть на отдельном устройстве (SLOG) или внутри pool. |
| **txg** | Transaction group — группа отложенных writes, коммитится на диск каждые ~5 секунд. |

**Ключевая инвариантность ZFS:** copy-on-write. ZFS никогда не перезаписывает блок «на месте» — пишет в новое место и атомарно меняет указатель. Это даёт мгновенные снапшоты и защиту от torn writes.

---

## 2. Disk layout — Layout-2 (default)

См. [QUESTIONS.md](QUESTIONS.md) Q1, если хочешь Layout-1 вместо этого.

### 2.1 Партиционирование каждого NVMe

На каждом из 4 дисков создаём 2 партиции:

```
/dev/nvme0n1
├── /dev/nvme0n1p1 — 50 ГБ — Proxmox root (rpool member)
└── /dev/nvme0n1p2 — ~1.95 ТБ — VM storage (datapool member)
```

Команда (повторить для nvme1n1, nvme2n1, nvme3n1):

```bash
# Создаём партиции
parted /dev/nvme0n1 --script mklabel gpt
parted /dev/nvme0n1 --script mkpart primary 1MiB 50GiB
parted /dev/nvme0n1 --script mkpart primary 50GiB 100%

# Сообщаем ядру о новых партициях
partprobe /dev/nvme0n1

# Проверка
lsblk /dev/nvme0n1
```

**Если Proxmox уже установлен** провайдером — у тебя уже есть `nvme0n1p1` (BIOS/EFI) и `nvme0n1p2` (root). Тогда оставшиеся диски (`nvme1n1`, `nvme2n1`, `nvme3n1`) можно полностью отдать под `datapool`, и rpool останется на одном диске первого NVMe (single point of failure для OS — компромисс).

Эталонный сценарий из bootstrap-заметок:
```
sgdisk -n 0:0:0 -t 0:bf01 /dev/sda
# создать раздел на весь хвост диска с типом ZFS (bf01 = ZFS Solaris root)
partprobe /dev/sda
```

`-n 0:0:0` означает «следующий свободный номер партиции, от текущего конца до конца диска». Удобно для добавления партиции после уже существующих.

### 2.2 Создание rpool — Proxmox OS

```bash
# 4-way mirror — выживает при отказе 3 из 4 дисков
zpool create -f \
    -o ashift=12 \
    -O compression=lz4 \
    -O atime=off \
    -O xattr=sa \
    -O acltype=posixacl \
    -O mountpoint=none \
    rpool mirror \
        /dev/nvme0n1p1 \
        /dev/nvme1n1p1 \
        /dev/nvme2n1p1 \
        /dev/nvme3n1p1
```

**Ключи:**
- `ashift=12` — sector size 4K (4096 байт). Правильно для всех NVMe и большинства современных SSD. Поменять потом нельзя — если ошибся, перебилдить pool.
- `-O compression=lz4` — прозрачное сжатие, дешёвое (5-10% CPU), экономит 30-60% места на текстовых/конфигурационных данных.
- `-O atime=off` — не обновлять access-time при каждом read. Огромный win для производительности.
- `-O xattr=sa, acltype=posixacl` — POSIX-ACL и xattr хранятся в inode (быстрее).
- `-O mountpoint=none` — root pool не должен монтироваться в `/` напрямую (Proxmox installer обычно делает дочерний dataset для root).

**Если Proxmox уже установлен через ISO с ZFS-on-root**, rpool уже создан — просто проверь `zpool status rpool`. Не пересоздавай.

### 2.3 Создание datapool — VM storage

```bash
# Stripe pool — 4 vdev по одному диску, без избыточности на ZFS-уровне
zpool create -f \
    -o ashift=12 \
    -O compression=lz4 \
    -O atime=off \
    -O xattr=sa \
    -O acltype=posixacl \
    -O mountpoint=/datapool \
    datapool \
        /dev/nvme0n1p2 \
        /dev/nvme1n1p2 \
        /dev/nvme2n1p2 \
        /dev/nvme3n1p2
```

Здесь каждый диск становится **отдельным top-level vdev**. ZFS будет страйпить writes по всем 4 vdev'ам — получаем суммарный bandwidth ~4× одного NVMe.

**Важно:** этот pool НЕ имеет внутренней избыточности. Отказ одного диска = потеря всех данных pool'а. Это сознательное решение: избыточность мы делаем на уровне LINSTOR/DRBD между нодами.

**Проверка:**
```bash
zpool status datapool
zpool list
zfs list
```

Ожидаемое из `zpool status`:
```
  pool: datapool
 state: ONLINE
config:
        NAME            STATE     READ WRITE CKSUM
        datapool        ONLINE       0     0     0
          nvme0n1p2     ONLINE       0     0     0
          nvme1n1p2     ONLINE       0     0     0
          nvme2n1p2     ONLINE       0     0     0
          nvme3n1p2     ONLINE       0     0     0
```

Все 4 диска на одном уровне — это означает stripe.

### 2.4 Datasets для разных назначений

Создаём подпулы (datasets) для логического разделения:

```bash
# Под LINSTOR-управляемые тома
zfs create -o mountpoint=none datapool/linstor

# (опционально) Под Proxmox local storage для ISO / template
zfs create -o mountpoint=/datapool/templates datapool/templates
zfs create -o mountpoint=/datapool/iso datapool/iso

# Параметры специально под VM-zvol
zfs set recordsize=64K datapool/linstor
zfs set sync=standard datapool/linstor
zfs set primarycache=metadata datapool/linstor   # см. §5
zfs set redundant_metadata=most datapool/linstor # экономия места на zvol-метаданных
```

**Почему `recordsize=64K`** — это блок ZFS, в котором живут записи. Для VM-нагрузки 64K — компромисс между:
- 16K (random-IO friendly, плохо для последовательных)
- 128K (default, последовательные хороши, для random — write amplification)

Для смешанной нагрузки PG/Redis/Kafka — 64K лучший компромисс. Для специализированных PG-zvol можно переопределить на 16K (PG page size = 8K, 16K — два page'а).

---

## 3. Проверка zpool после создания

Базовый health check:

```bash
zpool status        # все pools, all vdevs ONLINE, нет ошибок
zpool list -v       # размеры, степень утилизации
zfs list -t all     # все datasets и snapshots
zpool iostat -v 1   # live-метрики IOPS/throughput
```

Periodic scrub (раз в 1-2 недели, можно через cron):

```bash
zpool scrub datapool
# Прогресс:
zpool status datapool
# Когда закончит:
# scan: scrub repaired 0B in 0:23:41 with 0 errors on ...
```

Scrub читает все блоки и проверяет checksum'ы — выявляет bit rot до того, как он попадёт в реплику DRBD. Стандартный systemd-timer есть в `zfsutils-linux`:

```bash
systemctl enable zfs-scrub-monthly@datapool.timer
systemctl enable zfs-scrub-monthly@rpool.timer
```

---

## 4. ARC tuning (RAM cache)

ZFS по умолчанию забирает до 50% RAM под ARC. Для ноды с 64 ГБ RAM это 32 ГБ — много для системы, на которой ещё крутятся VM.

**Рекомендация:** ограничить ARC до 8-16 ГБ на ноде с 64 ГБ RAM.

```bash
# Лимит ARC = 16 ГБ
echo "options zfs zfs_arc_max=17179869184" > /etc/modprobe.d/zfs.conf

# Минимум — 4 ГБ (чтобы не схлопывался под нагрузкой)
echo "options zfs zfs_arc_min=4294967296" >> /etc/modprobe.d/zfs.conf

# Применить — нужен перезапуск или
update-initramfs -u
# (для немедленного применения без ребута)
echo 17179869184 > /sys/module/zfs/parameters/zfs_arc_max
echo 4294967296  > /sys/module/zfs/parameters/zfs_arc_min
```

Текущее использование ARC:

```bash
cat /proc/spl/kstat/zfs/arcstats | grep -E "^(size|c_max|c_min) "
# size = текущий размер ARC
# c_max = верхний лимит
# c_min = нижний
```

Для ноды со 128 ГБ — можно поднять лимит до 24-32 ГБ.

---

## 5. ZFS sync property — критичное свойство

`sync` определяет, как ZFS реагирует на `fsync()` от приложения. См. [README.md](README.md) §архитектура и [QUESTIONS.md](QUESTIONS.md) Q3.

| Значение | Поведение | Когда применять |
|---|---|---|
| `standard` (default) | fsync → write через ZIL → ack только после flush. Без fsync — async. | **Default для VM-zvol под БД.** Postgres / Kafka делают fsync корректно. |
| `always` | Каждый write принудительно через ZIL, даже без fsync. | Параноидальный режим. Защита от багов в guest OS. +5-15% latency. |
| `disabled` | fsync игнорируется. Все writes async. | **НИКОГДА для production-БД.** Power loss = потеря последних 5 сек. |

```bash
# Узнать текущее значение
zfs get sync datapool/linstor

# Установить
zfs set sync=standard datapool/linstor
```

---

## 6. Compression — что выбрать

ZFS поддерживает несколько алгоритмов:

| Алгоритм | CPU | Ratio | Использовать |
|---|---|---|---|
| `off` | 0 | 1.0x | никогда — даже на инкомпрессибильных данных метаданные сжимаются |
| `lz4` | низко (5-10%) | 1.5-2x | **Default** — практически бесплатно |
| `zstd` (zstd-3) | средне | 2-3x | Логи, бэкапы |
| `zstd-fast-1` | очень низко | 1.5x | Альтернатива lz4, чуть лучше |
| `gzip-9` | высоко | 3-4x | Архивные данные, не для горячих VM |

**Рекомендация:** `lz4` для всего, `zstd` для специфичных datasets (например, бэкапы PG WAL):

```bash
zfs set compression=lz4 datapool                    # default для всего pool
zfs set compression=zstd datapool/templates         # ISO / template — выгода больше
```

Проверка эффективности:
```bash
zfs get compressratio datapool
# 1.45x = сжимаем в 1.45 раза
```

---

## 7. Snapshots — мгновенные точки восстановления

ZFS снапшоты бесплатны (copy-on-write) и атомарны. Для VM-zvol:

```bash
# Создать снапшот dataset'а
zfs snapshot datapool/linstor/vm-100-disk-0@before-upgrade

# Откатиться (УНИЧТОЖИТ всё после снапшота!)
zfs rollback datapool/linstor/vm-100-disk-0@before-upgrade

# Удалить снапшот
zfs destroy datapool/linstor/vm-100-disk-0@before-upgrade

# Список снапшотов
zfs list -t snapshot
```

LINSTOR умеет управлять ZFS-снапшотами через свой API — см. [03-linstor-and-drbd.md](03-linstor-and-drbd.md) §snapshots.

**Рекомендация:** автоматизация снапшотов через `zfs-auto-snapshot` или `sanoid`:

```bash
apt install sanoid
# /etc/sanoid/sanoid.conf:
#
# [datapool/linstor]
#   use_template = production
#
# [template_production]
#   frequently = 0
#   hourly = 36
#   daily = 30
#   monthly = 6
#   yearly = 0
#   autosnap = yes
#   autoprune = yes

systemctl enable --now sanoid.timer
```

---

## 8. Расширение pool — добавление дисков

Сценарий из задачи: «изначально 2 диска, потом добавили ещё 2».

**Если Layout-2 (stripe):**

```bash
# Партиционируем новые диски (см. §2.1)
parted /dev/nvme2n1 --script mklabel gpt
parted /dev/nvme2n1 --script mkpart primary 1MiB 50GiB
parted /dev/nvme2n1 --script mkpart primary 50GiB 100%
partprobe /dev/nvme2n1
# То же для nvme3n1

# Добавляем партиции в pool
zpool add -f datapool /dev/nvme2n1p2
zpool add -f datapool /dev/nvme3n1p2

# (Опционально) добавить в rpool как дополнительные mirror-копии
zpool attach -f rpool /dev/nvme0n1p1 /dev/nvme2n1p1
zpool attach -f rpool /dev/nvme0n1p1 /dev/nvme3n1p1

zpool status
```

После `zpool add`, новые vdev'ы пустые — старые данные **не переразмазываются** автоматически. ZFS постепенно балансирует при новых writes. Если хочется явно перебалансировать — пройтись `zfs send/receive` или просто подождать (естественная балансировка через write churn).

**Если Layout-1 (split pools):** добавляем в нужный pool, mirror-pool превращается в RAID-10:

```bash
# Расширение mirror pool — превращаем mirror-2 в mirror-2 + mirror-2 (RAID-10)
zpool add -f pool-mirror mirror /dev/nvme2n1p2 /dev/nvme3n1p2
```

---

## 9. Замена сбойного диска

```bash
# 1. Узнать имя сбойного диска
zpool status
# Например: nvme1n1p2 — DEGRADED (UNAVAIL)

# 2. Физически заменить диск (hot-swap или с выключением)
# Новый диск получит ту же или другую букву

# 3. Партиционировать новый диск (как в §2.1)

# 4. Заменить в pool
zpool replace datapool /dev/nvme1n1p2 /dev/nvme1n1p2
# (если новый диск получил другое имя — указать его)

# 5. Resilver запустится автоматически
zpool status datapool
# scan: resilver in progress...
```

**Важно для Layout-2 (stripe):** в stripe-pool нет избыточности, поэтому отказ диска = потеря данных pool. Замена диска не восстанавливает данные — это просто восстановление пустого pool. Восстановление содержимого VM — через DRBD-resync с peer-ноды (LINSTOR делает это автоматически).

В Layout-1 mirror-pool отказ одного диска mirror-пары = pool в DEGRADED, данные доступны. После `zpool replace` идёт resilver, который восстанавливает копию на новый диск.

---

## 10. Quick reference — частые команды

```bash
# === Pool management ===
zpool list                              # все pools, размер
zpool status                            # health, vdev tree
zpool status -v                         # подробно с per-disk errors
zpool history datapool                  # история команд этого pool
zpool iostat -v 5                       # live IO метрики, 5-сек интервалы
zpool scrub datapool                    # запустить scrub
zpool clear datapool                    # сбросить error counters

# === Dataset management ===
zfs list                                # все datasets
zfs list -t snapshot                    # все snapshots
zfs list -t all                         # всё
zfs get all datapool/linstor            # все свойства dataset'а
zfs get used,referenced,compressratio datapool/linstor

# === Snapshots ===
zfs snapshot datapool/linstor/vm-100@now
zfs send datapool/linstor/vm-100@now | ssh node2 "zfs receive datapool/linstor/vm-100"
zfs rollback datapool/linstor/vm-100@before-update

# === Properties ===
zfs set compression=zstd datapool/templates
zfs set quota=100G datapool/scratch
zfs set reservation=20G datapool/critical-vm

# === Cache ===
cat /proc/spl/kstat/zfs/arcstats | head -30
arcstat 1                               # из пакета zfs-utils-linux, live стата
```

---

## 11. ZFS в Proxmox UI

После создания zpool через CLI, добавить его в Proxmox как storage:

1. Web UI → **Datacenter** → **Storage** → **Add** → **ZFS**
2. **ID:** `datapool` (или любое имя)
3. **ZFS Pool:** `datapool/linstor` (выбрать dataset)
4. **Content:** `Disk image` (для VM)
5. **✅ Thin provision** — ОБЯЗАТЕЛЬНО включить (zvol с `volsize=X` + `refreservation=none` = thin)
6. **Block size:** 8K (default подходит)
7. **OK**

После этого zvol-based VM можно создавать через Proxmox UI или Terraform на этом storage. Но для LINSTOR-managed томов мы НЕ используем Proxmox storage backend — LINSTOR создаёт zvol напрямую через свой API.

---

## 12. Что дальше

- LINSTOR поверх datapool — [03-linstor-and-drbd.md](03-linstor-and-drbd.md)
- Сравнение с CEPH — [04-ceph.md](04-ceph.md), [05-storage-comparison.md](05-storage-comparison.md)
- Walkthrough от пустого сервера — [07-step-by-step-bootstrap.md](07-step-by-step-bootstrap.md)

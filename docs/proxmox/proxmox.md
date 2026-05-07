# Тут будет написано много документации по настройке Proxmox и Terraform

# ------
# ------
# ------


Окей, сейчас разложу по полочкам. Будет длинно, но по делу — каждый термин с аналогией и примером, чтобы запомнилось.

---

# Часть 1. Фундамент — что такое «блочное устройство» и «файловая система»

Это база. Без неё всё остальное не сложится.

## 1.1 Блочное устройство (block device)

Это то, что в Linux выглядит как `/dev/sda`, `/dev/nvme0n1`, `/dev/drbd1000`.

**Аналогия:** представь огромную таблицу Excel в одну колонку. Каждая ячейка — один блок (обычно 4 КБ). У ячейки есть номер (offset) и содержимое (данные). Всё.

Блочное устройство умеет только две операции:
- `read(offset, length)` — прочитать N байт с позиции X
- `write(offset, length, data)` — записать N байт на позицию X

Оно НЕ знает что такое «файл», «папка», «имя». Оно знает только нумерованные блоки.

**Примеры блочных устройств:**
- Физический диск: `/dev/nvme0n1` (целый диск)
- Партиция: `/dev/nvme0n1p1` (часть диска)
- LVM volume: `/dev/vg0/lv-root`
- DRBD volume: `/dev/drbd1000` (виртуальный, реплицируется по сети)
- Loopback: `/dev/loop0` (файл, притворяющийся диском)

## 1.2 Файловая система (filesystem)

Это **слой поверх блочного устройства**, который вводит концепции файлов, папок, прав доступа, имён.

**Аналогия:** ты в Excel-таблице ввёл правило: «первые 1000 ячеек — это оглавление, говорит где какой файл лежит. Дальше идут сами файлы». Filesystem — это вот это правило.

**Примеры filesystem:**
- ext4 — стандарт Linux, без особых фич
- XFS — для больших файлов
- ZFS — со встроенной защитой от bit rot, снапшотами и т.д.
- NTFS — Windows
- FAT32 — флешки

Filesystem **монтируется** в какую-то папку (`mount /dev/sda1 /mnt/data`), после чего ты видишь файлы в `/mnt/data/`.

## 1.3 Volume manager (менеджер томов)

Это слой между физическими дисками и filesystem, который позволяет:
- Объединять несколько дисков в один большой логический
- Делить один диск на несколько логических
- Делать снапшоты, миграции, расширения

**Примеры volume manager:**
- LVM (стандарт Linux) — отдельная утилита
- ZFS — встроен в саму ФС (это его особенность)

**Стек:** физические диски → volume manager → логические тома → filesystem → файлы.

---

# Часть 2. ZFS — особенная файловая система с встроенным volume manager

## 2.1 Что в ZFS особенного

Обычный стек Linux:
```
[ext4 файловая система] → [LVM volume] → [физический диск /dev/sda]
        отдельный                отдельный            отдельный
```

ZFS объединяет всё это в одно целое:
```
[ZFS — это и filesystem И volume manager одновременно] → [физические диски]
```

Это даёт ZFS уникальные возможности, потому что одна программа управляет всей цепочкой:
- Знает о физическом расположении блоков → может оптимизировать
- Знает о filesystem-уровне → может делать checksum'ы файлов и атомарные снапшоты

## 2.2 Copy-on-Write (CoW) — главный принцип ZFS

**Обычный диск:** если ты меняешь блок 5, ZFS перезаписывает блок 5 новыми данными. Если в этот момент отрубилось питание — блок 5 в неизвестном состоянии.

**ZFS:** если ты меняешь блок 5, ZFS пишет новые данные в блок 1000 (свободный), потом атомарно меняет указатель «теперь блок 5 = блок 1000». Старые данные блока 5 ещё какое-то время лежат — пока на них есть ссылки (например, снапшот).

**Следствия:**
- Снапшоты бесплатны: «снапшот» = просто фиксация текущих указателей, никакого копирования
- Защита от torn writes: если питание отрубилось во время записи — старые данные целы, новые ещё не получили указатель
- Можно откатиться назад атомарно

## 2.3 z-семейство — все термины с буквы «z»

Тут начинается путаница, разложу по полочкам:

| Термин | Что это | Пример |
|---|---|---|
| **zpool** | Контейнер из дисков. Один zpool = одно «адресное пространство» для данных. | `datapool`, `rpool` |
| **vdev** | Группа дисков ВНУТРИ zpool с определённой топологией. zpool состоит из vdev'ов. | mirror vdev, stripe vdev, raidz vdev |
| **zfs (filesystem)** | Иерархическая ФС внутри zpool. Можно создавать вложенные. | `datapool/linstor`, `datapool/iso` |
| **zvol** | Блочное устройство внутри zpool (а не файловая система). Появляется в `/dev/zvol/`. | `/dev/zvol/datapool/linstor/vm-100` |
| **dataset** | Общий термин: или ZFS-filesystem, или zvol, или snapshot. Всё что внутри zpool. | `datapool/linstor` — это dataset |
| **ZIL** | ZFS Intent Log — журнал sync-writes. Аналог write-ahead log в БД. | живёт внутри zpool |
| **SLOG** | Separate intent LOG — ZIL на отдельном диске (обычно быстрый NVMe). | `/dev/optane0` |
| **ARC** | Adaptive Replacement Cache — read-кэш в RAM. | смотрим через `arcstat` |
| **L2ARC** | Level 2 ARC — read-кэш на SSD (когда RAM не хватает). | `/dev/ssd0` |
| **txg** | Transaction Group — пачка отложенных writes, коммитится раз в ~5 сек. | внутреннее понятие |

### 2.3.1 zpool — детальнее

zpool — это самый верхний контейнер. Команды:

```bash
# Создать zpool из двух дисков (stripe — без избыточности)
zpool create datapool /dev/nvme0n1 /dev/nvme1n1

# Список zpool'ов
zpool list
# NAME       SIZE   ALLOC   FREE
# datapool   3.6T   1.2T    2.4T
# rpool      400G   12G     388G

# Здоровье
zpool status datapool
# pool: datapool
# state: ONLINE
# config:
#   datapool   ONLINE
#     nvme0n1  ONLINE
#     nvme1n1  ONLINE
```

### 2.3.2 vdev — детальнее

vdev = группа дисков с топологией. Внутри одного zpool может быть несколько vdev'ов. Они работают как RAID-0 между собой (страйпинг), а ВНУТРИ vdev'а — топология определяет избыточность.

**Типы vdev:**

```
┌─ stripe vdev ───────────────────────┐
│ один диск, нет избыточности         │
│ disk1                               │
└─────────────────────────────────────┘

┌─ mirror vdev ───────────────────────┐
│ 2+ диска, RAID-1                    │
│ disk1 ←→ disk2  (идентичные копии) │
└─────────────────────────────────────┘

┌─ raidz1 vdev ───────────────────────┐
│ 3+ диска, аналог RAID-5             │
│ data1 + data2 + parity              │
│ выживает при отказе 1 диска         │
└─────────────────────────────────────┘

┌─ raidz2 vdev ───────────────────────┐
│ 4+ диска, аналог RAID-6             │
│ выживает при отказе 2 дисков        │
└─────────────────────────────────────┘
```

**Важно:** избыточность — на уровне vdev, не zpool. Если zpool состоит из 2 mirror vdev — это RAID-10. Если zpool из одного raidz1 — это RAID-5.

```bash
# Mirror vdev — RAID-1
zpool create rpool mirror /dev/nvme0n1p1 /dev/nvme1n1p1

# Stripe vdev (один диск без избыточности)
zpool create datapool /dev/nvme0n1

# RAID-10: 2 mirror vdev
zpool create datapool mirror /dev/nvme0n1 /dev/nvme1n1 mirror /dev/nvme2n1 /dev/nvme3n1
```

### 2.3.3 zfs filesystem (dataset) — детальнее

После создания zpool ты создаёшь внутри него **datasets** — это как папки, но с собственными свойствами.

```bash
# Создать дочерний dataset
zfs create datapool/iso
zfs create datapool/templates
zfs create datapool/linstor

# Список
zfs list
# NAME                USED   AVAIL   MOUNTPOINT
# datapool            12K    2.4T    /datapool
# datapool/iso         8K    2.4T    /datapool/iso
# datapool/templates   8K    2.4T    /datapool/templates
# datapool/linstor     8K    2.4T    none

# Свойства
zfs set compression=lz4 datapool/iso
zfs set quota=100G datapool/iso
zfs get all datapool/iso
```

Каждый dataset — это **отдельная файловая система**, монтируется отдельно, имеет свои свойства (сжатие, квоты, sync, snapshot policy).

### 2.3.4 zvol — детальнее (важно для нас!)

zvol = блочное устройство внутри zpool. Это **НЕ файловая система**, а «виртуальный диск», на который ты можешь поставить любую другую ФС или отдать VM.

```bash
# Создать zvol на 50 ГБ
zfs create -V 50G datapool/linstor/vm-100

# Появится блочное устройство:
ls -la /dev/zvol/datapool/linstor/vm-100
# brw-rw---- ... /dev/zvol/datapool/linstor/vm-100 → ../zd0

# Можно поставить ФС
mkfs.ext4 /dev/zvol/datapool/linstor/vm-100

# Или отдать как диск VM
qm set 100 --scsi0 datapool:vm-100
```

**Почему важно:** LINSTOR создаёт zvol'ы для каждого тома VM. То есть когда твоя VM думает что у неё `/dev/sda` — это на самом деле `/dev/zvol/datapool/linstor/vm-NNN-disk-0`.

### 2.3.5 ZIL — ZFS Intent Log

Когда приложение делает `fsync()` (требует «записать на диск немедленно»), ZFS:
1. Записывает данные в **ZIL** — журнал на диске
2. Возвращает приложению ack «готово, на диске»
3. Позже (в рамках txg) переписывает данные в финальное место

ZIL по умолчанию живёт ВНУТРИ zpool (на тех же дисках). Это работает, но создаёт write amplification — данные пишутся 2 раза.

### 2.3.6 SLOG — Separate intent LOG

Можно вынести ZIL на отдельный быстрый диск:

```bash
zpool add datapool log /dev/optane0
# теперь sync-writes идут на Optane, основные данные — на NVMe
```

В нашем случае все диски — NVMe, поэтому SLOG не нужен (нет разницы в скорости).

### 2.3.7 ARC — read-кэш в RAM

ZFS забирает RAM под read-кэш (по умолчанию до 50% RAM!). Кэширует часто читаемые блоки.

```bash
# Текущий размер ARC
arcstat 1
#    time  read  miss  miss%  dmis  dm%  pmis  pm%  mmis  mm%  arcsz  c
#    20:42:38  0    0    0      0    0    0    0    0    0     8.3G  16G

# Лимит ARC = 16 ГБ
echo "options zfs zfs_arc_max=17179869184" > /etc/modprobe.d/zfs.conf
```

Это важно настроить, иначе ZFS «съест» половину RAM, оставив мало для VM.

### 2.3.8 Снапшоты

```bash
# Создать снапшот dataset'а
zfs snapshot datapool/linstor/vm-100@before-upgrade

# Откатиться (УНИЧТОЖИТ всё после снапшота!)
zfs rollback datapool/linstor/vm-100@before-upgrade

# Список
zfs list -t snapshot

# Удалить
zfs destroy datapool/linstor/vm-100@before-upgrade
```

Снапшоты бесплатны (CoW). Занимают место только когда блоки начинают отличаться от снапшота.

### 2.3.9 zfs send / receive — репликация

```bash
# Отправить snapshot на другой сервер
zfs send datapool/linstor/vm-100@now | ssh node2 "zfs receive datapool/linstor/vm-100"

# Инкрементальная (только дельта между двумя снапшотами)
zfs send -i @yesterday datapool/linstor/vm-100@now | ssh node2 "zfs receive datapool/linstor/vm-100"
```

Это **офлайн-репликация**, не realtime. Для realtime нужен DRBD (см. ниже).

### 2.3.10 Резюме по ZFS-стеку

```
zpool                       ← контейнер из дисков
  └── vdev (mirror/stripe/raidz)   ← топология избыточности
        └── physical disks         ← железо

  └── dataset (filesystem)         ← как папки, можно монтировать
        └── files

  └── zvol (block device)          ← виртуальные диски для VM
        └── любая ФС или сырое устройство

  └── snapshots                    ← точки восстановления
  └── ZIL/SLOG                     ← журнал sync-writes
  └── ARC/L2ARC                    ← read-кэш
```

---

# Часть 3. DRBD — реплицируемые блочные устройства между серверами

## 3.1 Зачем нужен DRBD

ZFS на одной ноде защищает от отказа диска, но не от отказа ноды. Если упал сервер — данные недоступны.

DRBD = **Distributed Replicated Block Device**. Это драйвер ядра Linux, который создаёт блочное устройство, **зеркально реплицирующееся** между нодами по сети.

**Аналогия:** ZFS mirror — это RAID-1 между двумя дисками внутри одного компьютера. DRBD — это «RAID-1 между дисками двух разных компьютеров через сеть».

## 3.2 Как выглядит DRBD

```
Node 1                                     Node 2
─────                                      ─────
[/dev/zvol/datapool/linstor/foo]   ←→    [/dev/zvol/datapool/linstor/foo]
            ↑                                          ↑
            └──── DRBD реплицирует через сеть ────────┘
            ↓                                          ↓
[/dev/drbd1000]                            [/dev/drbd1000]
            ↑
            └─ это блочное устройство, которое видит приложение/VM
```

Приложение пишет в `/dev/drbd1000`, DRBD прозрачно реплицирует на peer.

## 3.3 Primary / Secondary

В обычном режиме DRBD имеет:
- **Primary** — на этой ноде можно читать и писать через `/dev/drbd1000`
- **Secondary** — на этой ноде только реплика, читать/писать через DRBD-устройство нельзя

Когда VM мигрирует с Node 1 на Node 2 — DRBD переключает primary с Node 1 на Node 2.

(DRBD 9 поддерживает multi-primary с cluster-FS, но мы это не используем.)

## 3.4 DRBD Protocol A / B / C — это критично

Когда приложение делает `write()`, в какой момент DRBD возвращает «ack, готово»?

| Протокол | Когда ack | Что значит |
|---|---|---|
| **A (async)** | Локальный диск записал + блок попал в TCP send buffer | Если local-нода упадёт ДО того как блок дошёл до peer — потеря |
| **B (semi-sync)** | Локальный + удалённый получил в RAM | Если peer-нода упадёт до flush на диск — потеря |
| **C (sync)** | Локальный + удалённый записал НА ДИСК | Гарантия: блок на 2 дисках на 2 нодах. Цена: +1 RTT латентности |

**В нашей инсталляции:**
- `rg-net-sync` (production БД) → Protocol C
- `rg-net-async` (кэши) → Protocol A

## 3.5 DRBD Quorum — защита от split-brain

**Split-brain** — ситуация, когда сетевая связь между нодами рвётся, и обе ноды думают что они primary. Каждая принимает writes отдельно. Когда связь восстанавливается — какие данные правильные? Никакие.

DRBD-quorum: writes разрешены только если живо большинство реплик.

С 2 нодами (replica=2) большинства не бывает (1/2 = не большинство). Поэтому добавляем **третью ноду как diskless tiebreaker** — она не хранит данные, только голосует. Теперь 2/3 = большинство, всё ок.

## 3.6 DRBD-команды

```bash
# Статус
drbdadm status
# foo role:Primary
#   peer role:Secondary
#     replication:Established peer-disk:UpToDate

cat /proc/drbd
# подробная инфа: какие ресурсы, состояние, скорость репликации

# Переключить primary на эту ноду
drbdadm primary foo

# Force resync (если что-то сломалось)
drbdadm invalidate foo

# Verify (онлайн-проверка checksum'ов)
drbdadm verify foo
```

---

# Часть 4. LINSTOR — менеджер DRBD-ресурсов

## 4.1 Зачем нужен LINSTOR

DRBD сам по себе — низкоуровневый. Чтобы создать новый DRBD-resource нужно:
1. Создать zvol на каждой ноде
2. Создать DRBD-config файл с описанием ресурса (какие ноды, какой порт, какой backend)
3. Распространить файл на все ноды
4. Запустить DRBD-resource на всех нодах
5. Сделать одну ноду primary
6. Дождаться initial sync

Если у тебя 100 томов — это 600 ручных шагов. Не масштабируется.

LINSTOR — **управляющий слой над DRBD**. Ты говоришь «создай volume 50G с replica=2» — LINSTOR сам делает все 6 шагов.

## 4.2 Архитектура LINSTOR

```
┌──────────────────────┐
│ LINSTOR Controller   │  ← один на кластер (можно с HA)
│ - API                │     хранит конфигурацию: какие ноды, какие пулы, какие resources
│ - БД (H2 / etcd)     │     принимает команды от пользователя
└──────────┬───────────┘
           │ команды
           ▼
┌──────────────────────┐  ┌──────────────────────┐  ┌──────────────────────┐
│ LINSTOR Satellite    │  │ LINSTOR Satellite    │  │ LINSTOR Satellite    │
│ на Node 1            │  │ на Node 2            │  │ на Node 3            │
│ - управляет ZFS      │  │ - управляет ZFS      │  │ - управляет ZFS      │
│ - управляет DRBD     │  │ - управляет DRBD     │  │ - управляет DRBD     │
└──────────────────────┘  └──────────────────────┘  └──────────────────────┘
```

## 4.3 Понятия LINSTOR — пошагово от низа

### 4.3.1 Node

Хост, известный LINSTOR'у. У него есть имя (равно `hostname`), IP-адреса и тип:
- `Combined` — controller + satellite (обычно Node 1)
- `Satellite` — только satellite
- `Auxiliary` — без storage, используется как diskless tiebreaker

```bash
linstor node create node1 10.0.2.1 --node-type Combined
linstor node create node2 10.0.2.2 --node-type Satellite
linstor node create node3 10.0.2.3 --node-type Satellite
linstor node list
```

### 4.3.2 Storage Pool

Конкретный backend storage на конкретной ноде. У нас это ZFS dataset.

«На этой ноде, в этом ZFS-пуле — храни тома».

```bash
# На каждой ноде создаём storage pool sp-data, указываем ZFS dataset
linstor storage-pool create zfsthin node1 sp-data datapool/linstor
linstor storage-pool create zfsthin node2 sp-data datapool/linstor
linstor storage-pool create zfsthin node3 sp-data datapool/linstor

linstor storage-pool list
# StoragePool | Node  | Driver   | PoolName        | FreeCapacity
# sp-data     | node1 | ZFS_THIN | datapool/linstor | 7.80 TiB
# sp-data     | node2 | ZFS_THIN | datapool/linstor | 7.80 TiB
# sp-data     | node3 | ZFS_THIN | datapool/linstor | 7.80 TiB
```

`zfsthin` = thin provisioning (zvol с volsize=X, реальное использование по факту).

### 4.3.3 Resource Group (RG) — шаблон

Шаблон для создания тома: «replica=2, протокол C, на пуле sp-data, с такими-то свойствами».

```bash
linstor resource-group create rg-net-sync \
    --storage-pool sp-data \
    --place-count 2

linstor resource-group set-property rg-net-sync DrbdOptions/Net/protocol C
```

Это аналогично «StorageClass» в Kubernetes — описывает «какие тома нам нужны такого типа».

### 4.3.4 Volume Group

Внутри RG нужно создать VolumeGroup — описывает дефолтный размер тома (или 0 = задавать каждый раз).

```bash
linstor volume-group create rg-net-sync
```

### 4.3.5 Resource — конкретный том

Из шаблона (RG) спавним конкретный resource:

```bash
linstor resource-group spawn-resources rg-net-sync vm-100-disk-0 50G
```

LINSTOR делает:
1. Создаёт zvol `datapool/linstor/vm-100-disk-0` на 2 нодах (по выбору auto-place)
2. Создаёт DRBD-resource с replica=2, Protocol C
3. Запускает DRBD на всех 3 нодах (2 diskful + 1 diskless tiebreaker)
4. Делает первый sync
5. Возвращает имя устройства `/dev/drbd1000`

### 4.3.6 Volume

Внутри одного Resource может быть несколько Volume (например, для VM с несколькими дисками). В простом случае — один volume на resource.

```bash
linstor resource list-volumes
# Resource          | Node  | Vol# | DeviceName        | Size
# vm-100-disk-0     | node1 | 0    | /dev/drbd1000     | 50 GiB
# vm-100-disk-0     | node2 | 0    | /dev/drbd1000     | 50 GiB
# vm-100-disk-0     | node3 | 0    | (diskless)        | 50 GiB
```

### 4.3.7 Diskless Replica (Tiebreaker)

Replica без локального диска. Только метаданные DRBD, нужна для голосования (quorum). LINSTOR может разместить автоматически.

```bash
# Включить auto-tiebreaker для RG
linstor resource-group set-property rg-net-sync DrbdOptions/auto-add-quorum-tiebreaker yes
```

При создании Resource из этого RG:
- 2 diskful replicas размещаются на 2 нодах с storage pool
- 1 diskless replica автоматически добавляется на 3-ю ноду

## 4.4 Как всё связано — пример полного цикла

```bash
# === 1. Регистрируем ноды ===
linstor node create node1 10.0.2.1 --node-type Combined
linstor node create node2 10.0.2.2 --node-type Satellite
linstor node create node3 10.0.2.3 --node-type Satellite

# === 2. Создаём storage pool на каждой ноде (поверх ZFS) ===
linstor storage-pool create zfsthin node1 sp-data datapool/linstor
linstor storage-pool create zfsthin node2 sp-data datapool/linstor
linstor storage-pool create zfsthin node3 sp-data datapool/linstor

# === 3. Создаём шаблон (resource group) ===
linstor resource-group create rg-net-sync --storage-pool sp-data --place-count 2
linstor resource-group set-property rg-net-sync DrbdOptions/Net/protocol C
linstor resource-group set-property rg-net-sync DrbdOptions/auto-add-quorum-tiebreaker yes
linstor volume-group create rg-net-sync

# === 4. Спавним конкретный том из шаблона ===
linstor resource-group spawn-resources rg-net-sync vm-100-disk-0 50G

# === 5. Что произошло физически ===
# На node1: создан zvol datapool/linstor/vm-100-disk-0 (50G)
# На node2: создан zvol datapool/linstor/vm-100-disk-0 (50G)
# На node3: создан DRBD-metadata только (diskless tiebreaker)
# На всех 3: запущен DRBD-resource vm-100-disk-0
# Initial sync прошёл (если данные есть)
# /dev/drbd1000 доступен на node1 (primary)

# === 6. Использовать (например, как диск VM) ===
mkfs.ext4 /dev/drbd/by-res/vm-100-disk-0/0
mount /dev/drbd/by-res/vm-100-disk-0/0 /mnt
echo "hello" > /mnt/test.txt

# === 7. Что происходит при write ===
# echo > запись в файл → ext4 → /dev/drbd1000 → DRBD ловит write
# DRBD пишет на локальный zvol (node1)
# DRBD отправляет блок по сети на node2
# Node2 пишет на свой zvol
# Node2 присылает ack
# DRBD на node1 возвращает ack приложению
# (Это Protocol C — ждём peer disk write)

# === 8. Очистка ===
linstor resource-definition delete vm-100-disk-0
# DRBD остановлен, zvol'ы удалены
```

---

# Часть 5. Полная картина стека — как всё связано

```
┌────────────────────────────────────────────────────────────────────┐
│  Application (Postgres, Kafka в VM)                                │
│  делает write() в /var/lib/postgresql/data/файл.dat                │
└──────────────────────────────┬─────────────────────────────────────┘
                               │ POSIX write
                               ▼
┌────────────────────────────────────────────────────────────────────┐
│  Guest OS (Ubuntu в VM)                                            │
│  ext4 filesystem → /dev/sda                                        │
└──────────────────────────────┬─────────────────────────────────────┘
                               │ block device write
                               ▼
┌────────────────────────────────────────────────────────────────────┐
│  Proxmox host                                                      │
│  /dev/sda VM = /dev/drbd1000 на host'е                             │
└──────────────────────────────┬─────────────────────────────────────┘
                               │
                               ▼
┌────────────────────────────────────────────────────────────────────┐
│  DRBD                                                              │
│  Принимает write, реплицирует на peer (Protocol C — sync)         │
│  Пишет в backing device — zvol                                    │
└──────────────┬─────────────────────────────────┬───────────────────┘
               │ local write                     │ network write (по 25 GbE)
               ▼                                 ▼
┌──────────────────────────────┐  ┌──────────────────────────────┐
│  ZFS на Node 1               │  │  ZFS на Node 2               │
│  zvol = datapool/linstor/    │  │  zvol = datapool/linstor/    │
│         vm-100-disk-0         │  │         vm-100-disk-0         │
│       ↓                       │  │       ↓                       │
│  zpool datapool               │  │  zpool datapool               │
│       ↓                       │  │       ↓                       │
│  vdev = stripe                │  │  vdev = stripe                │
│       ↓                       │  │       ↓                       │
│  /dev/nvme0n1p2               │  │  /dev/nvme0n1p2               │
│  /dev/nvme1n1p2               │  │  /dev/nvme1n1p2               │
│  /dev/nvme2n1p2               │  │  /dev/nvme2n1p2               │
│  /dev/nvme3n1p2               │  │  /dev/nvme3n1p2               │
└──────────────────────────────┘  └──────────────────────────────┘
                                          ↑
                                  ┌───────┴────────┐
                                  │  Node 3        │
                                  │  diskless      │
                                  │  tiebreaker    │
                                  │  (только voting)│
                                  └────────────────┘

LINSTOR-controller (на Node 1):
  - управляет жизненным циклом всех DRBD-resources
  - не находится в data path
  - отвечает на команды linstor-CLI
```

---

# Часть 6. Quick reference — кто что делает

| Слой | Что делает | Команды |
|---|---|---|
| **Application** | Бизнес-логика, файлы, БД | твой код |
| **Guest OS** | POSIX, mount, FS в VM | внутри VM |
| **Proxmox** | Управление VM, эмуляция «дисков» | `qm`, web UI |
| **LINSTOR** | Менеджер DRBD-ресурсов | `linstor` |
| **DRBD** | Сетевая репликация блоков | `drbdadm` |
| **ZFS** | Локальная ФС + volume manager | `zfs`, `zpool` |
| **Hardware** | Физические NVMe-диски | `lsblk`, `nvme` |

---

# Часть 7. Аналогия из обычного мира

Представь, что данные — это документы.

| ZFS термин | Аналогия |
|---|---|
| **Физический диск (NVMe)** | Лист бумаги |
| **vdev mirror** | 2 листа бумаги под копирку (один пишешь — оба заполняются) |
| **vdev stripe** | Несколько отдельных листов: «продолжай на следующем» |
| **zpool** | Папка-скоросшиватель, куда подшиты все vdev'ы |
| **dataset** | Раздел в скоросшивателе с заголовком (FS) |
| **zvol** | Раздел в скоросшивателе, который выделен под «второй скоросшиватель» (виртуальный диск для VM) |
| **snapshot** | Фотография всех страниц в этот момент |
| **ZIL** | Черновик, куда срочно записываешь, потом переписываешь начисто |
| **ARC** | Память — что недавно читал |

| DRBD термин | Аналогия |
|---|---|
| **DRBD resource** | Документ, который ты пишешь параллельно в двух офисах |
| **Primary/Secondary** | Один офис главный (туда пишут), другой только хранит копию |
| **Protocol C** | Курьер ждёт, пока в обоих офисах записали, и только тогда говорит «готово» |
| **Protocol A** | Курьер сразу говорит «готово» как только унёс из главного офиса (по дороге может уронить) |
| **Quorum / tiebreaker** | Третий офис голосует «кто настоящий главный», когда связь рвётся |

| LINSTOR термин | Аналогия |
|---|---|
| **Controller** | Главный офис-менеджер: где какой документ, кто за что отвечает |
| **Satellite** | Локальный клерк в каждом офисе, исполняет команды менеджера |
| **Storage Pool** | «Шкаф со скоросшивателями в этом офисе» |
| **Resource Group** | Шаблон: «такие документы создавать в 2 копиях, с курьером Proto C» |
| **Resource** | Конкретный документ, созданный по шаблону |

---

# Что читать дальше

Если хочешь глубже:

- ZFS: книга «FreeBSD Mastery: ZFS» от Lucas/Jude — лучший русский ресурс по ZFS, легко читается
- DRBD: официальный пользовательский гайд — https://linbit.com/drbd-user-guide/
- LINSTOR: офф-доки https://linbit.com/drbd-user-guide/linstor-guide-1_0-en/

В нашей доке `proxmox/`:
- [02-zfs.md](proxmox/02-zfs.md) — практические команды ZFS
- [03-linstor-and-drbd.md](proxmox/03-linstor-and-drbd.md) — настройка LINSTOR
- [05-storage-comparison.md](proxmox/05-storage-comparison.md) — сводные таблицы

Если что-то осталось непонятно — спроси, разберём конкретный термин подробнее.
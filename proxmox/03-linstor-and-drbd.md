# LINSTOR + DRBD — Setup, Resource Groups, Quorum

LINSTOR — управляющий слой (control plane) для репликации блочных устройств между нодами. DRBD — runtime, который физически реплицирует блоки. LINSTOR использует DRBD как движок, добавляя поверх API/CLI, авто-плейсмент, snapshots, integration с Proxmox/K8s.

В нашей инсталляции LINSTOR работает поверх ZFS (см. [02-zfs.md](02-zfs.md)).

---

## 1. Концепции — что есть что

| Термин | Что это |
|---|---|
| **DRBD resource** | Блочное устройство (`/dev/drbd1000`), реплицированное между нодами по сети. |
| **DRBD primary/secondary** | На primary можно читать/писать; на secondary — только реплика. В DRBD 9 — multi-primary возможно, но мы не используем. |
| **DRBD protocol A/B/C** | Способ ack writes. C = sync (ack после remote-disk write), A = async (ack после local-disk + сетевой буфер). |
| **DRBD quorum** | Минимум живых реплик для разрешения writes. Защита от split-brain. |
| **LINSTOR controller** | Daemon с API + БД конфигурации. Один на кластер (с возможностью HA). |
| **LINSTOR satellite** | Daemon на каждой ноде. Принимает команды от controller, исполняет на DRBD/ZFS. |
| **LINSTOR node** | Хост, известный controller'у. Может быть Combined (controller + satellite), Satellite (только satellite), или Auxiliary. |
| **LINSTOR storage pool** | Конкретный backend storage на конкретной ноде (например, ZFS pool `datapool`). |
| **LINSTOR resource definition** | «Спецификация» ресурса (имя, размер, properties). |
| **LINSTOR resource** | Развёрнутый ресурс на конкретных нодах (по resource definition). |
| **LINSTOR resource group** | Шаблон для создания ресурсов с одинаковыми параметрами (replica count, protocol, backing pool). |
| **LINSTOR volume** | Физический том внутри resource (один resource может иметь несколько томов). |
| **diskless** | LINSTOR может разместить ресурс без локального диска — узел становится клиентом DRBD по сети. Используется для tiebreaker. |

**Поток данных:** VM пишет в zvol (`/dev/zvol/datapool/linstor/vm-100`) → LINSTOR смонтировал его как DRBD device (`/dev/drbd1000`) → DRBD реплицирует на peer (по vmbr3 25 GbE) → ack возвращается в зависимости от protocol.

---

## 2. Установка LINSTOR + DRBD на каждую ноду

### 2.1 Добавить LINBIT-репозиторий

LINSTOR community-версия доступна через PPA / OBS-репозиторий. Для Proxmox 8 (Debian 12):

```bash
# Добавить ключ
wget -qO - https://packages.linbit.com/public.key | apt-key add -

# Добавить репо
cat > /etc/apt/sources.list.d/linbit.list <<EOF
deb http://packages.linbit.com/public/ proxmox-8 drbd-9
EOF

apt update
```

Альтернативно — собрать из исходников или использовать LINBIT Customer-репо (платный, с расширенной поддержкой).

### 2.2 Установить пакеты

**На всех нодах:**

```bash
apt install -y \
    drbd-dkms \
    drbd-utils \
    linstor-satellite \
    linstor-client
```

**Только на Node 1** (где будет controller):

```bash
apt install -y linstor-controller
```

`drbd-dkms` соберёт kernel module под текущее ядро Proxmox. Если ядро обновится — пересборка автоматическая через DKMS.

### 2.3 Проверка модуля DRBD

```bash
modprobe drbd
modinfo drbd | head -5
# expected: version: 9.x.x
cat /proc/drbd
# expected: пустой output, но без ошибок
```

Если `modprobe drbd` ругается — DKMS не собрал. Смотреть `dmesg | grep -i drbd` и `dkms status`.

### 2.4 Запуск сервисов

**На Node 1:**

```bash
systemctl enable --now linstor-controller
systemctl status linstor-controller
# expected: active (running)
```

**На всех нодах:**

```bash
systemctl enable --now linstor-satellite
systemctl status linstor-satellite
# expected: active (running)
```

### 2.5 Конфигурация LINSTOR client

Чтобы `linstor` CLI знал, куда обращаться, на всех нодах:

```bash
mkdir -p /etc/linstor
cat > /etc/linstor/linstor-client.conf <<EOF
[global]
controllers=10.0.2.1
EOF
```

(IP `10.0.2.1` — это Node 1 в storage VLAN, см. [01-network.md](01-network.md). Можно использовать cluster mgmt IP `10.0.0.1`, но storage сеть выделенная и быстрее.)

Проверка:

```bash
linstor node list
# expected: пустой список или ошибка connection — пока не зарегистрировали ноды
```

---

## 3. Регистрация нод в LINSTOR

### 3.1 Добавить ноды

С Node 1 (или с любой ноды, имеющей правильный `linstor-client.conf`):

```bash
# Node 1 — combined (controller + satellite)
linstor node create node1 10.0.2.1 --node-type Combined

# Node 2, Node 3 — satellite-only
linstor node create node2 10.0.2.2 --node-type Satellite
linstor node create node3 10.0.2.3 --node-type Satellite
```

**Имена нод** должны совпадать с `hostname` на каждой машине (LINSTOR проверяет). Если hostname другой — указать в `linstor node create`.

Проверка:

```bash
linstor node list
```

Ожидаемое:
```
+-------------------------------------------------------------+
| Node  | NodeType  | Addresses          | State              |
|=============================================================|
| node1 | COMBINED  | 10.0.2.1:3366      | Online             |
| node2 | SATELLITE | 10.0.2.2:3366      | Online             |
| node3 | SATELLITE | 10.0.2.3:3366      | Online             |
+-------------------------------------------------------------+
```

Если State = `Offline` или `EVICTED` — проверь firewall (порт 3366 TCP), ssh-доступность storage IP, статус `linstor-satellite` на той ноде.

---

## 4. Storage Pools — backing storage в LINSTOR

LINSTOR storage pool = «здесь живут zvol/lvol на этой ноде». В нашем случае backing — ZFS dataset `datapool/linstor`.

### 4.1 Создать storage pool на каждой ноде

```bash
# ZFS thin pool (zvol с refreservation=none)
linstor storage-pool create zfsthin node1 sp-data datapool/linstor
linstor storage-pool create zfsthin node2 sp-data datapool/linstor
linstor storage-pool create zfsthin node3 sp-data datapool/linstor
```

`zfsthin` = zvol будут thin-provisioned (volsize=X, реальное использование — по факту записи).
`zfs` = thick-provisioned (полный объём резервируется сразу).

Для VM-нагрузок thin — почти всегда правильный выбор (не платим за неиспользованное место).

Проверка:

```bash
linstor storage-pool list
```

Ожидаемое:
```
+-------------------------------------------------------------------------------------+
| StoragePool | Node  | Driver       | PoolName        | FreeCapacity | TotalCapacity |
|=====================================================================================|
| sp-data     | node1 | ZFS_THIN     | datapool/linstor| 7.80 TiB     | 7.80 TiB      |
| sp-data     | node2 | ZFS_THIN     | datapool/linstor| 7.80 TiB     | 7.80 TiB      |
| sp-data     | node3 | ZFS_THIN     | datapool/linstor| 7.80 TiB     | 7.80 TiB      |
+-------------------------------------------------------------------------------------+
```

---

## 5. DRBD Protocols — детально

LINSTOR настраивает DRBD-protocol через свойство ресурса. Стандарт DRBD различает:

| Protocol | Когда возвращается ack write | Latency | RPO при отказе primary |
|---|---|---|---|
| **A (async)** | Local disk written + блок в TCP send buffer на peer | минимум | До нескольких секунд (содержимое sendbuf) |
| **B (semi-sync)** | Local disk written + peer acknowledged receipt в RAM | средне | Несколько ms (RAM peer'а ещё не сброшена) |
| **C (sync)** | Local disk written + peer disk written | максимум | 0 |

**В нашем дизайне используем только A и C.** Protocol B редко применяется (компромисс без чёткого win'а).

LINSTOR назначает протокол через свойство `DrbdOptions/Net/protocol` на resource group или resource:

```bash
# Установка для resource group
linstor resource-group set-property rg-net-sync DrbdOptions/Net/protocol C
linstor resource-group set-property rg-net-async DrbdOptions/Net/protocol A
```

---

## 6. Resource Groups — 3 default

См. [README.md](README.md) §архитектура для контекста.

### 6.1 rg-local-single (replica=1, no network replica)

```bash
linstor resource-group create rg-local-single \
    --storage-pool sp-data \
    --place-count 1
```

Создаёт ресурсы с **1 копией данных**, размещённой на каком-то ноде. Без DRBD-репликации (DRBD-resource всё равно создаётся, но с 1 replica — без репликации трафика).

### 6.2 rg-net-async (replica=2, DRBD Proto A)

```bash
linstor resource-group create rg-net-async \
    --storage-pool sp-data \
    --place-count 2

# Set protocol A для async
linstor resource-group set-property rg-net-async \
    DrbdOptions/Net/protocol A

# Quorum settings — DRBD должен поддерживать кворум
linstor resource-group set-property rg-net-async \
    DrbdOptions/auto-quorum suspend-io

# Для replica=2 нужен diskless tiebreaker — auto-quorum с tiebreaker
linstor resource-group set-property rg-net-async \
    DrbdOptions/Resource/quorum majority
linstor resource-group set-property rg-net-async \
    DrbdOptions/Resource/on-no-quorum io-error
```

### 6.3 rg-net-sync (replica=2, DRBD Proto C, **production default**)

```bash
linstor resource-group create rg-net-sync \
    --storage-pool sp-data \
    --place-count 2

linstor resource-group set-property rg-net-sync \
    DrbdOptions/Net/protocol C

linstor resource-group set-property rg-net-sync \
    DrbdOptions/auto-quorum suspend-io

linstor resource-group set-property rg-net-sync \
    DrbdOptions/Resource/quorum majority
linstor resource-group set-property rg-net-sync \
    DrbdOptions/Resource/on-no-quorum io-error
```

### 6.4 Volume groups — обязательны для создания ресурсов

Каждая resource group должна иметь хотя бы одну volume group, описывающую дефолтный размер тома (или 0 = задавать при создании каждого ресурса):

```bash
linstor volume-group create rg-local-single
linstor volume-group create rg-net-async
linstor volume-group create rg-net-sync
```

### 6.5 Проверка

```bash
linstor resource-group list
linstor volume-group list
```

---

## 7. Diskless Tiebreaker — кворум для replica=2

С 3 нодами и replica=2 для prod-данных, чтобы избежать split-brain, нам нужна **3-я копия**, но без хранения данных — только для голосования. Это и есть **diskless replica**.

### 7.1 Как работает кворум DRBD

DRBD считает «кворум» простым голосованием:
- Если живых replicas (любых — diskful или diskless) меньше половины → IO заморожен
- Если ровно половина → split-brain risk, IO заморожен (если `on-no-quorum io-error`)
- Если больше половины → IO разрешён

С 2 diskful replicas без tiebreaker:
- 2/2 живы → OK
- 1/2 живы → split-brain неизбежен при healing → DRBD блокирует IO

С 2 diskful + 1 diskless tiebreaker:
- 3/3 живы → OK
- 2/3 живы → большинство → IO разрешён
- 1/3 жив (например, только diskless) → IO блокирован (правильно — данные на том node'е нет)

### 7.2 Auto-placement diskless tiebreaker

LINSTOR автоматически размещает diskless replica на 3-й ноде (которая не имеет diskful copy этого ресурса), если включено auto-quorum:

```bash
linstor resource-group set-property rg-net-sync \
    DrbdOptions/auto-add-quorum-tiebreaker yes
```

Когда создаём ресурс из этой группы, LINSTOR:
1. Размещает 2 diskful replicas на 2 нодах с storage pool
2. Добавляет 1 diskless replica на 3-ю ноду (без storage pool — клиент DRBD)

### 7.3 Verify

```bash
# Создаём тестовый ресурс
linstor resource-group spawn-resources rg-net-sync test-vol-1 1G

# Смотрим, где он
linstor resource list
```

Ожидаемое:
```
+-------------------------------------------------------+
| ResourceName | Node  | StoragePool | InUse | State    |
|=======================================================|
| test-vol-1   | node1 | sp-data     | InUse | UpToDate |
| test-vol-1   | node2 | sp-data     | InUse | UpToDate |
| test-vol-1   | node3 | DfltDisklessStorPool | InUse | Diskless |
+-------------------------------------------------------+
```

3 строки: 2 diskful (UpToDate) + 1 diskless tiebreaker.

---

## 8. Создание volume — пример workflow

```bash
# Spawn volume from resource group (под VM 100)
linstor resource-group spawn-resources rg-net-sync vm-100-disk-0 50G

# Это создаст:
# - resource-definition vm-100-disk-0
# - resource на node1, node2 (diskful), node3 (diskless tiebreaker)
# - volume 50 ГБ, DRBD device /dev/drbd1000

# Найти DRBD device
linstor resource list-volumes
# или
ls -la /dev/drbd/by-res/vm-100-disk-0/

# Можно сразу ставить ФС или отдавать в Proxmox
mkfs.ext4 /dev/drbd/by-res/vm-100-disk-0/0
```

В Proxmox VM создаётся с указанием LINSTOR storage backend (см. §11).

---

## 9. Snapshots через LINSTOR

```bash
# Создать снапшот
linstor snapshot create rg-net-sync vm-100-disk-0 before-upgrade

# Список снапшотов
linstor snapshot list

# Восстановить (создаёт новый ресурс из снапшота)
linstor snapshot resource restore --from-resource vm-100-disk-0 \
    --from-snapshot before-upgrade --to-resource vm-100-disk-0-restored

# Удалить снапшот
linstor snapshot delete vm-100-disk-0 before-upgrade
```

Снапшоты ZFS-уровня — атомарные и бесплатные. LINSTOR координирует консистентный снапшот на всех diskful-нодах.

---

## 10. LINSTOR Controller HA (advanced)

См. [QUESTIONS.md](QUESTIONS.md) Q4. Default — single controller на Node 1. HA-вариант (рекомендуется для prod через 1-2 месяца):

### 10.1 Подход: Controller на DRBD-replicated volume + Pacemaker

Идея: БД LINSTOR-controller'а живёт на DRBD-resource replica=3, controller-сервис управляется Pacemaker'ом, который запускает его на одной из нод. При падении ноды — Pacemaker переносит на другую.

**Установка:**

```bash
# 1. На каждой ноде — pacemaker, corosync, drbd-utils (уже есть)
apt install -y pacemaker corosync resource-agents drbd-reactor

# 2. Создать DRBD-resource для controller БД (через LINSTOR)
linstor resource-group create rg-controller-ha \
    --storage-pool sp-data \
    --place-count 3
linstor volume-group create rg-controller-ha

linstor resource-group spawn-resources rg-controller-ha linstor_db 1G

# 3. Перенести БД LINSTOR на новый DRBD volume
systemctl stop linstor-controller
mkfs.ext4 /dev/drbd/by-res/linstor_db/0
mount /dev/drbd/by-res/linstor_db/0 /mnt
mv /var/lib/linstor /mnt/linstor
ln -s /mnt/linstor /var/lib/linstor

# 4. Настроить drbd-reactor для управления controller'ом
# /etc/drbd-reactor.d/linstor_db.toml
cat > /etc/drbd-reactor.d/linstor_db.toml <<EOF
[[promoter]]
[promoter.resources.linstor_db]
start = ["var-lib-linstor.mount", "linstor-controller.service"]
EOF

systemctl restart drbd-reactor
```

**Подробности и официальный гайд:** https://linbit.com/drbd-user-guide/linstor-guide-1_0-en/#s-linstor_ha_setup

**Альтернатива через Kubernetes:** если LINSTOR Operator уже стоит в K8s, можно вынести controller туда. Но для standalone-Proxmox это лишний complexity.

---

## 11. Интеграция с Proxmox

LINSTOR имеет официальный Proxmox plugin: пакет `linstor-proxmox`.

### 11.1 Установка

На всех Proxmox-нодах:

```bash
apt install linstor-proxmox
```

### 11.2 Регистрация LINSTOR storage в Proxmox

Edit `/etc/pve/storage.cfg` или через UI:

```
drbd: linstor-sync
        content images, rootdir
        controller 10.0.2.1
        resourcegroup rg-net-sync
        preferlocal yes

drbd: linstor-async
        content images, rootdir
        controller 10.0.2.1
        resourcegroup rg-net-async
        preferlocal yes

drbd: linstor-local
        content images, rootdir
        controller 10.0.2.1
        resourcegroup rg-local-single
        preferlocal yes
```

`preferlocal yes` — VM предпочтительно запускается на ноде, где есть локальная diskful replica (избегаем сетевого hop'а для read'ов).

### 11.3 Создание VM на LINSTOR-storage

В UI: VM → Hardware → Hard Disk → Storage = `linstor-sync` (или `-async`/`-local`). Размер — задаётся.

Или через `qm`:

```bash
qm create 100 --name test-vm \
    --memory 4096 --cores 2 \
    --net0 virtio,bridge=vmbr2 \
    --scsi0 linstor-sync:50,iothread=1,ssd=1,discard=on \
    --bootdisk scsi0
```

LINSTOR plugin автоматически создаёт DRBD-resource при создании VM disk и удаляет при удалении.

---

## 12. Troubleshooting

### 12.1 «Out of sync» состояние

```bash
linstor resource list
# State: Inconsistent / SyncTarget / SyncSource

drbdadm status <resource>
# покажет процент завершённого ресинка

# Принудительно запустить full resync (если автоматический stuck)
drbdadm invalidate <resource>
# на ноде с устаревшими данными — она пересинкается с источника
```

### 12.2 Split-brain

С правильным quorum и diskless tiebreaker этого не должно случаться, но если случилось:

```bash
drbdadm status <resource>
# StandAlone — split-brain detected

# На «жертве» (нода с менее свежими данными):
drbdadm secondary <resource>
drbdadm disconnect <resource>
drbdadm -- --discard-my-data connect <resource>
```

### 12.3 LINSTOR satellite не подключается

```bash
systemctl status linstor-satellite
journalctl -u linstor-satellite -f

# Проверить firewall
nc -zv 10.0.2.1 3366    # с satellite до controller
nc -zv 10.0.2.2 3366    # с controller до satellite

# Restart
systemctl restart linstor-satellite
```

### 12.4 «Cannot allocate from storage pool»

```bash
linstor storage-pool list
# Смотрим FreeCapacity

# Если ZFS pool полон:
zpool list datapool
zfs list -o name,used,available datapool/linstor

# Удалить старые snapshots / unused resources
linstor resource-definition list
linstor resource delete <node> <resource>
```

### 12.5 Network performance низкая

```bash
# Проверить latency между нодами
ping -c 100 10.0.2.2 | tail -5
# expected: avg < 1 ms на 25 GbE direct

# Проверить throughput
iperf3 -s   # на одной ноде
iperf3 -c 10.0.2.2 -t 30 -P 4    # с другой
# expected: ~24 Gbit/s на 25 GbE link

# DRBD-specific: проверить settings
drbdadm net-options <resource>
# max-buffers, max-epoch-size, sndbuf-size, rcvbuf-size
```

---

## 13. Quick reference

```bash
# === Nodes ===
linstor node list
linstor node create <name> <ip> --node-type Satellite
linstor node delete <name>

# === Storage pools ===
linstor storage-pool list
linstor storage-pool create zfsthin <node> <sp-name> <zfs-dataset>
linstor storage-pool delete <node> <sp-name>

# === Resource groups ===
linstor resource-group list
linstor resource-group create <rg-name> --storage-pool <sp> --place-count <N>
linstor resource-group set-property <rg-name> <key> <value>
linstor resource-group delete <rg-name>

# === Resources ===
linstor resource list
linstor resource list-volumes
linstor resource-group spawn-resources <rg-name> <res-name> <size>
linstor resource delete <node> <res-name>
linstor resource-definition delete <res-name>

# === Snapshots ===
linstor snapshot create <rg> <res> <snap-name>
linstor snapshot list
linstor snapshot delete <res> <snap-name>

# === DRBD low-level ===
drbdadm status [resource]
drbdadm verify <resource>            # online checksum verify
drbdadm pause-sync <resource>
drbdadm resume-sync <resource>
drbdsetup status --verbose --statistics
cat /proc/drbd

# === Logs ===
journalctl -u linstor-controller -f
journalctl -u linstor-satellite -f
dmesg | grep -i drbd
```

---

## 14. Что дальше

- Сравнение с CEPH — [04-ceph.md](04-ceph.md)
- Сводные таблицы — [05-storage-comparison.md](05-storage-comparison.md)
- Provisioning VM поверх — [06-terraform-cloud-init.md](06-terraform-cloud-init.md)
- Walkthrough от bootstrap — [07-step-by-step-bootstrap.md](07-step-by-step-bootstrap.md)

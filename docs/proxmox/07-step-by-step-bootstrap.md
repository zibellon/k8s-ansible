# Step-by-Step Bootstrap — From Single Server to Full Cluster

Подробный walkthrough: от момента «у меня есть 1 сервер с предустановленным Proxmox» до «3-нодовый кластер с LINSTOR, готовый принять Terraform-провижининг VM для K8s».

Полный путь — 6 стадий:

| Стадия | Что | Время |
|---|---|---|
| 0 | Доступ к серверу, инвентаризация | 30 мин |
| 1 | Single-node Proxmox: ZFS, network, первая VM | 2-3 часа |
| 2 | Добавление дисков (с 2 → 4 на ноде) | 30 мин |
| 3 | Добавление 2-й и 3-й нод, кластеризация Proxmox | 2-3 часа |
| 4 | Установка LINSTOR + DRBD, resource groups | 2-3 часа |
| 5 | Подготовка Terraform + cloud-init template | 1-2 часа |
| 6 | Provisioning K8s VM, передача в k8s-ansible | 1 час |

**Каждая стадия имеет:** prerequisites, шаги, validation, troubleshooting.

---

## Stage 0 — Inventory

### 0.1 Получили доступ от провайдера

Провайдер прислал:
- IP-адрес сервера
- root password (или ssh-key)
- ссылку на Proxmox web UI: `https://<IP>:8006`

Пример из реального опыта:
```
URL: https://94.126.207.67:8006
Подсеть: 94.126.207.66/31
Gateway: 94.126.207.66
→ IP сервера = 94.126.207.67 (другой адрес из /31)
```

**Понять подсеть** /31: в /31 всего 2 адреса. Один — gateway (.66), второй — твой сервер (.67). Это норма для point-to-point links у провайдеров.

### 0.2 Зайти и собрать инфо

```bash
# SSH
ssh root@94.126.207.67

# Что за железо
dmidecode -t system | head -20
lscpu
free -h
lsblk
ip a
```

Записать:
- CPU model + cores
- RAM total
- Disk model + sizes (`lsblk` или `nvme list`)
- Network interfaces + speeds (`ethtool eth0 | grep Speed`)

### 0.3 Зайти в Proxmox web UI

В браузере: `https://<IP>:8006`. Login: `root`, пароль провайдера.

Проверить:
- **Datacenter → Summary** — версия Proxmox (должна быть 8.x)
- **Node → Summary** — uptime, hardware
- **Node → Disks** — все диски видны
- **Node → Network** — текущие интерфейсы

Если Proxmox 7.x или старше — уговорить провайдера переустановить на 8.x, или сделать самому через ISO. Эта документация для 8.x.

---

## Stage 1 — Single-node Proxmox

### 1.1 Базовая конфигурация системы

#### 1.1.1 Обновить пакеты

```bash
# Подписки no-subscription (бесплатная репа)
echo "deb http://download.proxmox.com/debian/pve $(lsb_release -cs) pve-no-subscription" \
    > /etc/apt/sources.list.d/pve-no-subscription.list

# Удалить enterprise repo (которая требует подписку)
rm -f /etc/apt/sources.list.d/pve-enterprise.list
rm -f /etc/apt/sources.list.d/ceph.list   # если есть, до настройки CEPH

apt update
apt full-upgrade -y
```

Если апгрейд просит ребут — отложить до окончания всех настроек, чтобы ребутнуть один раз.

#### 1.1.2 Дисабль nag-screen (опционально, для no-subscription)

```bash
sed -i.bak "s/data.status === 'Active'/false/g" \
    /usr/share/javascript/proxmox-widget-toolkit/proxmoxlib.js
systemctl restart pveproxy
```

#### 1.1.3 Установить полезные утилиты

```bash
apt install -y \
    parted \
    htop \
    iotop \
    iftop \
    tmux \
    curl \
    wget \
    vim \
    git \
    zfsutils-linux \
    sanoid                     # для авто-снапшотов ZFS (опционально)
```

### 1.2 Загрузить Ubuntu cloud image

```bash
cd /var/lib/vz/template/iso

# Server ISO (для ручной установки одной VM, опционально)
wget https://releases.ubuntu.com/noble/ubuntu-24.04.4-live-server-amd64.iso

# Cloud image (для template — основное)
wget https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img
```

Проверка в UI: **Datacenter → local → ISO Images** — должны увидеть оба файла.

### 1.3 ZFS — создание datapool (Layout-2, default)

#### 1.3.1 Понять текущее состояние дисков

```bash
lsblk
# Например:
# nvme0n1  1.9T disk
# ├─nvme0n1p1   1G part /boot/efi    ← Proxmox boot
# ├─nvme0n1p2   1G part [SWAP]       ← swap
# └─nvme0n1p3 1.9T part /            ← Proxmox root (ext4 или ZFS)
# nvme1n1  1.9T disk                 ← пустой
```

Если Proxmox installer отдал тебе один диск целиком (root на ext4) — у тебя 1 свободный диск (`nvme1n1`). Для bootstrap-а с 2 дисками — этого достаточно. Datapool сделаем на `nvme1n1` целиком; rpool останется на `nvme0n1` без mirror'а (single point of failure для OS — компромисс начала).

#### 1.3.2 Партиционировать второй диск

```bash
# Из bootstrap-заметок — простой способ через sgdisk
sgdisk -n 0:0:0 -t 0:bf01 /dev/nvme1n1
# -n 0:0:0 — следующая партиция, от начала до конца
# -t 0:bf01 — type ZFS Solaris root

partprobe /dev/nvme1n1

lsblk /dev/nvme1n1
# expected:
# nvme1n1
# └─nvme1n1p1  1.9T part
```

Альтернатива через `parted` (если хочется явных границ):

```bash
parted /dev/nvme1n1 --script mklabel gpt
parted /dev/nvme1n1 --script mkpart primary 1MiB 100%
partprobe /dev/nvme1n1
```

#### 1.3.3 Создать ZFS pool

```bash
zpool create -f \
    -o ashift=12 \
    -O compression=lz4 \
    -O atime=off \
    -O xattr=sa \
    -O acltype=posixacl \
    -O mountpoint=/datapool \
    datapool /dev/nvme1n1p1

zpool list
zpool status datapool
zfs list
```

Ожидаемое:
```
NAME        SIZE  ALLOC   FREE
datapool   1.81T   192K  1.81T
```

#### 1.3.4 Создать датасеты внутри datapool

```bash
zfs create -o mountpoint=none datapool/linstor
zfs set recordsize=64K datapool/linstor
zfs set sync=standard datapool/linstor
zfs set primarycache=metadata datapool/linstor
zfs set redundant_metadata=most datapool/linstor

zfs list
```

#### 1.3.5 Зарегистрировать в Proxmox UI

UI: **Datacenter → Storage → Add → ZFS**:
- ID: `datapool`
- ZFS Pool: `datapool/linstor` (выбираем dataset, не сам pool)
- Content: `Disk image, Container`
- ✅ **Thin provision**
- Block size: `8K`
- Save

Сейчас этот storage используется напрямую без LINSTOR (для теста). После Stage 4 мы перерегистрируем как LINSTOR-managed.

### 1.4 Настроить сеть

См. полный guide в [01-network.md](01-network.md). Минимум для single-node:

#### 1.4.1 Backup текущего конфига

```bash
cp /etc/network/interfaces /etc/network/interfaces.bak
```

#### 1.4.2 Отредактировать `/etc/network/interfaces`

```bash
vim /etc/network/interfaces
```

Минимальная конфигурация (только public + NAT для VM):

```
auto lo
iface lo inet loopback

auto eth0
iface eth0 inet manual

# Public bridge (для Proxmox UI и VM с public IP)
auto vmbr0
iface vmbr0 inet static
        address 94.126.207.67/31
        gateway 94.126.207.66
        bridge-ports eth0
        bridge-stp off
        bridge-fd 0

# NAT bridge для внутренних VM
auto vmbr2
iface vmbr2 inet static
        address 10.0.1.1/24
        bridge-ports none
        bridge-stp off
        bridge-fd 0
        post-up   iptables -t nat -A POSTROUTING -s '10.0.1.0/24' -o vmbr0 -j MASQUERADE
        post-down iptables -t nat -D POSTROUTING -s '10.0.1.0/24' -o vmbr0 -j MASQUERADE
```

#### 1.4.3 Включить IP forwarding

```bash
echo 'net.ipv4.ip_forward=1' > /etc/sysctl.d/99-proxmox-forward.conf
sysctl -p /etc/sysctl.d/99-proxmox-forward.conf
```

#### 1.4.4 Применить конфиг

```bash
ifreload -a
```

#### 1.4.5 Проверить

```bash
ip a show vmbr0
ip a show vmbr2
cat /proc/sys/net/ipv4/ip_forward
iptables -t nat -L POSTROUTING -n -v
```

В UI: **Node → Network** — должны быть active vmbr0 и vmbr2.

### 1.5 Создать первую тестовую VM (через UI)

Чтобы убедиться что всё работает.

#### 1.5.1 Через UI

**Create VM:**
- VM ID: `100`
- Name: `test-ubuntu`

**OS:**
- Use CD/DVD disc image: `local:iso/ubuntu-24.04.4-live-server-amd64.iso`
- Type: Linux, Version: 6.x

**System:**
- Graphic card: **VirtIO-GPU**
- Machine: **q35**
- BIOS: **OVMF (UEFI)**
- ✅ Add EFI Disk → Storage: `datapool`
- SCSI Controller: **VirtIO SCSI single**
- ✅ Qemu Agent

**Disks:**
- Bus: SCSI 0
- Storage: `datapool`
- Disk size: 50 GiB
- Cache: Write-back
- ✅ Discard
- ✅ SSD emulation
- ✅ IO thread

**CPU:**
- Cores: 2
- Type: host

**Memory:**
- 4096 MiB
- ❌ **Снять Ballooning Device** (для production VM лучше зафиксированная RAM)

**Network:**
- Bridge: `vmbr2` (NAT)
- Model: VirtIO

**Confirm → Finish**

#### 1.5.2 Запустить и установить ОС

В UI: VM 100 → Start → Console.

Установка Ubuntu вручную:
- Network: subnet `10.0.1.0/24`, address `10.0.1.100`, gateway `10.0.1.1`, DNS `8.8.8.8,1.1.1.1`
- Disk: всё как есть (один диск, ZFS или ext4)
- Username/SSH key

После установки:
```bash
# С Proxmox-хоста
ssh user@10.0.1.100
# должно работать
```

VM имеет интернет через NAT (`vmbr2 → vmbr0 → public`).

### 1.6 Validation Stage 1

```bash
# Pool здоров
zpool status datapool                    # ONLINE

# Сеть
ping -c 3 8.8.8.8                        # из Proxmox в интернет
ssh user@10.0.1.100 ping -c 3 8.8.8.8    # из VM в интернет

# Storage в Proxmox
pvesm status                             # datapool — active

# VM работает
qm list
# 100 test-ubuntu running
```

Если всё OK — Stage 1 закрыта.

---

## Stage 2 — Добавили ещё 2 диска (всего 4)

Сценарий: пришла поставка ещё 2 NVMe, вставили в сервер. Теперь `nvme2n1` и `nvme3n1` пустые.

### 2.1 Проверить что система видит новые диски

```bash
lsblk | grep nvme
# expected:
# nvme0n1   1.9T  (boot+root)
# nvme1n1   1.9T  (datapool member)
# nvme2n1   1.9T  (NEW)
# nvme3n1   1.9T  (NEW)
```

Если не видны — проверить:
- Физически вставлены и закреплены
- BIOS видит (через IPMI/iDRAC console)
- `dmesg | grep -i nvme` — драйвера зарегистрировали диск

### 2.2 Партиционировать новые диски

```bash
for d in /dev/nvme2n1 /dev/nvme3n1; do
    sgdisk -n 0:0:0 -t 0:bf01 $d
    partprobe $d
done

lsblk | grep nvme
```

### 2.3 Расширить datapool (Layout-2)

```bash
zpool add -f datapool /dev/nvme2n1p1 /dev/nvme3n1p1

zpool status datapool
zpool list datapool
```

Ожидаемое:
```
  pool: datapool
 state: ONLINE
config:
        NAME            STATE     READ WRITE CKSUM
        datapool        ONLINE       0     0     0
          nvme1n1p1     ONLINE       0     0     0
          nvme2n1p1     ONLINE       0     0     0   ← новый
          nvme3n1p1     ONLINE       0     0     0   ← новый
```

Pool теперь ~5.4 ТБ (3 × 1.8). Старые данные не перебалансируются автоматически — новые writes пойдут больше на новые vdev'ы (балансировка по mostly-empty).

### 2.4 Если хочется добавить mirror'инг для rpool

Если на оригинальной установке rpool был на одном диске (`nvme0n1`), можно добавить второй диск как mirror:

```bash
# Сначала посмотреть, какая партиция корневая
zpool status rpool
# rpool
#   nvme0n1p3  ← или похожее

# Скопировать партицию-структуру с nvme0n1 на nvme2n1 (или другой целевой диск)
sgdisk --replicate=/dev/nvme2n1 /dev/nvme0n1
sgdisk --randomize-guids /dev/nvme2n1   # генерируем уникальные GUID

# Превратить в mirror
zpool attach rpool /dev/nvme0n1p3 /dev/nvme2n1p3

# Resilver запустится:
zpool status rpool
# scan: resilver in progress...
```

Это сделает root отказоустойчивым к отказу одного диска. Для production — стоит. Для bootstrap — можно отложить.

### 2.5 Validation Stage 2

```bash
zpool list datapool                # размер вырос
zpool iostat -v 5                  # writes идут на все vdev'ы
zfs list datapool/linstor          # available увеличился
```

---

## Stage 3 — Добавили вторую и третью ноды

### 3.1 Подготовка нод (повторить для Node 2 и Node 3)

#### 3.1.1 Установить Proxmox

Если провайдер не установил — поставить с ISO. Принципиальные параметры:
- File system: ZFS RAID-1 (или RAID-Z) если есть несколько дисков
- Hostname: `node2.local` (or `node3.local`) — должно совпадать с тем, что будет в LINSTOR
- IP: соответствующий статический

#### 3.1.2 Базовая конфигурация (как в Stage 1.1)

```bash
# Заходим на новую ноду
ssh root@<node2-public-ip>

# No-subscription repo
echo "deb http://download.proxmox.com/debian/pve $(lsb_release -cs) pve-no-subscription" \
    > /etc/apt/sources.list.d/pve-no-subscription.list
rm -f /etc/apt/sources.list.d/pve-enterprise.list

apt update && apt full-upgrade -y

apt install -y parted htop iotop iftop tmux curl wget vim git zfsutils-linux
```

#### 3.1.3 Создать datapool на новой ноде

```bash
# Партиционировать диски (как в Stage 2.2)
for d in /dev/nvme1n1 /dev/nvme2n1 /dev/nvme3n1; do
    sgdisk -n 0:0:0 -t 0:bf01 $d
    partprobe $d
done

# Создать pool
zpool create -f \
    -o ashift=12 \
    -O compression=lz4 \
    -O atime=off \
    -O xattr=sa \
    -O acltype=posixacl \
    -O mountpoint=/datapool \
    datapool \
        /dev/nvme1n1p1 \
        /dev/nvme2n1p1 \
        /dev/nvme3n1p1

zfs create -o mountpoint=none datapool/linstor
zfs set recordsize=64K datapool/linstor
zfs set sync=standard datapool/linstor
zfs set primarycache=metadata datapool/linstor
zfs set redundant_metadata=most datapool/linstor
```

#### 3.1.4 Настроить сеть (cluster + storage)

См. [01-network.md](01-network.md). На каждой ноде добавляем `vmbr1` (cluster mgmt) и `vmbr3` (storage 25GbE).

```
# На Node 2 — пример
auto eth1
iface eth1 inet manual

auto vmbr1
iface vmbr1 inet static
        address 10.0.0.2/24                  # ← .2 для Node 2
        bridge-ports eth1
        bridge-stp off
        bridge-fd 0

auto eth2
iface eth2 inet manual
        mtu 9000

auto vmbr3
iface vmbr3 inet static
        address 10.0.2.2/24                  # ← .2 для Node 2
        bridge-ports eth2
        bridge-stp off
        bridge-fd 0
        mtu 9000
```

```bash
ifreload -a
```

Аналогично для Node 3 — `.3` адреса.

### 3.2 Проверить связность между нодами

```bash
# С Node 1
ping -c 3 10.0.0.2     # cluster mgmt до Node 2
ping -c 3 10.0.0.3     # до Node 3
ping -c 3 10.0.2.2     # storage до Node 2
ping -c 3 10.0.2.3     # storage до Node 3

# Jumbo frames на storage
ping -M do -s 8972 -c 3 10.0.2.2
ping -M do -s 8972 -c 3 10.0.2.3
```

Все должны отвечать. Если нет — разбираться с физикой/switch'ами.

### 3.3 Создать Proxmox cluster

#### 3.3.1 На Node 1 — инициализация

```bash
# Cluster network — выделенный VLAN на vmbr1
pvecm create k8s-cluster --link0 10.0.0.1

# Проверить
pvecm status
pvecm nodes
```

`--link0 10.0.0.1` сообщает corosync, какой IP использовать для heartbeats. Используем cluster mgmt сеть (vmbr1).

#### 3.3.2 На Node 2 и Node 3 — присоединение

```bash
# На Node 2
pvecm add 10.0.0.1 --link0 10.0.0.2

# На Node 3
pvecm add 10.0.0.1 --link0 10.0.0.3
```

При первом запуске pvecm попросит подтвердить SSH-fingerprint Node 1 и ввести root password Node 1.

#### 3.3.3 Проверить кластер

С любой ноды:

```bash
pvecm status
# expected:
# Quorum information
# Date:       ...
# Quorum:     2 (Quorum Lost — если меньше 2)
# Nodes:      3
#
# Membership information
# Nodeid      Votes Name
#      1          1 node1
#      2          1 node2
#      3          1 node3
```

В UI Datacenter → Cluster видим 3 ноды.

#### 3.3.4 Дополнительный link для corosync (HA)

Для production — добавить второй ring через storage сеть, чтобы corosync не падал при проблеме на cluster mgmt:

```bash
# На каждой ноде — добавить link1
pvecm updatecerts
# Edit /etc/pve/corosync.conf вручную (на одной ноде, синкается):

vim /etc/pve/corosync.conf
# Добавить ring1_addr к каждой node:
#  ring0_addr: 10.0.0.1
#  ring1_addr: 10.0.2.1
# и в totem:
#  interface {
#    linknumber: 1
#  }
```

Это рискованно — некорректный edit ломает кластер. Делать только когда базовая конфигурация работает.

### 3.4 Расшарить ISO templates между нодами (опционально)

Чтобы не качать ISO на каждую ноду:

UI: **Datacenter → Storage → local → Edit → Nodes**: оставить все. Файлы в `/var/lib/vz/template/iso` шарятся через NFS / синкаются вручную / используется shared storage.

Для bootstrap — просто скачать на каждой ноде (см. Stage 1.2).

### 3.5 Validation Stage 3

```bash
pvecm status                           # 3 ноды, quorum OK
pvesm status                           # на всех нодах datapool active

# Тест миграции VM (если есть VM)
qm migrate 100 node2 --online
qm migrate 100 node1 --online
```

---

## Stage 4 — LINSTOR + DRBD

См. [03-linstor-and-drbd.md](03-linstor-and-drbd.md) для контекста.

### 4.1 Установить пакеты на каждой ноде

```bash
# Добавить LINBIT репо
wget -qO - https://packages.linbit.com/public.key | apt-key add -
cat > /etc/apt/sources.list.d/linbit.list <<EOF
deb http://packages.linbit.com/public/ proxmox-8 drbd-9
EOF

apt update

# На всех нодах
apt install -y \
    drbd-dkms \
    drbd-utils \
    linstor-satellite \
    linstor-client

# Только на Node 1 — controller
apt install -y linstor-controller linstor-proxmox

# Только на Node 2 и Node 3 — proxmox plugin
apt install -y linstor-proxmox

# Загрузить модуль DRBD
modprobe drbd
modinfo drbd | head -3
# expected: version: 9.x.x
```

### 4.2 Запустить сервисы

```bash
# На Node 1
systemctl enable --now linstor-controller

# На всех нодах
systemctl enable --now linstor-satellite

# Проверка
systemctl status linstor-satellite
```

### 4.3 Конфигурация LINSTOR client

На каждой ноде:

```bash
mkdir -p /etc/linstor
cat > /etc/linstor/linstor-client.conf <<EOF
[global]
controllers=10.0.2.1
EOF
```

Тест:

```bash
linstor node list
# expected: пустая таблица или ошибка connection (если controller не запустился)
```

### 4.4 Зарегистрировать ноды в LINSTOR

С Node 1:

```bash
linstor node create node1 10.0.2.1 --node-type Combined
linstor node create node2 10.0.2.2 --node-type Satellite
linstor node create node3 10.0.2.3 --node-type Satellite

# Проверить
linstor node list
# State должно быть Online для всех
```

### 4.5 Создать storage pools

```bash
linstor storage-pool create zfsthin node1 sp-data datapool/linstor
linstor storage-pool create zfsthin node2 sp-data datapool/linstor
linstor storage-pool create zfsthin node3 sp-data datapool/linstor

linstor storage-pool list
```

### 4.6 Создать 3 resource groups

```bash
# rg-local-single (replica=1)
linstor resource-group create rg-local-single \
    --storage-pool sp-data \
    --place-count 1
linstor volume-group create rg-local-single

# rg-net-async (replica=2, Proto A)
linstor resource-group create rg-net-async \
    --storage-pool sp-data \
    --place-count 2
linstor resource-group set-property rg-net-async DrbdOptions/Net/protocol A
linstor resource-group set-property rg-net-async DrbdOptions/auto-quorum suspend-io
linstor resource-group set-property rg-net-async DrbdOptions/Resource/quorum majority
linstor resource-group set-property rg-net-async DrbdOptions/Resource/on-no-quorum io-error
linstor resource-group set-property rg-net-async DrbdOptions/auto-add-quorum-tiebreaker yes
linstor volume-group create rg-net-async

# rg-net-sync (replica=2, Proto C)
linstor resource-group create rg-net-sync \
    --storage-pool sp-data \
    --place-count 2
linstor resource-group set-property rg-net-sync DrbdOptions/Net/protocol C
linstor resource-group set-property rg-net-sync DrbdOptions/auto-quorum suspend-io
linstor resource-group set-property rg-net-sync DrbdOptions/Resource/quorum majority
linstor resource-group set-property rg-net-sync DrbdOptions/Resource/on-no-quorum io-error
linstor resource-group set-property rg-net-sync DrbdOptions/auto-add-quorum-tiebreaker yes
linstor volume-group create rg-net-sync

# Проверить
linstor resource-group list
```

### 4.7 Тест-том

```bash
# Создать
linstor resource-group spawn-resources rg-net-sync test-vol-1 1G

# Проверить размещение
linstor resource list
# expected:
# test-vol-1 на node1 (UpToDate, diskful)
# test-vol-1 на node2 (UpToDate, diskful)
# test-vol-1 на node3 (TieBreaker, diskless)

# Найти DRBD device
linstor resource list-volumes
# /dev/drbd1000 (или похожее)

# Записать что-нибудь
mkfs.ext4 /dev/drbd/by-res/test-vol-1/0
mount /dev/drbd/by-res/test-vol-1/0 /mnt
echo "hello drbd" > /mnt/test.txt
umount /mnt

# Удалить тест
linstor resource-definition delete test-vol-1
```

### 4.8 Зарегистрировать LINSTOR storage в Proxmox

Edit `/etc/pve/storage.cfg`:

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

В UI Datacenter → Storage — должны появиться 3 новых LINSTOR storage.

### 4.9 Validation Stage 4

```bash
linstor node list                  # все Online
linstor storage-pool list          # все sp-data есть на 3 нодах
linstor resource-group list        # 3 RG
pvesm status                       # linstor-* — active
```

---

## Stage 5 — Terraform + cloud-init

См. [06-terraform-cloud-init.md](06-terraform-cloud-init.md).

### 5.1 Создать API token Proxmox (см. 06 §3.1)

UI: Datacenter → Permissions:
- User: `terraform@pve` (создать)
- API Token: `terraform@pve!automation`, без Privilege Separation
- ACL: path=/, role=Administrator

Сохранить secret token.

### 5.2 Включить snippets storage

```bash
pvesm set local --content vztmpl,iso,snippets,backup,images
```

### 5.3 Создать VM template (см. 06 §4)

```bash
# На Node 1
cd /var/lib/vz/template/iso

# Скачать (если ещё нет)
wget https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img

# Создать template VM
qm create 9000 \
    --name ubuntu-24.04-tmpl \
    --memory 2048 --cores 2 \
    --net0 virtio,bridge=vmbr2 \
    --machine q35 --bios ovmf \
    --efidisk0 linstor-local:0,efitype=4m,pre-enrolled-keys=0 \
    --scsihw virtio-scsi-single \
    --serial0 socket --vga serial0 \
    --agent enabled=1 --ostype l26

# Импортировать диск
qm importdisk 9000 /var/lib/vz/template/iso/noble-server-cloudimg-amd64.img linstor-local

# Подключить
qm set 9000 --scsi0 linstor-local:vm-9000-disk-1,iothread=1,ssd=1,discard=on,cache=writeback
qm set 9000 --ide2 linstor-local:cloudinit
qm set 9000 --boot order=scsi0

# Конвертировать в template
qm template 9000

qm config 9000
```

### 5.4 Подготовить Terraform-проект

На development-машине (НЕ на Proxmox-сервере):

```bash
mkdir -p ~/work/k8s-proxmox-tf
cd ~/work/k8s-proxmox-tf

# Установить Terraform
brew install terraform                  # macOS
# или
sudo apt install terraform              # Ubuntu

# Создать структуру (см. 06 §5.1)
mkdir -p modules/proxmox-vm cloud-init
```

### 5.5 Заполнить .tf файлы

См. [06-terraform-cloud-init.md](06-terraform-cloud-init.md) §5 — копировать готовые шаблоны:
- `main.tf`
- `variables.tf`
- `terraform.tfvars` (с реальными данными — НЕ в git!)
- `modules/proxmox-vm/main.tf`
- `modules/proxmox-vm/variables.tf`

### 5.6 Терраформ init + plan

```bash
terraform init
terraform plan
```

Если есть ошибки — разбираться. Самые частые:
- API token неправильный → проверить
- VM ID конфликтует → выбрать другой
- LINSTOR storage `linstor-sync` не существует в Proxmox → проверить /etc/pve/storage.cfg

### 5.7 Тест на 1 VM

Изменить `main.tf` чтобы создавался только 1 VM:

```hcl
module "k8s_master_1" {
  source = "./modules/proxmox-vm"
  name           = "test-vm"
  node           = "node1"
  vm_id          = 999
  datastore_id   = "linstor-local"
  cores          = 2
  memory_mb      = 2048
  disk_size_gb   = 30
  ip_cidr        = "10.0.1.99/24"
  ssh_public_key = var.ssh_public_key
}
```

```bash
terraform apply
# yes

# Подождать 2-3 мин

ssh ubuntu@10.0.1.99       # должно работать
```

Если работает — удалить тестовый VM:

```bash
terraform destroy
# yes
```

### 5.8 Validation Stage 5

```bash
# Из dev-машины
terraform plan -target=module.test_only       # должен показать "no changes"

# В Proxmox
qm list                                       # template 9000 должен быть Stopped (templates не запускаются)
```

---

## Stage 6 — Provision K8s VM

### 6.1 Заполнить main.tf на полные 12 VM

См. [06-terraform-cloud-init.md](06-terraform-cloud-init.md) §5.7 — пример с 3 master + 9 worker.

Базовое распределение:
- **3 master** на разных нодах (node1, node2, node3) — `linstor-sync` для etcd
- **9 worker** распределены по 3 на ноду — `linstor-async` для root-disk (cattle, не pets)

### 6.2 Запустить provisioning

```bash
cd ~/work/k8s-proxmox-tf

terraform plan
# смотрим, что создаётся 12 VM

terraform apply
# yes
# ждём 10-15 минут
```

### 6.3 Проверить все VM

```bash
# В Proxmox UI или через qm list на ноде
qm list

# С dev-машины
for ip in 10.0.1.{10,11,12,20,21,22,23,24,25,26,27,28}; do
    echo "=== $ip ==="
    ssh -o ConnectTimeout=3 -o StrictHostKeyChecking=no ubuntu@$ip 'hostname; uptime'
done
```

Все должны отвечать. Если какая-то VM не отвечает — проверить:
- В Proxmox UI: статус VM (Running?)
- Console VM: cloud-init завершился?

### 6.4 Подготовить Ansible inventory для k8s-ansible

```bash
cd ~/work/k8s-ansible      # родительский проект
mkdir -p hosts-vars-override
```

Создать `hosts-vars-override/hosts.yaml`:

```yaml
all:
  children:
    managers:
      hosts:
        manager-1:
          ansible_host: 10.0.1.10
          ansible_user: ubuntu
          ansible_ssh_private_key_file: ~/.ssh/id_ed25519
          ansible_become: yes
          internal_ip: 10.0.1.10
          api_server_advertise_address: 10.0.1.10
          api_server_bind_port: 6443
          is_master: true                  # ОДИН манагер должен иметь true
          node_labels:
            - "node-role.kubernetes.io/master="
        manager-2:
          ansible_host: 10.0.1.11
          ansible_user: ubuntu
          ansible_ssh_private_key_file: ~/.ssh/id_ed25519
          ansible_become: yes
          internal_ip: 10.0.1.11
          api_server_advertise_address: 10.0.1.11
          api_server_bind_port: 6443
        manager-3:
          ansible_host: 10.0.1.12
          ansible_user: ubuntu
          ansible_ssh_private_key_file: ~/.ssh/id_ed25519
          ansible_become: yes
          internal_ip: 10.0.1.12
          api_server_advertise_address: 10.0.1.12
          api_server_bind_port: 6443

    workers:
      hosts:
        worker-1:
          ansible_host: 10.0.1.20
          ansible_user: ubuntu
          ansible_ssh_private_key_file: ~/.ssh/id_ed25519
          ansible_become: yes
          internal_ip: 10.0.1.20
        # ... worker-2 ... worker-9
```

### 6.5 Запустить bootstrap K8s через Ansible

См. [bootstrap-and-ha.md](../.claude/rules/bootstrap-and-ha.md):

```bash
cd ~/work/k8s-ansible

# 1. Подготовка нод (все)
for h in manager-1 manager-2 manager-3 worker-1 worker-2 worker-3 worker-4 worker-5 worker-6 worker-7 worker-8 worker-9; do
    ansible-playbook -i hosts-vars/ -i hosts-vars-override/ \
        playbook-system/node-install.yaml --limit $h
done

# 2. Init первого manager (is_master: true)
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ \
    playbook-system/cluster-init.yaml --limit manager-1

# 3. Cilium pre-flight (для firewall — должно быть до join)
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ \
    playbook-app/cilium-install.yaml

# 4. Join остальных manager'ов
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ \
    playbook-system/manager-join.yaml --limit manager-2
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ \
    playbook-system/manager-join.yaml --limit manager-3

# 5. Join worker'ов
for h in worker-1 worker-2 worker-3 worker-4 worker-5 worker-6 worker-7 worker-8 worker-9; do
    ansible-playbook -i hosts-vars/ -i hosts-vars-override/ \
        playbook-system/worker-join.yaml --limit $h
done
```

### 6.6 Установка приложений в K8s

```bash
for c in cert-manager external-secrets vault traefik metrics-server longhorn \
         mon-system argocd; do
    ansible-playbook -i hosts-vars/ -i hosts-vars-override/ \
        playbook-app/$c-install.yaml
done
```

См. [commands-reference.md](../.claude/rules/commands-reference.md) §2 для полной последовательности.

### 6.7 Validation Stage 6

```bash
# С manager-1
ssh ubuntu@10.0.1.10
sudo kubectl get nodes
# 3 manager + 9 worker, все Ready

sudo kubectl get pods -A
# все pods running (после установки приложений)
```

---

## Финальный чеклист

После всех 6 стадий:

- [ ] 3 ноды Proxmox в cluster, quorum OK
- [ ] На каждой ноде datapool ZFS ~7.8 ТБ
- [ ] LINSTOR controller на Node 1, satellites на всех
- [ ] 3 resource groups: rg-local-single, rg-net-async, rg-net-sync
- [ ] LINSTOR storage в Proxmox: linstor-sync, linstor-async, linstor-local
- [ ] Terraform template VM (id=9000) создан
- [ ] Terraform apply создал 12 K8s VM
- [ ] Все VM доступны по SSH
- [ ] Kubernetes bootstrap завершён через k8s-ansible
- [ ] Базовые приложения (cilium, cert-manager, vault, ...) развёрнуты

---

## Что дальше

- Регулярная эксплуатация — снапшоты ZFS, мониторинг, scrub'ы
- Backup-стратегия — отдельный проект (PBS, ZFS send, app-level)
- HA controller LINSTOR — миграция на DRBD-replicated controller (см. 03-linstor-and-drbd.md §10)
- При росте кластера — пересмотреть Layout (см. QUESTIONS.md Q1) и CEPH-вопрос (04-ceph.md)

---

## Quick troubleshooting per stage

### Stage 1 — Proxmox не доступен через UI
- Firewall на стороне провайдера? `pvesm status` локально работает?
- `systemctl status pveproxy pvedaemon`

### Stage 2 — `zpool add` ругается «would create unbalanced configuration»
- В существующем pool используется mirror, добавляем stripe — топология разная
- Решение: добавить `-f` для форс, или добавить тоже как mirror

### Stage 3 — `pvecm add` падает с timeout
- Cluster mgmt сеть не работает между нодами
- Проверить `ping 10.0.0.1` с Node 2
- Firewall между нодами на провайдерском уровне

### Stage 4 — `linstor node list` показывает Offline
- linstor-satellite не запустился
- Порт 3366 заблокирован между нодами
- DRBD module не загружен (`modprobe drbd`, `dmesg | grep drbd`)

### Stage 5 — Terraform `connection refused` на Proxmox API
- Token неправильный, или истёк
- В UI Datacenter → Permissions → API Tokens проверить
- TLS cert проблема — `insecure = true` в provider config

### Stage 6 — Ansible bootstrap падает на cluster-init
- Smaller issues: см. [recovery matrix в bootstrap-and-ha.md](../.claude/rules/bootstrap-and-ha.md) §7
- Cilium пре-флайт не выполнен → firewall блокирует join

---

## См. также

- [README.md](README.md) — общая архитектура
- [01-network.md](01-network.md), [02-zfs.md](02-zfs.md), [03-linstor-and-drbd.md](03-linstor-and-drbd.md), [06-terraform-cloud-init.md](06-terraform-cloud-init.md) — детали по компонентам
- [QUESTIONS.md](QUESTIONS.md) — отложенные решения
- [k8s-ansible/CLAUDE.md](../CLAUDE.md) — следующий уровень: K8s + apps

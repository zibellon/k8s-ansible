# Network Design

Сетевая модель Proxmox-кластера: bridges, NAT, storage VLAN, public IP, маршрутизация.

---

## 1. Сетевые роли

В нашем кластере выделены 4 логические сети, каждая со своим назначением:

| Сеть | Назначение | Скорость | Bridge | Подсеть (пример) |
|---|---|---|---|---|
| Public | Внешний доступ к VM с публичным IP, к Proxmox web UI | 1 GbE+ | `vmbr0` | публичная /31 от провайдера |
| Cluster mgmt | Proxmox cluster (corosync, миграция VM, web UI internal) | 1 GbE+ | `vmbr1` | 10.0.0.0/24 |
| VM internal (NAT) | Внутренние VM без публичного IP, выход в интернет через MASQUERADE | 1 GbE | `vmbr2` | 10.0.1.0/24 |
| Storage | LINSTOR/DRBD трафик, ceph если когда-то будет | **25 GbE** | `vmbr3` | 10.0.2.0/24 |

**Почему storage отдельно.** Storage-трафик при sync-репликации Postgres под нагрузкой может насыщать link до пропускной способности NVMe (1+ ГБ/с). Если этот трафик пойдёт через тот же интерфейс, что и public/cluster — задержки на API/management вырастут, latency VM возрастёт. Отдельный физический NIC для storage — индустриальный стандарт.

**Почему cluster-mgmt отдельно от public.** Corosync (Proxmox cluster heartbeat) очень чувствителен к джиттеру. Если 1 GbE интерфейс под public нагрузкой захлёбывается, corosync теряет heartbeats и нода может быть исключена из кластера. Изолированная mgmt-сеть устраняет этот риск.

---

## 2. Физические интерфейсы

Типичная конфигурация на каждой ноде (имена могут отличаться по железу):

```
eth0  — 1 GbE   — uplink в provider, public IP    → vmbr0
eth1  — 1 GbE   — local switch                    → vmbr1 (cluster mgmt)
eth2  — 25 GbE  — storage switch                  → vmbr3 (storage, no IP routing наружу)
```

`vmbr2` (NAT для внутренних VM) — software-only bridge, не привязан к физическому интерфейсу (`bridge-ports none`).

**Если у тебя только 2 физических NIC** (бюджетный сервер): объединить cluster-mgmt и storage на 25 GbE интерфейс через VLAN-теги. Менее красиво, но работает. См. §6 ниже.

---

## 3. Конфигурация `/etc/network/interfaces` — пример Node 1

Это конфигурация первой ноды с публичным IP `94.126.207.67/31` (пример из bootstrap-заметок). Для Node 2 / Node 3 — аналогично, но без public IP.

```bash
auto lo
iface lo inet loopback

# === Public uplink ===
auto eth0
iface eth0 inet manual

auto vmbr0
iface vmbr0 inet static
        address 94.126.207.67/31
        gateway 94.126.207.66
        bridge-ports eth0
        bridge-stp off
        bridge-fd 0
        # Этот bridge — для VM, которым нужен public IP, и для Proxmox web UI

# === Cluster management ===
auto eth1
iface eth1 inet manual

auto vmbr1
iface vmbr1 inet static
        address 10.0.0.1/24
        bridge-ports eth1
        bridge-stp off
        bridge-fd 0
        # Внутренний bridge для Proxmox cluster network (corosync, migration)

# === VM internal (NAT) ===
auto vmbr2
iface vmbr2 inet static
        address 10.0.1.1/24
        bridge-ports none
        bridge-stp off
        bridge-fd 0
        # NAT для VM без публичного IP — выход в интернет через MASQUERADE
        post-up   iptables -t nat -A POSTROUTING -s '10.0.1.0/24' -o vmbr0 -j MASQUERADE
        post-down iptables -t nat -D POSTROUTING -s '10.0.1.0/24' -o vmbr0 -j MASQUERADE

# === Storage (25 GbE) ===
auto eth2
iface eth2 inet manual
        # MTU 9000 — jumbo frames для DRBD-репликации
        mtu 9000

auto vmbr3
iface vmbr3 inet static
        address 10.0.2.1/24
        bridge-ports eth2
        bridge-stp off
        bridge-fd 0
        mtu 9000
        # Storage network — DRBD/LINSTOR трафик
        # IP forwarding по умолчанию off — этот bridge не должен роутить наружу
```

**Включить IP forwarding** (нужно для NAT через `vmbr2` → `vmbr0`):

```bash
echo 'net.ipv4.ip_forward=1' > /etc/sysctl.d/99-proxmox-forward.conf
sysctl -p /etc/sysctl.d/99-proxmox-forward.conf
```

**Применить без ребута:**

```bash
ifreload -a
```

В UI Proxmox после `ifreload` все bridges должны показать `Active: yes`.

---

## 4. Адресация Node 2 / Node 3

Аналогичный конфиг, но:
- Без блока `vmbr0` (public) — если у Node 2/3 нет своего публичного IP
- `vmbr1` адреса: `10.0.0.2/24`, `10.0.0.3/24`
- `vmbr3` адреса: `10.0.2.2/24`, `10.0.2.3/24`
- `vmbr2` остаётся `10.0.1.1/24` на каждой ноде, но с разными NAT-правилами (или, лучше, разные подсети: `10.0.1.0/24`, `10.0.11.0/24`, `10.0.21.0/24` — чтобы избежать пересечений если когда-то появится L3-роутинг между ними)

**Альтернатива** — на Node 2/3 НЕ настраивать `vmbr2` вообще. Все VM с потребностью в NAT поднимать только на Node 1, либо использовать общий L3-роутер (см. §7).

---

## 5. Validation после ifreload

Базовые проверки, которые надо сделать после `ifreload -a`:

```bash
# 1. Bridge поднят и имеет адрес
ip a show vmbr0
ip a show vmbr1
ip a show vmbr2
ip a show vmbr3

# 2. IP forwarding включён
cat /proc/sys/net/ipv4/ip_forward
# expected: 1

# 3. NAT-правило применено
iptables -t nat -L POSTROUTING -n -v
# expected: target=MASQUERADE, source=10.0.1.0/24, out=vmbr0

# 4. Default route через public gateway
ip route show default
# expected: default via 94.126.207.66 dev vmbr0

# 5. Pings внутри bridges
# С Node 1:
ping -c 3 10.0.0.2          # cluster network до Node 2 (после её настройки)
ping -c 3 10.0.2.2          # storage network до Node 2

# 6. MTU 9000 на storage работает
ping -M do -s 8972 -c 3 10.0.2.2
# флаг -M do = "don't fragment", -s 8972 = payload 8972 + 28 ICMP/IP headers = 9000
```

Если последний ping fails с `Frag needed` — где-то на пути MTU 9000 не поддерживается (промежуточный switch). Снизить до 1500 или настроить switch.

---

## 6. Если у тебя только 2 физических NIC

Объединение cluster-mgmt + storage через VLAN на одном 25 GbE NIC. Менее изолированно, но дешевле.

```bash
# eth1 теперь несёт trunk с VLAN 10 (cluster) и VLAN 20 (storage)
auto eth1
iface eth1 inet manual
        mtu 9000

# VLAN 10 → cluster network
auto eth1.10
iface eth1.10 inet manual
        vlan-raw-device eth1

auto vmbr1
iface vmbr1 inet static
        address 10.0.0.1/24
        bridge-ports eth1.10
        bridge-stp off
        bridge-fd 0

# VLAN 20 → storage network
auto eth1.20
iface eth1.20 inet manual
        vlan-raw-device eth1
        mtu 9000

auto vmbr3
iface vmbr3 inet static
        address 10.0.2.1/24
        bridge-ports eth1.20
        bridge-stp off
        bridge-fd 0
        mtu 9000
```

На switch'е соответствующие порты должны быть настроены как trunk с разрешёнными VLAN 10 и 20.

**Минус.** Шторм в storage-сети (например, full DRBD resync) может выжрать всю полосу 25 GbE и corosync на VLAN 10 потеряет heartbeats. Лучше иметь отдельные физические интерфейсы.

---

## 7. Когда нужно больше: bonded interfaces (LACP)

Для production с одной точкой отказа на switch — LACP bond из 2 NIC к двум switch'ам:

```bash
auto bond0
iface bond0 inet manual
        bond-slaves eth2 eth3
        bond-miimon 100
        bond-mode 802.3ad         # LACP
        bond-xmit-hash-policy layer3+4
        mtu 9000

auto vmbr3
iface vmbr3 inet static
        address 10.0.2.1/24
        bridge-ports bond0
        bridge-stp off
        bridge-fd 0
        mtu 9000
```

Switch должен поддерживать LACP и быть настроен симметрично. Удваивает полосу и даёт отказоустойчивость на уровне NIC + switch.

---

## 8. Firewall (опционально, на Proxmox-уровне)

Proxmox имеет встроенный firewall (`iptables` через свой wrapper). На public bridge стоит ограничить доступ к Proxmox web UI (порт 8006):

```bash
# Через UI: Datacenter → Firewall → Rules
# Или вручную в /etc/pve/firewall/cluster.fw

[OPTIONS]
enable: 1

[RULES]
IN ACCEPT -source <твой VPN или admin IP> -p tcp -dport 8006
IN DROP -p tcp -dport 8006
IN ACCEPT -p tcp -dport 22 -source <admin IPs>
```

Если есть VPN (WireGuard, OpenVPN) — admin-доступ только через него. Public 8006 на Proxmox — потенциальная цель брутфорса.

---

## 9. Внутренняя адресация VM — рекомендация

Для VM, которые войдут в Kubernetes-кластер:

| Компонент | Подсеть | Почему |
|---|---|---|
| K8s manager nodes (3 шт) | 10.0.1.10–12 | NAT bridge, internal |
| K8s worker nodes (~9 шт) | 10.0.1.20–28 | NAT bridge, internal |
| K8s service network (внутри K8s) | 10.128.0.0/12 | См. `hosts-vars/k8s-base.yaml` |
| K8s pod network (внутри K8s, Cilium) | 10.64.0.0/10 | См. `hosts-vars/k8s-base.yaml` |
| Public-exposed IPs (через MetalLB / Traefik LB) | reserved | Зависит от стратегии: Cloudflare-tunnel, MetalLB BGP, etc. |

Подсети K8s (service / pod) — внутренние для K8s, не пересекаются с Proxmox bridges, маршрутизируются Cilium.

---

## 10. Anti-patterns

- **Не размещать storage и cluster-mgmt на одном NIC** в production. Шторм репликации убивает кластер.
- **Не выставлять Proxmox web UI (8006) в public напрямую.** Только за VPN или с whitelist.
- **Не использовать STP на bridges Proxmox.** `bridge-stp off` — Proxmox bridges не должны участвовать в STP, это вызывает странные проблемы с миграцией VM.
- **Не забывать `mtu 9000` на storage**, если switch поддерживает. Даёт 5-10% throughput на DRBD-replication.
- **Не использовать одинаковые подсети `vmbr2`** на разных нодах, если планируется L3-роутинг между ними в будущем. Лучше сразу разделить.

---

## 11. Что дальше

- Конфигурация ZFS pools — [02-zfs.md](02-zfs.md)
- Установка LINSTOR поверх — [03-linstor-and-drbd.md](03-linstor-and-drbd.md)
- Полный walkthrough от начала — [07-step-by-step-bootstrap.md](07-step-by-step-bootstrap.md)

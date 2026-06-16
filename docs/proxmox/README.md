# Proxmox Cluster — Enterprise Bare-Metal Setup

Документация по разворачиванию production-ready Proxmox-кластера на bare-metal под workload Kubernetes со stateful-нагрузками (Postgres, Kafka, Clickhouse, Redis, NATS) и требованием zero RPO для критичных данных.

---

## Целевая инсталляция

- **3 физических сервера**, идентичные
- **4 × 2 ТБ NVMe** на каждой ноде (24 ТБ raw на кластер)
- **62 ГБ или 128 ГБ RAM** на ноду
- **1 публичный IP** (на одной из нод)
- **25 GbE** выделенная storage-сеть между нодами
- **~12 виртуальных машин** на кластер, под них Kubernetes
- **Нагрузка цели:** ~10 000 RPS на K8s-нагрузках, включая БД

---

## Архитектура — итоговые решения

### Storage stack

- **ZFS** — локальная файловая система и backing storage на каждой ноде
- **LINSTOR + DRBD** — сетевая блочная репликация между нодами
- **CEPH** — рассмотрен и отложен в backlog (см. [04-ceph.md](04-ceph.md))

### Правило репликации

Максимум **2 физических копии** одного блока в системе. Возможные комбинации:

- **0 копий доп.** = 1 копия данных. Локально, без избыточности.
- **1 копия локально** (= 2 физических копии на разных дисках одной ноды) — ZFS mirror. Только SYNC.
- **1 копия сетевая** (= 2 физических копии на разных нодах) — LINSTOR replica=2. SYNC или ASYNC.

Никогда не комбинируем «локальный mirror + сетевая replica» — это даёт 4 копии и нарушает правило.

### 3 уровня сохранности данных (LINSTOR Resource Groups)

| RG | Физических копий | Защита от диска | Защита от ноды | RPO | Под что |
|---|---|---|---|---|---|
| `rg-local-single` | 1 | ❌ | ❌ | n/a | scratch, build cache |
| `rg-net-async` | 2 (на разных нодах) | ✅ | ⚠️ с потерями | ~10–100 мс | кэши, аналитика, метрики |
| `rg-net-sync` | 2 (на разных нодах) | ✅ | ✅ | 0 | **Postgres, Kafka, Clickhouse, NATS persistence** |

Опциональный 4-й RG `rg-local-mirror` (replica=1 на ZFS mirror) — описан в [QUESTIONS.md](QUESTIONS.md), не используется в default-дизайне.

### Disk layout (по умолчанию — Layout-2 «all-stripe»)

На каждой ноде:

```
4 × 2 ТБ NVMe
├── partition 1 (50 ГБ × 4) → rpool — Proxmox OS
│      ZFS 4-way mirror, 50 ГБ usable, выживает при 3/4 одновременных отказах
└── partition 2 (~1.95 ТБ × 4) → datapool — VM storage
       ZFS stripe из 4 vdev (1 disk per vdev), ~7.8 ТБ usable per node
       Backing для всех LINSTOR storage pools
```

Альтернатива (Layout-1 split-pools с отдельным mirror-пулом) — в [QUESTIONS.md](QUESTIONS.md).

### Provisioning

- **Terraform** (`bpg/proxmox` provider) для декларативного создания VM
- **cloud-init** для bootstrap гостевой OS (Ubuntu 24.04 LTS)
- VM template с cloud-init подготавливается один раз вручную
- VM попадают в один из трёх RG в зависимости от назначения

---

## Структура документации

| Файл | Содержание |
|---|---|
| [README.md](README.md) | Этот файл — обзор и итоговые решения |
| [QUESTIONS.md](QUESTIONS.md) | Открытые вопросы и отложенные решения |
| [01-network.md](01-network.md) | Сетевой дизайн: bridges, NAT, VLAN, storage network |
| [02-zfs.md](02-zfs.md) | ZFS: pools, vdevs, dataset properties, tuning |
| [03-linstor-and-drbd.md](03-linstor-and-drbd.md) | LINSTOR setup, DRBD протоколы, resource groups, кворум |
| [04-ceph.md](04-ceph.md) | Backlog: что это, почему НЕ выбрали, когда вернуться |
| [05-storage-comparison.md](05-storage-comparison.md) | Сводные таблицы и матрицы выбора |
| [06-terraform-cloud-init.md](06-terraform-cloud-init.md) | Provisioning VM через Terraform + cloud-init |
| [07-step-by-step-bootstrap.md](07-step-by-step-bootstrap.md) | Пошаговый walkthrough от первого сервера до готовности K8s |

---

## Порядок чтения

**Первое прочтение** — для понимания общей картины:
1. README.md (этот файл)
2. [05-storage-comparison.md](05-storage-comparison.md) — контекст выборов
3. [07-step-by-step-bootstrap.md](07-step-by-step-bootstrap.md) — что и как делать

**Когда выполняешь работу** — справочники по теме:
- Сеть → [01-network.md](01-network.md)
- ZFS-команды → [02-zfs.md](02-zfs.md)
- LINSTOR-команды → [03-linstor-and-drbd.md](03-linstor-and-drbd.md)
- Terraform → [06-terraform-cloud-init.md](06-terraform-cloud-init.md)

**Перед production-decision** — обязательно прочитать:
- [QUESTIONS.md](QUESTIONS.md) — отложенные решения, влияющие на дизайн
- [04-ceph.md](04-ceph.md) — понимание альтернативы

---

## Архитектура — диаграмма

```
                       Public IP (например, 94.126.207.67/31)
                                    │
                                    ▼
            ┌─────────────────────────────────────────────────┐
            │  Node 1 (Proxmox)         vmbr0 (public bridge) │
            │                          ├── VM with public IP  │
            │                          │                      │
            │                          vmbr2 (NAT bridge)     │
            │                          ├── VM 10.0.1.x        │
            │                          ├── ...                │
            │                          │                      │
            │   datapool (ZFS, 7.8TB)   vmbr3 storage 25GbE   │
            │   ├── LINSTOR satellite      │                  │
            │   └── 12 VM zvols            │                  │
            └──────────────────────────────┼──────────────────┘
                                           │
                                  25 GbE switch / direct
                                           │
            ┌──────────────────────────────┼──────────────────┐
            │  Node 2 (Proxmox)            │                  │
            │   datapool (ZFS, 7.8TB)         vmbr3 25GbE     │
            │   LINSTOR satellite + DRBD                      │
            └─────────────────────────────────────────────────┘

            ┌─────────────────────────────────────────────────┐
            │  Node 3 (Proxmox)                                │
            │   datapool (ZFS, 7.8TB)         vmbr3 25GbE     │
            │   LINSTOR satellite + DRBD diskless tiebreaker  │
            └─────────────────────────────────────────────────┘
```

LINSTOR controller в начальной конфигурации живёт на Node 1 как systemd-сервис. HA-конфигурация (controller на DRBD volume с failover) — продвинутая тема, описана в [03-linstor-and-drbd.md](03-linstor-and-drbd.md) §HA controller.

---

## Что НЕ покрывает эта документация

- **Установка Proxmox с нуля.** Предполагаем, что Proxmox уже установлен на первой ноде провайдером или через ISO. Bootstrap начинается с момента «есть доступ в Proxmox web UI и SSH».
- **Настройка K8s внутри VM.** Это делается отдельно через [k8s-ansible](../CLAUDE.md) (родительский проект).
- **Backup-стратегия.** PBS (Proxmox Backup Server) и снапшот-политики ZFS — отдельная тема, не в scope этой инсталляции.
- **Hardware-troubleshooting.** Замена дисков, RAID-контроллеры, BMC/IPMI — выходит за рамки.

---

## Связь с проектом k8s-ansible

После того как Proxmox-кластер развёрнут и через Terraform созданы VM, дальнейший bootstrap Kubernetes идёт через [k8s-ansible](../CLAUDE.md). VM, созданные Terraform'ом, попадают в инвентарь Ansible как `managers` / `workers`, и применяется бутстрап-последовательность из [bootstrap-and-ha.md](../.claude/rules/bootstrap-and-ha.md):

```
full-node-install.yaml → cluster-init.yaml → manager-join.yaml → worker-join.yaml
```

Затем разворачиваются приложения (cilium → cert-manager → external-secrets → vault → ...).

# Open Questions & Deferred Decisions

Здесь живут решения, которые ещё не приняты — но отложены сознательно. По мере прохождения по документации возвращайся сюда и проставляй финальные ответы.

Каждый вопрос имеет:
- **Контекст** — почему это вообще вопрос
- **Опции** — варианты с trade-offs
- **Влияние** — что в дизайне зависит от решения
- **Текущая default** — что выбрано «временно» в остальной документации
- **Pending decision** — что нужно решить

---

## Q1. Disk Layout: Layout-1 (split pools) vs Layout-2 (all-stripe)

**Контекст.** На каждой ноде 4 × 2 ТБ NVMe. ZFS позволяет несколько подходов к организации этих дисков в пулы. Выбор влияет на полезную ёмкость и доступные resource groups.

**Опции:**

### Layout-1 — Split pools (поддерживает все 4 RG)

```
4 NVMe × 2 ТБ
├── 2 диска → pool-stripe (ZFS stripe vdev)  = 4 ТБ полезных
│   ↳ backing: rg-local-single, rg-net-async, rg-net-sync
└── 2 диска → pool-mirror (ZFS mirror vdev)  = 2 ТБ полезных
    ↳ backing: rg-local-mirror
```

- Полезная ёмкость на ноду: **6 ТБ** (4 + 2)
- Полезная ёмкость кластера: 18 ТБ raw
- Все 4 RG доступны
- Operational complexity: выше (управление двумя пулами)

### Layout-2 — All-stripe (3 RG из 4)

```
4 NVMe × 2 ТБ → pool-stripe (ZFS stripe vdev из 4 дисков) = 8 ТБ полезных
   ↳ backing: rg-local-single, rg-net-async, rg-net-sync
```

- Полезная ёмкость на ноду: **8 ТБ**
- Полезная ёмкость кластера: 24 ТБ raw
- `rg-local-mirror` недоступен (нет mirror-пула)
- Operational complexity: ниже (один пул)

**Влияние.**

- На [02-zfs.md](02-zfs.md) — какие zpool создавать
- На [03-linstor-and-drbd.md](03-linstor-and-drbd.md) — какие LINSTOR storage pools определять
- На [07-step-by-step-bootstrap.md](07-step-by-step-bootstrap.md) — команды zpool create

**Текущая default:** **Layout-2 (all-stripe)**. Совпадает с уже опробованным подходом в bootstrap-заметках.

**Аргумент за Layout-2.** Для не-критичных VM «защита от диска без сетевой реплики» (`rg-local-mirror`) — нишевый кейс. Если non-critical VM нужна disk-level redundancy — она получает её и от диска, и от ноды через `rg-net-async` (RPO в миллисекундах). А `rg-net-async` дешевле в эксплуатации, чем поддерживать отдельный mirror-пул.

**Аргумент за Layout-1.** Если есть VM, для которых сетевая реплика — overkill (например, локальные scratch-нагрузки тестового кластера), а `rg-local-single` слишком рискованно (single disk = одна потеря и всё пропало) — `rg-local-mirror` даёт удобный middle ground.

**Pending decision:** ⏳ Не подтверждено. Default — Layout-2.

---

## Q2. Количество Resource Groups: 3 vs 4

**Контекст.** Зависит от Q1. Если Layout-2, четвёртый RG (`rg-local-mirror`) физически невозможен.

**Опции:**

- **3 RG** (если Layout-2): `rg-local-single`, `rg-net-async`, `rg-net-sync`
- **4 RG** (если Layout-1): + `rg-local-mirror`

**Текущая default:** **3 RG**, согласуется с Layout-2.

**Pending decision:** ⏳ Решается автоматически после Q1.

---

## Q3. ZFS sync property — `standard` vs `always` для VM zvols

**Контекст.** ZFS свойство `sync` определяет реакцию на `fsync()` от приложения. См. [02-zfs.md](02-zfs.md) §sync-property.

**Опции:**

- **`sync=standard`** (default): fsync от Postgres / Kafka честно дожидается записи на диск через ZIL; writes без fsync идут асинхронно.
- **`sync=always`**: каждый write принудительно через ZIL, даже если приложение не звало fsync.

**Trade-off:**

- `sync=always` защищает от багов в guest OS / драйверах VM, которые могут «забыть» fsync. Цена: +5-15% latency на write, +нагрузка на NVMe (больше пишется через ZIL).
- `sync=standard` доверяет приложению. Postgres / Kafka — корректные приложения, делают fsync правильно. Для них защиты не нужно.

**Текущая default:** **`sync=standard`**.

**Pending decision:** ⏳ Можно оставить default. Поднять вопрос если будут специфичные VM с легаси-приложениями, где fsync семантика подозрительна.

---

## Q4. LINSTOR Controller HA

**Контекст.** LINSTOR controller — это management plane (база данных конфигурации, API). Если controller упал, существующие тома продолжают работать (DRBD на satellite — independent), но нельзя создавать новые ресурсы / делать failover.

**Опции:**

### A. Single controller на одной ноде (default для старта)

- Controller — обычный systemd-сервис на Node 1
- БД — встроенный H2 (file-based)
- Падение Node 1 = недоступность management plane (workload работает)

### B. HA controller через DRBD-replicated volume + Pacemaker

- Controller БД лежит на DRBD-resource replica=3 (по одной копии на каждую ноду)
- Pacemaker управляет «где сейчас работает linstor-controller»
- При падении ноды — failover на другую за 30-60 секунд

### C. HA controller через PostgreSQL backend

- БД — внешний PostgreSQL (можно использовать тот же в K8s, но это chicken-and-egg)
- Controller stateless, можно поднять на любой ноде

**Текущая default:** **A (single controller на Node 1)**. Для production через 1-2 месяца стоит мигрировать на B.

**Pending decision:** ⏳ Когда мигрировать на HA. Рекомендация — после первого инцидента с падением Node 1 или планово через месяц прод-эксплуатации.

---

## Q5. Storage network topology

**Контекст.** 25 GbE storage-сеть между 3 нодами. Способ соединения определяет отказоустойчивость и стоимость.

**Опции:**

### A. 25 GbE switch (отдельный)

- 3 ноды → 25 GbE switch
- Single point of failure: switch
- Можно добавить второй switch + bonded interfaces (LACP) — удвоение стоимости

### B. Full mesh (direct attach без switch)

- На каждой ноде 2 × 25 GbE NIC
- Node1 ↔ Node2, Node1 ↔ Node3, Node2 ↔ Node3 — прямые DAC-кабели
- Нет SPOF, дешевле (нет switch), но требует 2 NIC на ноду
- Сложнее конфигурация роутинга

**Текущая default:** **A (single switch)** — проще для bootstrap. Если нужна максимальная HA — мигрировать на full mesh или dual-switch.

**Pending decision:** ⏳ Зависит от того, что есть в DC / у провайдера.

---

## Q6. Backup-стратегия

**Контекст.** ZFS снапшоты + LINSTOR/DRBD замечательно защищают от железных отказов, но не от:
- Логических ошибок (DROP TABLE без WHERE)
- Ransomware / компрометации
- Багов в приложениях, портящих данные

Нужна стратегия offsite-backup.

**Опции:**

- **Proxmox Backup Server (PBS)** — отдельный сервер для дедуплицированных backup'ов VM
- **ZFS send/receive** на удалённый pool (другой DC)
- **App-level backup** — Postgres pg_basebackup + WAL archive в S3, etc.

**Текущая default:** ⏳ Не решено. Этот файл backup'ы НЕ покрывает.

**Pending decision:** ⏳ Отдельный проект.

---

## Q7. Когда возвращаться к CEPH

См. [04-ceph.md](04-ceph.md) — там описан backlog. Триггеры пересмотра:

- Кластер вырос до 5+ нод
- Нужен S3-совместимый object storage из K8s
- Нужна geo-репликация storage между DC
- Нагрузка такая, что DRBD-resync становится узким местом

**Текущая default:** Не возвращаться, пока не сработал хотя бы один триггер.

---

## Как обновлять этот файл

После принятия решения по любому Q:

1. Замени `⏳ Pending decision` на `✅ Решено: <ответ> (<дата>)`
2. Добавь `### Reasoning` блок с объяснением почему именно этот вариант
3. Если решение меняет default — обнови референсные файлы (02-zfs.md, 03-linstor-and-drbd.md, 07-step-by-step-bootstrap.md) одним коммитом
4. Не удаляй вопрос — оставь historical record

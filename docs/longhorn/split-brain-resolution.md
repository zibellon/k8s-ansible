# Kubernetes + Longhorn: High Availability и решение Split-Brain

Этот документ описывает архитектуру отказоустойчивости (HA) для кластера Kubernetes на базе VPS с использованием Longhorn для хранения данных (RWO volumes). Основное внимание уделено сценарию потери сетевой связности (Network Partition) и предотвращению ситуации Split-Brain (расщепления данных).

## Контекст и Сетап
- Инфраструктура: Bare-metal VPS серверы (5 шт)
- Storage: Longhorn (Distributed Block Storage)
- Workload: Stateful приложения (например, Postgres) с ReadWriteOnce (RWO) томами
- Требование: 100% сохранность данных (Consistency > Availability). При потере связи с узлом, система должна гарантировать, что данные не будут повреждены "зомби-записью", и автоматически восстановиться

## Ситуация (Сценарий отказа)
Узел `VPS-3`, на котором запущен Pod с базой данных (Postgres), теряет сетевую связность с остальным кластером (выдернули кабель, упал свич провайдера). При этом сам сервер `VPS-3` продолжает работать, и процесс Postgres внутри него "жив".

## Риск (Split-Brain)
- Кластер считает `VPS-3` мертвой и запускает новую копию БД на `VPS-2`
- `VPS-3` не знает, что она мертва, и продолжает писать данные на свой локальный диск (Longhorn volume)
- Возникает две ветки реальности данных. При восстановлении сети возможен конфликт или потеря данных

## Компоненты и их Ответственность
1. Kube-Controller-Manager (на мастере)
   1. Следит за статусом узлов. Если Kubelet не шлет heartbeat, помечает Node как `NotReady`.
   2. Помечает - делает запись в ETCD
   3. Параметр: `--node-monitor-grace-period` (default: 40s)
2. Kube-Controller-Manager (Taint Controller)
   1. Следит за Taints на узлах (`NotReady`)
   2. Если они висят долго, помечает Pods как `Terminating`
   3. Долго - `tolerationSeconds` (в Pod spec, default: 300s)
3. Kube-Scheduler
   1. Пытается назначить `Pending` поды на живые узлы
   2. Не может запустить Pod с RWO томом, пока старый том "прикреплен"
   3. Информация об этом хранится в ETCD
4. Longhorn Manager
   1. Следит за состоянием томов и реплик
   2. Управляет жизненным циклом (Attach/Detach)
   3. Параметр, который отвечает на вопрос: Что делать с PODS, которые используют PVC на NODE которая NotReady ?
      1. `Pod Deletion Policy When Node is Down`
5. Longhorn Engine
   1. Data Plane. Пишет данные. Обнаруживает ошибки записи в реплики
   2. Именно этот компонент отметит - что реплика `Мертвая`
   3. После запуска на живой реплике, увидии что другая реплика не работает и начнется отсчет - staleReplicaTimeout
6. medik8s
   1. Self-Node-Remediation (Operator)
   2. Агент на узле (Fencing). Следит за связью с API. Если связи нет — перезагружает узел
   3. Watchdog timer (hardware/software)


## 5. Настройка параметров
## Для реализации этой схемы необходимо настроить 3 уровня системы.

### Уровень 1: Kubernetes (Быстрая реакция на отказ)

**Где:** В спецификации `Deployment` или `StatefulSet`.
**Что:** Уменьшить время ожидания перед эвакуацией (`tolerations`). По умолчанию K8s ждет 5 минут. ставим 30 секунд.

```yaml
spec:
  template:
    spec:
      tolerations:
        - key: "node.kubernetes.io/not-ready"
          operator: "Exists"
          effect: "NoExecute"
          tolerationSeconds: 30  # <-- Важно
        - key: "node.kubernetes.io/unreachable"
          operator: "Exists"
          effect: "NoExecute"
          tolerationSeconds: 30  # <-- Важно
```

### Уровень 2: Longhorn (Агрессивная зачистка)

**Где:** Longhorn UI или ConfigMap -> Settings -> General.
- Pod Deletion Policy When Node is Down**
  - `Value: delete-both-statefulset-and-deployment-pod`
  - Позволяет Longhorn удалять "зависшие" поды из ETCD, как только они переходят в статус Terminating. Без этого RWO том не освободится.
- Stale Replica Timeout
  - `Value: 2880` (Default) или меньше (например, `60` минут).
  - Определяет, через сколько времени удалять "протухшую" реплику из конфига.
  - Если поставить очень мало (1 мин), то при краткосрочном сбое начнется полная пересинхронизация (full rebuild), что нагружает сеть. Рекомендуется 15-60 минут.

### Уровень 3: Операционная система / Fencing (Гарантия остановки)
## Это "страховочный трос", который обеспечивает 100% консистентность.

**Инструмент:** `Medik8s / Self-Node-Remediation`
**Принцип:** Использование Linux Watchdog (`/dev/watchdog`).

**Как настроить (концептуально):**
1.  Установить Operator в кластер.
2.  Настроить `SelfNodeRemediationConfig`:
    *   `safeTimeToAssumeNodeRebooted`: время, которое кластер ждет перед тем, как считать ноду перезагруженной.
    *   `watchdogFilePath`: `/dev/watchdog` (нужен установленный `watchdog` пакет на хосте или поддержка softdog).

**Логика работы агента:**
```bash
# Псевдокод логики агента на ноде
while true; do
  if check_api_server_connection(); then
    feed_watchdog() # Сброс таймера (еще 10 сек жизни)
  else
    # Связи нет -> Не кормим собаку
    # Через 10 сек ядро вызовет REBOOT
  fi
  sleep 1
done
```

# ------------------
# ------------------
# ------------------

хронология событий, после настроек
1. 00:00
   1. Обрыв сети на VPS-3
   2. Узел изолирован. Kubelet перестает слать heartbeat.
2. 00:10
   1. Fencing (Watchdog)
   2. Агент `Self-Node-Remediation` на VPS-3 замечает потерю связи с API. Перестает сбрасывать таймер Watchdog
   3. Это из проекта - `medik8s`
3. 00:20
   1. Reboot VPS-3
   2. Watchdog таймер истекает
   3. Ядро Linux делает `Hard Reset`
   4. Запись данных на VPS-3 `гарантированно остановлена`
      1. После перезагрузки, сеть недоступна - запускается только static-pods, больше ничего не запускается
      2. После перезагрузки, сеть доступна - kubelet связался с kube-api, и тут уже все зависит от времени возврата в строй
4. 00:40
   1. Node NotReady
   2. Kube-Controller-Manager не получил heartbeat за 40 сек. Ставит статус `NodeCondition: Ready=False` (Unknown). Вешаются Taints.
5. 01:10
   1. Pod Terminating
   2. Истекает `tolerationSeconds` (30 сек) в спецификации пода. Контроллер ставит поду статус `Terminating`.
   3. Ставит статус - записывает состояние POD в ETCD
   4. Вот тут интеерсный момент: Они будут висеть в статусе Terminating - БЕСКОНЕЧНО долго
      1. Или VPS-3 вернется в строй -> запустится kubelet -> подтвердит удаление
      2. Или - ручное вмешательство, удаление force
      3. Или - внешнее вмешательство (medik8s)
6. 01:11
   1. Reschedule & Start
   2. kube-Scheduler - начинает процесс запуска подов на новых NODE
   3. Stateless приложения (Без volume) - зависит от стратегии обновления
   4. Если стратегия `Recreate` - есть проблема
      1. PODS зависят от убийства старого пода. И новые запуститься не могут. Так как Recreate - сначала отключи старое, потом включи новое
      2. А убийство произойти не может - так как, это должен подтвердить kubelet на VPS-3
      3. А kubelet на vps-3 - не может достучаться до kube-api, так как сеть упала
   5. Если `RollingUpdate` — K8s создаст замену сразу, не дожидаясь смерти старого POD
   6. Приложения StateFul - для которых нужен PVC
      1. падают с ошибкой: `Volume already in use ...` (или что-то такое)
      2. PVC + RWO = только один POD может его примонтировать
      3. в ETCD записано: POD на VPS-3 примонтировал этот VOLUME (`VolumeAttachment`)
      4. Этот под - был отправлен в статус Terminating (надо завершить)
      5. И в таком статусе завис... Так как kubelet на vps-3 недоступен и ничего сделать не может
7. 01:11
   1. Longhorn Force Delete
   2. Longhorn Manager видит комбинацию: `Node Down` + `Pod Terminating`. Делает `force delete` пода из ETCD
   3. Это за собой тянет или сам Longhorn делает - удаление из ETCD `VolumeAttachment`
   4. Пока что запись `VolumeAttachment` ЕСТЬ для PVC + PV + RWO -> НИКТО не может подключить этот PV
   5. Longhorn будет принудительно удалять (force delete) ТОЛЬКО те поды, которые используют его тома. Он не будет трогать stateless nginx или backend, у которых нет томов Longhorn. Они так и останутся висеть в Terminating
8. 01:15
   1. Reschedule & Start
   2. kube-Scheduler - начинает процесс запуска подов на новых NODE
   3. Scheduler видит, что PVC + PV свободен, а старого пода нет. Назначает новый под на `VPS-2`.
9.  01:20
   1. Volume Attach
   2. Longhorn на `VPS-2` подключает реплику с живого узла (`VPS-5`). Данные консистентны (на момент 00:20)
   3. запускается longhorn-engine на vps-2
10. 01:21
   1. Stale Replica Cleanup
   2. Новый Engine помечает реплику с `VPS-3` как Error. Запускается таймер `staleReplicaTimeout`.
   3. Предположим что staleReplicaTimeout = 5 минут
11. Вариант-1. 01:21 - 06:20
   1.  vps-3 вернулся в строй
   2.  Там еще есть реплика, но на ней неактуальные данные
   3.  Запускается процесс - `rebuild`, на основе данных из vps-5
12. Вариант-1. 06:25 и далее
    1.  vps-3 вернулся в строй
    2.  Там еще есть реплика, но эта реплика в ETCD отмечена как: Мертвая реплика, надо удалить
    3.  Реплика физически удаляется с Node (vps-3)

# Longhorn + Network Partition: Сценарий восстановления

## Исходные условия
- Инфраструктура: Bare-metal VPS серверы (5 шт)
  - vps-1
  - vps-2
  - vps-3
  - vps-4
  - vps-5
- Storage: Longhorn
- Workload: Stateful приложения (например, Postgres), Deployment, с ReadWriteOnce (RWO) volume

## Ситуация
- на узле `VPS-3` запущен POD с базой данных (Postgres)
- Cron пишет в БД каждую секунду и запущен на `VPS-3`
- Volume - имеет две реплики. Например на `VPS-3` и `VPS-5`
- узел `VPS-3` теряет сеть с остальным кластером (выдернули кабель, упал свич провайдера, и так далее)
- При этом сам сервер `VPS-3` продолжает работать, и процесс Postgres + Cron внутри него "жив" и работает

## Какое требование
- 100% сохранность данных
- При потере связи с узлом, система должна гарантировать, что данные не будут повреждены "зомби-записью", и автоматически восстановиться
- Есть разрешение для downtime

## Хронология событий
- t0
  - Всё работает. Engine на `VPS-3` пишет в обе реплики
- t1
  - Сеть `VPS-3` отключена
- t20 +-
  - medik8s
  - Перстал обновлять watchdog. пошел отсчет в 30 сек до reboot
  - 30 sec - настройка на `Node` (на уровне модуля ядра)
- t1 -> t30
  - Engine на `VPS-3` продолжает писать ТОЛЬКО в локальную реплику (+30 записей)
  - там же рабоатет postgres + cron
- t30
  - Node NotReady
  - Kube-Controller-Manager не получил heartbeat за 40 сек. Ставит статус `NodeCondition: Ready=False` (Unknown). Вешаются Taints
- t50 +-
  - medik8s
  - Перезагрузка `VPS-3`
  - В этот момент - Запись данных на VPS-3 `гарантированно остановлена`
- t90
  - всем PODS - устанавливается статус `terminating`
  - Истекает `tolerationSeconds` (60 сек) в спецификации пода. Контроллер ставит поду статус `Terminating`.
  - Ставит статус - записывает состояние POD в ETCD
  - Вот тут интересный момент: PODs будут висеть в статусе `Terminating` - БЕСКОНЕЧНО долго
    - Или VPS-3 вернется в строй -> запустится kubelet -> подтвердит удаление
    - Или - ручное вмешательство, удаление force
    - Или - внешнее вмешательство (medik8s). Вот это наш вариант
- t91
  - Reschedule & Start
  - kube-Scheduler - начинает процесс запуска подов на новых NODE
  - Stateless приложения (Без volume) - зависит от стратегии обновления
    - Если `Recreate` = проблема
      - PODS зависят от убийства старого пода. И новые запуститься не могут. Так как Recreate - сначала отключи старое, потом включи новое
      - А убийство произойти не может - так как, это должен подтвердить kubelet на VPS-3
      - А kubelet на vps-3 - не может достучаться до kube-api, так как сеть упала
    - Если `RollingUpdate` — K8s создаст замену сразу, не дожидаясь смерти старого POD
  - Приложения StateFul - для которых нужен PVC
    - падают с ошибкой: `Volume already in use ...` (или что-то такое)
    - PVC + RWO = только один POD может его примонтировать
    - в ETCD записано: POD на VPS-3 примонтировал этот VOLUME (`VolumeAttachment`)
    - Этот под - был отправлен в статус Terminating (надо завершить)
    - И в таком статусе завис... Так как kubelet на vps-3 недоступен и ничего сделать не может
- t160 +-
  - TimeAssumedRebooted истёк
  - Добавить taint: `out-of-service`, на `VPS-3`
  - Force-delete postgres из ETCD (на уровне k8s)
  - Force-delete VolumeAttachment из ETCD (на уровне k8s)

## Состояние реплик на t40+
- `VPS-3`: 1030 записей (изолированные данные, реплика offline)
- `VPS-5`: 1000 записей (последняя синхронизация до t1)


## Сценарий A: `VPS-3` вернулась ДО t50 (до перезагрузки)
## По идее - ничего не случилось, просто сеть моргнула
## реплика на `VPS-5` === fail. требует rebuild

## Сценарий B: `VPS-3` вернулась ПОСЛЕ t90 ДО t160 (до перезагрузки)
## kube-scheduler - пытается назначить Terminating-pods на другие `VPS-X`. Падает с ошибкой, так как там есть volume-attach
## kubelet на `VPS-3` - запустился, подключился к kube-api, узнал, что ему удалить и так далее, удалил -> kube-scheduler все смог спокойно запустить. Актуальная реплика = `VPS-3` (там данных больше)

## Сценарий D: `VPS-3` - вернулась ПОСЛЕ `staleReplicaTimeout`. Или еще позже. Или не вернулась вообще
## longhorn-engine на рабоченм узле `VPS-5` (например) - отметил реплиу на `VPS-3` как мертвая в момент своего запуска
## по истечении `staleReplicaTimeout` - эта реплика признается МЕРТВОЙ и подлежит удалению

## Сценарий C: `VPS-3` вернулась ПОСЛЕ t160 (после out-of-service)
## kubelet на `VPS-3` - запустился, подключился к kube-api, узнал что для него ничего нет (нечего запускать). Он ничего и не запускает

## Сценарий C.1: `VPS-3` вернулась ДО запуска postgres

- t_XXX < t160 +-
  - ДО установки taint: `out-of-service`
  - `VPS-3` загрузилась, сети НЕТ
  - Postgres НЕ запускается (kubelet ждёт API)
  - по факту: НИЧЕГО не запускается, кроме `static-pods` (из специальной директории - `/etc/kubernetes/manifests`)
- t170
  - Возврат сети на `VPS-3`
  - Снять taint: `out-of-service`
- t171
  - Kubelet -> API (получил соединение, узнал - что ему надо и не надо запускать)
  - DaemonSets запускаются
- t172
  - Реплика `VPS-3` становится доступна для Longhorn
  - В этой реплике сейчас 1030 (на 30 больше чем на `VPS-5`)
- t180
  - Запуск postgres (scheduler назначает на `VPS-X`)
  - Volume attach, Engine стартует
  - Engine видит обе реплики, обе реплики ЖИВЫЕ И РАБОЧИЕ
  - сравнивает revision / HEAD
  - `VPS-3` (1030) > `VPS-5` (1000) = `VPS-3 эталон`
  - подключает `VPS-3` к postgres, чтобы postgers работал
- t181
  - Rebuild: `VPS-3` -> `VPS-5`

## Результат
- 30 записей с `VPS-3` СОХРАНЕНЫ
- Консистентность: обе реплики = 1030 записей

## Сценарий C.2: postgres запустился ДО возврата `VPS-3`

- t_XXX < t160 +-
  - ДО установки taint: `out-of-service`
  - `VPS-3` загрузилась, сети НЕТ
  - Postgres НЕ запускается (kubelet ждёт API)
  - по факту: НИЧЕГО не запускается, кроме `static-pods` (из специальной директории - `/etc/kubernetes/manifests`)
- t170
  - Postgres запускается на `VPS-X`
  - Volume attach, Engine стартует
  - Engine видит replica ТОЛЬКО `VPS-5` (`VPS-3` offline)
  - `VPS-5` = эталон (единственная доступная)
  - Реплика на `VPS-3` - отмечается как ERR
- t170 -> t190
  - Postgres пишет +20 записей в `VPS-5`
  - Теперь есть две реплики с разными данными (как ветки в git)
    - `VPS-3`: 1030 записей
    - `VPS-5`: 1020 записей
- t190
  - Возврат сети на `VPS-3`
- t191
  - Kubelet -> API
  - DaemonSets запускаются
- t192
  - Реплика `VPS-3` становится "видимой" для Engine
  - Engine обнаруживает diverged реплики
  - Engine доверяет своей рабочей реплике `VPS-5` = эталон
  - `VPS-3` помечена как `ERR`
- t193
  - Rebuild: `VPS-5` -> `VPS-3`

## Результат
- 30 записей с `VPS-3` ПОТЕРЯНЫ
- 20 новых записей с `VPS-5` сохранены
- Консистентность: обе реплики = 1020 записей

---

## Вывод
## Самый главны вопрос: СКОЛЬКО реплик увидит longhorn-engine в момент запуска
## Если увидел 1 реплику = она эталон, вторая = ERR. Как только вторая вернется - она будет пересобрана из `активной`
## Если увидел 2 реплики = Выбирай любую (на самом деле). Вторая, которая `не будет выбрана` - будет пересобрана из `выбранной`
- Или потеря XXX записей от "призрак-проесса"
- Или ничего не потеряем
##
## Ключевой принцип
## Engine доверяет той реплике, с которой он `активно работает`
## Diverged реплики всегда перезаписываются с эталонной

---

## Интересные моменты

### Почему postgres НЕ запускается на `VPS-3` после перезагрузки без сети?
- Kubelet стартует
- пытается связаться с kube-apiserver
- Нет сети
- Kubelet в режиме "ждать" — не может получить список подов из API
- Кэш подов после `reboot` пуст (kubelet не знает какие поды запускать)
- Запускаются ТОЛЬКО static pods (из `/etc/kubernetes/manifests/`)

### Почему Engine не может записать "node-2 = Failed" в ETCD при потере сети?
- `VPS-3` = сеть упала
- Engine на `VPS-3` не может связаться с ETCD
- Информация о Failed реплике остаётся только локально
- При выборе эталона Engine использует **revision реплик** (хранится локально на диске)

### Когда начинается rebuild?
- Rebuild НЕ начинается автоматически от возврата сети
- Rebuild начинается только когда Volume делает `attach` и `Engine стартует`
- Engine при старте подключается к репликам и сравнивает их состояние

---

## Компоненты и их Ответственность
1. Kube-Controller-Manager (на мастере)
   1. Следит за статусом узлов
   2. Если Kubelet с узла не шлет `heartbeat`, то Kube-Controller-Manager помечает Node как `NotReady`
   3. Помечает - делает запись в ETCD
   4. Параметр: `--node-monitor-grace-period` (default: 40s)
   5. Что сделать: Уменьшить до 30s
2. Kube-Controller-Manager (Taint Controller)
   1. Следит за Taints на узлах (`NotReady`)
   2. Если они висят долго, помечает Pods как `Terminating`
   3. Помечает - делает запись в ETCD (что этот POD - надо завершить)
   4. Долго - `tolerationSeconds` (в Pod spec, default: 300s). Как контролировать - написано ниже
3. Kube-Scheduler
   1. Пытается назначить `Pending` поды на живые узлы
   2. Может запустить POD - без volume и без `strategy: Recreate`
   3. Не может запустить POD `strategy: Recreate`. Так как - требуется подтверждение удаления
   4. Не может запустить Pod с RWO volume, пока старый том "прикреплен" (Существует запись `VolumeAttach`)
   5. Информация об этом хранится в ETCD
4. Longhorn Manager
   1. Следит за состоянием томов и реплик
   2. Управляет жизненным циклом (Attach/Detach)
   3. Параметр, который отвечает на вопрос: Что делать с PODS, которые используют PVC на NODE которая NotReady ?
      1. `Pod Deletion Policy When Node is Down`
      2. Нужна настройка: `do-nothing`
5. Longhorn Engine
   1. Data Plane
   2. Пишет данные
   3. Обнаруживает ошибки записи в реплики
   4. Именно этот компонент отметит - что реплика `Мертвая`
   5. После запуска на живой реплике, увидит что другая реплика не работает и начнется отсчет - `staleReplicaTimeout`
6. medik8s
   1. Self-Node-Remediation (Operator)
   2. Агент на узле (Fencing). Следит за связью с API. Если связи нет — перезагружает узел
   3. Watchdog timer (hardware/software)

### 1: Kubernetes (1)
### В спецификации `Deployment` или `StatefulSet`
### Уменьшить время ожидания перед эвакуацией (`tolerations`). По умолчанию K8s ждет 5 минут. Ставим 60 секунд.
### Этот параметр отвечает на вопрос: Через сколько `PODS` перейдут в состояние `Terminating`, после того как их `Node` будет отмечена как `NotReady`

```yaml
spec:
  template:
    spec:
      tolerations:
        - key: "node.kubernetes.io/not-ready"
          operator: "Exists"
          effect: "NoExecute"
          tolerationSeconds: 60  # <-- Важно
        - key: "node.kubernetes.io/unreachable"
          operator: "Exists"
          effect: "NoExecute"
          tolerationSeconds: 60  # <-- Важно
```

### 2: Kubernetes (2)
### Можно уменьшить время, через которое Node отмечается как NotReady
### 1. Kubernetes: kube-controller-manager

```yaml
# /etc/kubernetes/manifests/kube-controller-manager.yaml
spec:
  containers:
  - command:
    - kube-controller-manager
    - --node-monitor-grace-period=20s   # default: 40s
```

### 2: Longhorn
### Pod Deletion Policy When Node is Down = нужно поставить в `do-nothing`
### Pods + VolumeAttach - будут удалены через kubernetes, как только на node появится отметка: `out-of-service`

- Pod Deletion Policy When Node is Down
  - `Value: delete-both-statefulset-and-deployment-pod`
  - Позволяет Longhorn удалять "зависшие" поды из ETCD, как только произойдет два условия
    - Node = `NotReady`
    - Pod = `Terminating`
  - Без этого RWO volume не освободится
  - То есть: пока-что в ETCD есть запись с POD + VOLUME (RWO) + VolumeAttachment -> запустить новый Pod + этот VOLUME = невозможно
- Stale Replica Timeout
  - `Value: 2880` (Default) или меньше (например, `60` минут)
  - Определяет, через сколько времени удалять "протухшую" реплику из конфига (из ETCD)
  - replica становится `мертвая` - когда запускается `longhorn-engine` на живой Node и подключается к живой реплике
  - Он опрашивает остальные реплики (которые указаны в конфиге) - и недоступные помечает как `fail` (ну или как-то так)
  - Вот с этого момента - начинается ОТСЧЕТ
  - Если поставить очень мало (1 мин), то при краткосрочном сбое начнется полная пересинхронизация (full rebuild), что нагружает сеть. Рекомендуется 15-60 минут.


### 3. Watchdog timeout (на каждом worker)

```bash
# /etc/modprobe.d/softdog.conf
options softdog soft_margin=30

# Применить:
sudo modprobe -r softdog && sudo modprobe softdog
```

### 4. NodeHealthCheck CR

```yaml
apiVersion: remediation.medik8s.io/v1alpha1
kind: NodeHealthCheck
metadata:
  name: nhc-worker-default
spec:
  selector:
    matchExpressions:
      - key: node-role.kubernetes.io/worker
        operator: Exists
  unhealthyConditions:
    - type: Ready
      status: "False"
      duration: 15s
    - type: Ready
      status: Unknown
      duration: 15s
  remediationTemplate:
    apiVersion: self-node-remediation.medik8s.io/v1alpha1
    kind: SelfNodeRemediationTemplate
    name: self-node-remediation-automatic-strategy-template
    namespace: medik8s
```

### 5. SelfNodeRemediationConfig

```yaml
apiVersion: self-node-remediation.medik8s.io/v1alpha1
kind: SelfNodeRemediationConfig
metadata:
  name: self-node-remediation-config
  namespace: medik8s
spec:
  watchdogFilePath: "/dev/watchdog"
  apiCheckInterval: "6s"
  apiServerTimeout: "3s"
  maxApiErrorThreshold: 2
  peerDialTimeout: "3s"
  peerRequestTimeout: "5s"
  safeTimeToAssumeNodeRebootedSeconds: 120
  isSoftwareRebootEnabled: true
```

### 6. SelfNodeRemediationTemplate

```yaml
apiVersion: self-node-remediation.medik8s.io/v1alpha1
kind: SelfNodeRemediationTemplate
metadata:
  name: self-node-remediation-automatic-strategy-template
  namespace: medik8s
spec:
  template:
    spec:
      remediationStrategy: OutOfServiceTaint
```

### Итоговые тайминги
- Node NotReady
  - default=40s
  - new=30s
- NHC реакция
  - default=~30s
  - new=20s
- safeTime
  - default=180s
  - new=120s
- ИТОГО
  - default: ~250s
  - new: ~165s

---

## Старая информация

### Риск (Split-Brain)
- Кластер считает `VPS-3` мертвой
- Производятся определенные действия, чтобы запустить POD = postgres на новом узле `VPS-2` (Например)
- оно запускается, и монтируется реплика с узла `VPS-5`
- `VPS-3` не знает, что она мертва, и продолжает писать данные на свой локальный диск (Longhorn volume)
- Возникает две ветки реальности данных
- Узел `VPS-3` возвращается в строй - а там другая реплика с данными
- При восстановлении сети возможен конфликт или потеря данных

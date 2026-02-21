1. https://longhorn.io/docs/1.9.1/deploy/install/
2. https://github.com/longhorn/longhorn-manager/blob/v1.9.x/types/setting.go - где смотреть КАК правильно назвать настройки

# Какие tags на Nodes надо расставить, чтобы storage-class работали корректно
## Указание tags - через longhorn-UI
`lh-major-volume` | `lh-minor-volume` | `lh-manager` | `lh-worker`

## Комбинации tags
- `lh-major-volume` - Worker / Manager (Например для Portainer)
- `lh-major-volume,lh-manager` - Manger (Например - ...)
- `lh-major-volume,lh-worker` - Worker (Например - GitLab, Все основне проекты и ТД)
- `lh-minor-volume,lh-worker` - Worker (Например - VPN)

## В storageClass - можно (и даже нужно) указать TAGS для выбора node | disk
diskSelector: "ssd,fast"
nodeSelector: "storage,fast"

# Интересный момент про запуск
## Есть компоненты, которые запускает пользователь
## Для контроля, на какой Node будут запущены: зайти в `longhorn.yaml` файл и установить `nodeSelector`
longhornManager:
   nodeSelector:
      label-key1: "label-value1"
longhornDriver:
   nodeSelector:
      label-key1: "label-value1"
longhornUI:
   nodeSelector:
      label-key1: "label-value1"

## Есть компоненты, которые запускает сам `Longhorn`
## Для контроля используется параметр в ConfigMap `longhorn-default-setting`:
## `System Managed Components Node Selector` = `system-managed-components-node-selector`
## Оставляем без изменений. На всех Node в cluster, нужен запущенный longhorn
- Instance Manager
- Backing Image Manager
- Share Manager
- Engine Image
- CSI driver

## Запускает много всего
## Многие сущности не указаны явно в `longhorn.yaml` файлах. Они запускаются изнутри контейнеров
## Например
- daemonset.apps/longhorn-csi-plugin
- daemonset.apps/engine-image-ei-51cc7b9c 

# Удаление `volume` - PVC, PV, volume
1. Основная информация
   1. https://kubernetes.io/docs/concepts/storage/persistent-volumes/#reclaiming
   2. https://kubernetes.io/docs/concepts/storage/persistent-volumes/#reclaim-policy
   3. https://longhorn.io/docs/1.9.1/nodes-and-volumes/volumes/delete-volumes/
2. Общие парвила такие
   1. ВСЕ volumes (PersistentVolume) - создаются через PersistentVolumeClaim
   2. В PersistentVolumeClaim - используется StorageClass
   3. В StorageClass - есть настройка: `reclaimPolicy: "..."`
   4. `Retain` - После удаления PVC -> PV остается как был
   5. `Delete` - После удаления PVC -> PV тоже удаляется

# volume - НЕ ПУСТОЙ
Для БД postgres - это критичная проблема. Монтировать нужно в ПУСТУЮ директорию
Для этого нужно указать subPath (https://kubernetes.io/docs/concepts/storage/volumes/#using-subpath)
## Пример
```
volumeMounts:
  - name: pg-data
    mountPath: /var/lib/postgresql/data
    subPath: pg-data
```

## full.yaml -> longhorn-storageclass
Что интересного - внутри этого ConfigMap указан storageClass.yaml
И если его поменяь (напрмиер количество реплик) - он автоматически обновляется
Как и почему - загадка. Так как - нигде явно не указано его использование

## Поменять количество реплик
ConfigMap -> longhorn-storageclass -> numberOfReplicas: "1"

## full.yaml -> longhorn-default-setting
Это дефолтные настройки, которые будут применяться при старте системы
Предполагаю, что при обновлении этого ConfigMap - оно автоматически обновится в Longhorn (как и StorageClass)

# Как обновлять настройки
1. Обновляем что-то в ConfigMap
2. Запускаем новую ConfigMap `kubectl apply -f ...`

# DefaultSettings
1. Replica Zone Level Soft Anti-Affinity
   1. (default) true - МОЖНО две реплики одного VOLUME на NODE-s в ОДНОЙ зоне
   2. false - НЕЛЬЗЯ две реплики одного VOLUME на NODE-s в ОДНОЙ зоне
2. Replica Node Level Soft Anti-Affinity. Этот парарметр называется по другому: `replicaSoftAntiAffinity`
   1. (default) false - НЕЛЬЗЯ две реплики одного VOLUME на одной NODE
   2. true - МОЖНО две реплики одного VOLUME на одной NODE
3. Replica Disk Level Soft Anti-Affinity
   1. (default) true - МОЖНО две реплики одного VOLUME на ОДНОМ disk
   2. false - НЕЛЬЗЯ две реплики одного VOLUME на ОДНОМ disk

# Настройка очистки файловой системы при удалениее `snapshot`
## trim === вернуть файловой системе пустые блоки данных
## Настраивается в `longhorn-default-setting`: `remove-snapshots-during-filesystem-trim: true`

# Интересное на счет ZONE
1. Nodes that don’t belong to any zone will be treated as if they belong to the same zone.
2. Longhorn relies on label `topology.kubernetes.io/zone=<Zone name of the node>` in the Kubernetes node object to identify the zone.

# Интересные моменты по конфигурации StorageClass
1. DataLocality
   1. For “strict-local” the Replica count should be 1, or volume creation will fail with a parameter validation error.
   2. If “strict-local” is not possible for whatever other reason, volume creation will be failed. A “strict-local” replica that becomes displaced from its workload will be marked as “Stopped”.
2. DataLocality = strict-local
   1. Запустили на node = worker-1
   2. остановили контейнер
   3. Запустили на node = worker-2
   4. Получили ошибку
      1. default-scheduler  0/2 nodes are available
      2. 1 node(s) didn't match Pod's node affinity/selector
      3. 1 node(s) had volume node affinity conflict
      4. preemption: 0/2 nodes are available: 2 Preemption is not helpful for scheduling

# Еще некоторые параметры для StorageClass
# Если эти параметры у StorageClass активированы - они OVERRIDE global-settings

# Интересные моменты по конфигурации `longhorn-static`
## Settings: `default-longhorn-static-storage-class: "longhorn-static"`
## Это имя StorageClass, которое Longhorn использует внутренне для связывания (binding) PersistentVolumeClaims (PVCs)
## с уже существующими (статически созданными или восстановленными) PersistentVolumes (PVs), которые представляют тома Longhorn
## ---
## Нет provisioner: Созданный longhorn-static StorageClass не будет иметь секции provisioner: driver.longhorn.io
## Основная задача в том, чтобы служить именем для связывания PV и PVC с уже готовым томом Longhorn

# Про выбор Zone + Node + Disk - где будет расположен volume
## Последовательность выбора: zone -> node -> disk
## Есть два уровня настроек: `GLOBAL` и `storageClass`
## По дефолту, все настройки в storageClass (касательно выбора места для volume) - стоят в `ignored`
## То есть: используются только `GLOBAL` настройки

## Какие надо поставить настроки для `AntiAffinity`
- zone = МОЖНО. true = МОЖНО, false = НЕЛЬЗЯ
- node = НЕЛЬЗЯ. true = МОЖНО, false = НЕЛЬЗЯ
- disk = НЕЛЬЗЯ (мы сюда не дойдем, так как - Node нельзя)

## `GLOBAL`, допустимые значения: true | false
## `GLOBAL`, названия настроек + default
- replica-zone-soft-anti-affinity: true
- replica-soft-anti-affinity: false
  - Пропустили слово NODE, в указании настройки. Replica Node Level Soft Anti-Affinity
- replica-disk-soft-anti-affinity: true
  - Эту настройку можно не трогать, так как NODE=false -> до проверки DISK мы не дойдем

## `storageClass`, допустимые значения: "ignored" | "enabled" | "disabled"
## `storageClass`, названия настроек + default
- parameters.replicaZoneSoftAntiAffinity: "ignored"
- parameters.replicaSoftAntiAffinity: "ignored"
  - Пропустили слово NODE, в указании настройки. Replica Node Level Soft Anti-Affinity
- parameters.replicaDiskSoftAntiAffinity: "ignored"

## Про multipath
Все отключили, все окей работает
В UI Longhorn - показывается ошибка: что multipathd is running
Видимо: Он проверяет просто на запуск multipath, а не на конфиги multipath

## БЕДА ... PVC - создали, удалили, по шапке получили

Есть storageClass: `my-longhorn-one`
Создаем PVC. name: `my-pvc-one` + storageClass: `my-longhorn-one`
Автоматически создался PV (longhorn его создал)

потом удаляем PVC -> PV остался (так как в sotrageClass указано: Retain), в longhorn PV перешел в статус `Released`
Потом снова создаем PVC с таким же именем и в том же namespace
Создается новый PV в Longhorn

Нужно вернуть старые volume, которые находятся в статусе `Released`, а новые удалить

1. Удалить новый PVC
   1. Именно ресурс kubernetes
2. Получить информацию о старом PV в статусе `Released`
   1. `kubectl get pv | grep Released`
3. Отредактировать старый PV и удалите секцию `claimRef`
   1. `kubectl edit pv`
   2. Найти и удалить весь блок `claimRef`
   3. После удаления claimRef PV перейдет в статус `Available`
4. Создать PVC заново
   1. PVC должен автоматически привязаться к старому PV

## Секция claimRef
spec:
  claimRef:           # <- Удалить всю эту секцию
    apiVersion: v1
    kind: PersistentVolumeClaim
    name: my-pvc-one
    namespace: <your-namespace>
    resourceVersion: "..."
    uid: "..."

# Как зайти в volume через HOST (cd ./my-suoer-volume)
## https://github.com/longhorn/longhorn/discussions/7816
## https://hostlab.tech/blog/mount-longhorn-volume-to-host
1. Создать PVC, который будет обслуживать longhorn
2. Зайти в LonghornUI, увидеть новый volume
3. В секции `AttachTo` - будет пусто (Так как PVC только что создали и еще не использовали)
   1. !ВАЖНО! Если этот PVC хотя-бы раз использовался каким-то контейнером -> в секции `AttachTo` будет `НЕ_ПУСТО`
4. Запускаем любой контейнер для инициализации файловой системы внутри `volume`
   1. busybox - отличный вариант
   2. Запустили, подождали, удалили контейнер
5. После того в секции `AttachTo` - будет указан `busybox`, что он последним использовал этот `volume`
6. СМЫСЛ: Пока-что volume никем не использовался - он не проинициализирован
   1. И его нельзя замонтировать в файловую систему, чтобы получить к нему доступ
7. Через LonghornUI - зайти внутрь `volume` и посмотреть, на каких серверах лежат его `replicas`
8. Через LonghornUI - в списке с `volumes` выбрать наш volume и выполнить команду `Attach`
   1. Выбрвть сервер, на котором есть его `replicas` (не уверен что это важно) 
9.  На сервере, куда был сделан `Attach` выполнить команду: `sudo lsblk -f`
   1. В результате будет много всего
   2. Задача: найти строку у которой в колонке `MOUNTPOINTS` - ПУСТО
   3. Эта строка будет иметь нащвание типа: `sdd` | `sdq` | `sbf` - что-то такое
   4. Это и есть нужное блочное устройство
10. Монтируем
   1.  `sudo mkdir ~/my-longhorn-volume`
   2.  `sudo mount /dev/sdb my-longhorn-volume`
11. Зайти внутрь и ЧТО-ТО сделать
    1.  `cd ~/my-longhorn-volume`
12. Открепить `volume` и удалить временную директорию
    1.  `sudo umount /dev/sdb my-longhorn-volume`
    2.  `sudo rm -R my-longhorn-volume`

# Как настроить BACKUPS (S3)
## После создания через UI, посмотреть как настраивать через код (yaml). Мб можно сразу делать через yaml
## System backup: можно сделать только через `default-backup-target`. То есть - если такого таргета нет, то system-backup настроить нельзя
1. Создать `bucket` - куда будут литься бэкапы
   1. Обычно это делается через S3-ui, что-то такое
2. Получить ключи для бакета и S3:
   1. accessKey
   2. secretKey
   3. URL
3. Превратить все эти моменты в base64 через команду ниже
   1. echo -n SOME_VALUE_XXX | base64
4. Создать ресурс `kind: Secret`, прмиер в `./longhorn-s3-backup-secret.yaml`
5. Зайти в LonghornUI -> settings -> backup_targets
   1. Добавить новый target
   2. Название: Любое произвольное
   3. URL: s3://${BUCKET_NAME}@${REGION_NAME}/
   4. secret: Название секрета, созданного выше
   5. После создания - у него должен быть статус `Available`
6. Зайти в LonghornUI -> recurringJob
   1. Настроить JOB, которая будет делать backup по `cron`
   2. Название: Лучше написать что-то осознанное
   3. Task: Snapshot | Backup. В чем разница - написано ниже
   4. Retain: сколько копий оставляем в системе
   5. Concurrency: сколько в параллель выполнять задач
   6. Cron: тут все поняно
   7. groups: volumes -> добавить в группы, на группы натравить recurringJob
7. Зайти в LonghornUI -> volumes
   1. В настрйоках выбрать НОВЫЙ backup-target
   2. Без этого работать не будет
8. То есть система такая
   1. В общих настройках создать backup-target
   2. Создать RecurrentJob + привязать к goups
      1. Выполнении операция по CRON
   3. Все volume, которые нужно бэкапить
      1. прицепить к groups (которые указали выше)
      2. В настройках выбрать НОВЫЙ backup-target

# Как востановить из backup
1. Все настроили, все запустили
   1. Сейчас пустой кластер
   2. Longhorn, подключили к S3
2. Восстановить backup из S3
   1. через UI, backups, выбрать нужный backup, restore-latest-backup
   2. при восстановлении указать
      1. Use Previous Name - true
      2. Number of replicas - что будет в StorageClass
      3. data-engine - что будет в StorageClass
      4. accessMode - что будет в PVC
      5. NodeTag + DistTag - что будет в StorageClass
3. Дождаться восстановления. После - ДАННЫЕ будут уже на серверах, в блочных устройствах
4. Перейти в раздел VOLUMES (longhorn-ui)
   1. Там будет новый  volume
   2. Нажать на кнопку - Create PVC
   3. Название: как было раньше
   4. И остальные параметры
5. После этого - НАДО ОБЯЗАТЕЛЬНО создать: kubectl apply -f '..pvc...'
   1. То есть: Longhorn создает PVC
   2. Потом создаем PVC через kubectl

# Правильное размещение pod + volume
1. если выбрать best-effort (у storageclass)
2. указать правила размещения реплик volume, например только на воркерах
3. а нагрузку (pod) запустить на manager
4. В Longhorn-UI будет гореть предупреждение: не получается выполнить правило best-effort
5. Все будет работать исправно, но warning будет гореть

# Обновление
1. Делаем по инструкции из документации
2. После запуска - оно стартует долго
3. Там есть error
   1. То есть: kubectl get po -n longhorn-system
   2. Там будет несколько error
4. Версия engine - обновилась, volume были замонтированы на стырой версии engine
5. АВТОМАТИЧЕСКИ обновится не сможет
   1. Заходим в UI - advanced
   2. раздел: instance-manager-image
   3. Там будет указано, сколько и каких engine запущено на каких серверах
6. Чтобы обновить версию engine - надо останавливать workload (чтобы сделать detach volume)
7. То есть:
   1. Останавливаем workload
   2. volume делает - detach
   3. снова запускаем workload
   4. volume делает - attach
   5. новая версия engine подключилась

# Что-то очень интересное про snapshot ....

https://longhorn-host:XXXX/v1/volumes/pvc-e68224e4-d034-46eb-af04-ad9502629f1a?action=snapshotPurge

action=snapshotPurge -> Самое важное тут ...
И да, оно не сработало ...

# Конфиги, для контроля вывода Node из строя

## node-drain-policy. Варианты (от самого безопасного к опасному):
1. `block-for-eviction`
   1. Longhorn БЛОКИРУЕТ drain, автоматически эвакуирует ВСЕ реплики на другие ноды, и только после завершения eviction разрешает drain.
2. `block-for-eviction-if-contains-last-replica`
   1. То же самое, но только если на ноде ПОСЛЕДНЯЯ реплика. Если есть здоровые реплики на других нодах — drain разрешён сразу.
3. `block-if-contains-last-replica`
   1. DEFAULT. Просто блокирует drain если это последняя реплика. НЕ делает автоматическую эвакуацию. Ты должен вручную переместить реплику.
4. `allow-if-replica-is-stopped`
   1. Разрешает drain если последняя реплика остановлена. РИСК: если удалишь ноду после drain — потеря данных.
5. `always-allow`
   1. Всегда разрешает drain. МАКСИМАЛЬНЫЙ РИСК: возможны потеря данных И corruption если реплика была активна во время drain.

## node-down-pod-deletion-policy
1. `do-nothing`
   1. DEFAULT. Kubernetes НЕ удаляет поды автоматически. Volume остаётся "заблокированным" на упавшей ноде. Под не пересоздастся на другой ноде, пока старая нода не вернётся или админ вручную не вмешается.
2. `delete-statefulset-pod`
   1. Force delete только для StatefulSet подов
3. `delete-deployment-pod`
   1. Force delete только для Deployment подов
4. `delete-both-statefulset-and-deployment-pod`
   1. Force delete для всех типов подов

## Обновление

## Как удалить replica, черерз kubectl
## Название реплики - можно достать из UI + команда `kubectl -n longhorn-system get nodes.longhorn.io k8s-worker-1 -o yaml`
## Определяем на какой node находится реплика, через UI
## Вызываем команду для получения информации по этой node в системе longhorn
## там будет ID реплики
## удаляем реплику
kubectl -n longhorn-system delete replicas.longhorn.io pvc-b8023696-662b-40a1-8838-76e192880e4b-r-7226b9e7

## Как получить список longhorn-nodes
kubectl -n longhorn-system get nodes.longhorn.io -o wide
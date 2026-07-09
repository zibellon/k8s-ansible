# Кто ты вообще такой

используется HELM - Piraeus-operator https://github.com/piraeusdatastore/piraeus-operator

# StorageClass - parametrs
## `linstor.csi.linbit.com/autoPlace: "N"`
## Сколько replica создавать. Это total replica count — общее количество копий volume across ноды.

LINSTOR auto-place сам выбирает на каких нодах разместить replicas из тех, что matchят storagePool parameter:
- storagePool: "linstor-workers" + autoPlace: "2" → LINSTOR смотрит "у меня есть pool linstor-workers на k8s-worker-1 и k8s-worker-2" → размещает 2 replica по одной на каждом worker'е.
- storagePool: "linstor-managers" + autoPlace: "2" → но у тебя только 1 manager! → fail at PVC bind (нельзя разместить 2 replica на 1 ноде; LINSTOR не дублирует на одной machine)
- storagePool: "linstor-managers linstor-workers" + autoPlace: "2" → LINSTOR имеет 3 ноды (1 manager + 2 workers), выбирает 2 из 3.
Aliases: autoPlace в CSI parameter, mapped на LINSTOR's internal placementCount (CLI: linstor resource-group create --place-count N).

## `linstor.csi.linbit.com/allowRemoteVolumeAccess: "true|false"`
## Можно ли read/write из pod на ноде БЕЗ локальной replica.

Контекст: DRBD setup
Volume имеет N replicas на N конкретных нодах (выбранных auto-place)
Pod может быть schedule'ен в любую ноду K8s (включая ноды БЕЗ replica этого volume).
Что происходит когда pod НА НОДЕ БЕЗ REPLICA?
- allowRemoteVolumeAccess: "true" (permissive — стандартный choice)
  - Pod может работать на любой ноде. Если local replica нет — LINSTOR auto-create diskless replica на ноде где pod (это metadata-only, без storage backing несколько MB).
  - Diskless replica работает как proxy: pod пишет → diskless replica → forward'ит через DRBD network protocol → actual replicas
  - Performance cost: каждое чтение/запись = network round-trip (даже если 25 GbE — это slowdown)
  - Гибкость: pod может migrate на любую ноду, volume следует за ним.
- allowRemoteVolumeAccess: "false" (strict — Longhorn equivalent strict-local)
  - Pod может работать ТОЛЬКО на ноде с local replica. Если scheduler пытается поставить pod на ноду без replica → pod stuck в Pending или fail.
  - Performance: всегда local I/O — нет network overhead.
  - Жёсткое scheduling constraint — должен быть volumeBindingMode: WaitForFirstConsumer чтобы K8s ждал pod scheduling перед binding'ом PV (тогда node-affinity подстраивается).

## еще немного информации про параметры
- Immediate (как я указал в plan templates)
  - PV provisioning happens immediately when PVC создаётся (до того как pod scheduled).
  - LINSTOR auto-place выбирает 2 ноды БЕЗ знания где будет pod.
  - Например auto-place выбрал manager-1 + worker-1, а pod хочется на worker-2 — conflict, pod stuck.
  - Может привести к suboptimal locality.
- WaitForFirstConsumer (recommended для strict-local)
  - PV provisioning откладывается до scheduling первого pod
  - Scheduler сначала смотрит на pod constraints (resources, anti-affinity), выбирает best node — например worker-2
  - ТОЛЬКО ПОТОМ CSI driver создаёт replicas, гарантируя одну replica на той ноде (worker-2) + auto-place выбирает вторую (например manager-1)
  - PV.nodeAffinity aligned with actual pod placement — perfect locality.
  - Для allowRemoteVolumeAccess: false правильный выбор — WaitForFirstConsumer. Без него можно попасть в ситуацию когда replica placed на ноде где pod scheduling impossible (resource constraints).

## Протоколы
- Protocol A — Asynchronous
  - Условие "OK": данные записаны на локальный диск primary AND положены в TCP send buffer (но не подтверждены secondary).
- Protocol B — Memory-synchronous (semi-sync)
  - Условие "OK": данные на локальном диске primary AND secondary принял (TCP ACK) данные в RAM, но ещё не сделал fsync
- Protocol C — Fully synchronous (DRBD default)
  - Условие "OK": данные на локальном диске primary AND на диске secondary (fsync на обоих узлах подтверждён).

## КВОРУМ
Очень хороший вопрос. Это LINSTOR auto-feature — auto-add-quorum-tiebreaker, включена by default.

autoPlace: "2" → создать 2 disk-full replicas (с реальными данными)
2 — чётное число replicas. Это создаёт risk split-brain
При network partition между worker-1 и worker-2 каждый видит "peer недоступен" → оба могут стать Primary → данные расходятся.
Auto-feature автоматически добавляет третью diskless replica — она голосует в quorum, но не хранит данные. Это tiebreaker arbiter.

### Можно отключить ?

Да, через linstorCluster.properties:
properties:
  - { name: DrbdOptions/auto-add-quorum-tiebreaker, value: "no" }

## Первое содание
## Когда LINSTOR создаёт новый DRBD resource с replica=2
- На каждой node создаётся sparse-file 1 GiB (full volume size)
- Files инициализируются нулями (на уровне FS — sparse, на уровне DRBD bitmap — потенциально "грязные" регионы)
- DRBD запускает initial full sync — копирует весь volume bit-by-bit между replicas.

## Как узнать текущие настройки для DRDB на конкретной Node
kubectl -n linstor exec -t linstor-satellite.k8s-worker-1-hg4p6 -- drbdsetup status
kubectl -n linstor exec -t linstor-satellite.k8s-worker-1-hg4p6 -- drbdsetup show
kubectl -n linstor exec -t linstor-satellite.k8s-worker-1-hg4p6 -- drbdsetup show all
kubectl -n linstor exec -t linstor-satellite.k8s-worker-1-hg4p6 -- drbdsetup show --show-defaults

## Описание параметров
Представь sync как наполнение бутылки из крана:

- c-fill-target = диаметр горлышка. Узкое (50 KiB) — медленный flow. Широкое (10 MiB) — быстрый
- c-min-rate = минимальная струя, даже если кран еле открыт
- c-max-rate = максимальная струя, потолок даже когда полностью открыт
- c-plan-ahead = как часто rotателю корректирует кран (каждые 2 сек)

## Список команд, чтобы узнать какие CLI параметры и команды принимает linstor и его друзья
- `kubectl -n linstor exec deploy/linstor-controller -- linstor controller --help`
- `kubectl -n linstor exec deploy/linstor-controller -- linstor controller drbd-options --help`
- `kubectl -n linstor exec deploy/linstor-controller -- linstor resource-definition drbd-options --help`
- `kubectl -n linstor exec deploy/linstor-controller -- linstor resource-group drbd-options --help`

## Полезные команды
- Получить список Node в Linstor
  - `kubectl -n linstor exec deploy/linstor-controller -- linstor node list`
- Получить список Storage-Pools
  - `kubectl -n linstor exec deploy/linstor-controller -- linstor storage-pool list`
- Получить список ресурсов в Linstor
  - `kubectl -n linstor exec deploy/linstor-controller -- linstor resource list`
- Получить список volumes в Linstor
  - `kubectl -n linstor exec deploy/linstor-controller -- linstor resource list-volumes`
  - `kubectl -n linstor exec deploy/linstor-controller -- linstor volume list`
- получить список ХЗ ЧЕГО, но надо
  - `kubectl -n linstor exec deploy/linstor-controller -- linstor resource-group list`
- Получить список настроек текущих
  - `kubectl -n linstor exec deploy/linstor-controller -- linstor controller list-properties`
- получить список команд для CLI, при resource-group SPAWN | create
  - `kubectl -n linstor exec deploy/linstor-controller -- linstor resource-group spawn --help`
  - `kubectl -n linstor exec deploy/linstor-controller -- linstor resource-group create --help`
  - `kubectl -n linstor exec deploy/linstor-controller -- linstor resource create --help`
- Удалить PV
  - `kubectl -n linstor exec deploy/linstor-controller -- linstor resource-definition delete <имя_pv>`

## ОТсоединение и присоединение PVC
`kubectl exec -n linstor linstor-satellite.k8s-worker-1-dkxjh -c linstor-satellite -- drbdadm disconnect pvc-f4c73cfc-793c-4bbe-9766-55d28283df27`

`kubectl exec -n linstor linstor-satellite.k8s-worker-1-dkxjh -c linstor-satellite -- drbdadm connect pvc-f4c73cfc-793c-4bbe-9766-55d28283df27`

`kubectl exec -n linstor linstor-satellite.k8s-worker-1-dkxjh -c linstor-satellite -- timeout 15 drbdsetup events2 pvc-f4c73cfc-793c-4bbe-9766-55d28283df27`

## CheckSum данных, перед отправкой и на стороне приемника
- `data-integrity-alg`

ubuntu@k8s-manager-1:/opt/helm-charts/traefik/pre$ cat /proc/crypto | grep -E "^name|^driver" | grep -E "crc32c|sha1|sha256|md5"
name         : sha256
driver       : sha256-ni
name         : sha256
driver       : sha256-avx2
name         : sha256
driver       : sha256-avx
name         : sha256
driver       : sha256-ssse3
name         : sha1
driver       : sha1-ni
name         : sha1
driver       : sha1-avx2
name         : sha1
driver       : sha1-avx
name         : sha1
driver       : sha1-ssse3
name         : crc32c
driver       : crc32c-intel
driver       : drbg_nopr_hmac_sha256
driver       : drbg_nopr_sha256
driver       : drbg_pr_hmac_sha256
driver       : drbg_pr_sha256
name         : crc32c
driver       : crc32c-generic
name         : sha256
driver       : sha256-generic
name         : sha1
driver       : sha1-generic
name         : md5
driver       : md5-generic

5. Complementary: verify-alg (scrubbing)
data-integrity-alg ловит corruption at write time. Но что если бит флипнулся в покое на диске через 6 месяцев? Для этого — periodic scrubbing.

LINSTOR property: DrbdOptions/Net/verify-alg (того же кроптоформата) — задаёт алгоритм для verify-операции. Запускается вручную или по cron

# Сравнить все блоки primary ↔ secondary по hash:
drbdadm verify <resource-name>

# Или в Kubernetes контексте — через linstor CLI:
kubectl -n linstor exec deploy/linstor-controller -- \
  linstor resource-definition verify <rd-name>
При обнаружении расхождения DRBD логирует в dmesg номера блоков, и можно запустить drbdadm disconnect/connect чтобы re-sync проблемные блоки.

Рекомендация: ставь оба — data-integrity-alg crc32c + verify-alg crc32c, и periodic verify раз в неделю/месяц через cron внутри кластера.

# Пока-что тут, но не уверен

## Все работает стабильно и исправно. 1 control-plane + 5 worker
## Потом случается сбой = 4 worker выходят из строя. потом 2 из них возвращаются (минут через 10)
## потом падает control-plane и последгний worker
## Потом пдают все сервера вообще
## спустя минут 40 - этот сбой заканчивается, и все сервера восстаналиваются и работают в штатном режиме

## НО = некоторые поды не запустились. Pod, у которых есть PVC (replica-2, Protocol-C)

## postgres зависает в состояни - ContainerCreating (например)

## Заходим в его decribe и видим в events

  Warning  FailedMount  6s (x7 over 12m)  kubelet            MountVolume.WaitForAttach failed for volume "pvc-59c8a195-e1f1-40e8-8116-c2604cd6c83e" : volume pvc-59c8a195-e1f1-40e8-8116-c2604cd6c83e has GET error for volume attachment csi-ca090d3c530aa38deeb179ab275c7f928f898251192e88e0f12a7e0ab98b5082: volumeattachments.storage.k8s.io "csi-ca090d3c530aa38deeb179ab275c7f928f898251192e88e0f12a7e0ab98b5082" is forbidden: User "system:node:k8s-worker-2" cannot get resource "volumeattachments" in API group "storage.k8s.io" at the cluster scope: no relationship found between node 'k8s-worker-2' and this object

## Идем в kube-controller-manager и смотрим логи

kubectl -n kube-system logs kube-controller-manager-k8s-manager-1 --tail=400 \
  | grep -iE "attach|pvc-59c8a195|vault-data|desiredState|csinode|csidriver|informer|sync|leaderelection|fail|error" \
  | tail -80

## Если видим такое добро +-, это завис КЭШ на api-server и его надо сбросить

E0708 23:10:44.137700       1 operation_generator.go:176] VerifyVolumesAreAttached.GenerateVolumesAreAttachedFunc: nil spec for volume kubernetes.io/csi/linstor.csi.linbit.com^pvc-59c8a195-e1f1-40e8-8116-c2604cd6c83e

E0708 23:02:04.746957       1 stateful_set.go:509] "Error syncing StatefulSet, requeuing" err="read version: 7904338 is not as new as written version: 7904340 for group resource statefulsets.apps" logger="UnhandledError" key="vault-warden/vault-warden-pg"

## перезапуск kube-api + kube-controller-manager

sudo mv /etc/kubernetes/manifests/kube-apiserver.yaml /tmp/kube-apiserver.yaml
sleep 25
sudo mv /tmp/kube-apiserver.yaml /etc/kubernetes/manifests/

sudo mv /etc/kubernetes/manifests/kube-controller-manager.yaml /tmp/kube-controller-manager.yaml
sleep 15
sudo mv /tmp/kube-controller-manager.yaml /etc/kubernetes/manifests/
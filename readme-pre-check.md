# предварительная проверка сервероа

# ------
# Сети (Облака, VPC, address-pool и так далее)
# ------
## Лимиты по IP адресам на один сервер (одна Node)
## Правило: РАСШИРИТЬ ПОСЛЕ ИНИЦИАЛИЗАЦИИ кластера - НЕЛЬЗЯ
- kubelet
  - 1 нода = 110 подов (default) То есть: 110 ip адресов
  - Можно переопределить
  - Переменная: kubelet_max_pods (`k8s-base.yaml`) 
- cilium
  - ipam.operator.clusterPoolIPv4MaskSize = 24 (default)
  - Можно переопределить
  - переменная: cilium_helm_values_ipam_operator_pod_cidr_mask_size (`cilium.yaml`)

## Правило_2: Cloud-ip, pod-cidr и service-cidr - НИКОГДА НЕ ДОЛЖНЫ пересекаться

## Примерный расчет
- node = `200`
- kubelet.maxPods = `200`
- Максимум контейнеров в кластере = `200 * 200 = 40_000`
- cilium.clusterPoolIPv4MaskSize = `/23` (`512` адресов на 1 node)
- pod_cidr = `512` (cilium) * `200` (node) ~ `100_000` = `x.x.x.x/15` (`131_070` адресов, 10.0.0.1 - 10.1.255.254)
- service_cidr = pod_cidr / 8 = `x.x.x.x/18` (`16_384` адресов, 10.0.0.1 - 10.0.63.254)
- cilium.MAX_NODES = 131_070 / 512 = `256` (1 node === 1 BLOCK)

## Пример
VPC: 10.0.0.0/22 (10.0.0.1 - 10.0.3.254)
subnet (1-4): 10.0.0.0 - 10.0.0.254 (и таких 4 штуки, по /24 сети)
pod_cidr: 10.2.0.0/15, 10.2.0.1 - 10.3.255.254
service_cidr: 10.4.0.0/18, 10.4.0.1 - 10.4.63.254

# ------
# флаги активации
# ------
## Есть флаги активации для: seaweedFS, gitlab, gitlab-runner и argocd
## Это 4 компонента взаимодействут друг с другом по внутренней сети и им нужны NetworkPolicy для этого
## Если устанавливается только argocd (например) - то gitlab, gitlab-runner и seaweedFS = надо отключить
## Или при установке будет ошибка: Так как нет Namespace для установки Cross-namespace-NetworkPolicy
## флаги:
- seaweedfs_enabled
- gitlab_enabled
- gitlab_runner_enabled
- argocd_enabled

# ------
## общая информация о системе
# ------
## Команда: `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/node-info.yaml`
- Что проверяется
  - ядро
  - hostname
  - network-interface
  - и так далее

## Узнать, какой ip адрес принадлежит основному интерфейсу (ens_xxx | eth_xxx)
## Эта информация необходима, чтобы запустить `kube-api-server`
1. В данном примере случае: `10.129.0.27`

```
1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN group default qlen 1000
    link/loopback 00:00:00:00:00:00 brd 00:00:00:00:00:00
    inet 127.0.0.1/8 scope host lo
       valid_lft forever preferred_lft forever
    inet6 ::1/128 scope host noprefixroute 
       valid_lft forever preferred_lft forever
2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc mq state UP group default qlen 1000
    link/ether d0:0d:bd:92:06:81 brd ff:ff:ff:ff:ff:ff
    altname enp7s0
    inet 10.129.0.27/24 metric 100 brd 10.129.0.255 scope global dynamic eth0
       valid_lft 4294967229sec preferred_lft 4294967229sec
    inet6 fe80::d20d:bdff:fe92:681/64 scope link 
       valid_lft forever preferred_lft forever
```

## занести внутренний `ip` в `hosts-vars-override/`
## его надо будет разрешить в `cilium-host-firewall`, чтобы можно было делать `kubeadm join ...`
## есть два варианта
1. сначала ВСЕ join -> потом install cilium
   1. При таком сценарии - проблем нет
   2. cluster-init
   3. worker/manager join
   4. install cilium + cilium-host-firewall
   5. Все node уже внутри кластера и Cilium про них знает
2. cluster-init -> install cilium -> потом join
   1. install cilium + cilium-host-firewall
   2. Входящий трафик разрешается только от известных источников (внутри кластера, cilium.entities)
   3. Пока Node не добавлена в кластер = cilium видит ее как world (внешний мир)
   4. Трафик запрещен на уровне cilium-host-firewall
   5. при выполнении команды JOIN - timeout. Так как Node не может подключиться к кластеру
   6. Как решать
   7. Добавить новый сервер в hosts.yaml
   8. Обновить cilium-host-firewall
      1. Вызвать `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/cilium-install.yaml --tags post`
   9.  Это автоматически добавит в `cilium-host-firewall` новые ip адреса и обновит политику на сервере
   10. После этого делать: `... join ...`

# ------
## Скорость диска
# ------
`ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/disk-io-test.yaml`
  - на READ
  - на WRITE

# ------
## Скорость сети между серверами по внутренней сети
# ------
## `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/network-bandwidth-test.yaml --limit k8s-worker-1,k8s-worker-2`
##
- Покажет информацию: какой реально пропускной канал (скорость между серверами)
- `--limit k8s-worker-1,k8s-worker-2`
  - можно указать только ДВЕ node
  - они обязательно должны быть в inventory (`hosts-vars/ -i hosts-vars-override/`)

# ------
# конфигурация параметров для LINSTOR
# ------
1. У LINSTOR есть параметры по скорости репликации (Представь sync как наполнение бутылки из крана)
   1. DrbdOptions/PeerDevice/c-min-rate = минимальная струя, даже если кран еле открыт
   2. DrbdOptions/PeerDevice/c-max-rate = максимальная струя, потолок даже когда полностью открыт
   3. DrbdOptions/PeerDevice/c-fill-target = диаметр горлышка. Узкое (50 KiB) — медленный flow. Широкое (10 MiB) — быстрый
   4. DrbdOptions/PeerDevice/c-plan-ahead = как часто корректирует кран (каждые 2 сек)
2. Где эти параметры можно настроить
   1. Global - в момент запуска и настройки LINSTOR
   2. Per StorageClass - более приоритетно, чем глобальные настройки
3. После получения информации про скорость сети и скорость диска - эти параметры надо конфигурировать
4. Правила конфигурации
   1. Эти параметры применяются к КАЖДОМУ PVC
   2. если в системе будет 100PVC, на 10 ГБ (суммарно = 1ТБ)
   3. и всем резко будет нужно сделать ресинк-реплика
   4. то каждый PVC, попробует занять канал, указанный в настройках
   5. 100 PVC на 100 mb/sec = 10_000 mb/sec (10 gbit/sec)

## Как считать

### Немного математики
- clamp(value, min, max) — функция «зажимает» число в диапазоне [min, max]
  - if value < min:  return min     # ниже floor → подняли до floor
  - if value > max:  return max     # выше ceiling → опустили до ceiling
  - else:            return value   # в диапазоне → как есть

### Формула
- bottleneck_MBps = min(network_bandwidth_MBps, disk_write_speed_MBps, disk_read_speed_MBps)
- c_max_rate_KiBps = bottleneck_MBps × app_reserve_ratio × 1024
- c_min_rate_KiBps = c_max_rate_KiBps × min_rate_ratio
- c_fill_target_sectors = c_max_rate_KiBps × target_window_ms / 500
- c_plan_ahead_decisec = clamp(network_rtt_ms × rtt_multiplier / 100, min=5, max=30)
- initial_full_sync_minutes = pvc_volume_total_GB × 1024 / (c_max_rate_KiBps / 1024) / 60

### Доп параметры
- app_reserve_ratio = 0.5 — доля полосы, оставляемая для app I/O (sync не должен забить канал целиком). Диапазон: 0.3 (app-priority) … 0.7 (sync-priority).
- min_rate_ratio = 0.1 — отношение c-min-rate / c-max-rate. Гарантирует floor sync rate даже под полной app нагрузкой.
- target_window_ms = 200 — целевой буфер controller'а (сколько ms работы держать «в полёте»). DRBD-рекомендация.
- rtt_multiplier = 5 — множитель RTT для plan-ahead. DRBD-рекомендация: 5–10× RTT.

### Дано
network_bandwidth_MBps   = 125        # 1 Gbit/s = 125 MB/s
network_rtt_ms           = 1          # < 1 ms LAN
disk_write_speed_MBps    = 150
disk_read_speed_MBps     = 150
pvc_count                = 20
pvc_volume_total_GB      = 500

app_reserve_ratio        = 0.5
min_rate_ratio           = 0.1
target_window_ms         = 200
rtt_multiplier           = 5

### Расчет

bottleneck_MBps           = min(125, 150, 150)             = 125
c_max_rate_KiBps          = 125 × 0.5 × 1024               = 64000
c_min_rate_KiBps          = 64000 × 0.1                    = 6400
c_fill_target_sectors     = 64000 × 200 / 500              = 25600
c_plan_ahead_decisec      = clamp(1 × 5 / 100, 5, 30)      = clamp(0.05, 5, 30) = 5
initial_full_sync_minutes = 500 × 1024 / (64000/1024) / 60 = 512000 / 62.5 / 60 ≈ 137 минут (~2.3 часа)

# ------
# Подключение через bastion (SSH ProxyJump)
# ------
## Когда применимо
1. У основных node НЕТ публичных IP (например, облачная приватная VPC)
2. Доступен только bastion-сервер с белым IP
3. С bastion видны все node по приватной сети
4. SSH-ключ оператора уже есть И на bastion, И на всех node

## Как настроить
В `hosts-vars-override/hosts.yaml` под `all.vars` добавить bastion-параметры. На группах `managers` / `workers` переопределить `ansible_ssh_common_args` с `ProxyJump`. Поле `ansible_host` каждой node должно быть приватным IP (обычно совпадает с `internal_ip`).

## Multi-cluster
Каждый `hosts-vars-override-<cluster>/` живёт независимо: один override-каталог может содержать bastion-блок, другой — нет. Никаких глобальных правок в репозитории.

Пример:
```yaml
all:
  vars:
    bastion_host: "<public-ip-or-dns>"
    bastion_user: ubuntu
  children:
    managers:
      vars:
        ansible_ssh_common_args: "-o StrictHostKeyChecking=no -o ProxyJump={{ bastion_user }}@{{ bastion_host }}"
      hosts:
        k8s-manager-1:
          ansible_host: 10.0.0.10
          ansible_user: ubuntu
          internal_ip: "10.0.0.10"
          api_server_advertise_address: "10.0.0.10"
          is_master: true
    workers:
      vars:
        ansible_ssh_common_args: "-o StrictHostKeyChecking=no -o ProxyJump={{ bastion_user }}@{{ bastion_host }}"
      hosts:
        k8s-worker-1:
          ansible_host: 10.0.0.20
          ansible_user: ubuntu
          internal_ip: "10.0.0.20"
```

# ------
# S3-api
# ------
## В системе есть компоненты, которым для работы требуется S3-api: gitlab, gitlab-runner, loki и еще некоторые
## Чтобы подключиться к S3-api используется ресурс k8s.secret, Который создается через интеграцию с VAULT + ESO
## В момент установки компонента - будет создаем ESO, который достанет секреты из VAULT
## То есть: к моменту установки компонента, данные в VAULT уже должны быть
##
## Есть два варианта подключения к S3-API
## 1. Через встроенную систему Seaweedfs
##    изучить внимательно файл: `hosts-vars/seaweedfs-sync.yaml`
##    добавить все необходимые: identity, buckets + policy и указать пути в VAULT (куда положить созданные credentials)
##    в момент установки SeaweedFS - все сущности буду созданы автоматически и секреты будут записаны в VAULT
## 2. Через внешнее S3-api
##    Создать все необходимые credentials + buckets + policy на удаленном S3
##    Положить в vault (в ручном режиме), по правильным путям и с правильным неймингом

# ------
# SeaweedFS. `collections` и `bucket-size`
# ------
## `bucket` - абстракция. его физически на диске не существует
## `bucket` - можно создать сколько угодно. Это просто указатели путей и не более
## bucket - хранит свои данные в `collection`
##   ПРАВИЛО: `collection` === `bucket-name`
##   То есть: 1 `bucket` === 1 `collection`
## ---
## `collection` - физический `.dat` файл на volume-server (если упрощенно)
## Есть правило: 1 `collection` === 1 `.dat` файл (минимум)
##   то есть: если создать 100 `bucket` = для них 100 `collection` = надо 100 `.dat` файлов
## количество `.dat` файлов = ОГРАНИЧЕНО. Это ограничение - снять нельзя
## в настройках `seaweedfs_helm_values` есть два поля
##   `volume.dataDirs[*].maxVolumes` = максимальное количество `.dat` файлов (Default=0)
##   `master.volumeSizeLimitMB` = максимальный размер одного `.dat` файла
##   maximum numbers of volumes If set to zero, the limit will be auto configured as free disk space divided by volume size. (default "8")
## ---
## По дефолту для SeaweedFS = 4 volumes занято под системные нужды
## ---
## Сценарий_1: `volume.dataDirs[*].maxVolumes` = 1000
##   Ограничение поставили в 1000 .dat файлов. Это достаточно много
##   размер одного файла = 30 гб (по дефолту)
##   то есть: суммарно мы готовимся к объему в 30 TB (1000 * 30)
##   НО: у нас на серверах всего 100 гб. И оно будет работать
##   Потому-что: при создании и при записи данных SeaweedFS не выделяет сразу ВСЕ место. OverProvisioning
##   когда дойдем до лимита = надо будет расширять количество `maxVolumes`
## Сценарий_2: `volume.dataDirs[*].maxVolumes` = 0 (не меняли)
##   maxVolumes = floor(free_disk_space / volumeSizeLimitMB)
##   free_disk_space = `df -h /data1` (не PVC size напрямую). 4 GiB PVC после ext4 overhead + minFreeSpacePercent=1 reserves = ~3850 MB
##   то есть: если PVC=4gb, volumeSizeLimitMB=1000 mb, то maxVolumes = ~3
##   количество `collections` = ~floor(Размер одного PVC для volume-server / Размер максимальный одного `.dat` файла)
##   maxVolumes = задачется для каждого volume-node
## ---
## Если по топологии SeaweedFS у него закончатся collection (.dat файлы) = запись прекратится
## ---

# ------
# SeaweedFS. Erause-coding (`EC`) и `collections`
# ------
## Он работает НЕ НА ЛЕТУ, а через ручной запуск CLI команды
## ВАЖНО! При запуске - будет работать по ВСЕМ volumes, которые попали в фильтр. volume === collection (если упрощенно)
## То есть: этот алгоритм ничего не знает про buckets и S3
## то есть
##    Если у вас 100 логических бакетов (со своими политиками и так далее) и они все пишут в ОДНУ collection
##    При запуске `EC` - вы не сможете ему сказать: фозьми только бакеты 23 25 и 45. Так как - все данные лежат в одном `.dat` файле
##    Это одна из причин - для бакетов, которые в дальнейшем планируется использовать как `EC` = делать свои коллекции (`volume` === `.dat` файлы)

# ------
# Важно про `namespace`
# ------
## Сменить namespace МОЖНО для любых компонентов
## Сменить namespace НЕЛЬЗЯ для некоторых компонентов (Так указано в официальной документации)
- longhorn-system
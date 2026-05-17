# предварительная проверка сервероа

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
# Важно про `namespace`
# ------
## Сменить namespace МОЖНО для любых компонентов
## Сменить namespace НЕЛЬЗЯ для некоторых компонентов (Так указано в официальной документации)
- longhorn-system
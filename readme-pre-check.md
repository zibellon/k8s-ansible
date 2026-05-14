# предварительная проверка сервероа

## ---
## общая информация о системе
## ---
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

## ---
## Скорость диска
## ---
`ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/disk-io-test.yaml`
  - на READ
  - на WRITE

## ---
## Скорость сети между серверами по внутренней сети
## ---
## `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/network-bandwidth-test.yaml --limit k8s-worker-1,k8s-worker-2`
##
- Покажет информацию: какой реально пропускной канал (скорость между серверами)
- `--limit k8s-worker-1,k8s-worker-2`
  - можно указать только ДВЕ node
  - они обязательно должны быть в inventory (`hosts-vars/ -i hosts-vars-override/`)
 
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
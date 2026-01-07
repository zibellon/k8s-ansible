# Kubeadm Configuration

## InitConfiguration

```yaml
apiVersion: kubeadm.k8s.io/v1beta4
kind: InitConfiguration
localAPIEndpoint:
  advertiseAddress: "1.2.3.4"
  bindPort: 6443
nodeRegistration:
  criSocket: "unix:///var/run/containerd/containerd.sock"
```

## localAPIEndpoint - Настройки локального API-сервера на текущей control-plane ноде.

1. `advertiseAddress` - IP-адрес, на котором kube-apiserver слушает и который анонсируется другим компонентам. Используется для etcd peer-to-peer, kubelet → apiserver. Записывается в сертификат apiserver.crt
2. `bindPort` - Порт kube-apiserver (стандарт: 6443)

## Где используется
- При `kubeadm init` — обязательно
- При `kubeadm join --control-plane` — автоматически (IP default gateway интерфейса) или через `--apiserver-advertise-address`

## Пример: Нода с IP `1.2.3.4` — apiserver слушает на `1.2.3.4:6443`.

Вот тут есть важный момент про gateway
- Все сервера, где проводились тесты имели ОДИН интерфейс (сетевой интерфейс) как gateway
- Но есть вероятность, что будет два или более интерфейсов (сетевых интерфейсов) как gateway
- Есть команда: `ip route get 1`, она показывает какие IP адреса у сетевого интерфейса
  - на BareMetal - там будет белый IP, по которому подключаемся к NODE
  - на AWS | Yandex | otherCloud - там будет ВНУТРЕННИЙ ip из VPC
- еще одна команда для просмотра ВСЕХ сетевых интерфейсов: `ip addr show`

## nodeRegistration

1. `criSocket` - Unix-сокет container runtime. Для containerd: `unix:///var/run/containerd/containerd.sock`

## ClusterConfiguration

```yaml
apiVersion: kubeadm.k8s.io/v1beta4
kind: ClusterConfiguration
kubernetesVersion: "v1.34.0"
controlPlaneEndpoint: "1.2.3.4:6443"
apiServer:
  extraArgs:
    - name: service-node-port-range
      value: 1-50000
  certSANs: ["1.2.3.4", "localhost", "127.0.0.1"]
networking:
  serviceSubnet: "10.128.0.0/12"
  podSubnet: "10.64.0.0/10"
  dnsDomain: "cluster.local"
controllerManager:
  extraArgs:
    - name: allocate-node-cidrs
      value: "false"
```

## kubernetesVersion - Версия Kubernetes для установки. Формат: `v1.34.0`.

## controlPlaneEndpoint - Единая точка входа к API-серверу для всего кластера
## Сценарий
1. Single CP - IP первой control-plane ноды (напр. `1.2.3.4:6443`)
2. HA (3 CP) с внешним LB | IP/DNS балансировщика (напр. `lb.example.com:6443`)
3. HA (3 CP) с локальным HAProxy | `127.0.0.1:16443` — HAProxy static pod на каждой ноде

**Записывается в:**
- `/etc/kubernetes/admin.conf`
- `/etc/kubernetes/kubelet.conf`
- kubeconfig для всех компонентов

## apiServer
### extraArgs - Дополнительные аргументы для kube-apiserver.
1. `service-node-port-range` - Диапазон портов для NodePort сервисов
   1. `1-50000` (стандарт: 30000-32767)

### certSANs - Subject Alternative Names для TLS-сертификата apiserver.crt.

**Добавляются к автоматическим:**
- `kubernetes`, `kubernetes.default`, `kubernetes.default.svc`, `kubernetes.default.svc.cluster.local`
- Первый IP из `serviceSubnet`
- IP из `advertiseAddress`
- IP/DNS из `controlPlaneEndpoint`

**Когда добавлять вручную:**
- Публичные IP нод (если доступ к API извне)
- DNS-имена для внешнего доступа
- IP других control-plane нод (для HA)

## Проверка сертификата (какие SANS есть сейчас)
```bash
openssl x509 -in /etc/kubernetes/pki/apiserver.crt -text -noout | grep -A1 "Subject Alternative Name"
```

## Пример вывода
DNS:k8s-manager-1
DNS:kubernetes
DNS:kubernetes.default
DNS:kubernetes.default.svc
DNS:kubernetes.default.svc.cluster.local
IP Address:10.128.0.1
IP Address:1.2.3.4

### networking
1. `serviceSubnet` - CIDR для ClusterIP сервисов
   1. `10.128.0.0/12` (~1M адресов) |
2. `podSubnet` - CIDR для Pod IP
   1. `10.64.0.0/10` (~4M адресов)
3. `dnsDomain` - DNS-домен кластера
   1. `cluster.local`

**Важно для Cilium:** Подсети не должны пересекаться между собой и с внешней сетью

## controllerManager

## extraArgs

1. `allocate-node-cidrs` - Выделение CIDR для нод
   1. `false` — отключено, Cilium управляет IPAM
   2. Для Cilium + kube-proxy-replacement = Всегда `false`, Cilium использует собственный IPAM.

## KubeletConfiguration

```yaml
apiVersion: kubelet.config.k8s.io/v1beta1
kind: KubeletConfiguration
cgroupDriver: systemd
containerLogMaxSize: 100Mi
containerLogMaxFiles: 5
```

1. `cgroupDriver` - Драйвер cgroups
   1. `systemd` — для containerd + systemd
2. `containerLogMaxSize` - Макс. размер лог-файла контейнера
   1. `100Mi`
3. `containerLogMaxFiles` - Макс. количество ротируемых логов
   1. `5` (итого до 500Mi на контейнер)

---

## KubeProxyConfiguration

```yaml
apiVersion: kubeproxy.config.k8s.io/v1alpha1
kind: KubeProxyConfiguration
mode: ipvs
```

1. `mode` - Режим работы kube-proxy: `iptables`, `ipvs`, `nftables`

## Для Cilium + kube-proxy-replacement
- При init указывается `ipvs` (или любой)
- kubeadm init запускается с `--skip-phases=addon/kube-proxy`
- kube-proxy не устанавливается — Cilium берёт на себя его функции


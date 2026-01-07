1. https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/create-cluster-kubeadm/
2. https://losst.pro/kak-otklyuchit-ipv6-ubuntu-16-04 - отключить ipv6
3. https://www.itzgeek.com/how-tos/linux/ubuntu-how-tos/install-containerd-on-ubuntu-22-04.html - полезный рассказ про установку containerd
4. https://github.com/containerd/containerd/blob/main/docs/getting-started.md - containerd
   1. https://github.com/containerd/containerd/releases - список релизов
5. https://github.com/helm/helm/releases - helm
6. https://github.com/containernetworking/plugins/releases - что-то там для network (containerd)
7. https://github.com/opencontainers/runc/releases - runC. (Нужен для работы containerd)
8. https://www.thinkcode.se/blog/2019/02/20/kubernetes-service-node-port-range - Как перенастроить NodePortRange (Default: 30000-32767)
9. https://kustomize.io/ - что-то интересное. Как организовать много yaml файлов
10. https://github.com/codeaprendiz/learn_kubernetes - интересный репозиторий (Примеры yaml)
11. https://kubernetes.io/docs/reference/setup-tools/kubeadm/kubeadm-init/ - инициализация кластера через kubeadm
12. https://kubernetes.io/docs/reference/setup-tools/kubeadm/kubeadm-init/#options - kubeadm (список флагов)
13. https://docs.tigera.io/calico/latest/getting-started/kubernetes/self-managed-onprem/onpremises#install-calico - как установить calico
14. https://pkg.go.dev/k8s.io/kubelet/config/v1beta1#KubeletConfiguration - kubelete config file (для kubeadm init)
15. https://pkg.go.dev/k8s.io/kube-proxy/config/v1alpha1#KubeProxyConfiguration - kube-proxy config file (для kubeadm init)
16. https://github.com/kubernetes/kubernetes/blob/master/pkg/proxy/ipvs/README.md - как включить ipvs в kube-proxy

# ---
# Подготовка
# ---

## Шпаргалка про systemctl (`systemd`)
## `sudo systemctl enable SERVICE_NAME` - При загрузке системы сервис будет автоматически стартовать + systemd обновит зависимости запуска
## `--now` - запустит прямо сейчас, не дожидаясь перезагрузки

## Директории, где хранятся UNIT-files
1. /usr/lib/systemd/system/
   1. юниты из установленных пакетов RPM
2. /run/systemd/system/
   1. юниты, созданные в рантайме. Этот каталог приоритетнее каталога с установленными юнитами из пакетов
3. /etc/systemd/system/
   1. юниты, созданные и управляемые системным администратором. Этот каталог приоритетнее каталога юнитов, созданных в рантайме

# Проверка MAC адреса и product_uuid
You can get the MAC address of the network interfaces using the command `ip link` or `ifconfig -a`
The product_uuid can be checked by using the command `sudo cat /sys/class/dmi/id/product_uuid`

# Настроить iptables
Указано в соседнем файле: `./iptables-rules.md`

# Поменять hostname у каждой node
sudo hostnamectl - вывести информацию по системе
sudo hostnamectl set-hostname NEW_HOST_NAME

## Перезагрузить сервер `sudo reboot`
## После перезагрузки проверить `sudo hostnamectl`

# ---
# Отключить SWAP файл. ОБЯЗАТЕЛЬНО
# ---
## Проверить, что сейчас в swapfile (есть он или нет)
sudo swapon --show

## Если вывелась пустота - swapfile отключен
## Но для надежности - провести полную процедуру отключения

## Отключить
sudo swapoff -a
vim /etc/fstab -> comment line: # /swapfile none swap sw 0 0
systemctl mask swap.target

## Проверка размера swap файла. Если вывелось ПУСТОТА = все окей
sudo swapon --show

## Перезагрузить сервер `sudo reboot`
## После перезагрузки проверить повторно `sudo swapon --show`

# ---
# Включить модули ядра
# ---
## Чтобы просто включить (загрузить) модули ядра: `sudo modprobe MODULE_NAME`
## В этом варианте, после перезагрузки сервера - модули опять будут выключены (Что нас не устраивает)
## Чтобы модули ядра включить на постоянной основе, нужно создать файл в директории `/etc/modules-load.d/FILE_NAME.conf`
## В этом файле, указать список модулей (кажды с новой строки), которые должны загружаться при старте системы
## Файлов можно создать сколько угодно. Все файлы `xxx.conf` в указанной директории - будут загружаться при старте системы
## То есть: можно создать файл и прописать необходимые модули и сделать `sudo reboot` -> profit

# Модули ядра: `overlay` и `br_netfilter`
## /etc/modules-load.d/k8s.conf - файл для автозагрузки модулей, после перезагрузки
cat <<EOF | sudo tee /etc/modules-load.d/k8s.conf
overlay
br_netfilter
EOF

## ручная загрузка, чтобы не делать reboot
sudo modprobe overlay && sudo modprobe br_netfilter

## Проверка, что модули включились окей
sudo lsmod | grep br_netfilter
sudo lsmod | grep overlay

## Перезагрузить сервер `sudo reboot`
## После перезагрузки проверить повторно

# ---
# sysctl. IPv4 forward
# ---

## Провека, что сервис (который отвечает за загрузку sysctl - активен)
1. sudo systemctl status systemd-sysctl
   1. ubuntu 20.04 и выше
2. sudo systemctl status procps
   1. до Ubuntu 20.04

## Создать файл с настройками (Отсюда будут применяться настройки при перезагрузке)
cat <<EOF | sudo tee /etc/sysctl.d/k8s.conf
net.bridge.bridge-nf-call-iptables  = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward                 = 1
EOF

## Два варианта применения изменений
1. `sudo systemctl restart systemd-sysctl`
   1. Перезапускает systemd-сервис - аналогично перезапуску системы
   2. Считывает применяет параметры из
      1. /etc/sysctl.conf
      2. /etc/sysctl.d/*.conf
      3. /run/sysctl.d/*.conf
      4. /usr/lib/sysctl.d/*.conf
2. `sudo sysctl --system`
   1. Ручное применение
   2. Сразу показывает, какие параметры были установлены или если где-то была ошибка

## Проверка журнала `systemd-sysctl`
journalctl -u systemd-sysctl

## Проверка, что все загрузилось. Везде должно вывестись: name = 1
sudo sysctl net.bridge.bridge-nf-call-iptables
sudo sysctl net.bridge.bridge-nf-call-ip6tables
sudo sysctl net.ipv4.ip_forward

## Перезагрузить сервер `sudo reboot`
## После перезагрузки проверить повторно

# ---
# IPVS (если хотим использовать IPVS)
# ---

## установить пакеты
apt-get install -y ipset ipvsadm

## НАДО ТЕСТИРОВАТЬ, nf_conntrack - не обязательный пакет
apt-get install -y ipset ipvsadm nf_conntrack

## Включить модули ядра
## Создать файл для автозагрузки - k8s-ipvs.conf (напрмиер)
cat <<EOF | sudo tee /etc/modules-load.d/k8s-ipvs.conf
ip_vs
ip_vs_rr
ip_vs_wrr
ip_vs_sh
nf_conntrack
EOF

## загрузить в ручном режиме (Чтобы не делать reboot)
sudo modprobe ip_vs && sudo modprobe ip_vs_rr && sudo modprobe ip_vs_wrr && sudu modprobe ip_vs_sh && sudo modprobe nf_conntrack

## Проверка, что модули загружены
sudo lsmod | grep ip_vs
sudo lsmod | grep ip_vs_rr
sudo lsmod | grep ip_vs_wrr
sudo lsmod | grep ip_vs_sh
sudo lsmod | grep nf_conntrack

## Перезагрузить сервер `sudo reboot`
## После перезагрузки проверить повторно

# ---
# СХД - Longhorn (`./longhorn`)
# ---

# ---
# CNI - Cilium (`./cilium`)
# ---

# ---
# Установка
# ---

## На Ubuntu с версии 22.04 - Эта директория уже есть (Создавать не надо)
sudo mkdir -m 755 /etc/apt/keyrings

## Узнать текущую версию k8s
## Интересуют только MAJOR.MINOR
## Что в патч версии - нас не интересует
## То есть: 1.33 - ОКЕЙ, 1.33.1 - НЕ_ОКЕЙ
Проверить две ссылки
1. https://kubernetes.io/releases
   1. тут пишутся только MAJOR.MINOR
2. https://github.com/kubernetes/kubernetes/releases
   1. тут пишутся MAJOR.MINOR.PATCH

## Добавление офф репозитория k8s
sudo apt-get update \
  && sudo apt-get install -y apt-transport-https ca-certificates curl gpg \
  && curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.33/deb/Release.key | sudo gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg \
  && echo 'deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.33/deb/ /' | sudo tee /etc/apt/sources.list.d/kubernetes.list

## Установка containerd + runC
wget https://github.com/containerd/containerd/releases/download/v2.2.1/containerd-2.2.1-linux-amd64.tar.gz \
  && sudo tar Czxvf /usr/local containerd-2.2.1-linux-amd64.tar.gz \
  && wget https://raw.githubusercontent.com/containerd/containerd/main/containerd.service \
  && sudo mv containerd.service /usr/lib/systemd/system/ \
  && sudo systemctl daemon-reload \
  && sudo systemctl enable --now containerd \
  && wget https://github.com/opencontainers/runc/releases/download/v1.4.0/runc.amd64 \
  && sudo install -m 755 runc.amd64 /usr/local/sbin/runc \
  && wget https://github.com/containernetworking/plugins/releases/download/v1.9.0/cni-plugins-linux-amd64-v1.9.0.tgz \
  && sudo mkdir -p /opt/cni/bin \
  && sudo tar Cxzvf /opt/cni/bin cni-plugins-linux-amd64-v1.9.0.tgz \
  && sudo rm -f containerd-2.2.1-linux-amd64.tar.gz \
  && sudo rm -f cni-plugins-linux-amd64-v1.9.0.tgz

## Конфиг для кубов. Включение `systemd cgroup driver` + перезапуск containerd
sudo mkdir -p /etc/containerd/ \
  && containerd config default > /etc/containerd/config.toml \
  && sudo sed -i 's/SystemdCgroup \= false/SystemdCgroup \= true/g' /etc/containerd/config.toml \
  && sudo systemctl restart containerd

## Интересный момент про команду. Генерация ДЕФОЛТНОГО конфига и сохранение его по пути `/etc/containerd/config.toml`
containerd config default > /etc/containerd/config.toml

# ---
# Настройка registry-mirror для containerd. У docker.hub - есть ЛИМИТЫ на загрузку
# ---

## Создать директорию и файл
sudo mkdir -p /etc/containerd/certs.d/docker.io
sudo vim /etc/containerd/certs.d/docker.io/hosts.toml

## Прописать правила в файле `/etc/containerd/certs.d/docker.io/hosts.toml`

```
server = "https://docker.io"

[host."https://mirror.gcr.io"]
  capabilities = ["pull","resolve"]
```

## В файле `/etc/containerd/config.toml` - прописать путь до /etc/containerd/certs.d

sudo vim /etc/containerd/config.toml

[plugins."io.containerd.cri.v1.images".registry]
  config_path = "/etc/containerd/certs.d"

## Перезапустить containerd: `sudo systemctl restart containerd`

## ---

## kubeadm, kubelet and kubectl. Заморозить версии
sudo apt-get update \
  && sudo apt-get install -y kubelet kubeadm kubectl \
  && sudo apt-mark hold kubelet kubeadm kubectl

## Чтобы kubelet стартовал при старте системы
## The kubelet is now restarting every few seconds, as it waits in a crashloop for kubeadm to tell it what to do.
sudo systemctl enable --now kubelet

# ---------
# Инициализация кластера - последняя проверка (Только на голове)
# ---------

# ---
# Важно про `iptables` | `ipvs`
# Default: iptables
# ---
## Изменение iptables -> ipvs = доступно только через `kubeadm-config.yaml` файл
## То есть: через флаги в команде `kubeadm init --XXX` -> это сделать `НЕЛЬЗЯ`
## Если хотим использовать `iptables` - ничего делать не надо
## Если хотим использовать `ipvs` - надо поменять параметр в `kubeadm-config.yaml`
## В файле `kubeadm-config.yaml` установить KubeProxyConfiguration.mode = ipvs

# ---
# Важно про `cgroup`/`cgrpupDriver` (kubeadm-config.yaml: `KubeletConfiguration.cgroupDriver`)
# Default: systemd
# ---
## Если при инициализации кластера не указать `cgrpupDriver` - по дефолту будет `systemd` (Указано в документации)
## Важно, `cgrpupDriver` должен совпадать у `CRI` и `kubelet`
## Для `CRI` - руками меняли выше: `sudo sed -i 's/SystemdCgroup \= false/SystemdCgroup \= true/g' /etc/containerd/config.toml`
## Для `kubelet` - установит `kubeadm` в момент инициализации кластера

# --
# Важно про `--pod-network-cidr` (kubeadm-config.yaml: `ClusterConfiguration.networking.podSubnet`)
# Default: 10.244.0.0/16
# --
## при инициализации обязатеольно указываем `--pod-network-cidr=10.244.0.0/16`
## 10.244.0.0/16 === значение `ip-address-pool` из CNI плагина (flannel / calico / ...)
## у `flannel` = 10.244.0.0/16 (default)
## у `calico` = 192.168.0.0/16 (default)
## если при инициализации кластера указать другой --pod-network-cidr
## -> При установке `CNI` получаем ERROR
## -> при установке `CNI` тоже надо поменять (Указать тот, который был при инициализации кластера)

# ---
# Важно про `--service-cidr` (kubeadm-config.yaml: `ClusterConfiguration.networking.serviceSubnet`)
# Default: 10.96.0.0/12
# ---
## Этот CIDR не должен пересекаться с `--pod-network-cidr`
## Есть ограничение: МИНИМАЛЬНАЯ подсеть = /12
## То есть: /11, /10 и меньше - получаем ошибку: `networking.serviceSubnet: Invalid value: "10.128.0.0/10": specified service subnet is too large; for 32-bit addresses, the mask must be >= 12`

# ---
# Важно про `Wireguard-Local-Network`
# Если мы решим соединить сервера по локальной сети
# Вот тут где-то сылка на настройку `...`
# ---
## Сеть, которую будет использовать Wireguard - не должна пересекаться с сетями указанными в `kubeadm` и `CNI`
## По хорошему, сеть для Wireguard надо опустить ниже, чем 10.64.0.0/10
## например:
## -> 10.63.0.0/18. Диапозон = 10.63.0.1 - 10.63.63.254, количество адресов = 16,384
## -> 10.63.0.0/19. Диапозон = 10.63.0.1 - 10.63.31.254, количество адресов = 8,192
## -> 10.63.0.0/20. Диапозон = 10.63.0.1 - 10.63.15.254, количество адресов = 4,096

# ---
# Пример c максимальным количеством адресов
# ---
## --pod-network-cidr=10.64.0.0/10. Диапозон = 10.64.0.1 - 10.127.255.254, количество адресов = 4,194,304
## --service-cidr=10.128.0.0/12. Диапозон = 10.128.0.1 - 10.143.255.254, количество адресов = 1,048,574

# ---
# Важно про `service-node-port-range` (kubeadm-config.yaml: `ClusterConfiguration.apiServer.extraArgs`)
# Default: 30000-32767
# ---
## Это диапозон портов, которые можно указывать `kind:Service` в разделе `nodePort: XXXX`
## То есть: Создать сервис на порту 80/443 (для доступа по DomainName) = НЕЛЬЗЯ (Диапозон default: 30000-32767)
## Как изменить диапозон
## 1. В момент инициализации кластера через `kubeadm` в файле конфигурации
## -> Указать(Это будет добавлено как аргумент к запуску apiServer): `name: service-node-port-range`, `value: 1-40000`
## 2. После инициализации кластера
## -> vim /etc/kubernetes/manifests/kube-apiserver.yaml
## -> добавить информацию по node-port-range (Добавить новый флаг, для запуска api-server)
## -> `- --service-node-port-range=1-40000`
## !ВАЖНО! Updating a file in /etc/kubernetes/manifests will tell the kubelet to restart the static Pod for the corresponding component. Try doing these changes one node at a time to leave the cluster without downtime.

# ---
# Важно про обновление конфигурации кластера
# Link(1): [kubeadm reconfigure docs](https://kubernetes.io/docs/tasks/administer-cluster/kubeadm/kubeadm-reconfigure/#applying-cluster-configuration-changes)
# Link(2): [kubeadm init phase](https://kubernetes.io/docs/reference/setup-tools/kubeadm/kubeadm-init-phase/)
# ---
## После инициализации кластера через kubeadm создается НЕСКОЛЬКО конфигов
## 1. ConfigMap внутри etcd. Название: `kubeadm-config`, namespace: `kube-system`
## 2. Манифесты для компонентов control-plane в директории `etc/kubernetes/manifests`
## -> kube-apiserver, kube-scheduler, kube-controller-manager, CoreDNS, etcd and kube-proxy
## Как обновлять что-то
## 1. Обновить файл `kubeadm-config.yaml`
## 2. Выполнить команду `kubeadm init phase <PHASE_NAME> <COMPONENT_NAME | all> --config ./kubeadm-config.yaml`
## -> Пример: kubeadm init phase control-plane all --config ./kubeadm-config.yaml
## -> Пример: kubeadm init phase control-plane apiserver --config ./kubeadm-config.yaml
## -> Пример: kubeadm init phase upload-config all --config ./kubeadm-config.yaml
## -> Пример: kubeadm init phase upload-config kubelet --config ./kubeadm-config.yaml

# ---------
# Установка HELM (Только на голове)
# ---------
## Найти последний актуальный релиз: https://github.com/helm/helm/releases
## Установить одной командой
wget https://get.helm.sh/helm-v3.19.2-linux-amd64.tar.gz \
  && tar -zxvf helm-v3.19.2-linux-amd64.tar.gz \
  && sudo mv linux-amd64/helm /usr/local/bin/helm \
  && rm -f helm-v3.19.2-linux-amd64.tar.gz

# ---------
# Инициализация кластера - полетели (Только на голове)
# ---------

## Вывести DefaulConfig для kubeadm. Просто аомотреть, что там есть
kubeadm config print [command]

## init-defaults    Print default init configuration, that can be used for 'kubeadm init'
## join-defaults    Print default join configuration, that can be used for 'kubeadm join'
## reset-defaults   Print default reset configuration, that can be used for 'kubeadm reset'
## upgrade-defaults Print default upgrade configuration, that can be used for 'kubeadm upgrade'

## Для инициализации есть две основные команды
## Просто вызвать команду `kubeadm init` - НЕЛЬЗЯ
- с флагом --pod-network-cidr=10.244.0.0/16
- с флагом --config kubeadm-config.yaml

## Без указания конфига, а через флаг --pod-network-cidr
sudo kubeadm init --pod-network-cidr=10.244.0.0/16

## Через указать файл конфигурации `kubeadm-config.yaml` в момент инициализации кластера
kubeadm init --config kubeadm-config.yaml

## ---ВАЖНО---
## Если мы хотим использовать CILIUM + заменить kube-proxy (одна из главных его функций)
1. ЯВНО ВЫКЛЮЧИТЬ параметр для control-plane `--allocate-node-cidrs=false`. Нужно внести корректировку в файл `kubeadm-config.yaml`
2. kubeadm init --config kubeadm-config.yaml --skip-phases=addon/kube-proxy

## Полезная команда при инициализации. Явно указать какой сокет для CRI использовать
kubeadm init --cri-socket unix:///var/run/containerd/containerd.sock

# ОБЯЗАТЕЛЬНО перенести в HOME - что он там просит с конфигами (Только на голове)
mkdir -p $HOME/.kube \
  && sudo cp -i /etc/kubernetes/admin.conf $HOME/.kube/config \
  && sudo chown $(id -u):$(id -g) $HOME/.kube/config

# Не обязательно, но для удобства - снять taint с control-plane
## Если этого не сделать - на control-plane ничего запустить не получится
kubectl taint nodes `NODE_NAME` node-role.kubernetes.io/control-plane:NoSchedule-
kubectl taint nodes k8s-manager-1 node-role.kubernetes.io/control-plane:NoSchedule-

# Запуск сетевого плагина - CNI (Только на голове)
Пока-что не будет запущен сетевой плагин -> Все будет лежать

## Calico - `./calico`

## Cilium - `./cilium`

# Получить токен для присоединения NODE (Только на голове)
kubeadm token create --print-join-command

kubeadm join 1.2.3.4:6443 --token xxx_yyy \
        --discovery-token-ca-cert-hash sha256:AAA_BBB

## Как обновить

### 1. Остановить containerd
sudo systemctl stop containerd

### 2. Обновить containerd
sudo tar Czxvf /usr/local containerd-X.X.X-linux-amd64.tar.gz

### 3. Обновить runc
sudo install -m 755 runc.amd64 /usr/local/sbin/runc

### 4. Обновить CNI plugins
sudo tar Cxzvf /opt/cni/bin cni-plugins-linux-amd64-vX.X.X.tgz

### 5. Запустить containerd
sudo systemctl start containerd

### 6. Проверить
containerd --version
runc --version
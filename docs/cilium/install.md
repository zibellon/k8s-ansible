# ---------
# Подготовка серверов. Все сервера должны соответствовать требованиям
# ---------
1. Версия ядра linux => 6.3
   1. Для примера: Ubuntu 24.04.3 LTS + kernel_6.14.0
   2. Команда для проверки: `uname -a`
2. Установка LLVM + CLANG (Описано ниже)
   1. Версии LLVM + clang, >=18.1
3. Монтирование eBPF FS
   1. `mount | grep /sys/fs/bpf`
   2. Если что-то вывелось - то все окей
   3. Если ничего не вывелось - то надо лечить (Описано ниже)
4. Linux kernel: Модули и конфиги

# ---------
# Установка LLVM + CLANG
# ---------

## Сайт: https://apt.llvm.org/
## Сайт_2: https://llvm.org/
## Сайт_3: https://releases.llvm.org/download.html

## Есть два варианта установки

## Установка через скрипт
## Протестировал в реальных условиях. Все заработало и установилось 
## С офф сайта: `sudo ./llvm.sh <version number> all`
wget https://apt.llvm.org/llvm.sh \
   && sudo chmod +x llvm.sh \
   && sudo ./llvm.sh 20 all
## Какие пакеты будут устанволены
sudo apt-get install -y clang-20 lldb-20 lld-20 clangd-20 clang-tidy-20 clang-format-20 clang-tools-20 llvm-20-dev lld-20 lldb-20 llvm-20-tools libomp-20-dev libc++-20-dev libc++abi-20-dev libclang-common-20-dev libclang-20-dev libclang-cpp20-dev liblldb-20-dev libunwind-20-dev

## Установка ручная
## В боевых условиях не проверял. Проверял на тестовом сервере - получилось странно
## Странно: После установки, пакеты будут называться именно так, как при установке
## То есть: `clang -v` -> Команда не найдена. `clang-20 -v` -> все работает окей
## Зайти на сайт - https://apt.llvm.org/
## Добавить ключ. `wget -O - https://apt.llvm.org/llvm-snapshot.gpg.key | sudo apt-key add -`
## Создать файл `clang_llvm.list` по пути: `vim /etc/apt/sources.list.d/clang_llvm.list` и добавить туда пакеты
## `deb http://apt.llvm.org/noble/ llvm-toolchain-noble-20 main`
## `deb-src http://apt.llvm.org/noble/ llvm-toolchain-noble-20 main`

# ---------
# Монтирование eBPF
# ---------

# Немного информации
## В офф доке, написано: `If the eBPF filesystem is not mounted in the host filesystem, Cilium will automatically mount the filesystem`
## Cilium умеет сам монтировать `/sys/fs/bpf`, если он не смонтирован. Это происходит при старте cilium-agent: он проверяет, есть ли `mountpoint /sys/fs/bpf`, и если нет — монтирует туда bpffs. То есть кластер будет работать без ручного монтирования.
## Зачем тогда рекомендуют монтировать вручную (fstab / systemd)? Если cilium-agent перезапускается, а BPFFS был смонтирован им самим, то при остановке агента mount точка может исчезнуть → все eBPF объекты (maps, prog) будут потеряны. Это приведёт к падению datapath (сетевой трафик встанет), пока агент не поднимется и не восстановит правила.
## Чтобы этого избежать: рекомендуют монтировать на уровне системы (fstab или systemd unit) → тогда `/sys/fs/bpf` существует независимо от агента,и eBPF-объекты могут пережить рестарт cilium-agent.

## Проверить, через ЧТО запущен и управляется kubelet

## Если это НЕ systemd - добавить в /etc/fstab
`bpffs /sys/fs/bpf bpf defaults 0 0`

## Если это systemd (Как в текущем случае) - создать `unit-файл`

cat <<EOF | sudo tee /etc/systemd/system/sys-fs-bpf.mount
[Unit]
Description=Cilium BPF mounts
Documentation=https://docs.cilium.io/
DefaultDependencies=no
Before=local-fs.target umount.target kubelet.service
After=swap.target

[Mount]
What=bpffs
Where=/sys/fs/bpf
Type=bpf
Options=rw,nosuid,nodev,noexec,relatime,mode=700

[Install]
WantedBy=multi-user.target
EOF

## sudo systemctl enable sys-fs-bpf.mount
## Перезагрузить сервер `sudo reboot`

# ---------
# Список конфигов для ядра
# ---------

## Команды для проверки модулей ядра
## Получить список того, что ЕСТЬ в ядре (Список ОЧЕНЬ длинный): `cat /boot/config-$(uname -r)`
## Поиск по определенному конфигу, что в ядре сейчас: `cat /boot/config-$(uname -r) | grep CONFIG_NAME_AAAAA`
## Ищем название модуля ядра, который надо загрузить: `find /lib/modules/$(uname -r) -type f -name '*.ko*' | grep xfrm`

## Проверка, что нужный модуль (файл `*.ko`) есть в системе: `modinfo -n vxlan`
## `-n` -> чтобы вывести только название файла. Без этого флага, там еще много инфы выводится

## запустить скрипт для првоерки модулей ядра - `cilium-check-kernel.sh`
## Если есть ХОТЯ_БЫ один параметр который `IsNotSet` -> установить не полусится

1. Built‑in (=y):
   1. lsmod: не увидит, потому что это не модуль
   2. modprobe: вернёт “Module not found” (и не нужен)
   3. Функциональность уже есть в ядре сразу после загрузки
2. Module (=m):
   1. lsmod: увидите только если модуль загружен.
   2. modprobe <имя_модуля>: загрузит модуль (и зависимости) из /lib/modules/$(uname -r)/.
   3. Для автозагрузки при старте: добавьте имя модуля в /etc/modules-load.d/что-нибудь.conf.

## Если в требованиях указано =m -> в ядре допустимо как =m, так и =y
## Встроенный (=y) вариант тоже удовлетворяет требованию (модуль просто не нужен)

## ---

CONFIG_BPF=y
CONFIG_BPF_SYSCALL=y
CONFIG_BPF_JIT=y
CONFIG_CGROUPS=y
CONFIG_CGROUP_BPF=y
CONFIG_FIB_RULES=y
CONFIG_NET_CLS_BPF=y (оно есть `=m`, модуль: `cls_bpf`)
CONFIG_NET_CLS_ACT=y
CONFIG_NET_SCH_INGRESS=y (оно есть `=m`, модуль: `sch_ingress`)
CONFIG_PERF_EVENTS=y
CONFIG_GENEVE=y (есть `=m`, модуль: `geneve`, ИИ говорит еще: `udp_tunnel`, `ip6_udp_tunnel`)
CONFIG_VXLAN=y (есть `=m`, модуль: `vxlan`, ИИ говорит еще: `udp_tunnel`, `ip6_udp_tunnel`)
CONFIG_SCHEDSTATS=y
CONFIG_NETKIT=y
CONFIG_XFRM=y
CONFIG_XFRM_OFFLOAD=y
CONFIG_XFRM_STATISTICS=y
CONFIG_CRYPTO_SHA1=y
CONFIG_CRYPTO_USER_API_HASH=y (есть `=m`, модуль: `algif_hash`, ИИ говорит еще: `af_alg`)

## ---

CONFIG_NET_SCH_FQ=m (модуль: `sch_fq`)
CONFIG_IP_SET=m (модуль: `ip_set`)
CONFIG_IP_SET_HASH_IP=m (модуль: `ip_set_hash_ip`)
CONFIG_NETFILTER_XT_SET=m (модуль: `xt_set`)
CONFIG_NETFILTER_XT_MATCH_COMMENT=m (модуль: `xt_comment`)
CONFIG_NETFILTER_XT_TARGET_TPROXY=m (модуль: `xt_TPROXY`)
CONFIG_NETFILTER_XT_TARGET_MARK=m (модуль: `xt_mark`, ИИ гооврит что тут нужен: `xt_MARK`, но и `xt_mark` тоже подойдет)
CONFIG_NETFILTER_XT_TARGET_CT=m (модуль: `xt_CT`)
CONFIG_NETFILTER_XT_MATCH_MARK=m (модуль: `xt_mark`)
CONFIG_NETFILTER_XT_MATCH_SOCKET=m (модуль: `xt_socket`)
CONFIG_XFRM_ALGO=m (модуль: `xfrm_algo`)
CONFIG_XFRM_USER=m (модуль: `xfrm_user`)
CONFIG_INET_ESP=m (модуль: `esp4`)
CONFIG_INET6_ESP=m (модуль: `esp6`)
CONFIG_INET_IPCOMP=m (модуль: `ipcomp`)
CONFIG_INET6_IPCOMP=m (модуль: `ipcomp6`)
CONFIG_INET_XFRM_TUNNEL=m (модуль: `xfrm4_tunnel`)
CONFIG_INET6_XFRM_TUNNEL=m (модуль: `xfrm6_tunnel`)
CONFIG_INET_TUNNEL=m (модуль: `tunnel4`)
CONFIG_INET6_TUNNEL=m (модуль: `tunnel6`)

CONFIG_CRYPTO_AEAD=m (есть: `=y`)
CONFIG_CRYPTO_AEAD2=m (есть: `=y`)
CONFIG_CRYPTO_GCM=m (есть: `=y`)
CONFIG_CRYPTO_SEQIV=m (есть: `=y`)
CONFIG_CRYPTO_CBC=m (есть: `=y`)
CONFIG_CRYPTO_HMAC=m (есть: `=y`)
CONFIG_CRYPTO_SHA256=m (есть: `=y`)
CONFIG_CRYPTO_AES=m (есть: `=y`)

## Проблемный товарищ
CONFIG_INET_XFRM_MODE_TUNNEL=m. ТАКОГО ВООБЩЕ НЕТ

## Для `CONFIG_INET_XFRM_MODE_TUNNEL` нет отдельного `*.ko` и строки в `/etc/modules-load.d` не требуется

## Есть “соседние” XFRM-компоненты как модули/встроенные
## Этого достаточно для практической работы tunnel‑режима
1. CONFIG_XFRM_USER, CONFIG_XFRM_ALGO
2. CONFIG_INET_ESP, CONFIG_INET6_ESP
3. CONFIG_INET_XFRM_TUNNEL, CONFIG_INET6_XFRM_TUNNEL

## Пробуем завести политику tunnel (без состояний), затем удаляем
## Если команда создаёт/показывает политику без ошибки “Operation not supported”, режим tunnel поддерживается
1. `sudo ip xfrm policy add dir out src 192.0.2.1 dst 192.0.2.2 ptype main tmpl src 192.0.2.1 dst 192.0.2.2 proto esp mode tunnel`
2. `ip xfrm policy | head`
3. `sudo ip xfrm policy delete dir out src 192.0.2.1 dst 192.0.2.2 ptype main`

## Если `Cilium` ищет именно CONFIG_INET_XFRM_MODE_TUNNEL и ругается, это особенность чекера;
## Практически на Ubuntu 24.04 generic функциональность присутствует
## Единственный способ “сделать как в чеклисте” — другое ядро/сборка, но для работы Cilium это обычно не нужно

## ---
## Список модулей, которые нужно загрузить
## ---
## Создать файл для автозагрузки - k8s-cilium.conf
cat <<EOF | sudo tee /etc/modules-load.d/k8s-cilium.conf
cls_bpf
sch_ingress
udp_tunnel
ip6_udp_tunnel
geneve
vxlan
algif_hash
af_alg
sch_fq
ip_set
ip_set_hash_ip
xt_set
xt_comment
xt_TPROXY
xt_CT
xt_mark
xt_socket
xfrm_algo
xfrm_user
esp4
esp6
ipcomp
ipcomp6
xfrm4_tunnel
xfrm6_tunnel
tunnel4
tunnel6
EOF

## Перезагрузить сервер `sudo reboot`
## После перезагрузки проверить повторно (`sudo lsmod | grep ip6_udp_tunnel`, `sudo lsmod | grep xt_TPROXY`)

# ---------
# Запуск
# ---------

## ЯВНО ВЫКЛЮЧИТЬ параметр для control-plane `--allocate-node-cidrs=false`
## Через `kubeadm` конфиг, в момент инициализации

## Замена `kube-proxy`
1. Инициализация кластера: `kubeadm init --skip-phases=addon/kube-proxy`
2. Добавить флаги к `CiliumInstall.yaml`:
   1. --set 'kubeProxyReplacement=true'
   2. --set 'k8sServiceHost=NODE_IP' (по которому делается JOIN)
   3. --set 'k8sServicePort=6443'
   4. --set 'nodePort.range=1\,50000'

## Очень важное замечание про nodePort.range
1. Такого параметра ЯВНО не указано в helm-refs
2. Но он работает. Почему - НЕПОНЯТНО ...
3. Этот параметр превращается в: `node-port-range: "10000,32767"`
4. Расположение параметра: ConfigMap `cilium-config`
5. А такой параметр есть, у `cilium-agent` (https://docs.cilium.io/en/stable/cmdref/cilium-agent/)
6. Этот параметр должен совпадать с параметром из `kubeadm-config.yaml`

# Генерация финального XXX.yaml файла (чтобы знать, что там внутри)
helm repo add cilium https://helm.cilium.io/

helm template cilium cilium/cilium \
   --version 1.18.4 \
   --namespace kube-system \
   --set 'ipam.operator.clusterPoolIPv4PodCIDRList[0]=10.64.0.0/10' \
   --set 'ipam.operator.clusterPoolIPv4MaskSize=21' \
   --set 'kubeProxyReplacement=true' \
   --set 'kubeProxyReplacementHealthzBindAddr=0.0.0.0:10256' \
   --set 'k8sServiceHost=1.2.3.4' \
   --set 'k8sServicePort=6443' \
   --set 'nodePort.range=1\,50000' \
   --set 'hubble.relay.enabled=true' \
   --set 'hubble.ui.enabled=true' \
   --set 'hubble.peerService.clusterDomain=my-domain-cluster.local' \
   --set 'hostFirewall.enabled=true' > cilium-install.yaml

# Активация
kubectl apply -f cilium-install.yaml

# Информация про запуск: Оно стартует достаточно долго...

# ---
# Как обновить конфиги. Аналогично argo-cd
# ---

## Изменить конфиг - в манифесте
kubectl apply -f <обновленный-манифест>.yaml

## Рестарт Cilium агентов
kubectl rollout restart daemonset/cilium -n kube-system
kubectl rollout restart daemonset/cilium-envoy -n kube-system

## Если есть operator — его тоже
kubectl rollout restart deployment/cilium-operator -n kube-system

## проверяем статус
kubectl get all -n kube-system

# ---------
# Полезные команды
# ---------

# Проверить дропы пакетов
kubectl exec -n kube-system -it $(kubectl get pods -n kube-system -l k8s-app=cilium -o jsonpath='{.items[0].metadata.name}') -- cilium monitor --type drop

# получить список ВСЕХ endpoints
kubectl exec -n kube-system -it $(kubectl get pods -n kube-system -l k8s-app=cilium -o jsonpath='{.items[0].metadata.name}') -- cilium endpoint list

# Получить список подов с информацией с информацией о hostNetwork
kubectl get pods -n kube-system -o custom-columns="NAME:.metadata.name,HOSTNETWORK:.spec.hostNetwork,IP:.status.podIP,NODE:.spec.nodeName"

kubectl get pods -A -o custom-columns="NAME:.metadata.name,HOSTNETWORK:.spec.hostNetwork,IP:.status.podIP,NODE:.spec.nodeName"
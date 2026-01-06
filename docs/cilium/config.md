# Ссылки
1. https://github.com/cilium/cilium - GitHub
2. https://docs.cilium.io/en/stable - Документация
3. https://docs.cilium.io/en/stable/helm-reference - Какие есть настрйоки - HELM
4. https://docs.cilium.io/en/stable/network/kubernetes/kubeproxy-free - Замена kube-proxy
5. https://docs.cilium.io/en/stable/cmdref/ - все параметры, которые вообще есть для cilium
6. https://docs.cilium.io/en/stable/cmdref/cilium-agent/ - какие параметры прининмает Cilium-Agent
7. https://github.com/cilium/cilium/blob/main/install/kubernetes/cilium/values.yaml - Странный сайт, с values ....

# Основные настройки
1. `clusterPoolIPv4PodCIDRList`: Это весь ваш "пирог" IP-адресов. (e.g., 10.64.0.0/10)
2. `clusterPoolIPv4MaskSize`: Это размер "кусочка", который будет отрезан от пирога для каждой ноды
3. 1 NODE === 1 "кусочек"
   1. Если на ноде заканчиваются IP-адреса, Cilium, в отличие от Calico, не выделяет этой ноде второй блок
   2. То есть: Если неправильно задать размер сети - то у Node будет строго ограниченное количество Pod-s
4. Изменить параметры после установки - НЕЛЬЗЯ
5. Примеры
   1. clusterPoolIPv4MaskSize: 21 => Каждая нода получит сеть /21 (2046 доступных IP)
   2. clusterPoolIPv4MaskSize: 22 => Каждая нода получит сеть /22 (1022 доступных IP)
   3. clusterPoolIPv4MaskSize: 23 => Каждая нода получит сеть /23 (510 доступных IP)
   4. clusterPoolIPv4MaskSize: 24 => Каждая нода получит сеть /24 (254 доступных IP)
   5. clusterPoolIPv4MaskSize: 25 => Каждая нода получит сеть /25 (126 доступных IP)
   6. clusterPoolIPv4MaskSize: 26 => Каждая нода получит сеть /26 (62 доступных IP)

# Важный момент про режим работы
Есть два основных режима работы
- `cluster-pool` (Default). https://docs.cilium.io/en/stable/network/concepts/ipam/cluster-pool/
  - Сам управляет IPAM
  - Раздаёт podCIDR каждому Node
  - Не требует, чтобы `kube-controller-manager` делал это
  - То есть в этом режиме `allocate-node-cidrs` в `kube-controller-manager` должен быть выключен (false), чтобы не было конфликта
  - Параметры, которые отвечают за настройку сети
    - `clusterPoolIPv4PodCIDRList`
    - `clusterPoolIPv4MaskSize`
- `kubernetes`. https://docs.cilium.io/en/stable/network/concepts/ipam/kubernetes/
  - В этом режиме Cilium не управляет пулом адресов сам
  - Использует подсети, которые назначаются нодам самим Kubernetes (его компонентом `kube-controller-manager`)
  - В таком случае размер подсети для ноды нужно настраивать через флаги у самого `kube-controller-manager`
    - --allocate-node-cidrs=false/true
    - --cluster-cidr=x.x.x.x/y <- должен совпадать с podSubnet
    - --node-cidr-mask-size-ipv4 = 24 (Default)
    - --node-cidr-mask-size-ipv6 = 64 (Default)
  - Cilium бы просто "подчинялся" этому решению

# Для достижения макстмальной эффективности работы `Cilium` + `eBPF` -> надо ЗАМЕНИТЬ `kube-proxy`
## Основной гайд: https://docs.cilium.io/en/stable/network/kubernetes/kubeproxy-free

## Очень важное замечание про nodePort.range
1. Такого параметра ЯВНО не указано в helm-refs
2. Но он работает. Почему - НЕПОНЯТНО ...
3. Этот параметр превращается в: `node-port-range: "10000,32767"`
4. Расположение параметра: ConfigMap `cilium-config`
5. А такой параметр есть, у `cilium-agent` (https://docs.cilium.io/en/stable/cmdref/cilium-agent/)
6. Этот параметр должен совпадать с параметром из `kubeadm-config.yaml`

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
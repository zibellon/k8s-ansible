# что там требует kubernetes

# Какие порты должны быть открыты
команда: `nc 127.0.0.1 6443 -zv -w 2`

## Default port range for NodePort Services
30000-32767

## Manager
TCP	Inbound	6443	Kubernetes API server	All
TCP	Inbound	2379-2380	etcd server client API	kube-apiserver, etcd
TCP	Inbound	10250	Kubelet API	Self, Control plane
TCP	Inbound	10259	kube-scheduler	Self
TCP	Inbound	10257	kube-controller-manager	Self

## Worker
TCP	Inbound	10250	Kubelet API	Self, Control plane
TCP	Inbound	10256	kube-proxy	Self, Load balancers
TCP	Inbound	30000-32767	NodePort Services†	All
UDP	Inbound	30000-32767	NodePort Services†	All

# Нужно знать IP адреса всех node в кластере (Manager + Worker)
# как их узнать
- `kubectl get nodes -o wide`
# Предположим, что IP адреса такие
- manager = 1.1.1.1
- worker-1 = 2.2.2.2
- worker-2 = 3.3.3.3
- worker-3 = 4.4.4.4

# Так же нужно знать какие сети у kubernetes - `service-cidr` и `pod-cidr`
# как их узнать
- `kubectl get cm -n kube-system kubeadm-config -o yaml | grep serviceSubnet`
- `kubectl get cm -n kube-system kubeadm-config -o yaml | grep podSubnet`
# Предположим, что сети такие
- service-cidr = 10.128.0.0/12
- pod-cidr = 10.64.0.0/10

# ---
# Установить пакет для сохранения iptables
# ---
`sudo apt install iptables-persistent -y`

# ---
# как проверить текущие входище правилас
# ---
`sudo iptables -L INPUT -n --line-numbers -v`

# ---
# Как сохранить правила после редактирования
# ---
`sudo netfilter-persistent save`

# ---
# Небольшая особеннось с `Cilium`
# правило Cilium - должно всегда стоять первым в списке правил
# Не очень разобрался, как он сам его туда ставит
# но - после добавления ВСЕХ НОВЫХ правил - надо дернуть `restart`, через `kubectl`
# ---
`kubectl rollout restart daemonset/cilium -n kube-system`

# ---------
# ---MANAGER---
# ---------

# Добавь правило для localhost (после CILIUM_INPUT)
sudo iptables -I INPUT -i lo -j ACCEPT
# И для established connections
sudo iptables -I INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT

# === kube-api (6443) ===
sudo iptables -I INPUT -p tcp --dport 6443 -s 1.1.1.1 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 6443 -s 2.2.2.2 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 6443 -s 3.3.3.3 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 6443 -s 4.4.4.4 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 6443 -s 127.0.0.1 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 6443 -s 10.128.0.0/12 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 6443 -s 10.64.0.0/10 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 6443 -j DROP

# === ETCD (2379-2380) — только localhost ===
sudo iptables -I INPUT -p tcp --dport 2379:2380 -s 127.0.0.1 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 2379:2380 -s 1.1.1.1 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 2379:2380 -j DROP

# === Kubelet API (10250) — с control-plane и воркеров ===
sudo iptables -I INPUT -p tcp --dport 10250 -s 127.0.0.1 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 10250 -s 1.1.1.1 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 10250 -s 2.2.2.2 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 10250 -s 3.3.3.3 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 10250 -s 4.4.4.4 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 10250 -s 10.64.0.0/10 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 10250 -s 10.128.0.0/12 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 10250 -j DROP

# === kube-scheduler (10259) — только localhost ===
sudo iptables -I INPUT -p tcp --dport 10259 -s 127.0.0.1 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 10259 -s 1.1.1.1 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 10259 -j DROP

# === kube-controller-manager (10257) — только localhost ===
sudo iptables -I INPUT -p tcp --dport 10257 -s 127.0.0.1 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 10257 -s 1.1.1.1 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 10257 -j DROP

# ---------
# ---WORKER---
# ---------

# Добавь правило для localhost (после CILIUM_INPUT)
sudo iptables -I INPUT -i lo -j ACCEPT
# И для established connections
sudo iptables -I INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT

# === Kubelet API (10250) — с control-plane и других нод ===
sudo iptables -I INPUT -p tcp --dport 10250 -s 127.0.0.1 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 10250 -s 1.1.1.1 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 10250 -s 2.2.2.2 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 10250 -s 3.3.3.3 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 10250 -s 4.4.4.4 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 10250 -s 10.64.0.0/10 -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 10250 -s 10.128.0.0/12 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 10250 -j DROP

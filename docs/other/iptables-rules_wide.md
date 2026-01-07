# ---------
# Настройка iptables
# ---------

# Очистим все цепочки
`iptables -F INPUT`
This command flushes all rules from the specified chain and is equivalent to deleting each rule one by one, but is quite a bit faster. The command can be used without options, and will then delete all rules in all chains within the specified table

# Разрешим loopback-интерфейс
iptables -A INPUT -i lo -j ACCEPT
# Разрешим icmp (ping)
iptables -A INPUT -p icmp -j ACCEPT
# Разрешим входящие установленные и связанные соединения
iptables -A INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT

# Основные порты (SSH, HTTP, HTTPS, несколько кастомных портов)
iptables -A INPUT -p tcp --dport 22 -j ACCEPT
iptables -A INPUT --dport 80 -j ACCEPT
iptables -A INPUT --dport 443 -j ACCEPT
iptables -A INPUT --dport 3714 -j ACCEPT
iptables -A INPUT --dport 3717 -j ACCEPT
iptables -A INPUT --dport 30779 -j ACCEPT

iptables -A INPUT -p tcp --dport 6443 -j ACCEPT        # Kubernetes API server
iptables -A INPUT -p tcp --dport 2379 -j ACCEPT        # etcd client API
iptables -A INPUT -p tcp --dport 2380 -j ACCEPT        # etcd client API
iptables -A INPUT -p tcp --dport 10250 -j ACCEPT       # Kubelet API
iptables -A INPUT -p tcp --dport 10256 -j ACCEPT       # kube-proxy
iptables -A INPUT -p tcp --dport 10257 -j ACCEPT       # kube-controller-manager
iptables -A INPUT -p tcp --dport 10259 -j ACCEPT       # kube-scheduler
iptables -A INPUT --dport 30000:32767 -j ACCEPT        # NodePort ALL

# Политики по умолчанию
iptables -P INPUT DROP
iptables -P FORWARD ACCEPT
iptables -P OUTPUT ACCEPT

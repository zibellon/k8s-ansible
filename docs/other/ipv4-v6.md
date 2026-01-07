# Общие вводные
1. Арендовал сервера на timeweb-cloud
2. Ubuntu 22.04
3. Попытался запустить k8s
4. Упал на команде download

curl https://registry.k8s.io/v2
[ERROR ImagePull]: failed to pull image registry.k8s.io/kube-apiserver:v1.30.2
output: E0630 14:52:46.718855    3563 remote_image.go:180] "PullImage from image service failed" err="rpc error: code = Unknown desc = failed to pull and unpack image "registry.k8s.io/kube-apiserver:v1.30.2": failed to resolve reference "registry.k8s.io/kube-apiserver:v1.30.2": unexpected status from HEAD request to https://registry.k8s.io/v2/kube-apiserver/manifests/v1.30.2: 403 Forbidden" image="registry.k8s.io/kube-apiserver:v1.30.2"

# Что проверить
1. Проверить доступность через curl - curl https://registry.k8s.io/v2/kube-apiserver/manifests/v1.30.2
   1. Если там ошибка 403 - это может быть связано с ipv4
2. Проверить через curl - curl --ipv4 https://registry.k8s.io/v2/kube-apiserver/manifests/v1.30.2
   1. Если все прошло - устанавливаем по дефолту использование ipv4

# ВНИМАНИЕ
1. Можно отключить ipv6 - на всей системе целиком (Linux server)
2. После этого - все работает окей
3. НОООО
4. kubernetes - требует ipv4+ipv6
5. Если отключить ipv6 === Некоторые компоненты могут работать некорректно
   1. Пример: kubernetes-dashboard. Оно без ipv6 - НЕ СТАРТУЕТ

# ОТКЛЮЧИТЬ ipv6
1. В файле vim /etc/sysctl.conf
   1. Добавить строчки в самый низ
      1. net.ipv6.conf.all.disable_ipv6=1
      2. net.ipv6.conf.default.disable_ipv6=1
      3. net.ipv6.conf.lo.disable_ipv6=1
   2. Применить изменения: sudo systemctl restart procps
2. В загрузчике GRUB: vim /etc/default/grub
   1. Добавить строчку - GRUB_CMDLINE_LINUX = "ipv6.disable=1"
   2. Если GRUB_CMDLINE_LINUX уже существует - добавить в конец строки ipv6.disable=1
   3. Применить изменение: sudo update-grub2
3. В файле /etc/gai.conf
   1. Uncomment line: precedence ::ffff:0:0/96  100

# Отключение ipv6 (1)
cat >> /etc/sysctl.conf << EOF
net.ipv6.conf.all.disable_ipv6=1
net.ipv6.conf.default.disable_ipv6=1
net.ipv6.conf.lo.disable_ipv6=1
EOF

sudo systemctl restart procps

# Отключение ipv6 (2)
vim /etc/default/grub

GRUB_CMDLINE_LINUX = "... ipv6.disable=1"

sudo update-grub2

# Отключение ipv6 (3) - странный вариант
В файле /etc/gai.conf -> Uncomment line: precedence ::ffff:0:0/96  100

# Проверка - что ipv6 выключен. Должно вывести = 1 (Значит выключено)
cat /proc/sys/net/ipv6/conf/all/disable_ipv6

## Выводится ошибка
cat: /proc/sys/net/ipv6/conf/all/disable_ipv6: No such file or directory
Как решить проблему - ХЗ ...

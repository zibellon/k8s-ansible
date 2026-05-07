Proxmox
———

0. https://94.126.207.67:8006/

1. Определите IP-адрес сервера
На скриншоте указана публичная подсеть 94.126.207.66/31. В сетях /31 доступно всего два адреса. Поскольку поле «Шлюз по умолчанию» (Gateway) занято адресом 94.126.207.66, ваш сервер получил второй адрес в этом диапазоне:

IP-адрес сервера: 94.126.207.67

———

скачать образ в local, BM, - там ссылка на iso нужна
https://releases.ubuntu.com/noble/ubuntu-24.04.4-live-server-amd64.iso

———

Создать сеть - linux-bridge. Никакие поля не заполнял (что странно)

———

Да, всё идеально. Эти настройки (SCSI + VirtIO SCSI single + Discard + SSD emulation) обеспечат максимальную скорость и корректную работу с дисками.

———

Proxmox, shell - найти диски и что-то с ними сделать

lsblk или fdisk -l

———

 sgdisk -n 0:0:0 -t 0:bf01 /dev/sda - создать раздел на весь хвост диска ?
apt update && apt install parted -y - утилита, для работы с дисками
partprobe /dev/sda - создать раздел на диске ? (Сообщить ядру, что есть такой такой новый раздел)

zpool create -f -o ashift=12 fast_storage /dev/sdb /dev/sda5

zpool list
zfs list

вотом зайти в самый верхний уровень = DataCenter и что-то там создать, для раздела Storage. Обязательно поставить галочку на Thin provision

———

настрйока сети

vim /etc/network/interfaces - и туда воткнуть конфигурацию для сетевых интерфейсов (новых, виртуальных)

конфиг
auto vmbr2
iface vmbr2 inet static
        address 10.0.1.1/24
        bridge-ports none
        bridge-stp off
        bridge-fd 0
        # NAT для второй подсети
        post-up iptables -t nat -A POSTROUTING -s '10.0.1.0/24' -o vmbr0 -j MASQUERADE
        post-down iptables -t nat -D POSTROUTING -s '10.0.1.0/24' -o vmbr0 -j MASQUERADE
ifreload -a - применить изменения

в UI Proxmox - мы увидим ACTIVE = yes

валидация
• ip a show vmbr1
• cat /proc/sys/net/ipv4/ip_forward
• iptables -t nat -L POSTROUTING -n -v. target написано MASQUERADE, а в source 10.0.0.0/24

———

Graphic card: Выбери VirtIO-GPU. Это специальный драйвер для виртуализации, он работает быстрее и стабильнее стандартных.

Machine: Выбери q35. Это эмуляция современного чипсета с поддержкой шины PCIe. Для новых ядер Linux это предпочтительный вариант.

BIOS: Выбери OVMF (UEFI). Это современный стандарт загрузки. Старый SeaBIOS (Legacy) потихоньку уходит в прошлое.

Примечание: Когда выберешь UEFI, Proxmox может спросить, куда добавить «EFI Disk» — выбери там наше хранилище fast-zfs.

SCSI Controller: Оставь VirtIO SCSI single. Это отличный контроллер, он позволяет пробрасывать команды TRIM (Discard) внутрь виртуалки, что очень важно для ZFS.

Qemu Agent: ОБЯЗАТЕЛЬНО поставь галочку. Это «мостик» между Proxmox и твоей Ubuntu. Без него Proxmox не будет видеть IP-адрес виртуалки и не сможет корректно её выключить (будет просто «выдергивать шнур»).

Add TPM: Оставь пустым (галочку не ставь). Нам для сервера это не нужно.

———

Память (Memory) - Ballooning Device: Сними галочку.

———

устанока - OS

Subnet: 10.0.0.0/24 (это наша сеть)

Address: 10.0.0.100 (это IP этой конкретной виртуалки)

Gateway: 10.0.0.1 (это адрес нашего Proxmox, который мы настраивали в vmbr1)

Name servers: 8.8.8.8,1.1.1.1 (DNS, чтобы интернет работал по именам)

Search domains: Оставь пустым.


------

Proxmox - после установки поправить репозиторий, для обновления
там по дефолту стоит ENTERPRISE

потом сделать - refresh
потом сделать UPGRADE

——

Proxmox - how to pass throught PCIEs NICs with Proxmox Intel and AMD

——

https://github.com/community-scripts/ProxmoxVE
# ---------
# Подготовка сервера
# ---------

## Включить модули ядра
## Создать файл для автозагрузки - k8s-longhorn.conf (напрмиер)
cat <<EOF | sudo tee /etc/modules-load.d/k8s-longhorn.conf
iscsi_tcp
dm_crypt
EOF

## загрузить в ручном режиме (Чтобы не делать reboot)
sudo modprobe iscsi_tcp && sudo dm_crypt

## Проверка, что модули загружены
sudo lsmod | grep iscsi_tcp
sudo lsmod | grep dm_crypt

## Перезагрузить сервер `sudo reboot`
## После перезагрузки проверить повторно

## Установка пакетов: `open-iscsi`, `NFS`, `cryptsetup`, `dmsetup`
sudo apt-get install open-iscsi nfs-common cryptsetup dmsetup

## ------
## Mount Propagation
## ------
## Что-то очень интересное ... (Нужно дополнительное исследование)

## Проверить ВСЕ точки монтирования в системе
## Выведется древовидная структура
findmnt

```
TARGET    SOURCE       FSTYPE    OPTIONS
/         /dev/sda1    ext4      rw,relatime
```

## Надо проверить тип монтирования у корня `/`
cat /proc/1/mountinfo
cat /proc/1/mountinfo | grep "/ / "

## Найти строку - Где есть `/ /`
## (Пример) `31 1 8:1 / / rw,relatime shared:1 - ext4 /dev/sda1 rw`

## Если в строке указано: `shared:XXX` -> все окей
## Если в строке НЕ указано `shared:XXX` ИЛИ `private`, то Kubernetes не сможет использовать
## Mount Propagation и Longhorn не будет работать

## Файл, откуда запускается kubelet: `/lib/systemd/system`
## Полностью аналогичный файл: `/usr/lib/systemd/system`

## Приоритет загрузки unit-файлов: /etc -> /run -> /lib | /usr/lib
## Редактирование сервиса: `sudo systemctl edit kubelet`

## Создать новый сервис: `shared-root.service`
## Этот сервис, при старте системы будет делать `/` = `shared`
## Объяснение параметров
1. DefaultDependencies=no
   1. Гарантирует, что UNIT выполняется ровно там, где мы его «приклеили» (Before=kubelet.service)
2. Type=oneshot
   1. Это не долгоживущий демон, я просто выполняю одну команду и завершаюсь
3. Before=kubelet.service
   1. Запуск ПЕРЕД `kubelet.service`
4. WantedBy=multi-user.target
   1. Это UNIT, который запускается ПОСЛЕ системы, ПЕРЕД GUI (если есть)
   2. kubelet.service - имеет связь на этот UNIT
   3. Как проверить: ls -l /etc/systemd/system/multi-user.target.wants/
   4. Новый сервис - тоже имеет связь на этот UNIT
   5. Новый сервис - стартует ПЕРЕД `kubelet`
   6. То есть - все контейнеры и системы БУДУТ ЗАПУЩЕНЫ после `shared-root.service` и `kubelet`

cat <<EOF | sudo tee /etc/systemd/system/shared-root.service
[Unit]
Description=Make root filesystem a shared mount
DefaultDependencies=no
Before=kubelet.service

[Service]
Type=oneshot
ExecStart=/bin/mount --make-shared /

[Install]
WantedBy=multi-user.target
EOF

## sudo systemctl enable shared-root.service
## Перезагрузить сервер `sudo reboot`

## ------
## Проблема с `multipath`
## ------
## multipathd is running on `NODE_NAME` known to have a breakage that affects Longhorn. See description and solution at https://longhorn.io/kb/troubleshooting-volume-with-multipath
## GitHub issue: https://github.com/longhorn/longhorn/issues/1210

## Проверить, что `multipath` - вообще есть на Node: `systemctl status multipathd.service`
## Если вывелась пустота или ошибка (Что сервис не найден) - то все окей, проблем не будет
## Если вывелась информация - то надо делать фикс

## Решение_1. Отключить (для тестов и проверки - лучший вариант)
sudo systemctl disable multipathd multipathd.socket
sudo systemctl stop multipathd multipathd.socket

## Решение_2. Внести изменения в `multipathd.service`
## Проверить, какие конфиги: `multipath -t`
## Какое дефолтное значение:
```
blacklist {
   devnode "!^(sd[a-z]|dasd[a-z]|nvme[0-9])"
}
```

## Notice that Longhorn device names start with /dev/sd[x]
## Открыть файл: `sudo vim /etc/multipath.conf` (Или создать его, если файла нет)
## Добавить
```
blacklist {
   devnode "^sd[a-z0-9]+"
}
```

## Перезапустить сервис: `systemctl restart multipathd.service`
## Проверить, какие конфиги: `multipath -t`

## Перезагрузить сервер `sudo reboot`
## После перезагрузки проверить повторно

## Проверка готовности Node
curl -sSfL -o longhornctl https://github.com/longhorn/cli/releases/download/v1.9.0/longhornctl-linux-amd64
chmod +x longhornctl
./longhornctl check preflight

# ---------
# Запуск
# ---------

## Создать директорию `longhorn`

## Создать файл `ns.yaml` и поместить туда содержимое
vim ns.yaml
## Активация
kubectl create -f ns.yaml

## Создать файл `longhorn-configs.yaml` и поместить туда содержимое
vim longhorn-configs.yaml
## Активация
kubectl create -f longhorn-configs.yaml

## Создать файл `longhorn-storage-classess.yaml` и поместить туда содержимое
vim longhorn-storage-classess.yaml
## Активация
kubectl create -f longhorn-storage-classess.yaml

## Скачать файл
curl -O https://raw.githubusercontent.com/longhorn/longhorn/v1.10.1/deploy/longhorn.yaml

## Отредактировать скачанный файл
1. namespace: longhorn-system
   1. Удалить (Было создано выше)
2. name: longhorn-default-resource
   1. Удалить (Было создано выше)
3. name: longhorn-default-setting
   1. Удалить (Было создано выше)
4. name: longhorn-storageclass
   1. Удалить (Было создано выше)
5. name: longhorn-ui
   1. replicas: 2 -> replicas: 1
   2. affinity: ... -> полностью удалить

## Запустить `longhorn.yaml`
kubectl apply -f longhorn.yaml

## Создать файл `longhorn-ingress.yaml` и поместить туда содержимое
vim longhorn-ingress.yaml
## Активация
kubectl apply -f longhorn-ingress.yaml

## HELM
helm repo add longhorn https://charts.longhorn.io --force-update

helm repo update

helm template longhorn longhorn/longhorn \
   --namespace longhorn-system \
   --values ./lh-values.yaml > lh-install.yaml
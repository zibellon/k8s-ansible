# Запуск

## Снять tain с control-plane. Обязательно
Remove the taints on the control plane so that you can schedule pods on it.
kubectl taint nodes --all node-role.kubernetes.io/control-plane-
It should return the following = node/<your-hostname> untainted

## Создать директорию `calico`
## Скачать туда файлы
curl https://raw.githubusercontent.com/projectcalico/calico/v3.30.0/manifests/operator-crds.yaml -O
curl https://raw.githubusercontent.com/projectcalico/calico/v3.30.0/manifests/tigera-operator.yaml -O
curl https://raw.githubusercontent.com/projectcalico/calico/v3.30.0/manifests/custom-resources.yaml -O

## Применить файлы - без изменений (Оператор + CRD)
kubectl create -f operator-crds.yaml
kubectl create -f tigera-operator.yaml

## Изменить (Если надо) `default-ipv4-ippool`
## Файл: `custom-components.yaml`, раздел: `calicoNetwork/ipPools/cidr`
vim custom-resources.yaml

## Активация
kubectl create -f custom-resources.yaml

## Првоерка
watch kubectl get pods -n calico-system

## Что такое blockSize (calicoNetwork/ipPools/blockSize)
1. blockSize = `26` (64 IP-адреса)
2. Правило: 1 Block === 1 Node, 1 Node === MANY Block
   1. То есть - количество Node в кластере НЕ_МОЖЕТ превышать количество Block
   2. Пример
      1. cidr: 192.168.0.0/16, ~65,000 IP-адресов
      2. blockSize /21 = каждый блок 2048 адреса
      3. Максимум только ~32 узла в кластере (65536 / 2048 = 32), даже если на каждом узле только по 50 Pod
3. Изменить ПОСЛЕ инициализации = `НЕЛЬЗЯ`
4. Ограничения
   1. ipv4 = 20 to 32 (inclusive)
   2. ipv6 = 116 to 128 (inclusive)
5. Исходный CIDR делится на блоки (По указанному размеру `blockSize`)
6. Если заранее знать, сколько POD будет на каждой Node - надо подобрать такой blockSize, чтобы на узле было условно 3-4 блока
7. Если блок будет слишком маленький = на одной Node будет МНОГо блоков -> это плохо для производительности
8. Если блок будет слишком большой = Максимальное количество Node в кластере будет ОГРАНИЧЕНО (Прям сильно)
9.  Примеры
   1. /21 = 2048 IP-адресов
   2. /24 = 256 IP-адресов
   3. /26 = 64 IP-адреса
   4. /28 = 16 IP-адресов
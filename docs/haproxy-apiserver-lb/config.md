# Что это такое и как оно работает

## Зачем это нужно
задача: в кластере будет 2-3-5-7 control-plane
каждый worker и control-plane (kubelet и некоторые другие сервисы) - должны знать про ВСЕ control-plane (ip + port)
В случае отказа одного или нескольких control-plane -> запросы к control-plane все равно должны дойти (попытаться дойти)
Как все NODE в кластере узнают ВСЕ адреса ВСЕХ control-plane ?
- через внешний LB. (например в облаках или на отдельном сервере установить haproxy-apiserver-lb)
- на каждой NODE запустить `haproxy-apiserver-lb` и в конфиге указать ВСЕ ip + port всех control-plane
  - можно запустить как linux-service (тоже хороший вариант, меньши проблем и действий)
  - можно запустить как static-pod (был выбран этот вариант)

## 
На каждой node в кластере запускается static-pod - `haproxy-apiserver-lb`
static-pod - находится в директории `/etc/kubernetes/manifests` и kubelet запускает их в момент старта сервиса
Все static-pod лучше запускать в режиме - `networkMode: host` (То есть: все порты внутри контейнере будут доступны с хоста по 127.0.0.1:PORT)
Какие еще есть static-pod
- api-server
- etcd
- kube-scheduler
- kube-controller-manager

`haproxy-apiserver-lb` - балансирует запросы между ВСЕМИ ДОСТУПНЫМИ `control-plane`
То есть: если в кластер добавляется/удаляется control-plane - надо обновить конфиг `haproxy-apiserver-lb` на всех node в кластере. Удалить/Добавить - ip адреса (control-plane)

## Как происходит инициализация node. Тут есть одна особенность с последовательностью запуска

## Какая особенность
- был выбран режим запуска на каждой NODE как static-pod
- static-pod - запускается через kubelet
  - он (kubelet) лезет в директорию `/etc/kubernetes/manifests` и запускает все через CRI
- НО - чтобы kubelet это сделал -> он сам должен запуститься
- Чтобы он запустился ему нужен `/var/lib/kubelet/config.yaml` (которого НЕТ при установке kubelet)
- ТО есть: этот конфиг надо создать
- kubeadm - создает этот конфиг
- НО - чтобы kubeadm создал этот конфиг при выполнении команды JOIN -> ему нужно подключиться к control-plane
- А чтобы подключиться к control-plane -> нужен запущенный static-pod `haproxy-apiserver-lb`
- Замкнутый круг

## Решение и послежовательность установки
- устанавливаются все компоненты
- создается конфиг для `haproxy-apiserver-lb`
  - `/etc/kubernetes/manifests/haproxy-apiserver-lb.yaml` - static-pod
  - `/etc/kubernetes/haproxy-apiserver-lb.cfg` - конфиг для haproxy
- создается МИНИМАЛЬНЫЙ конфиг для старта kubelet
  - Надо чтобы он запустился и запустил все, что находится в директории для static-pods
- создается так называемы DROP-IN для kubelet.service = `/etc/systemd/system/kubelet.service.d/20-standalone.conf`
- kubelet перезапускается и DROP-IN - перекрывает правила запуска из дефолтного конфига
- kubelet запускает все, что находится в директории static-pods
  - там на данный момент только ОДИН manifest
- kubelet останавливается и возвращается в исходное состояние
- Теперь контейнер, запещенный как static-pod - управляется на уровне CRI (containerd)
- Теперь на node доступен 127.0.0.1:16443 (это haproxy-apiserver-lb)
  - при запросе к этому адресу - запрос будет балансироваться между ВСЕМИ control-plane

## Что внутри конфига (`./other/config.cfg`)
несколько haproxy-backend + проверка доступности
То есть: Если control-plane-3 выйдет из строя - то трафик на нее не пойдет, так как haproxy проверяет доступность каждого ip перед балансированием запроса туда
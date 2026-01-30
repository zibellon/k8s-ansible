# Полезные команды

sudo kubeadm token list - получить список токенов для подключения Nodes (Только на голове)
kubectl api-resources -o wide - получить список всех ресурсов, которые есть в KUBE

kubectl get nodes - Получить список Node
kubectl get nodes --show-labels - Получить список Nodes + Labels
kubectl label nodes <node-name> disktype=ssd - добавление label к node.  disktype: ssd
kubectl delete node <NODE_NAME> - удалить Node из кластера
kubectl get pod -n kube-flannel - получить pod для flannel
kubectl delete all --all -n kube-flannel - удалить все содержимое Namespace kube-flannel
kubectl get namespace - получить список всех namespace
kubectl delete -f delete-all-resources.yaml
kubectl get all -n kube-flannel
kubectl get all -n kubernetes-dashboard
kubectl get clusterroles - получить список всех ресурсов `kind: ClusterRole`
kubectl get endpoints -A - эта тема не видна в выборке all по namespace

# Отладка
## Проблема = The connection to the server 1.2.3.4:6443 was refused - did you specify the right host or port?
sudo systemctl status kubelet
sudo journalctl -u kubelet
sudo systemctl restart kubelet

sudo systemctl status kube-apiserver
sudo journalctl -u kube-apiserver
sudo systemctl restart kube-apiserver

sudo kubeadm init phase kubelet-start
sudo kubeadm config view

`telnet 1.2.3.4 6443` - проверка доступности ЧЕГО-ТО на порту 6443 (api-server)

# Снять taint с ControlPlane
kubectl taint nodes k8s-manager-1 node-role.kubernetes.io/control-plane:NoSchedule-

# Вывести пример конфига для kubeadm
kubeadm config print

# Полезные записи
- контейнеры могут общаться друг с другом по имени. Проверили (По видео) только на сервисах (Service)
- kube-proxy - работает в режиме iptables или ipVS
- kubectl -n kube-system get configmap kube-proxy -o yaml - вывести на экран информацию по конфигу компонента kube-proxy
  - Там должна быть информация на счет iptables или ipvs
- Пинговать сервис = НЕЛЬЗЯ
  - Если в режиме ipVS = пинговаться будет ОК
  - Если в режиме iptables = пинговаться НЕ БУДЕТ
- Если мы рабоатем в рамках одного Namespace - можно обращаться только по имени Service

# Ошибка - слишком длинный
The CustomResourceDefinition "installations.operator.tigera.io" is invalid: metadata.annotations: Too long: may not be more than 262144 bytes
Надо использовать: `kubectl create -f ./FILE_NAME.yaml`

# Проверка доступности ресурса
kubectl auth can-i list ingresses --as=system:serviceaccount:ns-my-test-2:traefik-ingress-controller

# ingress = ns_1, service = ns_2
Насколько сказано в официальной документации Kubernetes (и подтверждается обсуждением в GitHub), у ресурса Ingress бэкенд-Service обязан находиться в том же namespace, что и сам Ingress. Любые попытки указать Service из другого namespace считаются некорректными (Ingress API не позволяет кросс-namespace-ссылки).

The documentation says the backend must be in the same namespace as the ingress so this is not something we can change… 

То есть: если Ingress лежит в namespace: default, то и сервис svc-my-nginx-3-2 должен находиться в default. Кросс-namespace-Service (когда Ingress в одном namespace, а Service — в другом) не поддерживается.

# Установка Helm
wget https://get.helm.sh/helm-v3.16.3-linux-amd64.tar.gz \
  && tar -zxvf helm-v3.16.3-linux-amd64.tar.gz \
  && mv linux-amd64/helm /usr/local/bin/helm \
  && rm -f helm-v3.16.3-linux-amd64.tar.gz

# Как выглядит systemctl (sysctl)
## sudo systemctl status systemd-sysctl
## cat /lib/systemd/system/systemd-sysctl.service
---

#  SPDX-License-Identifier: LGPL-2.1-or-later
#
#  This file is part of systemd.
#
#  systemd is free software; you can redistribute it and/or modify it
#  under the terms of the GNU Lesser General Public License as published by
#  the Free Software Foundation; either version 2.1 of the License, or
#  (at your option) any later version.

[Unit]
Description=Apply Kernel Variables
Documentation=man:systemd-sysctl.service(8) man:sysctl.d(5)
DefaultDependencies=no
Conflicts=shutdown.target
After=systemd-modules-load.service
Before=sysinit.target shutdown.target
ConditionPathIsReadWrite=/proc/sys/net/

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/lib/systemd/systemd-sysctl
TimeoutSec=90s

# Запустить контейнер на любой ноде + доступ к консоли
kubectl debug node/internal-worker-2 -it --image=busybox

# Проверить доступ к домену и IP адрес
nslookup domain-name-123.com

# поменять права у volume
## То есть - это делается через одноразовую JOB
## Главное правило: PVC + PV = должны быть созданы (В нашем случае - volume восстанавливается через Longhorn)
kubectl apply -f - <<EOF
apiVersion: batch/v1
kind: Job
metadata:
  name: fix-permissions-on-volume
  namespace: ns-xxx-yyy
spec:
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: fix-perms
          image: busybox:1.37
          command: 
            - sh
            - -c
            - |
              echo "Fixing"
              chown -R 70:70 /fix-data
              ls -la /fix-data
              echo "Done!"
          securityContext:
            runAsUser: 0
          volumeMounts:
            - name: fix-data
              mountPath: /fix-data
              subPath: some-data
      volumes:
        - name: fix-data
          persistentVolumeClaim:
            claimName: pvc-lh-xxx-yyy-pg-data
EOF

`kubectl logs -n ns-xxx-yyy -l job-name=fix-permissions-on-volume`

`kubectl delete job fix-permissions-on-volume -n ns-xxx-yyy`

# Как создать какой-то СУПЕР-шифрованный пароль
## Требуется для VaultWarden
`echo -n 'my_secret_pass' | argon2 "$(openssl rand -base64 32)" -e -id -k 65540 -t 3 -p 4`

# Где лежит конфиг для kubelet
`cat /var/lib/kubelet/config.yaml`

# Откуда kubelet берет конфиги для static-pods. Запускаются самостоятельно, при старте kubelet
`/etc/kubernetes/manifests`

# Посмотреть. логи по kubelet, без страниц
`sudo journalctl -u kubelet -n 100 --no-pager`

# Дурка с containerd
`ctr -n k8s.io container ls`
`ctr -n k8s.io task ls`

# как увидеть Endpoints, куда идет трафик реально
`kubectl get endpointslice` - получение всех endpoint-slice
`kubectl get EndpointSlice kubernetes -o yaml`

# Как проверять watchdog | softdog
`lsmod | grep soft`
## Вывод
softdog                12288  0

`cat /sys/class/watchdog/watchdog0/timeout`
## Вывод
30

`ls -la /dev/watchdog`
## Вывод
crw------- 1 root root 10, 130 Jan 11 20:45 /dev/watchdog

# как работать с `crictl`
`sudo crictl ps -a` - посмотреть список контейнеров (аналог - docker ps -a)
`sudo crictl logs <CONTAINER_ID>` - посмотреть логи контейнера (поддержка --tail | -f)

## Ответ
addressType: IPv4
apiVersion: discovery.k8s.io/v1
endpoints:
- addresses:
  - 1.2.3.4
  conditions:
    ready: true
kind: EndpointSlice
metadata:
  creationTimestamp: "2025-12-05T19:35:24Z"
  generation: 1
  labels:
    kubernetes.io/service-name: kubernetes
  name: kubernetes
  namespace: default
  resourceVersion: "206"
  uid: 9d7cf5ef-8f63-4ea4-952a-53a404bf9741
ports:
- name: https
  port: 6443
  protocol: TCP

`kubectl get svc kubernetes -o yaml`

## Ответ
apiVersion: v1
kind: Service
metadata:
  creationTimestamp: "2025-12-05T19:35:24Z"
  labels:
    component: apiserver
    provider: kubernetes
  name: kubernetes
  namespace: default
  resourceVersion: "203"
  uid: c1dbe882-58e3-4078-b9e2-9f611d2d9160
spec:
  clusterIP: 10.128.0.1
  clusterIPs:
  - 10.128.0.1
  internalTrafficPolicy: Cluster
  ipFamilies:
  - IPv4
  ipFamilyPolicy: SingleStack
  ports:
  - name: https
    port: 443
    protocol: TCP
    targetPort: 6443
  sessionAffinity: None
  type: ClusterIP
status:
  loadBalancer: {}

# Как получить значения секретов в DECODE
kubectl get secret vault-self-creds -n ns-vault -o json | jq '.data | map_values(@base64d)'

# Как посмотреть логи от CronJob
kubectl get CronJob -n ns-vault
kubectl logs -n ns-vault -l job-name --tail=100

# HELM, commands
helm history <release-name> -n ns-gitlab
helm rollback <release-name> 2 -n ns-gitlab
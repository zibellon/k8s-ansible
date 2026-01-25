# HAProxy Ingress Controller - TCP Gateway

## Архитектура

VPN (1.2.3.4) → Node:28945 → HAProxy Ingress → svc-pg-dev:5432
                              ↑
                    externalTrafficPolicy: Local
                    (source IP сохраняется)
                              ↓
                    NetworkPolicy ipBlock фильтрация

# Установка

## Добавить repo
helm repo add haproxytech https://haproxytech.github.io/helm-charts
helm repo update

## Сгенерировать манифесты
helm template haproxy-ingress haproxytech/kubernetes-ingress \
  --namespace ns-haproxy-master \
  -f helm-values.yaml > install.yaml

## Сгенерировать манифесты
helm template haproxy-ingress haproxytech/kubernetes-ingress \
  --namespace ns-haproxy-master \
  --set 'namespace.create=true' \
  --set 'controller.kind=DaemonSet' \
  --set 'controller.ingressClassResource.name=haproxy-master-lb' \
  --set 'controller.ingressClass=haproxy-master-lb' \
  --set 'controller.podLabels.app-key=haproxy' \
  --set 'controller.podLabels.app-env=prod' \
  --set 'controller.defaultTLSSecret.enabled=false' \
  --set 'controller.logging.level=info' \
  --set 'controller.logging.traffic.address=stdout' \
  --set 'controller.logging.traffic.format=raw' \
  --set 'controller.logging.traffic.facility=daemon' \
  --set 'controller.service.enabled=true' \
  --set 'controller.service.type=ClusterIP' \
  --set 'controller.service.enablePorts.http=false' \
  --set 'controller.service.enablePorts.https=false' \
  --set 'controller.service.enablePorts.quic=false' \
  --set 'serviceAccount.create=true' > install.yaml

## Применить
kubectl apply -f install.yaml

## Применить NetworkPolicy
kubectl apply -f network-policies.yaml

---

# Добавление нового роута - `./other/add-backend-example.yaml`

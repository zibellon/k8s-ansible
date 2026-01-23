# Запуск

## Создать директорию `traefik`

## Скачать туда файл
curl https://raw.githubusercontent.com/traefik/traefik/v3.5/docs/content/reference/dynamic-configuration/kubernetes-crd-definition-v1.yml -O

## Применить CRD (Без изменений)
kubectl apply -f kubernetes-crd-definition-v1.yml

## Создать файл - `traefik-full.yaml` и положить туда содержимео `./traefik-full.yaml`
vim traefik-full.yaml

## Замечние по ресурсам `traefik`
## Есть такой файл. Но он не законченный, в нем указан `ClusterRoleBinding`, но не указан `ServiceAccount`
## То есть, при его применении будет ошибка: Что такого `ServiceAccount` еще нет
## Но в этом файле указаны все RBAC права, которые должны быть у `traefik`
## Эти права нужно отсюда скопировать и вставить в `traefik-full.yaml`
curl https://raw.githubusercontent.com/traefik/traefik/v3.5/docs/content/reference/dynamic-configuration/kubernetes-crd-rbac.yml -O

## Применить файл
kubectl apply -f traefik-full.yaml

## Достук к traefik-dashboard по domain-name (HTTP / HTTPS)
1. Если установлен `cart-manager` и он настроен
   1. Создать файл `traefik-ingress-https.yaml` и положить туда содержимое `./traefik-ingress-https.yaml`
   2. vim traefik-ingress-https.yaml
   3. kubectl apply -f traefik-ingress-https.yaml
2. Если без CertManager (Только `http`, NO_HTTPS)
   1. Создать файл `traefik-ingress-http.yaml` и положить туда содержимое `./traefik-ingress-http.yaml`
   2. vim traefik-ingress-http.yaml
   3. kubectl apply -f traefik-ingress-http.yaml

## `Certificate.namespace === Ingress/IngressRoute.namespace`
## Если это не так, будет ошибка
{
  "namespace":"ns-traefik-master",
  "error":"secret ns-traefik-master/cert-traefik-dashboard-my-domain-com does not exist",
  "message":"Error configuring TLS"
}

## Helm example
helm template traefik traefik/traefik \
--namespace kek-lol-123 \
--create-namespace \
--version 38.0.1 \
-f traefik-1.yaml > traefik-kek.yaml

## Helm example
helm template traefik traefik/traefik \
--namespace kek-lol-123 \
--create-namespace \
--version 38.0.1 \
--set image.tag=v3.6.2 \
--set deployment.kind=DaemonSet \
--set ingressClass.enabled=true \
--set ingressClass.name=ing-class-123 \
--set ingressClass.isDefaultClass=false \
--set service.type=NodePort \
--set service.spec.externalTrafficPolicy=Local \
--set ports.web.port=80 \
--set ports.web.nodePort=80 \
--set ports.web.expose.default=true \
--set ports.web.exposedPort=80 \
--set ports.websecure.port=443 \
--set ports.websecure.nodePort=443 \
--set ports.websecure.expose.default=true \
--set ports.websecure.exposedPort=443 \
--set ports.websecure.tls.enabled=true \
--set ports.traefik.expose.default=false \
--set providers.kubernetesCRD.enabled=true \
--set providers.kubernetesCRD.ingressClass=ing-class-123 \
--set providers.kubernetesCRD.allowCrossNamespace=true \
--set providers.kubernetesIngress.enabled=true \
--set providers.kubernetesIngress.ingressClass=ing-class-123 \
--set logs.general.level=DEBUG \
--set logs.general.format=json \
--set logs.access.enabled=true \
--set logs.access.format=json \
--set api.dashboard=true \
--set api.insecure=true \
--set metrics.prometheus.enabled=true \
--set entryPoints.web.asDefault=true \
--set entryPoints.web.forwardedHeaders.insecure=true \
--set entryPoints.web.proxyProtocol.insecure=true \
--set entryPoints.web.transport.respondingTimeouts.idleTimeout=600 \
--set entryPoints.web.transport.respondingTimeouts.writeTimeout=600 \
--set entryPoints.web.transport.respondingTimeouts.readTimeout=600 \
--set entryPoints.websecure.asDefault=true \
--set entryPoints.websecure.forwardedHeaders.insecure=true \
--set entryPoints.websecure.proxyProtocol.insecure=true \
--set entryPoints.websecure.transport.respondingTimeouts.idleTimeout=600 \
--set entryPoints.websecure.transport.respondingTimeouts.writeTimeout=600 \
--set entryPoints.websecure.transport.respondingTimeouts.readTimeout=600 \
--set securityContext.runAsNonRoot=true \
--set securityContext.runAsUser=65532 \
--set securityContext.runAsGroup=65532 \
--set podSecurityContext.runAsNonRoot=true \
--set podSecurityContext.runAsUser=65532 \
--set podSecurityContext.runAsGroup=65532 > traefik-kek.yaml
# Ссылки
1. https://cert-manager.io/docs/reference/api-docs/ - спецификация всех `CRD`

## При создании сертификата получил такой warning
Warning: spec.privateKey.rotationPolicy: In cert-manager >= v1.18.0, the default value changed from `Never` to `Always`.

## Последовательность действий для получения HTTPS соединения на domain
1. Развернуть Traefik + CRD
   1. В провайдерах указать: rubernetes, kubernetesCrd
   2. Почему два: cert-manager НЕ_УМЕЕТ работать с Traefik.crd.IngressRoute
   3. cert-manager - создает Ingress, для прохождения ACME. Обычный Ingress
2. Развернуть cert-manager
3. Создать cert-manager.Issuer. ClusterIssuer - чтобы не было ограничений по namespace
4. Создать cert-manager.Certificate
   1. Указать domainName и secretName
   2. secretName - ВАЖНО, он нужен
5. Создать traefik.IngressRoute + указать tls: SECRET_NAME
   1. traefik.IngressRoute - возьмет сертификат из kubernetes.Secret

## Получается - для каждого traefik.IngressRoute - надо создать сертификат
## Все сертификаты контролируются через одну точку (cert-manager)

## ОЧЕНЬ важный момент про `cert-manager.Certificate`
## У ресурса `kind: Certificate` ОБЯЗАТЕЛЬНО надо указать `namespace`
## Пример: `namespace: ns-traefik-master`
1. cert-manager на основе ресурса `kind: Certificate` - делает сертификат (через LetsEncrypt)
2. Результат сертификата = приватный | публичный ключи
3. cert-manager создает ресурс `kind: Secret`
4. `namespace` у этого ресурса (`kind: Secret`) === `namespace` у ресурса `kind: Certificate` (на основе которого сделан сертификат)
5. Чтобы ресурс `kind: Ingress` увидел ресурс `kind: Secret` (где лежит сертификат) - они должны быть в одном `namespace` 

## Как `очистить` ресурсы после создания `Certificate`
## Это касается только `Certificate`, `Secret`
1. cert-manager не удаляет ресурс `kind: Secret` при удалении ресурса `kind: Certificate`
2. То есть = два действия:
   1. Удалить Certificate (Удалить за собой все связанные сущности, кроме Secret)
   2. Удалить Secret
3. Можно настроить на автоматическое удаление
   1. При запуске, добавить в контроллер флаг: `--enable-certificate-owner-ref`

## Как происходит Обновление сертификатов
## Сертификат хранится в ресурсе `kind: Secret`. У сертификата есть срок жизни - условно 90 дней
## По истечении этого срока - сертификат надо обновить
## cert-manager - автомаически обновляет сертификаты (Обновляет данные в ресурсе `kind: Secret`)
## По дефолту: когда пройдет 2/3 от времени жизни сертификата
## То есть: ingress-controller (в этом случае Traefik), должен как-то отреагировать на обновление ресурса `kind: Secret`
## Чтобы начать использовать новый ресурс `kind: Secret` и не было downtime

## Какие есть параметры у ресурса `kind: Certificate`
## duration > renewBefore (Это логично)
duration: 2160h, 90d - сколько живет сертификат
renewBefore: 360h, 15d - за сколько дней до окончания сертификата надо обновить
renewBeforePercentage: 15, за сколько процентов до окончания сертификата надо обновить

## Проверка статуса готовности ресурса Certificate
## Есть флаг: ready = True/False
kubectl get Certificate -A

## Последовательность проверки при решение проблемы

kubectl get Certificate
kubectl describe Certificate <CertName>

kubectl get certificaterequest
kubectl describe certificaterequest <Name>

kubectl get order
kubectl describe order <OrderName>

kubectl get challenge
kubectl describe challenge <ChallengeName>
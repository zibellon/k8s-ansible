# Запуск

## Создать директорию `traefik`

## Скачать туда файл
curl https://raw.githubusercontent.com/traefik/traefik/v3.5/docs/content/reference/dynamic-configuration/kubernetes-crd-definition-v1.yml -O

## Применить CRD (Без изменений)
kubectl apply -f kubernetes-crd-definition-v1.yml

## Создать файл - `traefik-full.yaml` и положить туда содержимео `./other/traefik-full.yaml`
vim traefik-full.yaml

## Замечние по ресурсам `traefik`
## Есть такой файл. Но он не законченный, в нем указан `ClusterRoleBinding`, но не указан `ServiceAccount`
## То есть, при его применении будет ошибка: Что такого `ServiceAccount` еще нет
## Но в этом файле указаны все RBAC права, которые должны быть у `traefik`
## Эти права нужно отсюда скопировать и вставить в `traefik-full.yaml`
curl https://raw.githubusercontent.com/traefik/traefik/v3.5/docs/content/reference/dynamic-configuration/kubernetes-crd-rbac.yml -O

## Применить файл
kubectl apply -f traefik-full.yaml
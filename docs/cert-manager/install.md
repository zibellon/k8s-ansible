# Запуск

## Создать директорию `cert-manager`

## Скачать туда файл
curl -LO https://github.com/cert-manager/cert-manager/releases/download/v1.19.1/cert-manager.yaml

## Применить файл (без изменений)
kubectl apply -f cert-manager.yaml

## Создание Issuer / ClusterIssuer

## Создать файл с `cluster-issuer` и положить туда содержимое `./other/traefik-cluster-issuer.yaml`
vim traefik-cluster-issuer.yaml

## Применить файл
kubectl apply -f traefik-cluster-issuer.yaml

## Проверить, что все ОКЕЙ
kubectl get ClusterIssuer

# Запуск

## Создать директорию `argo-cd`

## Создать файл `namespace.yaml` и поместить туда содержимое `./namespace.yaml`
vim namespace.yaml
## Применить
kubectl apply -f namespace.yaml

## Скачать файл
curl -O https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

## Отредактировать скачанный файл
- конфиги
- параметры запуска
- известные хосты

## Активировать
## Указать флаг `-n argocd` = ОБЯЗАТЕЛЬНО
## В `*.yaml` манифестах не указано, в каком `namespace` должны находится созданные ресурсы
## Если не указать этот флаг - все ресурсы будут созданы в `namespace` = `default`
kubectl apply -n argocd -f install.yaml

## Создать файл `argocd-ingress-https.yaml` и поместить туда содержимое
vim argocd-ingress-https.yaml
## Активация
kubectl apply -f argocd-ingress-https.yaml

## Как получить пароль для входа в argocd-UI
## После запуска, пароль для пользователя `admin` генерируется в автоматическом режиме
## Пароль хранится в k8s.secret = `argocd-initial-admin-secret`, в поле `password`

## Как получить пароль через kubectl
`kubectl get secret argocd-initial-admin-secret -n argocd -o jsonpath='{.data.password}' | base64 --decode`

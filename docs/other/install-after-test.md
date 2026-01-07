# Что делать после запуска кластера

# Traefik
Настроить два провайдера = kubernetesCRD, kubernetes
Загрузить файл с конфигами (dynamic)
Вся статическая конфигурация указана через CLI (--XXX)

# Nginx-test (http)
Поднять NGINX - на голом порту (NodePort)
Поднять NGINX - на домене (через Ingress)
Поднять NGINX - на домене (через Ingress + Traefik.annotations)
Поднять NGINX - на домене (через Traefik.IngressRoute)
Настроить http_www -> http (Можно сделать только: annotations / Traefik.IngressRoute)

# CertManager
Просто запустить
Создать Issuer = ClusterIssuer (Он понадобится)

# Nginx-test (https)
Создать сертификат через cert-manager (www.my-nginx.my-domain.com / my-nginx.my-domain.com)
Поднять NGINX - на домене (через Ingress + tls)
Поднять NGINX - на домене (через Ingress + Traefik.annotations + tls)
Поднять NGINX - на домене (через Traefik.IngressRoute + tls)
Настроить http_www -> https (Можно сделать только: annotations / Traefik.IngressRoute)
Настроить http -> https (Можно сделать только: annotations / Traefik.IngressRoute)
Настроить https_www -> https (Можно сделать только: annotations / Traefik.IngressRoute)

# Traefik-dashboard + HTTPS (Через cert-manager)
Создать сертификат через cert-manager (my-ns-2-traefik-dashboard.my-domain.com)
Настроить https для traefik-dashboard

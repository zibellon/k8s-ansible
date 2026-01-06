# Про IngressClassName + CertManager

Из документации Traefik (https://doc.traefik.io/traefik/providers/kubernetes-ingress/#ingressclass)
If the parameter is set, only Ingresses containing an annotation with the same value are processed. Otherwise, Ingresses missing the annotation, having an empty value, or the value `traefik` are processed

# Ресурс IngressClass
По хорошенму - надо создать ОБЯЗАТЕЛЬНО ресурс `IngressClass`. Чтобы Traefik явно знал, какие Ingress ему обрабатывать
Однако: Если мы работаем через CRD IngressRoute - то там нельзя указать ingressClassName

## IngressClassName. Дополнительная информация
Чтобы запущенный Traefik точно знал, какие Ingress / IngressRoute (CRD) - ему обрабатывать

1. Default Ingress
   1. Можно указать аннотацию `"kubernetes.io/ingress.class": "traefik-master-lb"`
   2. traefik-master-lb = просто как label. Не надо создавать никаких IngressClass
   3. Можно указать `spec.ingressClassName: traefik-master-lb`
   4. Вот это уже - надо создать IngressClass
2. CRD_IngressRoute
   1. Только через аннотацию `"kubernetes.io/ingress.class": "traefik-master-lb"`
   2. Нет поля `spec.ingressClassName`

### Интересная формулировка в документации Traefik
1. IngressRoute (CRD)
   1. Value of kubernetes.io/ingress.class annotation that identifies `resource` objects to be processed.
   2. If the parameter is set, only `resources` containing an annotation with the same value are processed. Otherwise, `resources` missing the annotation, having an empty value, or the value traefik are processed
2. Ingress (Default)
   1. Value of kubernetes.io/ingress.class annotation that identifies `Ingress objects` to be processed.
   2. If the parameter is set, only `Ingresses` containing an annotation with the same value are processed. Otherwise, `Ingresses` missing the annotation, having an empty value, or the value traefik are processed.

То есть: в конфигурации traefik указано `--providers.kubernetescrd.ingressclass=traefik-master-lb` и `--providers.kubernetesingress.ingressclass=traefik-master-lb` -> значит теперь мы работаем только с
1. IngressRoute (CRD) + annotations
2. Ingress (Default) + annotations
   1. Можно через ingressClassName - но это кажется лишним
   2. Это нужно - только для ФИЛЬТРАЦИИ, чтобы точно правильный ingress-controller обработал Ingress

# Про default Ingress (По правилам kubernetes, без аннотаций)

У Traefik есть два entrypoints: web, websecure
--entryPoints.web.address=:80
--entryPoints.websecure.address=:443
Создали Ingress, самый обычный - БЕЗ АННОТАЦИИ
Он будет прослушиваться НА ВСЕХ entrypoints

Делаем запрос по домену http://my_domain.com = попадаем на сервер, на порт 80
Делаем запрос по домену https://my_domain.com = попадаем на сервер на порт 443

И там сработает service

Правила сервиса
- name: web
  port: 80
  targetPort: 80
  nodePort: 80 # Доступно на ВСЕХ Node в Cluster
- name: websecure
  port: 443
  targetPort: 443
  nodePort: 443 # Доступно на ВСЕХ Node в Cluster

The AsDefault option marks the EntryPoint to be in the list of default EntryPoints. EntryPoints in this list are used (by default) on HTTP and TCP routers that do not define their own EntryPoints option.

If there is no EntryPoint with the AsDefault option set to true, then the list of default EntryPoints includes all HTTP/TCP EntryPoints.

If at least one EntryPoint has the AsDefault option set to true, then the list of default EntryPoints includes only EntryPoints that have the AsDefault option set to true.

# Ingress - namespace
Traefik - смотрипо дефолту ЗА ВСЕМИ Ingress во всех namespace
ОДНАКО есть правило: ingress.namespace===service.namespace

Если ingress.namespace!==service.namespace -> получаем ошибку в логах traefik:
ERR ingress/kubernetes.go:324 > Cannot create service | providerName=kubernetes ingress=my-nginx-http-default namespace=default serviceName=svc-my-nginx-3-2 servicePort=&ServiceBackendPort{Name:,Number:80,} error=service not found
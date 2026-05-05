# Установка пакетов - локально (offline / AirGap)
## Если надо првоести установку в условиях отсутствия интернета на серверах
## Все пакеты, нужно скачатьна туда, откуда есть доступ в интернет и откуда есть доступ к серверам
## Разместить эти пакеты в директории: `pkgs-sources/` (в корне этого проекта)
## В параметрах, `hosts-vars-override/XXXXX.yaml` - указать правильные пути для установки пакетов

# ------
# HAProxy из локального `.deb`
# ------

## Где взять `.deb`
- Сайт vbernat PPA: `https://launchpad.net/~vbernat/+archive/ubuntu/haproxy-3.3/+packages`
- Выбрать нужную версию haproxy: `https://launchpad.net/~vbernat/+archive/ubuntu/haproxy-3.2/+packages`
- Скачатьнужный пакет для архитектуры сервера и версии ubuntu (пример ниже для: Ubuntu 24.04, amd64)
- `wget https://launchpad.net/~vbernat/+archive/ubuntu/haproxy-3.2/+files/haproxy_3.2.17-1ppa1~noble_amd64.deb`

## Куда положить
- В директорию `pkgs-sources/` в корне репозитория
- Имя файла свободное (например `haproxy_3.3.0-1ppa1~jammy_amd64.deb`)

## Как переключить
В `hosts-vars-override/XXXXX.yaml` под `all.vars` (или в любом файле override) задать:
```yaml
all:
  vars:
    haproxy_apiserver_lb_install_method: "local_deb"
    haproxy_apiserver_lb_local_deb_path: "pkgs-sources/haproxy_3.2.17-1ppa1~noble_amd64.deb"
```

## Замечание
Этот режим закрывает только сам HAProxy-пакет. Зависимости (`libc6`, `libssl3` и т.п.) всё ещё резолвятся через стандартные Ubuntu apt-mirrors

# ------
# установка Helm из локального `tarball` (tar.gz)
# ------

## Где взять `tarball = tar.gz`
- Официальный сайт: `https://github.com/helm/helm/releases`
- Скачать `helm-vX.Y.Z-linux-amd64.tar.gz` под архитектуру сервера (`linux-amd64` для x86_64; `linux-arm64` для ARM)
- Прямой `wget https://get.helm.sh/helm-v3.20.2-linux-amd64.tar.gz`:

## Куда положить
- В директорию `pkgs-sources/` в корне репозитория
- Имя файла свободное (например `helm-v3.20.2-linux-amd64.tar.gz`)

## Как переключить
В `hosts-vars-override/hosts.yaml` под `all.vars` (или в любом файле override) задать:
```yaml
all:
  vars:
    helm_install_method: "local_tarball"
    helm_local_tarball_path: "pkgs-sources/helm-v3.20.2-linux-amd64.tar.gz"
```

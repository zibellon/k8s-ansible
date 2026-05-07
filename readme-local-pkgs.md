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

# ------
# containerd из локального `tarball` (tar.gz) + `containerd.service`
# ------

## Где взять `tarball` + `containerd.service`
- containerd binary: `https://github.com/containerd/containerd/releases`
- containerd.service: `https://raw.githubusercontent.com/containerd/containerd/main/containerd.service` (берётся из main-ветки, не привязан к версии)
- Скачать `containerd-X.Y.Z-linux-amd64.tar.gz` под архитектуру сервера (`linux-amd64` для x86_64; `linux-arm64` для ARM):
  - `wget https://github.com/containerd/containerd/releases/download/v2.2.2/containerd-2.2.2-linux-amd64.tar.gz`
  - `wget https://raw.githubusercontent.com/containerd/containerd/main/containerd.service`

## Куда положить
- В директорию `pkgs-sources/` в корне репозитория
- Имена файлов свободные (например `containerd-2.2.2-linux-amd64.tar.gz`, `containerd.service`)

## Как переключить
В `hosts-vars-override/XXXXX.yaml` под `all.vars` (или в любом файле override) задать:
```yaml
all:
  vars:
    containerd_install_method: "local_tarball"
    containerd_local_tarball_path: "pkgs-sources/containerd-2.2.2-linux-amd64.tar.gz"
    containerd_service_local_path: "pkgs-sources/containerd.service"
```

## Замечание
- `containerd_service_local_path` ОБЯЗАТЕЛЕН в `local_tarball` режиме — assert в `main-components.yaml` проверяет наличие обоих путей.
- `containerd.service` всегда переразвёртывается (как и в url-режиме) — без guard'а на pre-check, чтобы поддерживать обновление unit-файла.

# ------
# runc из локального бинарника (не tarball)
# ------

## Где взять
- Официальный репо: `https://github.com/opencontainers/runc/releases`
- runc распространяется как одиночный бинарник (не tarball), под архитектуру сервера (`runc.amd64` для x86_64; `runc.arm64` для ARM):
  - `wget https://github.com/opencontainers/runc/releases/download/v1.4.2/runc.amd64`

## Куда положить
- В директорию `pkgs-sources/` в корне репозитория
- Имя файла свободное (например `runc.amd64`)

## Как переключить
В `hosts-vars-override/XXXXX.yaml` под `all.vars` (или в любом файле override) задать:
```yaml
all:
  vars:
    runc_install_method: "local_file"
    runc_local_path: "pkgs-sources/runc.amd64"
```

## Замечание
- Метод называется `local_file` (не `local_tarball`), потому что runc — одиночный исполняемый файл, копируется напрямую в `/usr/local/sbin/runc` с mode `0755`.

# ------
# CNI plugins из локального `tarball` (tgz)
# ------

## Где взять `tarball = tgz`
- Официальный репо: `https://github.com/containernetworking/plugins/releases`
- Скачать `cni-plugins-linux-amd64-vX.Y.Z.tgz` под архитектуру сервера (`linux-amd64` для x86_64; `linux-arm64` для ARM):
  - `wget https://github.com/containernetworking/plugins/releases/download/v1.9.1/cni-plugins-linux-amd64-v1.9.1.tgz`

## Куда положить
- В директорию `pkgs-sources/` в корне репозитория
- Имя файла свободное (например `cni-plugins-linux-amd64-v1.9.1.tgz`)

## Как переключить
В `hosts-vars-override/XXXXX.yaml` под `all.vars` (или в любом файле override) задать:
```yaml
all:
  vars:
    cni_plugins_install_method: "local_tarball"
    cni_plugins_local_tarball_path: "pkgs-sources/cni-plugins-linux-amd64-v1.9.1.tgz"
```

# ------
# k9s из локального `.deb`
# ------

## Где взять `.deb`
- Официальный репо: `https://github.com/derailed/k9s/releases`
- Скачать `k9s_linux_amd64.deb` под архитектуру сервера (`linux_amd64` для x86_64; `linux_arm64` для ARM):
  - `wget https://github.com/derailed/k9s/releases/download/v0.50.18/k9s_linux_amd64.deb`

## Куда положить
- В директорию `pkgs-sources/` в корне репозитория
- Имя файла свободное (например `k9s_linux_amd64.deb`)

## Как переключить
В `hosts-vars-override/XXXXX.yaml` под `all.vars` (или в любом файле override) задать:
```yaml
all:
  vars:
    k9s_install_method: "local_deb"
    k9s_local_deb_path: "pkgs-sources/k9s_linux_amd64.deb"
```

## Замечание
- k9s ставится только на manager-нодах (sub-play `install-k9s.yaml` запускается с `hosts: managers`).

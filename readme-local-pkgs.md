# Установка пакетов - локально (offline / AirGap)
## Если надо провести установку в условиях отсутствия интернета на серверах
## Все пакеты, нужно скачатьна туда, откуда есть доступ в интернет и откуда есть доступ к серверам
## Разместить эти пакеты в директории: `pkgs-sources/` (в корне этого проекта)
## В параметрах, `hosts-vars-override/XXXXX.yaml` - указать правильные пути для установки пакетов

# ------
# containerd из локального `tarball` (tar.gz) + `containerd.service`
# ------

## Где взять `tarball` + `containerd.service`
- containerd binary
  - `wget https://github.com/containerd/containerd/releases/download/v2.2.2/containerd-2.2.2-linux-amd64.tar.gz`
  - `curl -LO https://github.com/containerd/containerd/releases/download/v2.2.2/containerd-2.2.2-linux-amd64.tar.gz`
- containerd.service (берётся из main-ветки, не привязан к версии)
  - `wget https://raw.githubusercontent.com/containerd/containerd/main/containerd.service`
  - `curl -LO https://raw.githubusercontent.com/containerd/containerd/main/containerd.service`

## Как переключить
В `hosts-vars-override/XXXXX.yaml` под `all.vars` (или в любом файле override) задать:
```yaml
all:
  vars:
    containerd_install_method: "local_tarball"
    containerd_local_tarball_path: "pkgs-sources/containerd-2.2.2-linux-amd64.tar.gz"
    containerd_service_local_path: "pkgs-sources/containerd.service"
```

# ------
# runc из локального бинарника (не tarball)
# ------

## Где взять
- runc одиночный бинарник (не tarball), под архитектуру сервера (`runc.amd64` для x86_64; `runc.arm64` для ARM):
  - `wget https://github.com/opencontainers/runc/releases/download/v1.4.2/runc.amd64`
  - `curl -LO https://github.com/opencontainers/runc/releases/download/v1.4.2/runc.amd64`

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
- под архитектуру сервера (`linux-amd64` для x86_64; `linux-arm64` для ARM):
  - `wget https://github.com/containernetworking/plugins/releases/download/v1.9.1/cni-plugins-linux-amd64-v1.9.1.tgz`
  - `curl -LO https://github.com/containernetworking/plugins/releases/download/v1.9.1/cni-plugins-linux-amd64-v1.9.1.tgz`

## Как переключить
В `hosts-vars-override/XXXXX.yaml` под `all.vars` (или в любом файле override) задать:
```yaml
all:
  vars:
    cni_plugins_install_method: "local_tarball"
    cni_plugins_local_tarball_path: "pkgs-sources/cni-plugins-linux-amd64-v1.9.1.tgz"
```

# ------
# установка Helm из локального `tarball` (tar.gz)
# ------

## Где взять `tarball = tar.gz`
- под архитектуру сервера (`linux-amd64` для x86_64; `linux-arm64` для ARM)
  - `wget https://get.helm.sh/helm-v3.20.2-linux-amd64.tar.gz`
  - `curl -LO https://get.helm.sh/helm-v3.20.2-linux-amd64.tar.gz`

## Как переключить
В `hosts-vars-override/hosts.yaml` под `all.vars` (или в любом файле override) задать:
```yaml
all:
  vars:
    helm_install_method: "local_tarball"
    helm_local_tarball_path: "pkgs-sources/helm-v3.20.2-linux-amd64.tar.gz"
```

# ------
# k9s из локального `.deb`
# ------

## Где взять `.deb`
- под архитектуру сервера (`linux_amd64` для x86_64; `linux_arm64` для ARM):
  - `wget https://github.com/derailed/k9s/releases/download/v0.50.18/k9s_linux_amd64.deb`
  - `curl -LO https://github.com/derailed/k9s/releases/download/v0.50.18/k9s_linux_amd64.deb`

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

# ------
# HAProxy из локального `.deb`
# ------

## Где взять `.deb`
- Скачать для архитектуры сервера и версии ubuntu (пример ниже для: Ubuntu 24.04, amd64)
  - `wget https://launchpad.net/~vbernat/+archive/ubuntu/haproxy-3.2/+files/haproxy_3.2.17-1ppa1~noble_amd64.deb`
  - `curl -LO https://launchpad.net/~vbernat/+archive/ubuntu/haproxy-3.2/+files/haproxy_3.2.17-1ppa1~noble_amd64.deb`

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
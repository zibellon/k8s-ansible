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
# Kubernetes packages из локальных `.deb`
# ------

## Где взять `.deb`
- Официальный репозиторий: `https://pkgs.k8s.io/core:/stable:/v<X.Y>/deb/` (где `<X.Y>` — major.minor версия, например `1.35`)
- Можно браузером, либо `apt-get download <pkg>` с подключённой к интернету Ubuntu-машины (после `apt-add` репозитория pkgs.k8s.io)
- Нужны 5 файлов под архитектуру сервера и Ubuntu-релиз:
  - `kubernetes-cni_<version>_<arch>.deb` (transitive dep — kubelet требует CNI plugins .deb)
  - `cri-tools_<version>_<arch>.deb` (transitive dep — kubelet требует CRI tools)
  - `kubelet_<version>_<arch>.deb`
  - `kubectl_<version>_<arch>.deb`
  - `kubeadm_<version>_<arch>.deb`

## Куда положить
- В директорию `pkgs-sources/` в корне репозитория
- Имена файлов свободные

## Как переключить
В `hosts-vars-override/XXXXX.yaml` под `all.vars` задать:
```yaml
all:
  vars:
    k8s_install_method: "local_deb"
    k8s_kubernetes_cni_local_deb_path: "pkgs-sources/kubernetes-cni_<ver>_amd64.deb"
    k8s_cri_tools_local_deb_path: "pkgs-sources/cri-tools_<ver>_amd64.deb"
    k8s_local_deb_path_list:
      - "pkgs-sources/kubelet_<ver>_amd64.deb"
      - "pkgs-sources/kubectl_<ver>_amd64.deb"
      - "pkgs-sources/kubeadm_<ver>_amd64.deb"
```

## Замечание
- `k8s_local_deb_path_list` ДОЛЖЕН быть позиционно парным к `k8s_package_list` — одинаковая длина И одинаковый порядок (`kubelet → kubectl → kubeadm`). Assert в `main-components.yaml` проверит длину; порядок — на ответственности оператора.
- `kubernetes-cni` и `cri-tools` устанавливаются ПЕРЕД списком (kubelet.deb имеет на них `Depends`).
- Транзитивные deps Ubuntu-уровня (`conntrack`, `ethtool`, `socat`, `iptables` и т.п.) всё ещё резолвятся через стандартные Ubuntu apt-mirrors.

# ------
# Longhorn packages из локальных `.deb`
# ------

## Где взять `.deb`
- Стандартные Ubuntu archives: либо `apt-get download <pkg>` с подключённой Ubuntu-машины, либо `https://packages.ubuntu.com`
- Нужны 4 файла под архитектуру сервера и Ubuntu-релиз:
  - `open-iscsi_<version>_<arch>.deb`
  - `nfs-common_<version>_<arch>.deb`
  - `cryptsetup_<version>_<arch>.deb`
  - `dmsetup_<version>_<arch>.deb`
  - `libcryptsetup12_<version>_<arch>.deb`

## Ссылки на файлы
- curl -L -O "http://de.archive.ubuntu.com/ubuntu/pool/main/o/open-iscsi/open-iscsi_2.1.9-3ubuntu4_amd64.deb"
- curl -L -O "http://de.archive.ubuntu.com/ubuntu/pool/main/n/nfs-utils/nfs-common_2.6.4-3ubuntu5_amd64.deb"
- curl -L -O "http://de.archive.ubuntu.com/ubuntu/pool/main/c/cryptsetup/cryptsetup_2.7.0-1ubuntu4_amd64.deb"
- curl -L -O "http://de.archive.ubuntu.com/ubuntu/pool/main/l/lvm2/dmsetup_1.02.185-3ubuntu3_amd64.deb"
- curl -L -O "http://de.archive.ubuntu.com/ubuntu/pool/main/c/cryptsetup/libcryptsetup12_2.7.0-1ubuntu4_amd64.deb"

## Куда положить
- В директорию `pkgs-sources/` в корне репозитория
- Имена файлов свободные

## Как переключить
В `hosts-vars-override/XXXXX.yaml` под `all.vars` задать:
```yaml
all:
  vars:
    longhorn_install_method: "local_deb"
    longhorn_local_deb_path_list:
      - "pkgs-sources/open-iscsi_<ver>_amd64.deb"
      - "pkgs-sources/nfs-common_<ver>_amd64.deb"
      - "pkgs-sources/cryptsetup_<ver>_amd64.deb"
      - "pkgs-sources/dmsetup_<ver>_amd64.deb"
      - "pkgs-sources/libcryptsetup12_<ver>_amd64.deb"
```

## Замечание
- `longhorn_local_deb_path_list` ДОЛЖЕН быть позиционно парным к `longhorn_packages` — одинаковая длина И одинаковый порядок. Assert в `longhorn-prepare.yaml` проверит длину; порядок — на ответственности оператора.

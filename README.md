# k8s-ansible

Автоматизация production-кластера Kubernetes на bare-metal. Репозиторий состоит из двух половин:

- **`playbook-system/`** — императивные операции уровня узла: `kubeadm`, ETCD с шифрованием, HAProxy-балансировщик apiserver'а как systemd-юнит, join / drain / remove ноды.
- **`playbook-app/`** — декларативная установка приложений уровня кластера через Helm. Чарт бывает двух видов:
  - **upstream** — официальный чарт продукта (cilium, cert-manager, traefik, haproxy, vault, gitlab, zitadel, seaweedfs, linstor, kargo, portainer, teleport и другие — таких большинство);
  - **локальный** — вендоренный апстрим-манифест или собственный чарт из `playbook-app/charts/` (argocd, argo-events, argo-rollouts, mon-system, filestash, outline, cluster-base).

Основной принцип установки компонента — **три фазы**, каждая отдельным helm-релизом и перезапускаемая своим `--tags`:

| Фаза | Что ставит | Чей чарт |
|---|---|---|
| `pre` | NetworkPolicy, ESO (`ServiceAccount` + `SecretStore` + `ExternalSecret`), `Issuer` cert-manager'а | всегда локальный |
| `install` | сам компонент | upstream **или** локальный |
| `post` | Ingress, `Certificate`, `ServiceMonitor` и прочее «сверху» | всегда локальный |

Но это именно базовая схема — у части компонентов стадий больше:

| Стадии | У кого | Зачем |
|---|---|---|
| `crds` | argocd, mon-system, argo-events, argo-rollouts | CRD ставятся отдельно от релиза: внутри него `helm uninstall` снёс бы каскадом все CR |
| `postgresql` | gitlab, zitadel, seaweedfs, outline | своя БД компонента, отдельным `StatefulSet` |
| `redis` | gitlab, outline | свой кэш, отдельным `StatefulSet` |
| `cronjob` | outline | планировщик не входит в дефолтный набор `SERVICES` — задачи дёргаются снаружи |
| `operator`, `vault-cr`, `unseal-keys` | vault | оператор bank-vaults, затем сам CR Vault, затем раздача unseal-ключей |
| `install-operator`, `install-cluster` | linstor | два разных чарта: Piraeus-оператор и кластер LINSTOR |
| `pre-cfg` | argo-events | cr-namespace и права контроллера в нём — обязаны появиться **до** `install` |
| `cfg` | argocd, kargo, argo-events | конфигурация после старта контроллера: права, `Project`, `AppProject`, `Application`, CR |
| `policy-sync`, `user-sync`, `identity-distribute`, `bucket-sync` | seaweedfs | декларативная синхронизация IAM и бакетов в живой filer |
| `accounts-sync`, `accounts-distribute` | argocd | локальные аккаунты и раздача их кред по путям Vault |
| `config-root` | gitlab | реконсиляция root-пароля через Vault |
| `configure` | teleport | роли и пользователи Teleport через CRD |
| по стадии на каждый workload | mon-system | `prometheus-operator`, `prometheus`, `alertmanager`, `node-exporter`, `ksm`, `loki`, `vector`, `grafana`, `grafana-postgresql` |
| `namespaces`, `rbac` — **вместо** трёх фаз | cluster-base | у компонента нет workload, ESO и ingress: три стандартные фазы были бы пустыми |

Полный список тегов каждого компонента — в его разделе ниже.

## Содержание

- [1. Как и откуда запускать](#1-как-и-откуда-запускать)
- [2. Конфигурация](#2-конфигурация)
- [3. Pre-check и AirGap](#3-pre-check-и-airgap)
- [4. INIT — первичная установка](#4-init--первичная-установка)
- [5. JOIN — добавление нод](#5-join--добавление-нод)
- [6. bastion-proxy](#6-bastion-proxy)
- [7. Компоненты](#7-компоненты) — [Prometheus-operator CRD](#prometheus-operator-crd) · [Cilium](#cilium) · [metrics-server](#metrics-server) · [cert-manager](#cert-manager) · [External Secrets](#external-secrets) · [Stakater Reloader](#stakater-reloader) · [Traefik](#traefik) · [HAProxy](#haproxy) · [Cilium — фазы `pre` и `post`](#cilium--фазы-pre-и-post) · [LINSTOR](#linstor) · [Vault](#vault) · [ZITADEL](#zitadel) · [SeaweedFS](#seaweedfs) · [FileStash](#filestash) · [Teleport](#teleport) · [GitLab](#gitlab) · [GitLab Runner](#gitlab-runner) · [Outline](#outline) · [Portainer](#portainer) · [Argo-тройка — порядок установки и права](#argo-тройка--порядок-установки-и-права) · [ArgoCD](#argocd) · [ArgoCD — стадия `cfg`](#argocd--стадия-cfg) · [Argo Rollouts](#argo-rollouts) · [Argo Events](#argo-events) · [Kargo](#kargo) · [mon-system](#mon-system) · [cluster-base](#cluster-base)

---

## 1. Как и откуда запускать

> ⚠️ **Все запуски делать из корневой директории проекта.**

В плейбуках очень много логики завязано на корневую директорию — ту, откуда был сделан запуск. Определяется она так:

```yaml
project_root: "{{ lookup('env', 'PWD') }}"
```

Поэтому запуск из любого другого места сломает пути к `charts` и `task-include`.

---

## 2. Конфигурация

### 2.1 Слои переменных

| Директория | Что лежит | Под контролем git |
|---|---|---|
| `hosts-vars/` | все доступные переменные и их значения по умолчанию | да |
| `hosts-vars-example/` | готовые примеры конфигов на каждый компонент | да |
| `hosts-vars-override/<cluster>/` | реальные домены, IP и секреты конкретного кластера | **нет** |

Менять переменные в `hosts-vars/` не рекомендуется — эта директория под контролем git. Чтобы переопределить переменную, создайте свою директорию (например `hosts-vars-override/<cluster>/`) и положите туда `<component>.yaml` с нужным значением.

Запуск идёт с **двумя** `inventory`: сначала берутся все базовые переменные, затем сверху накладываются переменные из override.

```bash
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/utils/node-info.yaml
```

### 2.2 Как ставится всё, что НЕ является официальным helm-чартом

Этот цикл проходит **каждый локальный чарт**: фазы `pre` и `post` у всех компонентов без исключения, плюс `install` у тех, чей чарт локальный. Все шаги выполняются на `master_manager_fact` — том manager'е, у которого в inventory стоит `is_master: true`.

**1. Копирование чарта на сервер — архивом.**

Локальная директория `playbook-app/charts/<c>/<phase>/` пакуется в `tar.gz`, архив копируется на master, распаковывается, после чего оба архива (локальный и удалённый) удаляются. Архив вместо пофайлового копирования — так заметно быстрее и надёжнее на больших чартах.

Куда именно: `{{ remote_charts_dir }}/<c>/<phase>/`, где `remote_charts_dir` = `/opt/helm-charts` (задаётся в [`hosts-vars/ansible.yaml`](hosts-vars/ansible.yaml)).

**2. Рендер values.**

Переменная `<c>_<phase>_helm_values` из inventory сериализуется в YAML и пишется рядом с чартом: `/opt/helm-charts/<c>/<phase>/values-override.yaml`.

**3. `helm template` в staging-директорию** — чтобы получить один финальный файл без шаблонизации.

```bash
helm template <release> /opt/helm-charts/<c>/<phase> \
  --values /opt/helm-charts/<c>/<phase>/values-override.yaml \
  --namespace <ns> \
  > /opt/helm-charts/<c>/<phase>-k-tmp/template-output.yaml
```

Туда же, в `-k-tmp/`, рендерится `kustomization.yaml`: список патчей из `<c>_<phase>_kustomize_patches` и — опционально — builtin-трансформер `namespace:`.

**4. `kubectl kustomize` — сборка нового самостоятельного чарта.**

На выходе получается директория `/opt/helm-charts/<c>/<phase>-k/`, и собирается она из трёх кусков:

| Что | Откуда берётся |
|---|---|
| `Chart.yaml` | копируется из исходного чарта: `/opt/helm-charts/<c>/<phase>/Chart.yaml` |
| `raw/all.yaml` | результат `kubectl kustomize /opt/helm-charts/<c>/<phase>-k-tmp` — то есть отпатченный манифест |
| `templates/loader.yaml` | генерируется на месте, одна строка: `{{ .Files.Get "raw/all.yaml" }}` |

> **Зачем loader, а не положить манифест сразу в `templates/`.** При установке helm заново прогоняет Go-шаблонизатор по всему, что лежит в `templates/`. Манифест лежит в `raw/`, а `.Files.Get` возвращает его содержимое **строкой** — второй проход движка её не трогает. Именно это сохраняет runtime-синтаксис `{{ }}` внутри `ConfigMap` и `Secret`: Vector `{{ level }}`, ESO `{{ .access_key }}`, алерты Prometheus `{{ $value }}`, Loki `{{ $labels.foo }}`.

**5. Установка через `helm upgrade --install`** — чтобы нормально управлять жизненным циклом компонента: видеть историю релиза, откатываться, сносить одной командой.

```bash
helm upgrade --install <c>-<phase> /opt/helm-charts/<c>/<phase>-k \
  --namespace <ns> \
  --create-namespace \
  --cleanup-on-fail \
  --atomic \
  --wait \
  --wait-for-jobs \
  --timeout <c>_<phase>_helm_timeout
```

Ставится именно директория `-k`, а не исходный чарт. Запуск асинхронный: helm-операции длинные, а ansible иначе висит на одном соединении.

**Что в итоге лежит на сервере:**

```text
/opt/helm-charts/<component>/
├── <phase>/                    # распакованный исходный чарт
│   ├── Chart.yaml
│   ├── values-override.yaml    # ← отрендерен из inventory (шаг 2)
│   └── templates/
├── <phase>-k-tmp/              # staging: вход для kustomize
│   ├── template-output.yaml    # ← результат helm template (шаг 3)
│   └── kustomization.yaml      # ← патчи
└── <phase>-k/                  # ← ИМЕННО ЭТО ставится helm-ом (шаг 5)
    ├── Chart.yaml
    ├── raw/
    │   └── all.yaml            # ← результат kubectl kustomize (шаг 4)
    └── templates/
        └── loader.yaml         # {{ .Files.Get "raw/all.yaml" }}
```

Директории между прогонами **не** очищаются: исходный чарт перед распаковкой удаляется целиком, а `-k-tmp/` и `-k/` перезаписываются пофайлово. Поэтому туда удобно заглянуть, чтобы посмотреть, что именно уехало в кластер на последнем прогоне.

### 2.3 Как модифицировать установку

**Через `extra_objects`.** При шаблонизации можно подсунуть массив `<component>_<phase>_extra_objects` — helm-чарт примет его при установке. Так добавляются новые объекты.

**Через kustomize.** Массив вида `<component>_<phase>_kustomize_patches` позволяет удалять или модифицировать уже существующие объекты.

---

## 3. Pre-check и AirGap

| Тема | Файл |
|---|---|
| Что сделать **перед** установкой | [`readme-pre-check.md`](readme-pre-check.md) |
| AirGap: на серверах нет доступа в интернет | [`readme-local-pkgs.md`](readme-local-pkgs.md) |

---

## 4. INIT — первичная установка

### 4.1 Инициализация ноды (установка компонентов)

Без `--limit` инициализация выполняется на всех нодах сразу; с `--limit` — только на указанной.

```bash
# все ноды сразу
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/full-node-install.yaml

# одна конкретная нода
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/full-node-install.yaml --limit k8s-manager-1
```

### 4.2 Инициализация кластера (в первый раз)

Это буквально команда `kubeadm init ...`. Флаг `--limit` указывать **обязательно**.

```bash
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/cluster-init.yaml --limit k8s-manager-1
```

---

## 5. JOIN — добавление нод

### 5.1 Worker

Перед запуском добавить нового worker'а в `hosts-vars-override/`.

- Если Cilium уже установлен — см. `Подготовка_2`.

```bash
# 1. инициализация ноды
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/full-node-install.yaml --limit k8s-worker-1

# 2. получение токена и вызов kubeadm join ...
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/utils/worker-join.yaml --limit k8s-worker-1
```

### 5.2 Manager

Перед запуском добавить нового manager'а в `hosts-vars-override/`.

- Если Cilium уже установлен — см. `Подготовка_2`.
- Обновить SANs для apiserver'а.
- Обновить `haproxy-apiserver-lb`.

```bash
# 1. обновить SANs в сертификатах apiserver'а (добавить туда IP нового manager'а)
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/utils/apiserver-sans-update.yaml
```

- Вызывать **без** `--limit`: конфиг нужно обновить на **всех** текущих managers.
- Обновит только текущие managers.
- Перезапуск apiserver'а производится последовательно, для каждого manager'а.

```bash
# 2. обновить конфиг haproxy-apiserver-lb на всех текущих нодах (manager + worker)
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/utils/haproxy-apiserver-lb-update.yaml
```

- Обновление идёт по одной ноде за раз, через `serial: 1`.
- То есть перезапуск последовательный — ради HA-доступности.

```bash
# 3. инициализация ноды (--limit обязателен: добавляем конкретную ноду)
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/full-node-install.yaml --limit k8s-manager-2

# 4. загрузка сертификатов в k8s.secrets, получение токена, kubeadm join ...
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/utils/manager-join.yaml --limit k8s-manager-2
```

---

## 6. bastion-proxy

**Задача.** В кластере установлены docker-registry и S3-хранилище, к ним нужно дать доступ по DNS, но не светить реальный IP кластера.

Это можно сделать через Cloudflare или подобный WAF, но там есть лимиты на размер файла: у Cloudflare даже на самом дорогом тарифе лимит 500 МБ, а контейнер может весить 2–10 ГБ. Не вариант.

**Решение.** Покупается отдельный сервер — назовём его `bastion-proxy-1`. С кластером он не связан никак. На нём ставится HAProxy и включается `proxy-protocol-v2`.

- Все запросы, принятые на портах `80`, `443` и `10000–12000`, проксируются на указанный IP, маскируя конечный адрес назначения.
- Все DNS-записи направляются на этот сервер в режиме серого облачка (DNS-only). Сертификаты выпускаем через cert-manager.
- Такие запросы принимает Traefik-ingress или HAProxy-ingress.

**Как снять `proxy-protocol-v2`** — и тут есть особенность:

| Ingress | Как | Поведение |
|---|---|---|
| Traefik | добавить в конфиг entrypoint `proxyProtocol.insecure=true` | принимает и proxy-protocol, и обычные прямые запросы |
| HAProxy | так нельзя | **или** proxy-protocol-v2, **или** прямые запросы — опции «принимать всё» нет |

**Теги:** `node-install` · `haproxy-install` · `haproxy-config` · `verify`

```bash
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-system/bastion-proxy-install.yaml
```

---
## 7. Компоненты

Компоненты ставятся в порядке разделов ниже — он же порядок зависимостей. Три «ступени» по ходу списка отмечают моменты, начиная с которых становятся доступны PVC, секреты и SSO.

---

### Prometheus-operator CRD

Для всех компонентов при установке создаются объекты `ServiceMonitor` и `PodMonitor`. Их создание можно отключить флагами в `hosts-vars/`.

> ⚠️ Если не создать CRD prometheus-operator'а, установки компонентов упадут с ошибкой.

```bash
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/mon-system-install.yaml --tags crds
```

---

### Cilium

**Чарт:** официальный upstream.

- Если изменились конфиги (`ConfigMap`), Cilium не подцепит их автоматически. В том, что генерируется при `helm template`, у Deployment/DaemonSet нет checksum на основе `ConfigMap` — значит, для применения новых конфигов нужен ручной restart.
- Ожидание готовности deployment/daemonset — через `kubectl rollout status ...`.
- Есть ожидание готовности CRD. Если добавляются новые CRD, их ожидание надо добавить в [`playbook-app/cilium-install.yaml`](playbook-app/cilium-install.yaml).

**Важно 1.** Установка изначально производится только с тегом `--tags install`. Стадии `pre` и `post` ставятся позже — после cert-manager, ESO, Traefik и HAProxy.

**Теги:** `pre` · `install` · `post`

```bash
# установка
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/cilium-install.yaml --tags install

# обновление (версия, конфиг)
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/cilium-install.yaml
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/cilium-restart.yaml
```

---

### metrics-server

**Чарт:** официальный upstream.

Ожидание готовности deployment/daemonset — через `kubectl rollout status ...`.

**Теги:** `pre` · `install`

```bash
# установка + обновление (версия, конфиг)
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/metrics-server-install.yaml
```

---

### cert-manager

**Чарт:** официальный upstream.

- Ожидание готовности deployment/daemonset — через `kubectl rollout status ...`.
- Есть ожидание готовности CRD. Если добавляются новые CRD, их ожидание надо добавить в [`playbook-app/cert-manager-install.yaml`](playbook-app/cert-manager-install.yaml).

**Важно 1.** Сейчас через этот ansible можно настроить только `ClusterIssuer` (переменная `cert_manager_cluster_issuers`).

**Теги:** `pre` · `install` · `post`

```bash
# установка + обновление (версия, конфиг)
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/cert-manager-install.yaml

# отдельный playbook для перезапуска
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/cert-manager-restart.yaml
```

---

### External Secrets

**Чарт:** официальный upstream.

- Ожидание готовности deployment/daemonset — через `kubectl rollout status ...`.
- Есть ожидание готовности CRD. Если добавляются новые CRD, их ожидание надо добавить в [`playbook-app/external-secrets-install.yaml`](playbook-app/external-secrets-install.yaml).

**Теги:** `pre` · `install`

```bash
# установка + обновление (версия, конфиг)
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/external-secrets-install.yaml

# отдельный playbook для перезапуска
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/external-secrets-restart.yaml
```

---

### Stakater Reloader

**Чарт:** официальный upstream.

Ожидание готовности deployment/daemonset — через `kubectl rollout status ...`.

**Теги:** `pre` · `install`

```bash
# установка + обновление (версия, конфиг)
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/stakater-reloader-install.yaml

# отдельный playbook для перезапуска
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/stakater-reloader-restart.yaml
```

---

### Traefik

**Чарт:** официальный upstream. Первый из двух ingress-контроллеров.

- Параметры (конфиг) для работы передаются в CLI — как аргументы при запуске.
- Есть dashboard, доступный по URL.
- Ожидание готовности deployment/daemonset — через `kubectl rollout status ...`.
- Есть ожидание готовности CRD. Если добавляются новые CRD, их ожидание надо добавить в [`playbook-app/traefik-install.yaml`](playbook-app/traefik-install.yaml).
- Есть работа с ESO.

**Теги:** `pre` · `install` · `post`

```bash
# установка + обновление (версия, конфиг)
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/traefik-install.yaml

# отдельный playbook для перезапуска
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/traefik-restart.yaml
```

---

### HAProxy

**Чарт:** официальный upstream. Второй из двух ingress-контроллеров.

- Автоматически подхватывает конфиг, который генерируется через CRD.
- Ожидание готовности deployment/daemonset — через `kubectl rollout status ...`.
- Есть ожидание готовности CRD. Если добавляются новые CRD, их ожидание надо добавить в [`playbook-app/haproxy-install.yaml`](playbook-app/haproxy-install.yaml).
- Есть работа с ESO.

**Теги:** `pre` · `install` · `post`

```bash
# установка + обновление (версия, конфиг)
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/haproxy-install.yaml

# отдельный playbook для перезапуска
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/haproxy-restart.yaml
```

---

### Cilium — фазы `pre` и `post`

**Чарт:** локальный (`playbook-app/charts/`).

- Есть Hubble UI, доступный по URL.
- Это просто дополнительная конфигурация: никаких контейнеров тут не запускается.
- Устанавливается: `NetworkPolicy`, `NetworkPolicy` для `kube-system`, `CiliumClusterwideNetworkPolicy`.

```bash
# установка + обновление (версия, конфиг)
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/cilium-install.yaml --tags pre,post
```

---

### LINSTOR

**Чарт:** официальный upstream. Два чарта: Piraeus-оператор и кластер LINSTOR.

Два helm-чарта: оператор и кластер.

- Автоматически подхватывает конфиг, который генерируется через CRD.
- Ожидание готовности deployment/daemonset — через `kubectl rollout status ...`.
- Есть ожидание готовности CRD. Если добавляются новые CRD, их ожидание надо добавить в [`playbook-app/linstor-install.yaml`](playbook-app/linstor-install.yaml).

**Важно 1.** Может работать в абсолютно разных условиях:

| Условия | Что используется |
|---|---|
| VPS/VDS + 1 диск с ОС | sparse-file (`fileThinPool` / `filePool`) |
| VPS/VDS + 1 диск с ОС + N дисков RAW | sparse-file (`fileThinPool` / `filePool`) + `lvmThinPool` / `lvmPool` для RAW-устройств |
| BareMetal + N дисков RAW | `lvmThinPool` / `lvmPool` |

**Теги:** `pre` · `install-operator` · `install-cluster` · `post`

```bash
# установка + обновление (версия, конфиг)
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/linstor-install.yaml
```

**Обновление NetworkPolicy и мониторинга.** Есть официальный набор: <https://github.com/piraeusdatastore/piraeus-operator/tree/v2/config/extras/monitoring>. Это версия для kustomize, не для helm.

- NetworkPolicy оттуда **не используется** — написана своя версия.
- Для мониторинга нужны три файла `*-monitor`: скачать их и адаптировать под helm (буквально несколько строк).

---

> 🏁 **Ступень 1.** Теперь можно запускать всё, что требует volume (PVC).

---
---

### Vault

**Чарт:** официальный upstream. Ставится оператором bank-vaults.

> ⚠️ **Есть проблема:** из РФ не отдаётся **официальный чарт HashiCorp — `vault-helm`** (региональная блокировка на стороне HashiCorp). К bank-vaults это отношения не имеет: его чарт скачивается нормально.
> **Решение:** зайти на GitHub в раздел релизов <https://github.com/hashicorp/vault-helm>, скачать ZIP последнего релиза и достать оттуда все `templates`, `Chart.yaml` и `values.yaml`.

- Есть web-UI, доступный по URL.
- Есть volume — требуется работа с dynamic PVC.
- Есть ожидание готовности deployment/daemonset.

**Важно 1. Установка идёт через helm-чарт bank-vaults** (<https://bank-vaults.dev>, <https://github.com/bank-vaults>). Для хранения ключей используется `k8s-secret`. Есть playbook для доставки ключей из k8s Secret на manager-ноды в виде json-файла.

**Важно 2. Работа с конфигурацией идёт через Operator + CRD.** Все политики, роли, методы авторизации и прочее определяются в Vault CRD. Для их синхронизации с инстансом Vault надо вызвать:

```bash
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/vault-install.yaml --tags vault-cr
```

**Важно 3. Root-token нужен ровно один раз — чтобы создать админа.**

Есть политика `vault-admin` ([`hosts-vars/vault.yaml`](hosts-vars/vault.yaml)) — она даёт все права на всё. Порядок:

1. После установки Vault зайти в UI или CLI под root-token.
2. Открыть Auth Method → UserPass (он уже активирован).
3. Добавить нового пользователя, например `admin-rw`, задать пароль и указать политику `vault-admin`.
4. Выйти из root-token и авторизоваться под новым пользователем `admin-rw`.

**Важно 4. Добавление пользователей через userpass.**

Типичный сценарий и типичная ошибка:

1. Зашли в UI под root-token.
2. Добавили нового пользователя и задали ему пароль (без этого пользователя не создать).
3. Передали логин и пароль сотруднику, попросили сменить пароль.
4. **Ошибка: недостаточно прав.**

Так происходит потому, что при создании пользователю крепится политика `default`, а в ней нет разрешения на сброс пароля. Нужно такое правило:

```hcl
path "auth/userpass/users/{{identity.entity.aliases.<ACCESSOR>.name}}/password" {
  capabilities = ["update"]
}
```

**Что такое ACCESSOR?** Когда создаётся (включается) метод `userpass`, он получает уникальный ID — это и есть ACCESSOR. Правило, которое надо помнить: **ACCESSOR стабилен, пока `userpass` не пересоздан либо не выключен и включён заново.**

Узнать его можно двумя способами:

- В UI: Access → userpass → Configure. Там будет поле вида `auth_userpass_1q2w3e4r` — это ACCESSOR.
- В CLI: `vault read -field=accessor sys/auth/userpass`

Пример готовой политики — `user-self-service` в [`hosts-vars/vault.yaml`](hosts-vars/vault.yaml).

**Важно 5. Как работать с токеном в vault-cli.**

Авторизация — `vault login`, дальше он попросит ввести токен. После успешной авторизации можно выполнять команды в соответствии с правами токена.

Команды `vault logout` **не существует**. Чтобы выйти:

```bash
rm -f ~/.vault-token
vault token lookup   # проверить, что токена больше нет
```

**Важно 6. Как настроить OIDC.**

Основная проблема — `clientSecret`. Чтобы он появился в Kubernetes (как k8s Secret), нужен рабочий Vault и работающая интеграция с ESO. А если включить в Vault OIDC и указать неверный секрет — Vault не запустится. Отсюда порядок:

1. Запустить Vault полностью **без** OIDC (`vault_oidc_enabled: false`).
2. Зайти в Vault под root-token и положить туда нужные `clientSecret` и `clientId`.
3. Переопределить `vault_oidc_enabled: true`.
4. Перезапустить установку: `ansible-playbook ... --tags pre,vault-cr`

Добавится ExternalSecret для `oidc.clientSecret`, конфиги для vault-operator, и сам Vault перезапустится.

**Теги:** `pre` · `operator` · `vault-cr` · `unseal-keys` · `post`

```bash
# установка (обновление) + конфигурация + синхронизация политик = ОДИН playbook
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/vault-install.yaml
```

**Обновление.** Всё ставится через официальный helm-чарт, но RBAC почему-то решили ставить отдельно — почему, загадка. Порядок:

```bash
kubectl kustomize https://github.com/bank-vaults/vault-operator/deploy/rbac > vault-rbac-official.yaml
```

Дальше поправить содержимое под helm и перенести в `playbook-app/charts/vault/pre`.

---

> 🏁 **Ступень 2.** Теперь можно запускать всё, что требует секретов.

В `hosts-vars/` и `hosts-vars-override/` есть отдельная структура для управления Vault: какие политики, роли, аккаунты и пути для секретов. Пример — [`readme-vault.md`](readme-vault.md).
>
> Порядок при добавлении чего-то в Vault:
>
> 1. Добавить в `hosts-vars-override/` новые данные (policy + role).
> 2. Вызвать синхронизацию: `ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/vault-install.yaml --tags vault-cr`
> 3. Отдельно (через ArgoCD или иначе) загрузить в Kubernetes: `Namespace`, `ServiceAccount`, `SecretStore` (CRD), `ExternalSecret` (CRD).
>
> ⚠️ Синхронизация приводит структуру в Vault к описанной **полностью**: добавляет, обновляет и удаляет.

---

### ZITADEL

**Чарт:** официальный upstream.

Параметры — в `hosts-vars/` и `hosts-vars-override/`.

**Важно 1.** Если в момент установки не указать логин и пароль, данные для входа по умолчанию будут такими:

- Логин: `zitadel-admin@<ORG_NAME>.<zitadel_domain>`
- Пароль: `Password1!`

**Важно 2. Пароль первого `instance-admin` задаётся СТРОГО ОДИН РАЗ — в момент установки.**

Он кладётся в Vault, срабатывает ESO, и только потом запускается сам ZITADEL. Никаких автоматических механизмов смены этого пароля в k8s-ansible не предусмотрено.

Порядок: зайти под `instance-admin`, сменить пароль, и чтобы его не забыть — зайти в Vault и положить новый пароль туда.

> ⚠️ Здесь **нет** механики, как у GitLab: ротировать пароль повторным запуском k8s-ansible нельзя.

**Теги:** `pre` · `postgresql` · `install` · `post`

```bash
# установка + обновление (версия, конфиг)
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/zitadel-install.yaml

# отдельный playbook для перезапуска
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/zitadel-restart.yaml
```

---

> 🏁 **Ступень 3.** Теперь можно настраивать OIDC (SSO) для компонентов.

Общий принцип:
>
> 1. Зайти в ZITADEL, создать организацию, создать в ней проекты и всё настроить.
> 2. Получить `clientId`, `clientSecret` и что там ещё нужно — и сохранить это в Vault.
> 3. Перед запуском нового компонента, умеющего OIDC, подготовить конфиг.
> 4. Достать `organization_id`, создать пользователя в ZITADEL и добавить его в проект.
> 5. Запустить компонент.

---
---

### SeaweedFS

**Чарт:** официальный upstream. S3-хранилище кластера.

В `hosts-vars/` и `hosts-vars-override/` есть отдельная структура для управления SeaweedFS S3 API.

- Есть web-UI (очень странный и неинтересный), доступный по URL.
- Есть S3 API, доступный по URL.
- Есть volume — требуется работа с dynamic PVC.
- Есть ожидание готовности deployment/daemonset.

**Важно 1. Отдельная работа с policy, user, bucket и identity-distribute.** Всё описывается и синхронизируется полностью декларативно.

Переменные: `seaweedfs_managed_policies_extra`, `seaweedfs_identities_extra`, `seaweedfs_sync_buckets_extra`.

```bash
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/seaweedfs-install.yaml \
  --tags policy-sync,user-sync,bucket-sync,identity-distribute
```

Результат: политики созданы, credentials созданы в S3 API и доставлены в указанные пути Vault, бакеты созданы.

**Теги:** `pre` · `postgresql` · `install` · `policy-sync` · `user-sync` · `identity-distribute` · `bucket-sync` · `post`

```bash
# установка + обновление (версия, конфиг)
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/seaweedfs-install.yaml

# отдельный playbook для перезапуска
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/seaweedfs-restart.yaml
```

---

### FileStash

**Чарт:** локальный (`playbook-app/charts/`). Веб-клиент к S3.

- Есть web-UI: админский на `/admin` и клиентский в корне. Доступен по URL.
- Есть volume — требуется работа с dynamic PVC.
- Есть ожидание готовности `StatefulSet`.

**Важно 1. Установка НЕ декларативная.** Декларативно (через ENV) задаются только две вещи:

| Переменная | Источник |
|---|---|
| `ADMIN_PASSWORD` | ESO-секрет (bcrypt-хэш) |
| `APPLICATION_URL` | значение из `hosts-vars` |

Эти env FileStash сам пишет в `config.json` (`auth.admin` и `general.host`) — **на каждом старте**. То есть: поменяли пароль админа в Vault → сработал ESO → перезапустили контейнер → новый пароль.

Все остальные настройки (подключения, плагины и так далее) делаются в UI. Сам `config.json` лежит в PVC и мутируется приложением — при старте и при любых изменениях в admin-UI.

**Теги:** `pre` · `install` · `post`

```bash
# установка + обновление (версия, конфиг)
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/filestash-install.yaml
```

---

### Teleport

**Чарт:** официальный upstream.

Что устанавливается: proxy + auth + operator.

**Важно 1. Все ресурсы Teleport управляются через CRD.** Чтобы добавить новую роль или пользователя, добавляем значения в `hosts-vars-override/` и вызываем:

```bash
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/teleport-install.yaml --tags configure
```

Оператор работает только в одном направлении: **CRD → Teleport**. Если что-то добавить в Teleport UI, в CRD оно не появится. Если через UI обновить то, что есть в CRD, — через минуту оно вернётся к состоянию CRD. То есть через UI мы только смотрим и ничего не создаём.

**Важно 2. После установки** надо получить ссылку на сброс и установку пароля для пользователя `superadmin`:

```bash
kubectl exec -n teleport deploy/teleport-auth -- tctl users reset superadmin
```

Перейти по ссылке и установить пароль через UI.

**Важно 3. Как проверить, что оператор всё синхронизировал:**

```bash
kubectl exec -n teleport deploy/teleport-auth -- tctl get role/superadmin
kubectl exec -n teleport deploy/teleport-auth -- tctl users ls
```

**Важно 4.** Для авторизации через консоль (например, для `kubectl`) работает только MFA=OTP. Через PassKey не получилось.

**Важно 5. Оператор может отвалиться по приколу.** У него сертификат на 1 час, и если он его не обновит — отвалится. Почему это не прописано в healthcheck — загадка.

Что искать в логах (operator + auth):

```text
current time 2026-04-12T09:32:38Z is after 2026-04-12T08:36:50Z
write tcp 10.64.15.71:49648->10.132.251.113:3025: write: broken pipe
tls: expired certificate
```

Чинится простым перезапуском:

```bash
kubectl rollout restart deployment/teleport-operator -n teleport
```

**Теги:** `pre` · `install` · `post` · `configure`

```bash
# установка: auth, proxy, operator
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/teleport-install.yaml

# агент на КАЖДУЮ ноду — для доступа к ноде по SSH
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/teleport-ssh-agent-install.yaml

# отдельный playbook для перезапуска
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/teleport-restart.yaml
```

---

### GitLab

**Чарт:** официальный upstream.

- Есть UI и API, доступные по URL.
- Ожидание готовности deployment/daemonset — через `kubectl rollout status ...`.
- Есть работа с ESO.

**Важно 1. Про компоненты.**

- `gitlab-exporter` — количество реплик захардкожено в шаблоне самого официального helm-чарта (`1`).
- `gitaly` — StatefulSet, количество реплик определяется через `global.gitaly.internal.names`, по умолчанию `1`. RollingUpdate + StatefulSet означает «убить, а потом создать». Если реплик больше одной, начинается возня с Praefect (репликация git-данных между узлами).

**Важно 2. Про пароль root-пользователя.**

1. Сразу после установки GitLab создаётся k8s Secret `gitlab-gitlab-initial-root-password` — его создаёт сам helm-чарт.
2. Этот секрет сразу же удаляется. Получить доступ к GitLab на этот момент нельзя никак.
3. Запускается тег `config-root`. Он проверяет, есть ли пароль для root в Vault.
4. Если в Vault пароля нет или его `passwordMtime` не совпадает с `gitlab_root_creds.passwordMtime` из inventory — генерируется новый пароль.
5. Затем пароль проверяется в GitLab: подходит или нет.
6. Если не подходит — пароль в GitLab обновляется на этот.

> ⚠️ Если root-пользователь поменяет пароль в GitLab UI, при следующем прогоне ansible пароль сбросится на тот, что лежит в Vault.
> **Правило: пароль root-пользователя управляется полностью через ansible.**

**Хранилище объектов.** Локальный MinIO-сабчарт **не используется** — вместо него S3 (SeaweedFS): бакеты и креды заводятся в `seaweedfs-sync`.

**Теги:** `pre` · `postgresql` · `redis` · `install` · `post` · `config-root`

```bash
# установка + обновление (версия, конфиг)
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/gitlab-install.yaml
```

```bash
# отдельно — реконсиляция root-пароля через Vault
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/gitlab-install.yaml --tags config-root
```

---

### GitLab Runner

**Чарт:** официальный upstream.

- Ожидание готовности deployment/daemonset — через `kubectl rollout status ...`.
- Есть работа с ESO.

**Важно 1.** Здесь производится именно установка helm-чарта, без конфигурации в самом инстансе GitLab. Регистрация раннера в GitLab делается вручную. Порядок:

1. Зайти в GitLab, создать `instance-runner` и получить его токен.
2. Сохранить токен в Vault по правильному пути (указан в ESO) в поле `token`.
3. Поправить конфиг в `hosts-vars/` и `hosts-vars-override/` — там полный toml-файл.
4. Установить gitlab-runner через helm.

**Важно 2.** S3-кэш раннера живёт в SeaweedFS: identity `gitlab-runner` и бакет `gitlab-runner-cache` заводятся в `seaweedfs-sync`, креды приезжают через ESO.

**Теги:** `pre` · `install`

```bash
# установка + обновление (версия, конфиг)
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/gitlab-runner-install.yaml
```

---

### Outline

**Чарт:** локальный (`playbook-app/charts/`). Wiki.

Wiki. Компонент opt-in: `outline_enabled: true`.

- Есть web-UI, доступный по URL.
- Два sidecar-хранилища собственной сборки: PostgreSQL и Redis, каждый — `StatefulSet` + headless Service + PVC.
- Есть работа с ESO.
- Пять фаз плюс отдельная фаза `cronjob`.

**Важно 1. Вход ТОЛЬКО через OIDC — локального логина у Outline нет.**

Bootstrap идёт OIDC-first: **первый вошедший becomes админом**. Сразу после первого входа стоит привязать passkey — это единственный break-glass, если IdP станет недоступен.

`clientId` и `clientSecret` в inventory не кладутся, они живут в Vault:

```bash
vault kv put eso-secret/outline/oidc clientId=<id> clientSecret=<secret>
```

> ⚠️ Установка **падает на fail-fast**, если `outline_oidc_enabled: true`, а кред в Vault нет. Это сделано намеренно: без них под поднимется, но войти будет некому.

**Важно 2. `SECRET_KEY` генерируется ОДИН РАЗ и НЕ РОТИРУЕТСЯ НИКОГДА.**

Это ключ шифрования данных at-rest. Ротация превращает установку в кирпич — расшифровать уже записанное будет нечем. `UTILS_SECRET` и пароли PostgreSQL/Redis тоже генерятся по схеме seed-if-missing (алфавитно-цифровые, URL-safe), но их менять можно.

**Важно 3. Вложения браузер грузит НАПРЯМУЮ в S3.**

Server-side proxy у Outline нет, поэтому `outline_s3_upload_bucket_url` обязан быть **публично доступным** хостом, а не in-cluster Service. Бакет и identity заводятся заранее в SeaweedFS:

```bash
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/seaweedfs-install.yaml \
  --tags user-sync,identity-distribute,bucket-sync
```

Креды приезжают в Vault по пути `eso-secret/outline/s3-storage`. Установка тоже **падает на fail-fast**, если их там нет.

**Важно 4. Периодические задачи — отдельная фаза `cronjob`.**

У Outline планировщик не входит в дефолтный набор `SERVICES`, поэтому крон-задачи дёргаются снаружи: `CronJob` ходит в `/api/cron.<period>?token=${UTILS_SECRET}` на in-cluster Service. Расписания задаются переменными inventory:

| Переменная | По умолчанию |
|---|---|
| `outline_cron_hourly_schedule` | `5 * * * *` |
| `outline_cron_daily_schedule` | `15 3 * * *` |

**Важно 5. Реплика по умолчанию одна** (`outline_replica_count: 1`). Увеличивать бездумно нельзя: совместное редактирование в Outline держит состояние в памяти процесса, и для нескольких реплик его надо выносить в Redis отдельной настройкой.

**Теги:** `pre` · `postgresql` · `redis` · `install` · `post` · `cronjob`

**Предусловия:** Vault установлен и распечатан, политика и роль `outline.eso-main` синхронизированы; установлен ESO и storage; заведён S3-бакет с identity; при OIDC — приложение в ZITADEL и креды в Vault.

```bash
# установка + обновление (версия, конфиг)
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/outline-install.yaml

# отдельный playbook для перезапуска (три стадии: postgresql, redis, приложение)
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/outline-restart.yaml
```

---

### Portainer

**Чарт:** официальный upstream.

- Есть web-UI, доступный по URL.
- Есть volume — требуется работа с dynamic PVC: там лежит вся БД Portainer (окружения, пользователи, стеки, настройки).
- Ожидание готовности deployment/daemonset — через `kubectl rollout status ...`.
- Есть работа с ESO.

**Важно 1. Как создаётся пароль первого админа** (логин `admin`):

1. Playbook смотрит в Vault: `eso-secret/portainer/admin-creds`, поле `password`.
2. Если пароля там нет — генерирует новый (32 символа) и кладёт в Vault. Если есть — **не трогает**.
3. Срабатывает ESO, появляется k8s Secret `eso-portainer-admin-creds`.
4. Helm-чарт монтирует этот секрет в под файлом и передаёт контейнеру `--admin-password-file=/run/portainer/admin-password`.
5. Portainer читает файл, сам хэширует значение и создаёт пользователя `admin`.

В Vault пароль лежит **открытым текстом** — это единственный способ его узнать:

```bash
vault kv get eso-secret/portainer/admin-creds
```

Bcrypt на control-node не нужен (в отличие от FileStash и Kargo) — хэширование делает сам Portainer. Побочный эффект: раз админ создан при старте, Portainer не требует setup-token, ручная инициализация не нужна.

**Важно 2. РОТАЦИИ ЭТОГО ПАРОЛЯ НЕТ.**

Portainer применяет `--admin-password-file` только пока в его БД нет ни одного админа. Если админ уже создан, файл игнорируется, а в логах будет:

```text
instance already has an administrator user defined, skipping admin password related flags.
```

То есть поменять пароль в Vault и перезапустить под — **ничего не произойдёт**, пароль останется старым. Механики, как у GitLab, здесь нет.

Как менять пароль: зайти в UI под `admin`, сменить пароль там и **руками** положить новый пароль в Vault, чтобы не забыть. После такой смены значение в Vault становится просто заметкой — ansible на него больше не смотрит.

Если пароль потерян, средствами k8s-ansible он не восстанавливается. Гарантированный путь — удалить PVC `portainer` в namespace `portainer` и поставить компонент с нуля. БД при этом теряется полностью (окружения, пользователи, стеки). Для UI в режиме «посмотреть, что в кластере» это не страшно.

**Важно 3. У Portainer есть `ClusterRoleBinding` на `cluster-admin`.**

Это нужно, чтобы он управлял тем кластером, в котором сам работает. Управляется переменной `portainer_local_mgmt`.

- При `false` не будут созданы `ServiceAccount` и `ClusterRoleBinding`, под уедет на SA `default`. Portainer поднимется и UI откроется, но локальное окружение `Kubernetes` работать не будет — нет прав на kube-api.
- Следствие из `true`: кто зашёл в UI — тот админ кластера. Поэтому на проде UI закрывается VPN: `portainer_ui_vpn_only_enabled: true`.

**Важно 4. `portainer_trusted_origins`** (защита от CSRF). По умолчанию равен `portainer_ui_domain`.

Значение должно быть **голым хостом**: без `https://`, без порта, без пути — иначе под падает на старте (`log.Fatal`). Если флаг вообще не передавать, UI за Traefik будет получать `403` на любых изменениях: TLS снимается на Traefik, внутрь кластера идёт обычный http, и origin-проверка не сходится.

**Важно 5. Установка НЕ декларативная** — декларативно задаётся только пароль админа. Окружения, пользователи, стеки, реестры и остальные настройки делаются в UI и живут в БД на PVC. Edge-агенты не используются: tunnel-порт наружу не публикуется.

**Теги:** `pre` · `install` · `post`

```bash
# установка + обновление (версия, конфиг)
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/portainer-install.yaml

# отдельный playbook для перезапуска
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/portainer-restart.yaml
```

---
---

### Argo-тройка — порядок установки и права

Речь про `argocd`, `kargo` и `argo-events`.

> **Правило.** Сначала ВСЕ ТРИ ставятся **без** стадии `cfg` (только контроллеры), и лишь потом `cfg` — в особом порядке.
>
> ⚠️ Гонять эти плейбуки **без** `--tags` на голом кластере НЕЛЬЗЯ: без тегов отработает и `cfg`, а он опирается на объекты, которых на этом шаге ещё нет. На живом кластере (всё уже стоит) можно и без тегов.

**Волна 1 — контроллеры.** Порядок внутри волны произвольный.

```bash
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/argocd-install.yaml      --tags crds,pre,install,post
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/kargo-install.yaml       --tags pre,install,post
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/argo-events-install.yaml --tags crds,pre,pre-cfg,install,post
```

**Волна 2 — `cfg`.** Порядок **обязателен**.

```bash
# 1. создаёт namespace Kargo-проектов вместе с самим Project
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/kargo-install.yaml       --tags cfg

# 2. позиция средняя = соглашение, его namespace создан в волне 1
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/argo-events-install.yaml --tags cfg

# 3. RoleBinding едет в namespace из шага 1, раньше него нельзя
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/argocd-install.yaml      --tags cfg

# 4. ОБЯЗАТЕЛЕН: helm upgrade не пересоздаёт под контроллера, а уже открытые watch
#    переживают отзыв RBAC — без рестарта ограничение применяется наполовину
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/argocd-restart.yaml

# 5. последним: RoleBinding нельзя создать в несуществующем namespace
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/cluster-base-install.yaml --tags rbac
```

#### Права: общее для всех трёх

ArgoCD ставится namespace-scoped. Cluster-wide грант у него **ровно один и только на чтение**: `argocd-managed-ns-reader` (`namespaces get/list/watch`) через `ClusterRoleBinding`.

Всё остальное — **пара** `RoleBinding` на **каждый** namespace, куда ArgoCD что-либо кладёт:

| ClusterRole | ServiceAccount | Зачем |
|---|---|---|
| `argocd-managed-deployer` | `argocd-application-controller` | движок sync, пишет манифесты |
| `argocd-managed-ui` | `argocd-server` | действия оператора в UI/CLI, логи, exec |

Объявляются в `argocd_cfg_rbac_role_bindings` (`hosts-vars-override/<cluster>/argocd.yaml`).

> ⚠️ Одного `RoleBinding` **мало**: namespace обязан быть ещё и в Vault по пути `eso-secret/argocd/clusters/in-cluster`, поле `namespaces`. Иначе кэш контроллера туда не заглянет — см. [cluster-base, Важно 4](#cluster-base).

#### Права: kargo

- namespace `kargo` заводит сам helm-релиз (`--create-namespace`).
- `kargo-cluster-secrets`, `kargo-shared-resources`, `kargo-system-resources` заводит апстримный чарт.
- namespace **каждого** проекта заводит стадия `kargo --tags cfg`, одним релизом с `Project`.
- ArgoCD в namespace проекта — пара deployer + ui, **если** содержимое проекта едет из git-ops.
- `Role` `kargo-viewer` в namespace проекта создаёт management-controller **сам** — руками не заводить.
- Доступы людей: `kargo_custom_users` (SA в каждом перечисленном namespace; релизный `kargo` **обязателен**, без него `ListProjects` не авторизуется и список проектов пуст) плюс роли в `kargo_projects`.
- В namespace `argocd` права Kargo **не нужны**: `argocd.integrationEnabled: false`, связь только через git-ops-репозиторий.

#### Права: argo-events — два варианта

**Вариант 1. ОДИН namespace (`argo-events`).** `argo_events_cr_namespace` пустой.

Апстримный режим: контроллер видит CR в **своём** namespace, весь прикладной контур (`EventBus` + `EventSource` + `Sensor`) лежит там же.

- Права контроллера **не нужны** — хватает `Role` из вендоренного `namespace-install.yaml`.
- Стадия `pre-cfg` рендерит **ноль** объектов (шаблон под гейтом). Релиз пустой, прогон безвреден.
- ArgoCD в `argo-events` — пара deployer + ui **обязательна**, если EventBus/EventSource/Sensor едут из git-ops.

> ⚠️ В этом же namespace живёт контроллер, и права у него сильные: он читает и пишет секреты namespace, создаёт Pod'ы и Job'ы, управляет всеми CR Argo Events. `deployer` означает, что **любой коммит в git-ops** может положить сюда Pod с `serviceAccountName` контроллера — и получить все эти права, включая чтение секретов. Прежде чем выдавать, взвесить: доверяете ли вы содержимому git-ops-репозитория настолько же, насколько самому контроллеру.

**Вариант 2. ДВА namespace (`argo-events` + `argo-events-cfg`).** Так стоит на проде.

`argo_events_cr_namespace: "argo-events-cfg"` — контроллер смотрит **туда вместо** своего.

- namespace `argo-events-cfg` заводит стадия `pre-cfg`. В `cluster-base` его объявлять **нельзя** — получится два владельца.
- Права контроллера в `argo-events-cfg` — `Role` + `RoleBinding`, стадия `pre-cfg` ставит их **сама**, автоматически. Объект ложится в **чужой** namespace, а субъект (SA контроллера) остаётся в **своём** — путать нельзя.
- ArgoCD в `argo-events-cfg` — пара deployer + ui: весь прикладной контур (`EventBus` + `EventSource` + `Sensor`) едет из git-ops.
- ArgoCD в `argo-events` **не нужен**: в namespace контроллера git-ops ничего не кладёт.
- Cluster-scoped watch потоков (`Promotion`, `Application`) — `argo_events_rbac_cluster_roles` и `argo_events_rbac_cluster_role_bindings`. Эти объекты ArgoCD создать не может: вербов на cluster-scoped у него нет.

> ⚠️ Контроллер видит CR **ровно в одном** namespace. Включение чужого — это **перенос** реконсиляции, а не расширение: списки CR своего namespace (`argo_events_event_buses`, `_event_sources`, `_sensors`) обязаны остаться **пустыми**: объекты в них не будут обработаны контроллером.

---

### ArgoCD

**Чарт:** локальный — вендоренный апстрим-манифест.

- Есть UI, доступный по URL.
- Нет автоматической обработки новых конфигов (как у Cilium): после обновления конфигов нужен ручной restart.
- Есть ожидание готовности CRD. Если добавляются новые CRD, их ожидание надо добавить в [`playbook-app/argocd-install.yaml`](playbook-app/argocd-install.yaml).
- Ожидание готовности deployment/daemonset — через `kubectl rollout status ...`.
- Есть работа с ESO.

**Важно 1. Доверенные git-хосты.** Нужно выполнить `ssh-keyscan` на те git-репозитории, которые планируется использовать, и добавить их публичные ключи. Делается это kustomize-патчем на `ConfigMap` `argocd-ssh-known-hosts-cm` через `argocd_install_kustomize_patches_extra` в `hosts-vars-override/<cluster>/argocd.yaml`.

> ⚠️ Без этого ArgoCD не сможет подключиться к репозиториям — недоверенный host.
> ⚠️ strategic-merge перезаписывает `ssh_known_hosts` **целиком**: апстримные записи для github/gitlab.com теряются. Это нормально, если все git-ops-репозитории внутренние.

**Важно 2. Аккаунты и политики.** У ArgoCD есть механика локальных аккаунтов, она состоит из трёх частей:

| Объект | Что хранит |
|---|---|
| `ConfigMap` `argocd-cm` | список аккаунтов, их capabilities и время смены пароля |
| `ConfigMap` `argocd-rbac-cm` | ОБЩИЕ политики для всего ArgoCD: какой аккаунт какие права имеет в том или ином проекте |
| `Secret` `argocd-secret` | пароли в формате bcrypt для каждого аккаунта |

Логика зашита в стадию `accounts-sync` и управляется переменными `argocd_local_accounts` (список аккаунтов) и `argocd_policy_csv_list` (список политик). Пароли (состояние) хранятся в Vault — одним JSON-объектом по одному пути, сразу для всех аккаунтов.

Логика синхронизации:

| Ситуация | Что происходит |
|---|---|
| Аккаунт есть локально, но его нет в Vault | сгенерировать пароль, положить в Vault и в `argocd-secret` |
| Аккаунта нет локально, но он есть в Vault | удалить из Vault и из `argocd-secret` |
| Аккаунт есть и там, и там | приоритет у Vault — он точка правды. Если bcrypt различается, в `argocd-secret` кладётся значение из Vault |
| Аккаунт есть и там, и там, изменилось `passwordMtime` | сгенерировать новый пароль и положить в Vault и в `argocd-secret` |

После внесения изменений в политики и аккаунты надо сделать:

```bash
# синхронизировать аккаунты и политики (ConfigMap)
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/argocd-install.yaml --tags install

# синхронизировать пароли от аккаунтов в Vault + argocd-secret
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/argocd-install.yaml --tags accounts-sync
```

**Теги:** `crds` · `pre` · `install` · `post` · `cfg`, плюс точечные `accounts-sync` и `accounts-distribute`.

> ⚠️ Стадий `rbac` и `gitops` больше нет — обе слились в единую `cfg`.

```bash
# установка + конфигурация
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/argocd-install.yaml
```

- Локальные аккаунты (логин + пароль, включая custom-admin) задаются декларативно через `argocd_local_accounts` в `hosts-vars-override/`. Пароли генерятся в рантайме и кладутся в Vault по пути `eso-secret/argocd/accounts/creds`. Ротация: сдвинуть `passwordMtime` у аккаунта и прогнать `argocd-install.yaml --tags accounts-sync`.
- Контракт для внешнего git-ops-репозитория: имена из `argocd_local_accounts` ссылаются в `AppProject.spec.roles[].groups` как есть — строку-username ArgoCD биндит к роли проекта через Casbin. Custom-admin получает глобальный `role:admin` через `argocd_policy_csv_list` здесь же.

**Обновление версии.**

1. Скачать новый yaml. Важно: нужен `namespace-install.yaml` **конкретного тега**, а не `stable/manifests/install.yaml`.

   ```bash
   git -C sources/argo-cd show v<version>:manifests/namespace-install.yaml \
     > playbook-app/charts/argocd/install/templates/install.yaml
   ```

2. Разнести yaml по файлам:
   - `playbook-app/charts/argocd/crds/crds.yaml` — только CRD (примерно 24 000 строк);
   - `playbook-app/charts/argocd/install/templates/install.yaml` — всё, кроме CRD.
3. Версия **не** указывается в `hosts-vars/` и `hosts-vars-override/` — она уже внутри `*.yaml`.
4. Пример обновлённого конфига — `docs/argocd/...`.

> ⚠️ ArgoCD ставится namespace-scoped: в `namespace-install.yaml` нет трёх `ClusterRole` и трёх `ClusterRoleBinding`. Файл `stable/manifests/install.yaml` — это cluster-install, он вернёт ArgoCD права `cluster-admin`. Брать нельзя.
> ⚠️ Рестарт после установки **обязателен**: `helm upgrade` не пересоздаёт под контроллера, а открытые watch переживают отзыв RBAC.

```bash
# обновление версии или конфига
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/argocd-install.yaml
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/argocd-restart.yaml
```

---

### ArgoCD — стадия `cfg`

Бывшие стадии `rbac` + `gitops`, слитые в одну.

Установка всех необходимых ресурсов Kubernetes для git-ops-паттерна. Здесь не запускается никаких компонентов (Deployment, CronJob и так далее) — это создание ресурсов `AppProject` и `Application`. Есть работа с ESO.

**Важно 1.** Сгенерировать ssh-ключи и положить их в Vault. Чтобы ArgoCD смог подключиться к репозиторию, нужен k8s Secret, который создаётся через ESO, а тот смотрит в Vault. Эти два шага выполняются **вручную**.

**Важно 2.** Создать репозиторий и добавить к нему deploy-keys, созданные в предыдущем пункте. Эти два шага тоже выполняются **вручную**.

**Важно 3.** ESO для ArgoCD нужно столько, сколько разных **репозиториев**, а не ключей.

> ⚠️ В теории и на практике можно создать хоть 10 k8s Secret с одинаковым `repoUrl`. В этом случае ArgoCD возьмёт тот, который первым вернётся в ответе от kube-api. Чтобы избежать путаницы, держим схему «один репозиторий = один `repo_url` + один ESO + один секрет в Vault + один k8s Secret».

**Важно 4. Последовательность установки.**

1. Настроить конфиги для git-ops в `hosts-vars-override/`:
   - `argocd_git_ops_app_projects` и `argocd_git_ops_applications` — какие проекты и приложения создать;
   - `eso_vault_integration_argocd_extra`;
   - секреты вида `git_ops_repo_pattern` / `git_ops_repo_direct`.
2. Прогнать стадию `cfg` и проверить, что все ресурсы установились корректно.
3. Создать ssh-ключи (private + public) и положить их в Vault — ESO создаст из них k8s Secret.
4. Создать репозитории, URL которых указаны в `argocd_git_ops_applications`, и добавить к ним deploy-keys, чтобы у ArgoCD был доступ.

```bash
# установка + обновление (конфиг)
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/argocd-install.yaml --tags cfg
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/argocd-restart.yaml
```

> ⚠️ Порядок: `kargo --tags cfg` → `argocd --tags cfg`. RoleBinding едет в namespace Kargo-проектов, а их создаёт стадия `kargo/cfg`.

---

### Argo Rollouts

**Чарт:** локальный — вендоренный апстрим-манифест.

```bash
# установка + конфигурация
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/argo-rollouts-install.yaml

# отдельный playbook для перезапуска
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/argo-rollouts-restart.yaml
```

**Обновление версии.**

1. Скачать новый yaml: <https://raw.githubusercontent.com/argoproj/argo-rollouts/refs/tags/v1.9.1/manifests/install.yaml>
2. Разнести yaml по файлам:
   - `playbook-app/charts/argo-rollouts/crds/crds.yaml` — только CRD (там много строк);
   - `playbook-app/charts/argo-rollouts/install/templates/install.yaml` — всё, кроме CRD.
3. Версия **не** указывается в `hosts-vars/` и `hosts-vars-override/` — она уже внутри `*.yaml`.
4. Прогнать установку и рестарт.

---

### Argo Events

**Чарт:** локальный — вендоренный апстрим-манифест.

Компонент отвечает **только** за контроллер и вебхук. Прикладной контур — то есть `EventBus` (шина), `EventSource` и `Sensor` — может жить либо в том же namespace, либо в чужом.

**Куда смотрит контроллер** — определяет одна переменная `argo_events_cr_namespace`:

| Значение | Поведение |
|---|---|
| пусто (по умолчанию) | апстримный режим: контроллер видит CR в **своём** namespace |
| задано | к манифесту подмешивается патч `--managed-namespace`, и контроллер смотрит **туда вместо** своего |

Отдельного bool-тумблера нет: непустая строка и есть признак включения.

> ⚠️ Контроллер видит CR **ровно в одном** namespace. Это ограничение продукта: `--managed-namespace` принимает одно значение и кладётся единственным ключом в кэш контроллера. Значит включение чужого namespace — это **перенос** реконсиляции, а не расширение: CR в своём namespace перестанут обновляться.

**Порядок стадий:** `crds` → `pre` → `pre-cfg` → `install` → `post` → `cfg`

> ⚠️ `pre-cfg` идёт **перед** `install` не для красоты. На холодном старте без прав в cr-namespace контроллер не деградирует, а **умирает**: кэш не синхронизируется, controller-runtime не дожидается его за `CacheSyncTimeout` (2 минуты) и процесс завершается → `CrashLoopBackOff`.
>
> Отказ **отложенный**: readiness — это `healthz.Ping`, он отвечает `200` независимо от кэша, поэтому под успевает стать `Ready` и `helm --wait` считает install успешным. Падение начинается уже после того, как playbook отрапортовал ОК.
>
> Перестановка работает потому, что субъекты в биндингах Kubernetes **не** валидируются: `RoleBinding` создаётся раньше, чем появится сам SA.

**Что берётся из `cluster-base` — НИЧЕГО.**

- namespace `argo-events` компонент создаёт сам (`--create-namespace`).
- cr-namespace (если задан) компонент создаёт сам же — стадия `pre-cfg`, шаблон `charts/argo-events/pre-cfg/templates/cr-namespace.yaml`. Объявлять его в `cluster-base` **нельзя**: получится второй владелец объекта.

**Что кладёт ansible в cr-namespace — ровно две вещи.**

1. Права контроллера: `Role` + `RoleBinding`, шаблон `charts/argo-events/pre-cfg/templates/controller-rbac.yaml`. Рендерится автоматически при непустом `argo_events_cr_namespace`. Объект ложится в **чужой** namespace, а субъект (SA контроллера) остаётся в **своём** — путать нельзя.
2. То, что оператор сам объявил в списках `argo_events_rbac_*`.

Всё остальное там (NetworkPolicy, ESO, PodMonitor, прикладные CR) — **не** забота компонента. Это кладёт владелец содержимого, у нас — git-ops через ArgoCD.

```bash
# установка + конфигурация
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/argo-events-install.yaml

# отдельный playbook для перезапуска
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/argo-events-restart.yaml
```

Рестарт перекатывает только два Deployment в namespace контроллера. Шину, EventSource и Sensor не трогает: их создаёт контроллер, а перекат шины рвёт доставку.

**Обновление версии.** Вендоренный апстрим-манифест — это `namespace-install.yaml` тега `vX.Y.Z` **минус** три CRD.

```bash
git -C sources/argo-events show vX.Y.Z:manifests/namespace-install.yaml | sed -n '121,$p' \
  > playbook-app/charts/argo-events/install/templates/install.yaml
```

CRD ставит отдельная стадия `crds`. Внутри helm-релиза они означали бы, что `helm uninstall` каскадом снесёт все EventBus/EventSource/Sensor. После обновления манифеста обновить `argo_events_version` в `hosts-vars/`.

**EventBus** — массив `argo_events_event_buses`, пустой по умолчанию.

- Шина не поднимется сама: элемент со спекой заводится в `hosts-vars-override/`.
- Профиль с пояснениями лежит закомментированным примером в [`hosts-vars/argo-events.yaml`](hosts-vars/argo-events.yaml).

> ⚠️ `streamConfig.replicas` обязан совпадать с числом реплик JetStream: глобальный дефолт в апстримном ConfigMap равен `3`, и на одной реплике поток не создастся.
> ⚠️ `maxBytes` — **число в байтах**. Строка `"6Gi"` молча станет нулём, а ноль сервер нормализует в `-1`, то есть в **отсутствие** лимита.
> ⚠️ `streamConfig` применяется **ровно один раз** — при создании стрима, и создаёт его первый подключившийся EventSource или Sensor. Менять позже можно только удалив стрим вручную.

**Смена cr-namespace на живом кластере.**

- Если в целевом namespace уже есть объекты от прошлой установки, они могут принадлежать другому helm-релизу — стадия `pre-cfg` упрётся в `invalid ownership metadata`. Сначала снести старый релиз (`helm -n <ns> uninstall <release>`), потом ставить.
- Между сносом и стадией `pre-cfg` контроллер останется без прав. Уже **запущенный** контроллер это переживает (рефлекторы ретраят, процесс жив), умирает только холодный старт.

---

### Kargo

**Чарт:** официальный upstream.

Движок промоушенов. Стадии: `pre` → `install` → `post` → `cfg`.

**Что берётся из `cluster-base` — НИЧЕГО.**

- namespace `kargo` компонент создаёт сам.
- namespace **каждого** Kargo-проекта заводит стадия `cfg`, в одном релизе с самим `Project`. Обязательную метку `kargo.akuity.io/project: "true"` шаблон ставит сам; аннотация `kargo.akuity.io/keep-namespace: "true"` берётся из элемента `kargo_projects` и уезжает и на namespace, и на `Project`.
- namespace `kargo-cluster-secrets`, `kargo-shared-resources` и `kargo-system-resources` создаёт **сам апстримный чарт**. В `cluster-base` их объявлять **нельзя** — получится второй владелец и релиз упадёт.

**Порядок онбординга нового проекта.**

1. `kargo --tags cfg` — namespace проекта, `Project` и права. `Role` `kargo-viewer` в namespace проекта создаёт management-controller сам, вручную не заводить.
2. `argocd --tags cfg` — `RoleBinding` в этот namespace, если проектом управляет ArgoCD.

> ⚠️ Порядок `kargo` → `argocd` **обязателен**: namespace, в котором ArgoCD получает права, создаёт стадия `kargo/cfg`.

```bash
# установка + конфигурация
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/kargo-install.yaml

# отдельный playbook для перезапуска
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/kargo-restart.yaml
```

**Проекты и доступы** — декларативно в `hosts-vars-override/`:

- `kargo_projects` — список проектов;
- `kargo_custom_users` — доступы. У Kargo **нет** роли по умолчанию: права выдаются аннотацией с claims на любом `ServiceAccount`.

**Обновление версии.** Обновить версию чарта в [`hosts-vars/kargo.yaml`](hosts-vars/kargo.yaml) и прогнать установку.

---

### mon-system

**Чарт:** локальный (`playbook-app/charts/`).

Состав: prometheus-operator + prometheus + alertmanager + node-exporter + kube-state-metrics + loki + vector + grafana.

- Есть UI, доступный по URL.
- Есть ожидание готовности CRD. Если добавляются новые CRD, их ожидание надо добавить в [`playbook-app/mon-system-install.yaml`](playbook-app/mon-system-install.yaml).
- Ожидание готовности deployment/daemonset — через `kubectl rollout status ...`.
- У Grafana есть работа с ESO.

**Важно 1.** Через `--tags crds` можно установить только CRD — это нужно до установки любых других компонентов, см. [Prometheus-operator CRD](#prometheus-operator-crd).

**Теги:** `crds` · `pre` · `prometheus-operator` · `prometheus` · `alertmanager` · `node-exporter` · `ksm` · `loki` · `vector` · `grafana` · `grafana-postgresql` · `post`

```bash
# установка
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/mon-system-install.yaml

# отдельный playbook для перезапуска
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/mon-system-restart.yaml
```

**Обновление версии prometheus-operator.**

1. Скачать новый yaml: <https://github.com/prometheus-operator/prometheus-operator/releases>
2. Разнести yaml по файлам:
   - `playbook-app/charts/mon-system/crds/crds.yaml` — все CRD (примерно 80 000 строк);
   - `playbook-app/charts/mon-system/prometheus-operator/templates/prometheus-operator.yaml` — вся установка (Deployment, RBAC, Service).
3. Есть изменения в дефолтных конфигах — их надо не затереть: после вставки нового `*.yaml` вернуть обновлённые дефолтные конфиги.
4. Версия указывается в `hosts-vars/` и `hosts-vars-override/` — внутри `*.yaml` надо не потерять шаблонизацию.

**Обновление версий node-exporter, ksm, loki, vector, grafana** — просто обновить версии в `hosts-vars/`.

---

### cluster-base

**Чарт:** локальный (`playbook-app/charts/`).

Глобальный компонент: заводит **продуктовые** namespace кластера и выдаёт в них права. К приложению не привязан.

- Нет workload, нет ESO, нет ingress — поэтому два stage (`namespaces` + `rbac`), а не три фазы (`pre` + `install` + `post`).
- Два helm-релиза в двух своих namespace: `cluster-base-namespaces` (ns `cluster-namespaces`) и `cluster-base-rbac` (ns `cluster-rbac`).
- Объекты живут в **чужих** namespace или в cluster scope. Своих подов у компонента нет.
- Шесть списков объектов, по одному на тип: namespaces, `ServiceAccount`, `Role`, `ClusterRole`, `RoleBinding`, `ClusterRoleBinding`.
- Элемент списка — **полная** спецификация объекта: шаблон рендерит его как есть, ничего не вычисляя.
- Базовые списки в [`hosts-vars/cluster-base.yaml`](hosts-vars/cluster-base.yaml) **пустые**. Все объекты задаются в `hosts-vars-override/<cluster>/`.

> ⚠️ В stage `namespaces` по задумке лежат **только продуктовые** namespace — те, в которые раскатываются приложения. Никаких системных namespace тут быть не должно: свои системные namespace заводят **сами** компоненты.

| Namespace | Кто заводит |
|---|---|
| `argo-events-cfg` (cr-namespace) | стадия `argo-events --tags pre-cfg` |
| namespace каждого Kargo-проекта | стадия `kargo --tags cfg`, одним релизом с самим `Project` |
| `kargo-cluster-secrets`, `kargo-shared-resources`, `kargo-system-resources` | сам апстримный чарт kargo |
| namespace самих компонентов (`argocd`, `kargo`, `argo-events`, …) | их же helm-релиз (`--create-namespace`) |

> ⚠️ Объявить такой namespace ещё и здесь = **два владельца** одного объекта → stage падает на `invalid ownership metadata`.

**Важно 1. Порядок stage.** `namespaces` — до продуктов, `rbac` — после установки компонентов, чьи namespace он трогает. `RoleBinding` нельзя создать в несуществующем namespace, поэтому `rbac` идёт последним (traefik-lb, seaweedfs, argocd, argo-events).

**Важно 2.** Удаление элемента из `cluster_base_namespaces_list` = **снос namespace вместе со всем содержимым** на следующем прогоне. Защиты нет: аннотация `argocd.argoproj.io/sync-options` (`Delete=false` + `Prune=false`) защищает только от ArgoCD, но не от helm.

**Важно 3. Имя объекта в чужом namespace обязано быть уникальным.** Приём — префикс-маркер владельца (`argocd-managed-*`).

Совпадение имени с объектом другого helm-релиза роняет **весь** stage: `invalid ownership metadata`. Это штатная защита, флаг `--take-ownership` намеренно **не** используется.

Если перехват осознанный (объект создан ArgoCD, Kargo или руками) — усыновить вручную, проставив три метки владения:

```bash
kubectl label    <kind> <name> [-n <ns>] app.kubernetes.io/managed-by=Helm --overwrite
kubectl annotate <kind> <name> [-n <ns>] meta.helm.sh/release-name=<release> --overwrite
kubectl annotate <kind> <name> [-n <ns>] meta.helm.sh/release-namespace=<release-ns> --overwrite
```

Пары release/namespace: `cluster-base-namespaces` / `cluster-namespaces` и `cluster-base-rbac` / `cluster-rbac`. После простановки меток повторить прогон — helm примет объект как свой.

**Важно 4. Список namespace для ArgoCD дублируется в Vault:** путь `eso-secret/argocd/clusters/in-cluster`, поле `namespaces`.

> ⚠️ Namespace, которого нет в этом поле, ArgoCD не увидит даже при наличии `RoleBinding` — кэш контроллера в него не заглядывает. Обновлять оба места сразу: `hosts-vars-override/<cluster>/cluster-base.yaml` и Vault.

**Важно 5. Заведение namespace для нового продукта — два шага, и оба до `Application` в git-ops.** ArgoCD ставится namespace-scoped и создавать `Namespace` не может.

1. Запись в `cluster_base_namespaces_list` → прогон `cluster-base --tags namespaces`.
2. Пара `RoleBinding` (deployer + ui) в `argocd_cfg_rbac_role_bindings` (`hosts-vars-override/<cluster>/argocd.yaml`) → прогон `argocd --tags cfg` и `argocd-restart`.

С переходом на стадию `cfg` права ArgoCD уехали из `cluster-base`; namespaced-элементов в stage `rbac` сейчас нет. Объявлять `Namespace` в чарте продукта **не** нужно — ArgoCD его не применит.

**Теги:** `namespaces` · `rbac`

```bash
# установка + обновление (namespace + RBAC)
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/cluster-base-install.yaml

# только namespace
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/cluster-base-install.yaml --tags namespaces

# только RBAC
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/cluster-base-install.yaml --tags rbac
```

**Проверка прав после онбординга namespace:**

```bash
# должно вернуть yes
kubectl auth can-i create deployment \
  --as=system:serviceaccount:argocd:argocd-application-controller -n <new-ns>

# должно вернуть no
kubectl auth can-i create namespace \
  --as=system:serviceaccount:argocd:argocd-application-controller
```

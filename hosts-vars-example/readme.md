# hosts-vars-example

Готовые примеры конфигов для `hosts-vars-override/<cluster>/`.

Все значения — **плейсхолдеры**: `example.com`, IP из документационных диапазонов
(`203.0.113.0/24`, `198.51.100.0/24`, `10.0.0.0/8`), пароли `CHANGE-ME-*`,
OIDC client_id `0000...`, S3-ключи `CHANGE-ME-access-key-*`. Реальных данных здесь нет.

## Как пользоваться

```bash
mkdir -p hosts-vars-override/my-cluster
cp hosts-vars-example/vault/oidc.yaml hosts-vars-override/my-cluster/vault.yaml
```

Имя файла в override — **всегда имя компонента** (`vault.yaml`, `argocd.yaml`), независимо
от того, какое состояние скопировано. Дальше правятся домены, IP и секреты.

Запуск — с ДВУМЯ инвентарями, override указывает на подпапку кластера:

```bash
ansible-playbook -i hosts-vars/ -i hosts-vars-override/my-cluster/ playbook-app/vault-install.yaml
```

## Три слоя переменных

| Слой | Что лежит | В git |
|---|---|---|
| `hosts-vars/` | база: полная структура каждой переменной, дефолты | да |
| `hosts-vars-example/` | примеры состояний (эта директория) | да |
| `hosts-vars-override/<cluster>/` | реальные домены, IP, секреты | **нет**, gitignore |

Массивы с суффиксом `_extra` **складываются** с базовыми. Списки без суффикса
(`linstor_cluster_helm_values_properties`, `*_storage_classes`, `vault_groups_extra`
внутри своей роли) заменяют базовый **целиком** — частичного слияния не будет.

## Структура

Одна директория на компонент, внутри 1–4 файла — по одному на типовое состояние.
Один комментарий в шапке каждого файла объясняет, чем это состояние отличается и
какие в нём грабли; ниже — чистый YAML.

Повторяющиеся оси состояний:

**Ось TLS.** У КАЖДОГО компонента с UI есть оба варианта — это главная развилка:

| Вариант | Файлы | Флаги |
|---|---|---|
| Ingress + cert-manager (TLS на origin) | `direct-acme`, `ui-ingress-cert-manager`, `simple`, `small-*` | `issuer` / `ingress_tls` / `certificate` = `true` |
| Ingress + HTTP (TLS на внешнем слое) | `behind-cloudflare*`, `http-tls-upstream`, `ui-ingress-http-tls-upstream` | те же три = `false` |

Во втором варианте HTTPS терминирует слой, о котором кластер ничего не знает —
CDN, edge-балансировщик, сервис-меш, — и до сервера доходит уже HTTP. Тогда
сертификат на origin не нужен, а ACME HTTP-01 через проксирующий слой всё равно
не дойдёт. Внешний слой ОБЯЗАН проставлять `X-Forwarded-Proto: https`: без этого
приложения, строящие абсолютные ссылки (ZITADEL, GitLab, Teleport), уйдут в
петлю редиректов. Компоненты с UI: `argocd`, `cilium`, `filestash`, `gitlab`,
`kargo`, `linstor`, `longhorn`, `mon-system`, `outline`, `portainer`,
`seaweedfs`, `teleport`, `traefik`, `vault`, `zitadel`.

Остальные оси:

- **`simple` / `small-*`** — минимум для одного узла или тестового кластера.
- **`prod-*` / `ha-*`** — реплики, `nodeSelector`, requests/limits, репликация хранилища.
- **`oidc`** — вход через ZITADEL; client secret всегда в Vault, в inventory только `client_id`.

## Порядок установки argo-тройки

`argocd`, `kargo` и `argo-events` ставятся В ДВЕ ВОЛНЫ: сначала все три без стадии
`cfg`, затем `cfg` в порядке `kargo` → `argo-events` → `argocd` → `argocd-restart`.
Подробности и требуемые права — в корневом `README.md`, раздел «ARGO-ТРОЙКА».

У `argo-events` два принципиально разных состояния, им соответствуют два файла:
`single-namespace.yaml` (контроллер видит CR у себя) и
`two-namespaces-cr-namespace.yaml` (контроллер смотрит в чужой namespace —
это ПЕРЕНОС реконсиляции, а не расширение).

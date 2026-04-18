# Audit: hosts-vars helm_values vs upstream sources/

**Дата:** 2026-04-17  
**Scope:** 12 helm-компонентов — сверка ключей `*_helm_values` с upstream `sources/<c>/values.yaml`  
**Метод:** прямое сравнение key-path, image repository paths, deprecated/removed fields, security defaults

---

## Executive Summary

| Severity | Count | Components |
|---|---|---|
| 🔴 Breaking (при апгрейде) | 2 | zitadel |
| 🟡 Deprecated (в текущей конфигурации) | 0 | — |
| 🔵 Style / Missing-recommended | 5 | traefik, external-secrets, metrics-server, haproxy, longhorn |
| ⚠️ Version drift (мониторинг) | 4 | gitlab, gitlab-runner, cilium, zitadel |
| ✅ Safe / Fully compliant | 8 | traefik, external-secrets, metrics-server, haproxy, longhorn, cert-manager, teleport, vault |

**Итог:** Текущие деплои стабильны. 🔴 findings критичны только при апгрейде zitadel с 8.12.0 → 9.x — удалить два ключа перед бампом.

---

## Методология

- **Exact-match** (sources = ground truth): traefik, external-secrets, longhorn, haproxy, metrics-server, gitlab.
- **Minor drift** (sources HEAD, не pinned): cilium (1.19.1 ← 1.20.0-dev), gitlab-runner (0.78.0 ← 0.80.0-beta). Findings помечены ⚠️.
- **Major drift**: zitadel (8.12.0 ← 9.30.0). Findings — только для будущего апгрейда.
- **Templated version**: teleport, vault (bank-vaults 0.0.0). Только структурная проверка.
- **Custom local chart**: mon-prometheus-operator (не kube-prometheus-stack).

---

## Phase A — Exact-match components

---

### 1. traefik ✅ (chart 39.0.5)

**Chart version:** hosts-vars `39.0.5` = sources `39.0.5` ✓  
**Image path:** `docker.io/traefik` ✓ официальный путь

**Key-path audit:** все 35+ ключей существуют в upstream values.yaml ✓

**Deprecated fields:** `core.defaultRuleSyntax` помечен "Deprecated since v3.4" — **не используется** ✓

#### Findings

| Severity | Описание | Ключ | Действие |
|---|---|---|---|
| 🔵 Style | `podSecurityContext` не содержит `seccompProfile` (upstream default — `RuntimeDefault`) | `traefik_helm_values.podSecurityContext` | Добавить `seccompProfile: {type: RuntimeDefault}` |

Всё остальное — safe.

---

### 2. external-secrets ✅ (chart 2.3.0)

**Chart version:** `2.3.0` = `2.3.0` ✓  
**Image path:** `ghcr.io/external-secrets/external-secrets` ✓

**Key-path audit:** все ключи (controller / webhook / certController) существуют в upstream ✓

**Deprecated check:** `crds.unsafeServeV1Beta1` помечен "will be removed on 2026.05.01" — **не используется** ✓

#### Findings

| Severity | Описание | Действие |
|---|---|---|
| 🔵 Style | `securityContext` не задан явно — полагаемся на chart defaults (которые хорошие: `runAsNonRoot`, `readOnlyRootFilesystem`, `seccompProfile: RuntimeDefault`) | Опционально — задать явно для auditable конфига |

---

### 3. metrics-server ✅ (chart 3.13.0)

**Chart version:** `3.13.0` = `3.13.0` ✓  
**Image path:** `registry.k8s.io/metrics-server/metrics-server` ✓

**Key-path audit:** все ключи существуют ✓  
**Deprecated:** нет ✓

#### Findings

| Severity | Описание | Действие |
|---|---|---|
| 🔵 Style | `securityContext` не задан явно — chart defaults secure (`runAsNonRoot`, `seccompProfile: RuntimeDefault`) | Опционально |
| ⚪ Safe | `priorityClassName: system-cluster-critical` — upstream default, не переопределён (правильно) | — |

---

### 4. haproxy (kubernetes-ingress) ✅ (chart 1.49.0)

**Chart version:** `1.49.0` = `1.49.0` ✓  
**Image path:** `docker.io/haproxytech/kubernetes-ingress` ✓

**Key-path audit:** все ключи существуют ✓  
**Deprecated:** нет ✓

#### Findings

| Severity | Описание | Действие |
|---|---|---|
| 🔵 Style | `PodDisruptionBudget` не настроен (upstream поддерживает). Для DaemonSet-режима некритично. | Опционально для production HA |
| ⚪ Safe | `controller.resources: {}` — явно очищает upstream-рекомендацию (cpu:250m/mem:400Mi). Намеренно. | — |
| ⚪ Safe | `controller.service.type: ClusterIP` vs upstream default `NodePort` — намеренно, DaemonSet + port-specific | — |

---

### 5. longhorn ✅ (chart 1.11.1)

**Chart version:** `1.11.1` = `1.11.1` ✓  
**Image paths:** весь блок `image.longhorn.*` + `image.csi.*` соответствует upstream структуре ✓

**Key-path audit:** все 50+ ключей существуют ✓  
**Deprecated:** нет ✓

#### Findings

| Severity | Описание | Ключ | Действие |
|---|---|---|---|
| 🔵 Style | `longhornUI.replicas: 1` — upstream HA default `2`. Один pod UI снижает доступность. | `longhorn_helm_values.longhornUI.replicas` | Рассмотреть подъём до 2 |
| 🔵 Style | `longhornUI.affinity: {}` — явно убирает upstream pod anti-affinity (UI поды могут жить на одном узле). Риск при replicas≥2. | `longhorn_helm_values.longhornUI.affinity` | При replicas=2 вернуть upstream anti-affinity |
| 🔵 Style | `persistence.defaultClassReplicaCount: 2` — upstream default `3`. Снижена избыточность хранилища. | — | Документировать явно как намеренное снижение HA |
| ⚪ Safe | `longhornManager.log.format: json` — upstream default `plain`. Улучшает observability. | — | — |

---

### 6. gitlab ✅ ключи / ⚠️ version drift (chart 8.11.8)

**Chart version:** hosts-vars `8.11.8`, sources `9.10.3` — **drift +1 major version**  
⚠️ Аудит key-paths проведён против sources 9.10.3 — все ключи валидны (обратная совместимость сохранена).

**Image path:** `{{ gitlab_image_registry_host }}/gitlab-org/build/cng/*` ✓  
**Key-path audit (против 9.10.3):** все ключи существуют ✓  
**Deprecated:** нет ✓  
**External services pattern** (postgres/redis/minio отдельными релизами) — корректен ✓

#### Findings

| Severity | Описание | Действие |
|---|---|---|
| ⚠️ Version drift | sources содержат 9.10.3, hosts-vars пинят 8.11.8 | Обновить sources до тега 8.11.8 или обновить chart pin до 9.10.3 после тестирования |

---

## Phase B — Version verify

---

### 7. cert-manager ✅ (chart 1.20.2)

**Chart version:** hosts-vars `1.20.2`, sources Chart.yaml — шаблонизированный (v0.0.0), appVersion `v1.20.2` ✓  
**Image path:** `quay.io/jetstack/cert-manager-*` ✓

**Key-path audit:** все 35 ключей существуют ✓ (crds.enabled, acmesolver, cainjector, webhook, startupapicheck — всё valid)

**Deprecated:** `installCRDs` (старый ключ) — **не используем**, используем `crds.enabled` (современный) ✓

#### Findings

Нет критических. Конфигурация полностью корректна.

---

### 8. mon-prometheus-operator ✅

**Особенность:** компонент использует **custom local Helm chart** (`playbook-app/charts/mon-prometheus-operator/install/`), не upstream `kube-prometheus-stack`. Prometheus CR и Alertmanager CR деплоятся отдельными локальными чартами.

**Key-path audit (local chart):** все 7 ключей (`namespace`, `imageOperator`, `tolerations`, `nodeSelector`, `resources`, `port`, `args`) существуют ✓

**Image paths:**
- `quay.io/prometheus-operator/prometheus-operator:v0.90.1` = upstream path ✓
- `quay.io/prometheus-operator/prometheus-config-reloader:v0.90.1` = upstream path ✓
- `quay.io/prometheus/prometheus:v3.7.2` = upstream path ✓
- `quay.io/prometheus/alertmanager:v0.28.1` = upstream path ✓

#### Findings

Нет критических. Архитектурный выбор (custom chart вместо kube-prometheus-stack) намеренен.

---

## Phase C — Minor drift

---

### 9. cilium ⚠️ (chart 1.19.1 ← sources 1.20.0-dev)

**Chart version drift:** `1.19.1` vs sources `1.20.0-dev` ⚠️  
Аудит проведён против 1.20.0-dev; findings помечены.

**Image paths:** все image repository + tag ключи существуют ✓  
**`envoy.image.useDigest: false`** — валидный ключ, присутствует в upstream ✓

**Key-path audit:** 99% ключей существуют ✓

#### Findings

| Severity | Описание | Ключ | Действие |
|---|---|---|---|
| 🔵 Upgrade-note | `operator.image.suffix: ""` — в 1.20.x upstream поменял default с `""` на `"-ci"`. Наш явный override `""` **по-прежнему корректен** и продолжит давать `operator-generic`. Но при апгрейде стоит убедиться что template не изменился. | `cilium_helm_values.operator.image.suffix` | Проверить image pull при апгрейде до 1.20.x |
| ⚪ Safe | Все остальные ключи (hubble, envoy, ipam, hostFirewall, kubeProxyReplacement и пр.) существуют в 1.20.x ✓ | — | — |

---

### 10. gitlab-runner ✅ ключи / ⚠️ version drift (chart 0.78.0 ← 0.80.0-beta)

**Chart version drift:** `0.78.0` (stable) vs sources `0.80.0-beta` ⚠️  
**Image path:** `{{ gitlab_runner_image_registry_host }}/gitlab-org/gitlab-runner` ✓

**Key-path audit:** все ключи (включая `runners.config` TOML-блок, `rbac.*`, `serviceAccount.*`, `metrics.podMonitor.*`) существуют ✓

**Deprecated fields** — используем современные эквиваленты ✓:
- `rbac.generatedServiceAccountName` → мы используем `serviceAccount.name` ✓
- `metrics.serviceMonitor` → мы используем `metrics.podMonitor` ✓

#### Findings

| Severity | Описание | Действие |
|---|---|---|
| 🔵 Style | `metrics.podMonitor.scrapeTimeout: "15s"` — ключ не задокументирован в upstream values.yaml, но валиден в Prometheus Operator CRD. Пробрасывается в PodMonitor объект. | Документировать как custom extension |
| ⚪ Safe | sources содержат beta 0.80.0, hosts-vars пинят стабильный 0.78.0. Разумно. | — |

---

## Phase D — Special cases

---

### 11. teleport ✅

**Chart version:** `18.7.2` (hosts-vars), sources — templated (`*version`). Только структурная проверка.

**Key-path audit:** все 22 ключа существуют в upstream ✓:
`image`, `clusterName`, `kubeClusterName`, `chartMode`, `proxyListenerMode`, `persistence.*`, `auth.teleportConfig.*`, `proxy.teleportConfig.*`, `operator.*`, `log.*`, `service.*`, `podMonitor.*`, `tolerations`, `nodeSelector`, `affinity`, `resources`, `ingress.*`

**Deprecated:** нет ✓

#### Findings

Нет. Конфигурация полностью корректна.

---

### 12. vault (bank-vaults operator) ✅

**Chart version:** `1.23.4` (hosts-vars), sources `0.0.0` (development). Только структурная проверка.

**Operator helm values key-path audit:**  
`image.repository`, `bankVaults.image.repository`, `bankVaults.image.tag`, `tolerations`, `nodeSelector`, `resources` — все существуют ✓

**Vault CR spec:** `image:` в строковом формате `"registry/hashicorp/vault:tag"` — корректный формат CRD ✓  
Все поля CR (`size`, `serviceAccount`, `unsealConfig.*`, `config.*`, `externalConfig.*`, `volumeClaimTemplates`, `resources`, `tolerations`, `affinity`) — корректны ✓

#### Findings

Нет. Конфигурация полностью корректна.

---

### 13. zitadel 🔴 (chart 8.12.0 ← sources 9.30.0 — MAJOR GAP)

**Chart version:** `8.12.0` (hosts-vars), sources `9.30.0`. Аудит только для **планирования будущего апгрейда**.

⚠️ **Текущий деплой на 8.12.0 работает корректно. Findings ниже — только при апгрейде до 9.x.**

**Image paths:** `imageRegistry` + `image.repository` + `login.image.repository` — структура сохранена в 9.x ✓

#### 🔴 Breaking findings (актуальны только при апгрейде 8.x → 9.x)

| # | Ключ | Статус в 9.x | Описание | Действие перед апгрейдом |
|---|---|---|---|---|
| 1 | `zitadel.masterkeySecretKeyName` | **УДАЛЁН** | В 9.x ключ в secret захардкожен как `"masterkey"`. Поле убрано из values.yaml. | Удалить строку из `zitadel_helm_values` |
| 2 | `zitadel.initJob.additionalArgs` | **УДАЛЁН** | В 9.x `initJob` поддерживает только `enabled`, `annotations`, `resources`, `command` и пр. Поле `additionalArgs` удалено. Альтернатива — `setupJob.additionalArgs` (присутствует). | Проверить: нужны ли `--init-projections=true` через `setupJob.additionalArgs` |

#### ✅ Safe в 9.x (обратная совместимость)

Все остальные ключи присутствуют в 9.30.0: `imageRegistry`, `image.*`, `login.image.*`, `replicaCount`, `resources`, `tolerations`, `nodeSelector`, `affinity`, `zitadel.masterkeySecretName`, `zitadel.configmapConfig.*`, `zitadel.env`, `service.*`, `ingress.*`, `autoscaling.*`, `pdb.*`, `setupJob.additionalArgs` ✓

**Database config** (`MaxOpenConns`, `MaxIdleConns`, `ConnMaxLifetime`, `Admin/User.*`) — application-level поля, передаются через configmapConfig без изменений ✓

---

## Сводная таблица

| Компонент | Chart ver. match | Key-paths OK | Deprecated used | Breaking | Status |
|---|---|---|---|---|---|
| traefik | ✅ 39.0.5 | ✅ | — | — | ✅ |
| external-secrets | ✅ 2.3.0 | ✅ | — | — | ✅ |
| metrics-server | ✅ 3.13.0 | ✅ | — | — | ✅ |
| haproxy | ✅ 1.49.0 | ✅ | — | — | ✅ |
| longhorn | ✅ 1.11.1 | ✅ | — | — | ✅ |
| gitlab | ⚠️ 8.11.8←9.10.3 | ✅ | — | — | ✅ keys |
| cert-manager | ✅ 1.20.2 | ✅ | — | — | ✅ |
| mon-prometheus-op. | custom chart | ✅ | — | — | ✅ |
| cilium | ⚠️ 1.19.1←1.20dev | ✅ | — | — | ✅ keys |
| gitlab-runner | ⚠️ 0.78.0←0.80beta | ✅ | — | — | ✅ keys |
| teleport | templated ver. | ✅ | — | — | ✅ |
| vault (bank-vaults) | dev ver. 0.0.0 | ✅ | — | — | ✅ |
| zitadel | ⚠️ 8.12.0←9.30.0 | ✅ now / ⚠️ upgrade | — | 2 keys on upgrade | ⚠️ |

---

## Приоритизированный action-план

### 🔴 Critical (перед апгрейдом zitadel)

1. **Удалить `zitadel.masterkeySecretKeyName`** из `hosts-vars/zitadel.yaml`  
   — в 9.x ключ `masterkey` в secret захардкожен, поле убрано
2. **Проверить / перенести `zitadel.initJob.additionalArgs`** в `setupJob.additionalArgs`  
   — `--init-projections=true` нужно переместить или удалить

### 🔵 Style (по желанию, не блокирующие)

3. **traefik:** добавить `podSecurityContext.seccompProfile: {type: RuntimeDefault}` для соответствия upstream best practice
4. **longhorn:** обсудить `longhornUI.replicas: 1 → 2` и восстановить pod anti-affinity при replicas=2
5. **longhorn:** задокументировать `persistence.defaultClassReplicaCount: 2` как намеренное снижение (upstream=3)
6. **external-secrets / metrics-server:** опционально задать `securityContext` явно (сейчас полагаемся на chart defaults, они хорошие)

### ⚠️ Version drift (мониторинг)

7. **gitlab:** обновить sources до тега 8.11.8 или запланировать бамп pin → 9.10.3 с тестированием
8. **cilium:** при апгрейде до 1.20.x проверить `operator.image.suffix: ""` → image `operator-generic` pull
9. **gitlab-runner / zitadel:** дождаться stable release (runner 0.80.x), запланировать zitadel 9.x migration

---

*Проблема_3 (недостающие `_image_registry` для gitlab sub-components, gitlab-runner helper, teleport sub-parts) — вне scope этого аудита, отдельная итерация.*

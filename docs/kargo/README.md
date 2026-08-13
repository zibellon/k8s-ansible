# Kargo — versioned CD over ArgoCD (дизайн)

Исходники Kargo: `sources/kargo/`. Подробные планы: [per-cluster](model-per-cluster.md) · [central](model-central.md).

---

## Две модели размещения Kargo

Оценили два способа поставить Kargo. Оба рабочие; выбор — архитектурный

[Per-cluster](model-per-cluster.md)
- KARGO = по одной в каждом кластере (все стенды + все клиенты)
- В кластере клиента = ArgoCD + Kargo
- как идут стадии = registry-тег + git (Kargo независимы)
- Релиз = «рваный» (разные Kargo)
- Версия для клиента = per-client Warehouse `constraint`
- Polling registry = ×N (каждый клиентский Kargo)
- как обнаружить релиз = обязателен доп-тег


[Central](model-central.md)
- KARGO = ОДНА (management-кластер)
- В кластере клиента только ArgoCD
- как идут стадии = нативный Freight (один Kargo)
- Релиз = один Freight сквозь пайплайн (qa -> pre-prod -> prod)
- Версия для клиента = нативный per-client-Stage promote (функционал KARGO)
- Polling registry = дедуп (один Warehouse)
- как обнаружить релиз = доп-тег ИЛИ semverParse (оба работают)


В нашей модели (Kargo трогает только registry + git, в кластеры не ходит — `argocd-update` выключен)
аргумент «автономность» за per-cluster слабый: локальный Kargo автономности приложению не добавляет (runtime-путь = ArgoCD + git)
Поэтому central архитектурно лучше (релиз виден в Kargo-UI, единый UI, меньше запросов к registry, проще эксплуатация)
но концентрирует безопасность и масштаб в одном control plane

Обе модели могут работать


## Общие решения (для обеих моделей)

- Kargo НЕ деплоит
  - `controller.argocd.integrationEnabled: false`, шаг `argocd-update` не используется
  - Kargo мутирует git-ops (MR или прямым коммитом)
  - деплоит ArgoCD
  - Они не общаются (Kargo и ArgoCD)
- Deploy-ссылка = `tag + digest`: `registry/app:v1.3.0@sha256:Z`.
  - digest = истина (kubelet тянет по нему = ровно то, что тестировали)
  - тег — читаемость (в `kubectl` виден `v1.3.0`)
  - Тег в ссылке `не обязан существовать` в registry (доказано: `docker pull nginx:НЕСУЩ@sha256:...` работает)
  - `<none>` бывает только в `docker image ls` (локальный CLI), в k8s тег сохраняется в `.spec.containers[].image`
  - `tag-only отвергнут` (мутабельность, отвязывает Freight от контента) — кроме случая immutable-тегов в registry
- `git-ops = ArgoCD multi-source live values`
  - N репо с `values.yaml` накладываются (порядок массива = вес)
  - Helm-чарт лежит `plain в git` (без OCI)
  - Kargo пишет только пиновку образа (tag+digest) в values-файл
  - ArgoCD рендерит
- `Coherence «тот же образ + тот же конфиг, без пересборки»`
  - Digest переносится, никогда не пересобирается.
- `dev-трек` отдельный
  - `SHORT_SHA`-теги
  - `imageSelectionStrategy: NewestBuild`
  - едет только в dev (это стенд)
- `Промоушен на prod = MR`
  - `git-open-pr` → `git-wait-for-pr`
  - devops принимает/отклоняет
  - Kargo MR не закрывает (то есть - даже после аборта, MR остается висеть)
  - abort — `kargo promote --abort` вручную.
- **git-транспорт:** SSH сейчас, **но SSH удаляется в Kargo v1.13 для ВСЕХ git-шагов** → мигрировать на HTTPS scoped PAT (SSH оставить только людям/CI).
- **Rollback:** re-promote старого Freight; GC-retention (`maxRetainedFreight`) + registry/git-ретеншен ≥ глубины отката.

---

## Ключевые проверенные факты Kargo v1.11 (по исходникам)

| Факт | Где | Значение |
|---|---|---|
| Freight ID = `sha1(origin + tag@digest)` | `pkg/api/freight.go` | тот же digest под **новым тегом** = **новый Freight** |
| Freight не пересекает namespace/кластер | `stage_types.go:176` | `verifiedIn` — поле объекта; между отдельными Kargo не переносится |
| `verifiedIn` персистентен | `freight_types.go` | НЕ сбрасывается при выходе из стадии → каталог «прошедших» версий |
| Промоушены сериализованы per-Stage | `promotions.go:372` | один MR за раз; новые версии копятся `Pending` FIFO |
| `git-wait-for-pr` timeout | `local_orchestrator.go:322` | non-positive (`-1`, `0`, nil) = **ждать вечно** |
| `errorThreshold` | `promotion_types.go:274` | `uint32`, **`-1` нельзя**; `0` = дефолт 1; **счётчик НАКОПИТЕЛЬНЫЙ**, не «подряд» (`promotion.go:407`) → для вечного ожидания ставить `4294967295` |
| Лимит ошибок достигнут | `local_orchestrator.go:298` | Promotion → `Errored`; MR остаётся открытым; Freight не авто-ретраится (`regular_stages.go:1866`); очередь идёт дальше |
| `semverParse().Major/Minor/Patch()` | `functions.go:1151` | режет `-rcX`: `v1.3.0-rc5` → `v1.3.0` |
| `fromFreight:true` | — | **удалён в v1.3** (доки устарели) → `commitFrom().ID` |
| `SemVer` с пустым constraint | `semver_selector.go:77` | ловит пререлизы (rc); выбирает наибольший semver |
| `NewestBuild` | `newest_build_selector.go` | Freight на каждый новый билд (по времени сборки) — нужен для каталога версий |
| SSH git-URL | `warehouse_types.go:399` | deprecated v1.10, **removed v1.13** — все git-шаги на HTTPS |

---

## Промоушен-flow (детально, обе модели)

- `kargo promote` без `--wait` → **exit 0 сразу** (Promotion создан ≠ MR влит). Ожидание MR — серверное (`git-wait-for-pr`), наблюдаешь в UI.
- `--wait` → блокирует до терминала, НО exit 0 даже на `Failed` — проверяй фазу отдельно.
- **auto-без-`git-wait-for-pr` ОТВЕРГНУТ**: промоушен `Succeeded` на open-MR → Kargo-состояние врёт о том, что задеплоено.

Каноничный prod-шаг (MR, вечное ожидание):
```yaml
- uses: git-open-pr
  as: open-pr
  config: { repoURL: <gitops>, provider: gitlab, sourceBranch: '${{ outputs.push.branch }}', targetBranch: env/prod }
- uses: git-wait-for-pr
  retry: { timeout: -1, errorThreshold: 4294967295 }   # ждать вечно; терпеть моргания GitLab API
  config: { repoURL: <gitops>, provider: gitlab, prNumber: '${{ outputs[''open-pr''].pr.id }}', pollInterval: 5m }
```

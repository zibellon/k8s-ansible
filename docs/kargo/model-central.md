# Модель B — один центральный Kargo (central)

> Общие решения и проверенные факты — в [README](README.md). Здесь — специфика central + два варианта тега + конфиги.

## Принцип

**ОДИН** Kargo (в management-кластере). Он смотрит на **центральный registry** и пишет во **все** git-ops-репозитории (по стенду и по клиенту). В стендовых и клиентских кластерах — **только ArgoCD** (читает свой git-ops, деплоит). **Kargo и кластеры не общаются** (`argocd-update` выключен).

Ключевой факт: раз Kargo работает только с registry + git, **физическая топология кластеров ему невидима** — он видит только Project'ы, Stage'и и git-ops-репо. Поэтому один Kargo оркеструет весь флот **как один логический пайплайн**, хотя поды в 30 кластерах.

Следствие: **Freight течёт нативно** — «рваности» нет. `dev`/`qa`/`pre-prod`/`prod-<client>` — это Stage'и в одном Kargo; `verifiedIn` работает между ними; «тот же груз, что тестировали QA → prod» — буквально тот же объект.

```
     Central Registry + GitLab (AAA)
        ▲ discover      ▼ commit/MR (во все git-ops)
   ┌──────────────────────────────────────┐
   │   ЕДИНЫЙ Kargo (management-кластер)    │
   │  Warehouse → qa → pre-prod ─┬─ prod-vasya
   │  (Freight на каждую версию)  ├─ prod-petya
   │                              └─ prod-dima …
   └──────────────────────────────────────┘
        ▼ git-ops репо                    ▼
   [ArgoCD dev][ArgoCD qa]…[ArgoCD vasya][ArgoCD petya]…  (в кластерах ТОЛЬКО ArgoCD)
```

## Каталог версий + выбор конкретной

- **Freight на каждую версию:** релизный Warehouse — `imageSelectionStrategy: NewestBuild` (не `SemVer`: SemVer создаёт Freight только для наибольшей версии — v1.3.2 при живой v1.4.0 не получит Freight). NewestBuild даёт Freight на каждый новый билд.
  - ⚠️ Между поллингами промежуточный релиз может потеряться → **триггерить discovery на каждый релиз** (CI `kargo refresh warehouse`, либо registry-webhook на central external-webhooks-server).
- **«Список прошедших qa+pre-prod» = persistent `verifiedIn`.** `prod-<client>` с `sources.stages: [pre-prod]` считает доступными **ВСЕ** Freight с `verifiedIn[pre-prod]` (не только текущий). Оператор выбирает конкретную версию.

## Version skew — нативный, без constraint

`prod-vasya` на v1.4.0, `prod-petya` на v1.4.1, `prod-dima` на v1.3.2 — каждый `prod-<client>` Stage держит свой Freight, промоутится независимо. Просто выбираешь версию per Stage. `constraint` не нужен (это был костыль per-cluster).

---

## Два варианта тега (central — работают ОБА)

### Variant 1 — semverParse + digest
CI делает **только rc-теги**. Kargo при промоушене в клиентский Stage режет суффикс:
```yaml
- key: image.tag
  value: v${{ semverParse(imageFrom(vars.imageRepo).Tag).Major() }}.${{ semverParse(imageFrom(vars.imageRepo).Tag).Minor() }}.${{ semverParse(imageFrom(vars.imageRepo).Tag).Patch() }}
- key: image.digest
  value: '${{ imageFrom(vars.imageRepo).Digest }}'
```
`v1.3.0-rc5` → `v1.3.0`. Финальный тег в registry **не нужен** (у центрального Kargo уже есть Freight — discovery-канал не требуется). Деплой **обязательно tag+digest** (тега `v1.3.0` в registry нет — тянет digest).

### Variant 2 — дополнительный tag
CI на финализации вешает реальный тег `v1.3.0` на digest стабильного rc. Kargo читает его напрямую:
```yaml
- key: image.tag
  value: '${{ imageFrom(vars.imageRepo).Tag }}'      # = v1.3.0
- key: image.digest
  value: '${{ imageFrom(vars.imageRepo).Digest }}'
```
Плюс: явное человеческое действие «нарезать релиз», реальный тег в registry для гигиены. Деплой tag+digest (или tag-only при immutable-тегах).

---

## Конфиги (сценарий: my-casino, dev/qa/pre-prod по одному, prod = 20 клиентов, промоушен MANUAL)

```yaml
kind: Project
metadata: { name: my-casino }
---
# Freight на КАЖДУЮ релизную версию
kind: Warehouse
metadata: { name: release, namespace: my-casino }
spec:
  interval: 5m0s
  freightCreationPolicy: Automatic
  subscriptions:
  - image:
      repoURL: registry.aaa/my-casino
      imageSelectionStrategy: NewestBuild            # Freight per билд (каталог версий)
      allowTagsRegexes: ['^v[0-9]+\.[0-9]+\.[0-9]+']  # финалы (+ -rc для qa при желании)
      discoveryLimit: 50
---
kind: Stage                                          # qa — от Warehouse напрямую
metadata: { name: qa, namespace: my-casino }
spec:
  requestedFreight:
  - { origin: { kind: Warehouse, name: release }, sources: { direct: true } }
  promotionTemplate: { spec: { steps: [ '# git-clone → yaml-update → commit → push (прямо в git-ops/qa)' ] } }
---
kind: Stage                                          # pre-prod — только из qa
metadata: { name: pre-prod, namespace: my-casino }
spec:
  requestedFreight:
  - { origin: { kind: Warehouse, name: release }, sources: { stages: [qa] } }
  promotionTemplate: { spec: { steps: [ '# … прямой push в git-ops/pre-prod' ] } }
---
kind: Stage                                          # prod-vasya (1 из 20; копия, меняется gitopsRepo)
metadata: { name: prod-vasya, namespace: my-casino }
spec:
  requestedFreight:
  - { origin: { kind: Warehouse, name: release }, sources: { stages: [pre-prod] } }  # доступны все verifiedIn[pre-prod]
  promotionTemplate:
    spec:
      vars:
      - { name: gitopsRepo, value: https://gitlab.aaa/gitops/client-vasya.git }
      - { name: imageRepo,  value: registry.aaa/my-casino }
      steps:
      - uses: git-clone
        config: { repoURL: '${{ vars.gitopsRepo }}', checkout: [ { branch: env/prod, create: true, path: ./out } ] }
      - uses: yaml-update
        config:
          path: ./out/my-casino/image-values.yaml
          updates:
          # Variant 2 (доп-тег):
          - { key: image.tag,    value: '${{ imageFrom(vars.imageRepo).Tag }}' }
          # …или Variant 1 (semverParse) — заменить строку выше на v${{ semverParse(...).Major() }}.…
          - { key: image.digest, value: '${{ imageFrom(vars.imageRepo).Digest }}' }
      - uses: git-commit
        as: commit
        config: { path: ./out, message: 'client-vasya → ${{ imageFrom(vars.imageRepo).Tag }}' }
      - uses: git-push
        as: push
        config: { path: ./out, generateTargetBranch: true }
      - uses: git-open-pr
        as: open-pr
        config: { repoURL: '${{ vars.gitopsRepo }}', provider: gitlab, sourceBranch: '${{ outputs.push.branch }}', targetBranch: env/prod }
      - uses: git-wait-for-pr
        retry: { timeout: -1, errorThreshold: 4294967295 }
        config: { repoURL: '${{ vars.gitopsRepo }}', provider: gitlab, prNumber: '${{ outputs[''open-pr''].pr.id }}', pollInterval: 5m }
---
kind: ProjectConfig
metadata: { name: my-casino, namespace: my-casino }
spec:
  promotionPolicies:
  - { stageSelector: { name: qa }, autoPromotionEnabled: false }   # manual-подход; qa можно auto
  # pre-prod и все prod-* — без политики → строго ручной promote
```
Стенды `qa`/`pre-prod` — наши → прямой `git-push` (без MR). `prod-<client>` — MR (клиент принимает).

## Сценарий по шагам
1. Vasya на v1.3.0 → `prod-vasya.current` = Freight(v1.3.0).
2. Релизы v1.3.1, v1.4.0, v1.3.2, v1.4.1 → Warehouse создал 4 Freight (NewestBuild + refresh на каждый релиз).
3. Каждую версию промоутим `qa → pre-prod` → копится `verifiedIn[qa]`+`verifiedIn[pre-prod]` = **каталог**.
4. Решение «Vasya → v1.4.0»: `kargo promote --project my-casino --freight <v1.4.0-freight> --stage prod-vasya`.
5. Открывается MR в `git-ops/client-vasya` → принимаем → ArgoCD Vasya раскатывает v1.4.0.

```bash
kargo get freight --project my-casino     # каталог: tag + verifiedIn(qa/pre-prod)
kargo promote --project my-casino --freight <v1.4.0-freight> --stage prod-vasya
```

## 20 клиентов = шаблон
Stage'и `prod-<client>` идентичны кроме `gitopsRepo`. Генерировать tenant-Helm-чартом (параметр = клиент → repoURL), доставлять через ArgoCD ApplicationSet; общую MR-логику — в `ClusterPromotionTask`.

## Плюсы / минусы
- **+** нет «рваности» (нативный Freight), single pane of glass, дедуп polling, одна инсталляция, лёгкие клиентские кластеры (только ArgoCD), явный skew.
- **−** SPOF промоушена (control-plane; приложения живут на ArgoCD+git), концентрация git-креденшелов + registry-доступа (митигация: **MR-гейт на prod** — скомпрометированный Kargo может открыть MR, но не смёржить; per-Project RBAC; scoped-PAT), масштаб одного control plane → **шардинг контроллеров** внутри него.

## Что вернуло бы к per-cluster
Только **health-gated промоушен** (Kargo проверяет здоровье деплоя в кластере перед продвижением) — тогда нужен `argocd-update` + доступ к каждому ArgoCD (kubeconfig), и распределённые контроллеры оправданы. В нашей модели (гейт = человек мёржит MR) такой причины нет.

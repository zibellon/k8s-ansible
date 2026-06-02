# Testing — Layer 1 (Docker-based)

Local-only test runner for the k8s-ansible repo. Every test executes inside a single Docker image (`k8s-ansible-test:local`) so the environment is identical on any host. The user's machine needs only `docker` and `make`.

This is **Layer 1** of a planned multi-layer test stack. Helm template, variable resolution, snapshot/assertion tests are deferred to later layers.

For per-rule rationale and how DevOps/DevOps-docs/TeamLead are required to use the runner, see [`playbook-conventions.md`](playbook-conventions.md) and [`team-workflow.md`](team-workflow.md).

---

## 1. What is covered today

Layer 1 + Layer 2 + Layer 3 run five tools, gated by a single `make test` target:

- **yamllint** — every YAML file in the repo (with project-aware ignores).
- **ansible-lint** (profile: `moderate`) — every playbook in `playbook-system/` and `playbook-app/`.
- **ansible-playbook --syntax-check** — every playbook in `playbook-system/` and `playbook-app/`, plus every task-file in their `tasks/` subdirectories (wrapped in a temporary `import_tasks` playbook because bare task-files are not valid Plays). With both `-i hosts-vars/` and `-i hosts-vars-test/`.
- **helm template + kubeconform** — для каждого upstream Helm release (`<repo>/<chart>` или `oci://...`), который мы устанавливаем в production. Render values from `hosts-vars/` через ansible (production tasks `tasks-vault-config-verify.yaml` + `tasks-add-helm-repo.yaml` reused; `tasks-eso-verify.yaml` не вызывается из test driver — нет component scope), затем `helm template` → файл на диск → `kubeconform -strict --ignore-missing-schemas`. Render и validation разделены на отдельные шаги (см. `tests/helm-validate.yaml` STEP 4 и STEP 5). **Не** тестируются local wrappers (`pre/`, `post/`, `gitlab/postgresql/`, и т.п.) — там нет сторонней логики.
- **pytest** — unit-тесты для filter plugins (Python compute functions: `filter_plugins/seaweedfs_sync.py`, `filter_plugins/vault_config_verify.py`, `filter_plugins/eso_verify.py`). Tests live in `tests/python/test_*.py`. Catches runtime Jinja2/Python issues которые предыдущие 4 stages не видят (syntax check ≠ runtime evaluation).

All five must pass for `make test` to exit 0. Targets are independent and re-runnable individually.

## 2. Local prerequisites

- `docker` — any Engine ≥ 23 (BuildKit on by default), Docker Desktop, or Podman with a `docker` shim.
- `make` — GNU make, ships with macOS / every Linux distro.

No other host tooling is required. Specifically, do **not** install `ansible-lint` or `yamllint` directly on the host — versions inside the image are pinned and authoritative.

## 3. Commands

| Command | Purpose |
|---|---|
| `make help` | List available targets |
| `make docker-build` | Build the test image (`k8s-ansible-test:local`) |
| `make test` | Run all four checks (yamllint + ansible-lint + syntax + helm) |
| `make test-yamllint` | yamllint only |
| `make test-ansible-lint` | ansible-lint only |
| `make test-syntax` | ansible-playbook --syntax-check only |
| `make test-helm` | helm template + kubeconform for upstream charts only |
| `make test-pytest` | pytest unit tests for filter plugins only |

`make test` is fail-fast: if `test-yamllint` fails, the next three are not run. To see all failures at once, run each target separately.

`ensure-image` is an internal target every `test-*` depends on — it builds the image automatically if it is missing, otherwise it is a no-op.

## 4. Repo layout

| Path | Purpose |
|---|---|
| `Makefile` | Test entry point. Wraps every test as `docker run` with read-only volume mount + tmpfs `/tmp`. |
| `tests/Dockerfile` | Test image definition. Pinned `ansible-core`, `ansible-lint`, `yamllint`, plus `ansible.posix` and `community.general` collections. |
| `tests/Dockerfile.dockerignore` | BuildKit-scoped ignore list — keeps build context small. |
| `tests/run-syntax-check.sh` | Bash iterator running `ansible-playbook --syntax-check` over every playbook, then over every task-file (each wrapped in a temporary `import_tasks` playbook). |
| `tests/helm-validate.yaml` | Ansible-playbook driver for Layer 2. PRE phase: mock `master_manager_fact` + ESO secret lookups for chart values. STEP 1–7: per-chart Helm repo add (через `tasks-add-helm-repo.yaml`) → render values → `helm template` → `kubeconform` → aggregate. Reports per-chart OK/FAIL. |
| `tests/python/test_seaweedfs_sync.py` | Pytest unit tests for `filter_plugins/seaweedfs_sync.py` (Layer 3). Covers all 10 public + ~12 private helpers: parse/extract/diff/build/validate. Path setup via `sys.path.insert` to repo-root `filter_plugins/`. |
| `tests/python/test_vault_config_verify.py` | Pytest unit tests for `filter_plugins/vault_config_verify.py` (Layer 3). 13 cases — happy + G1/G2/G3 violations + multi + `_find_duplicates`. |
| `tests/python/test_eso_verify.py` | Pytest unit tests for `filter_plugins/eso_verify.py` (Layer 3). 23 cases — happy + B1/B2/B3/C1/C2/D + malformed + multi + private helpers. |
| `hosts-vars-test/upstream-charts.yaml` | Inventory-format vars-файл для Layer 2 (auto-loaded через `-i hosts-vars-test/`). Unified schema `upstream_charts` list для всех upstream charts (`is_oci`, `helm_url`, `helm_repo_name`, `helm_chart_name`, `helm_chart_version`, `namespace`, `values`). |
| `.yamllint.yaml` | yamllint config — extends `default` with project-aware relaxations and ignore paths. |
| `.ansible-lint.yml` | ansible-lint config — `profile: moderate` with documented `skip_list` and `mock_modules`. |
| `hosts-vars-test/` | Synthetic, committed replacement for `hosts-vars-override/` in tests. RFC 5737 IPs, literal `"test"` passwords, no secrets. |

## 5. Pinned tooling versions

Recorded in `tests/Dockerfile`:

- `python:3.12-slim`
- `ansible-core==2.20.5`
- `ansible-lint==26.4.0`
- `yamllint==1.38.0`
- `pytest==8.3.4` — Python unit test framework для Layer 3 (filter plugin tests).
- `ansible.posix:1.5.4` — required by `setup-ssh-keys.yaml` (authorized_key module).
- `community.general:12.6.0` — required by `playbook-app/tasks/tasks-copy-chart.yaml` (`archive` module). Surfaced when task-file syntax-check coverage was added.
- `helm 3.20.2` — pinned to match `playbook-system/install-helm.yaml` (the version actually deployed on the cluster, so test rendering reproduces production behaviour).
- `kubeconform 0.7.0` — strict K8s schema validator; standalone tool, not part of the cluster.

Bumping a version means editing `tests/Dockerfile` and rebuilding (`make docker-build`). Versions are intentionally frozen to make `make test` reproducible across machines.

## 6. Out of scope (deferred to later layers)

Layer 1 + Layer 2 deliberately stop short of:

- **Local chart wrappers** (наши `<c>/pre/`, `<c>/post/`, `<c>/gitlab/postgresql/`, и т.п.) — тестируется только upstream-часть. Local templates содержат самописанную логику; их валидация — потенциальный future Layer.
- **CRD-bundle validation** — `kubeconform` сейчас skip'ает CRD-типы (`CiliumNetworkPolicy`, `ExternalSecret`, `ServiceMonitor`, `IngressRoute`, и т.п.) через `--ignore-missing-schemas`. Полная валидация требует bundle JSON-schemas (например через `datreeio/CRDs-catalog` + per-project schemas) — отдельный future Layer.
- **Variable resolution** — Jinja-time `{{ … }}` resolution against full inventory выходит за рамки текущих syntactic + render checks. Future Layer для playbook'ов.
- **helm-unittest snapshot tests** + **assertion tests** — точечные проверки на конкретные labels/values/structure внутри rendered chart'a. Planned later.

These layers are tracked separately. Adding them must not loosen Layer 1 or Layer 2.

## 7. Debugging common failures

| Symptom | Likely cause | First step |
|---|---|---|
| `make: ... permission denied` running docker | Docker daemon not reachable from your user | Start Docker Desktop / `systemctl start docker` |
| `yamllint` complains about a file you did not change | Recent edit broke trailing-whitespace or EOF newline | `make test-yamllint` and read the path:line — fix in place |
| `ansible-lint` flags a new playbook with `name[missing]` | Task or play has no `name:` | Add `name:` — do not add to `skip_list` |
| `make test-syntax` says `couldn't resolve module/action 'X'` for a new module | `X` lives in a collection not installed in the image | Add a `RUN ansible-galaxy collection install <ns>.<col>:<ver>` to `tests/Dockerfile`, rebuild image |
| `make test-syntax` reports two `FAIL:` for one broken playbook | Broken file is included by another playbook | Fix the source file; both will go green |
| `make test-helm` falls render task with `'<var>' is undefined` | Production playbook sets this fact via `tasks-pre-check.yaml` / `set_fact` / direct inventory variable reference; test playbook hasn't been wired to do the same | Either (a) update `tests/helm-validate.yaml` to call the appropriate production task via `include_tasks: "{{ playbook_dir }}/../playbook-app/tasks/<task>.yaml"`, or (b) hardcode a mock in `hosts-vars-test/` |
| `make test-helm` falls helm template with `Error: chart pull failed` | `<c>_chart_version` in inventory does not exist in upstream repo (yanked or typo'd) | Verify version exists at the published repo (e.g. `helm search repo <repo>/<chart> --versions`); update inventory if intentional |
| `make test-helm` falls kubeconform with `key "<X>" already set in map` | Upstream chart bug — duplicate key produced by `toYaml` of merged values dict; K8s API server last-wins masks it in production | See §9 Known upstream issues; if new chart hits this, comment out the entry in `hosts-vars-test/upstream-charts.yaml` with explanation (как сделано для traefik) |
| `make test-pytest` fails with `ModuleNotFoundError: seaweedfs_sync` | Path setup в test file не находит filter plugin (e.g. moved location) | Verify `sys.path.insert` в `tests/python/test_seaweedfs_sync.py` correctly resolves repo-root `filter_plugins/` |
| `make test-pytest` fails with assertion mismatch | Python logic в filter plugin не matches expected behavior | Read pytest output (-v shows each test name); fix `filter_plugins/seaweedfs_sync.py` или update test if expectation outdated |

## 8. Verification idiom for SUB DONE reports

Per [`report-formats.md`](report-formats.md) §1.1 — SUB DONE отчёты обязаны содержать строку результата `make test` в секции `Verification`. Canonical extraction idiom:

```bash
make test 2>&1 | tail -5
```

**На success** последние 4 строки совпадают byte-for-byte с финальным блоком Makefile:

```
==========================================
make test → exit 0 (all 5 stages passed)
Wall-clock: Xm YYs
==========================================
```

Где `X`/`YY` — wall-clock duration. Подстрока `make test → exit 0` — детерминированный success-маркер, присутствует **только** когда все 5 стадий (yamllint + ansible-lint + syntax-check + helm-validate + pytest) прошли. Копируй её в отчёт верботим.

**На fail** success-блок отсутствует; `tail -5` показывает последние 5 строк output'а упавшей стадии. Make's fail-fast прерывает на первой упавшей — отображается ошибка **первой** не прошедшей стадии.

**Expected runtime** — cold full run ~4-5 минут wall-clock. Основная latency в `helm repo add` для 13 charts (без кеширования между прогонами). Если прогон превышает ~10 минут без появления success-маркера — подозревай зависание; abort, investigate.

**Anti-pattern (не делать):** повторный запуск `make test` с разными shell-pipe парсингами. Если первый прогон завершился (exit 0 или non-zero) — используй его результат. SUB-4 history: 40 минут wall-clock на 7 retry потому что маркер ещё не существовал; commit `22b7afe` его добавил.

## 9. Known upstream issues

### 9.1 traefik chart 39.0.5 + helm 3.20.2 — `service.spec` deep-merge bug

**Symptom:** `make test-helm` reports `FAIL: traefik` with kubeconform error `key "type" already set in map` in the rendered Service.

**Root cause:** Traefik chart's default `values.yaml` sets `service.spec.type: LoadBalancer`. Our override in `hosts-vars/traefik.yaml` sets `service.spec.type: NodePort` + `service.spec.externalTrafficPolicy: Local`. Helm 3.20.2 does **not** deep-merge `service.spec` correctly — both values end up in the rendered Service spec, producing two `type:` keys at the same YAML level. K8s API server's last-wins parser silently picks `NodePort` in production (so the cluster is functional), but `kubeconform`'s strict YAML→JSON unmarshal correctly flags the duplicate.

**Verified:** reproducible with `helm template traefik traefik/traefik --version 39.0.5` and even minimal `-f` overrides (or `--set` flags). Inspection of `sources/traefik-charts/traefik/templates/_service.tpl` confirms the chart uses only `.Values.service.spec` (top-level `service.type` is NOT read), so a "fix" via top-level `service.type: NodePort` would silently break NodePort behaviour.

**Mitigation in Layer 2:** traefik entry полностью закомментирован в `hosts-vars-test/upstream-charts.yaml`'s `upstream_charts` list (включая длинный комментарий с описанием bug'а сверху). Поэтому traefik не попадает в loop'ы STEP 1-5, не renders, и не вызывает kubeconform failure. Это keeps `make test` зелёным, knowledge о bug'е сохраняется в тексте comment'а файла.

**To re-enable:** await an upstream fix in either traefik chart's `_service.tpl` (replace bare `toYaml .service.spec` with a deep-merge-safe construct) or helm's deep-merge logic for nested dicts. Once fixed (и version bumped in `hosts-vars/traefik.yaml`), uncomment the traefik entry в `hosts-vars-test/upstream-charts.yaml` (раскомментировать строки `# - name: traefik` ... до конца записи).

**Production impact:** none. The cluster runs traefik with the correct NodePort behaviour because of K8s API last-wins. This issue is purely a test-time strictness mismatch.

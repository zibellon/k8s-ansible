# Testing — Layer 1 (Docker-based)

Local-only test runner for the k8s-ansible repo. Every test executes inside a single Docker image (`k8s-ansible-test:local`) so the environment is identical on any host. The user's machine needs only `docker` and `make`.

This is **Layer 1** of a planned multi-layer test stack. Helm template, variable resolution, snapshot/assertion tests are deferred to later layers.

For per-rule rationale and how DevOps/DevOps-docs/TeamLead are required to use the runner, see [`playbook-conventions.md`](playbook-conventions.md) and [`team-workflow.md`](team-workflow.md).

---

## 1. What is covered today

Layer 1 runs three tools, gated by a single `make test` target:

- **yamllint** — every YAML file in the repo (with project-aware ignores).
- **ansible-lint** (profile: `moderate`) — every playbook in `playbook-system/` and `playbook-app/`.
- **ansible-playbook --syntax-check** — every playbook in `playbook-system/` and `playbook-app/`, with both `-i hosts-vars/` and `-i hosts-vars-test/`.

All three must pass for `make test` to exit 0. Targets are independent and re-runnable individually.

## 2. Local prerequisites

- `docker` — any Engine ≥ 23 (BuildKit on by default), Docker Desktop, or Podman with a `docker` shim.
- `make` — GNU make, ships with macOS / every Linux distro.

No other host tooling is required. Specifically, do **not** install `ansible-lint` or `yamllint` directly on the host — versions inside the image are pinned and authoritative.

## 3. Commands

| Command | Purpose |
|---|---|
| `make help` | List available targets |
| `make docker-build` | Build the test image (`k8s-ansible-test:local`) |
| `make test` | Run all three checks (yamllint + ansible-lint + syntax) |
| `make test-yamllint` | yamllint only |
| `make test-ansible-lint` | ansible-lint only |
| `make test-syntax` | ansible-playbook --syntax-check only |

`make test` is fail-fast: if `test-yamllint` fails, the next two are not run. To see all failures at once, run each target separately.

`ensure-image` is an internal target every `test-*` depends on — it builds the image automatically if it is missing, otherwise it is a no-op.

## 4. Repo layout

| Path | Purpose |
|---|---|
| `Makefile` | Test entry point. Wraps every test as `docker run` with read-only volume mount + tmpfs `/tmp`. |
| `tests/Dockerfile` | Test image definition. Pinned `ansible-core`, `ansible-lint`, `yamllint`, plus `ansible.posix` collection. |
| `tests/Dockerfile.dockerignore` | BuildKit-scoped ignore list — keeps build context small. |
| `tests/run-syntax-check.sh` | Bash iterator running `ansible-playbook --syntax-check` over every playbook. |
| `.yamllint.yaml` | yamllint config — extends `default` with project-aware relaxations and ignore paths. |
| `.ansible-lint.yml` | ansible-lint config — `profile: moderate` with documented `skip_list` and `mock_modules`. |
| `hosts-vars-test/` | Synthetic, committed replacement for `hosts-vars-override/` in tests. RFC 5737 IPs, literal `"test"` passwords, no secrets. |

## 5. Pinned tooling versions

Recorded in `tests/Dockerfile`:

- `python:3.12-slim`
- `ansible-core==2.20.5`
- `ansible-lint==26.4.0`
- `yamllint==1.38.0`
- `ansible.posix:1.5.4` — required by `setup-ssh-keys.yaml` (authorized_key module).

Bumping a version means editing `tests/Dockerfile` and rebuilding (`make docker-build`). Versions are intentionally frozen to make `make test` reproducible across machines.

## 6. Out of scope (deferred to later layers)

Layer 1 deliberately stops short of:

- **Helm template + kubeconform** — Helm Go templates are excluded from yamllint/ansible-lint here; rendering and K8s schema validation are a planned layer.
- **Variable resolution** — Jinja-time `{{ … }}` resolution against full inventory is not exercised; only YAML/syntactic correctness is checked.
- **helm-unittest snapshot tests** and **assertion tests** — planned layers.

These layers are tracked separately. Adding them must not loosen Layer 1.

## 7. Debugging common failures

| Symptom | Likely cause | First step |
|---|---|---|
| `make: ... permission denied` running docker | Docker daemon not reachable from your user | Start Docker Desktop / `systemctl start docker` |
| `yamllint` complains about a file you did not change | Recent edit broke trailing-whitespace or EOF newline | `make test-yamllint` and read the path:line — fix in place |
| `ansible-lint` flags a new playbook with `name[missing]` | Task or play has no `name:` | Add `name:` — do not add to `skip_list` |
| `make test-syntax` says `couldn't resolve module/action 'X'` for a new module | `X` lives in a collection not installed in the image | Add a `RUN ansible-galaxy collection install <ns>.<col>:<ver>` to `tests/Dockerfile`, rebuild image |
| `make test-syntax` reports two `FAIL:` for one broken playbook | Broken file is included by another playbook | Fix the source file; both will go green |

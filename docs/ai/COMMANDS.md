> Actual for commit: a451e66

# COMMANDS — k8s-ansible (1520-tech-ansible)

All test targets run inside the pinned Docker image (`k8s-ansible-test:local`, rebuilt on every run via `ensure-image` — the repo is baked into the image, so edits are never validated stale). Linux/amd64 image; all tool versions pinned in `tests/Dockerfile`.

## Make targets

### `make test`
- Purpose: run all 5 test stages (yamllint + ansible-lint + syntax-check + helm + pytest) and print wall-clock duration.
- Command: `make test`
- Expected: exit 0; final line `make test → exit 0 (all 5 stages passed)`.
- Verification: verified via `make -n` dry-run at bootstrap; full Docker execution not performed.

### `make test-yamllint`
- Purpose: yamllint over all repo YAML.
- Command: `make test-yamllint` — runs `yamllint -c .yamllint.yaml .` in the image. Config ignores `sources/ docs/ proxmox/ hosts-vars-override/ .git/ .claude/`, chart `templates/`, 5 CRD dirs; line-length 200 (warning); `truthy.check-keys: false`.
- Expected: exit 0.
- Verification: verified via `make -n` dry-run at bootstrap; full Docker execution not performed.

### `make test-ansible-lint`
- Purpose: ansible-lint over both playbook halves.
- Command: `make test-ansible-lint` — runs `ansible-lint -c .ansible-lint.yml --offline playbook-system/ playbook-app/` in the image. Profile `moderate`; excludes charts + `hosts-vars-test/`.
- Expected: exit 0.
- Verification: verified via `make -n` dry-run at bootstrap; full Docker execution not performed.

### `make test-syntax`
- Purpose: `ansible-playbook --syntax-check` for every playbook and task include.
- Command: `make test-syntax` — runs `bash tests/run-syntax-check.sh` in the image: two cycles with `-i hosts-vars/ -i hosts-vars-test/` — all playbooks, then all task files wrapped in import_tasks plays (`tests/run-syntax-check.sh:1-20`).
- Expected: exit 0.
- Verification: verified via `make -n` dry-run at bootstrap; full Docker execution not performed.

### `make test-helm`
- Purpose: render upstream chart values → `helm template` → `kubeconform` (strict K8s schema validation). Pulls HTTP helm repos.
- Command: `make test-helm` — runs `ansible-playbook -i hosts-vars/ -i hosts-vars-test/ tests/helm-validate.yaml` in the image. Traefik validation disabled with documented upstream-bug rationale (`hosts-vars-test/upstream-charts.yaml:63-79`).
- Expected: exit 0.
- Verification: verified via `make -n` dry-run at bootstrap; full Docker execution not performed.

### `make test-pytest`
- Purpose: unit tests for the 11 filter plugins.
- Command: `make test-pytest` — runs `pytest tests/python/ -v -p no:cacheprovider` in the image (12 test files).
- Expected: exit 0, all tests passed.
- Verification: verified via `make -n` dry-run at bootstrap; full Docker execution not performed.

### `make docker-build` (and `ensure-image`)
- Purpose: build the test image; `ensure-image` is the quiet rebuild every test target depends on (repo baked in via `COPY . /repo`).
- Command: `make docker-build` — `docker build -t k8s-ansible-test:local -f tests/Dockerfile .`
- Expected: image built, exit 0.
- Verification: verified via `make -n` dry-run at bootstrap; full Docker execution not performed.

## Operator-only (agents must not run without explicit instruction)

Canonical deployment invocation:

```
ansible-playbook -i hosts-vars/ -i hosts-vars-override/<cluster>/ playbook-app/<component>-install.yaml [--tags pre|install|cfg|post]
```

Always both inventories; `<cluster>` = override subdir (e.g. `1520-tech-prod-1`). Install order and single-phase re-runs: [`reference/commands-reference.md`](../../reference/commands-reference.md).

Preview of a node-prep run before the real one (changes nothing; shows diffs of managed files and whether a reboot would happen — `...-reboot-cluster] Reboot non-bastion hosts first (parallel)` = `changed` → would reboot, `skipping` → no):

```
ansible-playbook -i hosts-vars/ -i hosts-vars-override/<cluster>/ playbook-system/prepare-linux-pkgs.yaml --check --diff [--limit <host>]
```

Bastion SSH is VPN-only on prod: run `bastion-proxy-install.yaml` from the VPN. Reopen SSH:

```
# from the VPN, instant (until the next node-install run):
ansible -i hosts-vars/ -i hosts-vars-override/<cluster>/ bastion_proxy -b -m systemd -a 'name=bastion-ssh-firewall state=stopped'
# from the VPN, via the playbook (add --start-at-task "[bastion-proxy-node] Configure fail2ban sshd jail" while an apt repo is unreachable):
ansible-playbook -i hosts-vars/ -i hosts-vars-override/<cluster>/ playbook-system/bastion-proxy-install.yaml --tags node-install -e bastion_proxy_haproxy_ssh_vpn_only_enabled=false
# without the VPN: OVH console, then (stop = until reboot; disable --now = survives reboot)
sudo systemctl disable --now bastion-ssh-firewall
```

Durable: set the flag back to `false` in the override.

Node SSH is VPN-only on prod too (`cilium_post_ssh_vpn_only_enabled: true`): run every prod playbook from the VPN. Reopen node SSH:

```
# from the VPN:
ansible-playbook -i hosts-vars/ -i hosts-vars-override/<cluster>/ playbook-app/cilium-install.yaml --tags post -e cilium_post_ssh_vpn_only_enabled=false
# without the VPN: Teleport (tsh ssh) or OVH console on the manager; pick a revision N whose
# values have sshVpnOnlyEnabled: false (helm get values cilium-post -n cilium --revision N), then
sudo helm --kubeconfig /etc/kubernetes/admin.conf rollback cilium-post <N> -n cilium
# never delete the CCNP as a shortcut: that opens every host port (etcd/apiserver/kubelet) to world
```

## Forbidden operations

- `ansible-playbook` against any real inventory (`hosts-vars-override/**`) — operator instruction only.
- `kubectl` / `helm` against the real cluster.
- Reading values under `hosts-vars-override/**`.
- Editing `docs/`, `sources/`, `readme-*.md`, `todo.md`.
- Any `git push` or history rewrite.

## Workstation

No hard version pins required on the operator workstation. Reference set = `tests/Dockerfile` pins: python:3.12-slim, `ansible-core==2.20.5`, `ansible-lint==26.4.0`, `yamllint==1.38.0`, `pytest==8.3.4`, `passlib==1.7.4`, `bcrypt==4.0.1`, collections `ansible.posix:1.5.4` + `community.general:12.6.0`, helm 3.21.1, kubeconform 0.7.0.

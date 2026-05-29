# Secrets & ESO Deep Dive

Depth reference for the Vault + External Secrets Operator subsystem. For the big picture, see `CLAUDE.md` §6. For the individual Vault/ESO task includes, see [`reusable-tasks.md`](reusable-tasks.md) §1.11–§1.15. For which components are ESO-integrated, see [`components.md`](components.md) §25.

---

## 1. Topology

```
                            ┌─────────────────────────────────────┐
                            │  bank-vaults operator (vault ns)    │
                            │  - installs & manages Vault CR      │
                            │  - declarative policies/auth/roles  │
                            │  - auto-unseal CronJob (managers)   │
                            └──────────────┬──────────────────────┘
                                           │ reconciles
                                           ▼
                                 ┌───────────────────┐
                                 │  Vault pod (single)│   Raft/Integrated storage
                                 │  Shamir 3/2        │   PVC: lh-major-single-best-effort
                                 │  Auth: kubernetes  │
                                 └─────────┬──────────┘
                                           │
                        ┌──────────────────┴────────────────────┐
                        │                                        │
                  KV v2: secret/                          KV v2: eso-secret/
               (human/admin — vault-admin policy)    (ESO-consumable, per-component read-only)
                        │                                        │
                        │                                        ▼
                        │                           ┌─────────────────────────┐
                        │                           │ ExternalSecretsOperator │
                        │                           │ (external-secrets ns)   │
                        │                           └────────────┬────────────┘
                        │                                        │
                        │                         reconciles SecretStore + ExternalSecret
                        │                                        │
                        ▼                                        ▼
       ad-hoc reads by admins               per-component K8s Secret objects
       (vault-rotate, vault-configure,      in each component namespace
        manual CLI, UI)                     (mounted as env/volumes by workloads)
```

Key guarantees:

- There is exactly one Vault pod (single-node Raft). The unseal shares live encrypted at rest in `vault-unsealer-secret` in the `vault` namespace, and in plaintext at `/etc/kubernetes/vault-unseal.json` on every manager (mode 0600). New managers receive the file via `tasks-vault-distribute-creds.yaml` during join.
- ESO has cluster-wide scope. Each ESO-integrated component owns a `SecretStore` CR in its own namespace and one or more `ExternalSecret` CRs.
- Only the `eso-secret/` KV engine is exposed to ESO policies. The `secret/` engine is for humans.

---

## 2. Inventory Contracts

### 2.1 `vault_policies` (list of dicts)

Each entry defines a Vault ACL policy:

```yaml
vault_policies:
  - name: "eso-argocd"
    rules: |
      path "eso-secret/data/argocd/*"  { capabilities = ["read"] }
      path "eso-secret/metadata/argocd/*" { capabilities = ["read"] }
```

Extension: `vault_policies_extra` (overrides can append policies without forking base).

### 2.2 `vault_roles` (list of dicts)

Each entry binds a Kubernetes ServiceAccount + namespace to one or more policies via the `kubernetes` auth method:

```yaml
vault_roles:
  - name: "eso-argocd"
    bound_service_account_names: "argocd-eso-sa"
    bound_service_account_namespaces: "argocd"
    policies:
      - "eso-argocd"
    ttl: "1h"
```

Extension: `vault_roles_extra`.

### 2.3 `eso_vault_integration_<c>` (object, per component)

Lives **exclusively** in `hosts-vars/<c>.yaml` (per-component file). The former `hosts-vars/vault-eso.yaml` was removed in SUB-7 — all 8 ESO integration blocks now live alongside the rest of each component's configuration.

```yaml
eso_vault_integration_<c>:
  sa_name: "eso-main"                # constant — same SA name in every namespace
  role_name: "<c>.eso-main"          # `<namespace>.eso-main` — must exist in merged `vault_roles + vault_roles_extra`
  secret_store_name: "eso-main.vault"  # constant — same SecretStore name in every namespace
  kv_engine_path: "eso-secret"       # referenced via Jinja in _secrets entries
```

### 2.4 `eso_vault_integration_<c>_secrets` (list, per component)

Also lives in `hosts-vars/<c>.yaml`. Each **base secret** is a separate top-level dict-variable `<c>_secret_<logical>` containing the full ExternalSecret structure. The array `eso_vault_integration_<c>_secrets` is a list of Jinja-string-references to these named dict-variables. `_extra` remains a list of full dict-items (operator extension via `hosts-vars-override/`).

**Canonical field set:**

| Field | Required | Purpose |
|---|---|---|
| `external_secret_name` | yes | `metadata.name` of the `ExternalSecret` CR. Conventionally matches `body.target.name`. |
| `vault_path` | yes | Path in Vault (without `kv_engine_path` prefix). Used directly by playbooks via `<c>_secret_<logical>.vault_path` for `tasks-vault-put`/`tasks-vault-get`. |
| `body` | yes | Full `spec` content starting from `target:`. Passed to the chart as `toYaml $secret.body`. Contains `target.name`, and either `dataFrom` (simple extract) or `data` (field remapping) or `target.template.*` (ESO template rendering). |
| `is_need_eso` | no | If `false`, the chart skips this entry entirely. |
| `refresh_interval` | no | Overrides the chart-level default from `esoResourcesConfig.externalSecretRefreshInterval`. |

**Minimal flat example (simple `dataFrom.extract`):**

```yaml
# Каждый base-секрет — отдельная top-level переменная
<c>_secret_admin:
  external_secret_name: "eso-<c>-admin"
  vault_path: "/<c>/admin"
  body:
    target:
      name: "eso-<c>-admin"
    dataFrom:
      - extract:
          key: "{{ eso_vault_integration_<c>.kv_engine_path }}/data/<c>/admin"

# Массив — список ссылок на named-переменные
eso_vault_integration_<c>_secrets:
  - "{{ <c>_secret_admin }}"
```

**Complex example (ESO template rendering for multi-field secret):**

```yaml
gitlab_secret_registry_connection_config:
  external_secret_name: "eso-gitlab-registry-connection-config"
  vault_path: "/gitlab/minio/registry"
  body:
    target:
      name: "eso-gitlab-registry-connection-config"
      template:
        engineVersion: v2
        data:
          config: |
            s3:
              bucket: registry
              accesskey: {% raw %}{{ .access_key }}{% endraw +%}
              secretkey: {% raw %}{{ .secret_key }}{% endraw +%}
              regionendpoint: https://minio.example.com
              region: us-east-1
              v4auth: true
    data:
      - secretKey: access_key
        remoteRef:
          key: "{{ eso_vault_integration_gitlab.kv_engine_path }}/data/gitlab/minio/registry"
          property: access_key
      - secretKey: secret_key
        remoteRef:
          key: "{{ eso_vault_integration_gitlab.kv_engine_path }}/data/gitlab/minio/registry"
          property: secret_key
```

**`{% raw %}{% endraw +%}` rule:** ESO template placeholders like `{{ .access_key }}` inside `body` values must be wrapped in `{% raw %}...{% endraw +%}` to prevent Ansible/Jinja2 from interpreting them during inventory loading. The `+%}` modifier is **required** when the closing tag sits inside a multi-line block scalar (e.g. `data: connection: |`): without it, Ansible's `trim_blocks=True` strips the newline after `{% endraw %}` and the next inventory line gets glued onto the same line, producing invalid YAML in the rendered K8s Secret. Inline form `key: "{% raw %}{{ .x }}{% endraw %}"` (closing tag followed by `"` before the newline) does NOT need `+%}` because the quote guards the newline. Affects: `gitlab` (registry_connection_config, backup_s3_connection_config — `+%}` required) and `gitlab-runner` (runner_token — inline quoted form, plain `{% endraw %}` is fine).

**Special ArgoCD `body.target.template.metadata.labels`:** Git-ops repo credentials require ArgoCD-specific labels so that ArgoCD recognises them as repository credentials:
- Pattern credentials (wildcard URL match): `argocd.argoproj.io/secret-type: repo-creds`
- Direct credentials (exact URL): `argocd.argoproj.io/secret-type: repository`

These are expressed entirely within `body.target.template.metadata.labels` — the chart template does not need to know about them.

**`_extra` entries** follow the same field schema as named dict-variables (full dict with `external_secret_name`, `vault_path`, `body`, etc.) but are written inline in `hosts-vars-override/` — no top-level named variable; extension for operators.

Extension: `eso_vault_integration_<c>_secrets_extra`.

Inline merge at usage sites: `<c>_pre_helm_values.eso.secrets: "{{ eso_vault_integration_<c>_secrets + (eso_vault_integration_<c>_secrets_extra | default([])) }}"` — no runtime fact, expression resolved by Ansible Jinja at render time.

### 2.5 Secret field-name variables (`<c>_<secret>_secret_key_<field>`)

Every Vault secret written by a playbook via `tasks-vault-put` has its **field names** parametrized as inventory variables `<c>_<secret>_secret_key_<field>` in `hosts-vars/<c>.yaml` (default = the literal field name). The variable is the single source of truth for that field name and is used everywhere it appears:

- `dto_vault_put_data` dict **keys** (Vault write);
- `dto_vault_get_field` (Vault read-back);
- `body.data[].remoteRef.property` in ESO `data[]`-secrets;
- the consumer chart `secretKeyRef.key` / helm-value, for `dataFrom.extract`-secrets where the K8s Secret key equals the Vault field name (e.g. `gitlab_redis_helm_values.credentialsSecretKey`, `mon_system_grafana_helm_values.adminSecret.usernameKey`).

**`dto_vault_put_data` must be a single Jinja dict expression.** Ansible does not template the keys of a nested YAML dict — a `dto_vault_put_data:` block with `"{{ var }}": ...` entries writes the literal `{{ var }}` string as the Vault field name. Build it as one `{{ { var1: val1, var2: val2 } }}` expression so the keys are evaluated (reference: the `Prepare PostgreSQL Vault put data` task in `gitlab-install.yaml`).

**Out of this convention** — names fixed by upstream charts stay literal: ESO `body.data[].secretKey`, the `{% raw %}{{ .field }}{% endraw %}` references inside `body.target.template`, and K8s secret keys hard-coded by upstream charts. The `<c>_<secret>_secret_key_<field>` variable still exists for those, with its default pinned to the required literal, so the put/get sides stay consistent.

---

## 3. Verify Tasks — Contracts

The former monolithic `tasks-eso-merge.yaml` was split in SUB-1; the resulting two merge tasks (`tasks-vault-policies-roles-merge.yaml`, `tasks-eso-secrets-merge.yaml`) were further refactored in ESO refactor v2 into **pure validation** tasks without `set_fact`. Inline merge `base + (extra | default([]))` is performed directly at usage sites (in `<c>_pre_helm_values.eso.secrets`, `hosts-vars/vault.yaml` `vault_spec.externalConfig`). Full contracts in [`reusable-tasks.md`](reusable-tasks.md) §1.8a–§1.8b.

### 3.1 `tasks-vault-config-verify.yaml`

**Purpose.** Pure validation pre-check для Vault policies + roles. Read-only, no `set_fact`.

**Input (dto):** `dto_label_name` (log prefix).

**Reads (inventory):** `vault_policies`, `vault_policies_extra`, `vault_roles`, `vault_roles_extra` (inline merge внутри vars каждого assert/fail).

**Validates:**
- Unique `name` in merged policies.
- Unique `name` in merged roles.
- Referential integrity: each role's `policies` references existing policy.

**Callers:** `vault-install.yaml` + 10 ESO-integrated install/configure playbook'ов + `tests/helm-validate.yaml` (12 callers total).

### 3.2 `tasks-eso-verify.yaml`

**Purpose.** Pure validation pre-check для одного ESO-integrated компонента. Read-only, no `set_fact`. Вызывается **после** `tasks-vault-config-verify.yaml` в playbook'е (две независимые task'и, не include task-from-task).

**Input (dto):**
- `dto_label_name` (log prefix).
- `dto_eso_secrets_list` (final base + extra array — inline Jinja expression в caller'е).
- `dto_eso_integration_object` (mapping — `eso_vault_integration_<c>`).
- `dto_namespace` (K8s namespace компонента).

**Reads (inventory):** `vault_policies/_extra`, `vault_roles/_extra` (inline merge).

**Validates (4 groups):**
- A. Input asserts.
- B. SecretStore→Vault connectivity scoped к role: role exists, SA binding, namespace binding, policies count > 0, each role.policies exists.
- C. ESO uniqueness: `external_secret_name`, `body.target.name`.
- D. Policy path coverage scoped к role's policies: каждый Vault path (из `body.dataFrom[].extract.key` и `body.data[].remoteRef.key`) должен быть substring какого-либо path-prefix из policies этой role (после stripping `/*`).

**Callers:** 10 ESO-integrated install/configure playbook'ов (8 install + 2 configure).

---

## 4. The Eight Vault/ESO Task Primitives

| Task | Use |
|---|---|
| `tasks-vault-config-verify.yaml` | Pre-check: validate Vault policies + roles uniqueness + role→policy refs. |
| `tasks-eso-verify.yaml` | Pre-check per-component: connectivity, uniqueness, policy coverage. |
| `tasks-vault-get.yaml` | Read a single KV field into a named fact + a caller-named exists boolean fact. Safe on missing paths. |
| `tasks-vault-put.yaml` | `vault kv put` + annotate ExternalSecret + wait for target K8s Secret to be present/updated. |
| `tasks-vault-delete.yaml` | Hard-delete a Vault KV v2 path entirely (metadata stanza + all versions) via `vault kv metadata delete`. Idempotent on missing path. |
| `tasks-generate-secret.yaml` | Generate random N-char secret into a named fact. |
| `tasks-eso-force-sync.yaml` | Annotate ExternalSecrets with `force-sync=<epoch>` to trigger ESO reconciliation. |
| `tasks-vault-distribute-creds.yaml` | Read `vault-unsealer-secret` from cluster and write `/etc/kubernetes/vault-unseal.json` on all managers. |

Full contracts (input/output/callers) in [`reusable-tasks.md`](reusable-tasks.md).

---

## 5. SecretStore + ExternalSecret Templates

Rendered by each component's `<c>/pre/` Helm chart from `<c>_pre_helm_values.eso.secrets` (which is inline `eso_vault_integration_<c>_secrets + (eso_vault_integration_<c>_secrets_extra | default([]))` — no runtime fact).

### 5.1 ServiceAccount

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {{ .Values.eso.saName }}
  namespace: {{ .Values.namespace }}
```

### 5.2 SecretStore (namespaced)

```yaml
apiVersion: external-secrets.io/v1beta1
kind: SecretStore
metadata:
  name: {{ .Values.eso.secretStoreName }}
  namespace: {{ .Values.namespace }}
spec:
  provider:
    vault:
      server: {{ .Values.vault.internalUrl | quote }}
      path: {{ .Values.eso.kvEnginePath | quote }}  # "eso-secret"
      version: v2
      auth:
        kubernetes:
          mountPath: kubernetes
          role: {{ .Values.eso.roleName | quote }}
          serviceAccountRef:
            name: {{ .Values.eso.saName }}
```

### 5.3 ExternalSecret — canonical chart template (identical across all 9 components)

All 8 `charts/<c>/pre/templates/eso-external-secret.yaml` files are now identical (modulo the banner comment). The entire `spec` body of each `ExternalSecret` comes from inventory via `$secret.body`:

```yaml
# =============================================================================
# EXTERNAL SECRETS FOR <COMPONENT> (from Vault)
# =============================================================================
{{- range $secret := .Values.eso.secrets }}
{{- if and (not (eq $.Values.eso.isNeedEso false)) (not (eq $secret.is_need_eso false)) }}
---
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: {{ $secret.external_secret_name }}
  namespace: {{ $.Values.namespace }}
spec:
  refreshInterval: {{ $secret.refresh_interval | default $.Values.esoResourcesConfig.externalSecretRefreshInterval }}
  refreshPolicy: {{ $.Values.esoResourcesConfig.externalSecretRefreshPolicy }}
  secretStoreRef:
    kind: SecretStore
    name: {{ $.Values.eso.secretStoreName }}
{{ toYaml $secret.body | indent 2 }}
{{- end }}
{{- end }}
```

The chart no longer contains any knowledge of `type`, data shapes, or ArgoCD labels. All of that is expressed in `body` within the inventory.

**Key properties:**
- `$.Values.eso.isNeedEso false` — gates the entire component. If the integration object has `is_need_eso: false`, no ExternalSecrets are rendered.
- `$secret.is_need_eso false` — gates a single item (e.g., `argocd` root password when ESO is disabled for that specific secret).
- `$secret.refresh_interval` — per-item override; falls back to `esoResourcesConfig.externalSecretRefreshInterval` from values.
- `toYaml $secret.body | indent 2` — dumps the entire `body` dict as YAML indented under `spec:`. The `body` must contain at minimum a `target.name` and either `dataFrom` or `data`.

---

## 6. Secret Flow — Seed vs. Rotation

### 6.1 First-seed (example: `gitlab` root password at install time)

```
1. <c>-install.yaml calls tasks-generate-secret.yaml  → new_password fact
2. tasks-vault-put.yaml:
     vault kv put eso-secret/gitlab/gitlab-root password=<new_password>
     kubectl annotate externalsecret gitlab-root force-sync=<epoch>
3. Main gitlab chart installs; pods mount the now-present Secret
4. (optional) verify: log in to GitLab with the new password from Vault
```

Idempotency: if `tasks-vault-get.yaml` reports the Vault path already exists, skip step 1–2.

### 6.2 Rotation (example: `gitlab` Postgres password)

```
1. tasks-generate-secret.yaml             → new_pg_password
2. (optional) write to /tmp on DB host, ALTER USER in running postgres
3. tasks-vault-put.yaml
     vault kv put eso-secret/gitlab/postgresql password=<new_pg_password>
     annotate
4. (future) Reloader restarts pods that mount the Secret
   — until Reloader is installed, manually run `gitlab-restart.yaml`
5. rm /tmp file
```

---

## 7. Adding a New ESO-integrated Component

Checklist — keep strictly in order.

1. **Add named dict-variables** in `hosts-vars/<c>.yaml` — one per base secret. Each variable is a full dict with fields `external_secret_name`, `vault_path`, `body` (and optional `is_need_eso`, `refresh_interval`). Example (see §2.4 for full schema):
   ```yaml
   <c>_secret_admin:
     external_secret_name: "eso-<c>-admin"
     vault_path: "/<c>/admin"
     body:
       target:
         name: "eso-<c>-admin"
       dataFrom:
         - extract:
             key: "{{ eso_vault_integration_<c>.kv_engine_path }}/data/<c>/admin"
   ```

2. **Add the `eso_vault_integration_<c>` object** in `hosts-vars/<c>.yaml`:
   ```yaml
   eso_vault_integration_<c>:
     sa_name: "<c>-eso-sa"
     role_name: "eso-<c>"
     secret_store_name: "<c>-eso-secret-store"
     kv_engine_path: "eso-secret"
     is_need_eso: true
   ```

3. **Define the base secrets list** in `hosts-vars/<c>.yaml` as an array of Jinja-string-references to the named dict-variables from Step 1:
   ```yaml
   eso_vault_integration_<c>_secrets:
     - "{{ <c>_secret_admin }}"
   ```

4. **Add an `_extra` entry in `hosts-extra.example.yaml`** so users know the extension point exists:
   ```yaml
   eso_vault_integration_<c>_secrets_extra: []
   ```

5. **Add the Vault policy** in `hosts-vars/vault.yaml` → `vault_policies`:
   ```yaml
   - name: "eso-<c>"
     rules: |
       path "eso-secret/data/<c>/*"     { capabilities = ["read"] }
       path "eso-secret/metadata/<c>/*" { capabilities = ["read"] }
   ```

6. **Add the Vault role** in `hosts-vars/vault.yaml` → `vault_roles`:
   ```yaml
   - name: "eso-<c>"
     bound_service_account_names: "<c>-eso-sa"
     bound_service_account_namespaces: "<c>-ns"
     policies:
       - "eso-<c>"
     ttl: "1h"
   ```

7. **Add `eso_vault_integration_<c>` integration object + `<c>_pre_helm_values.eso` block** в `hosts-vars/<c>.yaml`. `<c>_pre_helm_values.eso.secrets` — inline merge: `"{{ eso_vault_integration_<c>_secrets + (eso_vault_integration_<c>_secrets_extra | default([])) }}"`. Никаких runtime fact'ов / 8-component hard-coded lists больше нет.

8. **Render `ServiceAccount` and `SecretStore`** in the component's `charts/<c>/pre/templates/`. Copy the canonical `eso-external-secret.yaml` template from any existing component (§5.3) — it is identical across all 9 components and requires no modification.

9. **В `<c>-install.yaml` (и configure если нужно)** добавь два последовательных pre-check блока: `tasks-vault-config-verify.yaml` (dto: `dto_label_name`) + `tasks-eso-verify.yaml` (dto: `dto_label_name`, `dto_eso_secrets_list` = inline base+extra, `dto_eso_integration_object`, `dto_namespace`). Шаблон в [`reusable-tasks.md`](reusable-tasks.md) §3.1.

10. **Apply the new Vault policy/role**:
    ```
    ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/vault-install.yaml --tags install
    ```

11. **Install the component**:
    ```
    ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/<c>-install.yaml
    ```

12. **First-seed secrets** from a `<c>-configure.yaml` or in-install task using `tasks-generate-secret.yaml` + `tasks-vault-put.yaml`.

---

## 8. Rotation Procedures

### 8.1 `vault-rotate.yaml`

- Rekey Shamir shares via `vault operator rekey` — меняет unseal keys, сохраняя `vault_key_threshold`.
- **Root token не меняется** при rekey.
- Обновляет K8s Secret `{{ vault_unsealer_secret_name }}` в namespace `{{ vault_namespace }}`.
- Раздаёт `{{ vault_creds_host_path }}` на все managers через `tasks-vault-distribute-creds.yaml`.
- **Resume-safe через temp-файл** `{{ vault_rekey_temp_file_path }}` (default `/etc/kubernetes/vault-rekey-in-progress.json`), который пишется на `master_manager_fact` сразу после `vault operator rekey` и удаляется только в самом конце. Логика старта playbook'а:
  1. Если temp-файла **нет** → ROTATE branch: rekey → вывести новые ключи в ansible-лог → записать temp-файл → заменить K8s Secret → distribute → удалить temp-файл.
  2. Если temp-файл **есть** → RECOVERY branch: прочитать и проверить формат → заменить K8s Secret → distribute → удалить temp-файл. Rekey **не** повторяется (старые unseal keys уже невалидны после первого rekey).
- Формат temp-файла совпадает с distributed `{{ vault_creds_host_path }}`: `{"vault-root": "...", "vault-unseal-0": "...", ..., "vault-unseal-N": "..."}`. При полном сбое оператор может вручную скопировать temp-файл поверх `vault-unseal.json` на каждом manager'е.
- Если temp-файл повреждён (не JSON, нет `vault-root`, нет ни одного `vault-unseal-<N>`) — playbook падает с инструкцией: «починить файл вручную или удалить, если уверен, что rekey не прошёл».

### 8.2 Component credential rotation

Generic template (see §6.2 for concrete example):

1. `tasks-vault-get.yaml` — fetch current creds (if needed for DB ALTER statements).
2. `tasks-generate-secret.yaml` — new value.
3. Apply to the running workload (DB, IdP, etc.).
4. `tasks-vault-put.yaml` — persist + force ESO re-sync.
5. (future: Reloader) restart workload to pick up the new mounted Secret. Until Reloader lands, use `<c>-restart.yaml`.

---

## 9. Per-component Vault Paths (quick reference)

All under `eso-secret/` KV engine.

| Component | Path prefix | Typical keys |
|---|---|---|
| `traefik` | `eso-secret/traefik/*` | user-defined (TLS, basic-auth via `_extra`) |
| `haproxy` | `eso-secret/haproxy/*` | user-defined |
| `longhorn` | `eso-secret/longhorn/*` | S3 backup creds (access key, secret key, endpoint) |
| `gitlab` | `eso-secret/gitlab/*` | `postgresql`, `redis`, `minio-root`, `minio-registry`, `gitlab-root`, PATs |
| `gitlab-runner` | `eso-secret/gitlab-runner/*` | registration token, S3 cache creds |
| `zitadel` | `eso-secret/zitadel/*` | `postgresql`, `masterkey` |
| `argocd` | `eso-secret/argocd/*` | `admin` (password), optional OIDC client-secret, plus git-ops repo credentials (pattern + direct) under `eso-secret/argocd/git-ops/*` |
| `mon_system` | `eso-secret/mon-system/*` | `grafana/admin/creds` (password), `grafana/postgresql/creds` (username + password for backing Postgres), optional `grafana/oidc` client-secret, optional `grafana/<ds>` datasource creds |
| `seaweedfs` | `eso-secret/seaweedfs/*` + operator-chosen per-identity paths | `postgresql/creds` (username + password for filer Postgres backend); `s3-config/all` (combined identity JSON — single field `config` containing all identities admin+users+anonymous); per-identity `additional_vault_paths` (fan-out generated creds для downstream consumers, paths operator-chosen) |

---

## 10. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `ExternalSecret` stuck in `SecretSyncedError` | Vault policy missing `read` on the path, or role doesn't bind the SA | Run `vault-install.yaml --tags install` after updating policies; inspect `ExternalSecret` status for error detail |
| `SecretStore` shows `ValidationFailed` | `role_name` missing in Vault or `kubernetes` auth mount path wrong | Confirm merged `vault_roles + vault_roles_extra` has the role (run `tasks-vault-config-verify.yaml`); check bank-vaults logs |
| K8s Secret exists but pod doesn't see new value | Pod was not restarted after Secret change | `<c>-restart.yaml` (Reloader will automate this in future) |
| Vault sealed after reboot | Auto-unseal CronJob didn't run | Run manually on a manager: `kubectl -n vault exec vault-0 -- vault operator unseal <key>` × threshold |
| New manager can't unseal | `/etc/kubernetes/vault-unseal.json` missing | Re-run `tasks-vault-distribute-creds.yaml` (part of `manager-join.yaml`) |
| `tasks-vault-config-verify.yaml` fails "duplicate policy names in merged vault_policies" | Base + `_extra` both define the same policy name | Remove duplicate from `_extra`; only triggered by `vault-install.yaml`, not by component install playbooks |
| `tasks-eso-verify.yaml` fails "duplicate external_secret_name in dto_eso_secrets_list" | Base + `_extra` (or multiple `_extra` entries) define ExternalSecrets with the same `external_secret_name` | Rename one of the conflicting entries |

---

## 11. Migration Notes

- **SUB-1** split the former `tasks-eso-merge.yaml` into `tasks-vault-policies-roles-merge.yaml` (Vault policy/role merge, called only from `vault-install.yaml`) and `tasks-eso-secrets-merge.yaml` (per-component secrets merge, called from every ESO component install/configure playbook).
- **SUB-2/3** moved all `ExternalSecret` body content from Go-template chart logic into inventory (`body` field per item), making all 8 `eso-external-secret.yaml` charts identical.
- **SUB-4** removed the `type` field; replaced `selectattr('type', 'equalto', ...)` lookups in playbooks with `tasks-eso-lookup.yaml`; introduced `<c>_secret_name_<logical>` named variables in `hosts-vars/<c>.yaml`.
- **SUB-5** unified values-keys (`gitlabEso`/`runnerEso`/`zitadelEso`/`grafanaEso` → `eso`) so all 8 charts reference `$.Values.eso.*`.
- **SUB-7** removed `hosts-vars/vault-eso.yaml`; all 8 `eso_vault_integration_<c>` integration blocks now live in the corresponding per-component `hosts-vars/<c>.yaml`.
- **`vault_policies` / `vault_roles`** remain in `hosts-vars/vault.yaml` (not per-component).
- **mon-system consolidation (SUB-1..11)** removed six per-component charts/playbooks/vars (mon-prometheus-operator, mon-grafana, mon-loki, mon-vector, mon-node-exporter, mon-kube-state-metrics) and replaced them with a single consolidated mon-system stack: namespace `mon-system`, inventory `hosts-vars/mon-system.yaml`, chart tree `playbook-app/charts/mon-system/`, playbook `playbook-app/mon-system-install.yaml`. The grafana ESO integration was renamed `eso_vault_integration_grafana` → `eso_vault_integration_mon_system`; Vault policy/role `grafana.eso-main` → `mon-system.eso-main`; Vault path prefix `eso-secret/grafana/*` → `eso-secret/mon-system/*`; SA `grafana` ns binding → `mon-system` ns binding. The 8-component list in `tasks-eso-secrets-merge.yaml` now includes `mon_system` (not `grafana`).
- **ESO refactor (named-variable objects)** removed `tasks-eso-lookup.yaml`. Replaced array-of-dicts pattern in `eso_vault_integration_<c>_secrets` (with Jinja-refs to `<c>_secret_name_<logical>` strings) with array-of-references to top-level named dict-variables `<c>_secret_<logical>` (each containing full ExternalSecret structure: `external_secret_name`, `vault_path`, `body`). `<c>_secret_name_<logical>` string variables removed; all references in `*_helm_values` and playbooks migrated to direct `<c>_secret_<logical>.body.target.name` / `<c>_secret_<logical>.vault_path` notation. `_extra` remains a list of full dict-items (format preserved).
- **ESO refactor v2 (verify tasks + inline merge)** removed `tasks-eso-secrets-merge.yaml` and `tasks-vault-policies-roles-merge.yaml`. Two new pure-validation tasks (`tasks-vault-config-verify.yaml` + `tasks-eso-verify.yaml`) replace them — no `set_fact`, no runtime facts. Magic runtime facts `eso_vault_integration_<c>_secrets_merged`, `vault_policies_final`, `vault_roles_final` removed; inline merge `base + (extra | default([]))` performed at usage sites (`<c>_pre_helm_values.eso.secrets`, `hosts-vars/vault.yaml` `vault_spec.externalConfig`). Store-level `is_need_eso` (`eso_vault_integration_<c>.is_need_eso` + chart `.Values.eso.isNeedEso`) removed; gating только item-level через `body.is_need_eso` per-secret. ESO callers вызывают **два task'а последовательно** (vault-config-verify + eso-verify) — task НЕ вызывает другие task'и через include.
- **seaweedfs integration (F1-F4)** добавил 9-й ESO-integrated компонент: namespace `seaweedfs`, inventory `hosts-vars/seaweedfs.yaml`, Vault policy/role `seaweedfs.eso-main`, path prefix `eso-secret/seaweedfs/*`. Two named secret variables: `seaweedfs_secret_postgresql_creds` (simple `dataFrom.extract` для filer Postgres) и `seaweedfs_secret_admin_creds` (multi-field `body.target.template` с одним key `seaweedfs_s3_config` содержащим JSON identity config — потребляется через `s3.existingConfigSecret` upstream SeaweedFS chart 4.28.0; bootstrap admin creds генерируются в `seaweedfs-install.yaml` как 2 поля access_key + secret_key в Vault path `/seaweedfs/admin/creds`).
- **seaweedfs architecture v3 (declarative sync + combined JSON model)** заменил admin-only ESO template на combined identity JSON pattern. `seaweedfs_secret_admin_creds` удалён, замещён `seaweedfs_secret_s3_identities` — ESO template читает single Vault field `config` из `/seaweedfs/s3-config/all` (полный identity JSON: admin + users + anonymous) и рендерит K8s Secret `seaweedfs-s3-identities` с key `seaweedfs_s3_config` (consumed через upstream chart's `existingConfigSecret`). `seaweedfs_admin_identity` (intermediate concept из ранней итерации) удалён. Новые reusable tasks: `tasks-vault-delete.yaml` (generic Vault primitive); три SeaweedFS-специфичных в `playbook-app/tasks/seaweedfs/`: `tasks-seaweedfs-user-sync.yaml` (identity sync — diff vs Vault, generate-if-missing, fan-out `additional_vault_paths`, ESO sync, conditional rollout restart), `tasks-seaweedfs-bucket-sync.yaml` (buckets + quotas via weed shell, ConfigMap state diff), `tasks-seaweedfs-bucket-policy-sync.yaml` (full AWS IAM policies via aws s3api put-bucket-policy, ConfigMap state diff, self-contained admin creds fetch). Sync invoked from `seaweedfs-install.yaml` via tags `[user-sync]` (before helm install) + `[bucket-sync]` + `[bucket-policy-sync]` (after install + post + quota-cron). Standalone `seaweedfs-sync.yaml` создан и удалён в той же итерации (architecture migrated к task includes). Identity persistence — K8s Secret mounted в S3 pods (НЕ через `weed shell s3.configure -apply` который ephemeral). Quota enforcement — отдельный chart subdir `charts/seaweedfs/quota-cron/` (K8s CronJob каждые 5 минут). Per-component Vault paths — single primary `/seaweedfs/s3-config/all` (combined JSON, source of truth для S3 pods через ESO) + per-identity `additional_vault_paths` (fan-out generated creds для downstream consumer ESO). Helm chart version pinned 4.29.0 для consistency с CronJob image `chrislusf/seaweedfs:4.29`.

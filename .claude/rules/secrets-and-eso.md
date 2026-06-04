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

**Purpose.** Тонкий wrapper над Python-фильтром `vault_config_verify` (`filter_plugins/vault_config_verify.py`) — pre-check для Vault policies + roles. Wrapper: Rule-19 assert + `set_fact` `_local_error_item_list` (вызов фильтра) + `assert length == 0`. Фильтр возвращает `list[str]` нарушений, не кидает.

**Input (dto):** `dto_label_name` (log prefix).

**Reads (inventory):** `vault_policies`, `vault_policies_extra`, `vault_roles`, `vault_roles_extra` (merge в wrapper'е, merged списки передаются фильтру).

**Validates:**
- Unique `name` in merged policies.
- Unique `name` in merged roles.
- Referential integrity: each role's `policies` references existing policy.

**Callers:** `vault-install.yaml` + 9 ESO-install + 2 ESO-configure playbook'ов + `tests/helm-validate.yaml` (13 callers total).

### 3.2 `tasks-eso-verify.yaml`

**Purpose.** Тонкий wrapper над Python-фильтром `eso_verify` (`filter_plugins/eso_verify.py`) — pre-check для одного ESO-integrated компонента. Group A — Rule-19 assert в YAML; B/C/D — в фильтре. Wrapper: assert + `set_fact` `_local_error_item_list` + `assert length == 0`. Фильтр возвращает `list[str]`, не кидает. Вызывается **после** `tasks-vault-config-verify.yaml` в playbook'е (две независимые task'и, не include task-from-task).

**Input (dto):**
- `dto_label_name` (log prefix).
- `dto_eso_secrets_list` (final base + extra array — inline Jinja expression в caller'е).
- `dto_eso_integration_object` (mapping — `eso_vault_integration_<c>`).
- `dto_namespace` (K8s namespace компонента).

**Reads (inventory):** `vault_policies/_extra`, `vault_roles/_extra` (merge в wrapper'е, merged списки передаются фильтру).

**Validates (4 groups):**
- A. Input asserts.
- B. SecretStore→Vault connectivity scoped к role: role exists, SA binding, namespace binding, policies count > 0, each role.policies exists.
- C. ESO uniqueness: `external_secret_name`, `body.target.name`.
- D. Policy path coverage scoped к role's policies: каждый Vault path (из `body.dataFrom[].extract.key` и `body.data[].remoteRef.key`) должен быть substring какого-либо path-prefix из policies этой role (после stripping `/*`).

**Callers:** 11 ESO-integrated install/configure playbook'ов (9 install + 2 configure). НЕ вызывается из `tests/helm-validate.yaml`.

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
| `gitlab` | `eso-secret/gitlab/*` | `postgresql/creds`, `redis/creds`, `s3-storage` (single path, fields `username`/`accessKey`/`secretKey` — provisioned by SeaweedFS sync OR manual `vault kv put`), `gitlab-root`, PATs |
| `gitlab-runner` | `eso-secret/gitlab-runner/*` | registration token (`/token`), `s3-storage` (single path для runner cache, same fields format as gitlab) |
| `zitadel` | `eso-secret/zitadel/*` | `postgresql`, `masterkey` |
| `argocd` | `eso-secret/argocd/*` | `admin` (password), optional OIDC client-secret, plus git-ops repo credentials (pattern + direct) under `eso-secret/argocd/git-ops/*` |
| `mon_system` | `eso-secret/mon-system/*` | `grafana/admin/creds` (password), `grafana/postgresql/creds` (username + password for backing Postgres), `loki/s3/creds` (Loki S3 backend — fields `username`/`accessKey`/`secretKey`; provisioned by SeaweedFS sync OR manual `vault kv put`), optional `grafana/oidc` client-secret, optional `grafana/<ds>` datasource creds |
| `seaweedfs` | `eso-secret/seaweedfs/*` (+ per-identity operator-configurable Vault paths via `identity.extra_vault_paths`, fixed keys `username`/`accessKey`/`secretKey` — Layer 3 distribute; см. §11 v14) | `postgresql/creds` (username + password for filer Postgres backend); `s3-config/bootstrap` (ESO empty-config `{"identities":[]}` → K8s Secret `seaweedfs-s3-empty-config` → upstream chart `existingConfigSecret`, форсит filer-driven Replace-режим); `s3-config/all` (combined identity JSON key-store — single field `config`; **plain-var** `seaweedfs_s3_config_vault_path`, НЕ ESO secret, в K8s НЕ материализуется; read/write только Ansible sync — user-sync + identity-distribute); `admin-ui/creds` (`adminUser` + `adminPassword` — admin UI login, simple `dataFrom.extract`, seeded by `seaweedfs-install.yaml`). Managed IAM policies + identities живут в filer `/etc/iam/` (не в Vault) — см. §11 v14. |

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
- **seaweedfs architecture v3 (declarative sync + combined JSON model)** заменил admin-only ESO template на combined identity JSON pattern. `seaweedfs_secret_admin_creds` удалён, замещён `seaweedfs_secret_s3_identities` — ESO template читает single Vault field `config` из `/seaweedfs/s3-config/all` (полный identity JSON: admin + users + anonymous) и рендерит K8s Secret `seaweedfs-s3-identities` с key `seaweedfs_s3_config` (consumed через upstream chart's `existingConfigSecret`). `seaweedfs_admin_identity` (intermediate concept из ранней итерации) удалён. Новые reusable tasks: `tasks-vault-delete.yaml` (generic Vault primitive); три SeaweedFS-специфичных в `playbook-app/tasks/seaweedfs/`: `tasks-seaweedfs-user-sync.yaml` (identity sync — diff vs Vault, generate-if-missing, ESO sync, conditional rollout restart), `tasks-seaweedfs-bucket-sync.yaml` (buckets + quotas via weed shell, ConfigMap state diff), `tasks-seaweedfs-bucket-policy-sync.yaml` (full AWS IAM policies via aws s3api put-bucket-policy, ConfigMap state diff, self-contained admin creds fetch). Sync invoked from `seaweedfs-install.yaml` via tags `[user-sync]` (before helm install) + `[bucket-sync]` + `[bucket-policy-sync]` (after install + post + quota-cron). Standalone `seaweedfs-sync.yaml` создан и удалён в той же итерации (architecture migrated к task includes). Identity persistence — K8s Secret mounted в S3 pods (НЕ через `weed shell s3.configure -apply` который ephemeral). Quota enforcement — отдельный chart subdir `charts/seaweedfs/quota-cron/` (K8s CronJob каждые 5 минут). Per-component Vault paths — single primary `/seaweedfs/s3-config/all` (combined JSON, source of truth для S3 pods через ESO). Helm chart version pinned 4.29.0 для consistency с CronJob image `chrislusf/seaweedfs:4.29`.
- **seaweedfs architecture v4 (merge bucket+policy sync, add identity distribution)** объединил два sync task'а (бывшие `tasks-seaweedfs-bucket-sync.yaml` + `tasks-seaweedfs-bucket-policy-sync.yaml`) в один `tasks-seaweedfs-bucket-sync.yaml` с 7-phase execution (delete стейл buckets → delete стейл policies у kept → apply policies у kept → create new buckets → apply policies у new → apply quotas → update ConfigMap state). Inventory schema `seaweedfs_sync_buckets[]` теперь содержит optional `policy` field per bucket (AWS IAM document) — отдельный `seaweedfs_sync_bucket_policies`/_extra удалён. Single ConfigMap `seaweedfs-sync-buckets-state` (бывший `seaweedfs-sync-bucket-policies-state` удалён — orphan в кластере при upgrade, ОК). Self-contained admin creds fetch теперь conditional (only если any policy op). `s3.bucket.delete` без флага `-force`, но сам делает hard delete через CollectionDelete (бывший комментарий "fails non-empty" устарел; Object Lock с locked objects — единственное препятствие). Добавлен новый Layer 3 task `tasks-seaweedfs-identity-secret-distribute.yaml` (tag `[identity-distribute]`, runs после user-sync, до helm install) — distribute identity creds через fixed keys `username`/`accessKey`/`secretKey` (HARD-CODED, не configurable) в произвольные дополнительные Vault paths определяемые в `identity.extra_vault_paths` (full Vault paths с mount engine prefix). Diff-based via K8s ConfigMap `seaweedfs-sync-identity-distributions-state`: Phase A vault-put per (identity, path) target pair (full replace, idempotent) + Phase B vault-delete per state path не в target + Phase C update ConfigMap. Anonymous identity с непустым `extra_vault_paths` → fail (нет creds). Источник истины для credentials — Layer 1 combined JSON (записанный user-sync); Layer 3 только distribute'ит в дополнительные slots для consumer-компонентов (gitlab, mon-system и т.п.), которые потребляют через свой `eso_vault_integration_<consumer>_secrets_extra`. Numbering в `reusable-tasks.md` отражает playbook flow: §1.29 user-sync (L1) → §1.30 identity-secret-distribute (L3) → §1.31 bucket-sync (L2, merged scope). Empirically обнаружено что Ansible Jinja2 (core 2.20.x) НЕ поддерживает list/dict comprehension — patterns в новом identity-secret-distribute task используют set-block (`{%- set _r = [] -%}... {%- endfor -%}`), `subelements` filter с `skip_missing=true`, и `dict(zip(...))`.
- **seaweedfs architecture v5 (Python compute layer via filter plugin)** перенёс compute logic (diff/JSON/validation) трёх sync task'ов из Ansible Jinja2 (set-blocks, subelements + when filter, broken list comprehensions) в `filter_plugins/seaweedfs_sync.py` — 13 pure Python функций auto-discovered Ansible'ом из repo-root через `ansible.cfg`'s `[defaults] filter_plugins = filter_plugins` setting. Ansible task'и упростились (user-sync 273→244, identity-distribute 246→230, bucket-sync 308→260; ~125 строк reduction total) и теперь оркеструют только I/O (vault-get/put/delete, kubectl, generate-secret loops); вся compute через `{{ x | seaweedfs_<filter>(y) }}` filter pipe syntax. Pytest unit tests в `tests/python/test_seaweedfs_sync.py` (37 cases for all 13 functions) — Layer 3 в `make test` chain (после yamllint + ansible-lint + syntax-check + helm-validate); catches runtime issues that Jinja2 syntax-check не видит (например broken list comprehension `[{...} for x in X]` который crashed runtime в v3 era code — discovered + fixed в SUB-3 rewrite). Empirically verified: Ansible core 2.20.x не поддерживает list/dict comprehension в Jinja2 — patterns в v4 era code (set-block accumulator, subelements + when filter) тоже устарели после filter plugin введения. Side effect: `ansible.cfg` создан в repo root (minimal `[defaults] filter_plugins = filter_plugins`) — Dockerfile WORKDIR=/repo + Makefile mounts CURDIR:/repo:ro гарантируют что cfg auto-loaded во всех 5 make test stages.
- **seaweedfs architecture v6 (stateless context-passing filter API)** заменил 13 публичных filter'ов на **10 stateless filter'ов** через рефактор всех трёх layers (user-sync, identity-distribute, bucket-sync). Каждая публичная функция принимает **raw inputs** (Vault JSON string + target list из inventory + ConfigMap raw string) и делает ВСЮ compute-работу внутри Python — diff, validation, build, serialization. Промежуточных Ansible-facts между Python-вызовами нет; YAML task-файлы сокращаются ~50% (user-sync 244→183 строк -25%, identity-distribute 231→188 -19%; bucket-sync 261→283 +22 — line count slightly увеличился из-за 6 explicit phase-specific compute calls вместо одного unified, функциональное упрощение через stateless design сохраняется). Generation S3 ключей переехало из Ansible (`tasks-generate-secret.yaml` loops) в Python (`secrets.choice` с длинами и charset'ами из inventory: `seaweedfs_sync_access_key_length`/`_secret_key_length`/`_access_key_charset`/`_secret_key_charset`). Validation (anonymous-no-extra-paths, paths-unique, creds-exist, Principal-not-dict) переехала внутрь compute-функций — fail-fast при первом нарушении. Quota size конвертация (`"100GiB"` → `102400` MiB) встроена в `seaweedfs_buckets_quotas_to_apply` (output dict содержит `quota.size_mib` int field). Финальная карта public filters — ровно **10 функций**: `seaweedfs_user_sync_full` (Layer 1) + 3 distribute (paths_to_delete / paths_to_add / new_state_json) (Layer 3) + 6 buckets/bucket_policies (to_delete / policies_to_delete / to_create / policies_to_apply / quotas_to_apply / new_state_json) (Layer 2). Plus 12 private helpers (`_parse_combined_json`, `_extract_creds_by_name`, `_compute_identity_diff`, `_gen_secret`, `_parse_configmap_state`, `_flatten_target_paths`, `_validate_anonymous_no_extra`, `_validate_paths_unique`, `_validate_creds_exist`, `_compute_distribution_pairs`, `_compute_state_paths_to_delete`, `_build_new_distribution_state`, `_compute_bucket_diff`, `_quota_size_to_mib`, `_validate_principal_not_dict`, `_validate_principal_not_dict_in_buckets`, `_enrich_quotas_with_size_mib`) — internal primitives, не registered как filters. Admin creds extraction в bucket-sync.yaml переведён на Ansible-native chain (`from_json` + `selectattr` + `first`) — никаких SeaweedFS-specific filters для этой операции. Pytest unit tests `tests/python/test_seaweedfs_sync.py` — 39 cases (6 shared + 6 Layer 1 + 9 Layer 3 + 18 Layer 2), shared fixtures в `tests/python/conftest.py` (NEW в SUB-1). Admin identity вынесена в отдельную inventory переменную `seaweedfs_identity_admin` (object `{name, actions}`) — устраняет hardcoded `'admin'` string в bucket-sync; operator может переименовать admin без поиска по коду (по аналогии с GitLab `gitlab_postgresql_username` pattern). Phase structure для bucket-sync — 5 phase loops + ConfigMap update (Phase C+E merged в Phase D — apply policies kept + new вместе после create new buckets).
- **seaweedfs architecture v7 (bucket immutable settings: collection + replication)** добавил два обязательных поля per bucket: `collection` (string — group of volumes в SeaweedFS) + `replication` (3-digit string `^[0-9]{3}$` — `XYZ` где X=other DCs, Y=other racks, Z=other servers). Оба configurable через одну команду `fs.configure -locationPrefix=/buckets/<n> -collection=<c> -replication=<r> -apply` (combined). Phase C2 (новая) выполняет fs.configure для new buckets после Phase C (s3.bucket.create). Total phases: 6 loops + ConfigMap update (was 5+1). **Immutability enforcement через fail-fast ERROR + abort:** новый public filter `seaweedfs_buckets_immutable_violations` returns list of kept buckets где collection OR replication changed vs state; YAML assert (после compute calls, ДО Phase A) fails если list non-empty — sync aborts с detailed message, cluster intact, state ConfigMap не updated. Operator должен либо revert inventory к state values, либо migrate data manually + align. Rationale: change post-create technically works (fs.configure applies к новым writes), НО existing data остаётся в old configuration → data fragmentation. Validations: `_validate_buckets_have_collection_and_replication` (presence check) + `_validate_replication_format` (regex `^[0-9]{3}$`) called by every Layer 2 public filter — fail-fast при первом нарушении. **Final public filters: 11** (10 v6 + 1 new `seaweedfs_buckets_immutable_violations`). Pytest unit tests `tests/python/test_seaweedfs_sync.py` — 47 cases (39 v6 + 8 new для validation + violations detection); `tests/python/conftest.py` sample fixtures обновлены с collection + replication. **Breaking change:** existing operator inventory с bucket entries без collection/replication будет fail-fast на sync — operator должен update `hosts-vars-override/seaweedfs-sync.yaml`.
- **gitlab MinIO removal + SeaweedFS/external S3 backend (v8)** удалил локальный MinIO sub-chart из GitLab + GitLab-Runner (chart subdir `charts/gitlab/minio/` deleted, `gitlab-minio` Helm release no longer deployed, `gitlab-install.yaml` STEP 4+5 «Install MinIO» + «MinIO setup» удалены, `gitlab-runner-install.yaml` STEP 2 «MinIO runner-cache setup» удалён). GitLab теперь работает с **внешним S3 backend** — два варианта: (A) **SeaweedFS sync (opt-in)** — operator раскомментирует pre-built блоки в `hosts-vars-override/seaweedfs-sync.yaml` (SECTION 1 identities + SECTION 2 buckets, см. `components.md` §17.5 v8 opt-in bullet), затем `seaweedfs-install --tags user-sync,identity-distribute,bucket-sync` распределит creds в Vault + создаст buckets с full-access policies; (B) **cloud S3 (AWS S3/GCS/etc.)** — operator вручную `vault kv put <eso_vault_integration_gitlab.kv_engine_path><gitlab_secret_s3_creds.vault_path> username=<...> accessKey=<...> secretKey=<...>` + аналогично для gitlab-runner. В обоих вариантах GitLab playbook flow идентичен: `tasks-vault-get` → `assert` (fail-fast если creds missing с detailed message + двумя альтернативами provisioning) → `tasks-eso-force-sync` → `tasks-wait-secret` → install GitLab. Inventory clean break: удалены все 11 `gitlab_minio_*` variables + 2 MinIO ESO secret objects (`gitlab_secret_minio_root_creds` + `_minio_registry_creds`) + `gitlab_minio_helm_values` + 2 MinIO ingress configs. Added: `gitlab_s3_endpoint` (default SeaweedFS S3 service в `seaweedfs` namespace), `gitlab_s3_region`, `gitlab_s3_path_style`, 5 bucket name vars (`gitlab_registry_bucket` etc.), `gitlab_s3_secret_key_*` vars (Vault field names: `username`/`accessKey`/`secretKey` — standardized identity-distribute Layer 3 keys), `gitlab_secret_s3_creds` (single ESO secret для всех 5 GitLab buckets). ESO templates `gitlab_secret_registry_connection_config` + `gitlab_secret_backup_s3_connection_config` сохранены, обновлены endpoint/region/path/data references на новый Vault path + standardized fields. eso_vault_integration_gitlab_secrets array 7→6 entries (2 minio entries removed, 1 s3_creds added). gitlab-runner.yaml: `gitlab_runner_secret_minio_cache_creds` → `gitlab_runner_secret_s3_creds`, `gitlab_runner_minio_cache_bucket` → `gitlab_runner_s3_cache_bucket` (value `"runner-cache"` → `"gitlab-runner-cache"` matches seaweedfs-sync.yaml SECTION 2), TOML S3 endpoint updated. **Breaking change:** existing operator inventories с `gitlab_minio_*` references в `hosts-vars-override/gitlab.yaml` будут fail на helm-validate — operator должен убрать stale references перед upgrade. Existing data migration из существующего MinIO не автоматический — operator должен `mc mirror` или подобный manual migration из MinIO в новый S3 backend перед `helm uninstall gitlab-minio`. Net inventory + playbook reduction: ~433 строк (145 insertions / 578 deletions).
- **seaweedfs architecture v9 (aws-cli helper Deployment + NetworkPolicy refactor)** добавил постоянный Deployment `seaweedfs-aws-cli-helper` в `seaweedfs/pre/` chart (1 replica, `sleep infinity`, image `amazon/aws-cli:2.32.0`) для bucket-sync Phase B (`delete-bucket-policy`) + Phase D (`put-bucket-policy`). **Causa:** ранее эти phases вызывали `aws s3api` через `kubectl exec deploy/seaweedfs-s3`, но образ `chrislusf/seaweedfs:4.29` не содержит aws CLI (rc=127). SeaweedFS native shell не имеет команд для bucket policies — policy хранится в filer как `entry.Extended["s3-bucket-policy"]` и модифицируется ТОЛЬКО через S3 API. Bucket-sync теперь обращается к Deployment через `kubectl exec deploy/seaweedfs-aws-cli-helper` против cluster DNS endpoint `http://seaweedfs-s3.<namespace>.svc.<cluster_dns_domain>:8333` (переменная `cluster_dns_domain` из `hosts-vars/k8s-base.yaml`). **NetworkPolicy refactor (вариант B — средняя агрессивность):** добавлена общая `allow-internal-traffic` NP (`podSelector: {}` ingress+egress) для всех intra-namespace pod-to-pod communication. Удалена `seaweedfs-postgresql` NP полностью (только intra-namespace ingress от filer — покрыта allow-internal). Упрощены `seaweedfs-master` и `seaweedfs-volume` до egress-apiserver-only (intra-namespace правила убраны). Упрощены `seaweedfs-filer` и `seaweedfs-s3` до Traefik ingress (cross-namespace) + apiserver egress (intra-namespace правила убраны). Сохранены: `deny-all` (base default-deny), `allow-dns-egress` (DNS → kube-system), ACME solver loop (cross-namespace Traefik solver pods), `<ns>-allow-traefik` (NP в traefik namespace). Итог: 10 политик вместо 9 (added allow-internal-traffic; removed seaweedfs-postgresql; master/volume/filer/s3 упрощены). **Inventory:** `awsCliHelper:` block inline в `seaweedfs_pre_helm_values` в `hosts-vars/seaweedfs.yaml` (5 ключей: `image`, `imagePullSecrets`, `tolerations`, `nodeSelector`, `resources` — все три последних с empty defaults, operator fills через `hosts-vars-override/seaweedfs.yaml`). Chart `awsCliHelper:` defaults в `playbook-app/charts/seaweedfs/pre/values.yaml` — matching defaults. Template `aws-cli-deployment.yaml` потребляет fields через standard `{{- with .Values.awsCliHelper.X }}{{- toYaml . | nindent N }}{{- end }}` pattern: nodeSelector + tolerations в pod spec (между imagePullSecrets и containers), resources в container spec (после command). Conditional через `{{- with ... }}` — empty default НЕ рендерится в финальный manifest.
- **gitlab + gitlab-runner v8.1 (cross-ns NP к SeaweedFS S3 backend)** добавил hard-coded cross-namespace NetworkPolicy pairs в обоих consumer chart'ах: `gitlab/pre` рендерит `allow-seaweedfs-s3` (egress в gitlab ns, podSelector `release=gitlab`) + `gitlab-allow-seaweedfs-s3` (ingress в seaweedfs ns, podSelector `app.kubernetes.io/component=s3`); `gitlab-runner/pre` рендерит `To SeaweedFS S3` egress entries в existing `allow-gitlab-runner` и `allow-job-pod` NPs + `gitlab-runner-allow-seaweedfs-s3` ingress в seaweedfs ns (две `from` entries — runner pods + job pods). Параметризация через `.Values.seaweedfs.{namespace, s3Component, s3HttpPort}` с wiring `<c>_pre_helm_values.seaweedfs.*` в inventory. SeaweedFS — invariant L5 dependency для GitLab family L7 deployment'а; opt-in inventory переменной нет — хардкод (consumer-owned pattern, см. [`networking.md`](networking.md) §8). Также удалены v8 MinIO leftovers: NP `allow-minio` (gitlab/pre), cross-ns NP `{namespace}-allow-gitlab-minio` (gitlab-runner/pre), MinIO egress entries в `{namespace}-allow-traefik` (gitlab) и `allow-gitlab-runner`/`allow-job-pod` (gitlab-runner), stale `gitlab.minioApiPort` / `minioConsolePort` keys + блок `minio:` из values.yaml обоих pre chart'ов.
- **Loki S3 storage backend (Loki stateless)** перевёл mon-system Loki с локального PVC (chunks+index+WAL на LINSTOR) на S3 object store: `mon_system_loki_config_yaml` теперь `common.storage.s3` + `schema_config…object_store: s3` + `compactor.delete_request_store: s3`; chunks И tsdb-индекс уезжают в S3, локально остаётся только `emptyDir` (`mon_system_loki_emptydir_size_limit`, default `4Gi`) под WAL + index-cache + compactor scratch — Loki Deployment stateless (PVC удалён). S3 endpoint/bucket/region/path-style/insecure — `mon_system_loki_s3_*` (default in-cluster SeaweedFS, bucket `loki-logs`; внешний S3 — смена `mon_system_loki_s3_endpoint`). Creds из Vault через новый ESO secret `mon_system_secret_loki_s3_creds` (path `eso-secret/mon-system/loki/s3/creds`, fields `username`/`accessKey`/`secretKey`), добавлен в существующий `eso_vault_integration_mon_system_secrets` — новый ESO-компонент НЕ создавался (policy glob `eso-secret/data/mon-system/*` уже покрывает путь, `vault.yaml` без изменений). Loki контейнер читает creds через env `CUSTOM_LOKI_STORE_S3_ACCESS_KEY_ID` / `CUSTOM_LOKI_STORE_S3_SECRET_ACCESS_KEY` + флаг `-config.expand-env=true`. NetworkPolicy `allow-loki` (`mon-system/pre`, всегда при `loki.enabled`) даёт egress к SeaweedFS S3 (8333) + внешнему S3 (443/80), парный ingress `mon-system-allow-loki` в seaweedfs ns. Loki-фаза `mon-system-install.yaml` fail-fast'ит при отсутствии creds в Vault (vault-get → assert → eso-force-sync → wait-secret, калька с gitlab). Provisioning через SeaweedFS — loki opt-in в `hosts-vars/seaweedfs-sync.yaml` (identity `loki` + bucket `loki-logs`); альтернатива — внешний S3 + ручной `vault kv put`.
- **seaweedfs chart 4.31.0 bump + native quota enforcement, quota-cron removal (v10)** поднял upstream SeaweedFS Helm chart `4.30.0 → 4.31.0` (`hosts-vars/seaweedfs.yaml` `seaweedfs_helm_chart_version`) и **удалил** локальный quota-cron компонент целиком: chart subdir `charts/seaweedfs/quota-cron/` (CronJob `seaweedfs-quota-enforce` + values + extra-objects), STEP 5 QUOTA-CRON блок в `seaweedfs-install.yaml` (4 task'а + тег `[quota-cron]`, bucket-sync перенумерован STEP 6 → 5), PHASE-блок `seaweedfs_quota_cron_*` (7 переменных) в `hosts-vars/seaweedfs.yaml`. Образ master/volume/filer/s3 трекает `appVersion` через chart `_helpers.tpl` → pods переезжают на `chrislusf/seaweedfs:4.31`, отдельный пин не нужен. **Причина:** в 4.31 (commit `8c60408bf`, PR #9774) enforcement квот стал нативным и безусловным — s3-gateway каждую минуту (leader-locked через distributed-lock на одной из 3 реплик) переключает read-only-флаг bucket'а в обе стороны и переписывает filer.conf только при изменении флага (`weed/s3api/bucket_size_metrics.go` `enforceBucketQuotas` + `weed/filer/filer_conf.go` `ApplyBucketQuotaReadOnly`; цикл запускается безусловно в `weed/s3api/s3api_server.go` `go startBucketSizeMetricsLoop(...)`, без привязки к Prometheus/monitoring). Внешний крон (`weed shell s3.bucket.quota.enforce -apply` раз в 5 минут) стал избыточен. **Критическая связка:** удаление крона валидно ТОЛЬКО вместе с bump'ом на 4.31 (на 4.30 нативного энфорса нет) — оба изменения в одном commit. **bucket-sync не тронут** — Phase E по-прежнему ЗАДАЁТ квоты (`s3.bucket.quota -op=set`); «энфорсит» теперь gateway. Orphan'ов не осталось: quota-cron chart своего NP/RBAC не имел (pod ходил к master/filer через общий `allow-internal-traffic` NP в `seaweedfs/pre/`). Бонусом приехали фиксы #9755 (write-stall на auto-sized дисках `maxVolumes: 0`), #9772 (multipart-ETag совместимость для Loki/GitLab), #9760 (S3 отклоняет bucket с именем `filemeta` — у нас таких нет). seaweedfs не в `hosts-vars-test/upstream-charts.yaml` → helm-validate Layer 2 этот chart не рендерит; bump проверяется yamllint/syntax.
- **ESO/Vault verify → Python filter plugins** перенёс validation-логику двух pure-validation task'ей (`tasks-vault-config-verify.yaml` + `tasks-eso-verify.yaml`) из сырого Ansible Jinja2 в два независимых Python filter plugin'а: `filter_plugins/vault_config_verify.py` (global — policy/role uniqueness + referential integrity) и `filter_plugins/eso_verify.py` (per-component — Groups B/C/D: SecretStore→Vault connectivity scoped к role, ESO uniqueness, policy path coverage). Каждый фильтр stateless: принимает pre-merged `vault_policies`/`vault_roles` (merge `base + (extra | default([]))` делается в YAML-wrapper'е), возвращает `list[str]` нарушений и **не кидает**. YAML-task'и ужаты до тонких wrapper'ов: Rule-19 input-assert (для eso-verify — бывшая Group A) + `set_fact` `_local_error_item_list` (вызов фильтра) + `assert length == 0` (fail с полным multi-violation отчётом) — паттерн зеркалит `seaweedfs_buckets_immutable_violations` + его assert. Callers (config-verify 13, eso-verify 11) и dto-контракт не изменились. Поведение pass/fail сохранено; два latent-нюанса исправлены: (1) старый `Duplicates: {{ _names | difference(_names | unique) }}` всегда давал пустой список — `_find_duplicates` теперь показывает реальные дубликаты; (2) substring-проверка SA/namespace binding нормализована в exact membership через `_as_list` (идентично для реальных данных). Pytest: `tests/python/test_vault_config_verify.py` (13 cases) + `tests/python/test_eso_verify.py` (23 cases) — Layer 3 в `make test` (total pytest 83). `tasks-eso-verify.yaml` НЕ вызывается из `tests/helm-validate.yaml` (нет component scope); `tasks-vault-config-verify.yaml` — вызывается.
- **seaweedfs architecture v11 (collection removal + rack/dataCenter soft tier placement + volume tier topology)** убрал поле `collection` из bucket-схемы полностью и добавил два optional **immutable** поля `rack` + `dataCenter` для мягкого тир-размещения бакетов. **Causa (verified в `sources/seaweedfs` v4.31, `weed/shell/command_fs_configure.go:104-106`):** `fs.configure -collection=<c>` на путях `/buckets/*` отвергается ранним `return` ДО `AddLocationConf` → `replication` (и любые поля после) **не сохранялись ни для одного бакета** — все ехали на master `defaultReplication` вместо своей. collection вообще non-configurable (выводится из имени бакета, `filer_util.go:191`; у `s3.bucket.create` нет флага `-collection`). Фикс: убран `-collection` из Phase C2 fs.configure → `replication` применяется; команда стала `-locationPrefix=/buckets/<n> -replication=<r> [-rack=<rack>] [-dataCenter=<dc>] -apply`. **rack/dataCenter (verified `-rack`/`-dataCenter` — валидные flags `command_fs_configure.go:69/68`, persist'ятся в locConf; soft placement — `assign_file_id.go` altRequest при NoWritableVolumes спилит на другой тир):** optional, immutable (как replication — смена post-create → data fragmentation, fail-fast). bucket-схема `{name, collection, replication, ...}` → `{name, replication, rack?, dataCenter?, quota?, policy?}`. **Volume topology:** singular worker-only `volume:` → `volumes:` (plural) тир-группы `managers-1-dc-1` (3×control-plane, rack `managers-1`) + `workers-1-dc-1` (5×worker, rack `workers-1`), dataCenter `dc-1`; rack-метка = логический ТИР + номер физ.rack (в одном DC физически может быть несколько rack; дефис в именах rack/dataCenter разрешён — topology node-id plain string без charset-валидации). master HA отложена (replicas:1). **Filter:** `_validate_buckets_have_collection_and_replication` → `_validate_buckets_have_replication` (collection requirement убран, rack/dataCenter optional-string validation); `_compute_bucket_immutable_violations` сравнивает replication+rack+dataCenter. 11 public filters без изменений. Pytest `tests/python/test_seaweedfs_sync.py` 47→**49 cases**. **Breaking-ish:** bucket `collection:` в existing override — поле теперь мёртвое/ignored (убрать опционально); bucket rack/dataCenter значения должны совпадать с volume-group метками (`workers-1`/`managers-1`, `dc-1`).
- **seaweedfs admin + worker components + UI subdomain split (v12)** включил два upstream-компонента chart 4.31.0 (`admin.enabled` StatefulSet + `worker.enabled` Deployment) и переделал UI-доступ. **UI:** прежний совмещённый `adminUiIngressConfig` (path-routing — master через `PathPrefix(/master)` + filer catch-all на одном FQDN, ошибочный нейминг) заменён тремя Host-only IngressRoute на раздельных поддоменах (master/filer/admin, **без** path-prefix) + S3 endpoint = 4 доступа, все ACME-TLS, VPN off (тестовая фаза). Post chart: `ingress-admin-ui.yaml` удалён → `ingress-{master,filer,admin}.yaml`; `certificate.yaml` 1→4 UI cert-блока. Inventory: `seaweedfs_admin_ui_*` → `seaweedfs_{s3,master,filer,admin}_ui_*` (domain/tls/vpn) + три `*_ingress_config`; `post_helm_values` `adminUiIngressConfig` → `masterIngressConfig`/`filerIngressConfig`/`adminIngressConfig`. **admin:** service `seaweedfs-admin:http`/23646 (+grpc 33646). Login/password через новый (3-й) ESO secret `seaweedfs_secret_admin_ui_creds` (Vault `/seaweedfs/admin-ui/creds`, поля `adminUser`/`adminPassword`, simple `dataFrom.extract` → K8s Secret `eso-seaweedfs-admin-ui-creds`) → `admin.secret.existingSecret` → `WEED_ADMIN_USER`/`WEED_ADMIN_PASSWORD`. Seed — `seaweedfs-install.yaml` тег `[install]` перед main helm (зеркалит postgres-seed: vault-get/generate/put/eso-sync/wait). **Persistence — только PVC** (`data.type: persistentVolumeClaim`, lnstr-major-multi-sync, 2Gi; чарт авто `-dataDir=/data`): SQL-бэкенда у admin НЕТ — verified `sources/seaweedfs/weed/admin/dash/config_persistence.go` (чистый `os.ReadFile/WriteFile`; хранит session keys + maintenance/task config + историю задач). Логи stdout (`logs.type: ""`). **worker:** Deployment, `jobType "all"`, требует admin (чарт hard-fail'ит без `admin.enabled`; авто-коннект к in-cluster admin gRPC), stateless, логи stdout; NP не нужен (intra-ns `allow-internal-traffic`). **NetworkPolicy (pre chart):** +`seaweedfs-admin` (Traefik→23646 + apiserver egress); `seaweedfs-master` +Traefik ingress (9333); `<ns>-allow-traefik` egress +master+admin; `adminHttpPort: 23646` в pre values; 10→11 NP. **ESO:** seaweedfs ESO secrets 2→3; Vault policy glob `eso-secret/data/seaweedfs/*` уже покрывает `/seaweedfs/admin-ui/creds` — `vault.yaml` без изменений. `make test` зелёный (seaweedfs локальные charts helm-validate не рендерит — проверка yamllint/ansible-lint/syntax/pytest; helm-рендер admin/worker верифицирован вручную против `sources/seaweedfs` 4.31.0).
- **seaweedfs upstream metrics migration (v13)** заменил самописный ServiceMonitor (`charts/seaweedfs/post/templates/service-monitor.yaml` + inventory `seaweedfs_service_monitor`, 4 компонента master/volume/filer/s3) на встроенные upstream-метрики + ServiceMonitor chart 4.31.0. **Включение:** `seaweedfs_helm_values.global.seaweedfs.monitoring.enabled: true` (default false) → upstream рендерит SM для master/filer/s3/worker + 2 volume тир-группы (port `metrics`/9327) + admin (port `http`/23646 — `weed admin` отдаёт `/metrics` на главном http-порту через `promhttp`, до auth-middleware → без авторизации; verified `weed/admin/handlers/admin_handlers.go:67`). interval/scrapeTimeout захардкожены upstream (30s/5s), не конфигурируемы. **Причина переписать:** самописный SM покрывал лишь 4 компонента (терял worker+admin) и **никогда не работал** — у seaweedfs не было NP `allow-for-monitoring` (есть у vault/traefik/argocd/longhorn), поэтому скрейп Prometheus'ом блокировался baseline `deny-all`. **NP:** добавлен `allow-for-monitoring` в `seaweedfs/pre/` (`podSelector: {}`, ingress на `metricsPort` 9327 + `adminHttpPort` 23646, **без `from:`** — open-from-anywhere по project-convention; открывает 23646 cluster-internally для admin `/metrics`, ослабляя Traefik-only ограничение admin-NP — идентично vault'овому открытию 8200; admin UI остаётся за login/password). `metricsPort: 9327` добавлен в `seaweedfs/pre/values.yaml`; pre-NP 11→12. **Volume metrics:** тир-группы наследуют `metricsPort: 9327` из `.Values.volume` через chart per-group `mergeOverwrite` — отдельно задавать не надо. **Discovery:** mon-system Prometheus `serviceMonitorSelector: {}` → подхватывает SM без лейблов. **Удалено:** post `service-monitor.yaml` + post-values `serviceMonitor:` блок + мёртвые `seaweedfs.*MetricsPort` reference-значения + inventory `seaweedfs_service_monitor` + проводка. Verified: helm-render вендорённого upstream chart (6 SM kinds, admin via `port: http`) + post chart (0 SM). `make test` зелёный (seaweedfs локальные charts helm-validate не рендерит).
- **seaweedfs IAM v14 (filer-driven + identity-based access)** перевёл S3 IAM со static-config mount на filer-driven модель + identity-based доступ (managed policies на identity вместо bucket policies). **Корень проблемы:** static identities (смонтированный K8s Secret через `s3.existingConfigSecret`) — in-memory оверлей процесса s3-gateway (`IsStatic`), НЕ пишутся в filer → admin UI (читает filer через gRPC) показывал пустые Users/Policies (buckets видны — реальные записи `/buckets/` в filer). **Решение:** filer становится единственным источником IAM (durable, Postgres). **empty-config nuance (load-bearing):** `existingConfigSecret` нельзя просто убрать (чарт сгенерил бы дефолтные `anvAdmin`/`anvReadOnly` → Merge-режим, static identities невидимы) — поэтому ESO-secret `seaweedfs_secret_s3_identities` **retarget'нут** (не удалён) в `seaweedfs_secret_s3_bootstrap`: Vault path `/seaweedfs/s3-config/all` → `/seaweedfs/s3-config/bootstrap`, K8s Secret `seaweedfs-s3-identities` → `seaweedfs-s3-empty-config`, содержимое — empty static config `{"identities":[]}` (`staticIdentityNames=0` → `hasStaticConfig=false` → `ReplaceS3ApiConfiguration`, чистый filer-driven; чарт не генерит anvAdmin/anvReadOnly т.к. existingConfigSecret задан). Combined identity JSON `/seaweedfs/s3-config/all` остаётся как **plain key-store** (доступ через новые plain-vars `seaweedfs_s3_config_vault_path`/`_field`, НЕ ESO secret, в K8s НЕ материализуется; read/write только Ansible sync). ESO-массив seaweedfs остаётся **3** (postgresql + s3-bootstrap + admin-ui). Старый K8s Secret `seaweedfs-s3-identities` осиротеет при upgrade (безвредно, как orphan-ConfigMap; оператор может `kubectl delete secret seaweedfs-s3-identities`). Bootstrap Vault path сидится playbook'ом (seed-if-not-exists, install phase). **Layer P (новый):** managed policies — inventory `seaweedfs_managed_policies`/`_extra` (`{name, document}`, одна policy на consumer) → `weed shell s3.policy -put/-delete` → filer `/etc/iam/policies/`, diff vs ConfigMap `seaweedfs-sync-policies-state`; новый task `tasks-seaweedfs-policy-sync.yaml` (tag `policy-sync`, выполняется ДО user-sync). **Identity-based:** identity получает inventory-поле `policy_names` (attached managed policy через `s3.configure -policies=<csv>`); `actions=[]` + `policy_names=[<p>]` = доступ через managed policy Allow (admin: `actions=[Admin]` → isAdmin() bypass; anonymous — empty creds, в filer не апплаится). Старая модель «policy-only principals через bucket policy Allow» убрана. **Bucket:** `owner` (identity, required) вместо bucket policy — `s3.bucket.create -owner=<owner>` + reconcile existing через `s3.bucket.owner` (owner mutable); per-bucket `policy` field + bucket policies удалены полностью (owner НЕ влияет на policy-check — доступ к данным через managed policy identity). **user-sync переделан** (`tasks-seaweedfs-user-sync.yaml`): применяет identities в живой filer через `weed shell s3.configure -apply` (live-reload, без рестарта S3); ESO-force-sync + wait-secret + conditional rollout-restart для identities удалены; Vault combined JSON остаётся key-store (vault-put when changed). **bucket-sync переделан** (`tasks-seaweedfs-bucket-sync.yaml`): owner вместо policy phases (Phase A delete → B create `-owner` → C fs.configure → D owner-reconcile → E quotas → F ConfigMap); admin-creds fetch + aws-cli helper exec удалены. **aws-cli helper Deployment удалён** (`charts/seaweedfs/pre/templates/aws-cli-deployment.yaml` + `awsCliHelper` values) — был нужен только для `aws s3api put/delete-bucket-policy`; SeaweedFS shell не имеет bucket-policy команд, но managed-policy команды (`s3.policy`) есть и идут к filer по gRPC (S3 creds не нужны). NP-счётчик `seaweedfs/pre/` без изменений (aws-cli helper был покрыт `allow-internal-traffic`, dedicated NP не имел). **Порядок фаз `seaweedfs-install.yaml`:** `pre → postgresql → install → policy-sync → user-sync → identity-distribute → bucket-sync → post` (весь sync ПОСЛЕ install — `weed shell` требует running filer; bootstrap auth-окно между install и user-sync принято, риск §3 #6). **Python фильтры** (`filter_plugins/seaweedfs_sync.py`): +3 Layer P (`seaweedfs_policies_to_put`/`_to_delete`/`_new_state_json`) + `seaweedfs_identities_to_delete` + `seaweedfs_buckets_owners_to_set`; −2 bucket-policy (`seaweedfs_bucket_policies_to_delete`/`_to_apply`) + 2 private (`_validate_principal_not_dict`/`_in_buckets`); identity-build +`policy_names`, `_compute_bucket_diff` +`owner`/`owners_to_set` (policy keys убраны). Итого **14 public фильтров** (было 11). Pytest `tests/python/test_seaweedfs_sync.py` — **57 cases** (было 49: +9 Layer P, +6 v14-additions, −7 bucket-policy); total `make test` pytest 93. **PoC пропущен** (риск empty-config behavior §4.2 + `weed shell` флагов `s3.policy -put -file` / `s3.configure -policies` / `s3.bucket.owner` принят оператором; первая реальная валидация — применением на dev). `make test` зелёный (seaweedfs локальные charts helm-validate не рендерит — yamllint/ansible-lint/syntax/pytest).

# Secrets & ESO Deep Dive

Depth reference for the Vault + External Secrets Operator subsystem. For the big picture, see `CLAUDE.md` §6. For the individual Vault/ESO task includes, see [`reusable-tasks.md`](reusable-tasks.md) §1.11–§1.15. For which components are ESO-integrated, see [`components.md`](components.md) §23.

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
    policy: |
      path "eso-secret/data/argocd/*"  { capabilities = ["read"] }
      path "eso-secret/metadata/argocd/*" { capabilities = ["read"] }
```

Extension: `vault_policies_extra` (overrides can append policies without forking base).

### 2.2 `vault_roles` (list of dicts)

Each entry binds a Kubernetes ServiceAccount + namespace to one or more policies via the `kubernetes` auth method:

```yaml
vault_roles:
  - name: "eso-argocd"
    bound_service_account_names: ["argocd-eso-sa"]
    bound_service_account_namespaces: ["argocd"]
    policies: ["eso-argocd"]
    token_ttl: "1h"
    token_max_ttl: "24h"
```

Extension: `vault_roles_extra`.

### 2.3 `eso_vault_integration_<c>` (object, per component)

```yaml
eso_vault_integration_<c>:
  sa_name: "<c>-eso-sa"
  role_name: "eso-<c>"               # must exist in vault_roles_final
  secret_store_name: "<c>-eso-secret-store"
  kv_engine_path: "eso-secret"
  is_need_eso: true
```

### 2.4 `eso_vault_integration_<c>_secrets` (list, per component)

Each entry declares one `ExternalSecret` to be materialized into a K8s `Secret`:

```yaml
eso_vault_integration_<c>_secrets:
  - name: "<c>-admin"                 # K8s Secret name
    type: "Opaque"                    # K8s Secret type
    refresh_interval: "1m"            # ExternalSecret.spec.refreshInterval
    data:
      - secret_key: "password"        # key in the target K8s Secret
        remote_ref_key: "<c>/admin"   # path under kv_engine_path
        remote_ref_property: "password"
```

Extension: `eso_vault_integration_<c>_secrets_extra`.

Runtime-produced merged view: `eso_vault_integration_<c>_secrets_merged = base + extra`.

---

## 3. `tasks-eso-merge.yaml` — the contract

**Input.** None (reads all vars by convention).

**Output.** Sets three categories of facts:

| Fact | Source | Shape |
|---|---|---|
| `vault_policies_final` | `vault_policies + (vault_policies_extra | default([]))` | list of policy dicts |
| `vault_roles_final` | `vault_roles + (vault_roles_extra | default([]))` | list of role dicts |
| `eso_vault_integration_<c>_secrets_merged` (× 9 components) | per-component merge | list of ExternalSecret dicts |

**Validation.** Fails the play if any of:

- Duplicate policy names within `vault_policies_final`.
- Duplicate role names within `vault_roles_final`.
- A role references a policy not in `vault_policies_final`.
- An `eso_vault_integration_<c>.role_name` is not present in `vault_roles_final`.
- For `argocd` + `argocd_git_ops` (shared namespace): duplicate K8s `Secret` names across the two lists.

**Usage.** Call once, with `tag: [always]`, near the top of:

- Every ESO-integrated component's install playbook (so its `pre/` chart sees `*_secrets_merged`).
- `vault-install.yaml` (so the Vault CR values get `vault_policies_final` and `vault_roles_final`).

**Idempotency.** Pure merge + assertion. No side effects.

---

## 4. The Five Vault/ESO Task Primitives

| Task | Use |
|---|---|
| `tasks-vault-get.yaml` | Read a single KV field into a named fact + `<fact>_exists` boolean. Safe on missing paths. |
| `tasks-vault-put-and-sync.yaml` | `vault kv put` + annotate ExternalSecret + wait for target K8s Secret to be present/updated. |
| `tasks-generate-secret.yaml` | Generate random N-char secret into a named fact. |
| `tasks-eso-force-sync.yaml` | Annotate ExternalSecrets with `force-sync=<epoch>` to trigger ESO reconciliation. |
| `tasks-vault-distribute-creds.yaml` | Read `vault-unsealer-secret` from cluster and write `/etc/kubernetes/vault-unseal.json` on all managers. |

Full contracts (input/output/callers) in [`reusable-tasks.md`](reusable-tasks.md).

---

## 5. SecretStore + ExternalSecret Templates

Rendered by each component's `<c>/pre/` Helm chart from `eso_vault_integration_<c>_secrets_merged`.

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

### 5.3 ExternalSecret (per entry in merged list)

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: {{ .name }}
  namespace: {{ $.Values.namespace }}
spec:
  refreshInterval: {{ .refresh_interval | default "1m" | quote }}
  secretStoreRef:
    name: {{ $.Values.eso.secretStoreName }}
    kind: SecretStore
  target:
    name: {{ .name }}
    creationPolicy: Owner
    template:
      type: {{ .type | default "Opaque" }}
  data:
    {{- range .data }}
    - secretKey: {{ .secret_key }}
      remoteRef:
        key: {{ .remote_ref_key }}
        property: {{ .remote_ref_property }}
    {{- end }}
```

(The actual `pre/` charts in the repo follow this shape; refer to `charts/<c>/pre/templates/` for the exact current form.)

---

## 6. Secret Flow — Seed vs. Rotation

### 6.1 First-seed (example: `gitlab` root password at install time)

```
1. <c>-install.yaml calls tasks-generate-secret.yaml  → new_password fact
2. tasks-vault-put-and-sync.yaml:
     vault kv put eso-secret/gitlab/gitlab-root password=<new_password>
     kubectl annotate externalsecret gitlab-root force-sync=<epoch>
     wait for K8s Secret "gitlab-root" in ns gitlab
3. Main gitlab chart installs; pods mount the now-present Secret
4. (optional) verify: log in to GitLab with the new password from Vault
```

Idempotency: if `tasks-vault-get.yaml` reports the Vault path already exists, skip step 1–2.

### 6.2 Rotation (example: `gitlab` Postgres password)

```
1. tasks-generate-secret.yaml             → new_pg_password
2. (optional) write to /tmp on DB host, ALTER USER in running postgres
3. tasks-vault-put-and-sync.yaml
     vault kv put eso-secret/gitlab/postgresql password=<new_pg_password>
     annotate + wait for K8s Secret "gitlab-postgresql" to reflect new data
4. (future) Reloader restarts pods that mount the Secret
   — until Reloader is installed, manually run `gitlab-restart.yaml`
5. rm /tmp file
```

---

## 7. Adding a New ESO-integrated Component

Checklist — keep strictly in order.

1. **Add the `eso_vault_integration_<c>` object** in the component's `hosts-vars/<c>.yaml`:
   ```yaml
   eso_vault_integration_<c>:
     sa_name: "<c>-eso-sa"
     role_name: "eso-<c>"
     secret_store_name: "<c>-eso-secret-store"
     kv_engine_path: "eso-secret"
     is_need_eso: true
   ```
2. **Define the base secrets list**:
   ```yaml
   eso_vault_integration_<c>_secrets:
     - name: "<c>-admin"
       type: "Opaque"
       refresh_interval: "1m"
       data:
         - secret_key: "password"
           remote_ref_key: "<c>/admin"
           remote_ref_property: "password"
   ```
3. **Add an `_extra` entry in `hosts-extra.example.yaml`** so users know the extension point exists:
   ```yaml
   eso_vault_integration_<c>_secrets_extra: []
   ```
4. **Add the Vault policy** in `hosts-vars/vault.yaml` → `vault_policies`:
   ```yaml
   - name: "eso-<c>"
     policy: |
       path "eso-secret/data/<c>/*"     { capabilities = ["read"] }
       path "eso-secret/metadata/<c>/*" { capabilities = ["read"] }
   ```
5. **Add the Vault role** in `hosts-vars/vault.yaml` → `vault_roles`:
   ```yaml
   - name: "eso-<c>"
     bound_service_account_names: ["<c>-eso-sa"]
     bound_service_account_namespaces: ["<c>-ns"]
     policies: ["eso-<c>"]
   ```
6. **Update `tasks-eso-merge.yaml`** to include the new component in its merge + validation loop (the 9-component list is hard-coded today — extend it).
7. **Render `ServiceAccount`, `SecretStore`, `ExternalSecret`** in the component's `charts/<c>/pre/templates/` using the patterns in §5.
8. **In `<c>-install.yaml`** include `tasks-eso-merge.yaml` (tag `always`).
9. **Apply the new Vault policy/role**:
   ```
   ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/vault-install.yaml --tags install
   ```
10. **Install the component**:
    ```
    ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/<c>-install.yaml
    ```
11. **First-seed secrets** from a `<c>-configure.yaml` or in-install task using `tasks-generate-secret.yaml` + `tasks-vault-put-and-sync.yaml`.

---

## 8. Rotation Procedures

### 8.1 `vault-rotate.yaml`

- Rekey Shamir shares (generates new shares, threshold preserved).
- Rotate root token.
- Update `vault-unsealer-secret` in cluster.
- Redistribute `/etc/kubernetes/vault-unseal.json` on every manager via `tasks-vault-distribute-creds.yaml`.
- Uses state files in `{{ etcd_rotation_state_dir }}` for resume safety; see `bootstrap-and-ha.md` §3 for the state-file resume pattern.

### 8.2 Component credential rotation

Generic template (see §6.2 for concrete example):

1. `tasks-vault-get.yaml` — fetch current creds (if needed for DB ALTER statements).
2. `tasks-generate-secret.yaml` — new value.
3. Apply to the running workload (DB, IdP, etc.).
4. `tasks-vault-put-and-sync.yaml` — persist + force ESO re-sync.
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
| `argocd` | `eso-secret/argocd/*` | `admin` (password), optional OIDC client-secret |
| `argocd-git-ops` | `eso-secret/argocd-git-ops/*` | repo credentials (SSH keys or tokens), per-repo and pattern-based |
| `grafana` | `eso-secret/grafana/*` | `admin` (password), `oidc` client-secret, datasource creds |

---

## 10. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `ExternalSecret` stuck in `SecretSyncedError` | Vault policy missing `read` on the path, or role doesn't bind the SA | Run `vault-install.yaml --tags install` after updating policies; inspect `ExternalSecret` status for error detail |
| `SecretStore` shows `ValidationFailed` | `role_name` missing in Vault or `kubernetes` auth mount path wrong | Confirm `vault_roles_final` has the role; check bank-vaults logs |
| K8s Secret exists but pod doesn't see new value | Pod was not restarted after Secret change | `<c>-restart.yaml` (Reloader will automate this in future) |
| Vault sealed after reboot | Auto-unseal CronJob didn't run | Run manually on a manager: `kubectl -n vault exec vault-0 -- vault operator unseal <key>` × threshold |
| New manager can't unseal | `/etc/kubernetes/vault-unseal.json` missing | Re-run `tasks-vault-distribute-creds.yaml` (part of `manager-join.yaml`) |
| `tasks-eso-merge.yaml` fails "duplicate policy" | Base + `_extra` both define the same policy name | Remove duplicate from `_extra` |
| `tasks-eso-merge.yaml` fails "argocd/argocd_git_ops duplicate" | Both lists define a Secret with the same `name` in the shared `argocd` namespace | Rename one |

---

## 11. Migration Notes

(Deliberate differences from the previous `components.md` ESO section.)

- The previous `components.md` called the per-component ESO object "Vault ESO integration configured in `hosts-vars/vault.yaml`" — actually most `eso_vault_integration_<c>` objects live in `hosts-vars/<c>.yaml` (per-component), and `hosts-vars/vault-eso.yaml` holds the cross-component bits. Corrected in §2.3 above.
- "ServiceAccount allowed to authenticate" was under-specified; the precise `bound_service_account_names` + `bound_service_account_namespaces` list is now in §2.2.

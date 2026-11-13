> Actual for commit: a451e66

# DATA — k8s-ansible (1520-tech-ansible)

## Data stores

| Store | Role | Where configured |
|---|---|---|
| ETCD | cluster state, encryption-at-rest (aescbc `EncryptionConfiguration` generated at init) | `playbook-system/cluster-init.yaml:44-82`; rotation `playbook-system/utils/etcd-key-rotate.yaml` |
| Vault | cluster secret store (StatefulSet via bank-vaults operator) | `hosts-vars/vault.yaml` (OCI chart `:226`, unseal keys `:244-247`); rekey `playbook-app/vault-rotate.yaml` |
| Postgres ×5 | single-instance helm subcharts: gitlab (appVersion 16), outline (17), zitadel (16), seaweedfs (16), mon-system/grafana (16) | respective charts under `playbook-app/charts/` |
| Redis | gitlab (7.2), outline (7.4) | respective charts under `playbook-app/charts/` |
| Loki | logs on SeaweedFS S3 | `hosts-vars/mon-system.yaml:284-299` |
| SeaweedFS | central S3 (Loki + component buckets/users/policies) | `playbook-app/seaweedfs-install.yaml`, `filter_plugins/seaweedfs_*.py` |
| Teleport | SQLite backend | teleport component config |

**No traditional app-DB migration system**: all stores are helm-rendered StatefulSets; there are no migration jobs anywhere in `playbook-app/`.

## Secrets data model

Vault paths per component (SecretStore/ExternalSecret contracts, seed vs rotation): [`reference/secrets-and-eso.md`](../../reference/secrets-and-eso.md). No secret values live in docs by design.

## Sensitive data

`hosts-vars-override/**` is the secrets zone (committed deliberately to this private repo) — see [`INVARIANTS.md`](INVARIANTS.md) rule 2: never read/print/copy values; new secrets never go anywhere outside that directory.

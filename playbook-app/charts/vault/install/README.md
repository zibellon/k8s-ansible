# vault/install

Vault is installed via the bank-vaults operator OCI chart:

```
helm upgrade --install vault-operator oci://ghcr.io/bank-vaults/helm-charts/vault-operator
```

The operator manages Vault lifecycle via the `Vault` CRD.
The Vault CR spec is defined in `charts/vault/cr/`.

The old HashiCorp official Helm chart has been removed.

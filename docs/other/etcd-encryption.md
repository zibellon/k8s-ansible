# etcd Encryption at Rest

## Overview

By default, Kubernetes stores all data in etcd **in plaintext**, including Secrets.
`base64` encoding in Secrets is NOT encryption - it's just encoding.

```
Without encryption:  Secret → base64 → etcd (plaintext!)
With encryption:     Secret → base64 → AES-256 encrypt → etcd (encrypted)
```

---

## What to Encrypt

| Resource | Contains sensitive data? | Encrypt? |
|----------|--------------------------|----------|
| **Secrets** | ✅ Passwords, tokens, keys | ✅ Required |
| **ConfigMaps** | ⚠️ Sometimes (configs with passwords) | ⚠️ If needed |
| Pods, Deployments | ❌ Only specs | ❌ No need |
| PVC, Services | ❌ Metadata only | ❌ No need |

**Encryption = overhead**: CPU for encrypt/decrypt on every read/write.
Encrypt only what is truly sensitive.

---

## Setup Guide (kubeadm)

### Step 1: Generate encryption key

```bash
# Generate 32-byte key (256-bit AES)
head -c 32 /dev/urandom | base64
```

Save this key securely! Without it, you cannot decrypt etcd backup.

### Step 2: Create EncryptionConfiguration

Create file on **ALL control-plane nodes**:

```bash
# /etc/kubernetes/pki/encryption-config.yaml
```

```yaml
apiVersion: apiserver.config.k8s.io/v1
kind: EncryptionConfiguration
resources:
  - resources:
      - secrets
      # - configmaps  # uncomment if needed
    providers:
      # First provider is used for encrypting NEW data
      - aescbc:
          keys:
            - name: key1
              secret: <BASE64_ENCODED_32_BYTE_KEY>
      # identity allows reading old unencrypted data (migration)
      - identity: {}
```

Set permissions:

```bash
chmod 600 /etc/kubernetes/pki/encryption-config.yaml
```

### Step 3: Update kubeadm ClusterConfiguration

Add to `apiServer.extraArgs` and `extraVolumes`:

```yaml
apiVersion: kubeadm.k8s.io/v1beta4
kind: ClusterConfiguration
apiServer:
  extraArgs:
    - name: encryption-provider-config
      value: /etc/kubernetes/pki/encryption-config.yaml
  extraVolumes:
    - name: encryption-config
      hostPath: /etc/kubernetes/pki/encryption-config.yaml
      mountPath: /etc/kubernetes/pki/encryption-config.yaml
      readOnly: true
      pathType: File
```

### Step 4: Apply changes

For **existing cluster** - update kube-apiserver manifest:

```bash
# Edit directly (kubeadm will not override)
vim /etc/kubernetes/manifests/kube-apiserver.yaml
```

Add to `spec.containers[0].command`:

```yaml
- --encryption-provider-config=/etc/kubernetes/pki/encryption-config.yaml
```

Add volume and volumeMount:

```yaml
spec:
  containers:
  - name: kube-apiserver
    volumeMounts:
    - name: encryption-config
      mountPath: /etc/kubernetes/pki/encryption-config.yaml
      readOnly: true
  volumes:
  - name: encryption-config
    hostPath:
      path: /etc/kubernetes/pki/encryption-config.yaml
      type: File
```

kube-apiserver will restart automatically after manifest change.

### Step 5: Re-encrypt existing secrets

After enabling encryption, **old secrets remain unencrypted**!

```bash
# Re-encrypt all secrets
kubectl get secrets --all-namespaces -o json | kubectl replace -f -

# Re-encrypt configmaps (if encrypted)
kubectl get configmaps --all-namespaces -o json | kubectl replace -f -
```

---

## Verify Encryption

### Check if encryption is enabled

```bash
# Read secret directly from etcd
ETCDCTL_API=3 etcdctl \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/server.crt \
  --key=/etc/kubernetes/pki/etcd/server.key \
  get /registry/secrets/default/my-secret
```

- **Without encryption**: plaintext data visible
- **With encryption**: binary blob starting with `k8s:enc:aescbc:v1:key1:`

### Check apiserver logs

```bash
# Should see encryption-provider-config loaded
kubectl logs -n kube-system kube-apiserver-<node> | grep -i encrypt
```

---

## Key Rotation

### Step 1: Add new key (first in list)

```yaml
apiVersion: apiserver.config.k8s.io/v1
kind: EncryptionConfiguration
resources:
  - resources:
      - secrets
    providers:
      - aescbc:
          keys:
            - name: key2           # NEW key - first position
              secret: <NEW_KEY>
            - name: key1           # OLD key - for decrypting old data
              secret: <OLD_KEY>
      - identity: {}
```

### Step 2: Restart apiserver

```bash
# For kubeadm - touch manifest to trigger restart
touch /etc/kubernetes/manifests/kube-apiserver.yaml

# Or restart kubelet
systemctl restart kubelet
```

### Step 3: Re-encrypt all secrets with new key

```bash
kubectl get secrets --all-namespaces -o json | kubectl replace -f -
```

### Step 4: Remove old key

```yaml
apiVersion: apiserver.config.k8s.io/v1
kind: EncryptionConfiguration
resources:
  - resources:
      - secrets
    providers:
      - aescbc:
          keys:
            - name: key2           # Only new key remains
              secret: <NEW_KEY>
      - identity: {}
```

### Step 5: Restart apiserver again

```bash
touch /etc/kubernetes/manifests/kube-apiserver.yaml
```

---

## Available Encryption Providers

| Provider | Description | Recommendation |
|----------|-------------|----------------|
| `identity` | No encryption | Migration only |
| `aescbc` | AES-256 CBC | ✅ Recommended for bare-metal |
| `aesgcm` | AES-256 GCM | ✅ Faster, requires frequent rotation |
| `secretbox` | XSalsa20 + Poly1305 | ✅ Good choice |
| `kms` | External KMS | Best, but requires KMS provider |

For bare-metal without external KMS - use `aescbc` or `secretbox`.

---

## HA Cluster Notes

**CRITICAL**: encryption-config.yaml must be **identical on ALL control-plane nodes**!

```bash
# Copy to all control-plane nodes
for node in k8s-manager-2 k8s-manager-3; do
  scp /etc/kubernetes/pki/encryption-config.yaml root@$node:/etc/kubernetes/pki/
done
```

---

## Backup Considerations

1. **Always backup encryption key** separately from etcd backup
2. Without encryption key, etcd backup is **useless** (cannot decrypt)
3. Store key in secure location (password manager, HSM, offline)

---

## Limitations

- **Not end-to-end encryption**: data is decrypted in apiserver memory
- Anyone with Kubernetes API access (and RBAC permissions) can read Secrets
- Protects only against:
  - etcd backup theft
  - Direct etcd access
  - Disk theft

For Vault unseal keys - encryption at rest is **one layer**, but storing them outside cluster (Transit Vault, offline) is more secure.

---

## Ansible Integration

To integrate with `init-cluster.yaml`, add these tasks before `kubeadm init`:

```yaml
# Generate encryption key (only on first init)
- name: Generate encryption key
  shell: head -c 32 /dev/urandom | base64
  register: encryption_key
  when: not kubeadm_already_init_pre_check.stat.exists

# Create encryption config
- name: Create encryption config
  copy:
    dest: /etc/kubernetes/pki/encryption-config.yaml
    mode: '0600'
    content: |
      apiVersion: apiserver.config.k8s.io/v1
      kind: EncryptionConfiguration
      resources:
        - resources:
            - secrets
          providers:
            - aescbc:
                keys:
                  - name: key1
                    secret: {{ encryption_key.stdout }}
            - identity: {}
  when: not kubeadm_already_init_pre_check.stat.exists
```

And update kubeadm config to include encryption args.

---

## Quick Reference

```bash
# Generate key
head -c 32 /dev/urandom | base64

# Re-encrypt secrets
kubectl get secrets -A -o json | kubectl replace -f -

# Verify encryption
ETCDCTL_API=3 etcdctl get /registry/secrets/default/<secret-name> \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/server.crt \
  --key=/etc/kubernetes/pki/etcd/server.key

# Restart apiserver (kubeadm)
touch /etc/kubernetes/manifests/kube-apiserver.yaml
```

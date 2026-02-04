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

# Проверить

## Где находится конфиг
1. /etc/kubernetes/pki/encryption-config.yaml - сам конфиг
2. /etc/kubernetes/manifests/kube-apiserver.yaml - подключение к api-server

## Создать тестовый секрет
kubectl create secret generic encryption-test --from-literal=secret=verysecret

## Посмотреть, как он выглядит в ETCD
## Вариант_1. Но он НЕТОЧНЫЙ. Так как могут быть преколы с байтами и так далее
kubectl exec -it etcd-k8s-manager-1 -n kube-system -- etcdctl \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/server.crt \
  --key=/etc/kubernetes/pki/etcd/server.key \
  get /registry/secrets/default/encryption-test

## Вариант_2. Он точный
kubectl exec -it etcd-k8s-manager-1 -n kube-system -- etcdctl \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/server.crt \
  --key=/etc/kubernetes/pki/etcd/server.key \
  get /registry/secrets/default/encryption-test | hexdump -C | head -5

## Вариаент_3. Он точный
kubectl exec -it etcd-k8s-manager-1 -n kube-system -- etcdctl \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/server.crt \
  --key=/etc/kubernetes/pki/etcd/server.key \
  get /registry/secrets/default/encryption-test --print-value-only | strings | head -1

## Если он зашифрован - в выводе что-то непонятное
`k8s:enc:aescbc:v1:key1769554315:.........`
aescbc - алгоритм
key1769554315 - название ключа
## Если не зашифровано - в выводе будет реальное значение

## После тестов удалить 
kubectl delete secret encryption-test


## Key Rotation - https://kubernetes.io/docs/tasks/administer-cluster/encrypt-data

## Общие концепции

### Что такое Vault

Vault — это инструмент для безопасного хранения и управления секретами (пароли, API-ключи, сертификаты). Vault шифрует все данные перед записью на диск и требует аутентификации для доступа.

### Unseal Keys (мастер-ключи)

При инициализации Vault генерирует `мастер-ключ`, который используется для расшифровки данных. Этот ключ разбивается на несколько частей по алгоритму Shamir's Secret Sharing:

- `key-shares` — количество частей, на которые разбивается ключ (например: 3)
- `key-threshold` — минимальное количество частей для восстановления ключа (например: 2)

### Процесс Unseal

1. После перезапуска Vault находится в состоянии `sealed` (запечатан)
2. Данные зашифрованы и недоступны
3. Для распечатывания нужно ввести `key-threshold` unseal-ключей
4. После успешного unseal Vault готов к работе

```
┌─────────────────────────────────────────────────────────────┐
│                    VAULT SEALED STATE                       │
│                                                             │
│  Master Key разбит на 3 части (key-shares=3)                │
│  Для unseal нужно 2 части (key-threshold=2)                 │
│                                                             │
│  Unseal Key 1: ████████████████████████                     │
│  Unseal Key 2: ████████████████████████                     │
│  Unseal Key 3: ████████████████████████                     │
│                                                             │
│  [Key 1] + [Key 2] → Master Key → UNSEALED                  │
│  [Key 1] + [Key 3] → Master Key → UNSEALED                  │
│  [Key 2] + [Key 3] → Master Key → UNSEALED                  │
└─────────────────────────────────────────────────────────────┘
```

### Root Token. это токен с неограниченными правами доступа в Vault. Создаётся один раз при инициализации.
- Используется только для начальной настройки
- После настройки рекомендуется отозвать
- Для повседневной работы используются токены с ограниченными правами

### Secrets Engines. Это компонент Vault, который хранит, генерирует или шифрует данные.

- `KV v2`: Key-Value хранилище с версионированием
  - Хранение паролей, API-ключей
- `PKI`: Генерация сертификатов
  - TLS-сертификаты для сервисов
- `Transit`: Шифрование как сервис
  - Шифрование данных приложения
- `Database`: Динамические credentials для БД
  - Временные пароли для PostgreSQL

Например используем `KV v2` по пути `secret/`:

```bash
# Enable KV v2 engine
vault secrets enable -path=secret kv-v2

# Write secret
vault kv put secret/myapp/config username=admin password=secret123

# Read secret
vault kv get secret/myapp/config
```

### Policies. это набор правил, определяющих какие операции разрешены для определённых путей.

### Capabilities
- `create`: Создание новых секретов
- `read`: Чтение секретов
- `update`: Обновление существующих секретов
- `delete`: Удаление секретов
- `list`: Просмотр списка секретов

### Пример policy

```hcl
# Policy для приложения myapp - только чтение своих секретов
path "secret/data/myapp/*" {
  capabilities = ["read", "list"]
}

path "secret/metadata/myapp/*" {
  capabilities = ["read", "list"]
}
```

### Auth Methods и Roles. способ аутентификации в Vault (Kubernetes, LDAP, Token, etc.)
## Role — связывает identity (ServiceAccount) с policy

```
┌────────────────────────────────────────────────────────────┐
│                    AUTH FLOW                               │
│                                                            │
│  ServiceAccount ──► Auth Method ──► Role ──► Policy        │
│       │                  │            │          │         │
│       │                  │            │          ▼         │
│  (K8s JWT token)    (kubernetes)  (binding)  (permissions) │
└────────────────────────────────────────────────────────────┘
```

## Установка

Vault устанавливается через Ansible playbook в 4 этапа:

```bash
ansible-playbook -i hosts.yaml playbook-app/vault-install.yaml --limit <master_manager>
```

- `vault-pre`: NetworkPolicies
- `vault`: Официальный Helm chart (hashicorp/vault)
- `vault-self-eso`: ESO интеграция для root credentials
- `vault-post`: Ingress для UI

## Инициализация. При первой установке Vault автоматически инициализируется

1. `vault operator init` — генерация unseal keys и root token
2. `vault operator unseal` — распечатывание (автоматически вводятся key-threshold ключей)
3. `vault auth enable kubernetes` — включение Kubernetes auth
4. `vault secrets enable kv-v2` — включение KV secrets engine
5. Создание admin policy и role
6. Сохранение credentials в Vault — unseal keys и root token сохраняются в `secret/vault/credentials`
7. Синхронизация в K8s Secret через ESO

### Результат инициализации

```
==============================================
VAULT INITIALIZATION RESULT
==============================================
Unseal Key 1: ████████████████████████████████████████████
Unseal Key 2: ████████████████████████████████████████████
Unseal Key 3: ████████████████████████████████████████████
Root Token: hvs.████████████████████████████
==============================================
IMPORTANT: Save these credentials securely!
==============================================
```

## Интеграция с Kubernetes. Kubernetes Auth Method
## Kubernetes Auth позволяет подам аутентифицироваться в Vault используя ServiceAccount JWT token.

1. Pod использует ServiceAccount
2. ServiceAccount имеет JWT token (автоматически монтируется в pod)
3. Pod отправляет JWT token в Vault
4. Vault проверяет token через Kubernetes API
5. При успехе Vault выдаёт Vault token согласно связанной role

```
┌──────────────┐     JWT token      ┌──────────────┐
│     Pod      │ ─────────────────► │    Vault     │
│              │                    │              │
│  ServiceAcc  │     Vault token    │  K8s Auth    │
│  JWT token   │ ◄───────────────── │  Role+Policy │
└──────────────┘                    └──────────────┘
        │                                  │
        │                                  │
        ▼                                  ▼
┌──────────────┐                    ┌──────────────┐
│  Kubernetes  │ ◄──── verify ───── │  Kubernetes  │
│     API      │                    │     API      │
└──────────────┘                    └──────────────┘
```

### Связка Role + Policy + ServiceAccount. Три компонента для доступа приложения к секретам

- ServiceAccount: Kubernetes (namespace приложения)
  - Идентификация пода
- Policy: Vault
  - Права доступа к секретам
- Role: Vault (auth/kubernetes/role/)
  - Связь SA → Policy

### Пример связки для приложения `myapp` в namespace `ns-myapp`

```bash
# 1. ServiceAccount (в Kubernetes)
kubectl create sa myapp-vault-auth -n ns-myapp

# 2. Policy (в Vault)
vault policy write myapp-policy - <<EOF
path "secret/data/myapp/*" {
  capabilities = ["read", "list"]
}
path "secret/metadata/myapp/*" {
  capabilities = ["read", "list"]
}
EOF

# 3. Role (в Vault)
vault write auth/kubernetes/role/myapp-role \
  bound_service_account_names=myapp-vault-auth \
  bound_service_account_namespaces=ns-myapp \
  policies=myapp-policy \
  ttl=1h
```

---

## Интеграция с External Secrets Operator
## External Secrets Operator (ESO) — синхронизирует секреты из Vault в Kubernetes Secrets.

- SecretStore: Конфигурация подключения к Vault (namespace-scoped)
- ClusterSecretStore: Глобальная конфигурация подключения (cluster-scoped)
- ExternalSecret: Описание какие секреты синхронизировать

### Как работает синхронизация

```
┌─────────────────────────────────────────────────────────────────────┐
│                                                                     │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────────┐  │
│  │   Vault     │    │    ESO      │    │      Kubernetes         │  │
│  │             │    │  Operator   │    │                         │  │
│  │ secret/     │◄───│             │───►│  Secret: myapp-secrets  │  │
│  │ myapp/      │    │ External    │    │    DB_PASSWORD: ***     │  │
│  │ config      │    │ Secret      │    │    API_KEY: ***         │  │
│  │             │    │             │    │                         │  │
│  └─────────────┘    └─────────────┘    └─────────────────────────┘  │
│                            │                                        │
│                            ▼                                        │
│                     ┌─────────────┐                                 │
│                     │ SecretStore │                                 │
│                     │ (auth via   │                                 │
│                     │  K8s SA)    │                                 │
│                     └─────────────┘                                 │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### Пример SecretStore и ExternalSecret

```yaml
apiVersion: external-secrets.io/v1
kind: SecretStore
metadata:
  name: vault-backend
  namespace: ns-myapp
spec:
  provider:
    vault:
      server: http://vault.vault.svc.cluster.local:8200
      path: secret
      version: v2
      auth:
        kubernetes:
          mountPath: kubernetes
          role: myapp-role                    # Vault role
          serviceAccountRef:
            name: myapp-vault-auth            # K8s ServiceAccount
```

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: myapp-secrets
  namespace: ns-myapp
spec:
  refreshInterval: 1h
  secretStoreRef:
    kind: SecretStore
    name: vault-backend
  target:
    name: myapp-secrets                       # Имя K8s Secret
  data:
    - secretKey: DB_PASSWORD                  # Ключ в K8s Secret
      remoteRef:
        key: secret/data/myapp/database       # Путь в Vault
        property: password                    # Поле в Vault secret
```

## Добавление нового проекта
### Последовательность действий для добавления нового проекта `myapp`

### Таблица ресурсов

- Namespace: Kubernetes
  - `ns-myapp`
  - Namespace приложения
- ServiceAccount: Kubernetes (ns-myapp)
  - `myapp-vault-auth`
  - SA для аутентификации в Vault
- Secrets: Vault (в UI или через cli, положить секреты в vault)
  - `secret/myapp/*`
  - Секреты приложения
- Policy: Vault
  - `myapp-policy`
  - Права на чтение secret/myapp/*
- Role: Vault
  - `myapp-role`
  - Связь SA → Policy
- SecretStore: Kubernetes (ns-myapp)
  - `vault-backend`
  - Подключение ESO к Vault
- ExternalSecret: Kubernetes (ns-myapp)
  - `myapp-secrets`
  - Синхронизация секретов
- Secret: Kubernetes (ns-myapp)
  - `myapp-secrets`
  - Автоматически создаётся ESO |

### Шаг 1: Создание namespace и ServiceAccount

```bash
kubectl create namespace ns-myapp
kubectl create serviceaccount myapp-vault-auth -n ns-myapp
```

### Шаг 2: Создание секретов в Vault

```bash
# Login в Vault (использовать admin или root token)
vault login

# Создание секретов
vault kv put secret/myapp/database \
  host=postgres.ns-myapp.svc \
  port=5432 \
  username=myapp \
  password=SuperSecretPassword123

vault kv put secret/myapp/api \
  key=api-key-12345 \
  secret=api-secret-67890
```

### Шаг 3: Создание Policy в Vault

```bash
vault policy write myapp-policy - <<EOF
# Read-only access to myapp secrets
path "secret/data/myapp/*" {
  capabilities = ["read", "list"]
}
path "secret/metadata/myapp/*" {
  capabilities = ["read", "list"]
}
EOF
```

### Шаг 4: Создание Role в Vault

```bash
vault write auth/kubernetes/role/myapp-role \
  bound_service_account_names=myapp-vault-auth \
  bound_service_account_namespaces=ns-myapp \
  policies=myapp-policy \
  ttl=1h
```

### Шаг 5: Создание SecretStore (ESO)

```yaml
apiVersion: external-secrets.io/v1
kind: SecretStore
metadata:
  name: vault-backend
  namespace: ns-myapp
spec:
  provider:
    vault:
      server: http://vault.vault.svc.cluster.local:8200
      path: secret
      version: v2
      auth:
        kubernetes:
          mountPath: kubernetes
          role: myapp-role
          serviceAccountRef:
            name: myapp-vault-auth
```

### Шаг 6: Создание ExternalSecret (ESO)

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: myapp-secrets
  namespace: ns-myapp
spec:
  refreshInterval: 1h
  secretStoreRef:
    kind: SecretStore
    name: vault-backend
  target:
    name: myapp-secrets
  data:
    - secretKey: DB_HOST
      remoteRef:
        key: secret/data/myapp/database
        property: host
    - secretKey: DB_PASSWORD
      remoteRef:
        key: secret/data/myapp/database
        property: password
    - secretKey: API_KEY
      remoteRef:
        key: secret/data/myapp/api
        property: key
```

### Шаг 7: Использование в Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: myapp
  namespace: ns-myapp
spec:
  template:
    spec:
      containers:
        - name: myapp
          envFrom:
            - secretRef:
                name: myapp-secrets    # Secret созданный ESO
```

## Схема компонентов

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              VAULT ARCHITECTURE                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                         VAULT (vault)                               │   │
│  │                                                                     │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────────┐ │   │
│  │  │   Secrets   │  │  Policies   │  │    Kubernetes Auth          │ │   │
│  │  │   Engine    │  │             │  │                             │ │   │
│  │  │             │  │ admin-      │  │  ┌─────────────────────┐   │ │   │
│  │  │ secret/     │  │ policy      │  │  │ Role: admin-role    │   │ │   │
│  │  │ ├─vault/    │  │             │  │  │ SA: vault-admin     │   │ │   │
│  │  │ ├─myapp/    │  │ self-eso-   │  │  │ NS: vault           │   │ │   │
│  │  │ └─other/    │  │ policy      │  │  └─────────────────────┘   │ │   │
│  │  │             │  │             │  │  ┌─────────────────────┐   │ │   │
│  │  │             │  │ myapp-      │  │  │ Role: self-eso-role │   │ │   │
│  │  │             │  │ policy      │  │  │ SA: vault-self-eso  │   │ │   │
│  │  │             │  │             │  │  │ NS: vault           │   │ │   │
│  │  │             │  │             │  │  └─────────────────────┘   │ │   │
│  │  │             │  │             │  │  ┌─────────────────────┐   │ │   │
│  │  │             │  │             │  │  │ Role: myapp-role    │   │ │   │
│  │  │             │  │             │  │  │ SA: myapp-vault-auth│   │ │   │
│  │  │             │  │             │  │  │ NS: ns-myapp        │   │ │   │
│  │  └─────────────┘  └─────────────┘  │  └─────────────────────┘   │ │   │
│  │                                     └─────────────────────────────┘ │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                      │                                      │
│                                      │ K8s Auth                             │
│                                      ▼                                      │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                 EXTERNAL SECRETS OPERATOR (ns-external-secrets)     │    │
│  │                                                                     │    │
│  │  Watches: SecretStore, ExternalSecret                               │    │
│  │  Creates: Kubernetes Secrets                                        │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                      │                                      │
│                                      │ Sync                                 │
│                                      ▼                                      │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                    APPLICATION NAMESPACE (ns-myapp)                 │    │
│  │                                                                     │    │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────┐  │    │
│  │  │ ServiceAccount  │  │  SecretStore    │  │  ExternalSecret     │  │    │
│  │  │                 │  │                 │  │                     │  │    │
│  │  │ myapp-vault-    │  │ vault-backend   │  │ myapp-secrets       │  │    │
│  │  │ auth            │◄─│ (refs SA)       │◄─│ (refs SecretStore)  │  │    │
│  │  └─────────────────┘  └─────────────────┘  └─────────────────────┘  │    │
│  │                                                      │              │    │
│  │                                                      ▼              │    │
│  │                                            ┌─────────────────────┐  │    │
│  │                                            │  Secret (auto)      │  │    │
│  │                                            │                     │  │    │
│  │  ┌─────────────────┐                       │ myapp-secrets       │  │    │
│  │  │   Deployment    │◄──────────────────────│ DB_PASSWORD: ***    │  │    │
│  │  │     myapp       │      envFrom          │ API_KEY: ***        │  │    │
│  │  └─────────────────┘                       └─────────────────────┘  │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Команды для управления

### Vault CLI

```bash
# Статус Vault
kubectl exec -n vault vault-0 -- vault status

# Login
kubectl exec -n vault vault-0 -- vault login <token>

# List secrets
kubectl exec -n vault vault-0 -- vault kv list secret/

# Read secret
kubectl exec -n vault vault-0 -- vault kv get secret/myapp/database

# Write secret
kubectl exec -n vault vault-0 -- vault kv put secret/myapp/database password=newpass

# List policies
kubectl exec -n vault vault-0 -- vault policy list

# Read policy
kubectl exec -n vault vault-0 -- vault policy read myapp-policy

# List roles
kubectl exec -n vault vault-0 -- vault list auth/kubernetes/role

# Read role
kubectl exec -n vault vault-0 -- vault read auth/kubernetes/role/myapp-role
```

### Перезапуск Vault

```bash
ansible-playbook -i hosts.yaml playbook-app/vault-restart.yaml
```

**Важно:** После перезапуска Vault будет в состоянии **sealed**. Необходимо выполнить unseal:

```bash
kubectl exec -n vault vault-0 -- vault operator unseal <unseal_key_1>
kubectl exec -n vault vault-0 -- vault operator unseal <unseal_key_2>
```

Unseal keys хранятся в K8s Secret `vault-root-credentials` в namespace `vault`.

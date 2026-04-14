# Rule: Update Teleport Configuration
# Trigger keywords: обнови teleport, добавь роль teleport, teleport configure, update teleport config
# Manual trigger: /rule update-teleport

## Когда применяется
Пользователь просит обновить конфигурацию Teleport (добавить роли, пользователей, apps, databases, и т.д.).

## Что можно конфигурировать

**Все ресурсы через CRD (Teleport Operator):**
- Roles (TeleportRoleV8)
- Users (TeleportUser)
- Bots (TeleportBotV1)
- ProvisionTokens (TeleportProvisionToken)
- Apps (TeleportAppV3)
- Databases (TeleportDatabaseV3)
- GitHub/OIDC/SAML Connectors
- AccessLists, TrustedClusters, LoginRules
- WorkloadIdentities, InferenceModels/Policies/Secrets
- AutoUpdate Configs/Versions

## Чеклист шагов

### Шаг 1: Определить, что добавить

**Примеры:**
- Добавить новую роль (TeleportRoleV8)
- Добавить нового пользователя (TeleportUser)
- Добавить приложение (TeleportAppV3)
- Добавить базу данных (TeleportDatabaseV3)
- Добавить GitHub connector (TeleportGithubConnector)

### Шаг 2: Добавить в hosts-vars-override/

```yaml
# hosts-vars-override/teleport-configure.yaml

# Для Roles
teleport_configure_roles_extra:
  - name: <role-name>
    labels: {}
    annotations: {}
    spec:
      allow:
        logins: [root, ubuntu]
        node_labels: {'*': ['*']}
        kubernetes_groups: [system:masters]
        kubernetes_resources:
          - kind: '*'
            name: '*'
              namespace: '*'
              verbs: ['*']
      options:
        max_session_ttl: 30h0m0s

# Для Users
teleport_configure_users_extra:
  - name: <user-name>
    labels: {}
    annotations: {}
    spec:
      roles: [<role-name>]
      traits:
        logins: [root, ubuntu]
        kubernetes_groups: [system:masters]

# Для Apps
teleport_configure_apps_extra:
  - name: <app-name>
    labels: {}
    annotations: {}
    spec:
      uri: "https://app.internal:443"
      public_addr: "app.example.com"
      labels:
        env: "prod"

# Для Databases
teleport_configure_databases_extra:
  - name: <db-name>
    labels: {}
    annotations: {}
    spec:
      protocol: "postgres"
      uri: "db.internal:5432"
      database: "mydb"

# Для GitHub Connectors
teleport_configure_github_connectors_extra:
  - name: github
    spec:
      client_id: "<client-id>"
      client_secret: "<client-secret>"
      teams_to_roles:
        - organization: "my-org"
          team: "engineering"
          roles: ["access"]
```

### Шаг 3: Вызвать configure

```bash
# Только configure (CRDs sync)
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/teleport-install.yaml --tags configure
```

**Что произойдёт:**
1. Helm chart teleport-configure применит все CRD ресурсы
2. Teleport Operator синхронизирует CRD → Teleport API
3. Все роли, пользователи, apps, databases — обновятся в Teleport

### Шаг 4: Проверить синхронизацию

```bash
# Проверить роли
kubectl exec -n teleport deploy/teleport-auth -- tctl get role/<role-name>

# Проверить пользователей
kubectl exec -n teleport deploy/teleport-auth -- tctl users ls

# Проверить apps
kubectl exec -n teleport deploy/teleport-auth -- tctl apps ls

# Проверить databases
kubectl exec -n teleport deploy/teleport-auth -- tctl dbs ls

# Проверить GitHub connector
kubectl exec -n teleport deploy/teleport-auth -- tctl get github/github
```

### Шаг 5: Проверить логи Operator

```bash
# Проверить логи оператора
kubectl logs -n teleport deployment/teleport-operator

# Проверить статус CRD
kubectl get <resource-type> -n teleport
kubectl describe <resource-type> <name> -n teleport
```

## Важные нюансы

### ⚠️ Operator работает только в одну сторону
- **CRD → Teleport** (односторонняя синхронизация)
- Если добавить что-то через Teleport UI → оно НЕ появится в CRD
- Если через UI обновить что-то, что есть в CRD → через 1 минуту вернётся к состоянию CRD
- **Через UI только смотрим, ничего не создаём!**

### ⚠️ Operator certificate expiry
- Сертификат оператора на 1 час
- Если не обновит → отвалится
- **Симптомы в логах:**
  ```
  current time 2026-04-12T09:32:38Z is after 2026-04-12T08:36:50Z
  write tcp 10.64.15.71:49648->10.132.251.113:3025: write: broken pipe
  tls: expired certificate
  ```
- **Лечение:**
  ```bash
  kubectl rollout restart deployment/teleport-operator -n teleport
  ```

### ⚠️ MFA=OTP для консоли
- Для авторизации через консоль (kubectl) — работает только MFA=OTP
- PassKey НЕ работает

### ⚠️ teleport_cluster_name — IMMUTABLE
- НЕЛЬЗЯ менять после первого деплоя!
- Если изменить — всё сломается

## Типичные сценарии

### Сценарий 1: Добавить роль для разработчиков
```yaml
# hosts-vars-override/teleport-configure.yaml
teleport_configure_roles_extra:
  - name: developer
    spec:
      allow:
        logins: [developer]
        node_labels: {'*': ['*']}
        kubernetes_groups: [developers]
        kubernetes_resources:
          - kind: pods
            name: '*'
            namespace: '*'
            verbs: ['get', 'list', 'watch']
      options:
        max_session_ttl: 8h0m0s
```
```bash
ansible-playbook ... teleport-install.yaml --tags configure
```

### Сценарий 2: Добавить пользователя
```yaml
teleport_configure_users_extra:
  - name: john.doe
    spec:
      roles: [developer]
      traits:
        logins: [developer]
        kubernetes_groups: [developers]
```
```bash
ansible-playbook ... teleport-install.yaml --tags configure
```

### Сценарий 3: Добавить приложение
```yaml
teleport_configure_apps_extra:
  - name: grafana
    spec:
      uri: "http://grafana.grafana.svc:3000"
      public_addr: "grafana.example.com"
      labels:
        env: "prod"
```
```bash
ansible-playbook ... teleport-install.yaml --tags configure
```

## Валидация

### Проверить, что CRD применились
```bash
# Проверить все CRD ресурсы
kubectl get teleportrolev8 -n teleport
kubectl get teleportuser -n teleport
kubectl get teleportappv3 -n teleport
kubectl get teleportdatabasev3 -n teleport
```

### Проверить, что Operator синхронизировал
```bash
# Проверить статус CRD
kubectl describe teleportrolev8 <name> -n teleport
# Искать: Status.Conditions → Ready=True
```

### Проверить в Teleport
```bash
# Зайти в Teleport UI → проверить, что ресурсы появились
# ИЛИ через CLI
kubectl exec -n teleport deploy/teleport-auth -- tctl get role/<name>
```

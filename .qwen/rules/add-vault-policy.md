# Rule: Add ESO/Vault Policy
# Trigger keywords: добавь политику vault, добавить vault policy, добавь ESO, add vault policy, add ESO integration
# Manual trigger: /rule add-vault-policy

## Когда применяется
Пользователь просит добавить новую интеграцию с Vault + ESO для компонента (например: "добавь Vault политику для grafana", "добавь ESO для нового компонента").

## Чеклист шагов

### Шаг 1: Определить, что нужно

**Вариант A: Только добавить новую политику (без новой интеграции)**
- Добавить политику в `vault_policies_extra`
- Вызвать: `ansible-playbook ... vault-install.yaml --tags vault-cr`

**Вариант B: Добавить полную ESO интеграцию**
- Добавить политику + роль + SecretStore + ExternalSecret

### Шаг 2: Добавить политику в Vault

```yaml
# hosts-vars/vault.yaml (или hosts-vars-override/vault.yaml)
vault_policies_extra:
  - name: <component>.eso-main
    rules: |
      path "eso-secret/data/<component>/*" { capabilities = ["read", "list"] }
      path "eso-secret/metadata/<component>/*" { capabilities = ["read", "list"] }
```

**Валидация:**
- Название политики — уникальное в рамках всего Vault
- Path соответствует пути в Vault (eso-secret/data/<component>/...)
- Capabilities соответствуют задачам (read, list для ESO)

### Шаг 3: Добавить роль в Vault

```yaml
# hosts-vars/vault.yaml
vault_roles_extra:
  - name: <component>.eso-main
    bound_service_account_namespaces: <component-namespace>
    bound_service_account_names: eso-main
    policies:
      - <component>.eso-main
    ttl: "{{ vault_roles_default_ttl }}"
```

**Валидация:**
- Название роли — уникальное в рамках всего Vault
- `bound_service_account_namespaces` = namespace компонента
- `bound_service_account_names` = SA, который будет создан (обычно eso-main)
- `policies` = список политик (должны существовать в vault_policies_final!)

### Шаг 4: Добавить ESO интеграцию

```yaml
# hosts-vars/vault-eso.yaml
eso_vault_integration_<component>:
  sa_name: "eso-main"                     # ServiceAccount в namespace
  role_name: "<component>.eso-main"       # Vault role name
  secret_store_name: "eso-main.vault"     # SecretStore CRD name
  kv_engine_path: "eso-secret"            # KV engine path

eso_vault_integration_<component>_secrets: []
eso_vault_integration_<component>_secrets_extra: []
```

**Или добавить секреты сразу:**
```yaml
eso_vault_integration_<component>_secrets_extra:
  - external_secret_name: "eso-<component>-xxx"
    target_secret_name: "k8s-<component>-xxx"
    vault_path: "/<component>/xxx"
    type: "default"
    is_need_eso: true  # false = только Vault, без k8s Secret
```

### Шаг 5: Синхронизировать Vault

```bash
# Полная синхронизация (add + update + delete для policies + roles)
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/vault-policy-sync.yaml

# Или только add (безопаснее)
ansible-playbook ... vault-policy-sync.yaml --tags policy-add,role-add
```

**Что произойдёт:**
1. ESO merge проверит:
   - Уникальность policy names и role names
   - Что все policies из ролей существуют
   - Что SecretStore → Role → Policies существуют
   - Уникальность external_secret_name и target_secret_name
2. Добавит новые политики в Vault
3. Добавит новые роли в Vault

### Шаг 6: Положить секреты в Vault

**Вручную через Vault UI или CLI:**
```bash
# Пример: положить секрет для компонента
vault kv put eso-secret/<component>/xxx key1=value1 key2=value2
```

### Шаг 7: Создать/обновить ESO ресурсы

**Если компонент ещё не установлен:**
```bash
# Установить компонент (pre создаст SecretStore + SA + ExternalSecret)
ansible-playbook ... playbook-app/<component>-install.yaml --tags pre
```

**Если компонент уже установлен:**
```bash
# Только pre (добавит/обновит ESO ресурсы)
ansible-playbook ... playbook-app/<component>-install.yaml --tags pre
```

### Шаг 8: Проверить синхронизацию

```bash
# Проверить SecretStore
kubectl get secretstore -n <component-namespace>

# Проверить ExternalSecret
kubectl get externalsecret -n <component-namespace>

# Проверить, что Secret создался
kubectl get secret <target_secret_name> -n <component-namespace>

# Проверить статус ExternalSecret
kubectl describe externalsecret <name> -n <component-namespace>

# Force sync (если нужно)
ansible-playbook ... playbook-app/eso-force-sync.yaml --tags <component>
```

## Валидация

### Проверить, что всё работает
1. **Policy существует**: зайти в Vault UI → Policies → проверить `<component>.eso-main`
2. **Role существует**: Vault UI → Access → Auth Methods → kubernetes → Roles → проверить `<component>.eso-main`
3. **SecretStore создан**: `kubectl get secretstore -n <namespace>`
4. **ExternalSecret создан**: `kubectl get externalsecret -n <namespace>`
5. **Secret создался**: `kubectl get secret <target> -n <namespace>`
6. **Secret содержит данные**: `kubectl get secret <target> -n <namespace> -o jsonpath='{.data}'`

### Типичные ошибки

| Ошибка | Причина | Решение |
|--------|---------|---------|
| Duplicate policy name | Policy с таким именем уже есть | Проверить vault_policies_final |
| Role references missing policy | Роль ссылается на несуществующую политику | Добавить политику в vault_policies_extra |
| SecretStore role_name not found | Role не найдена в vault_roles_final | Добавить роль в vault_roles_extra |
| ExternalSecret не синхронизируется | Нет доступа к Vault path | Проверить политику и роль |
| Collision в namespace | Два ExternalSecret с одинаковым именем в одном NS | Проверить уникальность external_secret_name |

## Важные правила

1. **Порядок**: policy → role → integration → sync → secrets → ESO resources
2. **ESO merge валидирует** всё перед запуском (fail-only, ничего не создаёт в Vault)
3. **is_need_eso: false** = только сохранить в Vault, k8s Secret НЕ создавать (пример: root creds)
4. **Один репозиторий = один ESO secret** (для ArgoCD Git-Ops)
5. **ArgoCD Git-Ops** использует отдельный SA `eso-git-ops` (не `eso-main`!)
6. **Записи в Vault НЕ удаляются автоматически** при удалении k8s ресурсов

# Rule: Add ArgoCD Git-Ops Project
# Trigger keywords: добавь проект argocd, add argocd git-ops project, новый проект в argocd
# Manual trigger: /rule add-argocd-project

## Когда применяется
Пользователь просит добавить новый проект/приложение в ArgoCD через Git-Ops паттерн.

## Чеклист шагов

### Шаг 1: Определить, что добавить

**Проект (AppProject):**
- Name, description
- Source repos (какие репозитории разрешены)
- Destinations (какие namespaces/clusters разрешены)
- Cluster resources access

**Приложение (Application):**
- Name, project
- Repo URL + path + revision
- Destination namespace + cluster
- Sync policy (auto-sync, self-heal, prune)
- Helm values (если используется)

### Шаг 2: Подготовить SSH keys (если ещё не готовы)

```bash
# Создать SSH keys (если ещё не созданы)
ssh-keygen -t ed25519 -C "argocd-gitops" -f /tmp/argocd-gitops

# Положить private key в Vault
# Path: /argocd/git-ops/keys/<repo-name>
# Format: {"private_key": "..."}
```

**Важно:**
- 1 репозиторий = 1 ESO secret + 1 k8s secret + 1 vault secret
- НЕ создавать несколько secrets для одного repo_url!

### Шаг 3: Добавить ESO интеграцию

```yaml
# hosts-vars-override/argocd-git-ops.yaml
eso_vault_integration_argocd_git_ops_extra:
  # Pattern-based (для всех репозиториев с паттерном)
  - external_secret_name: "eso-argocd-git-ops-my-repo"
    target_secret_name: "eso-argocd-git-ops-my-repo"
    vault_path: "/argocd/git-ops/keys/my-repo"
    type: "git_ops_repo_direct"  # или "git_ops_repo_pattern"
    repo_url: "git@github.com:org/my-repo.git"
```

### Шаг 4: Определить AppProject + Application

```yaml
# hosts-vars-override/argocd-git-ops.yaml
argocd_git_ops_projects:
  - name: "my-project"
    description: "My project description"
    source_repos:
      - "git@github.com:org/my-repo.git"
    destinations:
      - namespace: "my-namespace"
        server: "https://kubernetes.default.svc"
    cluster_resource_whitelist:
      - group: "*"
        kind: "*"

argocd_git_ops_apps:
  - name: "my-app"
    project: "my-project"
    source:
      repo_url: "git@github.com:org/my-repo.git"
      path: "k8s/overlays/prod"
      target_revision: "main"
    destination:
      namespace: "my-namespace"
      server: "https://kubernetes.default.svc"
    sync_policy:
      automated:
        prune: true
        self_heal: true
      sync_options:
        - "CreateNamespace=true"
```

### Шаг 5: Вызвать install

```bash
# Установить/обновить ArgoCD Git-Ops
ansible-playbook -i hosts-vars/ -i hosts-vars-override/ playbook-app/argocd-git-ops-install.yaml

# Только pre (ESO ресурсы)
ansible-playbook ... --tags pre

# Только install (AppProject + Application)
ansible-playbook ... --tags install

# Только post (Ingress)
ansible-playbook ... --tags post
```

### Шаг 6: Проверить в ArgoCD

```bash
# Проверить AppProject
kubectl get appproject -n argocd

# Проверить Application
kubectl get application -n argocd

# Проверить, что Application синхронизировалось
# ArgoCD UI → Applications → my-app → Sync Status

# Проверить ресурсы в namespace
kubectl get all -n my-namespace
```

### Шаг 7: Проверить ESO синхронизацию

```bash
# Проверить ExternalSecret
kubectl get externalsecret -n argocd

# Проверить Secret
kubectl get secret eso-argocd-git-ops-my-repo -n argocd

# Проверить, что ArgoCD может подключиться к репозиторию
# ArgoCD UI → Settings → Repositories → проверить статус
```

## Важные нюансы

### Типы ESO секретов

| Тип | Описание |
|-----|----------|
| `git_ops_repo_pattern` | Pattern-based repo (auto-discovery) |
| `git_ops_repo_direct` | Direct repo connection |
| `git_ops_repo_direct_userpass` | Direct with username/password |
| `git_ops_repo_pattern_userpass` | Pattern with username/password |
| `helm_repo` | Helm repository |
| `helm_repo_oci` | Helm OCI repository |

### Последовательность установки (первый раз)

1. Настроить конфиги `argocd_git_ops_apps` + `eso_vault_integration_argocd_git_ops_extra`
2. Установить `argocd-git-ops-install.yaml`
3. Создать SSH keys → положить в Vault
4. Создать репозитории → добавить deploy-keys (public key)
5. Проверить, что ArgoCD подключился

### 1 репозиторий = 1 secret

- НЕ создавать несколько k8s.secret с одинаковым repoUrl
- Иначе ArgoCD возьмёт первый вернувшийся (непредсказуемо)
- 1 (repo_url + ESO.secret + vault.secret + k8s.secret)

## Типичные сценарии

### Сценарий 1: Добавить новый проект
```yaml
argocd_git_ops_projects_extra:
  - name: "new-project"
    description: "New project"
    source_repos:
      - "git@github.com:org/new-repo.git"
    destinations:
      - namespace: "new-namespace"
        server: "https://kubernetes.default.svc"
```

### Сценарий 2: Добавить новое приложение
```yaml
argocd_git_ops_apps_extra:
  - name: "new-app"
    project: "new-project"
    source:
      repo_url: "git@github.com:org/new-repo.git"
      path: "k8s"
      target_revision: "main"
    destination:
      namespace: "new-namespace"
      server: "https://kubernetes.default.svc"
    sync_policy:
      automated:
        prune: true
        self_heal: true
```

## Валидация

### Проверить, что всё работает
1. **ArgoCD UI → AppProjects** → проверить, что проект появился
2. **ArgoCD UI → Applications** → проверить, что приложение появилось
3. **Sync Status** → проверить, что синхронизировалось
4. **Resources** → проверить, что ресурсы создались в namespace
5. **Health Status** → проверить, что всё healthy

### Проверить ESO
```bash
# Проверить ExternalSecret
kubectl describe externalsecret <name> -n argocd

# Проверить Secret
kubectl get secret <name> -n argocd -o yaml
```

### Проверить репозиторий
```bash
# ArgoCD UI → Settings → Repositories
# Проверить, что репозиторий подключился успешно
# Проверить, что Connection Successful
```

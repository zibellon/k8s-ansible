# Rules Index — k8s-ansible

## Как работают правила

При выполнении задачи я автоматически определяю тип задачи и применяю соответствующее правило.
Ты можешь вызвать правило вручную через команду `/rule <rule-name>`.

## Доступные правила

| Правило | Trigger | Описание |
|---------|---------|----------|
| `add-component` | "добавь компонент", "установи компонент" | Добавление нового компонента с Helm chart |
| `update-component` | "обнови компонент", "обнови версию" | Обновление версии существующего компонента |
| `add-vault-policy` | "добавь политику vault", "добавь ESO" | Добавление интеграции с Vault + ESO |
| `update-teleport` | "обнови teleport", "добавь роль teleport" | Обновление конфигурации Teleport (roles, users, apps) |
| `update-prometheus` | "обнови alertmanager", "обнови prometheus конфиг" | Обновление конфигов Alertmanager + Prometheus |
| `add-argocd-project` | "добавь проект argocd", "add argocd git-ops" | Добавление проекта/приложения в ArgoCD Git-Ops |

## Как вызвать правило вручную

```
/rule add-component
/rule update-component
/rule add-vault-policy
/rule update-teleport
/rule update-prometheus
/rule add-argocd-project
```

## Общий workflow для всех правил

1. **Определить тип задачи** (автоматически или через `/rule`)
2. **Применить правило** (следую чеклисту из файла правила)
3. **Валидация** (проверить переменные, CRDs, pods, ESO sync)
4. **Выполнить** (запустить playbook с нужными тегами)
5. **Проверить результат** (kubectl get, helm list, UI check)

## Важные общие правила

1. **ВСЕ запуски** из корня проекта: `cd /path/to/k8s-ansible`
2. **Сначала sources/** → потом playbook-app/charts/
3. **Валидация переменных**: проверить, что переменные принимаются chart-ом
4. **Namespace**: НЕ менять для `longhorn-system` и `argocd`
5. **Директория docs/**: НЕ использовать, если пользователь явно не попросил
6. **hosts-vars-override/**: НИКОГДА не коммитить (секреты, IP, пароли)

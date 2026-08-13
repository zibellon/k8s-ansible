`kargo_oidc_global_service_account_namespaces`

Это список namespace'ов, в которых Kargo ищет ServiceAccount'ы с аннотацией claims, помимо namespace'ов Project'ов.

Механика (auth_middleware.go:288-305): после проверки ID-токена сервер собирает allowedNamespaces = этот список ∪ все namespace'ы с меткой Project. Затем SA, найденные по claim-индексу, фильтруются — те, что вне списка, отбрасываются. То есть аннотация на SA в произвольном namespace не сработает, если namespace не разрешён.

Ключевая деталь: релизный namespace чарт подставляет туда сам, безусловно (configmap.yaml:48-52) — в рендере видно GLOBAL_SERVICE_ACCOUNT_NAMESPACES: kargo. Поэтому при пустом списке наши SA в ns kargo уже работают, и переменная нужна только если захочешь держать «ролевые» SA в отдельном namespace (например kargo-rbac). Оставлять пустой — нормально.

## роль для того, чтобы делать promotion из UI

```
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: kargo-ui-promoter
rules:
  # --- Cluster-scoped: без этого UI не откроется вообще ---
  - apiGroups: [""]
    resources: ["namespaces"]
    verbs: ["get", "list", "watch"]
  - apiGroups: ["kargo.akuity.io"]
    resources: ["projects", "clusterconfigs", "clusterpromotiontasks"]
    verbs: ["get", "list", "watch"]

  # --- Namespaced Kargo-ресурсы: только чтение (создаёт ArgoCD) ---
  - apiGroups: ["kargo.akuity.io"]
    resources: ["stages", "warehouses", "freights", "projectconfigs", "promotiontasks"]
    verbs: ["get", "list", "watch"]

  # --- Кнопка PROMOTE: нужны ОБА правила ---
  - apiGroups: ["kargo.akuity.io"]
    resources: ["stages"]
    verbs: ["promote"]
  # patch — отмена зависшего продвижения (ставит аннотацию на Promotion)
  - apiGroups: ["kargo.akuity.io"]
    resources: ["promotions"]
    verbs: ["create", "get", "list", "watch", "patch"]

  # --- Ручное одобрение груза (кнопка Approve) ---
  - apiGroups: ["kargo.akuity.io"]
    resources: ["freights/status"]
    verbs: ["patch"]

  # --- Вспомогательное для UI: события, конфиги, вкладка Roles ---
  - apiGroups: [""]
    resources: ["events", "configmaps", "serviceaccounts"]
    verbs: ["get", "list", "watch"]
  - apiGroups: ["rbac.authorization.k8s.io"]
    resources: ["roles", "rolebindings"]
    verbs: ["get", "list", "watch"]

  # --- Argo Rollouts (верификация Stage). Пока в кластере не установлен —
  #     правило безвредно, но не работает. Оставлено на будущее.
  - apiGroups: ["argoproj.io"]
    resources: ["analysisruns", "analysistemplates"]
    verbs: ["get", "list", "watch"]
```
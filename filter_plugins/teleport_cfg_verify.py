"""
Teleport cfg compute layer — pure Python filter plugin.
Used by playbook-app/teleport-install.yaml (stage cfg).

Два фильтра:
  - teleport_cfg_verify — проверка ДО применения: согласованность ролей,
    пользователей и RBAC Kubernetes, которые шаблоны чарта teleport/cfg
    рендерят без проверок. Ловит молчаливые ошибки (группа без биндинга —
    доступа нет, правило без verbs — ничего не разрешает) и падающие
    (дубликат имени роняет helm, роль с именем preset не сматчится оператором).
  - teleport_cfg_reconcile_diff — проверка ПОСЛЕ применения: сравнение
    объявленного списка с тем, что реально лежит в Teleport (tctl get ...).
    Оператор Teleport применяет CR асинхронно и не пишет observedGeneration,
    поэтому истина — только сам Teleport.

Lives in repo-root filter_plugins/ directory. Discovered by Ansible via
ansible.cfg [defaults] filter_plugins = filter_plugins setting (ansible.cfg in
repo root; ansible-playbook always invoked with cwd=repo root per project
convention).
"""

# Префикс-маркер владельца для RBAC-объектов, рендеримых в cluster scope и в
# чужие namespace (playbook-conventions.md §17.12).
RBAC_NAME_PREFIX = 'teleport-managed-'
# Группа-суперпользователь kube-apiserver: обходит RBAC и не отзывается
# удалением биндингов.
FORBIDDEN_KUBE_GROUP = 'system:masters'
# Метка, которой оператор помечает свои объекты; без неё объект для оператора
# «чужой» и он отказывается его менять.
ORIGIN_LABEL = 'teleport.dev/origin'
ORIGIN_KUBERNETES = 'kubernetes'
# Встроенные объекты Teleport: preset/system-роли и bot-пользователи.
RESOURCE_TYPE_LABEL = 'teleport.internal/resource-type'
BUILTIN_RESOURCE_TYPES = ('preset', 'system')
BOT_LABEL = 'teleport.internal/bot'
# Preset-роли v18 (constants.go Preset*RoleName): создаются auth при старте,
# оператор их не сматчит — имя своей роли не должно совпадать.
PRESET_ROLE_NAMES = (
    'editor', 'access', 'auditor', 'reviewer', 'requester', 'group-access',
    'device-admin', 'device-enroll', 'require-trusted-device',
    'terraform-provider', 'wildcard-workload-identity-issuer', 'access-plugin',
    'access-plugin-with-review', 'list-access-request-resources', 'mcp-user',
    'beam-user', 'beam-admin',
)


# =============================================================================
# Private helpers (NOT registered as public filters)
# =============================================================================
def _find_duplicates(values):
    """Список значений, встречающихся >1 раза (порядок сохранён, без повторов в результате).

    Args:
        values: list of hashable values to check for duplicates.
    Returns:
        list of duplicate values (each appearing once, in order of first re-occurrence).
    """
    seen = set()
    dups = []
    for v in values:
        if v in seen and v not in dups:
            dups.append(v)
        seen.add(v)
    return dups


def _bound_groups(bindings):
    """Имена групп, на которые есть RoleBinding/ClusterRoleBinding.

    Args:
        bindings: list of binding dicts ({subjects: [{kind, name}]}).
    Returns:
        set[str] of Group subject names.
    """
    groups = set()
    for b in bindings:
        for s in (b.get('subjects') or []):
            if s.get('kind') == 'Group' and s.get('name'):
                groups.add(s['name'])
    return groups


def _labels(item):
    """metadata.labels объекта из `tctl get ... --format=json` (может отсутствовать)."""
    return (item.get('metadata') or {}).get('labels') or {}


# =============================================================================
# Public filters
# =============================================================================
def teleport_cfg_verify(roles, users, cluster_roles, cluster_role_bindings, role_bindings, teleport_namespace):
    """Нарушения согласованности стадии teleport/cfg.

    Args:
        roles: list of TeleportRoleV8 items ({name, spec}).
        users: list of TeleportUser items ({name, spec: {roles}}).
        cluster_roles: list of ClusterRole items ({name, rules}).
        cluster_role_bindings: list of ClusterRoleBinding items ({name, roleRef, subjects}).
        role_bindings: list of RoleBinding items ({name, namespace, roleRef, subjects}).
        teleport_namespace: namespace самого Teleport (RoleBinding туда запрещён).
    Returns:
        list[str]: violation messages ([] = OK).
    """
    roles = roles or []
    users = users or []
    cluster_roles = cluster_roles or []
    cluster_role_bindings = cluster_role_bindings or []
    role_bindings = role_bindings or []
    violations = []

    # Обязательные поля: шаблоны рендерят пустое значение молча (RoleBinding без
    # namespace уезжает в namespace релиза — то есть в namespace Teleport).
    required = (
        ('teleport_cfg_roles', roles, ('name', 'spec')),
        ('teleport_cfg_users', users, ('name', 'spec')),
        ('teleport_cfg_rbac_cluster_roles', cluster_roles, ('name', 'rules')),
        ('teleport_cfg_rbac_cluster_role_bindings', cluster_role_bindings, ('name', 'roleRef', 'subjects')),
        ('teleport_cfg_rbac_role_bindings', role_bindings, ('name', 'namespace', 'roleRef', 'subjects')),
    )
    for label, items, fields in required:
        for idx, item in enumerate(items):
            for field in fields:
                if not item.get(field):
                    violations.append(
                        "{}[{}]: отсутствует обязательное поле {}.".format(
                            label, item.get('name') or idx, field)
                    )

    # exec в под auth = встроенная Admin-идентичность tctl = админ Teleport.
    for b in role_bindings:
        if b.get('namespace') and b.get('namespace') == teleport_namespace:
            violations.append(
                "teleport_cfg_rbac_role_bindings[{}]: RoleBinding в namespace самого Teleport"
                " ({}) запрещён — права там (exec в auth) дают админа Teleport.".format(
                    b.get('name'), teleport_namespace)
            )

    role_names = [r.get('name') for r in roles]
    for label, values in (
        ('teleport_cfg_roles', role_names),
        ('teleport_cfg_users', [u.get('name') for u in users]),
        ('teleport_cfg_rbac_cluster_roles', [c.get('name') for c in cluster_roles]),
        ('teleport_cfg_rbac_cluster_role_bindings', [b.get('name') for b in cluster_role_bindings]),
        ('teleport_cfg_rbac_role_bindings',
         ['{}/{}'.format(b.get('namespace'), b.get('name')) for b in role_bindings]),
    ):
        dups = _find_duplicates(values)
        if dups:
            violations.append(
                "{}: name повторяется — helm упадёт на дубликате объекта. Дубликаты: {}.".format(
                    label, dups)
            )

    bound = _bound_groups(cluster_role_bindings + role_bindings)
    for role in roles:
        name = role.get('name')
        where = 'teleport_cfg_roles[{}]'.format(name)
        if name in PRESET_ROLE_NAMES:
            violations.append(
                "{}: имя совпадает с preset-ролью Teleport — оператор не сможет ею"
                " управлять (объект без метки {}={}).".format(where, ORIGIN_LABEL, ORIGIN_KUBERNETES)
            )
        allow = ((role.get('spec') or {}).get('allow') or {})
        for group in (allow.get('kubernetes_groups') or []):
            if group == FORBIDDEN_KUBE_GROUP:
                violations.append(
                    "{}: kubernetes_groups содержит {} — суперпользователь kube-apiserver"
                    " в обход RBAC. Используйте группу с ClusterRoleBinding.".format(
                        where, FORBIDDEN_KUBE_GROUP)
                )
            elif '{{' not in group and group not in bound:
                violations.append(
                    "{}: на группу '{}' нет ни ClusterRoleBinding, ни RoleBinding —"
                    " Kubernetes не даст ей никаких прав.".format(where, group)
                )
        for idx, res in enumerate(allow.get('kubernetes_resources') or []):
            if not res.get('verbs'):
                violations.append(
                    "{}: kubernetes_resources[{}] без verbs — в роли v8 такое правило"
                    " ничего не разрешает.".format(where, idx)
                )

    declared_roles = set(role_names)
    for user in users:
        for r in ((user.get('spec') or {}).get('roles') or []):
            if r not in declared_roles:
                violations.append(
                    "teleport_cfg_users[{}]: роль '{}' не объявлена в teleport_cfg_roles —"
                    " пользователям выдаются только роли из IaC (preset-роли Teleport тоже"
                    " запрещены: их содержимое задаёт не репозиторий).".format(user.get('name'), r)
                )

    for label, items in (
        ('teleport_cfg_rbac_cluster_roles', cluster_roles),
        ('teleport_cfg_rbac_cluster_role_bindings', cluster_role_bindings),
        ('teleport_cfg_rbac_role_bindings', role_bindings),
    ):
        for item in items:
            name = item.get('name') or ''
            if not name.startswith(RBAC_NAME_PREFIX):
                violations.append(
                    "{}[{}]: имя без префикса '{}' — объект в cluster scope / чужом"
                    " namespace обязан нести маркер владельца (§17.12).".format(
                        label, name, RBAC_NAME_PREFIX)
                )

    return violations


def teleport_cfg_reconcile_diff(live_items, declared_items, ignore_names=None):
    """Расхождение между объявленными объектами и тем, что лежит в Teleport.

    Args:
        live_items: list of objects from `tctl get <kind> --format=json`.
        declared_items: list of declared items ({name}).
        ignore_names: list of names of non-IaC objects to skip in 'unmanaged'
            (bootstrap-объекты upstream-чарта).
    Returns:
        dict:
          missing   — объявлены, но в Teleport нет объекта под управлением оператора;
          stale     — под управлением оператора, но из списков удалены (ждём удаления);
          unowned   — объявлены, но в Teleport лежит объект без метки оператора
                      (создан вручную — оператор его не тронет);
          unmanaged — прочие объекты не из IaC (созданы в UI/tctl);
          pending   — missing + stale (пусто = оператор сошёлся).
    """
    live_items = live_items or []
    declared = set(d.get('name') for d in (declared_items or []))
    ignore = set(ignore_names or [])

    managed = set()
    foreign = set()
    for item in live_items:
        name = (item.get('metadata') or {}).get('name')
        labels = _labels(item)
        if labels.get(ORIGIN_LABEL) == ORIGIN_KUBERNETES:
            managed.add(name)
        elif (labels.get(RESOURCE_TYPE_LABEL) not in BUILTIN_RESOURCE_TYPES
              and BOT_LABEL not in labels and name not in ignore):
            foreign.add(name)

    missing = sorted(declared - managed)
    stale = sorted(managed - declared)
    return {
        'missing': missing,
        'stale': stale,
        'unowned': sorted(declared & foreign),
        'unmanaged': sorted(foreign - declared),
        'pending': missing + stale,
    }


class FilterModule(object):
    def filters(self):
        return {
            'teleport_cfg_verify': teleport_cfg_verify,
            'teleport_cfg_reconcile_diff': teleport_cfg_reconcile_diff,
        }

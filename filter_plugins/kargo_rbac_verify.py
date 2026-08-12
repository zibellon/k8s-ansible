"""
Kargo RBAC consistency compute layer — pure Python filter plugin.
Used by playbook-app/tasks/tasks-kargo-rbac-verify.yaml.

Проверяет согласованность двух списков стадии kargo/rbac — kargo_custom_users
и kargo_projects, — которые шаблоны чарта рендерят БЕЗ каких-либо проверок.
Все интересные ошибки здесь либо молчаливые (объект создаётся, прав нет), либо
роняют helm на дубликате имени; ни одну из них шаблон не поймает.

Lives in repo-root filter_plugins/ directory. Discovered by Ansible via
ansible.cfg [defaults] filter_plugins = filter_plugins setting (ansible.cfg in
repo root; ansible-playbook always invoked with cwd=repo root per project
convention).
"""
import json

# Аннотация, по которой Kargo матчит ServiceAccount с OIDC-логином.
CLAIMS_ANNOTATION = 'rbac.kargo.akuity.io/claims'
# Legacy-форма той же аннотации. Обе индексируются и объединяются, но запись
# через UI стирает все ключи с этим префиксом — смешивать формы нельзя.
CLAIMS_ANNOTATION_LEGACY_PREFIX = 'rbac.kargo.akuity.io/claim.'
VALID_ROLE_TYPES = ('Role', 'ClusterRole')


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


def _verify_claims_annotation(annotations, where):
    """Нарушения формата аннотации claims у одного ServiceAccount.

    Индекс аутентификации Kargo парсит значение в map[string][]string и на
    ошибке делает continue БЕЗ записи в лог (pkg/indexer/indexer.go:470-474):
    невалидный JSON или скалярное значение дают SA, который просто никогда не
    сматчится. Ошибка полностью молчаливая, поэтому ловим её здесь.

    Args:
        annotations: dict аннотаций ServiceAccount (может быть None).
        where: строка-контекст для сообщения (напр. "kargo_custom_users[petya]").
    Returns:
        list[str]: violation messages.
    """
    violations = []
    annotations = annotations or {}

    for key in annotations:
        if key.startswith(CLAIMS_ANNOTATION_LEGACY_PREFIX):
            violations.append(
                "{}: legacy-аннотация '{}'. Обе формы индексируются и"
                " объединяются, но запись через UI стирает все ключи с этим"
                " префиксом. Используйте только '{}'.".format(
                    where, key, CLAIMS_ANNOTATION)
            )

    raw = annotations.get(CLAIMS_ANNOTATION)
    if raw is None:
        return violations

    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        violations.append(
            "{}: аннотация '{}' не парсится как JSON. Индекс аутентификации"
            " молча пропустит этот ServiceAccount, и человек не сматчится ни с"
            " одним логином.".format(where, CLAIMS_ANNOTATION)
        )
        return violations

    if not isinstance(parsed, dict):
        violations.append(
            "{}: аннотация '{}' должна быть JSON-объектом вида"
            " {{\"<claim>\":[\"<значение>\"]}}, а не {}.".format(
                where, CLAIMS_ANNOTATION, type(parsed).__name__)
        )
        return violations

    for claim, values in parsed.items():
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            violations.append(
                "{}: claim '{}' должен быть СПИСКОМ строк. Скаляр и любой другой"
                " тип индекс аутентификации молча пропускает — ServiceAccount"
                " никогда не сматчится, ошибки в логе не будет.".format(where, claim)
            )

    return violations


# =============================================================================
# Public filter
# =============================================================================
def kargo_rbac_verify(custom_users, projects, kargo_namespace):
    """Возвращает list[str] нарушений согласованности kargo_custom_users + kargo_projects.

    Stateless filter: оба списка приходят из inventory как есть.
    НЕ кидает исключений — raise делает Ansible-wrapper через assert.

    Args:
        custom_users: list сотрудников, элемент {saName, namespaces[], annotations?, labels?}.
        projects: list проектов, элемент {name, accounts: [{saName, roleType, roleName}]}.
        kargo_namespace: релизный namespace Kargo (kargo_namespace).
    Returns:
        list[str]: violation messages. Empty list means no violations.
    """
    violations = []
    custom_users = custom_users or []
    projects = projects or []

    # -------------------------------------------------------------------------
    # A. kargo_custom_users — структура и аннотации
    # -------------------------------------------------------------------------
    # saName -> set(namespaces); строится параллельно проверкам, используется в B.
    user_namespaces = {}

    for index, user in enumerate(custom_users):
        sa_name = user.get('saName')
        where = "kargo_custom_users[{}]".format(sa_name or "#{}".format(index))

        if not sa_name:
            violations.append(
                "kargo_custom_users[#{}]: отсутствует обязательное поле saName.".format(index)
            )
            continue

        namespaces = user.get('namespaces')
        if not isinstance(namespaces, list) or not namespaces:
            violations.append(
                "{}: namespaces должен быть непустым списком.".format(where)
            )
            namespaces = []

        dup_ns = _find_duplicates(namespaces)
        if dup_ns:
            violations.append(
                "{}: namespace повторяется в списке — helm упадёт на дубликате"
                " ServiceAccount. Дубликаты: {}.".format(where, dup_ns)
            )

        if namespaces and kargo_namespace not in namespaces:
            violations.append(
                "{}: релизный namespace '{}' не перечислен в namespaces. Субъект"
                " discovery-биндинга указывает на несуществующий ServiceAccount,"
                " и список проектов у человека будет пуст — внутрь он не попадёт"
                " даже по прямой ссылке.".format(where, kargo_namespace)
            )

        violations.extend(_verify_claims_annotation(user.get('annotations'), where))

        user_namespaces.setdefault(sa_name, set()).update(namespaces)

    dup_users = _find_duplicates([u.get('saName') for u in custom_users if u.get('saName')])
    if dup_users:
        violations.append(
            "kargo_custom_users: saName повторяется — helm упадёт на дубликате"
            " ServiceAccount, а в discovery-биндинге появятся дублирующиеся"
            " субъекты. Дубликаты: {}.".format(dup_users)
        )

    # -------------------------------------------------------------------------
    # B. kargo_projects — структура и ссылки на сотрудников
    # -------------------------------------------------------------------------
    for index, project in enumerate(projects):
        project_name = project.get('name')
        where = "kargo_projects[{}]".format(project_name or "#{}".format(index))

        if not project_name:
            violations.append(
                "kargo_projects[#{}]: отсутствует обязательное поле name.".format(index)
            )
            continue

        accounts = project.get('accounts')
        if accounts is None:
            accounts = []
        if not isinstance(accounts, list):
            violations.append("{}: accounts должен быть списком.".format(where))
            continue

        binding_keys = []
        for account_index, account in enumerate(accounts):
            sa_name = account.get('saName')
            role_type = account.get('roleType')
            role_name = account.get('roleName')
            account_where = "{}.accounts[#{}]".format(where, account_index)

            if not sa_name:
                violations.append("{}: отсутствует обязательное поле saName.".format(account_where))
            elif sa_name not in user_namespaces:
                violations.append(
                    "{}: saName '{}' не объявлен в kargo_custom_users. RoleBinding"
                    " сошлётся на несуществующий ServiceAccount: объект создастся,"
                    " ошибки не будет, прав тоже.".format(account_where, sa_name)
                )
            elif project_name not in user_namespaces[sa_name]:
                violations.append(
                    "{}: у сотрудника '{}' в namespaces нет '{}'. ServiceAccount в"
                    " этом namespace не создаётся, RoleBinding сошлётся в пустоту:"
                    " объект создастся, ошибки не будет, прав тоже.".format(
                        account_where, sa_name, project_name)
                )

            if role_type not in VALID_ROLE_TYPES:
                violations.append(
                    "{}: roleType должен быть одним из {}, получено {!r}.".format(
                        account_where, list(VALID_ROLE_TYPES), role_type)
                )

            if not role_name:
                violations.append(
                    "{}: отсутствует обязательное поле roleName.".format(account_where)
                )

            if sa_name and role_name:
                binding_keys.append("{}-{}".format(sa_name, role_name))

        dup_bindings = _find_duplicates(binding_keys)
        if dup_bindings:
            violations.append(
                "{}: пара saName+roleName повторяется — имя RoleBinding'а"
                " совпадёт и helm упадёт на дубликате. Дубликаты: {}.".format(
                    where, dup_bindings)
            )

    dup_projects = _find_duplicates([p.get('name') for p in projects if p.get('name')])
    if dup_projects:
        violations.append(
            "kargo_projects: name повторяется. Дубликаты: {}.".format(dup_projects)
        )

    return violations


class FilterModule(object):
    def filters(self):
        return {'kargo_rbac_verify': kargo_rbac_verify}

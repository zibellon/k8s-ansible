"""
ESO per-component validation compute layer — pure Python filter plugin.
Used by playbook-app/tasks/tasks-eso-verify.yaml.
All validation (SecretStore→Vault connectivity scoped к role этого
компонента, ESO uniqueness external_secret_name + body.target.name,
Vault path coverage policies этой role) lives here; Ansible task'и
используют filter через
{{ secrets | eso_verify(integration_object, namespace, policies, roles) }}
syntax.
Lives in repo-root filter_plugins/ directory. Discovered by Ansible
via ansible.cfg [defaults] filter_plugins = filter_plugins setting
(ansible.cfg in repo root; ansible-playbook always invoked with
cwd=repo root per project convention).
"""
import re
# =============================================================================
# Private helpers (NOT registered as public filters)
# =============================================================================
def _find_duplicates(names):
    """Список значений, встречающихся >1 раза (порядок сохранён, без повторов в результате).

    Args:
        names: list of strings to check for duplicates.
    Returns:
        list of duplicate values (each appearing once, in order of first re-occurrence).
    """
    seen = set()
    dups = []
    for n in names:
        if n in seen and n not in dups:
            dups.append(n)
        seen.add(n)
    return dups


def _as_list(value):
    """Нормализация поля роли в список: scalar→[scalar], list→list, None→[].

    Args:
        value: scalar, list, or None (e.g. bound_service_account_names which
               inventory writes as a single string).
    Returns:
        list — [] for None, the value itself if already a list, else [value].
    """
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _extract_prefixes(scoped_policies):
    """Path-префиксы из HCL rules набора policies (trailing /* отброшен, unique).

    Args:
        scoped_policies: list of policy dicts; each may have a 'rules' HCL string.
    Returns:
        list[str]: unique path prefixes (order-preserving) parsed from
                   `path "X"` patterns with any trailing `/*` stripped.
    """
    prefixes = []
    for p in scoped_policies:
        rules = p.get('rules') or ''
        for path in re.findall(r'path "([^"]+)"', rules):
            prefix = re.sub(r'/\*$', '', path)
            if prefix not in prefixes:
                prefixes.append(prefix)
    return prefixes


def _collect_vault_paths(secrets):
    """Union Vault путей из body.dataFrom[].extract.key + body.data[].remoteRef.key.

    Args:
        secrets: list of ESO secret dicts (each with a 'body' mapping).
    Returns:
        list[str]: unique Vault keys (order-preserving) referenced by either
                   the dataFrom.extract or data[].remoteRef forms.
    """
    paths = []
    for s in secrets:
        body = s.get('body') or {}
        for df in body.get('dataFrom') or []:
            extract = df.get('extract')
            if isinstance(extract, dict) and extract.get('key'):
                paths.append(extract['key'])
        for d in body.get('data') or []:
            ref = d.get('remoteRef')
            if isinstance(ref, dict) and ref.get('key'):
                paths.append(ref['key'])
    out = []
    for k in paths:
        if k not in out:
            out.append(k)
    return out
# =============================================================================
# Public filter
# =============================================================================
def eso_verify(eso_secrets_list, integration_object, namespace, policies, roles):
    """Возвращает list[str] нарушений ESO-конфигурации одного компонента.

    Stateless filter: policies и roles приходят УЖЕ merged (base+extra) из YAML.
    Собирает violations групп B/C/D, возвращает список строк (пустой = OK).
    НЕ кидает исключений — raise делает Ansible-wrapper через assert.

    Group B (scoped к role этого компонента) пропускается целиком если роль
    не найдена (одна B1-violation). Group C (uniqueness) считается ВСЕГДА,
    независимо от наличия роли.

    Args:
        eso_secrets_list: list of ESO secret dicts (merged base + extra).
        integration_object: eso_vault_integration_<c> mapping (role_name, sa_name, ...).
        namespace: K8s namespace компонента (string).
        policies: list of policy dicts (merged vault_policies + vault_policies_extra).
        roles: list of role dicts (merged vault_roles + vault_roles_extra).
    Returns:
        list[str]: violation messages. Empty list means no violations.
    """
    violations = []
    role_name = integration_object.get('role_name')
    sa_name = integration_object.get('sa_name')

    # B1 (gate): роль существует
    matches = [r for r in roles if r.get('name') == role_name]
    role = matches[0] if matches else None
    if role is None:
        all_role_names = [r.get('name') for r in roles]
        violations.append(
            "role '{}' NOT found in merged vault_roles. Available roles: {}. "
            "Define this role in vault_roles or vault_roles_extra.".format(role_name, all_role_names))
    else:
        # B2: SA binding + ns binding + >=1 policy (одна violation если что-то из трёх не так)
        sa_field = _as_list(role.get('bound_service_account_names'))
        ns_field = _as_list(role.get('bound_service_account_namespaces'))
        role_policies = role.get('policies') or []
        if not (sa_name in sa_field and namespace in ns_field and len(role_policies) > 0):
            violations.append(
                "role '{}' must bind SA '{}' in namespace '{}' and have at least one policy. "
                "Current role: bound_service_account_names={}, bound_service_account_namespaces={}, "
                "policies={}.".format(role_name, sa_name, namespace,
                                      role.get('bound_service_account_names'),
                                      role.get('bound_service_account_namespaces'), role_policies))
        # B3: каждая policy роли существует (одна violation на каждую missing)
        policy_names = set(p.get('name') for p in policies)
        for pol in role_policies:
            if pol not in policy_names:
                violations.append(
                    "role '{}' references policy '{}' which does NOT exist in merged vault_policies. "
                    "Define this policy in vault_policies or vault_policies_extra.".format(role_name, pol))
        # D: path coverage (substring), scoped к policies роли
        scoped = [p for p in policies if p.get('name') in set(role_policies)]
        prefixes = _extract_prefixes(scoped)
        for path in _collect_vault_paths(eso_secrets_list):
            if not any(prefix in path for prefix in prefixes):
                violations.append(
                    "Vault path '{}' is NOT covered by any policy of role '{}'. "
                    "Available scoped policy prefixes (after stripping /*): {}. "
                    "Add or extend a policy in vault_policies or vault_policies_extra.".format(
                        path, role_name, prefixes))

    # C1: unique external_secret_name (НЕЗАВИСИМО от роли — считается всегда)
    es_names = []
    for i, s in enumerate(eso_secrets_list):
        name = s.get('external_secret_name')
        if not name:
            violations.append("secret #{}: missing external_secret_name.".format(i))
        else:
            es_names.append(name)
    es_dups = _find_duplicates(es_names)
    if es_dups:
        violations.append(
            "duplicate external_secret_name in dto_eso_secrets_list for namespace '{}'. "
            "Duplicates: {}".format(namespace, es_dups))

    # C2: unique body.target.name (независимо от роли)
    tgt_names = []
    for i, s in enumerate(eso_secrets_list):
        target = (s.get('body') or {}).get('target') or {}
        name = target.get('name')
        if not name:
            ident = s.get('external_secret_name') or '#{}'.format(i)
            violations.append("secret '{}': missing body.target.name.".format(ident))
        else:
            tgt_names.append(name)
    tgt_dups = _find_duplicates(tgt_names)
    if tgt_dups:
        violations.append(
            "duplicate body.target.name in dto_eso_secrets_list for namespace '{}'. "
            "Duplicates: {}".format(namespace, tgt_dups))

    return violations


class FilterModule(object):
    def filters(self):
        return {'eso_verify': eso_verify}

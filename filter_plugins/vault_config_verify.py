"""
Vault config validation compute layer — pure Python filter plugin.
Used by playbook-app/tasks/vault/tasks-vault-config-verify.yaml.
All validation (unique policy names, unique role names, role→policy
referential integrity) lives here; Ansible task'и используют filter
через {{ policies | vault_config_verify(roles) }} syntax.
Lives in repo-root filter_plugins/ directory. Discovered by Ansible
via ansible.cfg [defaults] filter_plugins = filter_plugins setting
(ansible.cfg in repo root; ansible-playbook always invoked with
cwd=repo root per project convention).
"""
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
# =============================================================================
# Public filter
# =============================================================================
def vault_config_verify(policies, roles):
    """Возвращает list[str] нарушений конфигурации Vault (policies + roles).

    Stateless filter: policies и roles приходят УЖЕ merged (base+extra) из YAML.
    Собирает violations групп 1/2/3, возвращает список строк (пустой = OK).
    НЕ кидает исключений — raise делает Ansible-wrapper через assert.

    Args:
        policies: list of policy dicts (merged vault_policies + vault_policies_extra).
                  Each dict expected to have a 'name' key.
        roles: list of role dicts (merged vault_auth_kubernetes_roles + vault_auth_kubernetes_roles_extra).
               Each dict expected to have 'name' and 'policies' keys.
    Returns:
        list[str]: violation messages. Empty list means no violations.
    """
    violations = []

    policy_names = [p.get('name') for p in policies]
    role_names = [r.get('name') for r in roles]

    # G1 — duplicate policy names
    dup_policies = _find_duplicates(policy_names)
    if dup_policies:
        violations.append(
            "duplicate policy names in merged vault_policies (base + extra)."
            " Duplicates: {}".format(dup_policies)
        )

    # G2 — duplicate role names
    dup_roles = _find_duplicates(role_names)
    if dup_roles:
        violations.append(
            "duplicate role names in merged vault_auth_kubernetes_roles (base + extra)."
            " Duplicates: {}".format(dup_roles)
        )

    # G3 — referential integrity: each role.policies → existing policy
    policy_name_set = set(policy_names)
    for role in roles:
        role_name = role.get('name')
        for pol in role.get('policies') or []:
            if pol not in policy_name_set:
                violations.append(
                    "role '{}' references policy '{}' which does NOT exist in merged"
                    " vault_policies. Define this policy in vault_policies or"
                    " vault_policies_extra.".format(role_name, pol)
                )

    return violations


class FilterModule(object):
    def filters(self):
        return {'vault_config_verify': vault_config_verify}

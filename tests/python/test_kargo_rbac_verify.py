"""Tests for filter_plugins/kargo_rbac_verify.py.

conftest.py already inserts repo-root filter_plugins/ into sys.path,
so kargo_rbac_verify is importable directly.
"""
import json

import kargo_rbac_verify as krv


KARGO_NS = 'kargo'


# ---------------------------------------------------------------------------
# Fixtures (local helpers)
# ---------------------------------------------------------------------------
def _claims(*logins):
    return {'rbac.kargo.akuity.io/claims': json.dumps({'preferred_username': list(logins)})}


def _user(sa_name, namespaces=None, annotations=None):
    return {
        'saName': sa_name,
        'namespaces': [KARGO_NS] if namespaces is None else namespaces,
        'annotations': _claims('{}@example.com'.format(sa_name)) if annotations is None else annotations,
    }


def _account(sa_name, role_name='kargo-viewer', role_type='Role'):
    return {'saName': sa_name, 'roleType': role_type, 'roleName': role_name}


def _project(name, accounts=None):
    return {'name': name, 'accounts': accounts if accounts is not None else []}


def _verify(users, projects):
    return krv.kargo_rbac_verify(users, projects, KARGO_NS)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
def test_happy_path_empty_inputs():
    assert _verify([], []) == []


def test_happy_path_none_inputs():
    assert krv.kargo_rbac_verify(None, None, KARGO_NS) == []


def test_happy_path_single_user_single_project():
    users = [_user('petya', [KARGO_NS, 'my-wallet'])]
    projects = [_project('my-wallet', [_account('petya')])]
    assert _verify(users, projects) == []


def test_happy_path_user_without_project_membership():
    """Сотрудник только со списком проектов, без ролей нигде — валидно."""
    assert _verify([_user('petya')], []) == []


def test_happy_path_two_roles_in_one_project():
    users = [_user('petya', [KARGO_NS, 'my-wallet'])]
    projects = [_project('my-wallet', [
        _account('petya', 'kargo-viewer'),
        _account('petya', 'kargo-promoter'),
    ])]
    assert _verify(users, projects) == []


def test_happy_path_cluster_role_type():
    users = [_user('petya', [KARGO_NS, 'my-casino'])]
    projects = [_project('my-casino', [
        _account('petya', 'kargo-project-secrets-reader', role_type='ClusterRole'),
    ])]
    assert _verify(users, projects) == []


def test_happy_path_project_without_accounts_key():
    users = [_user('petya')]
    assert _verify(users, [{'name': 'my-wallet'}]) == []


# ---------------------------------------------------------------------------
# A. kargo_custom_users — структура
# ---------------------------------------------------------------------------
def test_user_without_sa_name():
    violations = _verify([{'namespaces': [KARGO_NS]}], [])
    assert len(violations) == 1
    assert 'saName' in violations[0]


def test_user_without_namespaces():
    violations = _verify([{'saName': 'petya', 'annotations': _claims('p@e.com')}], [])
    assert len(violations) == 1
    assert 'namespaces' in violations[0]


def test_user_with_empty_namespaces():
    violations = _verify([_user('petya', [])], [])
    assert len(violations) == 1
    assert 'непустым списком' in violations[0]


def test_user_with_scalar_namespaces():
    violations = _verify([_user('petya', KARGO_NS)], [])
    assert len(violations) == 1
    assert 'непустым списком' in violations[0]


def test_user_missing_release_namespace():
    """Главная молчаливая ошибка: без релизного namespace список проектов пуст."""
    violations = _verify([_user('petya', ['my-wallet'])], [])
    assert len(violations) == 1
    assert KARGO_NS in violations[0]
    assert 'discovery' in violations[0]


def test_user_duplicate_namespace_in_own_list():
    violations = _verify([_user('petya', [KARGO_NS, 'my-wallet', 'my-wallet'])], [])
    assert len(violations) == 1
    assert 'my-wallet' in violations[0]


def test_duplicate_sa_names():
    users = [_user('petya'), _user('petya')]
    violations = _verify(users, [])
    assert len(violations) == 1
    assert 'petya' in violations[0]


def test_empty_namespaces_does_not_also_report_missing_release_ns():
    """Пустой список — одно нарушение, не два: про релизный namespace молчим."""
    violations = _verify([_user('petya', [])], [])
    assert len(violations) == 1


# ---------------------------------------------------------------------------
# A. kargo_custom_users — аннотация claims
# ---------------------------------------------------------------------------
def test_claims_absent_is_valid():
    """Аннотация необязательна: SA без неё просто ни с кем не матчится."""
    assert _verify([_user('petya', [KARGO_NS], annotations={})], []) == []


def test_claims_invalid_json():
    users = [_user('petya', [KARGO_NS], annotations={'rbac.kargo.akuity.io/claims': 'not-json'})]
    violations = _verify(users, [])
    assert len(violations) == 1
    assert 'JSON' in violations[0]


def test_claims_json_array_instead_of_object():
    users = [_user('petya', [KARGO_NS], annotations={'rbac.kargo.akuity.io/claims': '["petya"]'})]
    violations = _verify(users, [])
    assert len(violations) == 1
    assert 'JSON-объектом' in violations[0]


def test_claims_scalar_value_is_the_silent_trap():
    """Скаляр вместо списка: индекс молча пропускает SA, ошибки в логе нет."""
    users = [_user('petya', [KARGO_NS],
                   annotations={'rbac.kargo.akuity.io/claims': '{"preferred_username":"petya@e.com"}'})]
    violations = _verify(users, [])
    assert len(violations) == 1
    assert 'СПИСКОМ' in violations[0]


def test_claims_non_string_list_item():
    users = [_user('petya', [KARGO_NS],
                   annotations={'rbac.kargo.akuity.io/claims': '{"preferred_username":[42]}'})]
    violations = _verify(users, [])
    assert len(violations) == 1
    assert 'СПИСКОМ' in violations[0]


def test_claims_multiple_bad_claims_reported_separately():
    raw = '{"preferred_username":"a", "email":"b"}'
    users = [_user('petya', [KARGO_NS], annotations={'rbac.kargo.akuity.io/claims': raw})]
    assert len(_verify(users, [])) == 2


def test_claims_legacy_prefix_annotation():
    users = [_user('petya', [KARGO_NS],
                   annotations={'rbac.kargo.akuity.io/claim.preferred_username': 'petya@e.com'})]
    violations = _verify(users, [])
    assert len(violations) == 1
    assert 'legacy' in violations[0]


# ---------------------------------------------------------------------------
# B. kargo_projects — структура
# ---------------------------------------------------------------------------
def test_project_without_name():
    violations = _verify([], [{'accounts': []}])
    assert len(violations) == 1
    assert 'name' in violations[0]


def test_project_with_scalar_accounts():
    violations = _verify([_user('petya')], [{'name': 'my-wallet', 'accounts': 'petya'}])
    assert len(violations) == 1
    assert 'accounts' in violations[0]


def test_duplicate_project_names():
    users = [_user('petya', [KARGO_NS, 'my-wallet'])]
    projects = [_project('my-wallet', [_account('petya')]),
                _project('my-wallet', [])]
    violations = _verify(users, projects)
    assert len(violations) == 1
    assert 'my-wallet' in violations[0]


def test_duplicate_sa_role_pair_in_one_project():
    """Одинаковое имя RoleBinding'а — helm упадёт."""
    users = [_user('petya', [KARGO_NS, 'my-wallet'])]
    projects = [_project('my-wallet', [_account('petya'), _account('petya')])]
    violations = _verify(users, projects)
    assert len(violations) == 1
    assert 'RoleBinding' in violations[0]


def test_same_sa_role_pair_in_different_projects_is_valid():
    users = [_user('petya', [KARGO_NS, 'my-wallet', 'my-casino'])]
    projects = [_project('my-wallet', [_account('petya')]),
                _project('my-casino', [_account('petya')])]
    assert _verify(users, projects) == []


# ---------------------------------------------------------------------------
# B. kargo_projects — accounts
# ---------------------------------------------------------------------------
def test_account_sa_not_declared_in_custom_users():
    projects = [_project('my-wallet', [_account('unknown-person')])]
    violations = _verify([_user('petya', [KARGO_NS, 'my-wallet'])], projects)
    assert len(violations) == 1
    assert 'unknown-person' in violations[0]
    assert 'kargo_custom_users' in violations[0]


def test_account_sa_missing_project_namespace():
    """Второй ключевой кейс: сотрудник есть, но SA в этом namespace не создаётся."""
    users = [_user('petya', [KARGO_NS])]
    projects = [_project('my-wallet', [_account('petya')])]
    violations = _verify(users, projects)
    assert len(violations) == 1
    assert 'my-wallet' in violations[0]


def test_account_without_sa_name():
    projects = [_project('my-wallet', [{'roleType': 'Role', 'roleName': 'kargo-viewer'}])]
    violations = _verify([], projects)
    assert len(violations) == 1
    assert 'saName' in violations[0]


def test_account_without_role_name():
    users = [_user('petya', [KARGO_NS, 'my-wallet'])]
    projects = [_project('my-wallet', [{'saName': 'petya', 'roleType': 'Role'}])]
    violations = _verify(users, projects)
    assert len(violations) == 1
    assert 'roleName' in violations[0]


def test_account_invalid_role_type():
    users = [_user('petya', [KARGO_NS, 'my-wallet'])]
    projects = [_project('my-wallet', [_account('petya', role_type='role')])]
    violations = _verify(users, projects)
    assert len(violations) == 1
    assert 'roleType' in violations[0]


def test_account_missing_role_type():
    users = [_user('petya', [KARGO_NS, 'my-wallet'])]
    projects = [_project('my-wallet', [{'saName': 'petya', 'roleName': 'kargo-viewer'}])]
    violations = _verify(users, projects)
    assert len(violations) == 1
    assert 'roleType' in violations[0]


# ---------------------------------------------------------------------------
# Комбинации
# ---------------------------------------------------------------------------
def test_multiple_violations_accumulate():
    users = [_user('petya', ['my-wallet'])]                 # нет релизного namespace
    projects = [_project('my-casino', [_account('ghost')])]  # неизвестный SA
    violations = _verify(users, projects)
    assert len(violations) == 2


def test_realistic_config_is_clean():
    users = [
        _user('petya', [KARGO_NS, 'my-wallet', 'my-casino', 'my-video']),
        _user('gleb-ohr', [KARGO_NS, 'my-wallet']),
    ]
    projects = [
        _project('my-wallet', [
            _account('petya', 'kargo-viewer'),
            _account('petya', 'kargo-promoter'),
            _account('gleb-ohr', 'kargo-viewer'),
        ]),
        _project('my-casino', [
            _account('petya', 'kargo-project-secrets-reader', role_type='ClusterRole'),
        ]),
        _project('my-video', [_account('petya', 'kargo-admin')]),
    ]
    assert _verify(users, projects) == []


# ---------------------------------------------------------------------------
# _find_duplicates
# ---------------------------------------------------------------------------
def test_find_duplicates_empty():
    assert krv._find_duplicates([]) == []


def test_find_duplicates_no_dups():
    assert krv._find_duplicates(['a', 'b', 'c']) == []


def test_find_duplicates_preserves_order():
    assert krv._find_duplicates(['b', 'a', 'b', 'a']) == ['b', 'a']


def test_find_duplicates_no_double_in_result():
    assert krv._find_duplicates(['a', 'a', 'a']) == ['a']

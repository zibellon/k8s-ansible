"""Tests for filter_plugins/vault_config_verify.py.

conftest.py already inserts repo-root filter_plugins/ into sys.path,
so vault_config_verify is importable directly.
"""
import vault_config_verify as vcv


# ---------------------------------------------------------------------------
# Fixtures (local helpers)
# ---------------------------------------------------------------------------
def _policy(name):
    return {'name': name, 'rules': 'path "eso-secret/data/{}/*" {{ capabilities = ["read"] }}'.format(name)}


def _role(name, policies, namespace='test-ns'):
    return {
        'name': name,
        'bound_service_account_names': 'eso-main',
        'bound_service_account_namespaces': namespace,
        'policies': policies,
        'ttl': '1h',
    }


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
def test_happy_path_empty_inputs():
    assert vcv.vault_config_verify([], []) == []


def test_happy_path_valid_policies_roles():
    policies = [_policy('eso-argocd'), _policy('eso-gitlab')]
    roles = [
        _role('argocd.eso-main', ['eso-argocd']),
        _role('gitlab.eso-main', ['eso-gitlab']),
    ]
    assert vcv.vault_config_verify(policies, roles) == []


def test_happy_path_role_with_empty_policies():
    policies = [_policy('eso-traefik')]
    roles = [_role('traefik.eso-main', [])]
    assert vcv.vault_config_verify(policies, roles) == []


# ---------------------------------------------------------------------------
# G1 — duplicate policy names
# ---------------------------------------------------------------------------
def test_duplicate_policy_names():
    policies = [_policy('eso-argocd'), _policy('eso-argocd')]
    roles = []
    result = vcv.vault_config_verify(policies, roles)
    assert len(result) == 1
    assert 'duplicate policy names' in result[0]
    assert 'eso-argocd' in result[0]


def test_duplicate_policy_name_appears_once_in_violations():
    # Three entries with same name → still one violation message
    policies = [_policy('eso-dup'), _policy('eso-dup'), _policy('eso-dup')]
    roles = []
    result = vcv.vault_config_verify(policies, roles)
    assert len(result) == 1
    assert 'eso-dup' in result[0]


# ---------------------------------------------------------------------------
# G2 — duplicate role names
# ---------------------------------------------------------------------------
def test_duplicate_role_names():
    policies = [_policy('eso-p')]
    roles = [_role('ns.eso-main', ['eso-p']), _role('ns.eso-main', ['eso-p'])]
    result = vcv.vault_config_verify(policies, roles)
    assert len(result) == 1
    assert 'duplicate role names' in result[0]
    assert 'ns.eso-main' in result[0]


# ---------------------------------------------------------------------------
# G3 — referential integrity
# ---------------------------------------------------------------------------
def test_missing_policy_reference():
    policies = [_policy('eso-argocd')]
    roles = [_role('argocd.eso-main', ['eso-argocd', 'eso-missing'])]
    result = vcv.vault_config_verify(policies, roles)
    assert len(result) == 1
    assert "references policy 'eso-missing'" in result[0]
    assert 'argocd.eso-main' in result[0]


def test_multiple_missing_policy_references_from_same_role():
    policies = []
    roles = [_role('ns.eso-main', ['eso-a', 'eso-b'])]
    result = vcv.vault_config_verify(policies, roles)
    assert len(result) == 2
    msgs = ' '.join(result)
    assert 'eso-a' in msgs
    assert 'eso-b' in msgs


# ---------------------------------------------------------------------------
# Multiple violations at once
# ---------------------------------------------------------------------------
def test_multiple_violation_groups():
    # G1: dup policy, G2: dup role, G3: missing ref — all three in one call
    policies = [_policy('eso-dup'), _policy('eso-dup')]
    roles = [
        _role('dup-role', ['eso-dup']),
        _role('dup-role', ['eso-dup']),
        _role('broken-role', ['eso-nonexistent']),
    ]
    result = vcv.vault_config_verify(policies, roles)
    # G1 + G2 + G3 (one missing ref)
    assert len(result) == 3


# ---------------------------------------------------------------------------
# Private helper: _find_duplicates
# ---------------------------------------------------------------------------
def test_find_duplicates_empty():
    assert vcv._find_duplicates([]) == []


def test_find_duplicates_no_dups():
    assert vcv._find_duplicates(['a', 'b', 'c']) == []


def test_find_duplicates_preserves_order():
    result = vcv._find_duplicates(['b', 'a', 'b', 'a'])
    assert result == ['b', 'a']


def test_find_duplicates_no_double_in_result():
    # 'x' appears 3 times — should appear once in result
    assert vcv._find_duplicates(['x', 'x', 'x']) == ['x']

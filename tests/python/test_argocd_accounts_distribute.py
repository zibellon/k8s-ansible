"""Pytest unit tests for filter_plugins/argocd_accounts_distribute.py.

Layer 3 of make test runner — catches runtime Jinja2 issues that
syntax-check / ansible-lint / helm-validate don't see. Lives in
tests/python/; invoked via `make test-pytest` or direct `pytest
tests/python/`.
Shared fixtures + path setup — in tests/python/conftest.py.
"""
import json
import pytest
import argocd_accounts_distribute as ad
from ansible.errors import AnsibleFilterError
# =============================================================================
# account-distribute (Layer 3) — argocd_accounts_distribute_* (stateless)
# =============================================================================

def test_distribute_paths_to_add_happy(sample_argocd_vault_mirror):
    """Happy: stableuser with one path → one pair with correct creds."""
    target = [{'name': 'stableuser', 'passwordMtime': '2026-06-20T13:00:00Z', 'vault_paths': ['eso-secret/team/ci']}]
    result = ad.argocd_accounts_distribute_paths_to_add(sample_argocd_vault_mirror, target, '')
    assert result == [{'path': 'eso-secret/team/ci', 'username': 'stableuser', 'password': 'pw-s'}]


def test_distribute_paths_to_add_multi(sample_argocd_vault_mirror):
    """Multi: stableuser + root-admin each with distinct path → two ordered pairs."""
    target = [
        {'name': 'stableuser', 'passwordMtime': '2026-06-20T13:00:00Z', 'vault_paths': ['eso-secret/team/ci']},
        {'name': 'root-admin', 'passwordMtime': '2026-06-20T13:00:00Z', 'vault_paths': ['eso-secret/admin/root']},
    ]
    result = ad.argocd_accounts_distribute_paths_to_add(sample_argocd_vault_mirror, target, '')
    assert result == [
        {'path': 'eso-secret/team/ci', 'username': 'stableuser', 'password': 'pw-s'},
        {'path': 'eso-secret/admin/root', 'username': 'root-admin', 'password': 'pw-r'},
    ]


def test_distribute_paths_to_add_raises_missing_from_mirror(sample_argocd_vault_mirror):
    """Edge: account with vault_paths not in mirror → validation raise."""
    target = [{'name': 'ghost-account', 'passwordMtime': '2026-06-20T13:00:00Z', 'vault_paths': ['eso-secret/ghost']}]
    with pytest.raises(AnsibleFilterError, match='missing from the Vault mirror'):
        ad.argocd_accounts_distribute_paths_to_add(sample_argocd_vault_mirror, target, '')


def test_distribute_paths_to_add_raises_empty_plaintext():
    """Edge: account in mirror but plaintext is empty → validation raise."""
    mirror = {'x': {'plaintext': '', 'hash': '$2a$10$X', 'passwordMtime': '2026-01-01T00:00:00Z'}}
    target = [{'name': 'x', 'passwordMtime': '2026-06-20T13:00:00Z', 'vault_paths': ['eso-secret/x']}]
    with pytest.raises(AnsibleFilterError, match='missing from the Vault mirror'):
        ad.argocd_accounts_distribute_paths_to_add(mirror, target, '')


def test_distribute_paths_to_add_raises_duplicate(sample_argocd_vault_mirror):
    """Edge: two accounts sharing same path → paths-unique validation raise."""
    target = [
        {'name': 'stableuser', 'passwordMtime': '2026-06-20T13:00:00Z', 'vault_paths': ['shared/path']},
        {'name': 'root-admin', 'passwordMtime': '2026-06-20T13:00:00Z', 'vault_paths': ['shared/path']},
    ]
    with pytest.raises(AnsibleFilterError, match='Duplicate'):
        ad.argocd_accounts_distribute_paths_to_add(sample_argocd_vault_mirror, target, '')


def test_distribute_paths_to_delete_orphan():
    """Happy: state has stale path 'eso-secret/old', target has new path → old returned for delete."""
    state_json = json.dumps([{'account_name': 'stableuser', 'vault_paths': ['eso-secret/old']}])
    target = [{'name': 'stableuser', 'passwordMtime': '2026-06-20T13:00:00Z', 'vault_paths': ['eso-secret/new']}]
    result = ad.argocd_accounts_distribute_paths_to_delete({}, target, state_json)
    assert result == [{'path': 'eso-secret/old', 'account_name': 'stableuser'}]


def test_distribute_paths_to_delete_empty_state():
    """Edge: empty state (no ConfigMap yet) → empty deletes list."""
    target = [{'name': 'stableuser', 'passwordMtime': '2026-06-20T13:00:00Z', 'vault_paths': ['eso-secret/team/ci']}]
    result = ad.argocd_accounts_distribute_paths_to_delete({}, target, '')
    assert result == []


def test_distribute_paths_to_delete_raises_duplicate():
    """Edge: two accounts with same path → paths-unique validation raise."""
    target = [
        {'name': 'stableuser', 'passwordMtime': '2026-06-20T13:00:00Z', 'vault_paths': ['shared/path']},
        {'name': 'root-admin', 'passwordMtime': '2026-06-20T13:00:00Z', 'vault_paths': ['shared/path']},
    ]
    with pytest.raises(AnsibleFilterError, match='Duplicate'):
        ad.argocd_accounts_distribute_paths_to_delete({}, target, '')
# =============================================================================
# per-item ConfigMap state split — helpers
# =============================================================================
def test_parse_configmaplist_empty_and_malformed():
    assert ad._parse_configmaplist('') == []
    assert ad._parse_configmaplist(None) == []
    assert ad._parse_configmaplist('not json {') == []
    assert ad._parse_configmaplist('{}') == []
    assert ad._parse_configmaplist('{"items": "x"}') == []


def test_validate_configmap_name_valid():
    for name in ['argocd-accounts-distributions-stableuser', 'a', 'vasya.pupkin',
                 'argocd-accounts-distributions-root-admin']:
        ad._validate_configmap_name(name)  # no raise


def test_validate_configmap_name_invalid_raises():
    for bad in ['', 'UpperCase', 'has_underscore', '-leadinghyphen',
                'trailinghyphen-', 'a' * 254]:
        with pytest.raises(AnsibleFilterError, match='not a valid RFC 1123'):
            ad._validate_configmap_name(bad)


def test_item_configmap_entry_structure():
    entry = ad._item_configmap_entry('argocd-accounts-distributions-', 'stableuser',
                                     {'account_name': 'stableuser', 'vault_paths': ['p1']})
    assert entry['name'] == 'argocd-accounts-distributions-stableuser'
    assert json.loads(entry['content']) == {'account_name': 'stableuser', 'vault_paths': ['p1']}
    assert ad._item_configmap_entry('p-', 'x', {'b': 1, 'a': 2})['content'] == '{"a": 2, "b": 1}'
# =============================================================================
# per-item ConfigMap state split — apply (account-distribute)
# =============================================================================
def test_distribute_configmaps_to_apply_happy():
    """Happy: account with vault_paths → 1 entry with correct name and content."""
    target = [
        {'name': 'no-paths-account'},
        {'name': 'stableuser', 'passwordMtime': '2026-06-20T13:00:00Z', 'vault_paths': ['eso-secret/team/ci']},
    ]
    result = ad.argocd_accounts_distribute_configmaps_to_apply(target)
    assert len(result) == 1
    assert result[0]['name'] == 'argocd-accounts-distributions-stableuser'
    assert json.loads(result[0]['content']) == {
        'account_name': 'stableuser',
        'vault_paths': ['eso-secret/team/ci'],
        'passwordMtime': '2026-06-20T13:00:00Z',
    }


def test_distribute_configmaps_to_apply_empty_and_no_paths():
    assert ad.argocd_accounts_distribute_configmaps_to_apply([]) == []
    assert ad.argocd_accounts_distribute_configmaps_to_apply(
        [{'name': 'stableuser'}]) == []


def test_distribute_configmaps_to_apply_raises_duplicate():
    target = [
        {'name': 'stableuser', 'passwordMtime': '2026-06-20T13:00:00Z', 'vault_paths': ['shared/path']},
        {'name': 'root-admin', 'passwordMtime': '2026-06-20T13:00:00Z', 'vault_paths': ['shared/path']},
    ]
    with pytest.raises(AnsibleFilterError, match='Duplicate'):
        ad.argocd_accounts_distribute_configmaps_to_apply(target)


def test_distribute_configmaps_to_apply_raises_invalid_name():
    """Edge: account name with underscore → computed CM name fails RFC 1123."""
    target = [{'name': 'bad_name', 'passwordMtime': '2026-06-20T13:00:00Z', 'vault_paths': ['p1']}]
    with pytest.raises(AnsibleFilterError, match='not a valid RFC 1123'):
        ad.argocd_accounts_distribute_configmaps_to_apply(target)
# =============================================================================
# per-item ConfigMap state split — reconstruction + stale-delete (generic)
# =============================================================================
def test_state_configmaps_to_combined_json_happy():
    """Happy: 2 CMs with .data.state → combined JSON array."""
    raw = json.dumps({'items': [
        {'metadata': {'name': 'argocd-accounts-distributions-stableuser'},
         'data': {'state': json.dumps({'account_name': 'stableuser', 'vault_paths': ['p1']}, sort_keys=True)}},
        {'metadata': {'name': 'argocd-accounts-distributions-root-admin'},
         'data': {'state': json.dumps({'account_name': 'root-admin', 'vault_paths': ['p2']}, sort_keys=True)}},
    ]})
    parsed = json.loads(ad.argocd_accounts_state_configmaps_to_combined_json(raw))
    assert isinstance(parsed, list) and len(parsed) == 2
    assert {e['account_name'] for e in parsed} == {'stableuser', 'root-admin'}


def test_state_configmaps_to_combined_json_empty_and_malformed():
    assert json.loads(ad.argocd_accounts_state_configmaps_to_combined_json('')) == []
    assert json.loads(ad.argocd_accounts_state_configmaps_to_combined_json(None)) == []
    assert json.loads(ad.argocd_accounts_state_configmaps_to_combined_json('{"items": []}')) == []
    assert json.loads(ad.argocd_accounts_state_configmaps_to_combined_json('garbage {')) == []


def test_state_configmaps_to_combined_json_skips_item_without_state():
    raw = json.dumps({'items': [
        {'metadata': {'name': 'cm-a'}, 'data': {'state': '{"account_name": "a"}'}},
        {'metadata': {'name': 'cm-b'}},
        {'metadata': {'name': 'cm-c'}, 'data': {}},
    ]})
    assert json.loads(ad.argocd_accounts_state_configmaps_to_combined_json(raw)) == [{'account_name': 'a'}]


def test_state_configmaps_to_combined_json_determinism():
    raw = json.dumps({'items': [
        {'metadata': {'name': 'cm-a'}, 'data': {'state': '{"account_name": "a", "vault_paths": ["p1"]}'}},
        {'metadata': {'name': 'cm-b'}, 'data': {'state': '{"account_name": "b", "vault_paths": ["p2"]}'}},
    ]})
    r1 = ad.argocd_accounts_state_configmaps_to_combined_json(raw)
    r2 = ad.argocd_accounts_state_configmaps_to_combined_json(raw)
    assert r1 == r2


def test_state_configmaps_to_delete_orphan():
    raw = json.dumps({'items': [
        {'metadata': {'name': 'argocd-accounts-distributions-stableuser'}},
        {'metadata': {'name': 'argocd-accounts-distributions-olduser'}},
    ]})
    result = ad.argocd_accounts_state_configmaps_to_delete(
        raw, ['argocd-accounts-distributions-stableuser'])
    assert result == ['argocd-accounts-distributions-olduser']


def test_state_configmaps_to_delete_all_kept():
    raw = json.dumps({'items': [
        {'metadata': {'name': 'argocd-accounts-distributions-stableuser'}},
        {'metadata': {'name': 'argocd-accounts-distributions-root-admin'}},
    ]})
    result = ad.argocd_accounts_state_configmaps_to_delete(
        raw, ['argocd-accounts-distributions-stableuser', 'argocd-accounts-distributions-root-admin'])
    assert result == []


def test_state_configmaps_to_delete_empty_input():
    assert ad.argocd_accounts_state_configmaps_to_delete('', ['x']) == []
    assert ad.argocd_accounts_state_configmaps_to_delete('{"items": []}', ['x']) == []


def test_state_configmaps_to_delete_empty_target():
    raw = json.dumps({'items': [
        {'metadata': {'name': 'argocd-accounts-distributions-stableuser'}},
        {'metadata': {'name': 'argocd-accounts-distributions-root-admin'}},
    ]})
    result = ad.argocd_accounts_state_configmaps_to_delete(raw, [])
    assert set(result) == {
        'argocd-accounts-distributions-stableuser',
        'argocd-accounts-distributions-root-admin',
    }


def test_distribute_paths_to_add_skips_unchanged(sample_argocd_vault_mirror):
    """Diff: path already in state, same passwordMtime → skipped."""
    state = json.dumps([{'account_name': 'stableuser',
                         'vault_paths': ['eso-secret/team/ci'],
                         'passwordMtime': '2026-06-20T13:00:00Z'}])
    target = [{'name': 'stableuser', 'passwordMtime': '2026-06-20T13:00:00Z',
               'vault_paths': ['eso-secret/team/ci']}]
    assert ad.argocd_accounts_distribute_paths_to_add(sample_argocd_vault_mirror, target, state) == []

def test_distribute_paths_to_add_emits_only_new_path(sample_argocd_vault_mirror):
    """Diff: same passwordMtime, one new path added → only the new path emitted."""
    state = json.dumps([{'account_name': 'stableuser',
                         'vault_paths': ['eso-secret/team/ci'],
                         'passwordMtime': '2026-06-20T13:00:00Z'}])
    target = [{'name': 'stableuser', 'passwordMtime': '2026-06-20T13:00:00Z',
               'vault_paths': ['eso-secret/team/ci', 'eso-secret/team/ci2']}]
    result = ad.argocd_accounts_distribute_paths_to_add(sample_argocd_vault_mirror, target, state)
    assert result == [{'path': 'eso-secret/team/ci2', 'username': 'stableuser', 'password': 'pw-s'}]

def test_distribute_paths_to_add_re_emits_all_on_rotation(sample_argocd_vault_mirror):
    """Diff: passwordMtime changed → ALL paths of that account re-emitted."""
    state = json.dumps([{'account_name': 'stableuser',
                         'vault_paths': ['eso-secret/team/ci', 'eso-secret/team/ci2'],
                         'passwordMtime': '2026-06-20T13:00:00Z'}])
    target = [{'name': 'stableuser', 'passwordMtime': '2026-06-25T00:00:00Z',
               'vault_paths': ['eso-secret/team/ci', 'eso-secret/team/ci2']}]
    result = ad.argocd_accounts_distribute_paths_to_add(sample_argocd_vault_mirror, target, state)
    assert result == [
        {'path': 'eso-secret/team/ci', 'username': 'stableuser', 'password': 'pw-s'},
        {'path': 'eso-secret/team/ci2', 'username': 'stableuser', 'password': 'pw-s'},
    ]

def test_configmaps_to_apply_changed_skips_unchanged():
    """Unchanged account (same vault_paths + passwordMtime) → not in changed list."""
    target = [{'name': 'stableuser', 'passwordMtime': '2026-06-20T13:00:00Z', 'vault_paths': ['p1']}]
    state = json.dumps([{'account_name': 'stableuser', 'vault_paths': ['p1'],
                         'passwordMtime': '2026-06-20T13:00:00Z'}])
    assert ad.argocd_accounts_distribute_configmaps_to_apply_changed(target, state) == []

def test_configmaps_to_apply_changed_new_account():
    """Account absent from state → included."""
    target = [{'name': 'stableuser', 'passwordMtime': '2026-06-20T13:00:00Z', 'vault_paths': ['p1']}]
    result = ad.argocd_accounts_distribute_configmaps_to_apply_changed(target, '')
    assert len(result) == 1 and result[0]['name'] == 'argocd-accounts-distributions-stableuser'

def test_configmaps_to_apply_changed_mtime_change():
    """passwordMtime changed → included."""
    target = [{'name': 'stableuser', 'passwordMtime': '2026-06-25T00:00:00Z', 'vault_paths': ['p1']}]
    state = json.dumps([{'account_name': 'stableuser', 'vault_paths': ['p1'],
                         'passwordMtime': '2026-06-20T13:00:00Z'}])
    assert len(ad.argocd_accounts_distribute_configmaps_to_apply_changed(target, state)) == 1

def test_configmaps_to_apply_changed_paths_change():
    """vault_paths changed → included."""
    target = [{'name': 'stableuser', 'passwordMtime': '2026-06-20T13:00:00Z', 'vault_paths': ['p1', 'p2']}]
    state = json.dumps([{'account_name': 'stableuser', 'vault_paths': ['p1'],
                         'passwordMtime': '2026-06-20T13:00:00Z'}])
    assert len(ad.argocd_accounts_distribute_configmaps_to_apply_changed(target, state)) == 1

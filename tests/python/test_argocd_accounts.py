"""Pytest unit tests for filter_plugins/argocd_accounts.py (Layer 1).

Part of the make test runner (Layer 3). Shared fixtures + path setup in
tests/python/conftest.py.
"""
import pytest
import argocd_accounts as ac
from ansible.errors import AnsibleFilterError


# ---------------------------------------------------------------------------
# _parse_live_accounts
# ---------------------------------------------------------------------------

def test_parse_live_accounts_happy(sample_argocd_live_secret_data):
    result = ac._parse_live_accounts(sample_argocd_live_secret_data)
    assert result == {
        'stableuser':   {'hash': '$2a$10$STABLE', 'mtime': '2026-06-20T13:00:00Z'},
        'vasya.pupkin': {'hash': '$2a$10$VASYA',  'mtime': '2026-06-20T13:00:00Z'},
        'olduser':      {'hash': '$2a$10$OLD',    'mtime': '2026-06-19T00:00:00Z'},
        'ghost':        {'hash': '$2a$10$GHOST',  'mtime': '2026-06-01T00:00:00Z'},
    }


def test_parse_live_accounts_ignores_server_secretkey(sample_argocd_live_secret_data):
    result = ac._parse_live_accounts(sample_argocd_live_secret_data)
    assert 'server.secretkey' not in result
    # none of the parsed names contain a dot that comes from server.secretkey
    assert 'SIGNINGKEY' not in str(result)


def test_parse_live_accounts_ignores_tokens(sample_argocd_live_secret_data):
    result = ac._parse_live_accounts(sample_argocd_live_secret_data)
    # tokens key must not create an entry or corrupt olduser
    assert set(result['olduser'].keys()) == {'hash', 'mtime'}


def test_parse_live_accounts_dotted_name(sample_argocd_live_secret_data):
    result = ac._parse_live_accounts(sample_argocd_live_secret_data)
    assert 'vasya.pupkin' in result


def test_parse_live_accounts_empty():
    assert ac._parse_live_accounts({}) == {}
    assert ac._parse_live_accounts(None) == {}
    assert ac._parse_live_accounts([]) == {}


# ---------------------------------------------------------------------------
# argocd_accounts_to_create
# ---------------------------------------------------------------------------

def test_to_create_petya_only(sample_argocd_desired, sample_argocd_vault_mirror):
    result = ac.argocd_accounts_to_create(sample_argocd_desired, sample_argocd_vault_mirror)
    assert result == [{'name': 'petya', 'passwordMtime': '2026-06-20T13:00:00Z'}]


def test_to_create_no_secret_key_leaked(sample_argocd_desired, sample_argocd_vault_mirror):
    result = ac.argocd_accounts_to_create(sample_argocd_desired, sample_argocd_vault_mirror)
    assert 'SIGNINGKEY' not in str(result)
    assert 'server.secretkey' not in str(result)


# ---------------------------------------------------------------------------
# argocd_accounts_to_delete
# ---------------------------------------------------------------------------

def test_to_delete_ghost_and_olduser(
        sample_argocd_desired, sample_argocd_vault_mirror, sample_argocd_live_secret_data):
    result = ac.argocd_accounts_to_delete(
        sample_argocd_desired, sample_argocd_vault_mirror, sample_argocd_live_secret_data)
    assert result == ['ghost', 'olduser']


def test_to_delete_sorted(
        sample_argocd_desired, sample_argocd_vault_mirror, sample_argocd_live_secret_data):
    result = ac.argocd_accounts_to_delete(
        sample_argocd_desired, sample_argocd_vault_mirror, sample_argocd_live_secret_data)
    assert result == sorted(result)


def test_to_delete_no_secret_key_leaked(
        sample_argocd_desired, sample_argocd_vault_mirror, sample_argocd_live_secret_data):
    result = ac.argocd_accounts_to_delete(
        sample_argocd_desired, sample_argocd_vault_mirror, sample_argocd_live_secret_data)
    assert 'SIGNINGKEY' not in str(result)
    assert 'server.secretkey' not in str(result)


# ---------------------------------------------------------------------------
# argocd_accounts_to_rotate
# ---------------------------------------------------------------------------

def test_to_rotate_vasya_only(sample_argocd_desired, sample_argocd_vault_mirror):
    result = ac.argocd_accounts_to_rotate(sample_argocd_desired, sample_argocd_vault_mirror)
    assert result == [{'name': 'vasya.pupkin', 'passwordMtime': '2026-06-21T10:00:00Z'}]


def test_to_rotate_no_secret_key_leaked(sample_argocd_desired, sample_argocd_vault_mirror):
    result = ac.argocd_accounts_to_rotate(sample_argocd_desired, sample_argocd_vault_mirror)
    assert 'SIGNINGKEY' not in str(result)
    assert 'server.secretkey' not in str(result)


# ---------------------------------------------------------------------------
# argocd_accounts_to_resync
# ---------------------------------------------------------------------------

def test_to_resync_root_admin_only(
        sample_argocd_desired, sample_argocd_vault_mirror, sample_argocd_live_secret_data):
    result = ac.argocd_accounts_to_resync(
        sample_argocd_desired, sample_argocd_vault_mirror, sample_argocd_live_secret_data)
    assert result == [{'name': 'root-admin', 'hash': '$2a$10$ROOT', 'passwordMtime': '2026-06-20T13:00:00Z'}]


def test_to_resync_stableuser_excluded(
        sample_argocd_desired, sample_argocd_vault_mirror, sample_argocd_live_secret_data):
    result = ac.argocd_accounts_to_resync(
        sample_argocd_desired, sample_argocd_vault_mirror, sample_argocd_live_secret_data)
    assert all(e['name'] != 'stableuser' for e in result)


def test_to_resync_vasya_excluded_as_rotate(
        sample_argocd_desired, sample_argocd_vault_mirror, sample_argocd_live_secret_data):
    result = ac.argocd_accounts_to_resync(
        sample_argocd_desired, sample_argocd_vault_mirror, sample_argocd_live_secret_data)
    assert all(e['name'] != 'vasya.pupkin' for e in result)


def test_to_resync_no_secret_key_leaked(
        sample_argocd_desired, sample_argocd_vault_mirror, sample_argocd_live_secret_data):
    result = ac.argocd_accounts_to_resync(
        sample_argocd_desired, sample_argocd_vault_mirror, sample_argocd_live_secret_data)
    assert 'SIGNINGKEY' not in str(result)
    assert 'server.secretkey' not in str(result)


# ---------------------------------------------------------------------------
# Critical safety: SIGNINGKEY must not appear in any delta
# ---------------------------------------------------------------------------

def test_no_signing_key_in_any_delta(
        sample_argocd_desired, sample_argocd_vault_mirror, sample_argocd_live_secret_data):
    create = ac.argocd_accounts_to_create(sample_argocd_desired, sample_argocd_vault_mirror)
    delete = ac.argocd_accounts_to_delete(
        sample_argocd_desired, sample_argocd_vault_mirror, sample_argocd_live_secret_data)
    rotate = ac.argocd_accounts_to_rotate(sample_argocd_desired, sample_argocd_vault_mirror)
    resync = ac.argocd_accounts_to_resync(
        sample_argocd_desired, sample_argocd_vault_mirror, sample_argocd_live_secret_data)
    for delta in (create, delete, rotate, resync):
        assert 'SIGNINGKEY' not in str(delta)
        assert 'server.secretkey' not in str(delta)


# ---------------------------------------------------------------------------
# Steady-state: all 4 filters return [] when desired == vault == live
# ---------------------------------------------------------------------------

def test_steady_state_all_empty(sample_argocd_live_secret_data):
    import base64
    desired = [{'name': 'stableuser', 'passwordMtime': '2026-06-20T13:00:00Z'}]
    vm = {'stableuser': {'plaintext': 'pw-s', 'hash': '$2a$10$STABLE', 'passwordMtime': '2026-06-20T13:00:00Z'}}
    def b(s):
        return base64.b64encode(s.encode()).decode()
    live = {
        'accounts.stableuser.password': b('$2a$10$STABLE'),
        'accounts.stableuser.passwordMtime': b('2026-06-20T13:00:00Z'),
    }
    assert ac.argocd_accounts_to_create(desired, vm) == []
    assert ac.argocd_accounts_to_delete(desired, vm, live) == []
    assert ac.argocd_accounts_to_rotate(desired, vm) == []
    assert ac.argocd_accounts_to_resync(desired, vm, live) == []


# ---------------------------------------------------------------------------
# Validation raises
# ---------------------------------------------------------------------------

def test_validate_not_a_list():
    with pytest.raises(AnsibleFilterError, match='must be a list'):
        ac.argocd_accounts_to_create({'name': 'x', 'passwordMtime': '2026-06-20T13:00:00Z'}, {})


def test_validate_item_without_name():
    with pytest.raises(AnsibleFilterError, match="non-empty string 'name'"):
        ac.argocd_accounts_to_create([{'passwordMtime': '2026-06-20T13:00:00Z'}], {})


def test_validate_name_with_slash():
    with pytest.raises(AnsibleFilterError, match='invalid name'):
        ac.argocd_accounts_to_create([{'name': 'a/b', 'passwordMtime': '2026-06-20T13:00:00Z'}], {})


def test_validate_duplicate_name():
    desired = [
        {'name': 'dup', 'passwordMtime': '2026-06-20T13:00:00Z'},
        {'name': 'dup', 'passwordMtime': '2026-06-20T13:00:00Z'},
    ]
    with pytest.raises(AnsibleFilterError, match='duplicate name'):
        ac.argocd_accounts_to_create(desired, {})


def test_validate_missing_mtime():
    with pytest.raises(AnsibleFilterError, match="non-empty 'passwordMtime'"):
        ac.argocd_accounts_to_create([{'name': 'x'}], {})


def test_validate_mtime_not_rfc3339():
    with pytest.raises(AnsibleFilterError, match='not RFC3339'):
        ac.argocd_accounts_to_create([{'name': 'x', 'passwordMtime': '2026-06-20 13:00'}], {})

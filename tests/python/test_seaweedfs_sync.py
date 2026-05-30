"""Pytest unit tests for filter_plugins/seaweedfs_sync.py.

Layer 3 of make test runner — catches runtime Jinja2 issues that
syntax-check / ansible-lint / helm-validate don't see. Lives in
tests/python/; invoked via `make test-pytest` or direct `pytest
tests/python/`.
Shared fixtures + path setup — in tests/python/conftest.py.
"""
import json
import pytest
import seaweedfs_sync as sw
from ansible.errors import AnsibleFilterError
# =============================================================================
# Shared utilities
# =============================================================================
def test_parse_combined_json_empty_returns_empty_default():
    assert sw._parse_combined_json('') == {'identities': []}
    assert sw._parse_combined_json(None) == {'identities': []}
def test_parse_combined_json_valid_returns_dict():
    s = '{"identities": [{"name": "alice", "credentials": [{"accessKey": "AK", "secretKey": "SK"}]}]}'
    result = sw._parse_combined_json(s)
    assert result['identities'][0]['name'] == 'alice'
def test_parse_combined_json_malformed_returns_empty_default():
    assert sw._parse_combined_json('not valid json {') == {'identities': []}
    assert sw._parse_combined_json('{}') == {'identities': []}
def test_extract_creds_by_name_builds_mapping():
    combined = {
        'identities': [
            {'name': 'alice', 'credentials': [{'accessKey': 'AK_A', 'secretKey': 'SK_A'}]},
            {'name': 'bob',   'credentials': [{'accessKey': 'AK_B', 'secretKey': 'SK_B'}]},
        ]
    }
    result = sw._extract_creds_by_name(combined)
    assert result['alice'] == {'accessKey': 'AK_A', 'secretKey': 'SK_A'}
    assert result['bob']['accessKey'] == 'AK_B'
def test_extract_creds_by_name_empty_combined():
    assert sw._extract_creds_by_name({'identities': []}) == {}
    assert sw._extract_creds_by_name({}) == {}
def test_extract_creds_by_name_handles_missing_credentials():
    combined = {'identities': [{'name': 'alice'}]}
    result = sw._extract_creds_by_name(combined)
    assert result['alice'] == {'accessKey': '', 'secretKey': ''}
# =============================================================================
# user-sync (Layer 1) — seaweedfs_user_sync_full (stateless)
# =============================================================================

def _patch_secrets_deterministic(monkeypatch):
    """Mock secrets.choice to always return first char of alphabet — determinism."""
    monkeypatch.setattr(sw.secrets, 'choice', lambda alphabet: alphabet[0])


def test_user_sync_full_happy_with_mock(monkeypatch, sample_vault_json, sample_generate_params):
    """Happy path: target = [alice], current has alice — update path, preserve creds."""
    _patch_secrets_deterministic(monkeypatch)
    target = [{'name': 'alice', 'actions': ['NewAction']}]
    result = sw.seaweedfs_user_sync_full(sample_vault_json, target, **sample_generate_params)
    parsed = json.loads(result)
    assert len(parsed['identities']) == 1
    assert parsed['identities'][0]['name'] == 'alice'
    # preserved existing creds
    assert parsed['identities'][0]['credentials'][0]['accessKey'] == 'ALICE_AK'
    assert parsed['identities'][0]['actions'] == ['NewAction']


def test_user_sync_full_empty_vault(monkeypatch, sample_target_identities, sample_generate_params):
    """Edge: empty vault → all target identities to_create."""
    _patch_secrets_deterministic(monkeypatch)
    result = sw.seaweedfs_user_sync_full('', sample_target_identities, **sample_generate_params)
    parsed = json.loads(result)
    names = [i['name'] for i in parsed['identities']]
    assert set(names) == {'admin', 'alice', 'anonymous'}
    # Mocked: first char × length
    admin = next(i for i in parsed['identities'] if i['name'] == 'admin')
    assert admin['credentials'][0]['accessKey'] == 'A' * 20
    # anonymous gets empty creds
    anon = next(i for i in parsed['identities'] if i['name'] == 'anonymous')
    assert anon['credentials'][0]['accessKey'] == ''
    assert anon['credentials'][0]['secretKey'] == ''


def test_user_sync_full_only_anonymous(monkeypatch, sample_generate_params):
    """Edge: target = [anonymous] only → no AK/SK generation, empty creds."""
    _patch_secrets_deterministic(monkeypatch)
    target = [{'name': 'anonymous', 'actions': []}]
    result = sw.seaweedfs_user_sync_full('', target, **sample_generate_params)
    parsed = json.loads(result)
    assert len(parsed['identities']) == 1
    assert parsed['identities'][0]['credentials'][0]['accessKey'] == ''


def test_user_sync_full_purity_length_charset(sample_generate_params):
    """Purity (no mock): real secrets.choice — verify length + charset compliance."""
    target = [{'name': 'alice', 'actions': []}]
    result = sw.seaweedfs_user_sync_full('', target, **sample_generate_params)
    parsed = json.loads(result)
    creds = parsed['identities'][0]['credentials'][0]
    assert len(creds['accessKey']) == 20
    assert len(creds['secretKey']) == 40
    assert all(c in sample_generate_params['access_key_charset'] for c in creds['accessKey'])
    assert all(c in sample_generate_params['secret_key_charset'] for c in creds['secretKey'])


def test_user_sync_full_determinism_with_mock(monkeypatch, sample_target_identities, sample_generate_params):
    """Determinism: two calls with same mock → identical output string."""
    _patch_secrets_deterministic(monkeypatch)
    result1 = sw.seaweedfs_user_sync_full('', sample_target_identities, **sample_generate_params)
    result2 = sw.seaweedfs_user_sync_full('', sample_target_identities, **sample_generate_params)
    assert result1 == result2


def test_user_sync_full_randomness_without_mock(sample_target_identities, sample_generate_params):
    """Non-mocked: two calls produce different AK/SK (verify real randomness)."""
    result1 = sw.seaweedfs_user_sync_full('', sample_target_identities, **sample_generate_params)
    result2 = sw.seaweedfs_user_sync_full('', sample_target_identities, **sample_generate_params)
    assert result1 != result2
# =============================================================================
# identity-distribute (Layer 3) — seaweedfs_distribute_* (stateless)
# =============================================================================

def test_distribute_paths_to_delete_orphan_path(sample_vault_json, sample_configmap_state_distribute):
    """Happy: state has stale path 'eso-secret/team/alice/old', target has new path — old returned for delete."""
    target = [{'name': 'alice', 'extra_vault_paths': ['eso-secret/team/alice/new']}]
    result = sw.seaweedfs_distribute_paths_to_delete(
        sample_vault_json, target, sample_configmap_state_distribute)
    assert result == [{'path': 'eso-secret/team/alice/old', 'identity_name': 'alice'}]


def test_distribute_paths_to_delete_empty_state(sample_vault_json):
    """Edge: empty state (no ConfigMap yet) → empty deletes list."""
    target = [{'name': 'alice', 'extra_vault_paths': ['p1']}]
    result = sw.seaweedfs_distribute_paths_to_delete(sample_vault_json, target, '')
    assert result == []


def test_distribute_paths_to_delete_raises_on_anonymous_with_extra():
    """Edge: anonymous identity with extra_vault_paths → validation raise."""
    target = [{'name': 'anonymous', 'extra_vault_paths': ['p1']}]
    with pytest.raises(AnsibleFilterError, match='Anonymous'):
        sw.seaweedfs_distribute_paths_to_delete('', target, '')


def test_distribute_paths_to_add_happy(sample_vault_json):
    """Happy: alice with one path → returns one pair with embedded creds."""
    target = [{'name': 'alice', 'extra_vault_paths': ['eso-secret/team/alice/new']}]
    result = sw.seaweedfs_distribute_paths_to_add(sample_vault_json, target, '')
    assert result == [{
        'path': 'eso-secret/team/alice/new',
        'name': 'alice',
        'accessKey': 'ALICE_AK',
        'secretKey': 'ALICE_SK',
    }]


def test_distribute_paths_to_add_raises_on_missing_creds(sample_vault_json):
    """Edge: identity 'orphan' not in combined JSON → validation raise."""
    target = [{'name': 'orphan', 'extra_vault_paths': ['p1']}]
    with pytest.raises(AnsibleFilterError, match='missing in Vault'):
        sw.seaweedfs_distribute_paths_to_add(sample_vault_json, target, '')


def test_distribute_paths_to_add_raises_on_duplicate_paths(sample_vault_json):
    """Edge: two identities sharing same path → paths-unique validation raise."""
    target = [
        {'name': 'admin', 'extra_vault_paths': ['shared/path']},
        {'name': 'alice', 'extra_vault_paths': ['shared/path']},
    ]
    with pytest.raises(AnsibleFilterError, match='Duplicate'):
        sw.seaweedfs_distribute_paths_to_add(sample_vault_json, target, '')


def test_distribute_new_state_json_happy(sample_target_identities):
    """Happy: target with alice + extra_vault_paths → serialized state JSON contains alice entry."""
    target = [{'name': 'alice', 'extra_vault_paths': ['p1', 'p2']}]
    result = sw.seaweedfs_distribute_new_state_json('', target, '')
    parsed = json.loads(result)
    assert parsed == [{'identity_name': 'alice', 'vault_paths': ['p1', 'p2']}]


def test_distribute_new_state_json_empty_target():
    """Edge: empty target → '[]' string."""
    result = sw.seaweedfs_distribute_new_state_json('', [], '')
    assert json.loads(result) == []


def test_distribute_new_state_json_skips_identities_without_paths():
    """Edge: target with identity без extra_vault_paths → not included in state."""
    target = [
        {'name': 'admin', 'actions': ['Admin']},  # no extra_vault_paths
        {'name': 'alice', 'extra_vault_paths': ['p1']},
    ]
    result = sw.seaweedfs_distribute_new_state_json('', target, '')
    parsed = json.loads(result)
    assert len(parsed) == 1
    assert parsed[0]['identity_name'] == 'alice'
# =============================================================================
# bucket-sync (Layer 2) filters
# =============================================================================
def test_compute_bucket_diff_empty_state_all_to_create():
    target = [{'name': 'b1'}, {'name': 'b2'}]
    result = sw.seaweedfs_compute_bucket_diff([], target)
    assert len(result['to_create_buckets']) == 2
    assert result['to_delete_buckets'] == []
def test_compute_bucket_diff_all_to_delete():
    state = [{'name': 'b1'}, {'name': 'b2'}]
    result = sw.seaweedfs_compute_bucket_diff(state, [])
    assert len(result['to_delete_buckets']) == 2
    assert result['to_create_buckets'] == []
def test_compute_bucket_diff_kept_policies_to_apply():
    state = [{'name': 'b1'}]
    target = [{'name': 'b1', 'policy': {'Version': '2012-10-17'}}]
    result = sw.seaweedfs_compute_bucket_diff(state, target)
    assert len(result['kept_policies_to_apply']) == 1
def test_compute_bucket_diff_kept_policies_to_delete():
    state = [{'name': 'b1', 'policy': {'Version': '2012-10-17'}}]
    target = [{'name': 'b1'}]
    result = sw.seaweedfs_compute_bucket_diff(state, target)
    assert len(result['kept_policies_to_delete']) == 1
    assert result['kept_policies_to_delete'][0]['name'] == 'b1'
def test_compute_bucket_diff_quotas_filter():
    target = [
        {'name': 'b1', 'quota': {'enabled': True, 'size': '1GiB'}},
        {'name': 'b2'},
    ]
    result = sw.seaweedfs_compute_bucket_diff([], target)
    assert len(result['quotas_to_apply']) == 1
    assert result['quotas_to_apply'][0]['name'] == 'b1'
def test_quota_size_to_mib_mib():
    assert sw.seaweedfs_quota_size_to_mib('100MiB') == 100
def test_quota_size_to_mib_gib():
    assert sw.seaweedfs_quota_size_to_mib('1GiB') == 1024
    assert sw.seaweedfs_quota_size_to_mib('100GiB') == 102400
def test_quota_size_to_mib_tib():
    assert sw.seaweedfs_quota_size_to_mib('1TiB') == 1048576
def test_quota_size_to_mib_invalid_suffix_raises():
    with pytest.raises(AnsibleFilterError, match='Unsupported'):
        sw.seaweedfs_quota_size_to_mib('100GB')
def test_validate_principal_not_dict_string_ok():
    assert sw.seaweedfs_validate_principal_not_dict(
        {'Principal': 'arn:aws:iam::*:user/alice'}) is True
def test_validate_principal_not_dict_list_ok():
    assert sw.seaweedfs_validate_principal_not_dict(
        {'Principal': ['arn:1', 'arn:2']}) is True
def test_validate_principal_not_dict_dict_raises():
    with pytest.raises(AnsibleFilterError, match='must be flat string'):
        sw.seaweedfs_validate_principal_not_dict(
            {'Principal': {'AWS': 'arn:1'}})

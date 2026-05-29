"""Pytest unit tests for filter_plugins/seaweedfs_sync.py.

Layer 3 of make test runner — catches runtime Jinja2 issues that
syntax-check / ansible-lint / helm-validate don't see. Lives in
tests/python/; invoked via `make test-pytest` or direct `pytest
tests/python/`.
Path setup: prepends repo-root filter_plugins/ to sys.path so
seaweedfs_sync module is importable (runs from any cwd).
"""
import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent / 'filter_plugins'))
import pytest
import seaweedfs_sync as sw
from ansible.errors import AnsibleFilterError
# =============================================================================
# Shared utilities
# =============================================================================
def test_parse_combined_json_empty_returns_empty_default():
    assert sw.seaweedfs_parse_combined_json('') == {'identities': []}
    assert sw.seaweedfs_parse_combined_json(None) == {'identities': []}
def test_parse_combined_json_valid_returns_dict():
    s = '{"identities": [{"name": "alice", "credentials": [{"accessKey": "AK", "secretKey": "SK"}]}]}'
    result = sw.seaweedfs_parse_combined_json(s)
    assert result['identities'][0]['name'] == 'alice'
def test_parse_combined_json_malformed_returns_empty_default():
    assert sw.seaweedfs_parse_combined_json('not valid json {') == {'identities': []}
    assert sw.seaweedfs_parse_combined_json('{}') == {'identities': []}
def test_extract_creds_by_name_builds_mapping():
    combined = {
        'identities': [
            {'name': 'alice', 'credentials': [{'accessKey': 'AK_A', 'secretKey': 'SK_A'}]},
            {'name': 'bob',   'credentials': [{'accessKey': 'AK_B', 'secretKey': 'SK_B'}]},
        ]
    }
    result = sw.seaweedfs_extract_creds_by_name(combined)
    assert result['alice'] == {'accessKey': 'AK_A', 'secretKey': 'SK_A'}
    assert result['bob']['accessKey'] == 'AK_B'
def test_extract_creds_by_name_empty_combined():
    assert sw.seaweedfs_extract_creds_by_name({'identities': []}) == {}
    assert sw.seaweedfs_extract_creds_by_name({}) == {}
def test_extract_creds_by_name_handles_missing_credentials():
    combined = {'identities': [{'name': 'alice'}]}
    result = sw.seaweedfs_extract_creds_by_name(combined)
    assert result['alice'] == {'accessKey': '', 'secretKey': ''}
# =============================================================================
# user-sync (Layer 1) filters
# =============================================================================
def test_compute_identity_diff_empty_inputs():
    result = sw.seaweedfs_compute_identity_diff([], [])
    assert result == {'to_create': [], 'to_update': [], 'to_delete': []}
def test_compute_identity_diff_all_to_create():
    target = [{'name': 'alice'}, {'name': 'bob'}]
    result = sw.seaweedfs_compute_identity_diff([], target)
    assert len(result['to_create']) == 2
    assert result['to_update'] == []
    assert result['to_delete'] == []
def test_compute_identity_diff_mixed():
    current = [{'name': 'alice'}, {'name': 'charlie'}]
    target = [{'name': 'alice'}, {'name': 'bob'}]
    result = sw.seaweedfs_compute_identity_diff(current, target)
    assert [i['name'] for i in result['to_create']] == ['bob']
    assert [i['name'] for i in result['to_update']] == ['alice']
    assert [i['name'] for i in result['to_delete']] == ['charlie']
def test_build_combined_identities_create_new():
    diff = {
        'to_create': [{'name': 'alice', 'actions': []}],
        'to_update': [],
        'to_delete': [],
    }
    new_secrets = {'alice': {'accessKey': 'AK_A', 'secretKey': 'SK_A'}}
    result = sw.seaweedfs_build_combined_identities(diff, [], new_secrets)
    assert len(result) == 1
    assert result[0]['name'] == 'alice'
    assert result[0]['credentials'][0]['accessKey'] == 'AK_A'
def test_build_combined_identities_anonymous_gets_empty_creds():
    diff = {
        'to_create': [{'name': 'anonymous', 'actions': []}],
        'to_update': [],
        'to_delete': [],
    }
    result = sw.seaweedfs_build_combined_identities(diff, [], {})
    assert result[0]['credentials'][0]['accessKey'] == ''
    assert result[0]['credentials'][0]['secretKey'] == ''
def test_build_combined_identities_update_preserves_creds():
    current = [{
        'name': 'alice',
        'credentials': [{'accessKey': 'EXISTING_AK', 'secretKey': 'EXISTING_SK'}],
        'actions': ['Old'],
    }]
    diff = {
        'to_create': [],
        'to_update': [{'name': 'alice', 'actions': ['New']}],
        'to_delete': [],
    }
    result = sw.seaweedfs_build_combined_identities(diff, current, {})
    assert result[0]['credentials'][0]['accessKey'] == 'EXISTING_AK'
    assert result[0]['actions'] == ['New']
def test_build_combined_json_serializes_deterministically():
    import json
    identities = [{
        'name': 'alice',
        'credentials': [{'accessKey': 'AK', 'secretKey': 'SK'}],
        'actions': [],
    }]
    result = sw.seaweedfs_build_combined_json(identities)
    parsed = json.loads(result)
    assert parsed['identities'][0]['name'] == 'alice'
    result2 = sw.seaweedfs_build_combined_json(identities)
    assert result == result2  # sort_keys determinism
# =============================================================================
# identity-distribute (Layer 3) filters
# =============================================================================
def test_compute_distribution_pairs_single_identity_multiple_paths():
    target = [{'name': 'alice', 'extra_vault_paths': ['path/X', 'path/Y']}]
    creds = {'alice': {'accessKey': 'AK_A', 'secretKey': 'SK_A'}}
    result = sw.seaweedfs_compute_distribution_pairs(target, creds)
    assert len(result) == 2
    assert result[0] == {'path': 'path/X', 'name': 'alice',
                         'accessKey': 'AK_A', 'secretKey': 'SK_A'}
    assert result[1]['path'] == 'path/Y'
def test_compute_distribution_pairs_multiple_identities():
    target = [
        {'name': 'alice', 'extra_vault_paths': ['path/A']},
        {'name': 'bob',   'extra_vault_paths': ['path/B1', 'path/B2']},
    ]
    creds = {
        'alice': {'accessKey': 'AK_A', 'secretKey': 'SK_A'},
        'bob':   {'accessKey': 'AK_B', 'secretKey': 'SK_B'},
    }
    result = sw.seaweedfs_compute_distribution_pairs(target, creds)
    assert len(result) == 3
def test_compute_distribution_pairs_missing_creds():
    target = [{'name': 'orphan', 'extra_vault_paths': ['path/X']}]
    result = sw.seaweedfs_compute_distribution_pairs(target, {})
    assert result[0]['accessKey'] == ''
    assert result[0]['secretKey'] == ''
def test_compute_state_paths_to_delete_empty_state():
    assert sw.seaweedfs_compute_state_paths_to_delete([], ['path/X']) == []
def test_compute_state_paths_to_delete_path_in_target_not_deleted():
    state = [{'identity_name': 'alice', 'vault_paths': ['path/X', 'path/Y']}]
    result = sw.seaweedfs_compute_state_paths_to_delete(state, ['path/X', 'path/Y'])
    assert result == []
def test_compute_state_paths_to_delete_orphan_paths():
    state = [{'identity_name': 'alice', 'vault_paths': ['path/X', 'path/STALE']}]
    result = sw.seaweedfs_compute_state_paths_to_delete(state, ['path/X'])
    assert result == [{'path': 'path/STALE', 'identity_name': 'alice'}]
def test_build_new_distribution_state_empty():
    assert sw.seaweedfs_build_new_distribution_state([]) == []
def test_build_new_distribution_state_with_paths():
    identities = [{'name': 'alice', 'extra_vault_paths': ['path/A', 'path/B']}]
    result = sw.seaweedfs_build_new_distribution_state(identities)
    assert result == [{'identity_name': 'alice', 'vault_paths': ['path/A', 'path/B']}]
def test_validate_target_paths_unique_ok():
    assert sw.seaweedfs_validate_target_paths_unique(['path/A', 'path/B']) is True
def test_validate_target_paths_unique_raises_on_duplicate():
    with pytest.raises(AnsibleFilterError, match='Duplicate'):
        sw.seaweedfs_validate_target_paths_unique(['path/A', 'path/A'])
def test_validate_anonymous_no_extra_paths_ok():
    identities = [{'name': 'alice', 'extra_vault_paths': ['p']}, {'name': 'anonymous'}]
    assert sw.seaweedfs_validate_anonymous_no_extra_paths(identities) is True
def test_validate_anonymous_no_extra_paths_raises_on_anonymous():
    identities = [{'name': 'anonymous', 'extra_vault_paths': ['p']}]
    with pytest.raises(AnsibleFilterError, match='Anonymous'):
        sw.seaweedfs_validate_anonymous_no_extra_paths(identities)
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

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
# bucket-sync (Layer 2) — seaweedfs_buckets_* (stateless)
# =============================================================================

def test_buckets_to_delete_orphan(sample_target_buckets, sample_configmap_state_buckets):
    """Happy: state has 'b_stale' not in target → returned for delete."""
    result = sw.seaweedfs_buckets_to_delete('', sample_target_buckets, sample_configmap_state_buckets)
    assert len(result) == 1
    assert result[0]['name'] == 'b_stale'


def test_buckets_to_delete_empty_state(sample_target_buckets):
    """Edge: no ConfigMap state → no deletes."""
    result = sw.seaweedfs_buckets_to_delete('', sample_target_buckets, '')
    assert result == []


def test_buckets_to_create_new(sample_target_buckets, sample_configmap_state_buckets):
    """Happy: target has 'b2' not in state → returned for create."""
    result = sw.seaweedfs_buckets_to_create('', sample_target_buckets, sample_configmap_state_buckets)
    names = [b['name'] for b in result]
    assert 'b2' in names


def test_buckets_to_create_empty_target():
    """Edge: empty target → no creates."""
    result = sw.seaweedfs_buckets_to_create('', [], '')
    assert result == []


def test_buckets_to_create_all_kept():
    """Edge: all target in state → no creates."""
    state = '[{"name": "b1", "replication": "001"}]'
    target = [{'name': 'b1', 'replication': '001'}]
    result = sw.seaweedfs_buckets_to_create('', target, state)
    assert result == []


def test_buckets_quotas_to_apply_enrich_size_mib(sample_target_buckets):
    """Happy: b1 has quota=1GiB → returned with size_mib=1024 field."""
    result = sw.seaweedfs_buckets_quotas_to_apply('', sample_target_buckets, '')
    assert len(result) == 1
    assert result[0]['name'] == 'b1'
    assert result[0]['quota']['size_mib'] == 1024
    assert result[0]['quota']['size'] == '1GiB'  # original preserved


def test_buckets_quotas_to_apply_disabled():
    """Edge: quota.enabled=False → size_mib=0."""
    target = [{'name': 'b1', 'replication': '001', 'quota': {'enabled': False}}]
    result = sw.seaweedfs_buckets_quotas_to_apply('', target, '')
    assert result[0]['quota']['size_mib'] == 0


def test_buckets_quotas_to_apply_no_quota():
    """Edge: target без quota field → not in result."""
    target = [{'name': 'b1', 'replication': '001'}]
    result = sw.seaweedfs_buckets_quotas_to_apply('', target, '')
    assert result == []


def test_buckets_quotas_to_apply_invalid_size_raises():
    """Edge: invalid quota.size unit → AnsibleFilterError."""
    target = [{'name': 'b1', 'replication': '001', 'quota': {'enabled': True, 'size': '100GB'}}]
    with pytest.raises(AnsibleFilterError, match='Unsupported'):
        sw.seaweedfs_buckets_quotas_to_apply('', target, '')


def test_buckets_new_state_json_serializes_target(sample_target_buckets):
    """Happy: target serialized to JSON string with sort_keys."""
    result = sw.seaweedfs_buckets_new_state_json('', sample_target_buckets, '')
    parsed = json.loads(result)
    assert len(parsed) == 2
    # determinism via sort_keys=True
    result2 = sw.seaweedfs_buckets_new_state_json('', sample_target_buckets, '')
    assert result == result2


def test_buckets_new_state_json_empty_target():
    """Edge: empty target → '[]' string."""
    result = sw.seaweedfs_buckets_new_state_json('', [], '')
    assert json.loads(result) == []
# =============================================================================
# bucket-sync (Layer 2) — replication + rack + dataCenter immutable settings (v8)
# =============================================================================

def test_buckets_validation_raises_on_missing_replication():
    """Edge: bucket без replication field → validation raise."""
    target = [{'name': 'b1'}]
    with pytest.raises(AnsibleFilterError, match='missing required .replication'):
        sw.seaweedfs_buckets_to_delete('', target, '')


def test_buckets_validation_raises_on_invalid_replication_format():
    """Edge: replication invalid format ("01", "abc", int 1) → validation raise."""
    for invalid in ['01', 'abc', '1', 1, '0011', '']:
        target = [{'name': 'b1', 'replication': invalid}]
        with pytest.raises(AnsibleFilterError, match='Invalid replication format'):
            sw.seaweedfs_buckets_to_delete('', target, '')


def test_buckets_validation_raises_on_non_string_rack():
    """Edge: rack present but not a non-empty string → validation raise."""
    for invalid in [123, '', None, ['workers']]:
        target = [{'name': 'b1', 'replication': '001', 'rack': invalid}]
        with pytest.raises(AnsibleFilterError, match="field 'rack' must be a non-empty string"):
            sw.seaweedfs_buckets_to_delete('', target, '')


def test_buckets_validation_raises_on_non_string_datacenter():
    """Edge: dataCenter present but not a non-empty string → validation raise."""
    target = [{'name': 'b1', 'replication': '001', 'dataCenter': 42}]
    with pytest.raises(AnsibleFilterError, match="field 'dataCenter' must be a non-empty string"):
        sw.seaweedfs_buckets_to_delete('', target, '')


def test_buckets_immutable_violations_rack_only():
    """Kept bucket с changed rack → 1 violation entry."""
    state = '[{"name": "b1", "replication": "001", "rack": "workers"}]'
    target = [{'name': 'b1', 'replication': '001', 'rack': 'managers'}]
    result = sw.seaweedfs_buckets_immutable_violations('', target, state)
    assert len(result) == 1
    assert result[0]['name'] == 'b1'
    assert result[0]['state_rack'] == 'workers'
    assert result[0]['target_rack'] == 'managers'


def test_buckets_immutable_violations_datacenter_only():
    """Kept bucket с changed dataCenter → 1 violation entry."""
    state = '[{"name": "b1", "replication": "001", "dataCenter": "dc1"}]'
    target = [{'name': 'b1', 'replication': '001', 'dataCenter': 'dc2'}]
    result = sw.seaweedfs_buckets_immutable_violations('', target, state)
    assert len(result) == 1
    assert result[0]['state_dataCenter'] == 'dc1'
    assert result[0]['target_dataCenter'] == 'dc2'


def test_buckets_immutable_violations_replication_only():
    """Happy: kept bucket с changed replication → 1 violation entry."""
    state = '[{"name": "b1", "replication": "001"}]'
    target = [{'name': 'b1', 'replication': '100'}]
    result = sw.seaweedfs_buckets_immutable_violations('', target, state)
    assert len(result) == 1
    assert result[0]['state_replication'] == '001'
    assert result[0]['target_replication'] == '100'


def test_buckets_immutable_violations_all_changed():
    """Kept bucket с replication+rack+dataCenter changed → single unified violation."""
    state = '[{"name": "b1", "replication": "001", "rack": "workers", "dataCenter": "dc1"}]'
    target = [{'name': 'b1', 'replication': '100', 'rack': 'managers', 'dataCenter': 'dc2'}]
    result = sw.seaweedfs_buckets_immutable_violations('', target, state)
    assert len(result) == 1
    assert result[0]['target_replication'] == '100'
    assert result[0]['target_rack'] == 'managers'
    assert result[0]['target_dataCenter'] == 'dc2'


def test_buckets_immutable_violations_no_change():
    """Edge: оба unchanged → empty list."""
    state = '[{"name": "b1", "replication": "001"}]'
    target = [{'name': 'b1', 'replication': '001'}]
    result = sw.seaweedfs_buckets_immutable_violations('', target, state)
    assert result == []


def test_buckets_immutable_violations_new_bucket_no_violation():
    """Edge: new bucket (not in state) → не в violations list. Идёт через Phase C2."""
    state = '[]'
    target = [{'name': 'b1', 'replication': '001'}]
    result = sw.seaweedfs_buckets_immutable_violations('', target, state)
    assert result == []
# =============================================================================
# managed policy sync (Layer P) — seaweedfs_policies_* (stateless)
# =============================================================================

def test_policies_to_put_returns_all_target(sample_managed_policies, sample_configmap_state_policies):
    """Happy: put-all — returns every target policy (idempotent overwrite)."""
    result = sw.seaweedfs_policies_to_put('', sample_managed_policies, sample_configmap_state_policies)
    names = [p['name'] for p in result]
    assert names == ['gitlab-rw', 'loki-rw']


def test_policies_to_put_empty_target():
    """Edge: empty target → empty put list."""
    result = sw.seaweedfs_policies_to_put('', [], '')
    assert result == []


def test_policies_to_delete_orphan(sample_managed_policies, sample_configmap_state_policies):
    """Happy: state has 'p_stale' not in target → returned for delete."""
    result = sw.seaweedfs_policies_to_delete('', sample_managed_policies, sample_configmap_state_policies)
    assert len(result) == 1
    assert result[0]['name'] == 'p_stale'


def test_policies_to_delete_empty_state(sample_managed_policies):
    """Edge: no ConfigMap state → no deletes."""
    result = sw.seaweedfs_policies_to_delete('', sample_managed_policies, '')
    assert result == []


def test_policies_new_state_json_serializes_target(sample_managed_policies):
    """Happy: target serialized to JSON with sort_keys (deterministic)."""
    result = sw.seaweedfs_policies_new_state_json('', sample_managed_policies, '')
    parsed = json.loads(result)
    assert len(parsed) == 2
    result2 = sw.seaweedfs_policies_new_state_json('', sample_managed_policies, '')
    assert result == result2


def test_policies_new_state_json_empty_target():
    """Edge: empty target → '[]' string."""
    result = sw.seaweedfs_policies_new_state_json('', [], '')
    assert json.loads(result) == []


def test_policies_validation_raises_on_missing_name():
    """Edge: policy без name → validation raise."""
    target = [{'document': {'Version': '2012-10-17'}}]
    with pytest.raises(AnsibleFilterError, match="missing required non-empty string 'name'"):
        sw.seaweedfs_policies_to_put('', target, '')


def test_policies_validation_raises_on_missing_document():
    """Edge: policy без document → validation raise."""
    target = [{'name': 'gitlab-rw'}]
    with pytest.raises(AnsibleFilterError, match="missing required non-empty mapping 'document'"):
        sw.seaweedfs_policies_to_put('', target, '')


def test_policies_validation_raises_on_non_dict_document():
    """Edge: document not a mapping → validation raise."""
    target = [{'name': 'gitlab-rw', 'document': 'not-a-dict'}]
    with pytest.raises(AnsibleFilterError, match="missing required non-empty mapping 'document'"):
        sw.seaweedfs_policies_to_put('', target, '')
# =============================================================================
# v14 additions — identity policy_names, identities_to_delete, bucket owners
# =============================================================================

def test_user_sync_full_includes_policy_names(sample_generate_params):
    """to_create + to_update identities carry policy_names (default [] if absent)."""
    target = [
        {'name': 'gitlab', 'actions': [], 'policy_names': ['gitlab-rw']},
        {'name': 'noattach', 'actions': []},
    ]
    result = sw.seaweedfs_user_sync_full('', target, **sample_generate_params)
    parsed = json.loads(result)
    by_name = {i['name']: i for i in parsed['identities']}
    assert by_name['gitlab']['policy_names'] == ['gitlab-rw']
    assert by_name['noattach']['policy_names'] == []


def test_identities_to_delete_orphans(sample_vault_json):
    """Vault has admin/alice/anonymous; target = [admin] → alice + anonymous to delete."""
    target = [{'name': 'admin', 'actions': ['Admin']}]
    result = sw.seaweedfs_identities_to_delete(sample_vault_json, target)
    assert set(result) == {'alice', 'anonymous'}


def test_identities_to_delete_empty_vault():
    """Edge: empty Vault → nothing to delete."""
    result = sw.seaweedfs_identities_to_delete('', [{'name': 'admin', 'actions': ['Admin']}])
    assert result == []


def test_buckets_owners_to_set_changed():
    """Kept bucket with changed owner → returned for s3.bucket.owner reconcile."""
    state = '[{"name": "b1", "replication": "001", "owner": "admin"}]'
    target = [{'name': 'b1', 'replication': '001', 'owner': 'gitlab'}]
    result = sw.seaweedfs_buckets_owners_to_set('', target, state)
    assert len(result) == 1
    assert result[0]['name'] == 'b1'
    assert result[0]['owner'] == 'gitlab'


def test_buckets_owners_to_set_unchanged():
    """Kept bucket with same owner → not returned."""
    state = '[{"name": "b1", "replication": "001", "owner": "gitlab"}]'
    target = [{'name': 'b1', 'replication': '001', 'owner': 'gitlab'}]
    result = sw.seaweedfs_buckets_owners_to_set('', target, state)
    assert result == []


def test_buckets_owners_to_set_new_bucket_excluded():
    """New bucket (not in state) → not in owners_to_set (gets owner at create)."""
    target = [{'name': 'b1', 'replication': '001', 'owner': 'gitlab'}]
    result = sw.seaweedfs_buckets_owners_to_set('', target, '')
    assert result == []

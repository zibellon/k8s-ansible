"""Pytest unit tests for filter_plugins/seaweedfs_distribute.py.

Layer 3 of make test runner — catches runtime Jinja2 issues that
syntax-check / ansible-lint / helm-validate don't see. Lives in
tests/python/; invoked via `make test-pytest` or direct `pytest
tests/python/`.
Shared fixtures + path setup — in tests/python/conftest.py.
"""
import json
import pytest
import seaweedfs_distribute as sw
from ansible.errors import AnsibleFilterError
# =============================================================================
# identity-distribute (Layer 3) — seaweedfs_distribute_* (stateless)
# =============================================================================

def test_distribute_paths_to_delete_orphan_path(sample_s3configure_raw, sample_configmap_state_distribute):
    """Happy: state key has stale path '.../old', target key has new path — old returned for delete."""
    target = [{'name': 'alice',
               'keys': [{'access_key': 'ALICE_AK', 'vault_paths': ['eso-secret/team/alice/new']}]}]
    result = sw.seaweedfs_distribute_paths_to_delete(
        sample_s3configure_raw, target, sample_configmap_state_distribute)
    assert result == [{'path': 'eso-secret/team/alice/old',
                       'identity_name': 'alice', 'access_key': 'ALICE_AK'}]


def test_distribute_paths_to_delete_empty_state(sample_s3configure_raw):
    """Edge: empty state (no ConfigMap yet) → empty deletes list."""
    target = [{'name': 'alice', 'keys': [{'access_key': 'ALICE_AK', 'vault_paths': ['p1']}]}]
    result = sw.seaweedfs_distribute_paths_to_delete(sample_s3configure_raw, target, '')
    assert result == []


def test_distribute_paths_to_delete_raises_on_anonymous_with_paths():
    """Edge: anonymous key with vault_paths → validation raise."""
    target = [{'name': 'anonymous', 'keys': [{'access_key': 'x', 'vault_paths': ['p1']}]}]
    with pytest.raises(AnsibleFilterError, match='Anonymous'):
        sw.seaweedfs_distribute_paths_to_delete('', target, '')


def test_distribute_paths_to_add_happy(sample_s3configure_raw):
    """Happy: alice key ALICE_AK with one path → one pair with that key's creds."""
    target = [{'name': 'alice',
               'keys': [{'access_key': 'ALICE_AK', 'vault_paths': ['eso-secret/team/alice/new']}]}]
    result = sw.seaweedfs_distribute_paths_to_add(sample_s3configure_raw, target, '')
    assert result == [{
        'path': 'eso-secret/team/alice/new',
        'name': 'alice',
        'accessKey': 'ALICE_AK',
        'secretKey': 'ALICE_SK',
    }]


def test_distribute_paths_to_add_multi_key(sample_s3configure_raw):
    """alice distributes BOTH keys → two pairs, distinct AK/SK, same name."""
    target = [{'name': 'alice', 'keys': [
        {'access_key': 'ALICE_AK', 'vault_paths': ['p/ak1']},
        {'access_key': 'ALICE_AK2', 'vault_paths': ['p/ak2']}]}]
    result = sw.seaweedfs_distribute_paths_to_add(sample_s3configure_raw, target, '')
    assert result == [
        {'path': 'p/ak1', 'name': 'alice', 'accessKey': 'ALICE_AK', 'secretKey': 'ALICE_SK'},
        {'path': 'p/ak2', 'name': 'alice', 'accessKey': 'ALICE_AK2', 'secretKey': 'ALICE_SK2'},
    ]


def test_distribute_paths_to_add_raises_on_missing_creds(sample_s3configure_raw):
    """Edge: key access_key not in the filer creds → validation raise."""
    target = [{'name': 'alice', 'keys': [{'access_key': 'NOPE_AK', 'vault_paths': ['p1']}]}]
    with pytest.raises(AnsibleFilterError, match='missing from the filer'):
        sw.seaweedfs_distribute_paths_to_add(sample_s3configure_raw, target, '')


def test_distribute_paths_to_add_raises_on_duplicate_paths(sample_s3configure_raw):
    """Edge: two keys sharing same path → paths-unique validation raise."""
    target = [{'name': 'admin', 'keys': [{'access_key': 'ADMIN_AK', 'vault_paths': ['shared/path']}]},
              {'name': 'alice', 'keys': [{'access_key': 'ALICE_AK', 'vault_paths': ['shared/path']}]}]
    with pytest.raises(AnsibleFilterError, match='Duplicate'):
        sw.seaweedfs_distribute_paths_to_add(sample_s3configure_raw, target, '')

# =============================================================================
# per-item ConfigMap state split — helpers
# =============================================================================
def test_parse_configmaplist_empty_and_malformed():
    assert sw._parse_configmaplist('') == []
    assert sw._parse_configmaplist(None) == []
    assert sw._parse_configmaplist('not json {') == []
    assert sw._parse_configmaplist('{}') == []
    assert sw._parse_configmaplist('{"items": "x"}') == []


def test_parse_configmaplist_returns_items(sample_configmaplist_raw):
    items = sw._parse_configmaplist(sample_configmaplist_raw)
    assert len(items) == 2
    assert items[0]['metadata']['name'] == 'seaweedfs-sync-buckets-b1'


def test_validate_configmap_name_valid():
    for name in ['seaweedfs-sync-buckets-b1', 'a', 'gitlab-registry',
                 'seaweedfs-sync-policies-gitlab-rw']:
        sw._validate_configmap_name(name)  # no raise


def test_validate_configmap_name_invalid_raises():
    for bad in ['', 'UpperCase', 'has_underscore', '-leadinghyphen',
                'trailinghyphen-', 'a' * 254]:
        with pytest.raises(AnsibleFilterError, match='not a valid RFC 1123'):
            sw._validate_configmap_name(bad)


def test_item_configmap_entry_structure():
    entry = sw._item_configmap_entry('seaweedfs-sync-buckets-', 'b1',
                                     {'name': 'b1', 'replication': '001'})
    assert entry['name'] == 'seaweedfs-sync-buckets-b1'
    assert json.loads(entry['content']) == {'name': 'b1', 'replication': '001'}
    assert sw._item_configmap_entry('p-', 'x', {'b': 1, 'a': 2})['content'] == '{"a": 2, "b": 1}'
# =============================================================================
# per-item ConfigMap state split — reconstruction + stale-delete (generic)
# =============================================================================
def test_state_configmaps_to_combined_json_happy(sample_configmaplist_raw):
    parsed = json.loads(sw.seaweedfs_state_configmaps_to_combined_json(sample_configmaplist_raw))
    assert isinstance(parsed, list) and len(parsed) == 2
    assert {b['name'] for b in parsed} == {'b1', 'b2'}
    b1 = next(b for b in parsed if b['name'] == 'b1')
    assert b1['owner'] == 'gitlab' and b1['replication'] == '001'


def test_state_configmaps_to_combined_json_empty_and_malformed():
    assert json.loads(sw.seaweedfs_state_configmaps_to_combined_json('')) == []
    assert json.loads(sw.seaweedfs_state_configmaps_to_combined_json(None)) == []
    assert json.loads(sw.seaweedfs_state_configmaps_to_combined_json('{"items": []}')) == []
    assert json.loads(sw.seaweedfs_state_configmaps_to_combined_json('garbage {')) == []


def test_state_configmaps_to_combined_json_skips_item_without_state():
    raw = json.dumps({'items': [
        {'metadata': {'name': 'cm-a'}, 'data': {'state': '{"name": "a"}'}},
        {'metadata': {'name': 'cm-b'}},
        {'metadata': {'name': 'cm-c'}, 'data': {}},
    ]})
    assert json.loads(sw.seaweedfs_state_configmaps_to_combined_json(raw)) == [{'name': 'a'}]


def test_state_configmaps_to_combined_json_determinism(sample_configmaplist_raw):
    r1 = sw.seaweedfs_state_configmaps_to_combined_json(sample_configmaplist_raw)
    r2 = sw.seaweedfs_state_configmaps_to_combined_json(sample_configmaplist_raw)
    assert r1 == r2


def test_state_configmaps_to_delete_orphan(sample_configmaplist_raw):
    result = sw.seaweedfs_state_configmaps_to_delete(
        sample_configmaplist_raw, ['seaweedfs-sync-buckets-b1'])
    assert result == ['seaweedfs-sync-buckets-b2']


def test_state_configmaps_to_delete_all_kept(sample_configmaplist_raw):
    result = sw.seaweedfs_state_configmaps_to_delete(
        sample_configmaplist_raw,
        ['seaweedfs-sync-buckets-b1', 'seaweedfs-sync-buckets-b2'])
    assert result == []


def test_state_configmaps_to_delete_empty_list():
    assert sw.seaweedfs_state_configmaps_to_delete('', ['x']) == []
    assert sw.seaweedfs_state_configmaps_to_delete('{"items": []}', ['x']) == []


def test_state_configmaps_to_delete_empty_target(sample_configmaplist_raw):
    result = sw.seaweedfs_state_configmaps_to_delete(sample_configmaplist_raw, [])
    assert set(result) == {'seaweedfs-sync-buckets-b1', 'seaweedfs-sync-buckets-b2'}
# =============================================================================
# per-item ConfigMap state split — per-group apply
# =============================================================================
def test_distribute_configmaps_to_apply_happy():
    target = [
        {'name': 'admin', 'actions': ['Admin'], 'keys': [{'access_key': 'a'}]},
        {'name': 'gitlab', 'keys': [
            {'access_key': 'gl-1', 'vault_paths': ['eso-secret/gitlab/s3', 'secret/x/y']},
            {'access_key': 'gl-2'}]},
    ]
    result = sw.seaweedfs_distribute_configmaps_to_apply(target)
    assert len(result) == 1
    assert result[0]['name'] == 'seaweedfs-sync-identity-distributions-gitlab'
    assert json.loads(result[0]['content']) == {
        'identity_name': 'gitlab',
        'keys': [{'access_key': 'gl-1', 'vault_paths': ['eso-secret/gitlab/s3', 'secret/x/y']}]}


def test_distribute_configmaps_to_apply_empty_and_no_paths():
    assert sw.seaweedfs_distribute_configmaps_to_apply([]) == []
    assert sw.seaweedfs_distribute_configmaps_to_apply(
        [{'name': 'bob', 'keys': [{'access_key': 'b'}]}]) == []


def test_distribute_configmaps_to_apply_anonymous_with_paths_raises():
    with pytest.raises(AnsibleFilterError, match='Anonymous'):
        sw.seaweedfs_distribute_configmaps_to_apply(
            [{'name': 'anonymous', 'keys': [{'access_key': 'x', 'vault_paths': ['p1']}]}])


def test_distribute_configmaps_to_apply_duplicate_paths_raises():
    target = [
        {'name': 'a', 'keys': [{'access_key': 'a1', 'vault_paths': ['shared/p']}]},
        {'name': 'b', 'keys': [{'access_key': 'b1', 'vault_paths': ['shared/p']}]},
    ]
    with pytest.raises(AnsibleFilterError, match='Duplicate'):
        sw.seaweedfs_distribute_configmaps_to_apply(target)

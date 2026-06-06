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
# identity-distribute (Layer 3) — seaweedfs_distribute_* (stateless)
# =============================================================================

def test_distribute_paths_to_delete_orphan_path(sample_s3configure_raw, sample_configmap_state_distribute):
    """Happy: state has stale path 'eso-secret/team/alice/old', target has new path — old returned for delete."""
    target = [{'name': 'alice', 'extra_vault_paths': ['eso-secret/team/alice/new']}]
    result = sw.seaweedfs_distribute_paths_to_delete(
        sample_s3configure_raw, target, sample_configmap_state_distribute)
    assert result == [{'path': 'eso-secret/team/alice/old', 'identity_name': 'alice'}]


def test_distribute_paths_to_delete_empty_state(sample_s3configure_raw):
    """Edge: empty state (no ConfigMap yet) → empty deletes list."""
    target = [{'name': 'alice', 'extra_vault_paths': ['p1']}]
    result = sw.seaweedfs_distribute_paths_to_delete(sample_s3configure_raw, target, '')
    assert result == []


def test_distribute_paths_to_delete_raises_on_anonymous_with_extra():
    """Edge: anonymous identity with extra_vault_paths → validation raise."""
    target = [{'name': 'anonymous', 'extra_vault_paths': ['p1']}]
    with pytest.raises(AnsibleFilterError, match='Anonymous'):
        sw.seaweedfs_distribute_paths_to_delete('', target, '')


def test_distribute_paths_to_add_happy(sample_s3configure_raw):
    """Happy: alice with one path → returns one pair with embedded creds."""
    target = [{'name': 'alice', 'extra_vault_paths': ['eso-secret/team/alice/new']}]
    result = sw.seaweedfs_distribute_paths_to_add(sample_s3configure_raw, target, '')
    assert result == [{
        'path': 'eso-secret/team/alice/new',
        'name': 'alice',
        'accessKey': 'ALICE_AK',
        'secretKey': 'ALICE_SK',
    }]


def test_distribute_paths_to_add_raises_on_missing_creds(sample_s3configure_raw):
    """Edge: identity 'orphan' not in combined JSON → validation raise."""
    target = [{'name': 'orphan', 'extra_vault_paths': ['p1']}]
    with pytest.raises(AnsibleFilterError, match='missing from the filer'):
        sw.seaweedfs_distribute_paths_to_add(sample_s3configure_raw, target, '')


def test_distribute_paths_to_add_raises_on_duplicate_paths(sample_s3configure_raw):
    """Edge: two identities sharing same path → paths-unique validation raise."""
    target = [
        {'name': 'admin', 'extra_vault_paths': ['shared/path']},
        {'name': 'alice', 'extra_vault_paths': ['shared/path']},
    ]
    with pytest.raises(AnsibleFilterError, match='Duplicate'):
        sw.seaweedfs_distribute_paths_to_add(sample_s3configure_raw, target, '')
# =============================================================================
# bucket-sync (Layer 2) — seaweedfs_buckets_* (stateless)
# =============================================================================

def _mk_filer(current):
    """Build (fs_configure_raw, bucket_list_raw) from current bucket dicts for v17 bucket tests.
    A bucket with no replication/rack/dataCenter gets NO fs.configure location (s3.bucket.list only)."""
    locs = []
    lines = []
    for b in current:
        loc = {'locationPrefix': '/buckets/' + b['name']}
        for k in ('replication', 'rack', 'dataCenter'):
            if k in b:
                loc[k] = b[k]
        if len(loc) > 1:
            locs.append(loc)
        owner = ('\towner:"' + b['owner'] + '"') if 'owner' in b else ''
        lines.append('  ' + b['name'] + '\tsize:0\tchunk:0' + owner)
    return json.dumps({'locations': locs}), '\n'.join(lines) + '\n'


def test_parse_fs_configure_locations_happy(sample_fs_configure_raw):
    locs = sw._parse_fs_configure_locations(sample_fs_configure_raw)
    assert set(locs.keys()) == {'b1', 'b2'}  # bare /buckets/ skipped
    assert locs['b1'] == {'replication': '001', 'rack': 'workers-1', 'dataCenter': 'dc-1'}
    assert 'rack' not in locs['b2'] and 'dataCenter' not in locs['b2']  # empty → absent


def test_parse_fs_configure_locations_empty_and_malformed():
    assert sw._parse_fs_configure_locations('') == {}
    assert sw._parse_fs_configure_locations(None) == {}
    assert sw._parse_fs_configure_locations('x{') == {}


def test_parse_s3_bucket_list_happy(sample_bucket_list_raw):
    bl = sw._parse_s3_bucket_list(sample_bucket_list_raw)
    assert bl['b1']['owner'] == 'gitlab' and bl['b_stale']['owner'] == 'admin'


def test_parse_s3_bucket_list_empty():
    assert sw._parse_s3_bucket_list('') == {} and sw._parse_s3_bucket_list(None) == {}


def test_merge_bucket_state(sample_fs_configure_raw, sample_bucket_list_raw):
    cur = sw._merge_bucket_state(sw._parse_fs_configure_locations(sample_fs_configure_raw),
                                 sw._parse_s3_bucket_list(sample_bucket_list_raw))
    by = {b['name']: b for b in cur}
    assert set(by) == {'b1', 'b2', 'b_stale'}            # existence = s3.bucket.list
    assert by['b1']['replication'] == '001' and by['b1']['owner'] == 'gitlab'
    assert 'replication' not in by['b_stale']             # in bucket.list, no fs.configure


def test_buckets_to_delete_orphan(sample_fs_configure_raw, sample_bucket_list_raw, sample_target_buckets):
    r = sw.seaweedfs_buckets_to_delete(sample_fs_configure_raw, sample_bucket_list_raw, sample_target_buckets)
    assert [b['name'] for b in r] == ['b_stale']


def test_buckets_to_delete_empty_filer(sample_target_buckets):
    assert sw.seaweedfs_buckets_to_delete('', '', sample_target_buckets) == []


def test_buckets_to_create_new(sample_target_buckets):
    fs, bl = _mk_filer([{'name': 'b1', 'replication': '001', 'rack': 'workers-1', 'dataCenter': 'dc-1', 'owner': 'gitlab'}])
    r = sw.seaweedfs_buckets_to_create(fs, bl, sample_target_buckets)
    assert [b['name'] for b in r] == ['b2']


def test_buckets_to_create_empty_target():
    assert sw.seaweedfs_buckets_to_create('', '', []) == []


def test_buckets_quotas_to_apply_all_buckets(sample_target_buckets):
    by = {b['name']: b for b in sw.seaweedfs_buckets_quotas_to_apply(sample_target_buckets)}
    assert by['b1']['_quota_op'] == 'set' and by['b1']['_quota_size_mib'] == 1024
    assert by['b2']['_quota_op'] == 'remove'


def test_buckets_quotas_to_apply_remove_when_absent():
    r = sw.seaweedfs_buckets_quotas_to_apply([{'name': 'b1', 'replication': '001'}])
    assert r[0]['_quota_op'] == 'remove' and r[0]['_quota_size_mib'] == 0


def test_buckets_quotas_to_apply_set_when_present():
    r = sw.seaweedfs_buckets_quotas_to_apply([{'name': 'b1', 'replication': '001', 'quota_size': '2GiB'}])
    assert r[0]['_quota_op'] == 'set' and r[0]['_quota_size_mib'] == 2048


def test_buckets_quotas_to_apply_invalid_unit_raises():
    with pytest.raises(AnsibleFilterError, match='quota_size'):
        sw.seaweedfs_buckets_quotas_to_apply([{'name': 'b1', 'replication': '001', 'quota_size': '100GB'}])


def test_buckets_quotas_to_apply_invalid_value_raises():
    with pytest.raises(AnsibleFilterError, match='quota_size'):
        sw.seaweedfs_buckets_quotas_to_apply([{'name': 'b1', 'replication': '001', 'quota_size': 'abcGiB'}])


def test_buckets_quotas_to_apply_zero_raises():
    with pytest.raises(AnsibleFilterError, match='quota_size'):
        sw.seaweedfs_buckets_quotas_to_apply([{'name': 'b1', 'replication': '001', 'quota_size': '0GiB'}])
# =============================================================================
# bucket-sync (Layer 2) — replication + rack + dataCenter immutable settings (v8)
# =============================================================================

def test_buckets_validation_raises_on_missing_replication():
    with pytest.raises(AnsibleFilterError, match='missing required .replication'):
        sw.seaweedfs_buckets_to_delete('', '', [{'name': 'b1'}])


def test_buckets_validation_raises_on_invalid_replication_format():
    for invalid in ['01', 'abc', '1', 1, '0011', '']:
        with pytest.raises(AnsibleFilterError, match='Invalid replication format'):
            sw.seaweedfs_buckets_to_delete('', '', [{'name': 'b1', 'replication': invalid}])


def test_buckets_validation_raises_on_non_string_rack():
    for invalid in [123, '', None, ['workers']]:
        with pytest.raises(AnsibleFilterError, match="field 'rack' must be a non-empty string"):
            sw.seaweedfs_buckets_to_delete('', '', [{'name': 'b1', 'replication': '001', 'rack': invalid}])


def test_buckets_validation_raises_on_non_string_datacenter():
    with pytest.raises(AnsibleFilterError, match="field 'dataCenter' must be a non-empty string"):
        sw.seaweedfs_buckets_to_delete('', '', [{'name': 'b1', 'replication': '001', 'dataCenter': 42}])


def test_buckets_immutable_violations_rack_changed():
    fs, bl = _mk_filer([{'name': 'b1', 'replication': '001', 'rack': 'workers-1'}])
    r = sw.seaweedfs_buckets_immutable_violations(fs, bl, [{'name': 'b1', 'replication': '001', 'rack': 'managers-1'}])
    assert len(r) == 1 and r[0]['name'] == 'b1'


def test_buckets_immutable_violations_replication_changed():
    fs, bl = _mk_filer([{'name': 'b1', 'replication': '001'}])
    r = sw.seaweedfs_buckets_immutable_violations(fs, bl, [{'name': 'b1', 'replication': '100'}])
    assert len(r) == 1 and r[0]['state_replication'] == '001' and r[0]['target_replication'] == '100'


def test_buckets_immutable_violations_no_change():
    fs, bl = _mk_filer([{'name': 'b1', 'replication': '001', 'rack': 'workers-1', 'dataCenter': 'dc-1'}])
    assert sw.seaweedfs_buckets_immutable_violations(fs, bl, [{'name': 'b1', 'replication': '001', 'rack': 'workers-1', 'dataCenter': 'dc-1'}]) == []


def test_buckets_immutable_violations_new_bucket_no_violation():
    assert sw.seaweedfs_buckets_immutable_violations('', '', [{'name': 'b1', 'replication': '001'}]) == []


def test_buckets_immutable_violations_no_fs_location_skipped():
    """v17 nuance: bucket in s3.bucket.list but no fs.configure location → replication absent →
    immutable check skipped (not false-flagged)."""
    fs, bl = _mk_filer([{'name': 'b1', 'owner': 'gitlab'}])  # no replication → no fs location
    assert sw.seaweedfs_buckets_immutable_violations(fs, bl, [{'name': 'b1', 'replication': '001', 'owner': 'gitlab'}]) == []
# =============================================================================
# managed policy sync (Layer P) — seaweedfs_policies_* (stateless)
# =============================================================================

def test_parse_s3_policy_list_happy(sample_s3policy_list_raw):
    parsed = sw._parse_s3_policy_list(sample_s3policy_list_raw)
    assert set(parsed.keys()) == {'gitlab-rw', 'p_stale'}
    assert parsed['gitlab-rw']['Version'] == '2012-10-17'


def test_parse_s3_policy_list_empty_and_malformed():
    assert sw._parse_s3_policy_list('') == {}
    assert sw._parse_s3_policy_list(None) == {}
    bad = sw._parse_s3_policy_list('Name: bad\nContent: {not json\n---\n')
    assert '__unparseable__' in bad['bad']


def test_policies_to_put_new_and_changed(sample_managed_policies, sample_s3policy_list_raw):
    # filer: gitlab-rw (same doc) + p_stale; target: gitlab-rw (same) + loki-rw (new)
    r = sw.seaweedfs_policies_to_put(sample_s3policy_list_raw, sample_managed_policies)
    assert [p['name'] for p in r] == ['loki-rw']  # gitlab-rw skipped (same doc), loki-rw new


def test_policies_to_put_semantic_key_order():
    target = [{'name': 'p1', 'document': {'Version': '2012-10-17', 'Statement': [
        {'Effect': 'Allow', 'Action': ['s3:*'], 'Resource': ['arn:aws:s3:::b']}]}}]
    filer = ('Name: p1\nContent: {"Statement": [{"Resource": ["arn:aws:s3:::b"], '
             '"Action": ["s3:*"], "Effect": "Allow"}], "Version": "2012-10-17"}\n---\n')
    assert sw.seaweedfs_policies_to_put(filer, target) == []  # reordered keys, same doc → not re-put


def test_policies_to_put_empty_target():
    assert sw.seaweedfs_policies_to_put('', []) == []


def test_policies_to_delete_orphan(sample_managed_policies, sample_s3policy_list_raw):
    r = sw.seaweedfs_policies_to_delete(sample_s3policy_list_raw, sample_managed_policies)
    assert [d['name'] for d in r] == ['p_stale']


def test_policies_to_delete_empty_filer(sample_managed_policies):
    assert sw.seaweedfs_policies_to_delete('', sample_managed_policies) == []


def test_policies_validation_raises_on_missing_name():
    with pytest.raises(AnsibleFilterError, match="missing required non-empty string 'name'"):
        sw.seaweedfs_policies_to_put('', [{'document': {'Version': '2012-10-17'}}])


def test_policies_validation_raises_on_missing_document():
    with pytest.raises(AnsibleFilterError, match="missing required non-empty mapping 'document'"):
        sw.seaweedfs_policies_to_put('', [{'name': 'gitlab-rw'}])


def test_policies_validation_raises_on_non_dict_document():
    with pytest.raises(AnsibleFilterError, match="missing required non-empty mapping 'document'"):
        sw.seaweedfs_policies_to_put('', [{'name': 'gitlab-rw', 'document': 'not-a-dict'}])


def test_policies_validation_raises_on_single_quote_in_document():
    target = [{'name': 'gitlab-rw', 'document': {'Statement': [{'Sid': "it's-bad"}]}}]
    with pytest.raises(AnsibleFilterError, match='single quote'):
        sw.seaweedfs_policies_to_put('', target)
# =============================================================================
# v14 additions — identity policy_names, identities_to_delete, bucket owners
# =============================================================================

def test_parse_s3_configure_identities_happy(sample_s3configure_raw):
    parsed = sw._parse_s3_configure_identities(sample_s3configure_raw)
    assert [i['name'] for i in parsed] == ['admin', 'alice', 'anonymous']  # static-id filtered
    alice = next(i for i in parsed if i['name'] == 'alice')
    assert alice['policyNames'] == ['team-alpha-rw'] and alice['accessKey'] == 'ALICE_AK'


def test_parse_s3_configure_identities_empty_and_malformed():
    assert sw._parse_s3_configure_identities('') == []
    assert sw._parse_s3_configure_identities(None) == []
    assert sw._parse_s3_configure_identities('garbage {') == []
    assert sw._parse_s3_configure_identities('[]') == []


def test_parse_s3_configure_identities_anonymous_empty_creds(sample_s3configure_raw):
    anon = next(i for i in sw._parse_s3_configure_identities(sample_s3configure_raw) if i['name'] == 'anonymous')
    assert anon['accessKey'] == '' and anon['secretKey'] == ''


_GEN = dict(access_key_length=20, secret_key_length=40,
            access_key_charset='ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789',
            secret_key_charset='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789')


def test_identity_actions_to_apply_new_generates_creds(sample_s3configure_raw):
    target = [{'name': 'bob', 'actions': [], 'policy_names': ['team-alpha-rw']}]
    r = sw.seaweedfs_identity_actions_to_apply(sample_s3configure_raw, target, **_GEN)
    assert len(r[0]['accessKey']) == 20 and len(r[0]['secretKey']) == 40
    assert r[0]['policies_attach'] == ['team-alpha-rw']


def test_identity_actions_to_apply_existing_preserves_and_diffs(sample_s3configure_raw):
    target = [{'name': 'alice', 'actions': [], 'policy_names': ['team-alpha-rw', 'extra-rw']}]
    r = sw.seaweedfs_identity_actions_to_apply(sample_s3configure_raw, target, **_GEN)
    assert r[0]['accessKey'] == 'ALICE_AK'              # preserved
    assert r[0]['policies_attach'] == ['extra-rw']      # only the new one


def test_identity_actions_to_apply_anonymous_no_creds(sample_s3configure_raw):
    target = [{'name': 'anonymous', 'actions': [], 'policy_names': ['pub-read']}]
    r = sw.seaweedfs_identity_actions_to_apply(sample_s3configure_raw, target, **_GEN)
    assert r[0]['accessKey'] == '' and r[0]['secretKey'] == ''
    assert r[0]['policies_attach'] == ['pub-read']


def test_identity_actions_to_apply_idempotent(sample_s3configure_raw):
    target = [{'name': 'alice', 'actions': [], 'policy_names': ['team-alpha-rw']}]
    r = sw.seaweedfs_identity_actions_to_apply(sample_s3configure_raw, target, **_GEN)
    assert r[0]['actions_add'] == [] and r[0]['policies_attach'] == [] and r[0]['accessKey'] == 'ALICE_AK'


def test_identity_actions_to_remove_detaches_policy(sample_s3configure_raw):
    """The v17 bugfix: alice had team-alpha-rw, target removes it → policies_detach."""
    target = [{'name': 'alice', 'actions': [], 'policy_names': []}]
    r = sw.seaweedfs_identity_actions_to_remove(sample_s3configure_raw, target)
    assert r == [{'name': 'alice', 'actions_remove': [], 'policies_detach': ['team-alpha-rw']}]


def test_identity_actions_to_remove_idempotent(sample_s3configure_raw):
    target = [{'name': 'alice', 'actions': [], 'policy_names': ['team-alpha-rw']}]
    assert sw.seaweedfs_identity_actions_to_remove(sample_s3configure_raw, target) == []


def test_identity_actions_to_remove_omits_empty(sample_s3configure_raw):
    """admin (no removals) must NOT appear (bare -delete would delete the user)."""
    target = [{'name': 'admin', 'actions': ['Admin'], 'policy_names': []}]
    r = sw.seaweedfs_identity_actions_to_remove(sample_s3configure_raw, target)
    assert all(e['name'] != 'admin' for e in r)


def test_identities_to_delete_orphans(sample_s3configure_raw):
    target = [{'name': 'admin', 'actions': ['Admin']}]
    assert set(sw.seaweedfs_identities_to_delete(sample_s3configure_raw, target)) == {'alice', 'anonymous'}


def test_identities_to_delete_empty():
    assert sw.seaweedfs_identities_to_delete('', [{'name': 'admin', 'actions': ['Admin']}]) == []


def test_buckets_owners_to_set_changed():
    fs, bl = _mk_filer([{'name': 'b1', 'replication': '001', 'owner': 'admin'}])
    r = sw.seaweedfs_buckets_owners_to_set(fs, bl, [{'name': 'b1', 'replication': '001', 'owner': 'gitlab'}])
    assert len(r) == 1 and r[0]['name'] == 'b1' and r[0]['owner'] == 'gitlab'


def test_buckets_owners_to_set_unchanged():
    fs, bl = _mk_filer([{'name': 'b1', 'replication': '001', 'owner': 'gitlab'}])
    assert sw.seaweedfs_buckets_owners_to_set(fs, bl, [{'name': 'b1', 'replication': '001', 'owner': 'gitlab'}]) == []


def test_buckets_owners_to_set_new_bucket_excluded():
    assert sw.seaweedfs_buckets_owners_to_set('', '', [{'name': 'b1', 'replication': '001', 'owner': 'gitlab'}]) == []
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
        {'name': 'admin', 'actions': ['Admin']},
        {'name': 'gitlab', 'extra_vault_paths': ['eso-secret/gitlab/s3', 'secret/x/y']},
    ]
    result = sw.seaweedfs_distribute_configmaps_to_apply(target)
    assert len(result) == 1
    assert result[0]['name'] == 'seaweedfs-sync-identity-distributions-gitlab'
    assert json.loads(result[0]['content']) == {
        'identity_name': 'gitlab', 'vault_paths': ['eso-secret/gitlab/s3', 'secret/x/y']}


def test_distribute_configmaps_to_apply_empty_and_no_paths():
    assert sw.seaweedfs_distribute_configmaps_to_apply([]) == []
    assert sw.seaweedfs_distribute_configmaps_to_apply([{'name': 'bob', 'actions': []}]) == []


def test_distribute_configmaps_to_apply_anonymous_with_paths_raises():
    with pytest.raises(AnsibleFilterError, match='Anonymous'):
        sw.seaweedfs_distribute_configmaps_to_apply([{'name': 'anonymous', 'extra_vault_paths': ['p1']}])


def test_distribute_configmaps_to_apply_duplicate_paths_raises():
    target = [
        {'name': 'a', 'extra_vault_paths': ['shared/p']},
        {'name': 'b', 'extra_vault_paths': ['shared/p']},
    ]
    with pytest.raises(AnsibleFilterError, match='Duplicate'):
        sw.seaweedfs_distribute_configmaps_to_apply(target)

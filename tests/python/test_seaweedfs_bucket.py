"""Pytest unit tests for filter_plugins/seaweedfs_bucket.py (Layer 2, v18 split).

Part of the make test runner (Layer 3). Shared fixtures + path setup in
tests/python/conftest.py.
"""
import json
import pytest
import seaweedfs_bucket as sw
from ansible.errors import AnsibleFilterError


def _mk_filer(current):
    """Build (fs_configure_raw, bucket_list_raw) from current bucket dicts.
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
        quota = ('\tquota:%d\tusage:0.00%%' % b['quota_bytes']) if b.get('quota_bytes') else ''
        owner = ('\towner:"' + b['owner'] + '"') if 'owner' in b else ''
        lines.append('  ' + b['name'] + '\tsize:0\tchunk:0' + quota + owner)
    return json.dumps({'locations': locs}), '\n'.join(lines) + '\n'


# --- parsers (unchanged from v17) ---
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
    assert set(by) == {'b1', 'b2', 'b_stale'}
    assert by['b1']['replication'] == '001' and by['b1']['owner'] == 'gitlab'
    assert 'replication' not in by['b_stale']


# --- delete / create ---
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


# --- quota split ---
def test_buckets_quota_to_upsert(sample_target_buckets):
    r = sw.seaweedfs_buckets_quota_to_upsert('', '', sample_target_buckets)
    assert r == [{'name': 'b1', '_quota_size_mib': 1024}]  # only b1 has quota_size


def test_buckets_quota_to_delete_empty_filer(sample_target_buckets):
    # '' filer => no current quotas => nothing to remove (b2 has no quota_size AND no filer quota)
    assert sw.seaweedfs_buckets_quota_to_delete('', '', sample_target_buckets) == []


def test_buckets_quota_to_upsert_size_conversion():
    target = [{'name': 'b1', 'replication': '001', 'rack': 'workers-1', 'dataCenter': 'dc-1', 'owner': 'gitlab', 'quota_size': '2GiB'}]
    assert sw.seaweedfs_buckets_quota_to_upsert('', '', target)[0]['_quota_size_mib'] == 2048


def test_buckets_quota_to_upsert_invalid_unit_raises():
    target = [{'name': 'b1', 'replication': '001', 'rack': 'r', 'dataCenter': 'd', 'owner': 'o', 'quota_size': '100GB'}]
    with pytest.raises(AnsibleFilterError, match='quota_size'):
        sw.seaweedfs_buckets_quota_to_upsert('', '', target)


def test_buckets_quota_to_upsert_zero_raises():
    target = [{'name': 'b1', 'replication': '001', 'rack': 'r', 'dataCenter': 'd', 'owner': 'o', 'quota_size': '0GiB'}]
    with pytest.raises(AnsibleFilterError, match='quota_size'):
        sw.seaweedfs_buckets_quota_to_upsert('', '', target)


def test_buckets_quota_to_upsert_unchanged_skipped(sample_target_buckets):
    # filer already has b1 with 1GiB == target b1 quota_size '1GiB' => no-op (skipped)
    fs, bl = _mk_filer([
        {'name': 'b1', 'replication': '001', 'rack': 'workers-1', 'dataCenter': 'dc-1', 'owner': 'gitlab',
         'quota_bytes': 1024 * 1024 * 1024},
        {'name': 'b2', 'replication': '001', 'rack': 'workers-1', 'dataCenter': 'dc-1', 'owner': 'loki'},
    ])
    assert sw.seaweedfs_buckets_quota_to_upsert(fs, bl, sample_target_buckets) == []


def test_buckets_quota_to_upsert_changed_emitted():
    # filer has b1 with 1GiB, target wants 2GiB => upsert b1 with new size
    fs, bl = _mk_filer([
        {'name': 'b1', 'replication': '001', 'rack': 'workers-1', 'dataCenter': 'dc-1', 'owner': 'gitlab',
         'quota_bytes': 1024 * 1024 * 1024},
    ])
    target = [{'name': 'b1', 'replication': '001', 'rack': 'workers-1', 'dataCenter': 'dc-1', 'owner': 'gitlab',
               'quota_size': '2GiB'}]
    assert sw.seaweedfs_buckets_quota_to_upsert(fs, bl, target) == [{'name': 'b1', '_quota_size_mib': 2048}]


def test_buckets_quota_to_delete_filer_has_quota():
    # b2 currently has a quota in filer, target b2 has NO quota_size => remove
    fs, bl = _mk_filer([
        {'name': 'b2', 'replication': '001', 'rack': 'workers-1', 'dataCenter': 'dc-1', 'owner': 'loki',
         'quota_bytes': 1024 * 1024 * 1024},
    ])
    target = [{'name': 'b2', 'replication': '001', 'rack': 'workers-1', 'dataCenter': 'dc-1', 'owner': 'loki'}]
    assert [b['name'] for b in sw.seaweedfs_buckets_quota_to_delete(fs, bl, target)] == ['b2']


def test_buckets_quota_to_delete_filer_no_quota_skipped():
    # b2 has NO quota in filer, target b2 has NO quota_size => no-op (skipped)
    fs, bl = _mk_filer([
        {'name': 'b2', 'replication': '001', 'rack': 'workers-1', 'dataCenter': 'dc-1', 'owner': 'loki'},
    ])
    target = [{'name': 'b2', 'replication': '001', 'rack': 'workers-1', 'dataCenter': 'dc-1', 'owner': 'loki'}]
    assert sw.seaweedfs_buckets_quota_to_delete(fs, bl, target) == []


def test_parse_s3_bucket_list_quota():
    # real s3.bucket.list line with quota field (bytes), quota before owner
    bl = sw._parse_s3_bucket_list('  test-bucket-1\tsize:0\tchunk:0\tquota:10485760\tusage:0.00%\towner:"test-user-1"\n')
    assert bl['test-bucket-1']['quota_bytes'] == 10485760
    assert bl['test-bucket-1']['owner'] == 'test-user-1'


def test_merge_bucket_state_carries_quota():
    fs, bl = _mk_filer([{'name': 'b1', 'replication': '001', 'rack': 'workers-1', 'dataCenter': 'dc-1',
                         'owner': 'gitlab', 'quota_bytes': 10485760}])
    cur = sw._merge_bucket_state(sw._parse_fs_configure_locations(fs), sw._parse_s3_bucket_list(bl))
    by = {b['name']: b for b in cur}
    assert by['b1']['quota_bytes'] == 10485760


# --- validation (v18: replication + rack + dataCenter + owner all required) ---
def test_buckets_validation_raises_on_missing_replication():
    with pytest.raises(AnsibleFilterError, match='missing required .replication'):
        sw.seaweedfs_buckets_to_delete('', '', [{'name': 'b1'}])


def test_buckets_validation_raises_on_invalid_replication_format():
    for invalid in ['01', 'abc', '1', 1, '0011', '']:
        with pytest.raises(AnsibleFilterError, match='Invalid replication format'):
            sw.seaweedfs_buckets_to_delete('', '', [{'name': 'b1', 'replication': invalid}])


def test_buckets_validation_raises_on_missing_rack():
    with pytest.raises(AnsibleFilterError, match="field 'rack' is REQUIRED"):
        sw.seaweedfs_buckets_to_delete('', '', [{'name': 'b1', 'replication': '001', 'dataCenter': 'dc-1', 'owner': 'o'}])


def test_buckets_validation_raises_on_missing_datacenter():
    with pytest.raises(AnsibleFilterError, match="field 'dataCenter' is REQUIRED"):
        sw.seaweedfs_buckets_to_delete('', '', [{'name': 'b1', 'replication': '001', 'rack': 'workers-1', 'owner': 'o'}])


def test_buckets_validation_raises_on_invalid_rack():
    for invalid in [123, '', None]:
        with pytest.raises(AnsibleFilterError, match="field 'rack' is REQUIRED"):
            sw.seaweedfs_buckets_to_delete('', '', [{'name': 'b1', 'replication': '001', 'rack': invalid, 'dataCenter': 'dc-1', 'owner': 'o'}])


def test_buckets_validation_raises_on_missing_owner():
    with pytest.raises(AnsibleFilterError, match="field 'owner' is REQUIRED"):
        sw.seaweedfs_buckets_to_delete('', '', [{'name': 'b1', 'replication': '001', 'rack': 'workers-1', 'dataCenter': 'dc-1'}])


# --- immutable violations (v18: owner + replication + rack + dataCenter) ---
def test_buckets_immutable_violations_rack_changed():
    fs, bl = _mk_filer([{'name': 'b1', 'replication': '001', 'rack': 'workers-1', 'dataCenter': 'dc-1', 'owner': 'gitlab'}])
    r = sw.seaweedfs_buckets_immutable_violations(fs, bl, [{'name': 'b1', 'replication': '001', 'rack': 'managers-1', 'dataCenter': 'dc-1', 'owner': 'gitlab'}])
    assert len(r) == 1 and r[0]['name'] == 'b1'


def test_buckets_immutable_violations_replication_changed():
    fs, bl = _mk_filer([{'name': 'b1', 'replication': '001', 'rack': 'workers-1', 'dataCenter': 'dc-1', 'owner': 'gitlab'}])
    r = sw.seaweedfs_buckets_immutable_violations(fs, bl, [{'name': 'b1', 'replication': '100', 'rack': 'workers-1', 'dataCenter': 'dc-1', 'owner': 'gitlab'}])
    assert len(r) == 1 and r[0]['state_replication'] == '001' and r[0]['target_replication'] == '100'


def test_buckets_immutable_violations_owner_changed():
    fs, bl = _mk_filer([{'name': 'b1', 'replication': '001', 'rack': 'workers-1', 'dataCenter': 'dc-1', 'owner': 'admin'}])
    r = sw.seaweedfs_buckets_immutable_violations(fs, bl, [{'name': 'b1', 'replication': '001', 'rack': 'workers-1', 'dataCenter': 'dc-1', 'owner': 'gitlab'}])
    assert len(r) == 1 and r[0]['state_owner'] == 'admin' and r[0]['target_owner'] == 'gitlab'


def test_buckets_immutable_violations_no_change():
    fs, bl = _mk_filer([{'name': 'b1', 'replication': '001', 'rack': 'workers-1', 'dataCenter': 'dc-1', 'owner': 'gitlab'}])
    assert sw.seaweedfs_buckets_immutable_violations(fs, bl, [{'name': 'b1', 'replication': '001', 'rack': 'workers-1', 'dataCenter': 'dc-1', 'owner': 'gitlab'}]) == []


def test_buckets_immutable_violations_new_bucket_no_violation():
    assert sw.seaweedfs_buckets_immutable_violations('', '', [{'name': 'b1', 'replication': '001', 'rack': 'workers-1', 'dataCenter': 'dc-1', 'owner': 'gitlab'}]) == []


def test_buckets_immutable_violations_no_fs_location_skipped():
    """Bucket in s3.bucket.list but no fs.configure location → replication absent in state →
    immutable check skipped (not false-flagged). Phase C will set the location."""
    fs, bl = _mk_filer([{'name': 'b1', 'owner': 'gitlab'}])  # no replication → no fs location
    assert sw.seaweedfs_buckets_immutable_violations(fs, bl, [{'name': 'b1', 'replication': '001', 'rack': 'workers-1', 'dataCenter': 'dc-1', 'owner': 'gitlab'}]) == []

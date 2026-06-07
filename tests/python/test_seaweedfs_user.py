"""Pytest unit tests for filter_plugins/seaweedfs_user.py (Layer 1, v18 split).

Part of the make test runner (Layer 3). Shared fixtures + path setup in
tests/python/conftest.py.
"""
import pytest
import seaweedfs_user as sw
from ansible.errors import AnsibleFilterError

_SK = dict(secret_key_length=40,
           secret_key_charset='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789')


def test_parse_s3_configure_identities_happy(sample_s3configure_raw):
    parsed = sw._parse_s3_configure_identities(sample_s3configure_raw)
    assert [i['name'] for i in parsed] == ['admin', 'alice', 'anonymous']  # static-id filtered
    alice = next(i for i in parsed if i['name'] == 'alice')
    assert alice['access_keys'] == ['ALICE_AK', 'ALICE_AK2']  # ALL creds, order preserved
    assert alice['policyNames'] == ['team-alpha-rw']
    assert 'secretKey' not in alice  # Layer 1 never needs the secret


def test_parse_s3_configure_identities_single_key(sample_s3configure_raw):
    admin = next(i for i in sw._parse_s3_configure_identities(sample_s3configure_raw)
                 if i['name'] == 'admin')
    assert admin['access_keys'] == ['ADMIN_AK'] and admin['actions'] == ['Admin']


def test_parse_s3_configure_identities_empty_and_malformed():
    assert sw._parse_s3_configure_identities('') == []
    assert sw._parse_s3_configure_identities(None) == []
    assert sw._parse_s3_configure_identities('garbage {') == []
    assert sw._parse_s3_configure_identities('[]') == []


def test_parse_s3_configure_identities_anonymous_empty_creds(sample_s3configure_raw):
    anon = next(i for i in sw._parse_s3_configure_identities(sample_s3configure_raw)
                if i['name'] == 'anonymous')
    assert anon['access_keys'] == []


def test_identities_to_create_new_uses_first_key(sample_s3configure_raw):
    target = [{'name': 'bob', 'actions': [], 'policy_names': ['team-alpha-rw'],
               'keys': [{'access_key': 'bob-key-1'}, {'access_key': 'bob-key-2'}]}]
    r = sw.seaweedfs_identities_to_create(sample_s3configure_raw, target, **_SK)
    assert [c['name'] for c in r] == ['bob']
    assert r[0]['accessKey'] == 'bob-key-1'  # keys[0], operator-chosen (NOT random)
    assert len(r[0]['secretKey']) == 40
    assert r[0]['policy_names'] == ['team-alpha-rw'] and r[0]['actions'] == []


def test_identities_to_create_skips_existing(sample_s3configure_raw):
    target = [{'name': 'alice', 'actions': [], 'policy_names': ['team-alpha-rw'],
               'keys': [{'access_key': 'ALICE_AK'}]}]
    assert sw.seaweedfs_identities_to_create(sample_s3configure_raw, target, **_SK) == []


def test_identities_to_create_anonymous_no_creds():
    target = [{'name': 'anonymous', 'actions': [], 'policy_names': ['pub-read']}]
    r = sw.seaweedfs_identities_to_create('', target, **_SK)  # empty filer → anonymous is new
    assert r[0]['name'] == 'anonymous' and r[0]['accessKey'] == '' and r[0]['secretKey'] == ''
    assert r[0]['policy_names'] == ['pub-read']


def test_identities_to_create_named_no_keys_raises():
    target = [{'name': 'bob', 'actions': [], 'policy_names': []}]
    with pytest.raises(AnsibleFilterError, match='no keys'):
        sw.seaweedfs_identities_to_create('', target, **_SK)


# --- keys_to_add (append new keys; no-rotation; brand-new excludes keys[0]) ---

def test_keys_to_add_existing_identity_adds_new_key(sample_s3configure_raw):
    """alice in filer with [ALICE_AK, ALICE_AK2]; target adds ALICE_AK3 → only AK3 added."""
    target = [{'name': 'alice', 'actions': [], 'policy_names': ['team-alpha-rw'],
               'keys': [{'access_key': 'ALICE_AK'}, {'access_key': 'ALICE_AK2'},
                        {'access_key': 'ALICE_AK3'}]}]
    r = sw.seaweedfs_keys_to_add(sample_s3configure_raw, target, **_SK)
    assert len(r) == 1 and r[0]['name'] == 'alice' and r[0]['accessKey'] == 'ALICE_AK3'
    assert len(r[0]['secretKey']) == 40


def test_keys_to_add_new_identity_excludes_first_key():
    """bob brand-new (empty filer), 2 keys → keys[0] excluded (to_create makes it), keys[1] added."""
    target = [{'name': 'bob', 'actions': [], 'policy_names': [],
               'keys': [{'access_key': 'bob-1'}, {'access_key': 'bob-2'}]}]
    r = sw.seaweedfs_keys_to_add('', target, **_SK)
    assert len(r) == 1 and r[0]['name'] == 'bob' and r[0]['accessKey'] == 'bob-2'


def test_keys_to_add_already_in_filer_skips(sample_s3configure_raw):
    """target == filer keys → nothing added (no-rotation)."""
    target = [{'name': 'alice', 'actions': [], 'policy_names': ['team-alpha-rw'],
               'keys': [{'access_key': 'ALICE_AK'}, {'access_key': 'ALICE_AK2'}]}]
    assert sw.seaweedfs_keys_to_add(sample_s3configure_raw, target, **_SK) == []


def test_keys_to_add_anonymous_skipped():
    target = [{'name': 'anonymous', 'actions': []}]
    assert sw.seaweedfs_keys_to_add('', target, **_SK) == []


def test_keys_to_add_global_dup_ak_raises():
    target = [{'name': 'bob', 'keys': [{'access_key': 'shared'}]},
              {'name': 'carol', 'keys': [{'access_key': 'shared'}]}]
    with pytest.raises(AnsibleFilterError, match='Duplicate access_key'):
        sw.seaweedfs_keys_to_add('', target, **_SK)


def test_keys_to_add_dup_ak_within_identity_raises():
    target = [{'name': 'bob', 'keys': [{'access_key': 'x'}, {'access_key': 'x'}]}]
    with pytest.raises(AnsibleFilterError, match='Duplicate access_key'):
        sw.seaweedfs_keys_to_add('', target, **_SK)


# --- keys_to_delete (prune filer-extra creds; identity-not-in-target handled elsewhere) ---

def test_keys_to_delete_filer_extra_key(sample_s3configure_raw):
    """filer alice [ALICE_AK, ALICE_AK2]; target keeps only ALICE_AK → ALICE_AK2 deleted."""
    target = [{'name': 'alice', 'actions': [], 'policy_names': ['team-alpha-rw'],
               'keys': [{'access_key': 'ALICE_AK'}]}]
    r = sw.seaweedfs_keys_to_delete(sample_s3configure_raw, target)
    assert r == [{'name': 'alice', 'accessKey': 'ALICE_AK2'}]


def test_keys_to_delete_identity_not_in_target_skipped(sample_s3configure_raw):
    """admin in filer but not in target → NOT pruned here (whole-identity delete handles it)."""
    target = [{'name': 'alice', 'actions': [], 'policy_names': ['team-alpha-rw'],
               'keys': [{'access_key': 'ALICE_AK'}, {'access_key': 'ALICE_AK2'}]}]
    r = sw.seaweedfs_keys_to_delete(sample_s3configure_raw, target)
    assert all(e['name'] != 'admin' for e in r) and r == []


def test_keys_to_delete_all_present_empty(sample_s3configure_raw):
    target = [{'name': 'alice', 'actions': [], 'policy_names': ['team-alpha-rw'],
               'keys': [{'access_key': 'ALICE_AK'}, {'access_key': 'ALICE_AK2'}]}]
    assert sw.seaweedfs_keys_to_delete(sample_s3configure_raw, target) == []


def test_identities_to_grant_adds_delta(sample_s3configure_raw):
    target = [{'name': 'alice', 'actions': [], 'policy_names': ['team-alpha-rw', 'extra-rw']}]
    r = sw.seaweedfs_identities_to_grant(sample_s3configure_raw, target)
    assert r == [{'name': 'alice', 'actions_add': [], 'policies_add': ['extra-rw']}]


def test_identities_to_grant_idempotent_omits_noop(sample_s3configure_raw):
    target = [{'name': 'alice', 'actions': [], 'policy_names': ['team-alpha-rw']}]
    assert sw.seaweedfs_identities_to_grant(sample_s3configure_raw, target) == []


def test_identities_to_revoke_detaches_policy(sample_s3configure_raw):
    """alice had team-alpha-rw; target removes it → revoke (drift heal)."""
    target = [{'name': 'alice', 'actions': [], 'policy_names': []}]
    r = sw.seaweedfs_identities_to_revoke(sample_s3configure_raw, target)
    assert r == [{'name': 'alice', 'actions_remove': [], 'policies_remove': ['team-alpha-rw']}]


def test_identities_to_revoke_idempotent(sample_s3configure_raw):
    target = [{'name': 'alice', 'actions': [], 'policy_names': ['team-alpha-rw']}]
    assert sw.seaweedfs_identities_to_revoke(sample_s3configure_raw, target) == []


def test_identities_to_revoke_omits_empty_bare_delete_guard(sample_s3configure_raw):
    """admin (no removals) must NOT appear (bare -delete would delete the whole identity)."""
    target = [{'name': 'admin', 'actions': ['Admin'], 'policy_names': []}]
    r = sw.seaweedfs_identities_to_revoke(sample_s3configure_raw, target)
    assert all(e['name'] != 'admin' for e in r)


def test_identities_to_delete_orphans(sample_s3configure_raw):
    target = [{'name': 'admin', 'actions': ['Admin']}]
    assert set(sw.seaweedfs_identities_to_delete(sample_s3configure_raw, target)) == {'alice', 'anonymous'}


def test_identities_to_delete_empty():
    assert sw.seaweedfs_identities_to_delete('', [{'name': 'admin', 'actions': ['Admin']}]) == []

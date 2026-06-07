"""Pytest unit tests for filter_plugins/seaweedfs_user.py (Layer 1, v18 split).

Part of the make test runner (Layer 3). Shared fixtures + path setup in
tests/python/conftest.py.
"""
import pytest
import seaweedfs_user as sw
from ansible.errors import AnsibleFilterError

_GEN = dict(access_key_length=20, secret_key_length=40,
            access_key_charset='ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789',
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


def test_identities_to_create_new_generates_creds(sample_s3configure_raw):
    target = [{'name': 'bob', 'actions': [], 'policy_names': ['team-alpha-rw']}]
    r = sw.seaweedfs_identities_to_create(sample_s3configure_raw, target, **_GEN)
    assert [c['name'] for c in r] == ['bob']
    assert len(r[0]['accessKey']) == 20 and len(r[0]['secretKey']) == 40
    assert r[0]['policy_names'] == ['team-alpha-rw'] and r[0]['actions'] == []


def test_identities_to_create_skips_existing(sample_s3configure_raw):
    target = [{'name': 'alice', 'actions': [], 'policy_names': ['team-alpha-rw']}]
    assert sw.seaweedfs_identities_to_create(sample_s3configure_raw, target, **_GEN) == []


def test_identities_to_create_anonymous_no_creds():
    target = [{'name': 'anonymous', 'actions': [], 'policy_names': ['pub-read']}]
    r = sw.seaweedfs_identities_to_create('', target, **_GEN)  # empty filer → anonymous is new
    assert r[0]['name'] == 'anonymous' and r[0]['accessKey'] == '' and r[0]['secretKey'] == ''
    assert r[0]['policy_names'] == ['pub-read']


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

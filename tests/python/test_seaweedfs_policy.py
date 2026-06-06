"""Pytest unit tests for filter_plugins/seaweedfs_policy.py (Layer P, v18 split).

Part of the make test runner (Layer 3). Shared fixtures + path setup in
tests/python/conftest.py.
"""
import pytest
import seaweedfs_policy as sw
from ansible.errors import AnsibleFilterError


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

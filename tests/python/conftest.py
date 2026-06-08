"""Pytest shared fixtures + path setup for the filter_plugins/ unit tests.

Path setup: prepends repo-root filter_plugins/ to sys.path so the filter
modules are importable from any cwd. Auto-loaded by pytest для всех test files
in tests/python/.
"""
import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent / 'filter_plugins'))

import json
import pytest


@pytest.fixture
def sample_target_buckets():
    """Default target buckets: b1 with quota_size, b2 without (both с replication + rack + dataCenter + owner)."""
    return [
        {
            'name': 'b1',
            'replication': '001',
            'rack': 'workers-1',
            'dataCenter': 'dc-1',
            'owner': 'gitlab',
            'quota_size': '1GiB',
        },
        {'name': 'b2', 'replication': '001', 'rack': 'workers-1', 'dataCenter': 'dc-1', 'owner': 'loki'},
    ]


@pytest.fixture
def sample_configmap_state_distribute():
    """Sample reconstructed ConfigMap state for identity-distribute (Layer 3) tests —
    per-key shape: [{identity_name, keys: [{access_key, vault_paths}]}]."""
    return json.dumps([
        {'identity_name': 'alice',
         'keys': [{'access_key': 'ALICE_AK', 'vault_paths': ['eso-secret/team/alice/old']}]},
    ])


@pytest.fixture
def sample_managed_policies():
    """Default target managed policies: gitlab-rw + loki-rw."""
    return [
        {
            'name': 'gitlab-rw',
            'document': {
                'Version': '2012-10-17',
                'Statement': [
                    {'Effect': 'Allow', 'Action': ['s3:*'],
                     'Resource': ['arn:aws:s3:::gitlab-registry', 'arn:aws:s3:::gitlab-registry/*']},
                ],
            },
        },
        {
            'name': 'loki-rw',
            'document': {
                'Version': '2012-10-17',
                'Statement': [
                    {'Effect': 'Allow', 'Action': ['s3:*'],
                     'Resource': ['arn:aws:s3:::loki-logs', 'arn:aws:s3:::loki-logs/*']},
                ],
            },
        },
    ]


@pytest.fixture
def sample_s3configure_raw():
    """`s3.configure` (no-arg) protojson dump: admin (Admin + 1 cred), alice (policyNames +
    2 creds — multi-key), anonymous (empty creds), plus a static identity the parser must
    IGNORE. Shared by test_seaweedfs_user.py (parses ALL creds → access_keys) and
    test_seaweedfs_distribute.py (reads credentials[0]). alice's FIRST credential stays
    ALICE_AK/ALICE_SK so the distribute filter (unchanged in this SUB) keeps passing."""
    return json.dumps({
        'identities': [
            {'name': 'admin', 'credentials': [{'accessKey': 'ADMIN_AK', 'secretKey': 'ADMIN_SK', 'status': 'Active'}],
             'actions': ['Admin'], 'policyNames': [], 'isStatic': False},
            {'name': 'alice', 'credentials': [
                {'accessKey': 'ALICE_AK', 'secretKey': 'ALICE_SK', 'status': 'Active'},
                {'accessKey': 'ALICE_AK2', 'secretKey': 'ALICE_SK2', 'status': 'Active'}],
             'actions': [], 'policyNames': ['team-alpha-rw'], 'isStatic': False},
            {'name': 'anonymous', 'credentials': [], 'actions': [], 'policyNames': [], 'isStatic': False},
            {'name': 'static-id', 'credentials': [{'accessKey': 'S_AK', 'secretKey': 'S_SK'}],
             'actions': ['Admin'], 'policyNames': [], 'isStatic': True},
        ],
        'accounts': [], 'serviceAccounts': [], 'policies': [], 'groups': [],
    })


@pytest.fixture
def sample_s3policy_list_raw():
    """`s3.policy -list` plain text: gitlab-rw (document IDENTICAL to sample_managed_policies'
    gitlab-rw → diff skips it) + p_stale (not in target → diff deletes it)."""
    gitlab_doc = {'Version': '2012-10-17', 'Statement': [
        {'Effect': 'Allow', 'Action': ['s3:*'],
         'Resource': ['arn:aws:s3:::gitlab-registry', 'arn:aws:s3:::gitlab-registry/*']}]}
    stale_doc = {'Version': '2012-10-17', 'Statement': [
        {'Effect': 'Allow', 'Action': ['s3:GetObject'], 'Resource': ['arn:aws:s3:::old']}]}
    return ('Name: gitlab-rw\nContent: ' + json.dumps(gitlab_doc) + '\n---\n'
            + 'Name: p_stale\nContent: ' + json.dumps(stale_doc) + '\n---\n')


@pytest.fixture
def sample_fs_configure_raw():
    """`fs.configure` protojson: bare /buckets/ (skipped) + b1 (full rep/rack/dc) +
    b2 (empty rack/dc via EmitUnpopulated → parser drops to absent)."""
    return json.dumps({'version': 0, 'locations': [
        {'locationPrefix': '/buckets/', 'replication': '000', 'rack': '', 'dataCenter': ''},
        {'locationPrefix': '/buckets/b1', 'replication': '001', 'rack': 'workers-1', 'dataCenter': 'dc-1', 'readOnly': False},
        {'locationPrefix': '/buckets/b2', 'replication': '001', 'rack': '', 'dataCenter': '', 'readOnly': False},
    ]})


@pytest.fixture
def sample_bucket_list_raw():
    """`s3.bucket.list` plain text: b1 (owner gitlab), b2 (owner loki), b_stale (owner admin)."""
    return ('  b1\tsize:0\tchunk:0\towner:"gitlab"\n'
            '  b2\tsize:0\tchunk:0\towner:"loki"\n'
            '  b_stale\tsize:0\tchunk:0\towner:"admin"\n')


@pytest.fixture
def sample_configmaplist_raw():
    """Sample `kubectl get cm -l <label> -o json` stdout: List with 2 per-item
    state ConfigMaps (each .data.state = single-item JSON object string).
    Used by seaweedfs_state_configmaps_* tests."""
    return json.dumps({
        "apiVersion": "v1",
        "kind": "List",
        "items": [
            {
                "apiVersion": "v1", "kind": "ConfigMap",
                "metadata": {"name": "seaweedfs-sync-buckets-b1",
                             "labels": {"seaweedfs-sync-state": "buckets"}},
                "data": {"state": json.dumps({"name": "b1", "replication": "001", "owner": "gitlab"}, sort_keys=True)},
            },
            {
                "apiVersion": "v1", "kind": "ConfigMap",
                "metadata": {"name": "seaweedfs-sync-buckets-b2",
                             "labels": {"seaweedfs-sync-state": "buckets"}},
                "data": {"state": json.dumps({"name": "b2", "replication": "001", "owner": "loki"}, sort_keys=True)},
            },
        ],
    })

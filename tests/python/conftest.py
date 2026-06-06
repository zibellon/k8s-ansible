"""Pytest shared fixtures + path setup for filter_plugins/seaweedfs_sync.py tests.

Path setup: prepends repo-root filter_plugins/ to sys.path so seaweedfs_sync
module is importable from any cwd. Auto-loaded by pytest для всех test files
in tests/python/.
"""
import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent / 'filter_plugins'))

import json
import pytest


@pytest.fixture
def sample_vault_json():
    """Combined JSON skeleton: admin + alice + anonymous, deterministic AK/SK."""
    return json.dumps({
        'identities': [
            {'name': 'admin', 'credentials': [{'accessKey': 'ADMIN_AK', 'secretKey': 'ADMIN_SK'}], 'actions': ['Admin']},
            {'name': 'alice', 'credentials': [{'accessKey': 'ALICE_AK', 'secretKey': 'ALICE_SK'}], 'actions': []},
            {'name': 'anonymous', 'credentials': [{'accessKey': '', 'secretKey': ''}], 'actions': []},
        ]
    }, sort_keys=True)


@pytest.fixture
def sample_target_identities():
    """Default target identities: admin + alice + anonymous."""
    return [
        {'name': 'admin', 'actions': ['Admin']},
        {'name': 'alice', 'actions': []},
        {'name': 'anonymous', 'actions': []},
    ]


@pytest.fixture
def sample_target_buckets():
    """Default target buckets: b1 with quota_size, b2 without (both с replication + owner)."""
    return [
        {
            'name': 'b1',
            'replication': '001',
            'owner': 'gitlab',
            'quota_size': '1GiB',
        },
        {'name': 'b2', 'replication': '001', 'owner': 'loki'},
    ]


@pytest.fixture
def sample_configmap_state_distribute():
    """Sample ConfigMap state for identity-distribute (Layer 3) tests."""
    return json.dumps([
        {'identity_name': 'alice', 'vault_paths': ['eso-secret/team/alice/old']},
    ])


@pytest.fixture
def sample_configmap_state_buckets():
    """Sample ConfigMap state for bucket-sync (Layer 2) tests. Includes replication."""
    return json.dumps([
        {'name': 'b1', 'replication': '001'},
        {'name': 'b_stale', 'replication': '000'},
    ])


@pytest.fixture
def sample_generate_params():
    """AK/SK length/charset params for seaweedfs_user_sync_full."""
    return {
        'access_key_length': 20,
        'secret_key_length': 40,
        'access_key_charset': 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789',
        'secret_key_charset': 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789',
    }


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
def sample_configmap_state_policies():
    """Sample ConfigMap state for policy-sync (Layer P) tests: gitlab-rw kept + p_stale orphan."""
    return json.dumps([
        {'name': 'gitlab-rw', 'document': {'Version': '2012-10-17'}},
        {'name': 'p_stale', 'document': {'Version': '2012-10-17'}},
    ])


@pytest.fixture
def sample_s3configure_raw():
    """`s3.configure` (no-arg) protojson dump: admin (Admin+creds), alice (policyNames+creds),
    anonymous (empty creds), plus a static identity the parser must IGNORE."""
    return json.dumps({
        'identities': [
            {'name': 'admin', 'credentials': [{'accessKey': 'ADMIN_AK', 'secretKey': 'ADMIN_SK', 'status': 'Active'}],
             'actions': ['Admin'], 'policyNames': [], 'isStatic': False},
            {'name': 'alice', 'credentials': [{'accessKey': 'ALICE_AK', 'secretKey': 'ALICE_SK', 'status': 'Active'}],
             'actions': [], 'policyNames': ['team-alpha-rw'], 'isStatic': False},
            {'name': 'anonymous', 'credentials': [], 'actions': [], 'policyNames': [], 'isStatic': False},
            {'name': 'static-id', 'credentials': [{'accessKey': 'S_AK', 'secretKey': 'S_SK'}],
             'actions': ['Admin'], 'policyNames': [], 'isStatic': True},
        ],
        'accounts': [], 'serviceAccounts': [], 'policies': [], 'groups': [],
    })


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

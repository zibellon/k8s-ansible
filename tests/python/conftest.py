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
    """Default target buckets: b1 with quota+policy, b2 without (both с replication)."""
    return [
        {
            'name': 'b1',
            'replication': '001',
            'owner': 'gitlab',
            'quota': {'enabled': True, 'size': '1GiB'},
            'policy': {
                'Version': '2012-10-17',
                'Statement': [
                    {'Sid': 'S1', 'Effect': 'Allow', 'Principal': 'arn:aws:iam::*:user/alice',
                     'Action': ['s3:GetObject'], 'Resource': ['arn:aws:s3:::b1/*']},
                ],
            },
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

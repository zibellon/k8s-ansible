"""Tests for filter_plugins/eso_verify.py.

conftest.py already inserts repo-root filter_plugins/ into sys.path,
so eso_verify is importable directly.
"""
import eso_verify as ev


# ---------------------------------------------------------------------------
# Fixtures (local helpers) — real shapes from hosts-vars/<c>.yaml
# ---------------------------------------------------------------------------
def _policy():
    return {
        'name': 'zitadel.eso-main',
        'rules': 'path "eso-secret/data/zitadel/*" { capabilities = ["read"] }\n'
                 'path "eso-secret/metadata/zitadel/*" { capabilities = ["read"] }',
    }


def _role(sa='eso-main', ns='zitadel', policies=None):
    return {
        'name': 'zitadel.eso-main',
        'bound_service_account_names': sa,
        'bound_service_account_namespaces': ns,
        'policies': ['zitadel.eso-main'] if policies is None else policies,
        'ttl': '1h',
    }


def _integration(role_name='zitadel.eso-main', sa_name='eso-main'):
    return {'sa_name': sa_name, 'role_name': role_name, 'kv_engine_path': 'eso-secret'}


def _secret_datafrom(esn='eso-zitadel-pg', target=None, key='eso-secret/data/zitadel/postgresql/creds'):
    return {
        'external_secret_name': esn,
        'body': {
            'target': {'name': esn if target is None else target},
            'dataFrom': [{'extract': {'key': key}}],
        },
    }


def _secret_data(esn='eso-gitlab-s3', target=None, key='eso-secret/data/zitadel/s3-storage'):
    return {
        'external_secret_name': esn,
        'body': {
            'target': {'name': esn if target is None else target},
            'data': [{'secretKey': 'ak', 'remoteRef': {'key': key, 'property': 'accessKey'}}],
        },
    }


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
def test_happy_path():
    secrets = [_secret_datafrom()]
    result = ev.eso_verify(secrets, _integration(), 'zitadel', [_policy()], [_role()])
    assert result == []


def test_happy_path_empty_secrets():
    result = ev.eso_verify([], _integration(), 'zitadel', [_policy()], [_role()])
    assert result == []


# ---------------------------------------------------------------------------
# Group B1 — role existence (gate)
# ---------------------------------------------------------------------------
def test_b1_role_not_found():
    secrets = [_secret_datafrom()]
    result = ev.eso_verify(secrets, _integration(role_name='nonexistent.eso-main'),
                           'zitadel', [_policy()], [_role()])
    assert len(result) == 1
    assert 'NOT found' in result[0]
    assert 'nonexistent.eso-main' in result[0]


def test_b1_role_not_found_still_computes_c():
    # Role absent AND duplicate external_secret_name → both violations present.
    s1 = _secret_datafrom(esn='eso-dup', target='t1')
    s2 = _secret_datafrom(esn='eso-dup', target='t2')
    result = ev.eso_verify([s1, s2], _integration(role_name='nonexistent.eso-main'),
                           'zitadel', [_policy()], [_role()])
    assert len(result) >= 2
    joined = ' '.join(result)
    assert 'NOT found' in joined
    assert 'duplicate external_secret_name' in joined


# ---------------------------------------------------------------------------
# Group B2 — SA binding + ns binding + >=1 policy
# ---------------------------------------------------------------------------
def test_b2_wrong_sa():
    result = ev.eso_verify([_secret_datafrom()], _integration(), 'zitadel',
                           [_policy()], [_role(sa='other-sa')])
    assert len(result) == 1
    assert 'must bind SA' in result[0]


def test_b2_wrong_namespace():
    result = ev.eso_verify([_secret_datafrom()], _integration(), 'WRONG-NS',
                           [_policy()], [_role()])
    assert len(result) == 1
    assert 'must bind SA' in result[0]


def test_b2_empty_policies():
    # Empty secrets so D produces nothing — isolate the B2 violation.
    result = ev.eso_verify([], _integration(), 'zitadel', [_policy()], [_role(policies=[])])
    assert len(result) == 1
    assert 'must bind SA' in result[0]


# ---------------------------------------------------------------------------
# Group B3 — referential integrity (role.policies → existing policy)
# ---------------------------------------------------------------------------
def test_b3_missing_policy_reference():
    role = _role(policies=['zitadel.eso-main', 'eso-missing'])
    result = ev.eso_verify([_secret_datafrom()], _integration(), 'zitadel', [_policy()], [role])
    assert len(result) == 1
    assert "references policy 'eso-missing'" in result[0]
    assert 'zitadel.eso-main' in result[0]


# ---------------------------------------------------------------------------
# Group C1 — unique external_secret_name
# ---------------------------------------------------------------------------
def test_c1_duplicate_external_secret_name():
    s1 = _secret_datafrom(esn='eso-dup', target='t1')
    s2 = _secret_datafrom(esn='eso-dup', target='t2')
    result = ev.eso_verify([s1, s2], _integration(), 'zitadel', [_policy()], [_role()])
    assert len(result) == 1
    assert 'duplicate external_secret_name' in result[0]
    assert 'eso-dup' in result[0]


# ---------------------------------------------------------------------------
# Group C2 — unique body.target.name
# ---------------------------------------------------------------------------
def test_c2_duplicate_target_name():
    s1 = _secret_datafrom(esn='eso-a', target='eso-shared')
    s2 = _secret_datafrom(esn='eso-b', target='eso-shared')
    result = ev.eso_verify([s1, s2], _integration(), 'zitadel', [_policy()], [_role()])
    assert len(result) == 1
    assert 'duplicate body.target.name' in result[0]
    assert 'eso-shared' in result[0]


# ---------------------------------------------------------------------------
# Group D — policy path coverage (substring, scoped to role policies)
# ---------------------------------------------------------------------------
def test_d_path_not_covered():
    s = _secret_datafrom(esn='eso-x', key='eso-secret/data/other/x')
    result = ev.eso_verify([s], _integration(), 'zitadel', [_policy()], [_role()])
    assert len(result) == 1
    assert 'is NOT covered' in result[0]
    assert 'eso-secret/data/other/x' in result[0]


def test_d_path_covered_substring():
    s = _secret_datafrom(esn='eso-x', key='eso-secret/data/zitadel/postgresql/creds')
    result = ev.eso_verify([s], _integration(), 'zitadel', [_policy()], [_role()])
    assert result == []


def test_d_collects_paths_from_both_forms():
    # Policy prefix covers NEITHER path → both forms reported uncovered,
    # which proves both dataFrom.extract.key and data[].remoteRef.key are collected.
    pol = {'name': 'multi.eso-main',
           'rules': 'path "eso-secret/data/unrelated/*" { capabilities = ["read"] }'}
    role = {'name': 'multi.eso-main', 'bound_service_account_names': 'eso-main',
            'bound_service_account_namespaces': 'multi', 'policies': ['multi.eso-main'], 'ttl': '1h'}
    integ = {'sa_name': 'eso-main', 'role_name': 'multi.eso-main', 'kv_engine_path': 'eso-secret'}
    s_df = _secret_datafrom(esn='eso-df', key='eso-secret/data/multi/a')
    s_data = _secret_data(esn='eso-data', key='eso-secret/data/multi/b')
    result = ev.eso_verify([s_df, s_data], integ, 'multi', [pol], [role])
    joined = ' '.join(result)
    assert len([v for v in result if 'is NOT covered' in v]) == 2
    assert 'eso-secret/data/multi/a' in joined
    assert 'eso-secret/data/multi/b' in joined


# ---------------------------------------------------------------------------
# Malformed items — return violation, never raise
# ---------------------------------------------------------------------------
def test_malformed_missing_target_name():
    s = {'external_secret_name': 'eso-x',
         'body': {'dataFrom': [{'extract': {'key': 'eso-secret/data/zitadel/x'}}]}}
    result = ev.eso_verify([s], _integration(), 'zitadel', [_policy()], [_role()])
    assert any('missing body.target.name' in v for v in result)
    assert any('eso-x' in v for v in result)


def test_malformed_missing_external_secret_name():
    s = {'body': {'target': {'name': 'eso-t'},
                  'dataFrom': [{'extract': {'key': 'eso-secret/data/zitadel/x'}}]}}
    result = ev.eso_verify([s], _integration(), 'zitadel', [_policy()], [_role()])
    assert any('missing external_secret_name' in v for v in result)


# ---------------------------------------------------------------------------
# Multiple violations at once
# ---------------------------------------------------------------------------
def test_multiple_violations_at_once():
    # B2 (wrong sa) + C1 (dup esn) + D (two uncovered paths)
    s1 = _secret_datafrom(esn='eso-dup', target='t1', key='eso-secret/data/other/x')
    s2 = _secret_datafrom(esn='eso-dup', target='t2', key='eso-secret/data/other/y')
    result = ev.eso_verify([s1, s2], _integration(), 'zitadel', [_policy()], [_role(sa='wrong-sa')])
    joined = ' '.join(result)
    assert 'must bind SA' in joined
    assert 'duplicate external_secret_name' in joined
    assert 'is NOT covered' in joined


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------
def test_as_list_none():
    assert ev._as_list(None) == []


def test_as_list_scalar():
    assert ev._as_list('eso-main') == ['eso-main']


def test_as_list_list():
    assert ev._as_list(['a', 'b']) == ['a', 'b']


def test_extract_prefixes_strips_glob_and_dedups():
    pols = [{'name': 'p', 'rules': 'path "eso-secret/data/zitadel/*" { }\n'
                                   'path "eso-secret/metadata/zitadel/*" { }\n'
                                   'path "eso-secret/data/zitadel/*" { }'}]
    assert ev._extract_prefixes(pols) == ['eso-secret/data/zitadel', 'eso-secret/metadata/zitadel']


def test_extract_prefixes_empty_rules():
    assert ev._extract_prefixes([{'name': 'p'}]) == []


def test_collect_vault_paths_both_forms_unique():
    secrets = [
        {'body': {'dataFrom': [{'extract': {'key': 'k1'}}]}},
        {'body': {'data': [{'remoteRef': {'key': 'k2'}}]}},
        {'body': {'dataFrom': [{'extract': {'key': 'k1'}}]}},
    ]
    assert ev._collect_vault_paths(secrets) == ['k1', 'k2']


def test_find_duplicates_preserves_order():
    assert ev._find_duplicates(['b', 'a', 'b', 'a']) == ['b', 'a']

"""
SeaweedFS user (identity) sync — pure Python filter plugin (Layer 1).
Used by playbook-app/tasks/seaweedfs/tasks-seaweedfs-user-sync.yaml.
Diffs the live filer `s3.configure` dump against the inventory target
(seaweedfs_identities + _extra) → delete / create / grant / revoke deltas.
Self-contained (no cross-file imports — v18 split из монолита seaweedfs_sync.py;
_parse_s3_configure_identities дублируется в seaweedfs_distribute.py, но в v20 return
shape расходится per-file: здесь {name, access_keys:[...]} — Layer 1 секрет не нужен).
Lives in repo-root filter_plugins/; discovered via ansible.cfg
[defaults] filter_plugins = filter_plugins.
"""
import json
import secrets
try:
    from ansible.errors import AnsibleFilterError
except ImportError:
    # Allow local pytest runs without Ansible installed
    AnsibleFilterError = Exception


def _gen_secret(length, charset):
    """Generate random string of `length` chars from `charset` via secrets.choice.
    Cryptographically secure. Mock-friendly: secrets module imported globally.
    """
    return ''.join(secrets.choice(charset) for _ in range(length))


def _parse_s3_configure_identities(raw):
    """Parse `s3.configure` (no-arg) protojson dump → list of normalized identity dicts.
    Returns [] for ''/None/malformed/missing 'identities'. Never raises. Skips isStatic
    identities (managed externally — must not touch them).

    Each entry: {'name', 'access_keys': [ak, ...], 'actions': [..], 'policyNames': [..]}.
    access_keys = every credential's accessKey (order preserved; [] when no credentials,
    e.g. anonymous). The secret is NOT returned — Layer 1 never needs it.
    (v20: reads ALL credentials, not just credentials[0]. Helper дублируется в
    seaweedfs_distribute.py с per-file return shape — намеренно, см. план.)"""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, dict):
        return []
    result = []
    for ident in data.get('identities') or []:
        if not isinstance(ident, dict) or ident.get('isStatic'):
            continue
        name = ident.get('name')
        if not name:
            continue
        creds = ident.get('credentials') or []
        access_keys = [c.get('accessKey') for c in creds
                       if isinstance(c, dict) and c.get('accessKey')]
        result.append({
            'name': name,
            'access_keys': access_keys,
            'actions': list(ident.get('actions') or []),
            'policyNames': list(ident.get('policyNames') or []),
        })
    return result


def seaweedfs_identities_to_delete(s3configure_raw, target_identities):
    """Identity names present in the filer (s3.configure dump) but absent from target →
    delete via `s3.configure -user=<name> -delete -apply` (bare delete = whole identity).
    (v18: reads the live filer dump.)"""
    current = _parse_s3_configure_identities(s3configure_raw)
    target_names = {t['name'] for t in target_identities}
    return [c['name'] for c in current if c['name'] not in target_names]


def seaweedfs_identities_to_create(s3configure_raw, target_identities, *,
                                   access_key_length, secret_key_length,
                                   access_key_charset, secret_key_charset):
    """Target identities NOT present in the filer → create with full grants.
    Generates fresh AK/SK (anonymous → empty creds). Returns
    [{name, accessKey, secretKey, actions, policy_names}] — applied via
    `s3.configure -user=X [-access_key -secret_key][-actions][-policies] -apply`.
    (v18: the 'create' slice — new identities only; existing creds are preserved by
    not being touched here.)"""
    current_names = {i['name'] for i in _parse_s3_configure_identities(s3configure_raw)}
    result = []
    for t in target_identities:
        name = t['name']
        if name in current_names:
            continue
        if name == 'anonymous':
            ak, sk = '', ''
        else:
            ak = _gen_secret(access_key_length, access_key_charset)
            sk = _gen_secret(secret_key_length, secret_key_charset)
        result.append({
            'name': name,
            'accessKey': ak,
            'secretKey': sk,
            'actions': list(t.get('actions') or []),
            'policy_names': list(t.get('policy_names') or []),
        })
    return result


def seaweedfs_identities_to_grant(s3configure_raw, target_identities):
    """Per identity present in BOTH filer and target: grants to ADD (target − filer).
    Returns [{name, actions_add, policies_add}] ONLY where at least one list is non-empty
    (no-op entries skipped). Applied via `s3.configure -user=X [-actions=csv][-policies=csv]
    -apply` (additive — addUniqueToSlice). Creds preserved (not touched here).
    (v18: the 'grant' slice — existing identities only.)"""
    target_by_name = {t['name']: t for t in target_identities}
    result = []
    for cur in _parse_s3_configure_identities(s3configure_raw):
        t = target_by_name.get(cur['name'])
        if t is None:
            continue
        actions_add = [a for a in (t.get('actions') or []) if a not in cur['actions']]
        policies_add = [p for p in (t.get('policy_names') or []) if p not in cur['policyNames']]
        if actions_add or policies_add:
            result.append({
                'name': cur['name'],
                'actions_add': actions_add,
                'policies_add': policies_add,
            })
    return result


def seaweedfs_identities_to_revoke(s3configure_raw, target_identities):
    """Per identity present in BOTH filer and target: grants to REMOVE (filer − target).
    Returns [{name, actions_remove, policies_remove}] ONLY where at least one list is
    non-empty (a bare `s3.configure -user=X -delete` would delete the whole identity — guard).
    Applied via `s3.configure -user=X [-policies=csv][-actions=csv] -delete -apply`
    (removeFromSlice, idempotent). (v18: the 'revoke' slice — drops stale grants.)"""
    target_by_name = {t['name']: t for t in target_identities}
    result = []
    for cur in _parse_s3_configure_identities(s3configure_raw):
        t = target_by_name.get(cur['name'])
        if t is None:
            continue
        t_actions = set(t.get('actions') or [])
        t_policies = set(t.get('policy_names') or [])
        actions_remove = [a for a in cur['actions'] if a not in t_actions]
        policies_remove = [p for p in cur['policyNames'] if p not in t_policies]
        if actions_remove or policies_remove:
            result.append({
                'name': cur['name'],
                'actions_remove': actions_remove,
                'policies_remove': policies_remove,
            })
    return result


# =============================================================================
# Ansible FilterModule registration
# =============================================================================
class FilterModule(object):
    """Ansible filter plugin entry point — registers seaweedfs user (identity) filters."""
    def filters(self):
        return {
            'seaweedfs_identities_to_delete': seaweedfs_identities_to_delete,
            'seaweedfs_identities_to_create': seaweedfs_identities_to_create,
            'seaweedfs_identities_to_grant': seaweedfs_identities_to_grant,
            'seaweedfs_identities_to_revoke': seaweedfs_identities_to_revoke,
        }

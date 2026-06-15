"""
SeaweedFS user (identity) sync — pure Python filter plugin (Layer 1).
Used by playbook-app/tasks/seaweedfs/tasks-seaweedfs-user-sync.yaml.
Diffs the live filer `s3.configure` dump against the inventory target
(seaweedfs_identities + _extra) → delete / create / grant / revoke / keys-add /
keys-delete deltas.
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


def _validate_target_keys(target_identities):
    """Fail-fast validation of the v20 per-key inventory schema. Raises AnsibleFilterError on:
      - a named (non-anonymous) identity with no account_id, or an empty / non-string one
        (account_id is the explicit object-ownership id — SeaweedFS 4.34 owner = account,
        pinned explicitly as the identity name);
      - a named identity whose account_id != name (strict 1:1 — the object owner is pinned to
        the name; to change ownership, rename = delete+recreate the identity);
      - a duplicate identity name across ALL identities (names must be globally unique;
        account_id == name, so a dup name is also a dup owner);
      - a named identity with an empty / non-string account_display_name when present
        (the field is OPTIONAL — omit it to default to account_id);
      - a named (non-anonymous) identity with no keys (a credential-less identity is useless —
        SeaweedFS never auto-generates keys);
      - a key with an empty / non-string access_key;
      - a duplicate access_key across ALL identities (global uniqueness — the S3 gateway maps
        accessKey→identity flat, so a dup is an auth collision);
      - the anonymous identity carrying keys, account_id, or account_display_name (anonymous
        maps to AccountAnonymous and never routes through the owner resolver).
    Pure / idempotent — every keys-consuming filter calls it first."""
    seen = {}
    seen_name = {}
    for ident in target_identities:
        name = ident.get('name')
        keys = ident.get('keys') or []
        if name == 'anonymous':
            if keys:
                raise AnsibleFilterError(
                    "Anonymous identity must not have keys (anonymous has empty "
                    "credentials). Remove 'keys' from the anonymous identity in inventory.")
            if ident.get('account_id') is not None:
                raise AnsibleFilterError(
                    "Anonymous identity must not have account_id (anonymous maps to "
                    "AccountAnonymous, never routed through the owner resolver). Remove "
                    "'account_id' from the anonymous identity in inventory.")
            if ident.get('account_display_name') is not None:
                raise AnsibleFilterError(
                    "Anonymous identity must not have account_display_name (anonymous "
                    "carries no account). Remove it from the anonymous identity in inventory.")
            continue
        account_id = ident.get('account_id')
        if not account_id or not isinstance(account_id, str):
            raise AnsibleFilterError(
                "Identity '{0}' has no account_id. account_id is a REQUIRED non-empty "
                "string — the explicit object-ownership id (SeaweedFS 4.34 owner = account, "
                "pinned explicitly as the identity name).".format(name))
        if account_id != name:
            raise AnsibleFilterError(
                "Identity '{0}' has account_id '{1}' != name. account_id MUST equal the "
                "identity name (strict 1:1 — the object owner is pinned to the name; to "
                "change ownership, rename = delete+recreate the identity).".format(name, account_id))
        if name in seen_name:
            raise AnsibleFilterError(
                "Duplicate identity name '{0}'. Identity names must be globally unique "
                "(account_id == name, so a dup name is also a dup owner).".format(name))
        seen_name[name] = True
        display = ident.get('account_display_name')
        if display is not None and (not display or not isinstance(display, str)):
            raise AnsibleFilterError(
                "Identity '{0}' has an empty / non-string account_display_name. When present "
                "it must be a non-empty string (the field is OPTIONAL — omit it to default "
                "to account_id).".format(name))
        if not keys:
            raise AnsibleFilterError(
                "Identity '{0}' has no keys. Every named identity must declare at least one "
                "key {{access_key, vault_paths?}} — a credential-less identity is useless "
                "(SeaweedFS never auto-generates keys).".format(name))
        for k in keys:
            ak = k.get('access_key')
            if not ak or not isinstance(ak, str):
                raise AnsibleFilterError(
                    "Identity '{0}' has a key with an empty / non-string access_key. "
                    "access_key is a REQUIRED non-empty operator-chosen identifier.".format(name))
            if ak in seen:
                raise AnsibleFilterError(
                    "Duplicate access_key '{0}' across identities '{1}' and '{2}'. "
                    "access_key must be globally unique (the S3 gateway maps "
                    "accessKey→identity flat — a dup is an auth collision).".format(
                        ak, seen[ak], name))
            seen[ak] = name


def seaweedfs_identities_to_delete(s3configure_raw, target_identities):
    """Identity names present in the filer (s3.configure dump) but absent from target →
    delete via `s3.configure -user=<name> -delete -apply` (bare delete = whole identity).
    (v18: reads the live filer dump.)"""
    current = _parse_s3_configure_identities(s3configure_raw)
    target_names = {t['name'] for t in target_identities}
    return [c['name'] for c in current if c['name'] not in target_names]


def seaweedfs_identities_to_create(s3configure_raw, target_identities, *,
                                   secret_key_length, secret_key_charset):
    """Target identities NOT present in the filer → create with their FIRST key + full grants.
    anonymous → empty creds + empty account fields; a named identity → keys[0].access_key
    (operator-chosen) plus a freshly generated secret_key, and its explicit account_id /
    account_display_name (the object-ownership id; display defaults to account_id when unset).
    Returns [{name, accountId, accountDisplayName, accessKey, secretKey, actions, policy_names}]
    — applied via `s3.configure -user=X [-account_id -account_display_name][-access_key
    -secret_key][-actions][-policies] -apply`. accountId/accountDisplayName are ALWAYS present
    ('' for anonymous) so the Phase B template can gate on them.
    (v20: per-key. keys[1:] are added by seaweedfs_keys_to_add, not here.)
    Raises (via _validate_target_keys) on a named identity with no account_id / no keys / dup."""
    _validate_target_keys(target_identities)
    current_names = {i['name'] for i in _parse_s3_configure_identities(s3configure_raw)}
    result = []
    for t in target_identities:
        name = t['name']
        if name in current_names:
            continue
        if name == 'anonymous':
            ak, sk = '', ''
            account_id, account_display_name = '', ''
        else:
            ak = t['keys'][0]['access_key']
            sk = _gen_secret(secret_key_length, secret_key_charset)
            account_id = t['account_id']
            account_display_name = t.get('account_display_name') or account_id
        result.append({
            'name': name,
            'accountId': account_id,
            'accountDisplayName': account_display_name,
            'accessKey': ak,
            'secretKey': sk,
            'actions': list(t.get('actions') or []),
            'policy_names': list(t.get('policy_names') or []),
        })
    return result


def seaweedfs_keys_to_add(s3configure_raw, target_identities, *,
                          secret_key_length, secret_key_charset):
    """Per identity: inventory access_keys NOT yet in the filer → add via
    `s3.configure -user=X -access_key=AK -secret_key=<gen> -apply` (append credential,
    multi-cred OK). For a BRAND-NEW identity (absent from the filer) the FIRST key is
    excluded by index — it is created by seaweedfs_identities_to_create; only keys[1:]
    are added here. anonymous is skipped (no creds). Returns [{name, accessKey, secretKey}]
    with a freshly generated secret_key per added key.
    NO-ROTATION INVARIANT: an access_key already present in the filer is never re-applied
    (re-applying with a secret would overwrite/rotate it — command_s3_configure.go).
    Raises (via _validate_target_keys) on schema violations."""
    _validate_target_keys(target_identities)
    current = _parse_s3_configure_identities(s3configure_raw)
    filer_aks_by_name = {c['name']: set(c['access_keys']) for c in current}
    result = []
    for t in target_identities:
        name = t['name']
        if name == 'anonymous':
            continue
        keys = t.get('keys') or []
        # brand-new identity (not in filer) → its keys[0] is handled by to_create
        candidate_keys = keys if name in filer_aks_by_name else keys[1:]
        existing_aks = filer_aks_by_name.get(name, set())
        for k in candidate_keys:
            ak = k['access_key']
            if ak in existing_aks:
                continue  # no-rotation: already in the filer
            result.append({
                'name': name,
                'accessKey': ak,
                'secretKey': _gen_secret(secret_key_length, secret_key_charset),
            })
    return result


def seaweedfs_keys_to_delete(s3configure_raw, target_identities):
    """Per identity present in BOTH filer and target: filer access_keys NOT in the inventory
    target → delete that single credential via `s3.configure -user=X -access_key=AK -delete
    -apply` (removes ONLY that credential — identity survives). Identities absent from target
    are removed wholesale by seaweedfs_identities_to_delete (gate: name in target_names — not
    duplicated here). Returns [{name, accessKey}]. (v20: per-key pruning of filer-extra creds.)
    Raises (via _validate_target_keys) on schema violations."""
    _validate_target_keys(target_identities)
    target_by_name = {t['name']: t for t in target_identities}
    result = []
    for cur in _parse_s3_configure_identities(s3configure_raw):
        name = cur['name']
        t = target_by_name.get(name)
        if t is None:
            continue  # whole-identity delete handled by seaweedfs_identities_to_delete
        target_aks = {k['access_key'] for k in (t.get('keys') or [])}
        for ak in cur['access_keys']:
            if ak not in target_aks:
                result.append({'name': name, 'accessKey': ak})
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
            'seaweedfs_keys_to_add': seaweedfs_keys_to_add,
            'seaweedfs_keys_to_delete': seaweedfs_keys_to_delete,
            'seaweedfs_identities_to_grant': seaweedfs_identities_to_grant,
            'seaweedfs_identities_to_revoke': seaweedfs_identities_to_revoke,
        }

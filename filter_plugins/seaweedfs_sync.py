"""
SeaweedFS sync compute layer — pure Python filter plugin.
Used by playbook-app/tasks/seaweedfs/tasks-seaweedfs-{user-sync,
identity-secret-distribute,bucket-sync}.yaml. All compute (diff,
JSON building, validation) lives here; Ansible task'и используют
filters через {{ x | seaweedfs_<filter>(y) }} syntax, оставляя
себе только orchestration (vault-get/put/delete, kubectl, loops).
Lives in repo-root filter_plugins/ directory. Discovered by Ansible
via ansible.cfg [defaults] filter_plugins = filter_plugins setting
(ansible.cfg in repo root; ansible-playbook always invoked with
cwd=repo root per project convention).
"""
import json
import secrets
try:
    from ansible.errors import AnsibleFilterError
except ImportError:
    # Allow local pytest runs without Ansible installed
    AnsibleFilterError = Exception
# =============================================================================
# Private helpers (NOT registered as public filters)
# =============================================================================
def _parse_combined_json(s):
    """Parse Vault combined JSON string to dict with 'identities' key.
    Returns {'identities': []} for None/empty/malformed input or when
    'identities' key is missing. Never raises.
    """
    if not s:
        return {'identities': []}
    try:
        data = json.loads(s)
    except (ValueError, TypeError):
        return {'identities': []}
    if not isinstance(data, dict) or 'identities' not in data:
        return {'identities': []}
    return data
def _extract_creds_by_name(combined):
    """Build {name: {accessKey, secretKey}} mapping from combined dict.
    Reads combined['identities'][*].credentials[0]. Missing fields
    default to empty string.
    """
    result = {}
    for entry in combined.get('identities', []):
        name = entry.get('name')
        creds_list = entry.get('credentials') or [{}]
        first = creds_list[0] if creds_list else {}
        result[name] = {
            'accessKey': first.get('accessKey', ''),
            'secretKey': first.get('secretKey', ''),
        }
    return result
def _compute_identity_diff(current, target):
    """Compute identity sync diff by name primary key.
    Args:
        current: list of identity dicts (from combined JSON parse).
        target: list of identity dicts (from inventory).
    Returns:
        {'to_create': [...], 'to_update': [...], 'to_delete': [...]}
    """
    target_names = {i['name'] for i in target}
    current_names = {i['name'] for i in current}
    return {
        'to_create': [i for i in target if i['name'] not in current_names],
        'to_update': [i for i in target if i['name'] in current_names],
        'to_delete': [i for i in current if i['name'] not in target_names],
    }
def _gen_secret(length, charset):
    """Generate random string of `length` chars from `charset` via secrets.choice.
    Cryptographically secure. Mock-friendly: secrets module imported globally.
    """
    return ''.join(secrets.choice(charset) for _ in range(length))
# =============================================================================
# Public Layer 1 filter — stateless user-sync orchestrator
# =============================================================================
def seaweedfs_user_sync_full(vault_raw_json, target_identities, *,
                              access_key_length, secret_key_length,
                              access_key_charset, secret_key_charset):
    """Финальный combined JSON для записи в Vault.

    Stateless filter: принимает raw Vault JSON string + target identities list,
    возвращает str — финальный combined JSON {"identities": [...]} c sort_keys=True
    для записи через vault kv put.

    Внутренняя логика (не часть public контракта):
        1. Parse vault → current identities list.
        2. Compute diff vs target (to_create, to_update, to_delete) by name.
        3. Generate fresh AK + SK для каждого to_create non-anonymous через
           secrets.choice(charset) цикл length раз. Anonymous получает пустые creds.
        4. Build new identities list:
           - to_create: {name, credentials: [{accessKey, secretKey}], actions}.
           - to_update: {name, credentials: preserved from current, actions: target's}.
           - to_delete: skip.
        5. Serialize {"identities": [...]} через json.dumps(... sort_keys=True).

    Args:
        vault_raw_json: str — current Vault combined JSON (may be '', None, malformed).
        target_identities: list — target identities from inventory.
        access_key_length: int — length of generated access_key.
        secret_key_length: int — length of generated secret_key.
        access_key_charset: str — charset for access_key generation.
        secret_key_charset: str — charset for secret_key generation.

    Returns:
        str — finalized combined JSON, ready for vault kv put.
    """
    combined = _parse_combined_json(vault_raw_json)
    current = combined['identities']
    diff = _compute_identity_diff(current, target_identities)
    current_by_name = {i['name']: i for i in current}
    result = []
    for item in diff['to_create']:
        name = item['name']
        if name == 'anonymous':
            ak, sk = '', ''
        else:
            ak = _gen_secret(access_key_length, access_key_charset)
            sk = _gen_secret(secret_key_length, secret_key_charset)
        result.append({
            'name': name,
            'credentials': [{'accessKey': ak, 'secretKey': sk}],
            'actions': item.get('actions', []),
        })
    for item in diff['to_update']:
        name = item['name']
        current_entry = current_by_name.get(name, {})
        existing_creds = current_entry.get('credentials') or [{'accessKey': '', 'secretKey': ''}]
        result.append({
            'name': name,
            'credentials': existing_creds,
            'actions': item.get('actions', []),
        })
    return json.dumps({'identities': result}, sort_keys=True)
# =============================================================================
# identity-distribute (Layer 3) filters
# =============================================================================
def seaweedfs_compute_distribution_pairs(target_identities, creds_by_name):
    """Build list of (path, name, accessKey, secretKey) pairs for vault-put loop.
    Args:
        target_identities: list with extra_vault_paths field (full Vault paths).
        creds_by_name: {name: {accessKey, secretKey}}.
    Returns:
        list of {path, name, accessKey, secretKey} — one per (identity, path) pair.
    """
    pairs = []
    for identity in target_identities:
        name = identity['name']
        creds = creds_by_name.get(name, {})
        for path in identity.get('extra_vault_paths', []):
            pairs.append({
                'path': path,
                'name': name,
                'accessKey': creds.get('accessKey', ''),
                'secretKey': creds.get('secretKey', ''),
            })
    return pairs
def seaweedfs_compute_state_paths_to_delete(state, target_paths):
    """Build list of state paths to vault-delete (not in target paths set).
    Args:
        state: list of state entries [{identity_name, vault_paths: [...]}, ...].
        target_paths: iterable of full target paths.
    Returns:
        list of {path, identity_name} dicts.
    """
    target_set = set(target_paths)
    deletes = []
    for entry in state:
        identity_name = entry.get('identity_name')
        for path in entry.get('vault_paths', []):
            if path not in target_set:
                deletes.append({'path': path, 'identity_name': identity_name})
    return deletes
def seaweedfs_build_new_distribution_state(target_identities):
    """Build new state list for ConfigMap update.
    Returns: list of {identity_name, vault_paths: [...]} entries.
    """
    return [
        {'identity_name': i['name'], 'vault_paths': i.get('extra_vault_paths', [])}
        for i in target_identities
    ]
def seaweedfs_validate_target_paths_unique(paths):
    """Return True if all paths unique; raise AnsibleFilterError if duplicates."""
    paths_list = list(paths)
    if len(paths_list) != len(set(paths_list)):
        seen = set()
        dups = []
        for p in paths_list:
            if p in seen and p not in dups:
                dups.append(p)
            seen.add(p)
        raise AnsibleFilterError(
            "Duplicate Vault paths in identity.extra_vault_paths "
            "across identities: {0}. Each Vault path must be unique "
            "(no race-like inventory bug).".format(dups)
        )
    return True
def seaweedfs_validate_anonymous_no_extra_paths(identities):
    """Return True if anonymous identity does NOT have non-empty extra_vault_paths.
    Raises AnsibleFilterError otherwise — anonymous has no credentials in
    combined JSON, distribute is meaningless.
    """
    for identity in identities:
        if identity.get('name') == 'anonymous' and identity.get('extra_vault_paths'):
            raise AnsibleFilterError(
                "Anonymous identity has non-empty extra_vault_paths — distribute "
                "impossible (anonymous has empty credentials in combined JSON). "
                "Remove extra_vault_paths from anonymous identity in inventory."
            )
    return True
# =============================================================================
# bucket-sync (Layer 2) filters
# =============================================================================
def seaweedfs_compute_bucket_diff(current_state, target_buckets):
    """Compute unified bucket+policy+quota sync diff.
    Args:
        current_state: list [{name, quota?, policy?}, ...] from ConfigMap.
        target_buckets: target list [{name, quota?, policy?}, ...].
    Returns:
        {
            'to_delete_buckets': [state entries to remove],
            'kept_policies_to_delete': [target entries — state had policy,
                                        target doesn't],
            'kept_policies_to_apply': [target kept entries with policy],
            'to_create_buckets': [target entries new vs state],
            'new_policies_to_apply': [to_create entries with policy],
            'quotas_to_apply': [target entries with quota defined],
        }
    """
    target_by_name = {b['name']: b for b in target_buckets}
    state_by_name = {b['name']: b for b in current_state}
    target_names = set(target_by_name)
    state_names = set(state_by_name)
    kept_names = target_names & state_names
    return {
        'to_delete_buckets': [state_by_name[n] for n in state_names - target_names],
        'kept_policies_to_delete': [
            target_by_name[n] for n in kept_names
            if 'policy' in state_by_name[n] and 'policy' not in target_by_name[n]
        ],
        'kept_policies_to_apply': [
            target_by_name[n] for n in kept_names
            if 'policy' in target_by_name[n]
        ],
        'to_create_buckets': [target_by_name[n] for n in target_names - state_names],
        'new_policies_to_apply': [
            target_by_name[n] for n in target_names - state_names
            if 'policy' in target_by_name[n]
        ],
        'quotas_to_apply': [b for b in target_buckets if 'quota' in b],
    }
def seaweedfs_quota_size_to_mib(size_str):
    """Convert human-readable size (e.g. '100GiB') to MiB integer.
    Supports MiB / GiB / TiB suffix. Raises AnsibleFilterError for any
    other suffix or malformed input.
    """
    s = str(size_str).strip()
    if s.endswith('MiB'):
        return int(s[:-3])
    if s.endswith('GiB'):
        return int(s[:-3]) * 1024
    if s.endswith('TiB'):
        return int(s[:-3]) * 1024 * 1024
    raise AnsibleFilterError(
        "Unsupported size unit in '{0}'. Use MiB/GiB/TiB.".format(size_str)
    )
def seaweedfs_validate_principal_not_dict(statement):
    """Return True if Statement.Principal is NOT a dict.
    Raises AnsibleFilterError on dict form — SeaweedFS 4.29 non-AWS
    limitation (policy_engine/types.go:55-77 accepts only string or list).
    """
    principal = statement.get('Principal')
    if isinstance(principal, dict):
        raise AnsibleFilterError(
            "Statement Principal must be flat string or array, not dict "
            "(got {0}). SeaweedFS 4.29 limitation. Use "
            "\"arn:aws:iam::*:user/<name>\" (single), "
            "[\"arn:...\", \"arn:...\"] (multiple), or \"*\" (anonymous).".format(principal)
        )
    return True
# =============================================================================
# Ansible FilterModule registration
# =============================================================================
class FilterModule(object):
    """Ansible filter plugin entry point — registers all seaweedfs_* filters."""
    def filters(self):
        return {
            'seaweedfs_parse_combined_json': _parse_combined_json,
            'seaweedfs_extract_creds_by_name': _extract_creds_by_name,
            'seaweedfs_user_sync_full': seaweedfs_user_sync_full,
            'seaweedfs_compute_distribution_pairs': seaweedfs_compute_distribution_pairs,
            'seaweedfs_compute_state_paths_to_delete': seaweedfs_compute_state_paths_to_delete,
            'seaweedfs_build_new_distribution_state': seaweedfs_build_new_distribution_state,
            'seaweedfs_validate_target_paths_unique': seaweedfs_validate_target_paths_unique,
            'seaweedfs_validate_anonymous_no_extra_paths': seaweedfs_validate_anonymous_no_extra_paths,
            'seaweedfs_compute_bucket_diff': seaweedfs_compute_bucket_diff,
            'seaweedfs_quota_size_to_mib': seaweedfs_quota_size_to_mib,
            'seaweedfs_validate_principal_not_dict': seaweedfs_validate_principal_not_dict,
        }

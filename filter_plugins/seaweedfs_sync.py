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
import re
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
# Private helpers (Layer 3 — identity-distribute)
# =============================================================================
def _parse_configmap_state(s):
    """Parse ConfigMap raw state JSON to list. Returns [] for empty/missing/malformed.
    Used for identity-distribute and bucket-sync state ConfigMaps."""
    if not s:
        return []
    try:
        data = json.loads(s)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    return data

def _flatten_target_paths(identities):
    """Flatten all extra_vault_paths across identities (preserve order, allow dupes
    — dupes detected by _validate_paths_unique downstream)."""
    paths = []
    for i in identities:
        paths.extend(i.get('extra_vault_paths', []))
    return paths

def _validate_anonymous_no_extra(identities):
    """Raise AnsibleFilterError if anonymous identity has non-empty extra_vault_paths.
    Anonymous has empty credentials in combined JSON — distribute meaningless."""
    for identity in identities:
        if identity.get('name') == 'anonymous' and identity.get('extra_vault_paths'):
            raise AnsibleFilterError(
                "Anonymous identity has non-empty extra_vault_paths — distribute "
                "impossible (anonymous has empty credentials in combined JSON). "
                "Remove extra_vault_paths from anonymous identity in inventory."
            )

def _validate_paths_unique(paths):
    """Raise AnsibleFilterError if duplicate paths in flattened target paths list."""
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

def _validate_creds_exist(target_identities, creds_by_name):
    """Raise AnsibleFilterError if any target identity (with extra_vault_paths)
    is missing in combined JSON creds_by_name dict."""
    for identity in target_identities:
        if not identity.get('extra_vault_paths'):
            continue
        name = identity['name']
        if name not in creds_by_name:
            raise AnsibleFilterError(
                "Identity '{0}' has extra_vault_paths but missing in Vault "
                "combined JSON. Run 'ansible-playbook playbook-app/seaweedfs-install.yaml "
                "--tags user-sync' first.".format(name)
            )

def _compute_distribution_pairs(target_identities, creds_by_name):
    """Build (path, name, accessKey, secretKey) pairs for Phase A vault-put loop.
    Identities без extra_vault_paths skipped automatically (empty inner loop)."""
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

def _compute_state_paths_to_delete(state, target_paths):
    """Build list of state paths to vault-delete (not in target paths set)."""
    target_set = set(target_paths)
    deletes = []
    for entry in state:
        identity_name = entry.get('identity_name')
        for path in entry.get('vault_paths', []):
            if path not in target_set:
                deletes.append({'path': path, 'identity_name': identity_name})
    return deletes

def _build_new_distribution_state(target_identities):
    """Build new state list — only identities with non-empty extra_vault_paths.
    Preserves backward compat with current YAML behavior (selectattr-filtered list)."""
    return [
        {'identity_name': i['name'], 'vault_paths': i.get('extra_vault_paths', [])}
        for i in target_identities
        if i.get('extra_vault_paths')
    ]
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
# Public Layer 3 filters — stateless identity-distribute orchestrators
# =============================================================================
def seaweedfs_distribute_paths_to_delete(vault_raw_json, target_identities, configmap_raw_json):
    """List of state paths to vault-delete (Phase B iteration list).

    Stateless filter: full validation + diff computation inside.

    Args:
        vault_raw_json: ignored — kept for shape consistency with other Layer 3 filters.
        target_identities: list — full target from inventory (base + extra).
        configmap_raw_json: str — ConfigMap state raw stdout (may be '', None, malformed).

    Returns:
        list of {path, identity_name} dicts — state paths to vault-delete.

    Raises:
        AnsibleFilterError if anonymous has extra_vault_paths OR paths not unique.
    """
    _validate_anonymous_no_extra(target_identities)
    target_paths = _flatten_target_paths(target_identities)
    _validate_paths_unique(target_paths)
    state = _parse_configmap_state(configmap_raw_json)
    return _compute_state_paths_to_delete(state, target_paths)


def seaweedfs_distribute_paths_to_add(vault_raw_json, target_identities, configmap_raw_json):
    """List of (path, name, accessKey, secretKey) pairs for Phase A vault-put.

    Stateless filter: full validation + creds extraction + pairs computation inside.

    Args:
        vault_raw_json: str — Vault combined JSON (source of identity credentials).
        target_identities: list — full target from inventory (base + extra).
        configmap_raw_json: ignored — kept for shape consistency.

    Returns:
        list of {path, name, accessKey, secretKey} dicts — (identity, path) pairs
        with embedded credentials for Ansible vault-put loop.

    Raises:
        AnsibleFilterError if anonymous has extra_vault_paths OR paths not unique
            OR any identity (with extra_vault_paths) missing in combined JSON.
    """
    _validate_anonymous_no_extra(target_identities)
    target_paths = _flatten_target_paths(target_identities)
    _validate_paths_unique(target_paths)
    combined = _parse_combined_json(vault_raw_json)
    creds_by_name = _extract_creds_by_name(combined)
    _validate_creds_exist(target_identities, creds_by_name)
    return _compute_distribution_pairs(target_identities, creds_by_name)


def seaweedfs_distribute_new_state_json(vault_raw_json, target_identities, configmap_raw_json):
    """JSON-serialized new ConfigMap state for Phase C kubectl apply.

    Stateless filter: full validation + state build + serialize inside.

    Args:
        vault_raw_json: ignored — kept for shape consistency.
        target_identities: list — full target from inventory (base + extra).
        configmap_raw_json: ignored — kept for shape consistency.

    Returns:
        str — JSON-serialized list of {identity_name, vault_paths} entries
        (only identities with non-empty extra_vault_paths), sort_keys=True.

    Raises:
        AnsibleFilterError if anonymous has extra_vault_paths OR paths not unique.
    """
    _validate_anonymous_no_extra(target_identities)
    target_paths = _flatten_target_paths(target_identities)
    _validate_paths_unique(target_paths)
    new_state = _build_new_distribution_state(target_identities)
    return json.dumps(new_state, sort_keys=True)
# =============================================================================
# Private helpers (Layer 2 — bucket-sync)
# =============================================================================
def _compute_bucket_diff(current_state, target_buckets):
    """Compute unified bucket+policy+quota+immutable-violations sync diff.

    Args:
        current_state: list [{name, replication, rack?, dataCenter?, quota?, policy?}, ...] from parsed ConfigMap.
        target_buckets: target list [{name, replication, rack?, dataCenter?, quota?, policy?}, ...].

    Returns:
        {
            'to_delete_buckets': [state entries to remove],
            'kept_policies_to_delete': [target entries — state had policy,
                                        target doesn't],
            'kept_policies_to_apply': [target kept entries with policy],
            'to_create_buckets': [target entries new vs state],
            'new_policies_to_apply': [to_create entries with policy],
            'quotas_to_apply': [target entries with quota defined],
            'immutable_violations': [kept entries where replication, rack, OR dataCenter changed —
                                     used by YAML assert for fail-fast ERROR + abort],
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
        'immutable_violations': _compute_bucket_immutable_violations(current_state, target_buckets),
    }

def _quota_size_to_mib(size_str):
    """Convert human-readable size (e.g. '100GiB') to MiB int.
    Supports MiB / GiB / TiB. Raises AnsibleFilterError for other suffix."""
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

def _validate_principal_not_dict(statement):
    """Raise AnsibleFilterError if Statement.Principal is dict.
    SeaweedFS 4.29 non-AWS limitation — accepts only string or list."""
    principal = statement.get('Principal')
    if isinstance(principal, dict):
        raise AnsibleFilterError(
            "Statement Principal must be flat string or array, not dict "
            "(got {0}). SeaweedFS 4.29 limitation. Use "
            "\"arn:aws:iam::*:user/<name>\" (single), "
            "[\"arn:...\", \"arn:...\"] (multiple), or \"*\" (anonymous).".format(principal)
        )

def _validate_principal_not_dict_in_buckets(target_buckets):
    """Iterate buckets with policy, validate Principal-not-dict per statement.
    Called by every public Layer 2 filter (cheap, idempotent)."""
    for bucket in target_buckets:
        policy = bucket.get('policy')
        if not policy:
            continue
        for statement in policy.get('Statement', []):
            _validate_principal_not_dict(statement)

def _enrich_quotas_with_size_mib(quota_buckets):
    """Return quota buckets list with quota.size_mib int field added.
    quota.enabled=True → compute size_mib from quota.size.
    quota.enabled=False → size_mib=0 (not used by Phase E weed shell)."""
    result = []
    for b in quota_buckets:
        quota = dict(b.get('quota') or {})
        if quota.get('enabled') and 'size' in quota:
            quota['size_mib'] = _quota_size_to_mib(quota['size'])
        else:
            quota['size_mib'] = 0
        new_bucket = dict(b)
        new_bucket['quota'] = quota
        result.append(new_bucket)
    return result

_REPLICATION_FORMAT_RE = re.compile(r'^[0-9]{3}$')

def _validate_replication_format(value):
    """Raise AnsibleFilterError если value не matches '^[0-9]{3}$' regex.
    Used by _validate_buckets_have_replication для каждого bucket."""
    if not isinstance(value, str) or not _REPLICATION_FORMAT_RE.match(value):
        raise AnsibleFilterError(
            "Invalid replication format: '{0}' (type {1}). "
            "Must be 3-digit string matching '^[0-9]{{3}}$'. "
            "Examples: '000' (no rep), '001' (+1 same rack), '100' (+1 other DC), "
            "'205' (8 total copies). See SeaweedFS replication docs.".format(value, type(value).__name__)
        )

def _validate_buckets_have_replication(target_buckets):
    """Iterate target buckets, validate replication (required, 3-digit format)
    и optional rack/dataCenter (non-empty string if present).
    Called by every public Layer 2 filter."""
    for bucket in target_buckets:
        name = bucket.get('name', '<unnamed>')
        replication = bucket.get('replication')
        if replication is None:
            raise AnsibleFilterError(
                "Bucket '{0}' missing required 'replication' field. "
                "Must be 3-digit string. See hosts-vars/seaweedfs-sync.yaml "
                "SECTION 2 schema documentation.".format(name)
            )
        _validate_replication_format(replication)
        for field in ('rack', 'dataCenter'):
            if field in bucket:
                value = bucket.get(field)
                if not isinstance(value, str) or not value:
                    raise AnsibleFilterError(
                        "Bucket '{0}' field '{1}' must be a non-empty string if present. "
                        "See hosts-vars/seaweedfs-sync.yaml SECTION 2 schema "
                        "documentation.".format(name, field)
                    )

def _compute_bucket_immutable_violations(current_state, target_buckets):
    """Compute kept buckets где replication, rack OR dataCenter changed vs state.
    Unified violations list — Used by YAML assert для fail-fast ERROR + abort.

    Returns: list of {name, state_replication, target_replication, state_rack,
    target_rack, state_dataCenter, target_dataCenter} dicts. Empty list = no violations."""
    target_by_name = {b['name']: b for b in target_buckets}
    state_by_name = {b['name']: b for b in current_state}
    kept_names = set(target_by_name) & set(state_by_name)
    violations = []
    for name in kept_names:
        state_entry = state_by_name[name]
        target_entry = target_by_name[name]
        state_replication = state_entry.get('replication')
        target_replication = target_entry.get('replication')
        state_rack = state_entry.get('rack')
        target_rack = target_entry.get('rack')
        state_dc = state_entry.get('dataCenter')
        target_dc = target_entry.get('dataCenter')
        if (state_replication != target_replication
                or state_rack != target_rack
                or state_dc != target_dc):
            violations.append({
                'name': name,
                'state_replication': state_replication,
                'target_replication': target_replication,
                'state_rack': state_rack,
                'target_rack': target_rack,
                'state_dataCenter': state_dc,
                'target_dataCenter': target_dc,
            })
    return violations
# =============================================================================
# Public Layer 2 filters — stateless bucket-sync orchestrators
# =============================================================================
def seaweedfs_buckets_to_delete(vault_raw_json, target_buckets, configmap_raw_json):
    """State entries not in target → list to delete via weed shell s3.bucket.delete.

    Args:
        vault_raw_json: ignored — kept for shape consistency.
        target_buckets: list — full target from inventory (base + extra).
        configmap_raw_json: str — ConfigMap state raw stdout.

    Returns:
        list of state {name, quota?, policy?} entries (state - target).

    Raises:
        AnsibleFilterError if any target bucket has Principal-dict in policy Statement.
    """
    _validate_buckets_have_replication(target_buckets)
    _validate_principal_not_dict_in_buckets(target_buckets)
    state = _parse_configmap_state(configmap_raw_json)
    diff = _compute_bucket_diff(state, target_buckets)
    return diff['to_delete_buckets']


def seaweedfs_bucket_policies_to_delete(vault_raw_json, target_buckets, configmap_raw_json):
    """Kept target buckets без policy that had policy in state → list for delete-bucket-policy.

    Args, Raises: same as seaweedfs_buckets_to_delete.

    Returns:
        list of target {name, quota?} entries (no policy field — that's the point).
    """
    _validate_buckets_have_replication(target_buckets)
    _validate_principal_not_dict_in_buckets(target_buckets)
    state = _parse_configmap_state(configmap_raw_json)
    diff = _compute_bucket_diff(state, target_buckets)
    return diff['kept_policies_to_delete']


def seaweedfs_buckets_to_create(vault_raw_json, target_buckets, configmap_raw_json):
    """New target entries not in state → list to create via weed shell s3.bucket.create.

    Args, Raises: same as above.

    Returns:
        list of target {name, quota?, policy?} entries (target - state).
    """
    _validate_buckets_have_replication(target_buckets)
    _validate_principal_not_dict_in_buckets(target_buckets)
    state = _parse_configmap_state(configmap_raw_json)
    diff = _compute_bucket_diff(state, target_buckets)
    return diff['to_create_buckets']


def seaweedfs_bucket_policies_to_apply(vault_raw_json, target_buckets, configmap_raw_json):
    """All target buckets с policy (kept + new merged) → list for put-bucket-policy.

    Args, Raises: same as above.

    Returns:
        list of target {name, quota?, policy} entries — kept_with_policy ∪ new_with_policy.
        One AWS CLI command (put-bucket-policy) для всех. Phase ordering: после create.
    """
    _validate_buckets_have_replication(target_buckets)
    _validate_principal_not_dict_in_buckets(target_buckets)
    state = _parse_configmap_state(configmap_raw_json)
    diff = _compute_bucket_diff(state, target_buckets)
    return diff['kept_policies_to_apply'] + diff['new_policies_to_apply']


def seaweedfs_buckets_quotas_to_apply(vault_raw_json, target_buckets, configmap_raw_json):
    """Target entries с quota → enriched with quota.size_mib int field.

    Args, Raises: same as above.

    Returns:
        list of {name, quota: {enabled, size, size_mib}, ...} entries — quota.size_mib
        pre-computed int (MiB), ready for Phase E weed shell -sizeMB=<X>.
    """
    _validate_buckets_have_replication(target_buckets)
    _validate_principal_not_dict_in_buckets(target_buckets)
    quota_buckets = [b for b in target_buckets if 'quota' in b]
    return _enrich_quotas_with_size_mib(quota_buckets)


def seaweedfs_buckets_new_state_json(vault_raw_json, target_buckets, configmap_raw_json):
    """JSON-serialized target_buckets for ConfigMap state update (Phase F).

    Args, Raises: same as above.

    Returns:
        str — json.dumps(target_buckets, sort_keys=True). Phase F kubectl apply
        consumes this directly без |to_json.
    """
    _validate_buckets_have_replication(target_buckets)
    _validate_principal_not_dict_in_buckets(target_buckets)
    return json.dumps(target_buckets, sort_keys=True)


def seaweedfs_buckets_immutable_violations(vault_raw_json, target_buckets, configmap_raw_json):
    """Detect immutable settings (replication/rack/dataCenter) changes vs state.

    Stateless filter: full validation + diff computation inside. Used by YAML
    assert для fail-fast ERROR + abort if non-empty list returned.

    Args:
        vault_raw_json: ignored — kept for shape consistency.
        target_buckets: list — full target from inventory (base + extra).
        configmap_raw_json: str — ConfigMap state raw stdout.

    Returns:
        list of {name, state_replication, target_replication, state_rack,
        target_rack, state_dataCenter, target_dataCenter} dicts — kept buckets
        с changed replication, rack, OR dataCenter. Empty = no violations.

    Raises:
        AnsibleFilterError if validation fails (missing replication field,
        invalid replication format, invalid rack/dataCenter, or Principal-dict in policy).
    """
    _validate_buckets_have_replication(target_buckets)
    _validate_principal_not_dict_in_buckets(target_buckets)
    state = _parse_configmap_state(configmap_raw_json)
    return _compute_bucket_immutable_violations(state, target_buckets)
# =============================================================================
# Ansible FilterModule registration
# =============================================================================
class FilterModule(object):
    """Ansible filter plugin entry point — registers all seaweedfs_* filters."""
    def filters(self):
        return {
            'seaweedfs_user_sync_full': seaweedfs_user_sync_full,
            'seaweedfs_distribute_paths_to_delete': seaweedfs_distribute_paths_to_delete,
            'seaweedfs_distribute_paths_to_add': seaweedfs_distribute_paths_to_add,
            'seaweedfs_distribute_new_state_json': seaweedfs_distribute_new_state_json,
            'seaweedfs_buckets_to_delete': seaweedfs_buckets_to_delete,
            'seaweedfs_bucket_policies_to_delete': seaweedfs_bucket_policies_to_delete,
            'seaweedfs_buckets_to_create': seaweedfs_buckets_to_create,
            'seaweedfs_bucket_policies_to_apply': seaweedfs_bucket_policies_to_apply,
            'seaweedfs_buckets_quotas_to_apply': seaweedfs_buckets_quotas_to_apply,
            'seaweedfs_buckets_new_state_json': seaweedfs_buckets_new_state_json,
            'seaweedfs_buckets_immutable_violations': seaweedfs_buckets_immutable_violations,
        }

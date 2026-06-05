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
           - to_create: {name, credentials: [{accessKey, secretKey}], actions, policy_names}.
           - to_update: {name, credentials: preserved from current, actions: target's, policy_names: target's}.
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
            'policy_names': item.get('policy_names', []),
        })
    for item in diff['to_update']:
        name = item['name']
        current_entry = current_by_name.get(name, {})
        existing_creds = current_entry.get('credentials') or [{'accessKey': '', 'secretKey': ''}]
        result.append({
            'name': name,
            'credentials': existing_creds,
            'actions': item.get('actions', []),
            'policy_names': item.get('policy_names', []),
        })
    return json.dumps({'identities': result}, sort_keys=True)


def seaweedfs_identities_to_delete(vault_raw_json, target_identities):
    """Names of identities present in Vault combined JSON but absent from target.

    Stateless filter: parse current Vault combined JSON, diff vs target by name,
    return names to delete via weed shell s3.configure -user=<n> -delete -apply.

    Args:
        vault_raw_json: str — current Vault combined JSON (may be '', None, malformed).
        target_identities: list — full target from inventory (base + extra).

    Returns:
        list of str — identity names present in Vault but not in target (to delete).
    """
    combined = _parse_combined_json(vault_raw_json)
    diff = _compute_identity_diff(combined['identities'], target_identities)
    return [i['name'] for i in diff['to_delete']]


def seaweedfs_combined_json_violations(raw):
    """Validate the Vault combined-JSON key-store before user-sync/distribute consume it.

    Returns a one-element violation list when `raw` is non-empty but does NOT parse
    as a valid combined-JSON object ({"identities": [...]}). Returns [] for empty raw
    (greenfield — vault-get returns '' for a missing path/field, which is legitimate).
    Mirrors the *_immutable_violations pattern: filter returns a list, YAML asserts
    length == 0.

    Why: a malformed key-store, if silently treated as empty by _parse_combined_json,
    would make user-sync re-create ALL identities with FRESH credentials — a silent
    rotation of every consumer's S3 creds. This gate makes that loud instead.

    Args:
        raw: str — Vault combined-JSON field value (may be '', None, malformed).

    Returns:
        list — [] if valid or greenfield-empty, else a one-element [<message str>].
    """
    if raw is None or raw == '':
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return ['Vault combined JSON is not parseable as JSON']
    if not isinstance(data, dict) or 'identities' not in data:
        return ["Vault combined JSON is not an object with an 'identities' key"]
    if not isinstance(data['identities'], list):
        return ["Vault combined JSON 'identities' is not a list"]
    return []
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
# =============================================================================
# Private helpers (Layer 2 — bucket-sync)
# =============================================================================
def _compute_bucket_diff(current_state, target_buckets):
    """Compute unified bucket+owner+quota+immutable-violations sync diff.

    Args:
        current_state: list [{name, replication, rack?, dataCenter?, owner?, quota?}, ...] from parsed ConfigMap.
        target_buckets: target list [{name, replication, rack?, dataCenter?, owner?, quota?}, ...].

    Returns:
        {
            'to_delete_buckets': [state entries to remove],
            'owners_to_set': [kept entries where target owner != state owner —
                              reconcile via s3.bucket.owner],
            'to_create_buckets': [target entries new vs state],
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
        'owners_to_set': [
            target_by_name[n] for n in kept_names
            if target_by_name[n].get('owner') != state_by_name[n].get('owner')
        ],
        'to_create_buckets': [target_by_name[n] for n in target_names - state_names],
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
        list of state {name, owner?, quota?} entries (state - target).

    Raises:
        AnsibleFilterError if any target bucket missing/invalid replication.
    """
    _validate_buckets_have_replication(target_buckets)
    state = _parse_configmap_state(configmap_raw_json)
    diff = _compute_bucket_diff(state, target_buckets)
    return diff['to_delete_buckets']


def seaweedfs_buckets_to_create(vault_raw_json, target_buckets, configmap_raw_json):
    """New target entries not in state → list to create via weed shell s3.bucket.create.

    Args, Raises: same as seaweedfs_buckets_to_delete.

    Returns:
        list of target {name, owner?, quota?} entries (target - state).
    """
    _validate_buckets_have_replication(target_buckets)
    state = _parse_configmap_state(configmap_raw_json)
    diff = _compute_bucket_diff(state, target_buckets)
    return diff['to_create_buckets']


def seaweedfs_buckets_quotas_to_apply(vault_raw_json, target_buckets, configmap_raw_json):
    """Target entries с quota → enriched with quota.size_mib int field.

    Args, Raises: same as seaweedfs_buckets_to_delete.

    Returns:
        list of {name, quota: {enabled, size, size_mib}, ...} entries — quota.size_mib
        pre-computed int (MiB), ready for Phase E weed shell -sizeMB=<X>.
    """
    _validate_buckets_have_replication(target_buckets)
    quota_buckets = [b for b in target_buckets if 'quota' in b]
    return _enrich_quotas_with_size_mib(quota_buckets)


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
        invalid replication format, or invalid rack/dataCenter).
    """
    _validate_buckets_have_replication(target_buckets)
    state = _parse_configmap_state(configmap_raw_json)
    return _compute_bucket_immutable_violations(state, target_buckets)


def seaweedfs_buckets_owners_to_set(vault_raw_json, target_buckets, configmap_raw_json):
    """Kept buckets where target owner differs from state owner → reconcile list.

    Stateless filter: validate + diff + return kept buckets needing s3.bucket.owner.
    New buckets get owner at create time (s3.bucket.create -owner); this covers
    owner changes on already-existing buckets (owner is mutable).

    Args:
        vault_raw_json: ignored — kept for shape consistency.
        target_buckets: list — full target from inventory (base + extra).
        configmap_raw_json: str — ConfigMap state raw stdout.

    Returns:
        list of target {name, owner, ...} entries (kept buckets with changed owner).
    """
    _validate_buckets_have_replication(target_buckets)
    state = _parse_configmap_state(configmap_raw_json)
    diff = _compute_bucket_diff(state, target_buckets)
    return diff['owners_to_set']
# =============================================================================
# Private helpers (Layer P — managed policy sync)
# =============================================================================
def _validate_managed_policies(target_policies):
    """Iterate target managed policies, validate name (required non-empty string)
    + document (required non-empty mapping). Called by every public Layer P filter."""
    for policy in target_policies:
        name = policy.get('name')
        if not isinstance(name, str) or not name:
            raise AnsibleFilterError(
                "Managed policy missing required non-empty string 'name' field "
                "(got {0}). See hosts-vars/seaweedfs-sync.yaml seaweedfs_managed_policies "
                "schema documentation.".format(name)
            )
        document = policy.get('document')
        if not isinstance(document, dict) or not document:
            raise AnsibleFilterError(
                "Managed policy '{0}' missing required non-empty mapping 'document' "
                "field (AWS IAM policy doc). See hosts-vars/seaweedfs-sync.yaml "
                "seaweedfs_managed_policies schema documentation.".format(name)
            )
        if "'" in json.dumps(document):
            raise AnsibleFilterError(
                "Managed policy '{0}' document contains a single quote (') — this "
                "would break shell quoting in tasks-seaweedfs-policy-sync.yaml Phase B "
                "(printf '%s' '<json>'). Remove single quotes from the policy "
                "document.".format(name)
            )


def _compute_policy_diff(current_state, target_policies):
    """Compute managed-policy sync diff by name primary key.

    Args:
        current_state: list [{name, document}, ...] from parsed ConfigMap state.
        target_policies: target list [{name, document}, ...] from inventory.

    Returns:
        {
            'to_put': [all target entries — s3.policy -put idempotent overwrite],
            'to_delete': [state entries not in target — s3.policy -delete],
        }
    """
    target_names = {p['name'] for p in target_policies}
    return {
        'to_put': list(target_policies),
        'to_delete': [p for p in current_state if p.get('name') not in target_names],
    }
# =============================================================================
# Public Layer P filters — stateless managed-policy-sync orchestrators
# =============================================================================
def seaweedfs_policies_to_put(vault_raw_json, target_policies, configmap_raw_json):
    """All target managed policies → list to put via weed shell s3.policy -put.

    Stateless filter: validate + return all target (s3.policy -put is idempotent
    overwrite, so put-all every run is self-healing).

    Args:
        vault_raw_json: ignored — kept for shape consistency with other filters.
        target_policies: list — full target from inventory (base + extra).
        configmap_raw_json: str — ConfigMap state raw stdout (unused for put-all,
            kept for signature consistency).

    Returns:
        list of target {name, document} entries (all target policies).

    Raises:
        AnsibleFilterError if any policy missing name or document.
    """
    _validate_managed_policies(target_policies)
    state = _parse_configmap_state(configmap_raw_json)
    diff = _compute_policy_diff(state, target_policies)
    return diff['to_put']


def seaweedfs_policies_to_delete(vault_raw_json, target_policies, configmap_raw_json):
    """State managed policies not in target → list to delete via weed shell s3.policy -delete.

    Stateless filter: validate + diff + return state-only entries.

    Args:
        vault_raw_json: ignored — kept for shape consistency.
        target_policies: list — full target from inventory (base + extra).
        configmap_raw_json: str — ConfigMap state raw stdout.

    Returns:
        list of state {name, ...} entries (state - target by name).

    Raises:
        AnsibleFilterError if any policy missing name or document.
    """
    _validate_managed_policies(target_policies)
    state = _parse_configmap_state(configmap_raw_json)
    diff = _compute_policy_diff(state, target_policies)
    return diff['to_delete']
# =============================================================================
# Private helpers (per-item ConfigMap state split)
# =============================================================================
_CM_PREFIX_BUCKETS = 'seaweedfs-sync-buckets-'
_CM_PREFIX_POLICIES = 'seaweedfs-sync-policies-'
_CM_PREFIX_DISTRIBUTIONS = 'seaweedfs-sync-identity-distributions-'

_CONFIGMAP_NAME_RE = re.compile(r'^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$')

def _validate_configmap_name(name):
    """Raise AnsibleFilterError if `name` is not a valid RFC 1123 DNS subdomain
    (lowercase alphanumeric, '-', '.'; <=253 chars). Used by *_configmaps_to_apply
    so an invalid bucket/policy/identity name fails fast before any kubectl mutation."""
    if not name or len(name) > 253 or not _CONFIGMAP_NAME_RE.match(name):
        raise AnsibleFilterError(
            "Computed ConfigMap name '{0}' is not a valid RFC 1123 DNS subdomain "
            "(lowercase alphanumeric, '-', '.'; <=253 chars). The bucket/policy/"
            "identity name embedded in it must be DNS-compatible.".format(name)
        )

def _item_configmap_entry(prefix, item_name, stored_dict):
    """Build a single per-item state ConfigMap descriptor {name, content}.
    name = prefix + item_name (validated RFC 1123); content = single-item JSON
    (sort_keys=True) stored verbatim in ConfigMap .data.state."""
    cm_name = prefix + item_name
    _validate_configmap_name(cm_name)
    return {'name': cm_name, 'content': json.dumps(stored_dict, sort_keys=True)}

def _parse_configmaplist(raw):
    """Parse `kubectl get configmap -l <label> -o json` stdout to the .items list.
    Returns [] for empty/missing/malformed input or when items is absent. Never raises."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, dict):
        return []
    items = data.get('items', [])
    return items if isinstance(items, list) else []
# =============================================================================
# Public filters — per-item ConfigMap state split (generic + per-group apply)
# =============================================================================
def seaweedfs_state_configmaps_to_combined_json(configmaplist_raw):
    """Reconstruct combined-array state JSON from a labeled ConfigMapList.

    Stateless generic filter: parse `kubectl get cm -l <label> -o json` stdout,
    collect each item's .data.state (a single-item JSON object string), parse it,
    return json.dumps(list, sort_keys=True). Result is shape-identical to the OLD
    single-ConfigMap .data.state array, so existing diff filters consume it unchanged.

    Args:
        configmaplist_raw: str — `kubectl get cm -l ... -o json` stdout (List kind).
            '' / None / malformed → '[]'. Greenfield ({items:[]}) → '[]'.

    Returns:
        str — JSON array of single-item dicts, sort_keys=True.
    """
    items = _parse_configmaplist(configmaplist_raw)
    out = []
    for item in items:
        state_str = (item.get('data') or {}).get('state')
        if not state_str:
            continue
        try:
            out.append(json.loads(state_str))
        except (ValueError, TypeError):
            continue
    return json.dumps(out, sort_keys=True)


def seaweedfs_state_configmaps_to_delete(configmaplist_raw, target_cm_names):
    """Names of existing state ConfigMaps no longer in target → list to delete.

    Stateless generic filter: existing ConfigMap names (.items[].metadata.name)
    minus target_cm_names set. Prunes per-item state ConfigMaps for buckets/
    policies/identities that dropped out of the target.

    Args:
        configmaplist_raw: str — `kubectl get cm -l ... -o json` stdout.
        target_cm_names: list of str — ConfigMap names that SHOULD exist
            (from <group>_configmaps_to_apply | map(attribute='name')).

    Returns:
        list of str — existing ConfigMap names not in target_cm_names.
    """
    items = _parse_configmaplist(configmaplist_raw)
    target_set = set(target_cm_names or [])
    result = []
    for item in items:
        name = (item.get('metadata') or {}).get('name')
        if name and name not in target_set:
            result.append(name)
    return result


def seaweedfs_buckets_configmaps_to_apply(target_buckets):
    """Per-item state ConfigMap descriptors for buckets.

    Stateless filter: validate (replication presence/format + rack/dataCenter),
    then one {name, content} per target bucket. content = full bucket dict
    (sort_keys=True), stored verbatim.

    Args:
        target_buckets: list — full target from inventory (base + extra).

    Returns:
        list of {name, content} — name='seaweedfs-sync-buckets-<bucket>'.

    Raises:
        AnsibleFilterError on invalid replication or non-DNS bucket name.
    """
    _validate_buckets_have_replication(target_buckets)
    return [_item_configmap_entry(_CM_PREFIX_BUCKETS, b['name'], b) for b in target_buckets]


def seaweedfs_policies_configmaps_to_apply(target_policies):
    """Per-item state ConfigMap descriptors for managed policies.

    Stateless filter: validate (name + document), then one {name, content} per
    target policy. content = full policy dict (sort_keys=True).

    Args:
        target_policies: list — full target from inventory (base + extra).

    Returns:
        list of {name, content} — name='seaweedfs-sync-policies-<policy>'.

    Raises:
        AnsibleFilterError on missing name/document or non-DNS policy name.
    """
    _validate_managed_policies(target_policies)
    return [_item_configmap_entry(_CM_PREFIX_POLICIES, p['name'], p) for p in target_policies]


def seaweedfs_distribute_configmaps_to_apply(target_identities):
    """Per-item state ConfigMap descriptors for identity-distribution.

    Stateless filter: validate (anonymous-no-extra + paths-unique), then one
    {name, content} per identity WITH non-empty extra_vault_paths. content =
    {identity_name, vault_paths} (sort_keys=True) — same shape as one element of
    the distribution state array consumed by seaweedfs_distribute_paths_to_delete.

    Args:
        target_identities: list — full target from inventory (base + extra).

    Returns:
        list of {name, content} — name='seaweedfs-sync-identity-distributions-<identity>'.
        Identities without extra_vault_paths are skipped.

    Raises:
        AnsibleFilterError if anonymous has extra_vault_paths, paths not unique,
        or non-DNS identity name.
    """
    _validate_anonymous_no_extra(target_identities)
    _validate_paths_unique(_flatten_target_paths(target_identities))
    return [
        _item_configmap_entry(
            _CM_PREFIX_DISTRIBUTIONS, i['name'],
            {'identity_name': i['name'], 'vault_paths': i.get('extra_vault_paths', [])},
        )
        for i in target_identities if i.get('extra_vault_paths')
    ]
# =============================================================================
# Ansible FilterModule registration
# =============================================================================
class FilterModule(object):
    """Ansible filter plugin entry point — registers all seaweedfs_* filters."""
    def filters(self):
        return {
            'seaweedfs_user_sync_full': seaweedfs_user_sync_full,
            'seaweedfs_identities_to_delete': seaweedfs_identities_to_delete,
            'seaweedfs_combined_json_violations': seaweedfs_combined_json_violations,
            'seaweedfs_distribute_paths_to_delete': seaweedfs_distribute_paths_to_delete,
            'seaweedfs_distribute_paths_to_add': seaweedfs_distribute_paths_to_add,
            'seaweedfs_buckets_to_delete': seaweedfs_buckets_to_delete,
            'seaweedfs_buckets_to_create': seaweedfs_buckets_to_create,
            'seaweedfs_buckets_quotas_to_apply': seaweedfs_buckets_quotas_to_apply,
            'seaweedfs_buckets_immutable_violations': seaweedfs_buckets_immutable_violations,
            'seaweedfs_buckets_owners_to_set': seaweedfs_buckets_owners_to_set,
            'seaweedfs_policies_to_put': seaweedfs_policies_to_put,
            'seaweedfs_policies_to_delete': seaweedfs_policies_to_delete,
            'seaweedfs_state_configmaps_to_combined_json': seaweedfs_state_configmaps_to_combined_json,
            'seaweedfs_state_configmaps_to_delete': seaweedfs_state_configmaps_to_delete,
            'seaweedfs_buckets_configmaps_to_apply': seaweedfs_buckets_configmaps_to_apply,
            'seaweedfs_policies_configmaps_to_apply': seaweedfs_policies_configmaps_to_apply,
            'seaweedfs_distribute_configmaps_to_apply': seaweedfs_distribute_configmaps_to_apply,
        }

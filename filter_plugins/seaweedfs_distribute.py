"""
SeaweedFS identity-distribute sync — pure Python filter plugin (Layer 3).
Used by playbook-app/tasks/seaweedfs/tasks-seaweedfs-identity-secret-distribute.yaml.
Distributes identity credentials (read from the live filer s3.configure dump) into
operator-configured extra Vault paths; diffs target vs per-item state ConfigMaps.
Self-contained (no cross-file imports — v18 split из монолита seaweedfs_sync.py;
private helper _parse_s3_configure_identities дублируется с seaweedfs_user.py, но в v20
return shape расходится per-file: здесь {name, creds: {access_key: secret_key}} — Layer 3
нужны секреты per key).
Lives in repo-root filter_plugins/; discovered via ansible.cfg
[defaults] filter_plugins = filter_plugins.
"""
import json
import re
try:
    from ansible.errors import AnsibleFilterError
except ImportError:
    # Allow local pytest runs without Ansible installed
    AnsibleFilterError = Exception
# =============================================================================
# Private helpers (NOT registered as public filters)
# =============================================================================
def _parse_s3_configure_identities(raw):
    """Parse `s3.configure` (no-arg) protojson dump → list of normalized identity dicts.
    Returns [] for ''/None/malformed/missing 'identities'. Never raises. Skips isStatic
    identities (managed externally — v17 must not touch them).

    Each entry: {'name', 'creds': {access_key: secret_key, ...}, 'actions': [..],
    'policyNames': [..]}. creds maps EVERY credential's accessKey → secretKey ({} when no
    credentials, e.g. anonymous).
    (v20: reads ALL credentials into a per-key creds map. Helper дублируется в
    seaweedfs_user.py с per-file return shape — намеренно, см. план.)"""
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
        creds = {}
        for c in ident.get('credentials') or []:
            if isinstance(c, dict) and c.get('accessKey'):
                creds[c['accessKey']] = c.get('secretKey', '') or ''
        result.append({
            'name': name,
            'creds': creds,
            'actions': list(ident.get('actions') or []),
            'policyNames': list(ident.get('policyNames') or []),
        })
    return result


def _creds_by_name_from_identities(parsed):
    """{name: {access_key: secret_key, ...}} from _parse_s3_configure_identities output.
    Used by identity-distribute (Layer 3) to read identity credentials from the filer."""
    return {i['name']: i['creds'] for i in parsed}
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
    """Flatten all keys[].vault_paths across identities (preserve order, allow dupes
    — dupes detected by _validate_paths_unique downstream)."""
    paths = []
    for i in identities:
        for k in i.get('keys', []):
            paths.extend(k.get('vault_paths', []))
    return paths

def _validate_anonymous_no_keys_with_paths(identities):
    """Raise AnsibleFilterError if the anonymous identity has any key with vault_paths.
    Anonymous has empty credentials — distribute meaningless."""
    for identity in identities:
        if identity.get('name') != 'anonymous':
            continue
        for k in identity.get('keys', []):
            if k.get('vault_paths'):
                raise AnsibleFilterError(
                    "Anonymous identity has a key with vault_paths — distribute "
                    "impossible (anonymous has empty credentials). Remove vault_paths "
                    "from the anonymous identity's keys in inventory."
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
            "Duplicate Vault paths in identity keys[].vault_paths "
            "across identities: {0}. Each Vault path must be unique "
            "(no race-like inventory bug).".format(dups)
        )

def _validate_creds_exist(target_identities, creds_by_name):
    """Raise AnsibleFilterError if any target key (with vault_paths) refers to an
    (identity, access_key) absent from the filer s3.configure dump."""
    for identity in target_identities:
        name = identity['name']
        for k in identity.get('keys', []):
            if not k.get('vault_paths'):
                continue
            ak = k.get('access_key')
            if name not in creds_by_name or ak not in creds_by_name[name]:
                raise AnsibleFilterError(
                    "Identity '{0}' key '{1}' has vault_paths but is missing from the filer "
                    "s3.configure dump. Run 'ansible-playbook playbook-app/seaweedfs-install.yaml "
                    "--tags user-sync' first.".format(name, ak)
                )

def _compute_distribution_pairs(target_identities, creds_by_name):
    """Build (path, name, accessKey, secretKey) pairs for the Phase A vault-put loop —
    one per (identity, key-with-vault_paths, path). Keys без vault_paths skipped."""
    pairs = []
    for identity in target_identities:
        name = identity['name']
        creds = creds_by_name.get(name, {})
        for k in identity.get('keys', []):
            ak = k.get('access_key')
            for path in k.get('vault_paths', []):
                pairs.append({
                    'path': path,
                    'name': name,
                    'accessKey': ak,
                    'secretKey': creds.get(ak, ''),
                })
    return pairs

def _compute_state_paths_to_delete(state, target_paths):
    """Build list of state paths to vault-delete (not in target paths set). State is the
    per-identity distribution state: [{identity_name, keys: [{access_key, vault_paths}]}]."""
    target_set = set(target_paths)
    deletes = []
    for entry in state:
        identity_name = entry.get('identity_name')
        for k in entry.get('keys', []):
            access_key = k.get('access_key')
            for path in k.get('vault_paths', []):
                if path not in target_set:
                    deletes.append({
                        'path': path,
                        'identity_name': identity_name,
                        'access_key': access_key,
                    })
    return deletes
# =============================================================================
# Public Layer 3 filters — stateless identity-distribute orchestrators
# =============================================================================
def seaweedfs_distribute_paths_to_delete(s3configure_raw, target_identities, configmap_raw_json):
    """List of {path, identity_name, access_key} state paths to vault-delete (Phase B list).

    Stateless filter: full validation + diff computation inside.

    Args:
        s3configure_raw: ignored — kept for shape consistency with other Layer 3 filters.
        target_identities: list — full target from inventory (base + extra).
        configmap_raw_json: str — ConfigMap state raw stdout (may be '', None, malformed).

    Returns:
        list of {path, identity_name, access_key} dicts — state paths to vault-delete.

    Raises:
        AnsibleFilterError if anonymous has a key with vault_paths OR paths not unique.
    """
    _validate_anonymous_no_keys_with_paths(target_identities)
    target_paths = _flatten_target_paths(target_identities)
    _validate_paths_unique(target_paths)
    state = _parse_configmap_state(configmap_raw_json)
    return _compute_state_paths_to_delete(state, target_paths)


def seaweedfs_distribute_paths_to_add(s3configure_raw, target_identities, configmap_raw_json):
    """List of {path, name, accessKey, secretKey} pairs for the Phase A vault-put loop —
    one per (identity, key-with-vault_paths, path).

    Stateless filter: validation + creds extraction + pairs computation inside.
    (v20: per-key — each key carries its own access_key + vault_paths; the secret comes
    from the filer s3.configure dump, not the Vault combined JSON.)

    Args:
        s3configure_raw: str — `s3.configure` dump (source of identity credentials).
        target_identities: list — full target from inventory (base + extra).
        configmap_raw_json: ignored — kept for shape consistency.

    Returns:
        list of {path, name, accessKey, secretKey} dicts.

    Raises:
        AnsibleFilterError if anonymous has a key with vault_paths OR paths not unique OR any
        (identity, access_key) with vault_paths missing from the filer dump."""
    _validate_anonymous_no_keys_with_paths(target_identities)
    target_paths = _flatten_target_paths(target_identities)
    _validate_paths_unique(target_paths)
    parsed = _parse_s3_configure_identities(s3configure_raw)
    creds_by_name = _creds_by_name_from_identities(parsed)
    _validate_creds_exist(target_identities, creds_by_name)
    return _compute_distribution_pairs(target_identities, creds_by_name)

# =============================================================================
# Private helpers (per-item ConfigMap state split)
# =============================================================================
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


def seaweedfs_distribute_configmaps_to_apply(target_identities):
    """Per-item state ConfigMap descriptors for identity-distribution.

    Stateless filter: validate (anonymous-no-keys-with-paths + paths-unique), then one
    {name, content} per identity WITH at least one key carrying vault_paths. content =
    {identity_name, keys: [{access_key, vault_paths}]} (only keys with non-empty vault_paths;
    sort_keys=True) — same shape as one element of the distribution state array consumed by
    seaweedfs_distribute_paths_to_delete.

    Args:
        target_identities: list — full target from inventory (base + extra).

    Returns:
        list of {name, content} — name='seaweedfs-sync-identity-distributions-<identity>'.
        Identities without any key-with-vault_paths are skipped.

    Raises:
        AnsibleFilterError if anonymous has a key with vault_paths, paths not unique,
        or non-DNS identity name.
    """
    _validate_anonymous_no_keys_with_paths(target_identities)
    _validate_paths_unique(_flatten_target_paths(target_identities))
    result = []
    for i in target_identities:
        keys_with_paths = [
            {'access_key': k.get('access_key'), 'vault_paths': k.get('vault_paths', [])}
            for k in i.get('keys', []) if k.get('vault_paths')
        ]
        if keys_with_paths:
            result.append(_item_configmap_entry(
                _CM_PREFIX_DISTRIBUTIONS, i['name'],
                {'identity_name': i['name'], 'keys': keys_with_paths},
            ))
    return result
# =============================================================================
# Ansible FilterModule registration
# =============================================================================
class FilterModule(object):
    """Ansible filter plugin entry point — registers all seaweedfs_* filters."""
    def filters(self):
        return {
            'seaweedfs_distribute_paths_to_delete': seaweedfs_distribute_paths_to_delete,
            'seaweedfs_distribute_paths_to_add': seaweedfs_distribute_paths_to_add,

            'seaweedfs_state_configmaps_to_combined_json': seaweedfs_state_configmaps_to_combined_json,
            'seaweedfs_state_configmaps_to_delete': seaweedfs_state_configmaps_to_delete,
            'seaweedfs_distribute_configmaps_to_apply': seaweedfs_distribute_configmaps_to_apply,
        }

"""
SeaweedFS identity-distribute sync — pure Python filter plugin (Layer 3).
Used by playbook-app/tasks/seaweedfs/tasks-seaweedfs-identity-secret-distribute.yaml.
Distributes identity credentials (read from the live filer s3.configure dump) into
operator-configured extra Vault paths; diffs target vs per-item state ConfigMaps.
Self-contained (no cross-file imports — v18 split из монолита seaweedfs_sync.py;
private helper _parse_s3_configure_identities дублируется с seaweedfs_user.py намеренно,
см. plan §Shared helper).
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

    Each entry: {'name', 'accessKey', 'secretKey', 'actions': [..], 'policyNames': [..]}.
    credentials[0] → AK/SK ('' when credentials empty, e.g. anonymous)."""
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
        first = creds[0] if creds else {}
        result.append({
            'name': name,
            'accessKey': first.get('accessKey', '') or '',
            'secretKey': first.get('secretKey', '') or '',
            'actions': list(ident.get('actions') or []),
            'policyNames': list(ident.get('policyNames') or []),
        })
    return result


def _creds_by_name_from_identities(parsed):
    """{name: {'accessKey','secretKey'}} from _parse_s3_configure_identities output.
    Used by identity-distribute (Layer 3) to read identity credentials from the filer."""
    return {i['name']: {'accessKey': i['accessKey'], 'secretKey': i['secretKey']} for i in parsed}
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
                "Identity '{0}' has extra_vault_paths but is missing from the filer "
                "s3.configure dump. Run 'ansible-playbook playbook-app/seaweedfs-install.yaml "
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
# Public Layer 3 filters — stateless identity-distribute orchestrators
# =============================================================================
def seaweedfs_distribute_paths_to_delete(s3configure_raw, target_identities, configmap_raw_json):
    """List of state paths to vault-delete (Phase B iteration list).

    Stateless filter: full validation + diff computation inside.

    Args:
        s3configure_raw: ignored — kept for shape consistency with other Layer 3 filters.
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


def seaweedfs_distribute_paths_to_add(s3configure_raw, target_identities, configmap_raw_json):
    """List of {path, name, accessKey, secretKey} pairs for the Phase A vault-put loop.

    Stateless filter: validation + creds extraction + pairs computation inside.
    (v17: credentials read from the filer `s3.configure` dump, not the Vault combined JSON.)

    Args:
        s3configure_raw: str — `s3.configure` dump (source of identity credentials).
        target_identities: list — full target from inventory (base + extra).
        configmap_raw_json: ignored — kept for shape consistency.

    Returns:
        list of {path, name, accessKey, secretKey} dicts.

    Raises:
        AnsibleFilterError if anonymous has extra_vault_paths OR paths not unique OR any
        identity (with extra_vault_paths) missing from the filer dump."""
    _validate_anonymous_no_extra(target_identities)
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
            'seaweedfs_distribute_paths_to_delete': seaweedfs_distribute_paths_to_delete,
            'seaweedfs_distribute_paths_to_add': seaweedfs_distribute_paths_to_add,

            'seaweedfs_state_configmaps_to_combined_json': seaweedfs_state_configmaps_to_combined_json,
            'seaweedfs_state_configmaps_to_delete': seaweedfs_state_configmaps_to_delete,
            'seaweedfs_distribute_configmaps_to_apply': seaweedfs_distribute_configmaps_to_apply,
        }

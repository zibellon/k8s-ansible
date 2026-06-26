"""
ArgoCD account-distribute sync — pure Python filter plugin (Layer 3).
Used by playbook-app/tasks/argocd/tasks-argocd-accounts-distribute.yaml.
Distributes ArgoCD account credentials (read from the Vault mirror) into
operator-configured extra Vault paths; diffs target vs per-item state ConfigMaps.
Self-contained (no cross-file imports).
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
def _parse_configmap_state(s):
    """Parse ConfigMap raw state JSON to list. Returns [] for empty/missing/malformed.
    Used for account-distribute state ConfigMaps."""
    if not s:
        return []
    try:
        data = json.loads(s)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    return data

def _flatten_target_paths(accounts):
    """Flatten all vault_paths across accounts (preserve order, allow dupes
    — dupes detected by _validate_paths_unique downstream)."""
    paths = []
    for account in accounts:
        paths.extend(account.get('vault_paths', []))
    return paths

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
            "Duplicate Vault paths in accounts[].vault_paths "
            "across accounts: {0}. Each Vault path must be unique "
            "(no race-like inventory bug).".format(dups)
        )

def _validate_creds_exist(target_accounts, mirror):
    """Raise AnsibleFilterError if any target account (with vault_paths) is absent
    from the Vault mirror or has an empty plaintext credential."""
    for account in target_accounts:
        name = account['name']
        if not account.get('vault_paths'):
            continue
        if name not in mirror or not mirror[name].get('plaintext'):
            raise AnsibleFilterError(
                "Account '{0}' has vault_paths but is missing from the Vault mirror "
                "or has an empty plaintext. Run 'ansible-playbook playbook-app/argocd-install.yaml "
                "--tags accounts-sync' first.".format(name)
            )

def _compute_distribution_pairs(target_accounts, mirror, state):
    """(path, username, password) pairs for the Phase A vault-put loop — only NEW paths
    plus ALL paths of accounts whose passwordMtime changed vs `state`. Accounts without
    vault_paths skipped. `state` = list of {account_name, vault_paths, passwordMtime}."""
    state_by_account = {e.get('account_name'): e for e in state}
    pairs = []
    for account in target_accounts:
        name = account['name']
        paths = account.get('vault_paths', [])
        if not paths:
            continue
        entry = state_by_account.get(name)
        state_paths = set(entry.get('vault_paths', [])) if entry else set()
        rotated = (entry is None) or (entry.get('passwordMtime') != account['passwordMtime'])
        for path in paths:
            if rotated or path not in state_paths:
                pairs.append({
                    'path': path,
                    'username': name,
                    'password': mirror.get(name, {}).get('plaintext', ''),
                })
    return pairs

def _compute_state_paths_to_delete(state, target_paths):
    """Build list of state paths to vault-delete (not in target paths set). State is the
    per-account distribution state: [{account_name, vault_paths}]."""
    target_set = set(target_paths)
    deletes = []
    for entry in state:
        account_name = entry.get('account_name')
        for path in entry.get('vault_paths', []):
            if path not in target_set:
                deletes.append({
                    'path': path,
                    'account_name': account_name,
                })
    return deletes
# =============================================================================
# Private helpers (per-item ConfigMap state split)
# =============================================================================
_CM_PREFIX_DISTRIBUTIONS = 'argocd-accounts-distributions-'

_CONFIGMAP_NAME_RE = re.compile(r'^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$')

def _validate_configmap_name(name):
    """Raise AnsibleFilterError if `name` is not a valid RFC 1123 DNS subdomain
    (lowercase alphanumeric, '-', '.'; <=253 chars). Used by *_configmaps_to_apply
    so an invalid account name fails fast before any kubectl mutation."""
    if not name or len(name) > 253 or not _CONFIGMAP_NAME_RE.match(name):
        raise AnsibleFilterError(
            "Computed ConfigMap name '{0}' is not a valid RFC 1123 DNS subdomain "
            "(lowercase alphanumeric, '-', '.'; <=253 chars). The account "
            "name embedded in it must be DNS-compatible.".format(name)
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
# Public filters — ArgoCD account-distribute + ConfigMap state split
# =============================================================================
def argocd_accounts_distribute_paths_to_add(mirror, target_accounts, configmap_raw_json):
    """List of {path, username, password} pairs for the Phase A vault-put loop.

    Stateless filter: validation + creds extraction + pairs computation inside.

    Args:
        mirror: dict — {name: {plaintext, hash, passwordMtime}} (Vault mirror).
        target_accounts: list — full target accounts from inventory (base + extra).
        configmap_raw_json: str — per-item ConfigMap state (used for change-detection:
            skip paths already distributed with an unchanged passwordMtime).

    Returns:
        list of {path, username, password} dicts.

    Raises:
        AnsibleFilterError if paths not unique OR any account with vault_paths
        missing from the Vault mirror or with empty plaintext.
    """
    _validate_paths_unique(_flatten_target_paths(target_accounts))
    _validate_creds_exist(target_accounts, mirror)
    state = _parse_configmap_state(configmap_raw_json)
    return _compute_distribution_pairs(target_accounts, mirror, state)


def argocd_accounts_distribute_paths_to_delete(mirror, target_accounts, configmap_raw_json):
    """List of {path, account_name} state paths to vault-delete (Phase B list).

    Stateless filter: full validation + diff computation inside.

    Args:
        mirror: ignored — kept for shape consistency with other Layer 3 filters.
        target_accounts: list — full target accounts from inventory (base + extra).
        configmap_raw_json: str — ConfigMap state raw stdout (may be '', None, malformed).

    Returns:
        list of {path, account_name} dicts — state paths to vault-delete.

    Raises:
        AnsibleFilterError if paths not unique.
    """
    _validate_paths_unique(_flatten_target_paths(target_accounts))
    state = _parse_configmap_state(configmap_raw_json)
    return _compute_state_paths_to_delete(state, _flatten_target_paths(target_accounts))


def argocd_accounts_distribute_configmaps_to_apply(target_accounts):
    """Per-item state ConfigMap descriptors for account-distribution.

    Stateless filter: validate (paths-unique), then one {name, content} per account
    WITH non-empty vault_paths. content = {account_name, vault_paths, passwordMtime} (sort_keys=True)
    — same shape as one element of the distribution state array consumed by
    argocd_accounts_distribute_paths_to_delete.

    Args:
        target_accounts: list — full target accounts from inventory (base + extra).

    Returns:
        list of {name, content} — name='argocd-accounts-distributions-<account>'.
        Accounts without vault_paths are skipped.

    Raises:
        AnsibleFilterError if paths not unique or non-DNS account name.
    """
    _validate_paths_unique(_flatten_target_paths(target_accounts))
    result = []
    for account in target_accounts:
        if account.get('vault_paths'):
            result.append(_item_configmap_entry(
                _CM_PREFIX_DISTRIBUTIONS, account['name'],
                {'account_name': account['name'], 'vault_paths': account['vault_paths'],
                 'passwordMtime': account['passwordMtime']},
            ))
    return result


def argocd_accounts_distribute_configmaps_to_apply_changed(target_accounts, configmap_raw_json):
    """Subset of argocd_accounts_distribute_configmaps_to_apply whose desired content
    differs from the current per-item ConfigMap state — new account, changed vault_paths,
    or changed passwordMtime. Phase C applies ONLY these; unchanged accounts' ConfigMaps
    are skipped.
    Args:
        target_accounts: list — full target accounts from inventory.
        configmap_raw_json: str — combined per-item ConfigMap state (may be '', None).
    Returns:
        list of {name, content} — subset of configmaps_to_apply(target_accounts).
    """
    desired = argocd_accounts_distribute_configmaps_to_apply(target_accounts)
    state = _parse_configmap_state(configmap_raw_json)
    stored_by_name = {e.get('account_name'): json.dumps(e, sort_keys=True) for e in state}
    changed = []
    for d in desired:
        account_name = json.loads(d['content']).get('account_name')
        if stored_by_name.get(account_name) != d['content']:
            changed.append(d)
    return changed


def argocd_accounts_state_configmaps_to_combined_json(configmaplist_raw):
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


def argocd_accounts_state_configmaps_to_delete(configmaplist_raw, target_cm_names):
    """Names of existing state ConfigMaps no longer in target → list to delete.

    Stateless generic filter: existing ConfigMap names (.items[].metadata.name)
    minus target_cm_names set. Prunes per-item state ConfigMaps for accounts
    that dropped out of the target.

    Args:
        configmaplist_raw: str — `kubectl get cm -l ... -o json` stdout.
        target_cm_names: list of str — ConfigMap names that SHOULD exist
            (from argocd_accounts_distribute_configmaps_to_apply | map(attribute='name')).

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
# =============================================================================
# Ansible FilterModule registration
# =============================================================================
class FilterModule(object):
    """Ansible filter plugin entry point — registers all argocd_* filters."""
    def filters(self):
        return {
            'argocd_accounts_distribute_paths_to_add': argocd_accounts_distribute_paths_to_add,
            'argocd_accounts_distribute_paths_to_delete': argocd_accounts_distribute_paths_to_delete,
            'argocd_accounts_distribute_configmaps_to_apply': argocd_accounts_distribute_configmaps_to_apply,
            'argocd_accounts_distribute_configmaps_to_apply_changed': argocd_accounts_distribute_configmaps_to_apply_changed,
            'argocd_accounts_state_configmaps_to_combined_json': argocd_accounts_state_configmaps_to_combined_json,
            'argocd_accounts_state_configmaps_to_delete': argocd_accounts_state_configmaps_to_delete,
        }

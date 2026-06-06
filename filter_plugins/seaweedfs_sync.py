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
def _gen_secret(length, charset):
    """Generate random string of `length` chars from `charset` via secrets.choice.
    Cryptographically secure. Mock-friendly: secrets module imported globally.
    """
    return ''.join(secrets.choice(charset) for _ in range(length))


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
# Public Layer 1 filter — stateless user-sync orchestrator
# =============================================================================

def seaweedfs_identities_to_delete(s3configure_raw, target_identities):
    """Identity names present in the filer (s3.configure dump) but absent from target →
    delete via `s3.configure -user=<name> -delete -apply`.
    (v17: reads the live filer dump, not the Vault combined JSON.)"""
    current = _parse_s3_configure_identities(s3configure_raw)
    target_names = {t['name'] for t in target_identities}
    return [c['name'] for c in current if c['name'] not in target_names]


def seaweedfs_identity_actions_to_apply(s3configure_raw, target_identities, *,
                                        access_key_length, secret_key_length,
                                        access_key_charset, secret_key_charset):
    """Per target identity: the ADDITIVE apply payload (creds + actions/policies to add).

    NEW (not in filer) → fresh AK/SK (anonymous → empty), actions_add = target actions,
    policies_attach = target policy_names. EXISTING → creds preserved from the filer dump,
    actions_add = target.actions - filer.actions, policies_attach = target.policy_names - filer.policyNames.

    Returns [{name, accessKey, secretKey, actions_add, policies_attach}] for every target
    identity. The YAML Phase B emits cred/-actions/-policies flags conditionally + a
    'grants-something' when-guard, so a no-op entry is harmless. Removals are a separate
    filter (seaweedfs_identity_actions_to_remove)."""
    current = {i['name']: i for i in _parse_s3_configure_identities(s3configure_raw)}
    result = []
    for t in target_identities:
        name = t['name']
        t_actions = list(t.get('actions') or [])
        t_policies = list(t.get('policy_names') or [])
        cur = current.get(name)
        if cur is None:
            if name == 'anonymous':
                ak, sk = '', ''
            else:
                ak = _gen_secret(access_key_length, access_key_charset)
                sk = _gen_secret(secret_key_length, secret_key_charset)
            actions_add = t_actions
            policies_attach = t_policies
        else:
            ak, sk = cur['accessKey'], cur['secretKey']
            actions_add = [a for a in t_actions if a not in cur['actions']]
            policies_attach = [p for p in t_policies if p not in cur['policyNames']]
        result.append({
            'name': name,
            'accessKey': ak,
            'secretKey': sk,
            'actions_add': actions_add,
            'policies_attach': policies_attach,
        })
    return result


def seaweedfs_identity_actions_to_remove(s3configure_raw, target_identities):
    """Per identity present in BOTH filer and target: filer-extra actions/policies to remove
    via `s3.configure -user=X [-policies=csv][-actions=csv] -delete -apply`.

    Returns [{name, actions_remove, policies_detach}] ONLY where at least one list is
    non-empty (a bare `s3.configure -user=X -delete` would delete the whole user — guard).
    This is the v17 bugfix: removed policies/actions now reach the filer."""
    target_by_name = {t['name']: t for t in target_identities}
    result = []
    for cur in _parse_s3_configure_identities(s3configure_raw):
        t = target_by_name.get(cur['name'])
        if t is None:
            continue
        t_actions = set(t.get('actions') or [])
        t_policies = set(t.get('policy_names') or [])
        actions_remove = [a for a in cur['actions'] if a not in t_actions]
        policies_detach = [p for p in cur['policyNames'] if p not in t_policies]
        if actions_remove or policies_detach:
            result.append({
                'name': cur['name'],
                'actions_remove': actions_remove,
                'policies_detach': policies_detach,
            })
    return result


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
# Private helpers (Layer 2 — bucket-sync)
# =============================================================================
def _compute_bucket_diff(current_state, target_buckets):
    """Compute unified bucket+owner+immutable-violations sync diff.

    Args:
        current_state: list [{name, replication?, rack?, dataCenter?, owner?}, ...] — merged live filer state (fs.configure + s3.bucket.list).
        target_buckets: target list [{name, owner, replication, rack?, dataCenter?, quota_size?}, ...] from inventory.

    Returns:
        {
            'to_delete_buckets': [state entries to remove],
            'owners_to_set': [kept entries where target owner != state owner —
                              reconcile via s3.bucket.owner],
            'to_create_buckets': [target entries new vs state],
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
        'immutable_violations': _compute_bucket_immutable_violations(current_state, target_buckets),
    }

def _parse_fs_configure_locations(raw):
    """Parse `fs.configure` (no-arg) protojson → {bucket_name: {'replication', 'rack'?, 'dataCenter'?}}.
    Only locationPrefix under '/buckets/'; name = suffix (skip bare '/buckets/'). Empty rack/
    dataCenter (protojson EmitUnpopulated) → key absent. {} for ''/None/malformed. Never raises."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    result = {}
    prefix = '/buckets/'
    for loc in data.get('locations') or []:
        if not isinstance(loc, dict):
            continue
        lp = loc.get('locationPrefix', '')
        if not lp.startswith(prefix):
            continue
        name = lp[len(prefix):]
        if not name:
            continue
        entry = {'replication': loc.get('replication', '') or ''}
        if loc.get('rack'):
            entry['rack'] = loc['rack']
        if loc.get('dataCenter'):
            entry['dataCenter'] = loc['dataCenter']
        result[name] = entry
    return result


def _parse_s3_bucket_list(raw):
    """Parse `s3.bucket.list` plain-text stdout → {name: {'owner'?}}. Per line: strip leading
    spaces, split on tab; field 0 = bucket name; owner from the owner:"X" field. (Existence +
    owner only; quota is applied idempotently, not diffed.) {} for ''/None. Never raises."""
    if not raw:
        return {}
    result = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        fields = stripped.split('\t')
        name = fields[0].strip()
        if not name:
            continue
        entry = {}
        for f in fields[1:]:
            f = f.strip()
            if f.startswith('owner:'):
                entry['owner'] = f[len('owner:'):].strip().strip('"')
        result[name] = entry
    return result


def _merge_bucket_state(fs_locs, bucket_list):
    """Merge fs.configure (replication/rack/dc) + s3.bucket.list (existence/owner) → current
    bucket list. Existence = s3.bucket.list (the real /buckets/<n> dirs); each entry enriched
    with replication/rack/dataCenter from fs.configure (absent if no location config)."""
    result = []
    for name, meta in bucket_list.items():
        entry = {'name': name}
        if meta.get('owner') is not None:
            entry['owner'] = meta['owner']
        loc = fs_locs.get(name)
        if loc:
            entry['replication'] = loc.get('replication', '')
            if loc.get('rack'):
                entry['rack'] = loc['rack']
            if loc.get('dataCenter'):
                entry['dataCenter'] = loc['dataCenter']
        result.append(entry)
    return result


def _current_buckets(fs_configure_raw, bucket_list_raw):
    """Merged live filer bucket state from the two filer reads."""
    return _merge_bucket_state(_parse_fs_configure_locations(fs_configure_raw),
                               _parse_s3_bucket_list(bucket_list_raw))


def _quota_size_to_mib(size_str):
    """Convert human-readable size (e.g. '100GiB') to a positive MiB int.
    Supports MiB / GiB / TiB. Raises AnsibleFilterError on a bad unit, a
    non-integer numeric part, or a non-positive value (use an absent quota_size
    to express 'no limit', not '0GiB')."""
    s = str(size_str).strip()
    for suffix, factor in (('MiB', 1), ('GiB', 1024), ('TiB', 1024 * 1024)):
        if s.endswith(suffix):
            num = s[:-3].strip()
            try:
                value = int(num)
            except (ValueError, TypeError):
                raise AnsibleFilterError(
                    "Invalid quota_size '{0}': '{1}' is not an integer.".format(size_str, num)
                )
            if value <= 0:
                raise AnsibleFilterError(
                    "Invalid quota_size '{0}': value must be a positive integer "
                    "(use an absent quota_size for no limit).".format(size_str)
                )
            return value * factor
    raise AnsibleFilterError(
        "Invalid quota_size '{0}': unsupported unit. Use MiB/GiB/TiB "
        "(e.g. '100GiB').".format(size_str)
    )


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
        if not state_replication:
            continue
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
def seaweedfs_buckets_to_delete(fs_configure_raw, bucket_list_raw, target_buckets):
    """Filer buckets (s3.bucket.list) not in target → delete via `s3.bucket.delete`.
    (v17: diffs the live filer.)"""
    _validate_buckets_have_replication(target_buckets)
    return _compute_bucket_diff(_current_buckets(fs_configure_raw, bucket_list_raw),
                                target_buckets)['to_delete_buckets']


def seaweedfs_buckets_to_create(fs_configure_raw, bucket_list_raw, target_buckets):
    """Target buckets not in the filer → create via `s3.bucket.create -owner`. (v17.)"""
    _validate_buckets_have_replication(target_buckets)
    return _compute_bucket_diff(_current_buckets(fs_configure_raw, bucket_list_raw),
                                target_buckets)['to_create_buckets']


def seaweedfs_buckets_owners_to_set(fs_configure_raw, bucket_list_raw, target_buckets):
    """Kept buckets whose target owner differs from the filer owner → reconcile via
    `s3.bucket.owner`. (v17: owner read from s3.bucket.list.)"""
    _validate_buckets_have_replication(target_buckets)
    return _compute_bucket_diff(_current_buckets(fs_configure_raw, bucket_list_raw),
                                target_buckets)['owners_to_set']


def seaweedfs_buckets_immutable_violations(fs_configure_raw, bucket_list_raw, target_buckets):
    """Kept buckets whose immutable replication/rack/dataCenter changed vs the filer
    (fs.configure) → fail-fast list for the YAML assert. Buckets with no fs.configure
    location (replication absent) are skipped. (v17.)"""
    _validate_buckets_have_replication(target_buckets)
    return _compute_bucket_immutable_violations(
        _current_buckets(fs_configure_raw, bucket_list_raw), target_buckets)


def seaweedfs_buckets_quotas_to_apply(target_buckets):
    """All target buckets annotated with _quota_op ('set' if valid quota_size, else 'remove')
    + _quota_size_mib. (v17: quota is applied idempotently every run, no filer read needed.)"""
    _validate_buckets_have_replication(target_buckets)
    result = []
    for b in target_buckets:
        new_bucket = dict(b)
        if 'quota_size' in b:
            new_bucket['_quota_op'] = 'set'
            new_bucket['_quota_size_mib'] = _quota_size_to_mib(b['quota_size'])
        else:
            new_bucket['_quota_op'] = 'remove'
            new_bucket['_quota_size_mib'] = 0
        result.append(new_bucket)
    return result
# =============================================================================
# Private helpers (Layer P — managed policy sync)
# =============================================================================
def _parse_s3_policy_list(raw):
    """Parse `s3.policy -list` plain-text stdout → {name: <document dict>}.
    Format: repeated 'Name: <name>\\nContent: <single-line JSON>\\n---'. json.loads the
    Content; on failure store a sentinel so the diff treats it as changed (self-heal re-put).
    Returns {} for ''/None. Never raises."""
    if not raw:
        return {}
    result = {}
    name = None
    for line in raw.splitlines():
        if line.startswith('Name:'):
            name = line[len('Name:'):].strip()
        elif line.startswith('Content:') and name is not None:
            content = line[len('Content:'):].strip()
            try:
                result[name] = json.loads(content)
            except (ValueError, TypeError):
                result[name] = {'__unparseable__': content}
            name = None
    return result
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


# =============================================================================
# Public Layer P filters — stateless managed-policy-sync orchestrators
# =============================================================================
def seaweedfs_policies_to_put(s3policy_list_raw, target_policies):
    """Target managed policies that are NEW or whose document differs from the filer's
    (semantic dict ==, key-order-insensitive) → put via `s3.policy -put -name -file`.
    (v17: diffs the live filer `s3.policy -list`; only changed/new are re-put, not put-all.)

    Returns [{name, document}]. Validates target via _validate_managed_policies (incl. the
    single-quote shell guard)."""
    _validate_managed_policies(target_policies)
    current = _parse_s3_policy_list(s3policy_list_raw)
    result = []
    for p in target_policies:
        name = p['name']
        if name not in current or current[name] != p['document']:
            result.append({'name': name, 'document': p['document']})
    return result


def seaweedfs_policies_to_delete(s3policy_list_raw, target_policies):
    """Filer policy names not in target → delete via `s3.policy -delete -name`.
    (v17: diffs the live filer `s3.policy -list`.) Returns [{name}]."""
    _validate_managed_policies(target_policies)
    current = _parse_s3_policy_list(s3policy_list_raw)
    target_names = {p['name'] for p in target_policies}
    return [{'name': n} for n in current if n not in target_names]
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
            'seaweedfs_identities_to_delete': seaweedfs_identities_to_delete,
            'seaweedfs_identity_actions_to_apply': seaweedfs_identity_actions_to_apply,
            'seaweedfs_identity_actions_to_remove': seaweedfs_identity_actions_to_remove,
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
            'seaweedfs_distribute_configmaps_to_apply': seaweedfs_distribute_configmaps_to_apply,
        }

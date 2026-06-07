"""
SeaweedFS bucket sync — pure Python filter plugin (Layer 2).
Used by playbook-app/tasks/seaweedfs/tasks-seaweedfs-bucket-sync.yaml.
Diffs the live filer (fs.configure + s3.bucket.list) against the inventory target
(seaweedfs_sync_buckets + _extra) → delete / create / quota-upsert / quota-delete deltas
+ immutable_violations (owner/replication/rack/dataCenter) for the fail-fast assert.
Self-contained (no cross-file imports — v18 split из монолита seaweedfs_sync.py).
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
    """Parse `s3.bucket.list` plain-text stdout → {name: {'owner'?, 'quota_bytes'?}}. Per line:
    strip leading spaces, split on tab; field 0 = bucket name; owner from the owner:"X" field;
    quota from the quota:<bytes> field (present only when the bucket quota > 0). {} for ''/None.
    Never raises."""
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
            elif f.startswith('quota:'):
                try:
                    entry['quota_bytes'] = int(f[len('quota:'):].strip())
                except (ValueError, TypeError):
                    pass
        result[name] = entry
    return result


def _merge_bucket_state(fs_locs, bucket_list):
    """Merge fs.configure (replication/rack/dc) + s3.bucket.list (existence/owner/quota) → current
    bucket list. Existence = s3.bucket.list (the real /buckets/<n> dirs); each entry enriched
    with replication/rack/dataCenter from fs.configure (absent if no location config) and
    quota_bytes from s3.bucket.list (absent if no quota set)."""
    result = []
    for name, meta in bucket_list.items():
        entry = {'name': name}
        if meta.get('owner') is not None:
            entry['owner'] = meta['owner']
        if meta.get('quota_bytes') is not None:
            entry['quota_bytes'] = meta['quota_bytes']
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
    """Raise AnsibleFilterError если value не matches '^[0-9]{3}$' regex."""
    if not isinstance(value, str) or not _REPLICATION_FORMAT_RE.match(value):
        raise AnsibleFilterError(
            "Invalid replication format: '{0}' (type {1}). "
            "Must be 3-digit string matching '^[0-9]{{3}}$'. "
            "Examples: '000' (no rep), '001' (+1 same rack), '100' (+1 other DC), "
            "'205' (8 total copies). See SeaweedFS replication docs.".format(value, type(value).__name__)
        )


def _validate_buckets(target_buckets):
    """Validate each target bucket: replication (required, 3-digit) + rack + dataCenter + owner
    (v18: all REQUIRED non-empty strings). Called by every public Layer 2 filter."""
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
        for field in ('rack', 'dataCenter', 'owner'):
            value = bucket.get(field)
            if not isinstance(value, str) or not value:
                raise AnsibleFilterError(
                    "Bucket '{0}' field '{1}' is REQUIRED (non-empty string in v18). "
                    "See hosts-vars/seaweedfs-sync.yaml SECTION 2 schema "
                    "documentation.".format(name, field)
                )


def _compute_bucket_diff(current_state, target_buckets):
    """Bucket create/delete diff (owner immutable in v18 → no reconcile).
    Returns {to_delete_buckets, to_create_buckets}."""
    target_by_name = {b['name']: b for b in target_buckets}
    state_by_name = {b['name']: b for b in current_state}
    return {
        'to_delete_buckets': [state_by_name[n] for n in set(state_by_name) - set(target_by_name)],
        'to_create_buckets': [target_by_name[n] for n in set(target_by_name) - set(state_by_name)],
    }


def _compute_bucket_immutable_violations(current_state, target_buckets):
    """Kept buckets where an IMMUTABLE field (owner, replication, rack, dataCenter) changed vs
    the filer. Used by the YAML fail-fast assert. Buckets with no fs.configure location
    (replication absent in state) are skipped (Phase C will set it). Returns
    [{name, state_owner, target_owner, state_replication, target_replication, state_rack,
    target_rack, state_dataCenter, target_dataCenter}]."""
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
        if (state_entry.get('owner') != target_entry.get('owner')
                or state_replication != target_entry.get('replication')
                or state_entry.get('rack') != target_entry.get('rack')
                or state_entry.get('dataCenter') != target_entry.get('dataCenter')):
            violations.append({
                'name': name,
                'state_owner': state_entry.get('owner'),
                'target_owner': target_entry.get('owner'),
                'state_replication': state_replication,
                'target_replication': target_entry.get('replication'),
                'state_rack': state_entry.get('rack'),
                'target_rack': target_entry.get('rack'),
                'state_dataCenter': state_entry.get('dataCenter'),
                'target_dataCenter': target_entry.get('dataCenter'),
            })
    return violations


# =============================================================================
# Public Layer 2 filters — stateless bucket-sync orchestrators
# =============================================================================
def seaweedfs_buckets_to_delete(fs_configure_raw, bucket_list_raw, target_buckets):
    """Filer buckets (s3.bucket.list) not in target → delete via `s3.bucket.delete`. (v18.)"""
    _validate_buckets(target_buckets)
    return _compute_bucket_diff(_current_buckets(fs_configure_raw, bucket_list_raw),
                                target_buckets)['to_delete_buckets']


def seaweedfs_buckets_to_create(fs_configure_raw, bucket_list_raw, target_buckets):
    """Target buckets not in the filer → create via `s3.bucket.create -owner`. (v18.)"""
    _validate_buckets(target_buckets)
    return _compute_bucket_diff(_current_buckets(fs_configure_raw, bucket_list_raw),
                                target_buckets)['to_create_buckets']


def seaweedfs_buckets_immutable_violations(fs_configure_raw, bucket_list_raw, target_buckets):
    """Kept buckets whose immutable owner/replication/rack/dataCenter changed vs the filer →
    fail-fast list for the YAML assert. Buckets with no fs.configure location are skipped. (v18.)"""
    _validate_buckets(target_buckets)
    return _compute_bucket_immutable_violations(
        _current_buckets(fs_configure_raw, bucket_list_raw), target_buckets)


def seaweedfs_buckets_quota_to_upsert(fs_configure_raw, bucket_list_raw, target_buckets):
    """Target buckets WITH quota_size whose quota DIFFERS from the live filer →
    [{name, _quota_size_mib}] (s3.bucket.quota -op=set). Current quota read from the filer
    (s3.bucket.list 'quota:<bytes>'; absent → 0); buckets whose quota already matches the
    target are skipped (no-op). New buckets (absent in filer → 0) always differ → emitted.
    (diff-based: only changed/new quotas are re-set.)"""
    _validate_buckets(target_buckets)
    current = {b['name']: b.get('quota_bytes', 0)
               for b in _current_buckets(fs_configure_raw, bucket_list_raw)}
    result = []
    for b in target_buckets:
        if 'quota_size' not in b:
            continue
        target_mib = _quota_size_to_mib(b['quota_size'])
        if current.get(b['name'], 0) != target_mib * 1024 * 1024:
            result.append({'name': b['name'], '_quota_size_mib': target_mib})
    return result


def seaweedfs_buckets_quota_to_delete(fs_configure_raw, bucket_list_raw, target_buckets):
    """Target buckets WITHOUT quota_size that currently HAVE a quota in the live filer →
    [{name}] (s3.bucket.quota -op=remove → unlimited). Current quota read from the filer
    (s3.bucket.list 'quota:<bytes>'); buckets already quota-less are skipped (no-op). Operates
    on target → buckets being deleted (filer-not-target) are naturally excluded.
    (diff-based: only buckets that actually have a quota to drop are emitted.)"""
    _validate_buckets(target_buckets)
    current = {b['name']: b.get('quota_bytes', 0)
               for b in _current_buckets(fs_configure_raw, bucket_list_raw)}
    return [{'name': b['name']} for b in target_buckets
            if 'quota_size' not in b and current.get(b['name'], 0) > 0]


# =============================================================================
# Ansible FilterModule registration
# =============================================================================
class FilterModule(object):
    """Ansible filter plugin entry point — registers seaweedfs bucket filters."""
    def filters(self):
        return {
            'seaweedfs_buckets_to_delete': seaweedfs_buckets_to_delete,
            'seaweedfs_buckets_to_create': seaweedfs_buckets_to_create,
            'seaweedfs_buckets_immutable_violations': seaweedfs_buckets_immutable_violations,
            'seaweedfs_buckets_quota_to_upsert': seaweedfs_buckets_quota_to_upsert,
            'seaweedfs_buckets_quota_to_delete': seaweedfs_buckets_quota_to_delete,
        }

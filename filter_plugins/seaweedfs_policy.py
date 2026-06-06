"""
SeaweedFS managed-policy sync — pure Python filter plugin (Layer P).
Used by playbook-app/tasks/seaweedfs/tasks-seaweedfs-policy-sync.yaml.
Diffs the live filer `s3.policy -list` dump against the inventory target
(seaweedfs_managed_policies + _extra) → put/delete deltas.
Self-contained (no cross-file imports — v18 split из монолита seaweedfs_sync.py).
Lives in repo-root filter_plugins/; discovered via ansible.cfg
[defaults] filter_plugins = filter_plugins.
"""
import json
try:
    from ansible.errors import AnsibleFilterError
except ImportError:
    # Allow local pytest runs without Ansible installed
    AnsibleFilterError = Exception


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
# Ansible FilterModule registration
# =============================================================================
class FilterModule(object):
    """Ansible filter plugin entry point — registers seaweedfs policy filters."""
    def filters(self):
        return {
            'seaweedfs_policies_to_put': seaweedfs_policies_to_put,
            'seaweedfs_policies_to_delete': seaweedfs_policies_to_delete,
        }

"""
ArgoCD local-accounts sync — pure Python filter plugin (Layer 1).
Used by playbook-app/tasks/argocd/tasks-argocd-accounts-sync.yaml.
Diffs desired accounts (argocd_local_accounts) against the Vault mirror and
the live argocd-secret → create / delete / rotate / resync deltas. Secrets
never enter the filter (it computes WHO needs a password; the task generates
plaintext + bcrypt). Lives in repo-root filter_plugins/; discovered via
ansible.cfg [defaults] filter_plugins = filter_plugins.
"""
import base64
import re
try:
    from ansible.errors import AnsibleFilterError
except ImportError:
    AnsibleFilterError = Exception

_NAME_RE = re.compile(r'^[A-Za-z0-9._-]+$')
_RFC3339_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$')
_PREFIX = 'accounts.'
_PW = '.password'
_MTIME = '.passwordMtime'
_CAP_TOKENS = {'login', 'apiKey'}


def _validate_desired(desired):
    """Fail-fast schema check of argocd_local_accounts. Raises AnsibleFilterError."""
    if not isinstance(desired, list):
        raise AnsibleFilterError("argocd_local_accounts must be a list")
    seen = set()
    for item in desired:
        if not isinstance(item, dict):
            raise AnsibleFilterError("argocd_local_accounts: each item must be a mapping")
        name = item.get('name')
        if not isinstance(name, str) or not name:
            raise AnsibleFilterError("argocd_local_accounts: each account needs a non-empty string 'name'")
        if not _NAME_RE.match(name):
            raise AnsibleFilterError("argocd_local_accounts: invalid name %r (allowed: A-Za-z0-9._-)" % name)
        if name in seen:
            raise AnsibleFilterError("argocd_local_accounts: duplicate name %r" % name)
        seen.add(name)
        mtime = item.get('passwordMtime')
        if not isinstance(mtime, str) or not mtime:
            raise AnsibleFilterError("argocd_local_accounts: account %r needs a non-empty 'passwordMtime'" % name)
        if not _RFC3339_RE.match(mtime):
            raise AnsibleFilterError("argocd_local_accounts: account %r passwordMtime %r is not RFC3339" % (name, mtime))
        enabled = item.get('enabled')
        if not isinstance(enabled, bool):
            raise AnsibleFilterError(
                "argocd_local_accounts: account %r needs a boolean 'enabled' (true/false)" % name)
        caps = item.get('capabilities')
        if not isinstance(caps, str) or not caps.strip():
            raise AnsibleFilterError(
                "argocd_local_accounts: account %r needs a non-empty string 'capabilities' "
                "(login / apiKey / 'login, apiKey')" % name)
        tokens = [t.strip() for t in caps.split(',')]
        if not all(tokens) or any(t not in _CAP_TOKENS for t in tokens):
            raise AnsibleFilterError(
                "argocd_local_accounts: account %r capabilities %r invalid; "
                "allowed CSV tokens: login, apiKey" % (name, caps))


def _parse_live_accounts(live_secret_data):
    """Base64 .data map of argocd-secret -> {name: {hash, mtime}}. Ignores every key
    that is not accounts.<name>.password / .passwordMtime (server.secretkey,
    admin.password, accounts.<name>.tokens, etc.). Dotted names handled via
    prefix/suffix stripping (NOT split('.'))."""
    result = {}
    if not isinstance(live_secret_data, dict):
        return result
    for key, b64val in live_secret_data.items():
        if not isinstance(key, str) or not key.startswith(_PREFIX):
            continue
        if key.endswith(_MTIME):           # check Mtime suffix FIRST (more specific)
            name, field = key[len(_PREFIX):-len(_MTIME)], 'mtime'
        elif key.endswith(_PW):
            name, field = key[len(_PREFIX):-len(_PW)], 'hash'
        else:
            continue
        if not name:
            continue
        try:
            val = base64.b64decode(b64val).decode('utf-8')
        except Exception:
            continue
        result.setdefault(name, {})[field] = val
    return result


def argocd_accounts_to_create(desired, vault_mirror):
    """D not in V -> [{name, passwordMtime}] (task generates plaintext+hash)."""
    _validate_desired(desired)
    vm = vault_mirror if isinstance(vault_mirror, dict) else {}
    return [{'name': a['name'], 'passwordMtime': a['passwordMtime']} for a in desired if a['name'] not in vm]


def argocd_accounts_to_delete(desired, vault_mirror, live_secret_data):
    """(V union S) not in D -> sorted [name]. Covers vault-side deletes + S-only strays."""
    _validate_desired(desired)
    vm = vault_mirror if isinstance(vault_mirror, dict) else {}
    live = _parse_live_accounts(live_secret_data)
    dnames = {a['name'] for a in desired}
    return sorted((set(vm.keys()) | set(live.keys())) - dnames)


def argocd_accounts_to_rotate(desired, vault_mirror):
    """D∩V where desired.passwordMtime != vault.passwordMtime -> [{name, passwordMtime}]."""
    _validate_desired(desired)
    vm = vault_mirror if isinstance(vault_mirror, dict) else {}
    out = []
    for a in desired:
        name = a['name']
        if name in vm and a['passwordMtime'] != (vm[name] or {}).get('passwordMtime'):
            out.append({'name': name, 'passwordMtime': a['passwordMtime']})
    return out


def argocd_accounts_to_resync(desired, vault_mirror, live_secret_data):
    """D∩V, mtime equal, but S drifted from V (missing / hash!= / mtime!=)
    -> [{name, hash, passwordMtime}] carrying V's hash+mtime (Vault authoritative, no gen)."""
    _validate_desired(desired)
    vm = vault_mirror if isinstance(vault_mirror, dict) else {}
    live = _parse_live_accounts(live_secret_data)
    out = []
    for a in desired:
        name = a['name']
        if name not in vm:
            continue
        v = vm[name] or {}
        if a['passwordMtime'] != v.get('passwordMtime'):
            continue  # rotation, not resync
        l = live.get(name)
        if l is None or l.get('hash') != v.get('hash') or l.get('mtime') != v.get('passwordMtime'):
            out.append({'name': name, 'hash': v.get('hash'), 'passwordMtime': v.get('passwordMtime')})
    return out


def argocd_accounts_validate(desired):
    """Fail-fast schema validation entrypoint for argocd_local_accounts.
    Runs _validate_desired and returns the list unchanged — for an early
    set_fact in argocd-install.yaml (before the kustomize render)."""
    _validate_desired(desired)
    return desired


class FilterModule(object):
    """Ansible filter plugin entry point — argocd local-accounts sync filters."""
    def filters(self):
        return {
            'argocd_accounts_to_create': argocd_accounts_to_create,
            'argocd_accounts_to_delete': argocd_accounts_to_delete,
            'argocd_accounts_to_rotate': argocd_accounts_to_rotate,
            'argocd_accounts_to_resync': argocd_accounts_to_resync,
            'argocd_accounts_validate': argocd_accounts_validate,
        }

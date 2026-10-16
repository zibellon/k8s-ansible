"""Tests for filter_plugins/teleport_cfg_verify.py.

conftest.py already inserts repo-root filter_plugins/ into sys.path,
so teleport_cfg_verify is importable directly.
"""
import teleport_cfg_verify as tcv


ADMIN_GROUP = 'teleport:cluster-admin'
TELEPORT_NS = 'teleport'


# ---------------------------------------------------------------------------
# Fixtures (local helpers)
# ---------------------------------------------------------------------------
def _role(name, groups=None, resources=None):
    allow = {'kubernetes_groups': [ADMIN_GROUP] if groups is None else groups}
    allow['kubernetes_resources'] = (
        [{'kind': '*', 'api_group': '*', 'namespace': '*', 'name': '*', 'verbs': ['*']}]
        if resources is None else resources)
    return {'name': name, 'spec': {'allow': allow}}


def _user(name, roles):
    return {'name': name, 'spec': {'roles': roles, 'traits': {'logins': [name]}}}


def _crb(name='teleport-managed-cluster-admin', group=ADMIN_GROUP, role='cluster-admin'):
    return {
        'name': name,
        'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'ClusterRole', 'name': role},
        'subjects': [{'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'Group', 'name': group}],
    }


def _rb(name, namespace, group):
    b = _crb(name, group, 'edit')
    b['namespace'] = namespace
    return b


def _verify(roles=None, users=None, cluster_roles=None, crbs=None, rbs=None):
    """Defaults mirror the base structure: superadmin + admin roles, one CRB, user superadmin."""
    return tcv.teleport_cfg_verify(
        roles if roles is not None else [_role('superadmin'), _role('admin')],
        users if users is not None else [_user('superadmin', ['superadmin'])],
        cluster_roles if cluster_roles is not None else [],
        crbs if crbs is not None else [_crb()],
        rbs if rbs is not None else [],
        TELEPORT_NS,
    )


def _live(name, labels=None):
    return {'kind': 'role', 'metadata': {'name': name, 'labels': labels or {}}}


def _managed(name):
    return _live(name, {'teleport.dev/origin': 'kubernetes'})


# ---------------------------------------------------------------------------
# teleport_cfg_verify — happy path
# ---------------------------------------------------------------------------
def test_verify_happy_path_default_structure():
    assert _verify() == []


def test_verify_none_inputs():
    assert tcv.teleport_cfg_verify(None, None, None, None, None, TELEPORT_NS) == []


def test_verify_group_bound_by_rolebinding_only():
    roles = [_role('ns-edit', groups=['teleport:team-edit'])]
    users = [_user('alice', ['ns-edit'])]
    rbs = [_rb('teleport-managed-team-edit', 'team', 'teleport:team-edit')]
    assert _verify(roles=roles, users=users, crbs=[], rbs=rbs) == []


def test_verify_templated_group_is_not_checked_for_binding():
    roles = [_role('traits', groups=['{{internal.kubernetes_groups}}'])]
    assert _verify(roles=roles, users=[], crbs=[]) == []


def test_verify_role_without_kube_access():
    roles = [{'name': 'ssh-only', 'spec': {'allow': {'logins': ['{{internal.logins}}']}}}]
    assert _verify(roles=roles, users=[_user('bob', ['ssh-only'])], crbs=[]) == []


# ---------------------------------------------------------------------------
# teleport_cfg_verify — violations
# ---------------------------------------------------------------------------
def test_verify_system_masters_rejected():
    v = _verify(roles=[_role('superadmin', groups=['system:masters'])])
    assert len(v) == 1 and 'system:masters' in v[0]


def test_verify_unbound_group():
    v = _verify(crbs=[])
    assert len(v) == 2 and all(ADMIN_GROUP in m for m in v)


def test_verify_group_subject_kind_user_does_not_bind_group():
    crb = _crb()
    crb['subjects'] = [{'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'User', 'name': ADMIN_GROUP}]
    assert len(_verify(crbs=[crb])) == 2


def test_verify_resource_without_verbs():
    res = [{'kind': 'pods', 'api_group': '', 'namespace': '*', 'name': '*'}]
    v = _verify(roles=[_role('superadmin', resources=res)])
    assert len(v) == 1 and 'verbs' in v[0]


def test_verify_resource_with_empty_verbs():
    res = [{'kind': 'pods', 'api_group': '', 'namespace': '*', 'name': '*', 'verbs': []}]
    assert len(_verify(roles=[_role('superadmin', resources=res)])) == 1


def test_verify_preset_role_name_rejected():
    v = _verify(roles=[_role('access')], users=[_user('a', ['access'])])
    assert len(v) == 1 and 'preset' in v[0]


def test_verify_user_with_preset_role_rejected():
    v = _verify(users=[_user('aud', ['superadmin', 'auditor'])])
    assert len(v) == 1 and "'auditor'" in v[0]


def test_verify_user_references_undeclared_role():
    v = _verify(users=[_user('alice', ['superadmin', 'ghost'])])
    assert len(v) == 1 and "'ghost'" in v[0]


def test_verify_duplicate_role_names():
    v = _verify(roles=[_role('superadmin'), _role('superadmin')])
    assert len(v) == 1 and 'teleport_cfg_roles' in v[0]


def test_verify_duplicate_user_names():
    users = [_user('alice', ['superadmin']), _user('alice', ['superadmin'])]
    v = _verify(users=users)
    assert len(v) == 1 and 'teleport_cfg_users' in v[0]


def test_verify_duplicate_rolebinding_same_namespace_only():
    rbs = [_rb('teleport-managed-x', 'a', ADMIN_GROUP), _rb('teleport-managed-x', 'b', ADMIN_GROUP)]
    assert _verify(rbs=rbs) == []
    rbs.append(_rb('teleport-managed-x', 'a', ADMIN_GROUP))
    v = _verify(rbs=rbs)
    assert len(v) == 1 and 'a/teleport-managed-x' in v[0]


def test_verify_rbac_name_prefix_required():
    v = _verify(crbs=[_crb(name='cluster-admin-binding')], cluster_roles=[{'name': 'viewer', 'rules': [{}]}])
    assert len(v) == 2
    assert all("'teleport-managed-'" in m for m in v)


def test_verify_duplicate_cluster_role_names():
    crs = [{'name': 'teleport-managed-x', 'rules': [{}]}, {'name': 'teleport-managed-x', 'rules': [{}]}]
    v = _verify(cluster_roles=crs)
    assert len(v) == 1 and 'teleport_cfg_rbac_cluster_roles' in v[0]


def test_verify_duplicate_crb_names():
    v = _verify(crbs=[_crb(), _crb()])
    assert len(v) == 1 and 'teleport_cfg_rbac_cluster_role_bindings' in v[0]


def test_verify_rolebinding_without_namespace():
    rb = _rb('teleport-managed-team-edit', 'team', ADMIN_GROUP)
    del rb['namespace']
    v = _verify(rbs=[rb])
    assert len(v) == 1 and 'namespace' in v[0]


def test_verify_rolebinding_in_teleport_namespace_rejected():
    v = _verify(rbs=[_rb('teleport-managed-x', TELEPORT_NS, ADMIN_GROUP)])
    assert len(v) == 1 and 'namespace самого Teleport' in v[0]


def test_verify_required_fields_bindings():
    crb = _crb()
    del crb['roleRef']
    del crb['subjects']
    v = _verify(crbs=[crb])
    assert any('roleRef' in m for m in v) and any('subjects' in m for m in v)


def test_verify_required_fields_names():
    v = _verify(roles=[_role('superadmin'), {'spec': {}}], users=[_user('superadmin', ['superadmin']), {'spec': {}}])
    assert any('teleport_cfg_roles[1]: отсутствует обязательное поле name' in m for m in v)
    assert any('teleport_cfg_users[1]: отсутствует обязательное поле name' in m for m in v)


def test_verify_cluster_role_without_rules():
    v = _verify(cluster_roles=[{'name': 'teleport-managed-viewer'}])
    assert len(v) == 1 and 'rules' in v[0]


def test_verify_rolebinding_prefix_required():
    v = _verify(rbs=[_rb('edit-binding', 'team', ADMIN_GROUP)])
    assert len(v) == 1 and 'teleport_cfg_rbac_role_bindings[edit-binding]' in v[0]


def test_verify_multiple_violations_collected():
    roles = [_role('access', groups=['system:masters'], resources=[{'kind': 'pods'}])]
    v = _verify(roles=roles, users=[_user('a', ['nope'])], crbs=[])
    assert len(v) == 4


# ---------------------------------------------------------------------------
# teleport_cfg_reconcile_diff
# ---------------------------------------------------------------------------
def test_diff_converged():
    live = [_managed('superadmin'), _managed('admin')]
    d = tcv.teleport_cfg_reconcile_diff(live, [{'name': 'superadmin'}, {'name': 'admin'}])
    assert d == {'missing': [], 'stale': [], 'unowned': [], 'unmanaged': [], 'pending': []}


def test_diff_missing_and_stale():
    live = [_managed('superadmin'), _managed('old')]
    d = tcv.teleport_cfg_reconcile_diff(live, [{'name': 'superadmin'}, {'name': 'admin'}])
    assert d['missing'] == ['admin']
    assert d['stale'] == ['old']
    assert d['pending'] == ['admin', 'old']


def test_diff_unowned_declared_object():
    live = [_live('admin')]
    d = tcv.teleport_cfg_reconcile_diff(live, [{'name': 'admin'}])
    assert d['missing'] == ['admin']
    assert d['unowned'] == ['admin']
    assert d['unmanaged'] == []


def test_diff_builtin_objects_ignored():
    live = [
        _live('access', {'teleport.internal/resource-type': 'preset'}),
        _live('some-system', {'teleport.internal/resource-type': 'system'}),
        _live('bot-operator', {'teleport.internal/bot': 'operator'}),
        _live('operator'),
    ]
    d = tcv.teleport_cfg_reconcile_diff(live, [], ['operator'])
    assert d == {'missing': [], 'stale': [], 'unowned': [], 'unmanaged': [], 'pending': []}


def test_diff_unmanaged_reported():
    live = [_managed('superadmin'), _live('made-in-ui')]
    d = tcv.teleport_cfg_reconcile_diff(live, [{'name': 'superadmin'}], ['operator'])
    assert d['unmanaged'] == ['made-in-ui']
    assert d['pending'] == []


def test_diff_none_inputs():
    d = tcv.teleport_cfg_reconcile_diff(None, None)
    assert d['pending'] == [] and d['unmanaged'] == []


def test_diff_item_without_labels():
    live = [{'kind': 'user', 'metadata': {'name': 'x'}}]
    assert tcv.teleport_cfg_reconcile_diff(live, [])['unmanaged'] == ['x']


def test_find_duplicates_order_preserved():
    assert tcv._find_duplicates(['a', 'b', 'a', 'c', 'b', 'a']) == ['a', 'b']

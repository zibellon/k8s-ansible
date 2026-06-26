"""Regression guard for the passlib bcrypt backend used by Ansible's
password_hash('bcrypt') filter (filestash admin password auto-generation).

passlib 1.7.x fails to load its bcrypt backend with bcrypt >= 4.1, so bcrypt
is pinned <4.1 in tests/Dockerfile. This test fails loudly if that pin
regresses: bcrypt.hash() would raise, and the format asserts guard Go
compatibility (any $2 minor, 60 chars → passes Filestash /healthz auth.admin
length check)."""


def test_passlib_bcrypt_backend_produces_go_compatible_hash():
    from passlib.hash import bcrypt

    h = bcrypt.using(rounds=12).hash("filestash-admin-regression-guard")
    assert h.startswith("$2"), f"unexpected bcrypt ident: {h[:4]}"
    assert len(h) == 60, f"unexpected bcrypt hash length: {len(h)}"
    assert bcrypt.verify("filestash-admin-regression-guard", h)

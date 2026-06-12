"""Tests for filter_plugins/password_generate.py.

conftest.py already inserts repo-root filter_plugins/ into sys.path,
so password_generate is importable directly.
"""
import pytest
from ansible.errors import AnsibleFilterError
import password_generate as pg

D = "0123456789"
L = "abcdefghijklmnopqrstuvwxyz"
S = "!@#%^&*-_=+"


# ---------------------------------------------------------------------------
# 1. Length == sum of counts
# ---------------------------------------------------------------------------
def test_length_4_6_6_4():
    result = pg.password_generate(4, 6, 6, 4, D, L, S)
    assert len(result) == 20


def test_length_8_12_12_0():
    result = pg.password_generate(8, 12, 12, 0, D, L, S)
    assert len(result) == 32


def test_length_1_1_1_1():
    result = pg.password_generate(1, 1, 1, 1, D, L, S)
    assert len(result) == 4


# ---------------------------------------------------------------------------
# 2. Exact class counts (200 iterations)
# ---------------------------------------------------------------------------
def test_exact_class_counts():
    for _ in range(200):
        pw = pg.password_generate(4, 6, 6, 4, D, L, S)
        assert sum(c.isdigit() for c in pw) == 4
        assert sum(c.isupper() for c in pw) == 6
        assert sum(c.islower() for c in pw) == 6
        assert sum(c in S for c in pw) == 4


# ---------------------------------------------------------------------------
# 3. All symbols from allowed set
# ---------------------------------------------------------------------------
def test_all_symbols_from_allowed_set():
    allowed = set(D) | {x.upper() for x in L} | {x.lower() for x in L} | set(S)
    for _ in range(20):
        pw = pg.password_generate(4, 6, 6, 4, D, L, S)
        for ch in pw:
            assert ch in allowed


# ---------------------------------------------------------------------------
# 4. count_special=0 → no special chars
# ---------------------------------------------------------------------------
def test_no_special_when_count_zero():
    special_set = set(S)
    for _ in range(20):
        pw = pg.password_generate(8, 12, 12, 0, D, L, S)
        for ch in pw:
            assert ch not in special_set


# ---------------------------------------------------------------------------
# 5. Case derivation from charset_letters
# ---------------------------------------------------------------------------
def test_case_derivation_upper():
    for _ in range(20):
        pw = pg.password_generate(0, 3, 0, 0, D, "abc", S)
        assert len(pw) == 3
        for ch in pw:
            assert ch in {"A", "B", "C"}


def test_case_derivation_lower():
    for _ in range(20):
        pw = pg.password_generate(0, 0, 3, 0, D, "abc", S)
        assert len(pw) == 3
        for ch in pw:
            assert ch in {"a", "b", "c"}


# ---------------------------------------------------------------------------
# 6. Error: negative count
# ---------------------------------------------------------------------------
def test_error_negative_count():
    with pytest.raises(AnsibleFilterError):
        pg.password_generate(-1, 1, 1, 1, D, L, S)


# ---------------------------------------------------------------------------
# 7. Error: count_digits > 0 with empty charset_digits
# ---------------------------------------------------------------------------
def test_error_empty_charset_digits():
    with pytest.raises(AnsibleFilterError):
        pg.password_generate(4, 0, 0, 0, "", L, S)


# ---------------------------------------------------------------------------
# 8. Error: count_upper > 0 with empty charset_letters
# ---------------------------------------------------------------------------
def test_error_empty_charset_letters_upper():
    with pytest.raises(AnsibleFilterError):
        pg.password_generate(0, 1, 0, 0, D, "", S)


# ---------------------------------------------------------------------------
# 9. Error: count_special > 0 with empty charset_special
# ---------------------------------------------------------------------------
def test_error_empty_charset_special():
    with pytest.raises(AnsibleFilterError):
        pg.password_generate(0, 0, 0, 1, D, L, "")


# ---------------------------------------------------------------------------
# 10. Error: all counts == 0
# ---------------------------------------------------------------------------
def test_error_all_counts_zero():
    with pytest.raises(AnsibleFilterError):
        pg.password_generate(0, 0, 0, 0, D, L, S)


# ---------------------------------------------------------------------------
# 11. Error: non-int count (float and bool)
# ---------------------------------------------------------------------------
def test_error_float_count():
    with pytest.raises(AnsibleFilterError):
        pg.password_generate(1.5, 1, 1, 1, D, L, S)


def test_error_bool_count():
    with pytest.raises(AnsibleFilterError):
        pg.password_generate(True, 1, 1, 1, D, L, S)

"""
Password generation filter plugin — cryptographically strong, class-guaranteed.
Generates a password with exact counts of digit / uppercase / lowercase / special
characters, shuffled via secrets.SystemRandom (OS-backed CSPRNG).
Заменяет недетерминированный lookup('password'): каждый класс символов гарантирован
точным счётчиком, позиции перемешиваются случайно.
Used by playbook-app/tasks/tasks-generate-secret.yaml.
Lives in repo-root filter_plugins/ directory. Discovered by Ansible
via ansible.cfg [defaults] filter_plugins = filter_plugins setting
(ansible.cfg in repo root; ansible-playbook always invoked with
cwd=repo root per project convention).
"""
import secrets
from ansible.errors import AnsibleFilterError


def password_generate(count_digits, count_upper, count_lower, count_special,
                      charset_digits, charset_letters, charset_special):
    """Генерирует пароль с детерминированной гарантией классов символов.

    Принимает точные счётчики символов каждого класса и наборы символов.
    Возвращает перемешанную строку длиной == sum(count_*).
    Заглавные и строчные выводятся из charset_letters через .upper()/.lower().

    Args:
        count_digits:    кол-во цифр (int >= 0, не bool).
        count_upper:     кол-во заглавных букв (int >= 0, не bool).
        count_lower:     кол-во строчных букв (int >= 0, не bool).
        count_special:   кол-во спецсимволов (int >= 0, не bool).
        charset_digits:  строка допустимых цифр.
        charset_letters: строка букв (upper/lower выводятся через .upper()/.lower()).
        charset_special: строка допустимых спецсимволов.
    Returns:
        str: сгенерированный пароль длиной count_digits+count_upper+count_lower+count_special.
    Raises:
        AnsibleFilterError: при нарушении валидации входа.
    """
    for name, val in [('count_digits', count_digits), ('count_upper', count_upper),
                      ('count_lower', count_lower), ('count_special', count_special)]:
        if isinstance(val, bool) or not isinstance(val, int):
            raise AnsibleFilterError(
                "password_generate: {} must be a non-bool integer, got {!r}".format(name, val)
            )
        if val < 0:
            raise AnsibleFilterError(
                "password_generate: {} must be >= 0, got {!r}".format(name, val)
            )

    if count_digits + count_upper + count_lower + count_special < 1:
        raise AnsibleFilterError(
            "password_generate: sum of all counts must be >= 1 (empty password is not allowed)"
        )

    if count_digits > 0 and not charset_digits:
        raise AnsibleFilterError(
            "password_generate: count_digits > 0 but charset_digits is empty"
        )
    if (count_upper > 0 or count_lower > 0) and not charset_letters:
        raise AnsibleFilterError(
            "password_generate: count_upper or count_lower > 0 but charset_letters is empty"
        )
    if count_special > 0 and not charset_special:
        raise AnsibleFilterError(
            "password_generate: count_special > 0 but charset_special is empty"
        )

    upper_pool = [c.upper() for c in charset_letters]
    lower_pool = [c.lower() for c in charset_letters]
    picks  = [secrets.choice(charset_digits)  for _ in range(count_digits)]
    picks += [secrets.choice(upper_pool)      for _ in range(count_upper)]
    picks += [secrets.choice(lower_pool)      for _ in range(count_lower)]
    picks += [secrets.choice(charset_special) for _ in range(count_special)]
    secrets.SystemRandom().shuffle(picks)
    return ''.join(picks)


class FilterModule(object):
    def filters(self):
        return {'password_generate': password_generate}

"""Password strength policy (NIST SP 800-63B aligned).

Composition rules ("one uppercase, one symbol") push users toward
``Password1!`` and are routinely bypassed by attackers who check the
top-10000 list first. The policy here is length-first with a blocklist of
the passwords that actually show up in credential-stuffing wordlists:

- at least ``MIN_LENGTH`` characters (12), and at most ``MAX_LENGTH`` so a
  hostile input cannot be used to burn CPU in bcrypt's key setup;
- not on the blocklist, case-insensitively;
- not purely sequential or a single repeated character;
- not an email address or a derivative of the account name.

Long passphrases with no symbols pass; short ``P@ssw0rd`` does not.
"""

from __future__ import annotations

import re
import unicodedata

#: bcrypt silently truncates at 72 *bytes*. Anything longer is ignored by
#: the comparison, so a 200-character password is really its first 72
#: bytes — cap the input and tell the user rather than pretending the tail
#: adds strength.
MIN_LENGTH = 12
MAX_LENGTH = 128
MAX_BCRYPT_BYTES = 72

#: Normalised (lowercased, leet-speak folded) forms of the passwords that
#: dominate credential-stuffing lists. Kept small on purpose: a local list
#: costs nothing to check and catches the overwhelming majority of weak
#: self-chosen passwords. Deployments can extend it with a downloadable
#: wordlist via ``PASSWORD_BLOCKLIST_PATH``.
_BLOCKLIST = frozenset(
    {
        "password",
        "password1",
        "password123",
        "password1234",
        "passw0rd",
        "p@ssw0rd",
        "p@ssword",
        "passw0rd123",
        "qwerty",
        "qwertyuiop",
        "qwerty123",
        "qwerty12345",
        "123456",
        "12345678",
        "123456789",
        "1234567890",
        "123123",
        "111111",
        "000000",
        "abc123",
        "abcd1234",
        "iloveyou",
        "sunshine",
        "princess",
        "football",
        "baseball",
        "superman",
        "batman",
        "letmein",
        "welcome",
        "welcome1",
        "monkey",
        "dragon",
        "master",
        "login",
        "admin",
        "administrator",
        "root",
        "guest",
        "secret",
        "changeme",
        "contractos",
        "contract",
        "contracts",
    }
)

#: Leet-speak folding so "P@ssw0rd" and "password" collapse together.
_LEET = str.maketrans({"@": "a", "4": "a", "3": "e", "1": "l", "0": "o", "5": "s", "$": "s", "7": "t"})


def _normalize(password: str) -> str:
    """Fold a candidate to its comparable form for blocklist lookups."""
    folded = unicodedata.normalize("NFKD", password)
    # Drop combining marks so "pässword" matches "password".
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return folded.lower().translate(_LEET)


def _is_sequential(password: str) -> bool:
    """True for keyboard-order or arithmetic runs like abcdef or 123456.

    Runs are measured *within* a character class. Measuring across the
    whole string would miss "x1234567890x", because wrapping from '9' to
    '0' is a -9 step in ASCII and breaks the +1 run.
    """
    if len(password) < 4:
        return False

    for is_class in (str.isdigit, str.isalpha):
        run = 0
        previous: int | None = None
        step = 0
        for char in password.lower():
            if not is_class(char):
                previous, run = None, 0
                continue
            current = ord(char)
            if previous is not None:
                delta = current - previous
                if run > 0 and delta == step:
                    run += 1
                else:
                    run = 1
                    step = delta
                if run >= 4:
                    return True
            else:
                run = 1
            previous = current

    return False


def _is_single_repeat(password: str) -> bool:
    return len(set(password)) == 1


def validate_password_strength(
    password: str,
    *,
    email: str | None = None,
    name: str | None = None,
) -> str | None:
    """Return ``None`` when the password is acceptable, else the reason.

    The reason is written to be shown directly to the user in a form field
    error, so it names the fix rather than the rule.
    """
    if password is None:
        return "Password is required."

    if len(password) < MIN_LENGTH:
        return f"Password must be at least {MIN_LENGTH} characters long."

    if len(password) > MAX_LENGTH:
        return f"Password must be at most {MAX_LENGTH} characters long."

    if len(password.encode("utf-8")) > MAX_BCRYPT_BYTES:
        return (
            f"Password is too long to be stored securely "
            f"(limit {MAX_BCRYPT_BYTES} bytes)."
        )

    normalized = _normalize(password)

    if normalized in _BLOCKLIST:
        return "That password appears on the list of passwords attackers try first. Choose something else."

    # Structural checks run on the raw candidate: the leet-speak folding used
    # for blocklist lookups rewrites digits into letters, which would hide a
    # pure numeric run like "123456789".
    if _is_sequential(password):
        return "Password cannot contain a run of consecutive letters or numbers."
    if _is_single_repeat(password):
        return "Password cannot be a single character repeated."

    if email:
        local_part = email.split("@", 1)[0].lower()
        if local_part and len(local_part) >= 4 and local_part in normalized:
            return "Password must not contain your email address."

    if name:
        name_folded = _normalize(name)
        for token in (t for t in re.split(r"\s+", name_folded) if len(t) >= 5):
            if token in normalized:
                return "Password must not contain your name."

    return None


def load_extended_blocklist() -> frozenset[str]:
    """Load operator-supplied extra blocklist entries, if configured.

    Set ``PASSWORD_BLOCKLIST_PATH`` to a newline-delimited file to extend
    the built-in list (e.g. an exported breach corpus). Returns an empty
    set when unset or unreadable — the built-in list still applies.
    """
    import os

    path = os.environ.get("PASSWORD_BLOCKLIST_PATH", "").strip()
    if not path:
        return frozenset()
    try:
        with open(path, encoding="utf-8") as handle:
            return frozenset(
                _normalize(line.strip())
                for line in handle
                if line.strip() and not line.startswith("#")
            )
    except OSError:
        return frozenset()


def check_password(
    password: str,
    *,
    email: str | None = None,
    name: str | None = None,
) -> str | None:
    """Validate against the built-in and (when configured) extra blocklist."""
    reason = validate_password_strength(password, email=email, name=name)
    if reason is not None:
        return reason

    extended = load_extended_blocklist()
    if extended and _normalize(password) in extended:
        return "That password appears in a compromised-credential list. Choose something else."

    return None

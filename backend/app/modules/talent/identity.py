"""Normalisation of contact identifiers for person matching (EPIC-00 spec section 5.2, ADR 0004).

Pure functions with no I/O. Two raw identifiers that normalise to the same string are treated
as the same human, so the rules are deliberately conservative: a rule that is too aggressive
merges two different people, which is far worse than leaving a duplicate.

Both functions return ``None`` for input that cannot be used as an identifier, and both are
idempotent: normalising an already normalised value returns it unchanged.
"""

from __future__ import annotations

import phonenumbers

# Gmail ignores dots and everything after "+" in the local part, and googlemail.com is an alias.
_GMAIL_DOMAINS = frozenset({"gmail.com", "googlemail.com"})
_GMAIL_CANONICAL_DOMAIN = "gmail.com"

# Providers documented to deliver "user+tag@" to "user@" but where dots are significant.
# Add a provider only with evidence (ADR 0004).
_PLUS_TAG_DOMAINS = frozenset({"outlook.com", "hotmail.com", "live.com", "icloud.com"})


def normalize_email(raw: str | None) -> str | None:
    """Return the canonical form of an email address, or ``None`` if it is not usable.

    - Trim surrounding whitespace and lowercase.
    - ``gmail.com`` / ``googlemail.com``: drop dots and any ``+tag`` from the local part and use
      ``gmail.com`` as the domain.
    - ``outlook.com``, ``hotmail.com``, ``live.com``, ``icloud.com``: drop ``+tag`` only.
    - Any other domain: no further change (dots and ``+`` are kept).

    The validity check is structural, not RFC 5322: exactly one ``@``, a non-empty local part, a
    domain made of at least two non-empty dot-separated labels, and no whitespace. A local part
    that is empty after removing the tag (``+jobs@gmail.com``) is invalid.
    """
    if raw is None:
        return None
    email = raw.strip().lower()
    if email.count("@") != 1 or any(ch.isspace() for ch in email):
        return None
    local, domain = email.split("@")
    labels = domain.split(".")
    if not local or len(labels) < 2 or not all(labels):
        return None

    if domain in _GMAIL_DOMAINS:
        local = local.split("+", 1)[0].replace(".", "")
        domain = _GMAIL_CANONICAL_DOMAIN
    elif domain in _PLUS_TAG_DOMAINS:
        local = local.split("+", 1)[0]

    if not local:
        return None
    return f"{local}@{domain}"


def normalize_phone(raw: str | None, default_region: str) -> str | None:
    """Return a phone number in E.164 format (``+919876543210``), or ``None`` if it is not usable.

    ``default_region`` (ISO 3166-1 alpha-2, normally ``settings.PHONE_DEFAULT_REGION``) is used
    for numbers written without a country code, so ``09876543210`` with ``"IN"`` becomes
    ``+919876543210``. Numbers that ``phonenumbers`` cannot parse, or that are not valid for
    their region (wrong length, unassigned prefix), return ``None``.
    """
    if raw is None:
        return None
    try:
        number = phonenumbers.parse(raw, default_region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(number):
        return None
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)

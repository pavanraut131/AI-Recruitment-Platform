"""Normalisation of contact identifiers for person matching (EPIC-00 spec section 5.2, ADR 0004).

Pure functions with no I/O. Two raw identifiers that normalise to the same string are treated
as the same human, so the rules are deliberately conservative: a rule that is too aggressive
merges two different people, which is far worse than leaving a duplicate.

The normalisers return ``None`` for input that cannot be used as an identifier, and are
idempotent: normalising an already normalised value returns it unchanged. :func:`classify_phone`
also says whether a phone is reliable enough to match on (ADR 0004 amendment, option A).
"""

from __future__ import annotations

from dataclasses import dataclass

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


# Where the country code of a parsed number came from. Only these two mean the resume itself
# said which country the number is in ("+91 ...", "0091 ..."); FROM_NUMBER_WITHOUT_PLUS_SIGN is
# phonenumbers guessing that leading digits are the default region's code, and
# FROM_DEFAULT_COUNTRY is the default region filled in for a national number.
_EXPLICIT_COUNTRY_CODE_SOURCES = frozenset(
    {
        phonenumbers.CountryCodeSource.FROM_NUMBER_WITH_PLUS_SIGN,
        phonenumbers.CountryCodeSource.FROM_NUMBER_WITH_IDD,
    }
)
_MOBILE_TYPES = frozenset(
    {phonenumbers.PhoneNumberType.MOBILE, phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE}
)


@dataclass(frozen=True)
class PhoneIdentity:
    """A valid phone number and what is known about how reliably it identifies someone.

    ``match_eligible`` is the ADR 0004 amendment (2026-10-16, option A): a phone may match a
    Person on its own only if the resume wrote its country code explicitly, or if it is a
    mobile number in the default region. Anything else (a landline whose country was only
    inferred from ``PHONE_DEFAULT_REGION``) is stored as ``inferred`` and never matches.
    """

    e164: str
    explicit_country_code: bool
    is_mobile: bool

    @property
    def match_eligible(self) -> bool:
        return self.explicit_country_code or self.is_mobile


def classify_phone(raw: str | None, default_region: str) -> PhoneIdentity | None:
    """Normalise a phone like :func:`normalize_phone` and classify it for matching.

    - ``e164``: the same value :func:`normalize_phone` returns.
    - ``explicit_country_code``: the raw value carried its country code (``+91 ...`` or an
      international dialling prefix such as ``0091 ...``), so the country was not inferred.
    - ``is_mobile``: ``phonenumbers`` types the number as ``MOBILE`` or ``FIXED_LINE_OR_MOBILE``
      (the latter covers regions such as US/CA where the two cannot be told apart).

    Returns ``None`` for anything :func:`normalize_phone` would discard.
    """
    if raw is None:
        return None
    try:
        # keep_raw_input is what makes phonenumbers record country_code_source.
        number = phonenumbers.parse(raw, default_region, keep_raw_input=True)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(number):
        return None
    return PhoneIdentity(
        e164=phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164),
        explicit_country_code=number.country_code_source in _EXPLICIT_COUNTRY_CODE_SOURCES,
        is_mobile=phonenumbers.number_type(number) in _MOBILE_TYPES,
    )


def normalize_phone(raw: str | None, default_region: str) -> str | None:
    """Return a phone number in E.164 format (``+919876543210``), or ``None`` if it is not usable.

    ``default_region`` (ISO 3166-1 alpha-2, normally ``settings.PHONE_DEFAULT_REGION``) is used
    for numbers written without a country code, so ``09876543210`` with ``"IN"`` becomes
    ``+919876543210``. Numbers that ``phonenumbers`` cannot parse, or that are not valid for
    their region (wrong length, unassigned prefix), return ``None``.

    Whether the result may be used for matching is a separate question: see
    :func:`classify_phone`.
    """
    identity = classify_phone(raw, default_region)
    return identity.e164 if identity is not None else None

"""Unit tests for identity normalisation (spec section 5.2; tricky cases from section 6.4).

Pure functions: the table-driven cases pin down each rule, and the Hypothesis properties check
idempotence and that formatting variants collapse to one value. The only fixture loads all of
``phonenumbers``' metadata up front (see :func:`_all_phone_metadata_loaded`).
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from datetime import timedelta

import phonenumbers
import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from app.modules.talent.identity import (
    PhoneIdentity,
    classify_phone,
    normalize_email,
    normalize_phone,
)
from app.platform.config import Settings

# Per-example deadline. With all metadata loaded up front a warm example is well under 1 ms of
# work; the margin covers first-use regex compilation and slow CI runners, not loading anything.
PROPERTY_SETTINGS = settings(max_examples=200, deadline=timedelta(milliseconds=500))


@pytest.fixture(scope="module", autouse=True)
def _all_phone_metadata_loaded() -> Iterator[None]:
    """Load the metadata of every region and non-geographic code before any test runs.

    ``phonenumbers`` imports a region's metadata module (``phonenumbers.data.region_XX``) the
    first time it sees that region. Hypothesis times each example against the deadline, so an
    example that is the first to reach a new country code pays for those imports inside the
    timed call. The phone generators below reach any country code (``\\+[1-9][0-9]{6,14}``), and
    a single ``+1`` number can import up to 25 modules (every region sharing calling code 1).
    Warming only IN, US and GB, as this module used to, left all of that in the timed path.

    That was the E0-13 flake in ``test_normalize_phone_is_idempotent``: reproduced with
    ``--hypothesis-seed=9`` on a loaded machine as ``FlakyFailure: Unreliable test timings! On
    an initial run, this test took 661.64ms, which exceeded the deadline of 500.00ms, but on a
    subsequent run it took 0.68 ms`` for ``raw='+4749782259', region='GB'`` (the first ``+47``
    number: Norway and Svalbard metadata). The property itself held on every input tried.

    What stays in the timed path is CPU work in memory: ``phonenumbers`` compiles a region's
    patterns on first use through ``re``'s bounded cache, which no fixture can pin. Measured
    worst case with that cache purged before every example: about 10 ms, about 100 ms with
    coverage tracing everything, so the 500 ms deadline keeps a wide margin.
    """
    phonenumbers.PhoneMetadata.load_all()
    yield


# ── Email ────────────────────────────────────────────────────────────────────

# ── Email ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Case and surrounding whitespace
        pytest.param("A.Sharma@Example.COM", "a.sharma@example.com", id="case-folded"),
        pytest.param("  asharma@example.com \t\n", "asharma@example.com", id="whitespace-trimmed"),
        pytest.param("ASHARMA@EXAMPLE.COM", "asharma@example.com", id="all-upper"),
        # Gmail: dots and +tag removed
        pytest.param("a.sharma@gmail.com", "asharma@gmail.com", id="gmail-dots"),
        pytest.param("asharma+jobs@gmail.com", "asharma@gmail.com", id="gmail-plus-tag"),
        pytest.param("A.Sharma+Jobs@Gmail.com", "asharma@gmail.com", id="gmail-dots-tag-case"),
        pytest.param("a.s.h.a.r.m.a@gmail.com", "asharma@gmail.com", id="gmail-many-dots"),
        pytest.param("asharma+a+b@gmail.com", "asharma@gmail.com", id="gmail-multiple-plus"),
        pytest.param("a.sharma+x.y@gmail.com", "asharma@gmail.com", id="gmail-dots-in-tag"),
        pytest.param("asharma+@gmail.com", "asharma@gmail.com", id="gmail-empty-tag"),
        # Googlemail is an alias of gmail.com
        pytest.param("a.sharma@googlemail.com", "asharma@gmail.com", id="googlemail-to-gmail"),
        pytest.param("asharma+cv@GoogleMail.com", "asharma@gmail.com", id="googlemail-tag-case"),
        # +tag allowlist: tag removed, dots kept
        pytest.param("a.sharma+jobs@outlook.com", "a.sharma@outlook.com", id="outlook-tag"),
        pytest.param("a.sharma+x@hotmail.com", "a.sharma@hotmail.com", id="hotmail-tag"),
        pytest.param("a.sharma+x@live.com", "a.sharma@live.com", id="live-tag"),
        pytest.param("a.sharma+x+y@icloud.com", "a.sharma@icloud.com", id="icloud-tag"),
        pytest.param("a.sharma@outlook.com", "a.sharma@outlook.com", id="outlook-no-tag"),
        # Other domains: untouched apart from trim + lowercase
        pytest.param("a.sharma+jobs@example.com", "a.sharma+jobs@example.com", id="other-kept"),
        pytest.param("a.sharma@yahoo.com", "a.sharma@yahoo.com", id="yahoo-dots-kept"),
        pytest.param("a+b@gmail.co.in", "a+b@gmail.co.in", id="gmail-lookalike-domain"),
        pytest.param("a.b+c@mail.gmail.com", "a.b+c@mail.gmail.com", id="gmail-subdomain"),
        pytest.param("+jobs@example.com", "+jobs@example.com", id="other-plus-only-local"),
        pytest.param("a.b@outlook.co.uk", "a.b@outlook.co.uk", id="outlook-other-tld"),
        # Invalid -> None
        pytest.param(None, None, id="none"),
        pytest.param("", None, id="empty"),
        pytest.param("   ", None, id="blank"),
        pytest.param("asharma", None, id="no-at"),
        pytest.param("a@b@example.com", None, id="two-ats"),
        pytest.param("@example.com", None, id="empty-local"),
        pytest.param("asharma@", None, id="empty-domain"),
        pytest.param("asharma@localhost", None, id="domain-without-dot"),
        pytest.param("asharma@.com", None, id="domain-leading-dot"),
        pytest.param("asharma@example.", None, id="domain-trailing-dot"),
        pytest.param("asharma@example..com", None, id="domain-empty-label"),
        pytest.param("a sharma@example.com", None, id="inner-space"),
        pytest.param("asharma@exa\tmple.com", None, id="inner-tab"),
        pytest.param("+jobs@gmail.com", None, id="gmail-local-only-tag"),
        pytest.param("...@gmail.com", None, id="gmail-local-only-dots"),
        pytest.param("+tag@outlook.com", None, id="outlook-local-only-tag"),
    ],
)
def test_normalize_email(raw: str | None, expected: str | None) -> None:
    assert normalize_email(raw) == expected


# ── Phone ────────────────────────────────────────────────────────────────────

INDIAN_MOBILE_E164 = "+919876543210"


@pytest.mark.parametrize(
    ("raw", "region", "expected"),
    [
        # The same Indian mobile written in different ways (spec section 6.4)
        pytest.param("+91 98765 43210", "IN", INDIAN_MOBILE_E164, id="in-intl-spaces"),
        pytest.param("09876543210", "IN", INDIAN_MOBILE_E164, id="in-trunk-zero"),
        pytest.param("98765-43210", "IN", INDIAN_MOBILE_E164, id="in-national-dash"),
        pytest.param("+91-98765-43210", "IN", INDIAN_MOBILE_E164, id="in-intl-dashes"),
        pytest.param("(+91) 9876543210", "IN", INDIAN_MOBILE_E164, id="in-intl-parens"),
        pytest.param("9876543210", "IN", INDIAN_MOBILE_E164, id="in-bare-digits"),
        pytest.param("0091 98765 43210", "IN", INDIAN_MOBILE_E164, id="in-idd-prefix"),
        pytest.param("  +91 98765 43210  ", "IN", INDIAN_MOBILE_E164, id="in-whitespace"),
        pytest.param("+919876543210", "IN", INDIAN_MOBILE_E164, id="in-already-e164"),
        # An explicit country code wins over the default region
        pytest.param("+91 98765 43210", "US", INDIAN_MOBILE_E164, id="in-intl-region-us"),
        pytest.param("+1 415 555 2671", "IN", "+14155552671", id="us-intl-region-in"),
        # US number, US region
        pytest.param("(415) 555-2671", "US", "+14155552671", id="us-national"),
        pytest.param("415.555.2671", "US", "+14155552671", id="us-dots"),
        # Without a country code, a national number only makes sense for its own region
        pytest.param("09876543210", "US", None, id="in-trunk-zero-region-us"),
        # Invalid -> None
        pytest.param(None, "IN", None, id="none"),
        pytest.param("", "IN", None, id="empty"),
        pytest.param("   ", "IN", None, id="blank"),
        pytest.param("not a phone", "IN", None, id="garbage-words"),
        pytest.param("n/a", "IN", None, id="garbage-short"),
        pytest.param("12345", "IN", None, id="too-short"),
        pytest.param("+91 98765 4321", "IN", None, id="in-one-digit-short"),
        pytest.param("+91 98765 432100", "IN", None, id="in-one-digit-long"),
        pytest.param("+999 1234 5678", "IN", None, id="unknown-country-code"),
        pytest.param("9876543210", "ZZ", None, id="unknown-region"),
    ],
)
def test_normalize_phone(raw: str | None, region: str, expected: str | None) -> None:
    assert normalize_phone(raw, region) == expected


# ── Phone classification (ADR 0004 amendment, option A) ──────────────────────


@pytest.mark.parametrize(
    ("raw", "region", "expected"),
    [
        # Explicit country code: "+" or an international dialling prefix
        pytest.param(
            "+1 415 555 2671",
            "IN",
            PhoneIdentity("+14155552671", explicit_country_code=True, is_mobile=True),
            id="us-plus",
        ),
        pytest.param(
            "+91 98765 43210",
            "IN",
            PhoneIdentity(INDIAN_MOBILE_E164, explicit_country_code=True, is_mobile=True),
            id="in-mobile-plus",
        ),
        pytest.param(
            "(+91) 9876543210",
            "IN",
            PhoneIdentity(INDIAN_MOBILE_E164, explicit_country_code=True, is_mobile=True),
            id="in-mobile-plus-parens",
        ),
        pytest.param(
            "0091 98765 43210",
            "IN",
            PhoneIdentity(INDIAN_MOBILE_E164, explicit_country_code=True, is_mobile=True),
            id="in-mobile-idd",
        ),
        pytest.param(
            "+91 80 2222 3333",
            "IN",
            PhoneIdentity("+918022223333", explicit_country_code=True, is_mobile=False),
            id="in-landline-plus",
        ),
        pytest.param(
            "011 1 415 555 2671",
            "US",
            PhoneIdentity("+14155552671", explicit_country_code=True, is_mobile=True),
            id="us-idd-from-us",
        ),
        # Region inferred: mobiles stay match-eligible, landlines do not
        pytest.param(
            "9876543210",
            "IN",
            PhoneIdentity(INDIAN_MOBILE_E164, explicit_country_code=False, is_mobile=True),
            id="in-mobile-bare",
        ),
        pytest.param(
            "09876543210",
            "IN",
            PhoneIdentity(INDIAN_MOBILE_E164, explicit_country_code=False, is_mobile=True),
            id="in-mobile-trunk-zero",
        ),
        pytest.param(
            "080 2222 3333",
            "IN",
            PhoneIdentity("+918022223333", explicit_country_code=False, is_mobile=False),
            id="in-landline-bare",
        ),
        # The E0-11 finding: a US number read as a valid Indian landline
        pytest.param(
            "(415) 555-2671",
            "IN",
            PhoneIdentity("+914155552671", explicit_country_code=False, is_mobile=False),
            id="us-national-region-in",
        ),
        # US/CA numbers are FIXED_LINE_OR_MOBILE, which counts as mobile
        pytest.param(
            "(415) 555-2671",
            "US",
            PhoneIdentity("+14155552671", explicit_country_code=False, is_mobile=True),
            id="us-national-region-us",
        ),
        pytest.param(
            "020 7183 8750",
            "GB",
            PhoneIdentity("+442071838750", explicit_country_code=False, is_mobile=False),
            id="gb-landline-bare",
        ),
        # Unusable -> None, exactly as normalize_phone
        pytest.param(None, "IN", None, id="none"),
        pytest.param("not a phone", "IN", None, id="garbage"),
        pytest.param("12345", "IN", None, id="too-short"),
        pytest.param("(415) 555-2671", "GB", None, id="invalid-for-region"),
    ],
)
def test_classify_phone(raw: str | None, region: str, expected: PhoneIdentity | None) -> None:
    assert classify_phone(raw, region) == expected


@pytest.mark.parametrize(
    ("explicit", "mobile", "eligible"),
    [(True, True, True), (True, False, True), (False, True, True), (False, False, False)],
)
def test_phone_is_match_eligible_with_a_country_code_or_as_a_mobile(
    explicit: bool, mobile: bool, eligible: bool
) -> None:
    phone = PhoneIdentity("+918022223333", explicit_country_code=explicit, is_mobile=mobile)
    assert phone.match_eligible is eligible


def test_phone_default_region_setting_defaults_to_india() -> None:
    assert Settings.model_fields["PHONE_DEFAULT_REGION"].default == "IN"


# ── Properties ───────────────────────────────────────────────────────────────

_EMAIL_LOCAL_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-+"
_EMAIL_DOMAIN_SAMPLES = [
    "gmail.com",
    "googlemail.com",
    "GMail.com",
    "outlook.com",
    "hotmail.com",
    "live.com",
    "icloud.com",
    "example.com",
    "company.co.in",
]

_email_like = st.builds(
    lambda local, domain, pad: f"{pad}{local}@{domain}{pad}",
    st.text(_EMAIL_LOCAL_ALPHABET, min_size=1, max_size=20),
    st.sampled_from(_EMAIL_DOMAIN_SAMPLES)
    | st.from_regex(r"[a-z0-9]{1,10}\.[a-z]{2,6}", fullmatch=True),
    st.sampled_from(["", " ", "\t"]),
)


@PROPERTY_SETTINGS
@given(st.one_of(st.text(max_size=40), st.emails(), _email_like))
def test_normalize_email_is_idempotent(raw: str) -> None:
    once = normalize_email(raw)
    if once is not None:
        assert normalize_email(once) == once


_DOTTED_LOCAL = st.from_regex(
    r"[a-z0-9]{1,8}(\.[a-z0-9]{1,8}){1,3}(\+[a-z0-9]{1,6})?", fullmatch=True
)
_RULE_DOMAINS = {
    "gmail.com",
    "googlemail.com",
    "outlook.com",
    "hotmail.com",
    "live.com",
    "icloud.com",
}
_OTHER_DOMAIN = st.from_regex(r"[a-z0-9]{1,10}\.(com|org|io|co\.in)", fullmatch=True).filter(
    lambda domain: domain not in _RULE_DOMAINS
)


@PROPERTY_SETTINGS
@given(_DOTTED_LOCAL, _OTHER_DOMAIN)
def test_other_domains_keep_local_part_dots_and_tags(local: str, domain: str) -> None:
    assert normalize_email(f"{local}@{domain}") == f"{local}@{domain}"


@PROPERTY_SETTINGS
@given(_DOTTED_LOCAL)
def test_gmail_local_part_drops_dots_and_tag(local: str) -> None:
    expected = local.split("+", 1)[0].replace(".", "")
    assert normalize_email(f"{local}@gmail.com") == f"{expected}@gmail.com"


# Ten-digit Indian mobile numbers (they start with 6-9); filtered to those phonenumbers accepts.
_indian_mobile = st.from_regex(r"[6-9][0-9]{9}", fullmatch=True).filter(
    lambda digits: normalize_phone("+91" + digits, "IN") is not None
)


@st.composite
def _formatted_indian_mobile(draw: st.DrawFn, digits: str) -> str:
    """Write ``digits`` the way people do: prefixes, groupings, separators, parentheses."""
    prefix = draw(st.sampled_from(["", "0", "+91", "+91 ", "+91-", "(+91) ", "+91 (0)", "0091 "]))
    cuts = sorted(draw(st.sets(st.integers(min_value=1, max_value=9), max_size=3)))
    groups = [digits[a:b] for a, b in zip([0, *cuts], [*cuts, 10], strict=True)]
    if len(groups) > 1 and draw(st.booleans()):
        groups[0] = f"({groups[0]})"
    body = groups[0]
    for group in groups[1:]:
        body += draw(st.sampled_from([" ", "-", ".", "  ", " - "])) + group
    padding = draw(st.sampled_from(["", " "]))
    return f"{padding}{prefix}{body}{padding}"


@PROPERTY_SETTINGS
@given(st.data(), _indian_mobile)
def test_formatting_variants_of_an_indian_mobile_normalise_to_one_e164(
    data: st.DataObject, digits: str
) -> None:
    written = data.draw(_formatted_indian_mobile(digits), label="written")
    assert normalize_phone(written, "IN") == "+91" + digits


# phonenumbers accepts any Unicode decimal digit; resumes can contain Devanagari, Arabic-Indic
# and full-width digits (and a full-width plus).
_UNICODE_DIGITS = "".join(chr(zero + i) for zero in (0x0966, 0x0660, 0xFF10) for i in range(10))

_phone_like = st.one_of(
    st.text(max_size=30),
    st.text("0123456789 +-()", max_size=20),
    st.text("0123456789 +-" + _UNICODE_DIGITS + "＋", max_size=16),
    st.from_regex(r"\+[1-9][0-9]{6,14}", fullmatch=True),  # any country code
    _indian_mobile.flatmap(_formatted_indian_mobile),  # always valid
)


@PROPERTY_SETTINGS
@given(_phone_like, st.sampled_from(["IN", "US", "GB"]))
# The input of the E0-13 timing flake: the first +47 number to be seen (see the fixture).
@example("+4749782259", "GB")
# The worst case for lazy metadata: a +1 number that matches none of the 25 NANPA regions.
@example("+19995551234", "IN")
# Edge cases worth pinning: extensions are dropped, so the result must still be stable.
@example("+91 98765 43210 ext. 123", "IN")
@example("+1 415 555 2671 x12", "GB")
@example("+ 91 98765 43210", "US")  # space after the plus
@example("९८७६५४३२१०", "IN")  # Devanagari
@example("＋９１９８７６５４３２１０", "US")
@example("9" * 30, "IN")  # far too long
def test_normalize_phone_is_idempotent(raw: str, region: str) -> None:
    once = normalize_phone(raw, region)
    if once is not None:
        assert normalize_phone(once, region) == once
        # E.164 carries its country code, so the default region no longer matters.
        assert normalize_phone(once, "IN") == once


# Not every 10-digit number starting 6-9 is a mobile (some 6x ranges are landlines).
_typed_indian_mobile = _indian_mobile.filter(
    lambda digits: (phone := classify_phone("+91" + digits, "IN")) is not None and phone.is_mobile
)


@PROPERTY_SETTINGS
@given(st.data(), _typed_indian_mobile)
def test_every_written_form_of_an_indian_mobile_is_match_eligible(
    data: st.DataObject, digits: str
) -> None:
    written = data.draw(_formatted_indian_mobile(digits), label="written")
    phone = classify_phone(written, "IN")
    assert phone is not None
    assert phone.e164 == "+91" + digits
    assert phone.match_eligible

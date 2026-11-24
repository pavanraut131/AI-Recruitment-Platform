"""Password hashing and policy (spec §4.2, §10; NIST SP 800-63B)."""

from __future__ import annotations

import pytest

from app.modules.identity import passwords
from app.platform.config import settings


def test_argon2id_with_the_rfc_9106_low_memory_profile() -> None:
    hashed = passwords.hash_password_sync("correct horse battery staple")
    assert hashed.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    assert passwords.verify_password_sync(hashed, "correct horse battery staple")
    assert not passwords.verify_password_sync(hashed, "correct horse battery stapl")
    assert not passwords.verify_password_sync("not a hash", "anything")
    # Salted: the same password hashes differently each time.
    assert passwords.hash_password_sync("correct horse battery staple") != hashed


def test_rehash_when_the_parameters_change(monkeypatch: pytest.MonkeyPatch) -> None:
    hashed = passwords.hash_password_sync("correct horse battery staple")
    assert not passwords.needs_rehash(hashed)
    monkeypatch.setattr(settings, "PASSWORD_HASH_MEMORY_KIB", 32768)
    assert passwords.needs_rehash(hashed)
    assert passwords.needs_rehash("garbage")


async def test_async_helpers_and_the_dummy() -> None:
    hashed = await passwords.hash_password("correct horse battery staple")
    assert await passwords.verify_password(hashed, "correct horse battery staple")
    await passwords.verify_dummy("anything")  # never raises, never succeeds


@pytest.mark.parametrize(
    "password",
    [
        "x" * 12,
        "x" * 128,
        "correct horse battery staple",  # spaces
        "パスワードは十二文字以上です",  # any characters
        "🔐" * 12,
        "all lowercase no digits",  # no composition rules
    ],
)
def test_policy_accepts(password: str) -> None:
    passwords.check_policy(password, email="ada@example.com")


@pytest.mark.parametrize(
    ("password", "message"),
    [
        ("x" * 11, "Use at least 12 characters"),
        ("x" * 129, "Use at most 128 characters"),
        ("Ada@Example.com", "Don't use your email address as your password"),
    ],
)
def test_policy_refuses(password: str, message: str) -> None:
    with pytest.raises(passwords.PasswordPolicyError, match=message):
        passwords.check_policy(password, email="ada@example.com")


def test_long_passwords_are_not_truncated() -> None:
    long = "a" * 100 + "b"
    hashed = passwords.hash_password_sync(long)
    assert not passwords.verify_password_sync(hashed, "a" * 100 + "c")

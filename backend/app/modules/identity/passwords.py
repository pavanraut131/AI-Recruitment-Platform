"""Passwords (EPIC-02 spec §4.2, ASVS V2.1 / V2.4): Argon2id hashing and the password policy.

- **Hashing:** ``argon2-cffi``'s ``PasswordHasher`` with Argon2id and the cost from the settings
  (RFC 9106 low-memory profile by default: 64 MiB, 3 passes, 4 lanes). Hashing and verifying
  take tens of milliseconds of CPU, so the async functions run them in a worker thread and never
  block the event loop. :func:`needs_rehash` tells sign-in to upgrade a hash made with older
  parameters.
- **No enumeration by timing:** :func:`verify_dummy` checks the password against a fixed hash
  made with the current parameters, so sign-in with an unknown email costs what a wrong
  password costs.
- **Policy** (NIST SP 800-63B): 12 to 128 characters, any characters (spaces, emoji), no
  composition rules, not the account's email. Nothing is truncated: Argon2 hashes the whole
  password. No forced rotation.
"""

from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import Final

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.platform.config import settings

MIN_LENGTH: Final = 12
MAX_LENGTH: Final = 128


class PasswordPolicyError(ValueError):
    """The password does not meet the policy; the message says why (shown to the user)."""


def check_policy(password: str, *, email: str | None = None) -> None:
    """Raise :class:`PasswordPolicyError` unless ``password`` meets the policy."""
    if len(password) < MIN_LENGTH:
        raise PasswordPolicyError(f"Use at least {MIN_LENGTH} characters")
    if len(password) > MAX_LENGTH:
        raise PasswordPolicyError(f"Use at most {MAX_LENGTH} characters")
    if email is not None and password.strip().lower() == email.strip().lower():
        raise PasswordPolicyError("Don't use your email address as your password")


@lru_cache(maxsize=4)
def _hasher(memory_kib: int, time_cost: int, parallelism: int) -> PasswordHasher:
    return PasswordHasher(
        time_cost=time_cost,
        memory_cost=memory_kib,
        parallelism=parallelism,
        hash_len=32,
        salt_len=16,
        type=Type.ID,
    )


def hasher() -> PasswordHasher:
    """The hasher for the current settings (cached per parameter set)."""
    return _hasher(
        settings.PASSWORD_HASH_MEMORY_KIB,
        settings.PASSWORD_HASH_TIME_COST,
        settings.PASSWORD_HASH_PARALLELISM,
    )


@lru_cache(maxsize=4)
def _dummy_hash(memory_kib: int, time_cost: int, parallelism: int) -> str:
    return _hasher(memory_kib, time_cost, parallelism).hash("hireflow-dummy-password-for-timing")


def hash_password_sync(password: str) -> str:
    return hasher().hash(password)


def verify_password_sync(password_hash: str, password: str) -> bool:
    try:
        return hasher().verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """Whether ``password_hash`` was made with other parameters than the current ones."""
    try:
        return hasher().check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(hash_password_sync, password)


async def verify_password(password_hash: str, password: str) -> bool:
    return await asyncio.to_thread(verify_password_sync, password_hash, password)


async def verify_dummy(password: str) -> None:
    """Spend the time a real verification takes, for an account that does not exist (or has no
    password). Always fails."""
    dummy = _dummy_hash(
        settings.PASSWORD_HASH_MEMORY_KIB,
        settings.PASSWORD_HASH_TIME_COST,
        settings.PASSWORD_HASH_PARALLELISM,
    )
    await asyncio.to_thread(verify_password_sync, dummy, password)

"""Tenancy shared by every module (EPIC-02 spec §3.2, §6.1, §6.3, §7).

**Tenant context (E2-03).** A database session belongs to at most one agency, and knows which:

- :func:`bind` stores the agency in the session (``session.info``). Bind a session before its
  first transaction; binding it to another agency later is an error (one session, one tenant).
- :func:`agency_id_of` returns the bound agency and **raises** :class:`TenantNotBound` for an
  unbound session, so a repository query without a tenant fails loudly instead of returning
  every agency's rows. Repositories filter every query on a tenant table with
  :func:`tenant_filter` and set ``agency_id`` on every insert from :func:`agency_id_of`.
- An ``after_begin`` listener on SQLAlchemy's ``Session`` class (the sync session behind
  ``AsyncSession`` too, so the API and the Celery workers alike) runs
  ``SELECT set_config('app.agency_id', :agency_id, true)`` at the start of **every**
  transaction of a bound session. ``true`` makes the setting local to the transaction, so it
  ends with the commit or rollback and a pooled connection never carries a tenant into its next
  checkout; a session that commits and goes on (the Celery tasks do, several times) gets the
  setting again at its next ``BEGIN``. Session-level settings of ``app.*`` are forbidden
  anywhere in the code base (``tests/tenancy/test_no_session_level_setting.py`` checks).
  Row-level security reads the setting (E2-04, spec §6.2; :mod:`app.platform.rls`): the
  application role sees and writes only the rows of the transaction's agency, and an unbound
  session sees none. In development and tests, :func:`guard_pool_checkouts` also asserts that
  every connection a pool hands out carries no agency.
- :class:`TenantContext` is "which agency"; :class:`AccessScope` adds which of its clients the
  caller may see (``None``: all of them). Services that list or fetch campaign data take an
  ``AccessScope`` and filter by it (client scoping, E2-06): it is an authorization rule inside
  one tenant, enforced by the application, not by row-level security (spec §5.3).

Operator commands that span agencies (``reset_stuck``, ``storage_orphans``,
``verify_people_applications``, ``backfill_tenancy``) connect as the owner role through
:mod:`app.platform.maintenance` (unbound sessions, the ``maintenance`` policy: every agency) and
call only repository functions that say they read every agency; those never call
:func:`agency_id_of`. As the application role an unbound session would see nothing.

**Transition constants** (E2-01 to E2-12):

- :data:`DEFAULT_AGENCY_ID`: the agency (identity's ``agencies``) that every existing row
  belongs to, named **"Default agency"** (slug ``default``). Until E2-05 every HTTP request is
  bound to it, and ``/api/v1`` stays bound to it until it is removed (E2-12).
- :data:`DEFAULT_CLIENT_ID`: the client (hiring's ``clients``) of the default agency that every
  existing campaign is run for, named **"General"** (spec §15, Q7). Since E2-06 a campaign is
  created for the client its request names (or the caller's default client); the E2-06
  migration gave every other agency a "General" client too.

The values are fixed, so the migration, the code and the runbooks agree without a lookup.
Never reuse them for anything else. They live here, not in ``identity`` or ``hiring``, because
every tenant module needs them and none of them may import ``identity`` (a leaf).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import ColumnElement, Connection, Engine, event, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, Session, SessionTransaction

DEFAULT_AGENCY_ID: Final = uuid.UUID("00000000-0000-4000-8000-00000000a001")
DEFAULT_CLIENT_ID: Final = uuid.UUID("00000000-0000-4000-8000-00000000c001")

SETTING: Final = "app.agency_id"
"""The transaction-local Postgres setting that carries the agency (read by RLS, spec §6.2)."""

_INFO_KEY: Final = "tenant"
# set_config(name, value, is_local=true) is SET LOCAL with bind parameters: no SQL formatting.
_SET_LOCAL = text("SELECT set_config(:name, :agency_id, true)")


class TenantNotBound(RuntimeError):
    """A tenant table was queried through a session that is not bound to an agency."""


class TenantMismatch(RuntimeError):
    """A session or scope was used with a different agency than it is bound to."""


class TenantLeak(RuntimeError):
    """A pooled connection was handed out with an agency still set (a session-level setting)."""


@dataclass(frozen=True, slots=True)
class TenantContext:
    """The agency a unit of work runs for."""

    agency_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class AccessScope:
    """What a caller may see inside its agency: every client (``client_ids=None``) or only the
    listed ones (spec §5.3; an empty set: no client at all). A membership limited to some
    clients gets the second kind (E2-06), and services filter campaign-owned data by it."""

    agency_id: uuid.UUID
    client_ids: frozenset[uuid.UUID] | None = None

    @property
    def all_clients(self) -> bool:
        return self.client_ids is None

    def allows(self, client_id: uuid.UUID) -> bool:
        """Whether a client of this agency is in the scope."""
        return self.client_ids is None or client_id in self.client_ids

    def restricted_to(self, client_ids: Iterable[uuid.UUID] | None) -> AccessScope:
        """This scope narrowed to ``client_ids`` (a request's ``client_id`` filter): their
        intersection with the allowed clients, never more. ``None`` (no filter) keeps it."""
        if client_ids is None:
            return self
        wanted = frozenset(client_ids)
        allowed = wanted if self.client_ids is None else wanted & self.client_ids
        return AccessScope(self.agency_id, allowed)


AnySession = Session | AsyncSession


def _sync(session: AnySession) -> Session:
    return session.sync_session if isinstance(session, AsyncSession) else session


def bind(session: AnySession, agency_id: uuid.UUID) -> None:
    """Bind ``session`` (sync or async) to ``agency_id`` for the rest of its life.

    Binding again to the same agency does nothing. Raises :class:`TenantMismatch` if the
    session is bound to another agency, and ``RuntimeError`` if it is unbound but already in a
    transaction (that transaction would run without the setting): bind before the first query.
    """
    sync = _sync(session)
    current: TenantContext | None = sync.info.get(_INFO_KEY)
    if current is not None:
        if current.agency_id != agency_id:
            raise TenantMismatch(f"session is bound to agency {current.agency_id}, not {agency_id}")
        return
    if sync.in_transaction():
        raise RuntimeError("bind the session to its agency before its first transaction")
    sync.info[_INFO_KEY] = TenantContext(agency_id)


def tenant_of(session: AnySession) -> TenantContext | None:
    """The session's tenant, or ``None`` if it is unbound."""
    tenant: TenantContext | None = _sync(session).info.get(_INFO_KEY)
    return tenant


def agency_id_of(session: AnySession) -> uuid.UUID:
    """The agency ``session`` is bound to; raises :class:`TenantNotBound` if there is none."""
    tenant = tenant_of(session)
    if tenant is None:
        raise TenantNotBound(
            "this session is not bound to an agency (app.platform.tenancy.bind); "
            "tenant tables are never queried without one"
        )
    return tenant.agency_id


def tenant_filter(session: AnySession, column: InstrumentedAttribute[Any]) -> ColumnElement[bool]:
    """``column = <the session's agency>``, for a model's ``agency_id`` column (``NOT NULL``
    since the E2-02 contract). Row-level security applies the same condition underneath; the
    explicit filter keeps the intent readable and the ``(agency_id, …)`` indexes usable."""
    return column == agency_id_of(session)


def require_scope(session: AnySession, scope: AccessScope) -> AccessScope:
    """``scope``, after checking that it is for the agency ``session`` is bound to (a scope of
    another agency is a bug in the caller: :class:`TenantMismatch`)."""
    agency_id = agency_id_of(session)
    if scope.agency_id != agency_id:
        raise TenantMismatch(f"scope of agency {scope.agency_id} on a session of {agency_id}")
    return scope


@event.listens_for(Session, "after_begin")
def _set_transaction_tenant(
    session: Session, transaction: SessionTransaction, connection: Connection
) -> None:
    """Every transaction of a bound session starts with the agency in ``app.agency_id``."""
    tenant: TenantContext | None = session.info.get(_INFO_KEY)
    if tenant is not None:
        connection.execute(_SET_LOCAL, {"name": SETTING, "agency_id": str(tenant.agency_id)})


# ── Pool checkouts (development and tests) ────────────────────
_CHECKOUT_SETTING = f"SELECT current_setting('{SETTING}', true)"


def pooled_setting(dbapi_connection: Any) -> str | None:
    """``app.agency_id`` on a raw DBAPI connection, outside any transaction of its own (the
    statement's implicit transaction is rolled back)."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute(_CHECKOUT_SETTING)
        row = cursor.fetchone()
    finally:
        cursor.close()
        dbapi_connection.rollback()
    value: str | None = row[0] if row else None
    return value


def guard_pool_checkouts(engine: Engine) -> None:
    """Assert, at every checkout from ``engine``'s pool, that the connection carries no agency
    (spec §6.3): a transaction-local setting ends with its transaction, so a non-empty value
    means something set it for the session. Raises :class:`TenantLeak`.

    For development and tests only (``APP_ENV``), where the extra round trip per checkout does
    not matter; the callers decide. Pass ``engine.sync_engine`` for an ``AsyncEngine``. It
    talks to the DBAPI connection directly, so statement-counting listeners never see it.
    """

    @event.listens_for(engine, "checkout")
    def _assert_no_agency(dbapi_connection: Any, record: Any, proxy: Any) -> None:
        value = pooled_setting(dbapi_connection)
        if value:
            raise TenantLeak(
                f"a pooled connection was checked out with {SETTING}={value!r}: something set "
                "it for the session instead of the transaction (set_config(..., true) only)"
            )


def guard_in_development(engine: Engine, app_env: str) -> None:
    """:func:`guard_pool_checkouts` when ``app_env`` is ``development`` or ``test``."""
    if app_env.strip().lower() in {"development", "test"}:
        guard_pool_checkouts(engine)

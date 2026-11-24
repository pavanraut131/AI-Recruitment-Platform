"""Sessions of different agencies on **one** connection (tests only).

The rolled-back test transaction is one connection (``db_connection``, ``sync_connection``,
``sync_session``'s): every session a test opens on it joins that transaction with a savepoint
(``join_transaction_mode="create_savepoint"``). ``app.agency_id`` is transaction-local, so on a
shared connection it is whatever the last session to begin set: after a session of agency B
has run, ``db_session`` (the default agency) would go on with B's setting, and row-level
security would answer for B.

In production a session never shares its connection, and ``app.platform.tenancy`` sets the
agency once per transaction. Here :func:`install` adds Session listeners that keep the
connection's setting equal to the **running** session's agency (``''`` for an unbound session)
before each flush and each statement it executes. It records what it applied in the
connection's ``info``, so a test with one session (the common case) sends no extra statement,
and it sends its statement on the DBAPI cursor, so the statement-counting tests (SQLAlchemy's
``before_cursor_execute``) count exactly what production sends.
"""

from __future__ import annotations

from typing import Any

_APPLIED = "tests.applied_agency_id"
_CONN_INFO = "tests.connection_info"


def install() -> None:
    """Register the listeners (once, at session start: after ``app`` can be imported)."""
    from sqlalchemy import Connection, event
    from sqlalchemy.orm import ORMExecuteState, Session, SessionTransaction

    from app.platform.tenancy import SETTING, tenant_of

    def wanted(session: Session) -> str:
        tenant = tenant_of(session)
        return "" if tenant is None else str(tenant.agency_id)  # a UUID: safe as a literal

    def apply(session: Session, connection: Connection) -> None:
        value = wanted(session)
        if connection.info.get(_APPLIED) != value:
            # set_config(..., true): transaction-local, like app.platform.tenancy's.
            cursor = connection.connection.dbapi_connection.cursor()  # type: ignore[union-attr]
            try:
                cursor.execute(f"SELECT set_config('{SETTING}', '{value}', true)")
            finally:
                cursor.close()
            connection.info[_APPLIED] = value

    @event.listens_for(Session, "after_begin")
    def _record(session: Session, transaction: SessionTransaction, connection: Connection) -> None:
        # Runs after app.platform.tenancy's listener, which set a bound session's agency.
        session.info[_CONN_INFO] = connection.info
        if tenant_of(session) is not None or not connection.in_nested_transaction():
            # Bound: just set. Unbound on its own connection: a new transaction, nothing set.
            connection.info[_APPLIED] = wanted(session)
        else:
            apply(session, connection)  # unbound, joining a transaction another session used

    @event.listens_for(Session, "before_flush")
    def _before_flush(session: Session, *_: Any) -> None:
        apply(session, session.connection())

    @event.listens_for(Session, "do_orm_execute")
    def _before_execute(state: ORMExecuteState) -> None:
        apply(state.session, state.session.connection())

    @event.listens_for(Session, "after_transaction_end")
    def _forget(session: Session, transaction: SessionTransaction) -> None:
        # Ended (committed, rolled back to a savepoint, nested or not, or closed): the
        # connection's setting may have reverted, so the next statement applies it again.
        info = session.info.get(_CONN_INFO)
        if info is not None:
            info.pop(_APPLIED, None)

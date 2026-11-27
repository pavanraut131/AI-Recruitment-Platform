"""identity's Celery tasks (EPIC-02 spec §4.3, §4.4, §6.3): sending email and the daily purge.

- :func:`send_email` (``identity.send_email``, queue ``default``): sends one message with the
  configured backend (``app.platform.email``). Requests queue it (:func:`enqueue_email`) after
  their transaction commits, so an HTTP response never waits on SMTP and its timing does not
  say whether a message was sent (password reset: no account enumeration). Retried with
  back-off while the mail server is unreachable.
- :func:`purge_expired` (``identity.purge_expired``, queue ``default``): the first Celery
  **beat** schedule (``app.platform.celery_app``), daily. Deletes sessions that ended more than
  ``IDENTITY_PURGE_AFTER`` (30 days) ago, used or expired password-reset tokens, and invitations
  that expired more than 30 days ago.

Both touch **no tenant data** (identity's tables are global, spec §6.4), so neither takes an
``agency_id`` nor uses ``TenantTask``. The purge runs as the workers' database role, the
application role ``hireflow_app`` (spec §6.2: the owner role is for migrations and operator
commands that span agencies), on an **unbound** session: under row-level security that session
could not see a single tenant row, and it needs none.

**The message holds a link with a token.** It travels in the Celery message (Redis, internal),
but never in logs or Flower: :func:`enqueue_email` gives the call a redacted ``argsrepr`` /
``kwargsrepr``, and the task logs only the subject.
"""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Final

import structlog
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from app.modules.identity import repository
from app.platform import email
from app.platform.celery_app import celery_app
from app.platform.config import settings
from app.platform.tenancy import guard_in_development

if TYPE_CHECKING:
    from celery import Task

logger = structlog.get_logger()

SEND_EMAIL_TASK: Final = "identity.send_email"
PURGE_EXPIRED_TASK: Final = "identity.purge_expired"
QUEUE: Final = "default"

# Back-off between delivery attempts while the mail server is unreachable (seconds: 30, 60, 120,
# … capped at 15 minutes), for about a day.
SEND_MAX_RETRIES: Final = 12
SEND_RETRY_BACKOFF_MAX: Final = 15 * 60


@lru_cache
def _engine() -> Engine:
    """The workers' engine (application role), created on first use: the API process imports
    this module to queue emails and never purges."""
    engine = create_engine(settings.SYNC_DATABASE_URL, pool_pre_ping=True)
    guard_in_development(engine, settings.APP_ENV)
    return engine


def sync_session() -> Session:
    """A new, unbound session as the application role (identity's global tables only)."""
    return Session(_engine())


# ── Email ─────────────────────────────────────────────────────
@celery_app.task(
    bind=True,
    name=SEND_EMAIL_TASK,
    queue=QUEUE,
    max_retries=SEND_MAX_RETRIES,
    acks_late=True,
)
def send_email(self: Task[Any, Any], message: dict[str, str]) -> None:
    """Send one message (an :meth:`app.platform.email.EmailMessage.to_dict`)."""
    parsed = email.EmailMessage.from_dict(message)
    try:
        email.send(parsed)
    except email.EmailDeliveryError as exc:
        delay = min(30 * 2**self.request.retries, SEND_RETRY_BACKOFF_MAX)
        logger.warning("email_retry", subject=parsed.subject, retries=self.request.retries)
        raise self.retry(exc=exc, countdown=delay) from exc


def enqueue_email(message: email.EmailMessage) -> None:
    """Queue :func:`send_email`. Call it after the transaction that created the message's token
    has committed."""
    # What Flower, the worker's log and task events show instead of the arguments (Celery's
    # message options argsrepr / kwargsrepr, which celery-types does not list).
    redacted: dict[str, Any] = {
        "argsrepr": "()",
        "kwargsrepr": f"{{'message': <email: {message.subject!r}>}}",
    }
    send_email.apply_async(kwargs={"message": message.to_dict()}, queue=QUEUE, **redacted)


# ── Purge ─────────────────────────────────────────────────────
@celery_app.task(name=PURGE_EXPIRED_TASK, queue=QUEUE)
def purge_expired() -> dict[str, int]:
    """Delete what has outlived its use (see the module docstring); returns the counts."""
    now = datetime.now(UTC)
    with sync_session() as db:
        counts = repository.purge_expired_sync(
            db, now=now, ended_before=now - settings.IDENTITY_PURGE_AFTER
        )
        db.commit()
    logger.info("identity_purged", **counts)
    return counts

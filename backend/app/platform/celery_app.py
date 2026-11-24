"""The Celery application, and :class:`TenantTask`, the base class of every task that touches
tenant data (EPIC-02 spec §6.3)."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import structlog
from billiard.einfo import ExceptionInfo
from celery import Celery, Task
from sqlalchemy.orm import Session

from app.platform.config import settings
from app.platform.tenancy import DEFAULT_AGENCY_ID, bind

logger = structlog.get_logger()

celery_app = Celery(
    "resumeai",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    # Imported by worker processes at startup (a string, so platform imports no module). The
    # task names below predate the module layout and are kept on purpose; see that module.
    include=["app.modules.ingestion.tasks"],
)

celery_app.conf.update(
    # Serialization
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Timezone
    timezone="UTC",
    enable_utc=True,
    # Routing
    task_routes={
        "workers.extract_task.extract_resume": {"queue": "extract_queue"},
        "workers.ai_task.process_candidate_ai": {"queue": "ai_queue"},
    },
    # Retry config
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_max_retries=3,
    # Result expiry
    result_expires=86400,  # 24 hours
    # Worker optimizations
    worker_prefetch_multiplier=1,
    task_always_eager=False,
    # Monitoring
    worker_send_task_events=True,
    task_send_sent_event=True,
)


# ── Tenant tasks ──────────────────────────────────────────────
AGENCY_ID_ARG = "agency_id"
"""The keyword argument that carries a tenant task's agency (a UUID string)."""

# celery-types makes Task generic over (params, return); the runtime class is not subscriptable.
if TYPE_CHECKING:
    _Task = Task[..., Any]
else:
    _Task = Task


class TenantTask(_Task):
    """Base class of the tasks that touch tenant data (spec §6.3).

    Such a task takes its agency **explicitly**, as the keyword argument ``agency_id`` (the
    task names and positional arguments stay as they were: they are a contract). The class

    - validates it in ``before_start`` and binds it to the structlog context, so every log line
      of the task names its agency (unbound again in ``after_return``);
    - opens database sessions bound to it (:meth:`tenant_session`), which the task body and its
      failure handlers use: every query of the task is filtered to that agency, and every
      transaction starts with it in ``app.agency_id``.

    **Transition (until E2-04).** A message without ``agency_id`` (queued before E2-03 was
    deployed) runs for the default agency, which every row then belonged to, and is logged
    (``task_without_agency_id``). E2-04 rejects such messages instead; its runbook drains the
    queues first.

    Subclasses provide :meth:`session_factory` (the worker's ``sessionmaker``).
    """

    abstract = True

    @staticmethod
    def agency_from(value: object) -> uuid.UUID:
        """The agency a task argument names; ``None`` (an old message) is the default agency.
        Raises ``ValueError`` for anything that is not a UUID."""
        if value is None:
            return DEFAULT_AGENCY_ID
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))

    def agency_of(self, args: tuple[Any, ...], kwargs: Mapping[str, Any] | None) -> uuid.UUID:
        """The agency of a task call (its ``agency_id`` keyword, or its second positional
        argument)."""
        value = (kwargs or {}).get(AGENCY_ID_ARG)
        if value is None and len(args) > 1:
            value = args[1]
        return self.agency_from(value)

    def before_start(self, task_id: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        agency_id = self.agency_of(args, kwargs)  # an invalid id fails the task here
        if kwargs.get(AGENCY_ID_ARG) is None and len(args) < 2:
            logger.warning(
                "task_without_agency_id",
                task=self.name,
                task_id=task_id,
                fallback_agency_id=str(agency_id),
            )
        structlog.contextvars.bind_contextvars(agency_id=str(agency_id))

    def after_return(
        self,
        status: str,
        retval: Any,
        task_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        einfo: ExceptionInfo,
    ) -> None:
        structlog.contextvars.unbind_contextvars("agency_id")

    def session_factory(self) -> Session:
        """A new, unbound database session (provided by the subclass)."""
        raise NotImplementedError

    def tenant_session(self, agency_id: uuid.UUID) -> Session:
        """A new database session bound to ``agency_id``."""
        session = self.session_factory()
        bind(session, agency_id)
        return session

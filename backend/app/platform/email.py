"""Outgoing email (EPIC-02 spec §4.4): one message type and two backends.

- ``console`` (the default, and what the tests use): the message is logged (``email_console``,
  with its text body, so a developer without a mail catcher can copy a link) and kept in
  :data:`outbox`, which the tests read. It logs tokens by design, so the settings refuse it
  outside ``APP_ENV=development|test`` (spec §10: no tokens in logs).
- ``smtp``: sent through ``SMTP_HOST:SMTP_PORT`` (optionally ``STARTTLS`` and a login). The
  Compose stack points it at **Mailpit** (web UI and HTTP API on ``MAILPIT_UI_HOST_PORT``,
  default 8025), which catches every message; a deployment chooses a provider in E5.

A message is plain text plus a minimal HTML version (``multipart/alternative``). Requests never
send mail themselves: ``identity`` queues the Celery task ``identity.send_email`` (queue
``default``), which calls :func:`send`, so an HTTP response never waits on SMTP and its timing
says nothing about whether a message was sent. :class:`EmailDeliveryError` is what a sender may
retry (the mail server was unreachable or refused the message).

Nothing here knows what the messages say: the templates are identity's
(``app.modules.identity.emails``).
"""

from __future__ import annotations

import smtplib
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from email.message import EmailMessage as MimeMessage
from email.utils import formatdate, make_msgid, parseaddr
from typing import Final, Protocol

import structlog

from app.platform.config import settings

logger = structlog.get_logger()

CONSOLE: Final = "console"
SMTP: Final = "smtp"


@dataclass(frozen=True, slots=True)
class EmailMessage:
    """One message to one recipient. JSON-serialisable (:meth:`to_dict`), so it can travel in a
    Celery message."""

    to: str
    subject: str
    text: str
    html: str

    def to_dict(self) -> dict[str, str]:
        return {"to": self.to, "subject": self.subject, "text": self.text, "html": self.html}

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> EmailMessage:
        return cls(
            to=str(data["to"]),
            subject=str(data["subject"]),
            text=str(data["text"]),
            html=str(data["html"]),
        )


class EmailDeliveryError(RuntimeError):
    """The message could not be handed to the mail server (worth retrying later)."""


class EmailBackend(Protocol):
    def send(self, message: EmailMessage) -> None: ...


outbox: list[EmailMessage] = []
"""Every message the console backend has "sent" in this process, oldest first (tests clear it)."""


class ConsoleBackend:
    """Logs the message and appends it to :data:`outbox`. Development and tests only."""

    def send(self, message: EmailMessage) -> None:
        outbox.append(message)
        logger.info("email_console", to=message.to, subject=message.subject, text=message.text)


class SmtpBackend:
    """Sends through an SMTP server, one connection per message."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        sender: str,
        username: str | None = None,
        password: str | None = None,
        starttls: bool = False,
        timeout: float = 10.0,
    ) -> None:
        self.host = host
        self.port = port
        self.sender = sender
        self.username = username
        self.password = password
        self.starttls = starttls
        self.timeout = timeout

    @classmethod
    def from_settings(cls) -> SmtpBackend:
        return cls(
            settings.SMTP_HOST,
            settings.SMTP_PORT,
            sender=settings.EMAIL_FROM,
            username=settings.SMTP_USERNAME or None,
            password=settings.SMTP_PASSWORD or None,
            starttls=settings.SMTP_STARTTLS,
            timeout=settings.SMTP_TIMEOUT,
        )

    def build(self, message: EmailMessage) -> MimeMessage:
        """The MIME message: text, then HTML as the preferred alternative. The header values
        come from templates that keep user-provided names on one line; the default policy
        refuses a header with a line break anyway (no header injection)."""
        mime = MimeMessage()
        mime["From"] = self.sender
        mime["To"] = message.to
        mime["Subject"] = message.subject
        mime["Date"] = formatdate(usegmt=True)
        domain = parseaddr(self.sender)[1].rpartition("@")[2] or None
        mime["Message-ID"] = make_msgid(domain=domain)
        mime.set_content(message.text)
        mime.add_alternative(message.html, subtype="html")
        return mime

    def send(self, message: EmailMessage) -> None:
        mime = self.build(message)
        try:
            with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as smtp:
                if self.starttls:
                    smtp.starttls(context=ssl.create_default_context())
                if self.username:
                    smtp.login(self.username, self.password or "")
                # The envelope recipient is exactly the one address, whatever the To header
                # might parse into.
                smtp.send_message(mime, to_addrs=[message.to])
        except (OSError, smtplib.SMTPException) as exc:
            # The recipient and the body (which may hold a token) stay out of the log.
            logger.warning("email_failed", error=type(exc).__name__, subject=message.subject)
            raise EmailDeliveryError(f"could not send the email: {type(exc).__name__}") from exc
        logger.info("email_sent", subject=message.subject, message_id=mime["Message-ID"])


def backend() -> EmailBackend:
    """The backend ``EMAIL_BACKEND`` names (read on every call: tests may patch the settings)."""
    if settings.EMAIL_BACKEND == SMTP:
        return SmtpBackend.from_settings()
    return ConsoleBackend()


def send(message: EmailMessage) -> None:
    """Send ``message`` now, with the configured backend. Called by the ``identity.send_email``
    task, never from a request."""
    backend().send(message)

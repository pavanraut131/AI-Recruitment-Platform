"""Email (spec §4.4; E2-07): the platform's console and SMTP backends, identity's templates (plain
text and HTML, ``FRONTEND_URL`` links, names on one line and escaped), and the
``identity.send_email`` task: retried while the server is down, its arguments hidden from logs
and Flower."""

from __future__ import annotations

import smtplib
from datetime import UTC, datetime
from email.message import EmailMessage as MimeMessage
from typing import Any

import pytest

from app.modules.identity import emails, tasks
from app.modules.identity.schemas import Role
from app.platform import email
from app.platform.config import settings
from tests.fakes import CeleryRecorder

MESSAGE = email.EmailMessage(
    to="ada@example.com", subject="Hello", text="Plain body", html="<p>HTML body</p>"
)


class FakeSMTP:
    """Stands in for ``smtplib.SMTP``; records what each connection did."""

    connections: list[FakeSMTP] = []
    fail_with: BaseException | None = None

    def __init__(self, host: str, port: int, timeout: float) -> None:
        if FakeSMTP.fail_with is not None:
            raise FakeSMTP.fail_with
        self.address = (host, port, timeout)
        self.calls: list[tuple[str, Any]] = []
        FakeSMTP.connections.append(self)

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, *exc: object) -> None:
        self.calls.append(("quit", None))

    def starttls(self, context: Any) -> None:
        self.calls.append(("starttls", context))

    def login(self, user: str, password: str) -> None:
        self.calls.append(("login", (user, password)))

    def send_message(self, message: MimeMessage, to_addrs: list[str]) -> None:
        self.calls.append(("send", (message, to_addrs)))


@pytest.fixture
def smtp(monkeypatch: pytest.MonkeyPatch) -> type[FakeSMTP]:
    FakeSMTP.connections = []
    FakeSMTP.fail_with = None
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(settings, "EMAIL_BACKEND", "smtp")
    monkeypatch.setattr(settings, "SMTP_HOST", "mailpit")
    monkeypatch.setattr(settings, "SMTP_PORT", 1025)
    return FakeSMTP


# ── Backends ──────────────────────────────────────────────────
def test_the_console_backend_keeps_and_logs_the_message(
    capsys: pytest.CaptureFixture[str],
) -> None:
    email.outbox.clear()
    assert settings.EMAIL_BACKEND == "console"  # the tests' default
    email.send(MESSAGE)
    assert email.outbox == [MESSAGE]
    logged = capsys.readouterr().out
    assert "email_console" in logged and "Plain body" in logged
    email.outbox.clear()


def test_the_smtp_backend_sends_text_and_html(smtp: type[FakeSMTP]) -> None:
    email.send(MESSAGE)
    [connection] = smtp.connections
    assert connection.address == ("mailpit", 1025, settings.SMTP_TIMEOUT)
    [(kind, (mime, to_addrs)), quit_] = connection.calls
    assert (kind, quit_) == ("send", ("quit", None))
    assert to_addrs == ["ada@example.com"]  # the envelope: exactly the one address
    assert mime["From"] == settings.EMAIL_FROM and mime["To"] == "ada@example.com"
    assert mime["Subject"] == "Hello" and mime["Message-ID"].endswith("@hireflow.localhost>")
    assert mime.get_content_type() == "multipart/alternative"
    text, html = mime.iter_parts()
    assert text.get_content_type() == "text/plain" and "Plain body" in text.get_content()
    assert html.get_content_type() == "text/html" and "<p>HTML body</p>" in html.get_content()
    assert email.outbox == []  # nothing went to the console backend


def test_starttls_and_login_when_configured(
    smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "SMTP_STARTTLS", True)
    monkeypatch.setattr(settings, "SMTP_USERNAME", "mailer")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "s3cret")
    email.send(MESSAGE)
    kinds = [kind for kind, _ in smtp.connections[0].calls]
    assert kinds == ["starttls", "login", "send", "quit"]
    assert smtp.connections[0].calls[1] == ("login", ("mailer", "s3cret"))


def test_an_unreachable_server_is_a_delivery_error(smtp: type[FakeSMTP]) -> None:
    smtp.fail_with = ConnectionRefusedError(111, "refused")
    with pytest.raises(email.EmailDeliveryError, match="ConnectionRefusedError"):
        email.send(MESSAGE)


def test_a_header_cannot_be_injected() -> None:
    backend = email.SmtpBackend("h", 1, sender=settings.EMAIL_FROM)
    with pytest.raises(ValueError):
        backend.build(
            email.EmailMessage(to="a@example.com", subject="Hi\nBcc: x@evil.test", text="", html="")
        )


def test_messages_survive_a_celery_round_trip() -> None:
    assert email.EmailMessage.from_dict(MESSAGE.to_dict()) == MESSAGE


# ── The task ──────────────────────────────────────────────────
def test_enqueue_hides_the_message_from_logs_and_flower(celery_calls: CeleryRecorder) -> None:
    message = emails.password_reset(to="ada@example.com", display_name="Ada", token="T0KEN")
    tasks.enqueue_email(message)
    [call] = celery_calls.for_task("identity.send_email")
    assert call.kwargs == {"message": message.to_dict()}
    assert call.options["queue"] == "default"
    assert call.options["argsrepr"] == "()"
    assert "T0KEN" not in call.options["kwargsrepr"]
    assert "Reset your HireFlow AI password" in call.options["kwargsrepr"]


def test_the_task_sends_with_the_configured_backend(smtp: type[FakeSMTP]) -> None:
    tasks.send_email(message=MESSAGE.to_dict())
    assert len(smtp.connections) == 1


def test_the_task_retries_while_the_server_is_down(
    smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch
) -> None:
    retried: list[dict[str, Any]] = []

    def fake_retry(**kwargs: Any) -> Exception:
        retried.append(kwargs)
        return RuntimeError("retry scheduled")

    monkeypatch.setattr(tasks.send_email, "retry", fake_retry)
    smtp.fail_with = OSError("down")
    with pytest.raises(RuntimeError, match="retry scheduled"):
        tasks.send_email(message=MESSAGE.to_dict())
    [kwargs] = retried
    assert isinstance(kwargs["exc"], email.EmailDeliveryError) and kwargs["countdown"] == 30
    assert tasks.send_email.max_retries == tasks.SEND_MAX_RETRIES


# ── Templates ─────────────────────────────────────────────────
def test_the_invitation_email() -> None:
    message = emails.invitation(
        to="nina@example.com",
        agency_name="Rao\nRecruiting",  # a line break must not reach the subject
        inviter_name="Asha <b>Rao</b>",
        role=Role.admin,
        token="tok",
        expires_at=datetime(2026, 12, 4, 10, 0, tzinfo=UTC),
    )
    assert message.subject == "Asha <b>Rao</b> invited you to join Rao Recruiting on HireFlow AI"
    assert "as an admin." in message.text
    assert "http://localhost:3000/accept-invite#token=tok" in message.text
    assert "expires on 4 December 2026" in message.text
    assert 'href="http://localhost:3000/accept-invite#token=tok"' in message.html
    assert "Asha &lt;b&gt;Rao&lt;/b&gt;" in message.html and "<b>Rao</b>" not in message.html

    anonymous = emails.invitation(
        to="n@example.com",
        agency_name="Default agency",
        inviter_name=None,
        role=Role.viewer,
        token="t",
        expires_at=datetime(2026, 12, 4, tzinfo=UTC),
    )
    assert anonymous.subject == "You're invited to join Default agency on HireFlow AI"
    assert "as a viewer." in anonymous.text


def test_links_follow_frontend_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "FRONTEND_URL", "https://app.hireflow.example/")
    message = emails.password_reset(to="a@example.com", display_name="Ada", token="xyz")
    assert "https://app.hireflow.example/reset-password#token=xyz" in message.text
    changed = emails.password_changed(to="a@example.com", display_name="Ada")
    assert "https://app.hireflow.example/forgot-password" in changed.text

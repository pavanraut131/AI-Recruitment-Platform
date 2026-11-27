"""The emails identity sends (EPIC-02 spec §4.4): invitation, password reset and "your password
was changed". Each is plain text plus a minimal HTML version, with links to ``FRONTEND_URL``.

Private to identity: ``service`` builds a message here and queues it (``identity.tasks``); the
platform's ``app.platform.email`` sends it. Values that come from people (agency and user
names) are put on one line (no header injection through a subject) and HTML-escaped in the
HTML version. A token only ever appears in a link's **fragment** (``#token=…``), which browsers
never send to a server, so it cannot end up in access logs or ``Referer`` headers.
"""

from __future__ import annotations

import html
from datetime import datetime, timedelta

from app.modules.identity.schemas import Role
from app.platform.config import settings
from app.platform.email import EmailMessage

PRODUCT = "HireFlow AI"

ROLE_WORDS = {
    Role.owner: "an owner",
    Role.admin: "an admin",
    Role.recruiter: "a recruiter",
    Role.viewer: "a viewer",
}


def frontend_url(path: str) -> str:
    """``FRONTEND_URL`` joined with ``path`` (which starts with ``/``)."""
    return settings.FRONTEND_URL.rstrip("/") + path


def invitation_link(token: str) -> str:
    return frontend_url(f"/accept-invite#token={token}")


def reset_link(token: str) -> str:
    return frontend_url(f"/reset-password#token={token}")


def one_line(value: str) -> str:
    """``value`` with every run of whitespace (line breaks included) collapsed to one space."""
    return " ".join(value.split())


def _date(moment: datetime) -> str:
    return f"{moment:%d %B %Y}".lstrip("0")


def _duration(length: timedelta) -> str:
    minutes = int(length.total_seconds() // 60)
    if minutes % (24 * 60) == 0:
        days = minutes // (24 * 60)
        return f"{days} day{'s' if days != 1 else ''}"
    if minutes % 60 == 0:
        hours = minutes // 60
        return f"{hours} hour{'s' if hours != 1 else ''}"
    return f"{minutes} minute{'s' if minutes != 1 else ''}"


def _html(paragraphs: list[str], *, link: tuple[str, str] | None = None) -> str:
    """A minimal HTML body: escaped paragraphs and at most one button-like link."""
    parts = [f"<p>{html.escape(text)}</p>" for text in paragraphs]
    if link is not None:
        label, url = link
        parts.insert(
            1,
            f'<p><a href="{html.escape(url, quote=True)}" '
            'style="display:inline-block;padding:10px 16px;background:#2f5d46;color:#ffffff;'
            f'text-decoration:none;border-radius:6px">{html.escape(label)}</a></p>',
        )
    body = "\n".join(parts)
    return (
        '<!doctype html>\n<html lang="en">\n<body style="font-family:Arial,Helvetica,sans-serif;'
        'font-size:15px;line-height:1.5;color:#1f2421">\n'
        f'{body}\n<p style="color:#5b625e;font-size:13px">{PRODUCT}</p>\n</body>\n</html>\n'
    )


def invitation(
    *,
    to: str,
    agency_name: str,
    inviter_name: str | None,
    role: Role,
    token: str,
    expires_at: datetime,
) -> EmailMessage:
    agency = one_line(agency_name)
    inviter = one_line(inviter_name) if inviter_name else None
    link = invitation_link(token)
    who = f"{inviter} invited you" if inviter else "You're invited"
    subject = f"{who} to join {agency} on {PRODUCT}"
    intro = f"{who} to join {agency} on {PRODUCT} as {ROLE_WORDS[role]}."
    expiry = f"The invitation expires on {_date(expires_at)}. After that, ask for a new one."
    ignore = "If you weren't expecting this, you can ignore this email."
    text = f"{intro}\n\nAccept the invitation:\n{link}\n\n{expiry}\n{ignore}\n\n{PRODUCT}\n"
    return EmailMessage(
        to=to,
        subject=subject,
        text=text,
        html=_html([intro, expiry, ignore], link=("Accept the invitation", link)),
    )


def password_reset(*, to: str, display_name: str, token: str) -> EmailMessage:
    name = one_line(display_name)
    link = reset_link(token)
    intro = f"Hi {name}, someone asked to reset the password of your {PRODUCT} account."
    valid = (
        f"The link works once, for {_duration(settings.PASSWORD_RESET_TTL)}. "
        "Choosing a new password signs you out everywhere."
    )
    ignore = "If you didn't ask for this, ignore this email: your password stays as it is."
    text = f"{intro}\n\nChoose a new password:\n{link}\n\n{valid}\n{ignore}\n\n{PRODUCT}\n"
    return EmailMessage(
        to=to,
        subject=f"Reset your {PRODUCT} password",
        text=text,
        html=_html([intro, valid, ignore], link=("Choose a new password", link)),
    )


def password_changed(*, to: str, display_name: str) -> EmailMessage:
    name = one_line(display_name)
    forgot = frontend_url("/forgot-password")
    intro = (
        f"Hi {name}, the password of your {PRODUCT} account was just changed, and every "
        "browser that was signed in has been signed out."
    )
    warning = f"If this wasn't you, reset your password now: {forgot}"
    text = f"{intro}\n\n{warning}\n\n{PRODUCT}\n"
    return EmailMessage(
        to=to,
        subject=f"Your {PRODUCT} password was changed",
        text=text,
        html=_html(
            [intro, "If this wasn't you, reset your password now."],
            link=(
                "Reset your password",
                forgot,
            ),
        ),
    )

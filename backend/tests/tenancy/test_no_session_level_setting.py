"""The CI check for transaction-local tenant settings (EPIC-02 spec §6.3).

``app.agency_id`` is set only with ``set_config(…, true)`` (``SET LOCAL``), at the start of each
transaction, by ``app.platform.tenancy``. A session-level ``SET app.…`` (or ``set_config(…,
false)``) would survive on the pooled connection into the next request: the classic RLS pooling
leak. This test greps the backend code, the migrations and the repository's scripts and SQL for
either form, outside ``app/platform/tenancy.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
REPO = BACKEND.parent
ROOTS = [BACKEND / "app", BACKEND / "alembic", REPO / "scripts", REPO / "docker"]
SUFFIXES = {".py", ".sql", ".sh", ".ini"}
ALLOWED = {BACKEND / "app" / "platform" / "tenancy.py"}

# SET app.x / SET SESSION app.x (SET LOCAL is transaction-local, so it is fine)
SESSION_SET = re.compile(r"\bSET\s+(?:SESSION\s+)?\"?app\.", re.IGNORECASE)
# set_config('app.x', …, false): the session-level form of set_config
SESSION_SET_CONFIG = re.compile(
    r"set_config\s*\(\s*'app\.[^']*'\s*,[^)]*,\s*false\s*\)", re.IGNORECASE
)


def offending_lines(text: str) -> list[str]:
    return [
        line.strip()
        for line in text.splitlines()
        if SESSION_SET.search(line) or SESSION_SET_CONFIG.search(line)
    ]


def test_the_patterns_catch_session_level_settings_only() -> None:
    assert offending_lines("SET app.agency_id = '1'")
    assert offending_lines('cursor.execute("set session app.agency_id to x")')
    assert offending_lines("SELECT set_config('app.agency_id', :a, false)")
    assert not offending_lines("SET LOCAL app.agency_id = '1'")
    assert not offending_lines("SELECT set_config('app.agency_id', :a, true)")
    assert not offending_lines("SET search_path TO public")


def test_no_session_level_setting_of_app_variables() -> None:
    found: dict[str, list[str]] = {}
    for root in ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix not in SUFFIXES or path in ALLOWED or "__pycache__" in path.parts:
                continue
            lines = offending_lines(path.read_text(encoding="utf-8", errors="replace"))
            if lines:
                found[str(path.relative_to(REPO))] = lines
    assert not found, f"session-level SET of app.* (use tenancy.bind instead): {found}"

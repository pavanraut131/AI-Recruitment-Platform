"""Write the ``/api/v2`` OpenAPI document to a file, without a server (E1-03, ADR 0006).

Usage (from ``backend/``; ``make api-client`` runs it and then the TypeScript generator)::

    python -m app.devtools.export_openapi [--out ../frontend/src/lib/api/openapi.v2.json]

The document is ``app.v2.v2_app.openapi()``, the one served at ``/api/v2/openapi.json``. It is
the source of the generated frontend client (``frontend/src/lib/api/schema.d.ts``), and CI
regenerates both and fails on a diff, so the output is deterministic: keys sorted, two-space
indent, ASCII-safe, one trailing newline.

No database, Redis or AI provider is needed: importing the app only builds it. Settings are
still validated at import time, so the script sets dummy connection URLs and
``AI_PROVIDER=fake`` (no Gemini key needed) before importing anything from ``app``. They are
forced, not defaulted, so a developer's environment or ``.env`` cannot change the document
(``API_V2_PREFIX`` ends up in its ``servers``).
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

DEFAULT_OUT = (
    Path(__file__).resolve().parents[3] / "frontend" / "src" / "lib" / "api" / "openapi.v2.json"
)

# Placeholders that satisfy app.platform.config.Settings; nothing connects to them.
EXPORT_ENV = {
    "DATABASE_URL": "postgresql+asyncpg://openapi:openapi@localhost:5432/openapi",
    "SYNC_DATABASE_URL": "postgresql://openapi:openapi@localhost:5432/openapi",
    "AI_PROVIDER": "fake",
    "API_V2_PREFIX": "/api/v2",
}


def v2_document() -> dict[str, Any]:
    """The v2 OpenAPI document, built offline with :data:`EXPORT_ENV`."""
    os.environ.update(EXPORT_ENV)
    from app.v2 import v2_app  # after the environment: Settings load on import

    return v2_app.openapi()


def render(document: dict[str, Any]) -> str:
    """Stable text for a document: the same input always gives the same bytes."""
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Write the /api/v2 OpenAPI document to a file.")
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help="where to write the document (default: %(default)s)",
    )
    args = parser.parse_args(argv)
    out: Path = args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n": the same bytes on Windows as in CI.
    out.write_text(render(v2_document()), encoding="utf-8", newline="\n")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()

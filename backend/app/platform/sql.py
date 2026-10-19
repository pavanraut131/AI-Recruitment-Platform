"""Small SQL helpers shared by query builders."""

import uuid
from collections.abc import Collection
from typing import cast

from sqlalchemy import BindParameter, bindparam
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.types import TypeEngine

LIKE_ESCAPE = "\\"


def escape_like(term: str) -> str:
    """Escape LIKE/ILIKE wildcards so ``term`` matches literally.

    Pair it with ``escape=LIKE_ESCAPE``:
    ``col.ilike(f"%{escape_like(term)}%", escape=LIKE_ESCAPE)``.
    The escape character itself is escaped first, so a user-supplied backslash stays literal.
    """
    return (
        term.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
        .replace("%", LIKE_ESCAPE + "%")
        .replace("_", LIKE_ESCAPE + "_")
    )


def uuid_array(ids: Collection[uuid.UUID]) -> BindParameter[list[uuid.UUID]]:
    """``ids`` as one ``uuid[]`` parameter, for ``column == any_(uuid_array(ids))`` (SQL
    ``column = ANY(:ids)``): one bind parameter however many ids, where an expanding ``IN``
    sends one per id (and asyncpg caps a statement at 32,767 parameters)."""
    array_type = cast("TypeEngine[list[uuid.UUID]]", ARRAY(UUID(as_uuid=True)))
    return bindparam(None, list(ids), type_=array_type)

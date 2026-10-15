"""Small SQL helpers shared by query builders."""

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

"""Deprecated re-export shim; use :mod:`app.platform.database`."""

# TODO(E0-10): remove
from app.platform.database import AsyncSessionLocal, Base, engine, get_db

__all__ = ["AsyncSessionLocal", "Base", "engine", "get_db"]

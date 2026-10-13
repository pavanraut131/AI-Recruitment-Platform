"""Deprecated re-export shim; use :mod:`app.platform.config`."""

# TODO(E0-10): remove
from app.platform.config import Settings, get_settings, settings

__all__ = ["Settings", "get_settings", "settings"]

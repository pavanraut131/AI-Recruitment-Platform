"""Deprecated re-export shim; use :mod:`app.platform.logging`."""

# TODO(E0-10): remove
from app.platform.logging import configure_logging

__all__ = ["configure_logging"]

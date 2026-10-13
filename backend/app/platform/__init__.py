"""Shared kernel used by every module: settings, database session, object storage, logging and
the Celery app.

It must not import anything from :mod:`app.modules` (enforced by import-linter).
"""

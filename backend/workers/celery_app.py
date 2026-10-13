"""Deprecated re-export shim; use :mod:`app.platform.celery_app`.

Kept because the worker, beat and flower containers (docker-compose.yml) and the README start
Celery with ``celery -A workers.celery_app``, which finds the ``Celery`` instance in this module.
"""

# TODO(E0-10): remove
from app.platform.celery_app import celery_app

__all__ = ["celery_app"]

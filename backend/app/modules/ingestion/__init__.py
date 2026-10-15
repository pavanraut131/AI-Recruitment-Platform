"""ingestion: getting resumes into the system.

Owns the upload endpoint, the ``resumes`` table, the extract/analyse Celery tasks and SSE progress
events. May use the ``hiring``, ``talent`` and ``ai`` service interfaces.
"""

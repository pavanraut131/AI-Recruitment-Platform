"""Composition layer: HTTP endpoints whose use case spans more than one module (ADR 0005).

Modules may only call modules below them (``ingestion -> hiring -> talent``), so an endpoint
that needs, say, hiring's applications *and* ingestion's resumes cannot live in hiring. These
endpoints live here instead, outside ``app.modules``, and call each module's public ``service``
(import-linter: ``app.api`` imports only modules' ``service`` and ``schemas``, and no module or
``app.platform`` imports ``app.api``). They hold no business rules of their own: they fetch from
one service, hand the result to the other, and own the transaction when both write.

The endpoints are ``/api/v2``'s (``app.api.v2``). The ``/api/v1`` ones (``app.api.routes``) were
removed with v1 in E2-12.
"""

"""Composition layer: HTTP endpoints whose use case spans more than one module.

Modules may only call modules below them (``ingestion -> hiring -> talent``), so an endpoint
that needs, say, hiring's candidate data *and* ingestion's resumes cannot live in hiring. These
endpoints live here instead, outside ``app.modules``, and call each module's public ``service``
(import-linter: ``app.api`` imports only modules' ``service`` and ``schemas``, and no module or
``app.platform`` imports ``app.api``). They hold no business rules of their own: they fetch from
one service, hand the result to the other, and own the transaction when both write.

Each endpoint keeps the path, method, tags, function name (OpenAPI ``operationId``), docstring
and response model it had in its module, so the published API is unchanged.
"""

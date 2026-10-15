"""ai: all access to language models.

Public interface: ``service.py`` (the ``AIService`` protocol, its errors and the
``get_ai_service()`` factory chosen by the ``AI_PROVIDER`` setting) and ``schemas.py``
(``ExtractionResult``, ``ScoringResult``, ``JobRequirements``, ``JDParseResult``). Private:
the prompts and the implementations (``gemini.py``, the only importer of ``google.*`` in the app,
and ``fake.py``). Depends on no other module, only on :mod:`app.platform`.
"""

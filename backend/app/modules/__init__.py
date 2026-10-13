"""Domain modules of the modular monolith (spec EPIC-00 §3, ADR 0001).

Each module exposes its public interface in ``service.py`` (plus the ``schemas`` it uses) and
owns its tables. Other modules may import only those two; import-linter enforces the rules.
"""

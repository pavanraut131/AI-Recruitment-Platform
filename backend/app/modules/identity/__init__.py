"""identity: agencies (the tenants) and, from E2-05, users, memberships, sessions, invitations,
password-reset tokens, OIDC accounts and audit events (EPIC-02 spec §3.2).

Public interface: ``service.py`` and ``schemas.py``. Private: ``models.py``. A **leaf** like
``talent`` and ``ai``: it imports no other module, only :mod:`app.platform`. Other modules never
import it either: they refer to an agency by its id (``agency_id``, a plain UUID with no foreign
key, ADR 0003) and take the tenant types from :mod:`app.platform.tenancy`.

Its tables are **global**, not under agency row-level security (spec §6.4): they are read
before a tenant is known (login, the session lookup, invitation tokens).
"""

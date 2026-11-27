"""Composition layer of ``/api/v2`` (ADR 0005): the v2 endpoints whose use case spans modules.

These routers call modules' ``service`` functions only (and use their ``schemas`` /
``schemas_v2``), hold no business rules, and own the transaction when two modules write. Most v2 campaign and application endpoints are here because
their resources need ingestion's data (resume counts, the resumes' extraction snapshots) next to
hiring's; the people endpoints combine talent's people with hiring's applications; the dashboard
reads all three; client changes write hiring's client and identity's audit event together; the
team endpoints (E2-07) name identity's members' and invitations' clients from hiring.

``schemas`` holds the resources that are themselves compositions (a Person with their
applications, the Home aggregate). The v2 application (``app.v2``) includes these routers next to
the module-owned ones (``app.modules.<name>.router_v2``).
"""

from app.api.v2 import applications, campaigns, clients, dashboard, people, team

routers = (
    campaigns.router,
    applications.router,
    people.router,
    dashboard.router,
    clients.router,
    team.router,
)

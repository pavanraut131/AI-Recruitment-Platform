"""``/api/v2/people``: the talent list and a Person's profile (talent + hiring).

Talent owns people, their identifiers and identity conflicts; hiring owns applications. The list
adds each Person's application count and latest upload (one grouped hiring query for the page);
the profile adds every application with its campaign.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v2.schemas import Person, PersonListItem, PersonPage
from app.modules.hiring import service as hiring
from app.modules.identity.dependencies import access_scope, tenant_db
from app.modules.talent import service as talent
from app.platform.pagination import PageParams
from app.platform.problems import errors_as_http, problem_responses
from app.platform.tenancy import AccessScope

router = APIRouter(prefix="/people", tags=["people"])

TALENT_ERRORS: dict[type[Exception], int] = {talent.NotFoundError: status.HTTP_404_NOT_FOUND}


@router.get(
    "", operation_id="listPeople", response_model=PersonPage, responses=problem_responses(422)
)
async def list_people(
    page: Annotated[PageParams, Depends()],
    q: Annotated[
        str | None,
        Query(max_length=200, description="Contained in the name or one of the emails."),
    ] = None,
    db: AsyncSession = Depends(tenant_db),
    scope: AccessScope = Depends(access_scope),
) -> PersonPage:
    """People across all campaigns, most recently updated first (search by name or email
    only; semantic search is E6)."""
    people, total = await talent.list_people(db, q, page.offset, page.limit)
    activity = await hiring.people_activity(db, scope, [p.id for p in people])
    items = [
        PersonListItem(
            **person.model_dump(),
            application_count=activity[person.id].applications,
            last_applied_at=activity[person.id].last_applied_at,
        )
        for person in people
    ]
    return PersonPage(items=items, total=total, page=page.page, page_size=page.page_size)


@router.get(
    "/{person_id}",
    operation_id="getPerson",
    response_model=Person,
    responses=problem_responses(404, 422),
)
async def get_person(
    person_id: uuid.UUID,
    db: AsyncSession = Depends(tenant_db),
    scope: AccessScope = Depends(access_scope),
) -> Person:
    """The Person's profile, identifiers and identity conflicts, with all their applications."""
    with errors_as_http(TALENT_ERRORS):
        person = await talent.get_person(db, person_id)
    applications = await hiring.person_applications(db, scope, person_id)
    return Person(**person.model_dump(), applications=applications)

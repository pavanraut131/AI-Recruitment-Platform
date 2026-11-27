"""``/api/v2`` applications: the campaign's list, CSV export and bulk actions, and one
application's detail, update and delete (hiring + ingestion).

An ``Application`` shows facts from its current resume's extraction snapshot (ingestion) next to
hiring's scoring and recruiter fields (spec §7.1), so hiring builds it with
``ingestion.resume_profiles(db)``. Deletes remove hiring's applications and ingestion's resumes
in one transaction, then the stored PDFs.

Permissions (EPIC-02 §8.3): reads need ``workspace:read``, changes ``application:review``,
deletes ``application:delete`` (the bulk ``delete`` action too), the CSV ``application:export``.
Everything goes through the campaign, so it follows the campaign's client (§5.3).
"""

import uuid
from dataclasses import replace
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.hiring import service as hiring
from app.modules.hiring.schemas_v2 import (
    Application,
    ApplicationCategory,
    ApplicationPage,
    ApplicationSort,
    ApplicationUpdate,
    BulkApplicationAction,
    BulkApplicationResult,
    PipelineStage,
)
from app.modules.identity.dependencies import (
    Permission,
    access_scope,
    ensure_permission,
    require_permission,
    tenant_db,
)
from app.modules.identity.schemas import Principal
from app.modules.ingestion import service as ingestion
from app.platform.pagination import PageParams
from app.platform.problems import errors_as_http, problem_responses
from app.platform.tenancy import AccessScope

router = APIRouter(tags=["applications"])

HIRING_ERRORS: dict[type[Exception], int] = {
    hiring.NotFoundError: status.HTTP_404_NOT_FOUND,
    hiring.InvalidInputError: status.HTTP_422_UNPROCESSABLE_CONTENT,
}

READ = Depends(require_permission(Permission.workspace_read))
REVIEW = Depends(require_permission(Permission.application_review))
DELETE = Depends(require_permission(Permission.application_delete))
EXPORT = Depends(require_permission(Permission.application_export))


def application_filters(
    campaign_id: uuid.UUID,
    q: Annotated[
        str | None,
        Query(max_length=200, description="Contained in the name or the email."),
    ] = None,
    category: Annotated[
        list[ApplicationCategory] | None, Query(description="Any of these categories.")
    ] = None,
    stage: Annotated[
        list[PipelineStage] | None, Query(description="Any of these pipeline stages.")
    ] = None,
    min_score: Annotated[float | None, Query(ge=0, le=100)] = None,
    max_score: Annotated[float | None, Query(ge=0, le=100)] = None,
    skill: Annotated[
        str | None, Query(max_length=100, description="Contained in one of the skills.")
    ] = None,
    min_experience: Annotated[
        float | None, Query(ge=0, description="Years of experience, at least.")
    ] = None,
    sort: Annotated[
        ApplicationSort,
        Query(description="Order; scores sort unscored last; ties by id."),
    ] = ApplicationSort.score_desc,
) -> hiring.ApplicationQuery:
    """The list filters, shared by the list and the export (the export applies all of them:
    B8)."""
    return hiring.ApplicationQuery(
        campaign_id=campaign_id,
        q=q,
        categories=[c.value for c in category or []],
        stages=[s.value for s in stage or []],
        min_score=min_score,
        max_score=max_score,
        skill=skill,
        min_experience=min_experience,
        sort=sort,
    )


Filters = Annotated[hiring.ApplicationQuery, Depends(application_filters)]


@router.get(
    "/campaigns/{campaign_id}/applications",
    operation_id="listCampaignApplications",
    response_model=ApplicationPage,
    responses=problem_responses(404, 422),
    dependencies=[READ],
)
async def list_campaign_applications(
    filters: Filters,
    page: Annotated[PageParams, Depends()],
    db: AsyncSession = Depends(tenant_db),
    scope: AccessScope = Depends(access_scope),
) -> ApplicationPage:
    """The campaign's applications matching the filters, best score first by default."""
    with errors_as_http(HIRING_ERRORS):
        items, total = await hiring.list_applications_page(
            db, scope, filters, page.offset, page.limit, ingestion.resume_profiles(db)
        )
    return ApplicationPage(items=items, total=total, page=page.page, page_size=page.page_size)


@router.get(
    "/campaigns/{campaign_id}/applications/export",
    operation_id="exportCampaignApplications",
    response_class=Response,
    responses={
        200: {
            "description": "CSV, one row per application, in the list's order.",
            "content": {"text/csv": {"schema": {"type": "string"}}},
        },
        **problem_responses(404, 422),
    },
    dependencies=[EXPORT],
)
async def export_campaign_applications(
    campaign_id: uuid.UUID,
    filters: Filters,
    ids: Annotated[
        list[uuid.UUID] | None,
        Query(max_length=500, description="Only these applications (export a selection)."),
    ] = None,
    db: AsyncSession = Depends(tenant_db),
    scope: AccessScope = Depends(access_scope),
) -> Response:
    """Every application matching the filters (no paging) as CSV."""
    query = replace(filters, ids=ids) if ids else filters
    with errors_as_http(HIRING_ERRORS):
        csv_text = await hiring.export_applications_csv(
            db, scope, query, ingestion.resume_profiles(db)
        )
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="applications_{campaign_id}.csv"'},
    )


@router.post(
    "/campaigns/{campaign_id}/applications/bulk",
    operation_id="bulkApplicationAction",
    response_model=BulkApplicationResult,
    responses=problem_responses(404, 422),
)
async def bulk_application_action(
    campaign_id: uuid.UUID,
    payload: BulkApplicationAction,
    db: AsyncSession = Depends(tenant_db),
    scope: AccessScope = Depends(access_scope),
    principal: Principal = REVIEW,
) -> BulkApplicationResult:
    """Move the applications to a stage, reject them (stage ``rejected_manual``) or delete them
    (with their resumes; the ``delete`` action also needs ``application:delete``). Ids that are
    not this campaign's applications are reported in ``not_found``; the others are acted on."""
    if payload.root.action == "delete":
        ensure_permission(principal, Permission.application_delete)
    with errors_as_http(HIRING_ERRORS):
        result, deleted = await hiring.bulk_applications(db, scope, campaign_id, payload.root)
    files = await ingestion.delete_application_resumes(db, deleted)
    await db.commit()
    await ingestion.delete_stored_files(files)
    return result


@router.get(
    "/applications/{application_id}",
    operation_id="getApplication",
    response_model=Application,
    responses=problem_responses(404, 422),
    dependencies=[READ],
)
async def get_application(
    application_id: uuid.UUID,
    db: AsyncSession = Depends(tenant_db),
    scope: AccessScope = Depends(access_scope),
) -> Application:
    with errors_as_http(HIRING_ERRORS):
        return await hiring.get_application_v2(
            db, scope, application_id, ingestion.resume_profiles(db)
        )


@router.patch(
    "/applications/{application_id}",
    operation_id="updateApplication",
    response_model=Application,
    responses=problem_responses(404, 422),
    dependencies=[REVIEW],
)
async def update_application(
    application_id: uuid.UUID,
    payload: ApplicationUpdate,
    db: AsyncSession = Depends(tenant_db),
    scope: AccessScope = Depends(access_scope),
) -> Application:
    """The recruiter's changes: pipeline stage, notes, category override (only those sent)."""
    with errors_as_http(HIRING_ERRORS):
        return await hiring.update_application_v2(
            db, scope, application_id, payload, ingestion.resume_profiles(db)
        )


@router.delete(
    "/applications/{application_id}",
    operation_id="deleteApplication",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=problem_responses(404, 422),
    dependencies=[DELETE],
)
async def delete_application(
    application_id: uuid.UUID,
    db: AsyncSession = Depends(tenant_db),
    scope: AccessScope = Depends(access_scope),
) -> None:
    """Delete the application with its resumes and their stored PDFs."""
    with errors_as_http(HIRING_ERRORS):
        deleted = await hiring.delete_application_v2(db, scope, application_id)
    files = await ingestion.delete_application_resumes(db, [deleted])
    await db.commit()
    await ingestion.delete_stored_files(files)

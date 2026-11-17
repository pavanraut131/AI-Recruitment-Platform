"""Paged lists for the ``/api/v2`` application.

Every v2 list endpoint takes ``page`` (from 1) and ``page_size`` (1 to :data:`MAX_PAGE_SIZE`)
through :class:`PageParams` and returns a :class:`Page`: ``{items, total, page, page_size}``,
where ``total`` counts every item that matches the request's filters (not just this page).
Out-of-range values are a 422 (problem details, ``app.platform.problems``).

Give each concrete page a name of its own (``class CampaignPage(Page[Campaign]): ...``), so the
OpenAPI document (and the client generated from it) gets ``CampaignPage`` rather than
``Page_Campaign_``.
"""

from typing import Annotated, Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel, Field

T = TypeVar("T")

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int = Field(description="Items matching the filters, across all pages.")
    page: int = Field(description="This page's number, from 1.")
    page_size: int = Field(description="The requested page size.")


class PageParams:
    """The ``page`` / ``page_size`` query parameters (a FastAPI dependency)."""

    def __init__(
        self,
        page: Annotated[int, Query(ge=1, description="Page number, from 1.")] = 1,
        page_size: Annotated[
            int, Query(ge=1, le=MAX_PAGE_SIZE, description="Items per page.")
        ] = DEFAULT_PAGE_SIZE,
    ) -> None:
        self.page = page
        self.page_size = page_size

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size

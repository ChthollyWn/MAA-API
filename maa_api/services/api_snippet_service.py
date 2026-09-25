"""Business rules for saved API-console requests."""

from __future__ import annotations

from collections.abc import Mapping
import re
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator
from sqlalchemy.exc import IntegrityError

from maa_api.db import session as db_session
from maa_api.db.models import ApiSnippet, utcnow
from maa_api.db.repositories.api_snippet import ApiSnippetRepository
from maa_api.domain.errors import AppError, ErrorCode

HttpMethod = Literal[
    "GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE"
]
_CREDENTIAL_HEADER = re.compile(
    r"authorization|cookie|(?:^|[-_])token(?:$|[-_])|api[-_]?key|password|(?:^|[-_])secret(?:$|[-_])",
    re.IGNORECASE,
)


class ApiSnippetWrite(BaseModel):
    """Validated full snippet payload; credential fields are stripped by the service."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    method: HttpMethod
    path: str = Field(min_length=1, max_length=2048)
    path_params: dict[str, Any] = Field(default_factory=dict)
    query: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    body: Any | None = None

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        if not isinstance(value, str):
            return value
        value = value.strip()
        if not value:
            raise ValueError("name 不能为空")
        return value

    @field_validator("method", mode="before")
    @classmethod
    def normalize_method(cls, value: str) -> str:
        return value.upper() if isinstance(value, str) else value

    @field_validator("path")
    @classmethod
    def require_relative_api_path(cls, value: str) -> str:
        if "\\" in value:
            raise ValueError("path 不得包含反斜杠")
        if not value.startswith("/") or value.startswith("//"):
            raise ValueError("path 必须是以 / 开头的同源路径")
        if "?" in value or "#" in value:
            raise ValueError("query 与 fragment 必须单独保存")
        return value


class ApiSnippetView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    method: str
    path: str
    path_params: dict[str, Any]
    query: dict[str, Any]
    headers: dict[str, str]
    body: Any | None
    created_at: datetime = Field(json_schema_extra={"format": "date-time"})
    updated_at: datetime = Field(json_schema_extra={"format": "date-time"})

    @field_serializer("created_at", "updated_at")
    def serialize_utc_datetime(self, value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class ApiSnippetPage(BaseModel):
    items: list[ApiSnippetView]
    total: int


def sanitize_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Remove credential-bearing standard headers case-insensitively."""
    clean: dict[str, str] = {}
    for key, value in headers.items():
        normalized_key = key.strip()
        if normalized_key and not _CREDENTIAL_HEADER.search(normalized_key):
            clean[normalized_key] = value
    return clean


def sanitize_query(query: Mapping[str, Any]) -> dict[str, Any]:
    """Remove token query credentials without changing ordinary parameters."""
    return {key: value for key, value in query.items() if key.lower() != "token"}


class ApiSnippetService:
    def __init__(self, session_factory=None) -> None:
        self._session_factory = session_factory or db_session.session_factory

    async def list(self) -> ApiSnippetPage:
        async with self._session_factory() as session:
            items = await ApiSnippetRepository(session).list()
        return ApiSnippetPage(
            items=[_to_view(item) for item in items],
            total=len(items),
        )

    async def get(self, snippet_id: str) -> ApiSnippetView:
        async with self._session_factory() as session:
            item = await ApiSnippetRepository(session).get(snippet_id)
        if item is None:
            raise _not_found(snippet_id)
        return _to_view(item)

    async def create(self, payload: ApiSnippetWrite) -> ApiSnippetView:
        clean_name = payload.name
        item = ApiSnippet(
            name=clean_name,
            method=payload.method,
            path=payload.path,
            path_params=dict(payload.path_params),
            query=sanitize_query(payload.query),
            headers=sanitize_headers(payload.headers),
            body=payload.body,
        )
        async with self._session_factory() as session:
            try:
                await ApiSnippetRepository(session).create(item)
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise _name_conflict(clean_name) from exc
        return _to_view(item)

    async def update(self, snippet_id: str, payload: ApiSnippetWrite) -> ApiSnippetView:
        async with self._session_factory() as session:
            repository = ApiSnippetRepository(session)
            item = await repository.get(snippet_id)
            if item is None:
                raise _not_found(snippet_id)
            item.name = payload.name
            item.method = payload.method
            item.path = payload.path
            item.path_params = dict(payload.path_params)
            item.query = sanitize_query(payload.query)
            item.headers = sanitize_headers(payload.headers)
            item.body = payload.body
            item.updated_at = utcnow()
            clean_name = item.name
            try:
                await session.flush()
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise _name_conflict(clean_name) from exc
        return _to_view(item)

    async def delete(self, snippet_id: str) -> None:
        async with self._session_factory() as session:
            if not await ApiSnippetRepository(session).delete(snippet_id):
                raise _not_found(snippet_id)
            await session.commit()


def _not_found(snippet_id: str) -> AppError:
    return AppError(
        ErrorCode.API_SNIPPET_NOT_FOUND,
        "API 调试台收藏不存在",
        {"snippet_id": snippet_id},
    )


def _to_view(item: ApiSnippet) -> ApiSnippetView:
    """Sanitize persisted values on every read before exposing a snippet."""
    data = item.model_dump()
    data["query"] = sanitize_query(item.query)
    data["headers"] = sanitize_headers(item.headers)
    return ApiSnippetView.model_validate(data)


def _name_conflict(name: str) -> AppError:
    return AppError(
        ErrorCode.API_SNIPPET_NAME_CONFLICT,
        "API 调试台收藏名称已存在",
        {"name": name},
    )

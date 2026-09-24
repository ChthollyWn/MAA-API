"""Saved API-console request endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status

from maa_api.api.deps import require_auth
from maa_api.api.errors import error_responses
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.api_snippet_service import (
    ApiSnippetPage,
    ApiSnippetService,
    ApiSnippetView,
    ApiSnippetWrite,
)

__all__ = ["router"]

router = APIRouter(prefix="/api/snippets", tags=["snippets"])


def _service(request: Request) -> ApiSnippetService:
    service = getattr(request.app.state, "api_snippet_service", None)
    if service is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "API 收藏服务尚未启动")
    return service


@router.get(
    "",
    response_model=ApiSnippetPage,
    summary="读取 API 调试台收藏",
    responses=error_responses("UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def list_snippets(request: Request) -> ApiSnippetPage:
    return await _service(request).list()


@router.post(
    "",
    response_model=ApiSnippetView,
    status_code=status.HTTP_201_CREATED,
    summary="创建 API 调试台收藏",
    responses={
        **error_responses(
            "API_SNIPPET_NAME_CONFLICT", "VALIDATION_ERROR", "UNAUTHORIZED"
        ),
        201: {
            "description": "Created",
            "headers": {
                "Location": {
                    "description": "URI of the created API snippet",
                    "schema": {"type": "string"},
                }
            },
        },
    },
    dependencies=[Depends(require_auth)],
)
async def create_snippet(
    payload: ApiSnippetWrite, request: Request, response: Response
) -> ApiSnippetView:
    item = await _service(request).create(payload)
    response.headers["Location"] = f"/api/snippets/{item.id}"
    return item


@router.get(
    "/{snippet_id}",
    response_model=ApiSnippetView,
    summary="读取 API 调试台收藏",
    responses=error_responses("API_SNIPPET_NOT_FOUND", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def get_snippet(snippet_id: str, request: Request) -> ApiSnippetView:
    return await _service(request).get(snippet_id)


@router.put(
    "/{snippet_id}",
    response_model=ApiSnippetView,
    summary="更新 API 调试台收藏",
    responses=error_responses(
        "API_SNIPPET_NOT_FOUND",
        "API_SNIPPET_NAME_CONFLICT",
        "VALIDATION_ERROR",
        "UNAUTHORIZED",
    ),
    dependencies=[Depends(require_auth)],
)
async def update_snippet(
    snippet_id: str, payload: ApiSnippetWrite, request: Request
) -> ApiSnippetView:
    return await _service(request).update(snippet_id, payload)


@router.delete(
    "/{snippet_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="删除 API 调试台收藏",
    responses=error_responses("API_SNIPPET_NOT_FOUND", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def delete_snippet(snippet_id: str, request: Request) -> Response:
    await _service(request).delete(snippet_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

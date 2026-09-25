"""Persistence access for reusable API-console requests."""

from sqlalchemy import delete, select

from maa_api.db.models import ApiSnippet
from maa_api.db.repositories.base import BaseRepository


class ApiSnippetRepository(BaseRepository):
    """Read and write snippets without deciding API or credential policy."""

    async def create(self, snippet: ApiSnippet) -> ApiSnippet:
        self.session.add(snippet)
        await self.session.flush()
        return snippet

    async def get(self, snippet_id: str) -> ApiSnippet | None:
        return await self.session.get(ApiSnippet, snippet_id, populate_existing=True)

    async def list(self) -> list[ApiSnippet]:
        result = await self.session.execute(
            select(ApiSnippet).order_by(
                ApiSnippet.updated_at.desc(), ApiSnippet.id.desc()
            )
        )
        return list(result.scalars().all())

    async def delete(self, snippet_id: str) -> bool:
        result = await self.session.execute(
            delete(ApiSnippet).where(ApiSnippet.id == snippet_id)
        )
        return result.rowcount > 0

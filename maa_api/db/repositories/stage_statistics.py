"""Read structured StageDrops and SanityBeforeStage callback statistics."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select

from maa_api.db.models import SanityObservation, StageDrop
from maa_api.db.repositories.base import BaseRepository

__all__ = ["StageStatisticsRepository"]


class StageStatisticsRepository(BaseRepository):
    """Aggregate structured callback facts without reading presentation logs."""

    async def drop_stats(
        self,
        *,
        stage_code: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> dict[str, object]:
        conditions = _conditions(StageDrop, stage_code, since, until)
        callbacks = (
            select(StageDrop.callback_id)
            .where(*conditions)
            .distinct()
            .subquery()
        )
        result = await self.session.execute(
            select(
                StageDrop.item_id,
                StageDrop.item_name,
                func.sum(StageDrop.quantity),
                func.sum(StageDrop.add_quantity),
            )
            .where(*conditions)
            .group_by(StageDrop.item_id, StageDrop.item_name)
            .order_by(StageDrop.item_name.asc(), StageDrop.item_id.asc())
        )
        items = [
            {
                "item_id": item_id,
                "item_name": item_name,
                "quantity": int(quantity or 0),
                "add_quantity": int(add_quantity or 0),
            }
            for item_id, item_name, quantity, add_quantity in result.all()
        ]
        runs = int(
            await self.session.scalar(select(func.count()).select_from(callbacks)) or 0
        )
        return {
            "stage_code": stage_code,
            "runs": runs,
            "items": items,
        }

    async def sanity_curve(
        self,
        *,
        stage_code: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[dict[str, object]]:
        conditions = _conditions(SanityObservation, stage_code, since, until)
        result = await self.session.execute(
            select(SanityObservation)
            .where(*conditions)
            .order_by(SanityObservation.created_at.asc(), SanityObservation.id.asc())
        )
        return [
            {
                "stage_code": row.stage_code,
                "current_sanity": row.current_sanity,
                "max_sanity": row.max_sanity,
                "created_at": row.created_at.isoformat() + "Z",
            }
            for row in result.scalars().all()
        ]


def _conditions(model, stage_code, since, until):
    conditions = []
    if stage_code is not None:
        conditions.append(model.stage_code == stage_code)
    if since is not None:
        conditions.append(model.created_at >= since)
    if until is not None:
        conditions.append(model.created_at <= until)
    return conditions

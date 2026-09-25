"""Parse and persist structured MaaCore callback facts."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import uuid4

from maa_api.core.enums import Message
from maa_api.db.models import SanityObservation, StageDrop

__all__ = ["CallbackStatisticsService"]


class CallbackStatisticsService:
    """Build statistic rows from callback structure, never from display text."""

    def rows_for(
        self,
        callback: Mapping[str, Any],
        *,
        message: Any,
        created_at: datetime,
        pipeline_id: str | None,
        task_id: str | None,
        stage_code_fallback: str | None = None,
    ) -> tuple[list[StageDrop], list[SanityObservation]]:
        what = callback.get("what")
        try:
            if int(message) != int(Message.SubTaskExtraInfo):
                return [], []
        except (TypeError, ValueError):
            return [], []
        details = callback.get("details")
        if not isinstance(details, Mapping):
            return [], []
        stage = details.get("stage")
        stage = stage if isinstance(stage, Mapping) else {}
        stage_code = _optional_text(stage.get("stageCode")) or stage_code_fallback

        if what == "StageDrops":
            stats = details.get("stats")
            if not isinstance(stats, list):
                return [], []
            normalized: list[tuple[str | None, str | None, int, int]] = []
            for item in stats:
                if not isinstance(item, Mapping):
                    return [], []
                quantity = _integer(item.get("quantity"))
                add_quantity = _integer(item.get("addQuantity"))
                if quantity is None or add_quantity is None:
                    return [], []
                item_id = _optional_text(item.get("itemId"))
                item_name = _optional_text(item.get("itemName"))
                if item_id is None and item_name is None:
                    return [], []
                normalized.append(
                    (
                        item_id,
                        item_name,
                        quantity,
                        add_quantity,
                    )
                )
            callback_id = str(uuid4())
            stars = _integer(details.get("stars"))
            return (
                [
                    StageDrop(
                        callback_id=callback_id,
                        pipeline_id=pipeline_id,
                        task_id=task_id,
                        stage_code=stage_code,
                        stars=stars,
                        item_id=item_id,
                        item_name=item_name,
                        quantity=quantity,
                        add_quantity=add_quantity,
                        created_at=created_at,
                    )
                    for item_id, item_name, quantity, add_quantity in normalized
                ],
                [],
            )

        if what == "SanityBeforeStage":
            current = _integer(details.get("current_sanity"))
            maximum = _integer(details.get("max_sanity"))
            if current is None or maximum is None or current < 0 or maximum < 0:
                return [], []
            return [], [
                SanityObservation(
                    pipeline_id=pipeline_id,
                    task_id=task_id,
                    stage_code=stage_code,
                    current_sanity=current,
                    max_sanity=maximum,
                    created_at=created_at,
                )
            ]

        return [], []


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value

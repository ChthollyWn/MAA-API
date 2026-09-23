"""Resolve MAA task defaults from persisted settings (docs/05 §9)."""

from __future__ import annotations

import logging

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from maa_api.db.repositories.setting import SettingRepository
from maa_api.domain.task import ChannelDefaults

__all__ = ["load_channel_defaults"]

logger = logging.getLogger(__name__)

_SETTING_CLIENT_TYPE = "channel.client_type"
_SETTING_SERVER = "channel.server"


async def load_channel_defaults(session: AsyncSession) -> ChannelDefaults:
    """Read channel defaults, independently falling back on malformed values."""
    repo = SettingRepository(session)
    raw_values = {
        "client_type": await repo.get(_SETTING_CLIENT_TYPE),
        "server": await repo.get(_SETTING_SERVER),
    }
    defaults = ChannelDefaults()
    for field_name, raw in raw_values.items():
        if raw is None:
            continue
        try:
            parsed = ChannelDefaults.model_validate({field_name: raw})
        except ValidationError:
            logger.warning(
                "setting %s 的值非法（%r），回退默认值",
                f"channel.{field_name}",
                raw,
            )
            continue
        setattr(defaults, field_name, getattr(parsed, field_name))
    return defaults

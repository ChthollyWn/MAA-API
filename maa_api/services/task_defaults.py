"""Resolve MAA task defaults from persisted settings (docs/05 §9)."""

from __future__ import annotations

import logging

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from maa_api.db.repositories.setting import SettingRepository
from maa_api.domain.task import ChannelDefaults
from maa_api.settings import load_settings

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
    # Resolve the same four-layer settings stack as the visual API. Validate DB
    # channel values independently first so a legacy malformed value only falls
    # back that one dimension, preserving M5's client_type/server independence.
    valid_overrides: dict[str, str] = {}
    for field_name, raw in raw_values.items():
        if raw is None:
            continue
        try:
            ChannelDefaults.model_validate({field_name: raw})
        except ValidationError:
            logger.warning(
                "setting %s 的值非法（%r），回退默认值",
                f"channel.{field_name}",
                raw,
            )
            continue
        valid_overrides[f"channel.{field_name}"] = raw
    settings = load_settings(db_overrides=valid_overrides)
    try:
        return ChannelDefaults(
            client_type=settings.channel.client_type,
            server=settings.channel.server,
        )
    except ValidationError:
        # A malformed config.yaml is normally caught during startup. Keep this
        # layer defensive for queue callers that run before startup validation.
        logger.warning("渠道默认设置非法，回退代码默认值")
        return ChannelDefaults()

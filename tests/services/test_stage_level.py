"""`StageLevelService.level_data`（规格 §9.3）：定位、打包、体积与降级。

用 `tests/fixtures/gamedata/` 的关卡文件与假的 ``GamedataClient``（不打网）。定位走真的
``ActivityIndex``（读本机资源包），拆包取数换成返回夹具的假客户端。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.external.gamedata import GamedataData
from maa_api.services.game.level_data import (
    ALL_GROUPS,
    StageLevelService,
    default_stage_level_service,
)

FIXTURES = Path("tests/fixtures/gamedata")
LEVEL_ID = "obt/main/level_main_01-07"


def async_test(func):
    """本仓库不装 pytest-asyncio：异步用例统一用 `asyncio.run` 跑。"""

    def wrapper(*args, **kwargs):
        return asyncio.run(func(*args, **kwargs))

    wrapper.__name__ = func.__name__
    return wrapper


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeGamedata:
    """假的拆包客户端：记录请求，按 ``level`` / ``excel`` 回夹具或抛错。"""

    def __init__(self, payload: Any = None, error: Exception | None = None) -> None:
        self.payload = payload if payload is not None else fixture("level_main_01-07.json")
        self.error = error
        self.calls: list[str] = []

    async def level(self, level_id: str) -> GamedataData:
        self.calls.append(level_id)
        if self.error is not None:
            raise self.error
        return GamedataData(
            data=self.payload,
            url=(
                "https://fastly.jsdelivr.net/gh/Kengxxiao/ArknightsGameData@master/"
                f"zh_CN/gamedata/levels/{level_id}.json"
            ),
            fetched_at=datetime(2026, 10, 10, 12, 0).astimezone(),
            cached=False,
            stale=False,
            via="https://fastly.jsdelivr.net/gh/Kengxxiao/ArknightsGameData@master",
        )

    async def excel(self, name: str) -> GamedataData:  # pragma: no cover - 本模块不用
        raise AssertionError("level_data 不该取常量表")


def service(gamedata: Any = None) -> StageLevelService:
    return StageLevelService(gamedata=gamedata or FakeGamedata())


# --------------------------------------------------------------------------- #
# 定位
# --------------------------------------------------------------------------- #


@async_test
async def test_code_resolves_to_level_id_and_reads_the_file() -> None:
    """代号 → levelId → ``zh_CN/gamedata/levels/<levelId>.json``。"""
    fake = FakeGamedata()
    payload = await service(fake).level_data("1-7")
    assert fake.calls == [LEVEL_ID]
    assert payload["stage"] == {
        "code": "1-7",
        "name": "暴君",
        "stage_id": "main_01-07",  # `#f#` 变体被丢掉
        "level_id": LEVEL_ID,
    }
    assert payload["level_id"] == LEVEL_ID
    assert payload["query"] == "1-7"


@async_test
async def test_stage_id_and_level_id_also_resolve() -> None:
    """stageId / levelId / 中文名都能定位（与 get_stage 同一套索引）。"""
    for query in ("main_01-07", LEVEL_ID, "暴君"):
        payload = await service().level_data(query)
        assert payload["level_id"] == LEVEL_ID, query


@async_test
async def test_unknown_stage_raises_not_found() -> None:
    with pytest.raises(AppError) as info:
        await service().level_data("不存在的关卡-99")
    assert info.value.code is ErrorCode.GAME_DATA_NOT_FOUND
    with pytest.raises(AppError) as empty:
        await service().level_data("   ")
    assert empty.value.code is ErrorCode.INVALID_PARAMETER


@async_test
async def test_gamedata_error_propagates() -> None:
    """来源取不到时如实抛错，不降级成空图（§3.5）。"""
    fake = FakeGamedata(
        error=AppError(ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE, "镜像挂了", {"kind": "gamedata"})
    )
    with pytest.raises(AppError) as info:
        await service(fake).level_data("1-7")
    assert info.value.code is ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE


# --------------------------------------------------------------------------- #
# 返回形状
# --------------------------------------------------------------------------- #


@async_test
async def test_default_returns_every_group_with_envelope() -> None:
    """默认返回四个字段组 + 来源信封（``untrusted`` 与来源链接）。"""
    payload = await service().level_data("1-7")
    assert payload["include"] == list(ALL_GROUPS)
    assert set(payload) >= {"options", "map", "routes", "waves", "truncated", "bytes"}
    assert payload["options"]["deploy_limit"] == 8
    assert payload["map"]["width"] == 11
    assert payload["routes"]
    assert payload["waves"]
    assert payload["untrusted"] is True
    assert payload["sources"][0]["kind"] == "gamedata"
    assert payload["unavailable"] == []
    assert payload["bytes"] <= payload["max_bytes"] == 24 * 1024
    assert payload["source_ref"]["url"] == "https://github.com/Kengxxiao/ArknightsGameData"
    assert payload["enemy_names_note"].startswith("关卡文件只带敌人 key")


@async_test
async def test_include_subset_and_wave_detail() -> None:
    """``include`` 与 ``wave_detail`` 透传到打包器。"""
    payload = await service().level_data("1-7", include=["waves"], wave_detail="full")
    assert payload["include"] == ["waves"]
    assert "map" not in payload and "routes" not in payload and "options" not in payload
    assert payload["wave_detail"] == "full"
    assert payload["waves_detail"] == "full"
    summary = await service().level_data("1-7", include=["waves"])
    assert len(summary["waves"]) <= len(payload["waves"])


@async_test
async def test_invalid_include_and_wave_detail() -> None:
    with pytest.raises(AppError) as info:
        await service().level_data("1-7", include=["map", "nope"])
    assert info.value.code is ErrorCode.INVALID_PARAMETER
    assert info.value.details["include"] == ["map", "nope"]
    with pytest.raises(AppError) as detail:
        await service().level_data("1-7", wave_detail="everything")
    assert detail.value.code is ErrorCode.INVALID_PARAMETER


@async_test
async def test_region_note_follows_client_type() -> None:
    """外服渠道带 ``region_note``（规格 §4.3）。"""
    from maa_api.settings import Settings, SourcesSettings

    foreign = StageLevelService(
        gamedata=FakeGamedata(),
        settings_provider=lambda: Settings(
            channel={"client_type": "YoStarJP"}, sources=SourcesSettings()
        ),
    )
    payload = await foreign.level_data("1-7", include=["options"])
    assert payload["region_note"]
    cn = await service().level_data("1-7", include=["options"])
    assert cn["region_note"] is None


def test_default_service_is_a_shared_instance() -> None:
    assert default_stage_level_service() is default_stage_level_service()

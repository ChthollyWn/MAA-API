"""作业草稿（规格 §9.2 的 D2 修复、§9.5）：协议校验、静态检查与资源落库。

协议校验与静态检查用纯函数（`services/copilot/rules.py`）直接验；落库用
`tests/services/conftest.py` 的临时库与会话工厂，另配一个**假 ResourceService**
（真 service 需要资源目录与自定义层，不属于本任务的写范围）。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from functools import wraps
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.db.models import ResourceAsset
from maa_api.db.repositories.resource import ResourceAssetRepository
from maa_api.domain.enums import ResourceAssetKind
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.copilot.draft import MAX_NOTE_CHARS, CopilotDraftService
from maa_api.services.copilot.rules import (
    PROTOCOL_NOTE,
    normalize_content,
    static_check,
)
from maa_api.services.external.copilot_site import CopilotRow, CopilotSiteData

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "copilot"


def async_test(func):
    def wrapper(*args, **kwargs):
        return asyncio.run(func(*args, **kwargs))

    return wraps(func)(wrapper)


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def full_content(site_id: int) -> dict[str, Any]:
    """夹具里一份**完整**作业（带 actions）。"""
    return json.loads(fixture(f"get_{site_id}.json")["data"]["content"])


#: 1-7 的地块（真实文件裁的一小块）：外层不可部署、内侧高台可部署。
#: 每格是 `load_stage_layout` 的 3 字符码：地形 可部署 高度。
LAYOUT: dict[str, Any] = {
    "stage_id": "main_01-07",
    "width": 11,
    "height": 7,
    "tiles": [
        ["#00"] * 11,
        ["#00", "#21", "#21", "#21", ".10", ".10", ".10", ".10", ".10", ".00", ".00"],
        ["#00", "#21", ".10", ".10", ".10", "#21", "#21", "#21", "#21", "#00", "#00"],
        ["#00", ".10", ".10", "#00", ".10", ".10", ".10", ".10", ".10", ".00", ".00"],
        ["#00", "#21", ".10", ".10", ".10", "#21", "#21", "#21", "#21", "#00", "#00"],
        ["#00", "#21", "#21", "#21", ".10", ".10", ".10", ".10", ".10", ".00", ".00"],
        ["#00"] * 11,
    ],
}

#: 干员基础数据（`battle_data.json` 的裁剪）：position 判近战/远程，rarity 推技能数。
OPERATORS: dict[str, dict[str, Any]] = {
    "稀音": {"char_id": "char_336_folivo", "position": "RANGED", "rarity": 5},
    "令": {"char_id": "char_2023_ling", "position": "RANGED", "rarity": 6},
    "浊心斯卡蒂": {"char_id": "char_1012_skadi2", "position": "RANGED", "rarity": 6},
    "塑心": {"char_id": "char_245_cello", "position": "RANGED", "rarity": 6},
    "克洛丝": {"char_id": "char_124_kroos", "position": "RANGED", "rarity": 3},
    "黑角": {"char_id": "char_500_noirc", "position": "MELEE", "rarity": 2},
    "芬": {"char_id": "char_123_fang", "position": "MELEE", "rarity": 3},
    "移动摄影器": {
        "char_id": "token_10010_folivo_car",
        "position": "MELEE",
        "rarity": 5,
    },
    "维什戴尔": {"char_id": "char_1035_wisdel", "position": "RANGED", "rarity": 6},
    "艾拉": {"char_id": "char_4123_ela", "position": "RANGED", "rarity": 6},
    "新约能天使": {"char_id": "char_1041_angel2", "position": "RANGED", "rarity": 6},
    "逻各斯": {"char_id": "char_4133_logos", "position": "RANGED", "rarity": 6},
    "能天使": {"char_id": "char_103_angel", "position": "RANGED", "rarity": 6},
    "艾雅法拉": {"char_id": "char_180_amgoat", "position": "RANGED", "rarity": 6},
    "夜莺": {"char_id": "char_179_cgbird", "position": "RANGED", "rarity": 6},
    "白面鸮": {"char_id": "char_128_plosis", "position": "RANGED", "rarity": 5},
}

BOX_ROWS: list[dict[str, Any]] = [
    {
        "char_id": "char_336_folivo",
        "name": "稀音",
        "rarity": 5,
        "own": True,
        "elite": 1,
        "level": 60,
        "skill_level": 7,
        "masteries": {"1": 0, "2": 0, "3": 0},
        "modules": {},
    },
    {
        "char_id": "char_500_noirc",
        "name": "黑角",
        "rarity": 2,
        "own": True,
        "elite": 0,
        "level": 30,
        "skill_level": 7,
        "masteries": {"1": 0, "2": 0, "3": 0},
        "modules": {},
    },
    {
        "char_id": "char_124_kroos",
        "name": "克洛丝",
        "rarity": 3,
        "own": True,
        "elite": 1,
        "level": 55,
        "skill_level": 7,
        "masteries": {"1": 1, "2": 0, "3": 0},
        "modules": {},
    },
    # 下面这几条让作业站夹具里的两份真实作业（22311 / 53355）能原样通过校验。
    {
        "char_id": "char_245_cello",
        "name": "塑心",
        "rarity": 6,
        "own": True,
        "elite": 2,
        "level": 60,
        "skill_level": 7,
        "masteries": {"1": 0, "2": 0, "3": 0},
        "modules": {"X": 1},
    },
    {
        "char_id": "char_1012_skadi2",
        "name": "浊心斯卡蒂",
        "rarity": 6,
        "own": True,
        "elite": 2,
        "level": 90,
        "skill_level": 7,
        "masteries": {"1": 0, "2": 3, "3": 0},
        "modules": {"Y": 2},
    },
    {
        "char_id": "char_2023_ling",
        "name": "令",
        "rarity": 6,
        "own": True,
        "elite": 2,
        "level": 90,
        "skill_level": 7,
        "masteries": {"1": 3, "2": 0, "3": 0},
        "modules": {},
    },
    {
        "char_id": "char_1035_wisdel",
        "name": "维什戴尔",
        "rarity": 6,
        "own": True,
        "elite": 2,
        "level": 90,
        "skill_level": 7,
        "masteries": {"1": 3, "2": 0, "3": 0},
        "modules": {"X": 3},
    },
    {
        "char_id": "char_4123_ela",
        "name": "艾拉",
        "rarity": 6,
        "own": True,
        "elite": 2,
        "level": 90,
        "skill_level": 7,
        "masteries": {"1": 3, "2": 0, "3": 0},
        "modules": {"X": 3},
    },
    {
        "char_id": "char_1041_angel2",
        "name": "新约能天使",
        "rarity": 6,
        "own": True,
        "elite": 2,
        "level": 90,
        "skill_level": 7,
        "masteries": {"1": 3, "2": 0, "3": 0},
        "modules": {"X": 3},
    },
    {
        "char_id": "char_4133_logos",
        "name": "逻各斯",
        "rarity": 6,
        "own": True,
        "elite": 2,
        "level": 90,
        "skill_level": 7,
        "masteries": {"1": 3, "2": 0, "3": 0},
        "modules": {"X": 3},
    },
    {
        "char_id": "char_103_angel",
        "name": "能天使",
        "rarity": 6,
        "own": True,
        "elite": 2,
        "level": 90,
        "skill_level": 7,
        "masteries": {"1": 3, "2": 0, "3": 0},
        "modules": {"X": 3},
    },
    {
        "char_id": "char_180_amgoat",
        "name": "艾雅法拉",
        "rarity": 6,
        "own": True,
        "elite": 2,
        "level": 90,
        "skill_level": 7,
        "masteries": {"1": 3, "2": 0, "3": 0},
        "modules": {"X": 3},
    },
]


class FakeBox:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows if rows is not None else BOX_ROWS
        self.calls: list[dict[str, Any]] = []

    async def list_operators(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"items": self.rows, "total": len(self.rows)}


class FakeStageLevel:
    def __init__(self, limit: int | None = 8) -> None:
        self.limit = limit
        self.calls: list[str] = []

    async def level_data(self, stage: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(stage)
        return {"options": {"deploy_limit": self.limit}}


class FakeSiteClient:
    def __init__(self, rows: list[CopilotRow] | None = None) -> None:
        self.rows = rows or []
        self.calls: list[Any] = []

    async def get(self, site_id: int | str) -> CopilotSiteData:
        self.calls.append(site_id)
        return CopilotSiteData(
            endpoint="get",
            url=f"https://prts.maa.plus/copilot/get/{site_id}",
            fetched_at=datetime(2026, 10, 10, 12, 0),
            cached=False,
            stale=False,
            rows=tuple(self.rows),
            status_code=200,
        )


class FakeResourceService:
    """假的 ResourceService：按真 service 的 wire 形状写一条资源行。

    真 service 的校验比官方协议严（D2），且需要一个资源目录与自定义层；这里只替掉
    「校验 + 落盘」那一段，落库仍走真仓储，好让 `CopilotDraftService._write_meta`
    面对的是真行。
    """

    def __init__(self, session_factory: Any) -> None:
        self.session_factory = session_factory
        self.saved: list[dict[str, Any]] = []

    async def upload_copilot(
        self, *, name: str, description: str | None, content: dict[str, Any]
    ) -> dict[str, Any]:
        asset_id = uuid.uuid4().hex
        self.saved.append(
            {"id": asset_id, "name": name, "description": description, "content": content}
        )
        asset = ResourceAsset(
            id=asset_id,
            kind=ResourceAssetKind.COPILOT,
            name=name,
            description=description,
            content=content,
            meta={
                "stage_name": content.get("stage_name"),
                "title": (content.get("doc") or {}).get("title")
                if isinstance(content.get("doc"), dict)
                else None,
            },
            checksum="0" * 64,
        )
        async with self.session_factory() as session:
            stored = await ResourceAssetRepository(session).create(asset)
            await session.commit()
        return {
            "id": stored.id,
            "kind": str(stored.kind),
            "name": stored.name,
            "description": stored.description,
            "meta": stored.meta,
            "enabled": True,
            "checksum": stored.checksum,
            "created_at": "2026-10-10T04:00:00",
            "updated_at": "2026-10-10T04:00:00",
        }


def service(
    session_factory: Any,
    *,
    resource_service: Any | None = None,
    box: Any | None = None,
    stage_level: Any | None = None,
    site_client: Any | None = None,
    layout: Any = LAYOUT,
    operators: Any = OPERATORS,
) -> tuple[CopilotDraftService, FakeResourceService]:
    resources = (
        resource_service
        if resource_service is not None
        else FakeResourceService(session_factory)
    )
    draft = CopilotDraftService(
        session_factory,
        resource_service=resources,
        box=box if box is not None else FakeBox(),
        stage_level=stage_level if stage_level is not None else FakeStageLevel(),
        site_client=site_client,
        layout_provider=lambda stage: layout,
        operators_provider=lambda: operators,
    )
    return draft, resources


# ---------------------------------------------------------------------------
# 协议校验（D2 修复后的口径）
# ---------------------------------------------------------------------------
def simple(**overrides: Any) -> dict[str, Any]:
    content: dict[str, Any] = {
        "stage_name": "main_01-07",
        "minimum_required": "v6.0.0",
        "doc": {"title": "测试"},
        "opers": [{"name": "稀音"}],
        "groups": [],
        "actions": [{"type": "SpeedUp"}],
    }
    content.update(overrides)
    return content


def test_protocol_accepts_version_and_difficulty() -> None:
    normalized = normalize_content(simple(version=3, difficulty=1))
    assert normalized["version"] == 3
    assert normalized["difficulty"] == 1


def test_protocol_accepts_missing_doc_title() -> None:
    normalized = normalize_content(simple(doc={"details": "只有详情"}))
    assert normalized["doc"] == {"details": "只有详情"}
    assert normalize_content(simple(doc="纯字符串"))["doc"] == "纯字符串"


def test_protocol_accepts_skill_zero_and_usage_three() -> None:
    normalized = normalize_content(
        simple(opers=[{"name": "稀音", "skill": 0, "skill_usage": 3}])
    )
    assert normalized["opers"][0]["skill"] == 0
    assert normalized["opers"][0]["skill_usage"] == 3


def test_protocol_accepts_action_costs() -> None:
    normalized = normalize_content(
        simple(
            actions=[
                {"type": "Deploy", "name": "稀音", "location": [8, 2], "costs": 50},
                {"type": "Skill", "name": "稀音", "costs": 0, "cost_changes": -2},
            ]
        )
    )
    assert normalized["actions"][0]["costs"] == 50


def test_protocol_accepts_potentiality_key() -> None:
    normalized = normalize_content(
        simple(
            opers=[
                {
                    "name": "予愿安洁莉娜",
                    "skill": 3,
                    "requirements": {"elite": 2, "potentiality": 1, "module": 2},
                }
            ]
        )
    )
    assert normalized["opers"][0]["requirements"]["potentiality"] == 1


def test_protocol_accepts_click_and_swipe_actions() -> None:
    normalized = normalize_content(
        simple(
            actions=[
                {"type": "Click", "location": [1, 1]},
                {"type": "Swipe", "begin": [0, 0, 10, 10], "end": [100, 100, 10, 10]},
                {"type": "SetUnitLocation", "name": "稀音", "location": [8, 2]},
                {"type": "MoveCamera", "distance": [1, -1], "location": [5, 6]},
            ]
        )
    )
    assert [action["type"] for action in normalized["actions"]] == [
        "Click",
        "Swipe",
        "SetUnitLocation",
        "MoveCamera",
    ]


def test_protocol_rejects_wrong_shapes() -> None:
    with pytest.raises(AppError) as info:
        normalize_content([1, 2, 3])
    assert info.value.code is ErrorCode.COPILOT_JSON_INVALID

    with pytest.raises(AppError) as info:
        normalize_content(simple(unknown_field=1))
    assert "未知顶层字段" in (info.value.message or "")

    with pytest.raises(AppError):
        normalize_content(simple(stage_name=""))

    with pytest.raises(AppError):
        normalize_content(simple(minimum_required=""))

    with pytest.raises(AppError):
        normalize_content(simple(actions="nope"))

    with pytest.raises(AppError):
        normalize_content(simple(actions=[{"type": "Deploy", "location": "8,2"}]))

    with pytest.raises(AppError):
        normalize_content(simple(opers=[{"skill": 4, "name": "稀音"}]))

    with pytest.raises(AppError):
        normalize_content(simple(opers=[{"skill_usage": 4, "name": "稀音"}]))


def test_protocol_requires_actions_unless_sss() -> None:
    without = simple()
    without.pop("actions")
    with pytest.raises(AppError):
        normalize_content(without)
    assert normalize_content({**without, "type": "SSS", "stages": [{"stage_name": "x"}]})


# ---------------------------------------------------------------------------
# 静态检查
# ---------------------------------------------------------------------------
def check(content: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    options: dict[str, Any] = {
        "layout": LAYOUT,
        "operators": OPERATORS,
        "box": {row["name"]: row for row in BOX_ROWS},
        "deploy_limit": 8,
    }
    options.update(kwargs)
    return static_check(content, **options)


def test_static_check_accepts_a_real_copilot() -> None:
    report = check(full_content(22311))
    assert report["ok"] is True
    assert report["violations"] == []
    assert report["deploy_count"] == 5
    assert report["support_used"] == 0


def test_static_check_accepts_a_real_group_copilot() -> None:
    report = check(full_content(53355))
    assert report["ok"] is True
    assert report["deploy_count"] == 2


def test_out_of_bounds_location_is_rejected() -> None:
    report = check(
        simple(actions=[{"type": "Deploy", "name": "稀音", "location": [11, 2]}])
    )
    assert report["ok"] is False
    assert report["violations"][0]["code"] == "location_out_of_bounds"


def test_unbuildable_tile_is_rejected() -> None:
    report = check(simple(actions=[{"type": "Deploy", "name": "稀音", "location": [0, 0]}]))
    assert report["ok"] is False
    assert report["violations"][0]["code"] == "tile_not_deployable"


def test_melee_on_high_ground_is_rejected() -> None:
    report = check(simple(actions=[{"type": "Deploy", "name": "黑角", "location": [1, 1]}]))
    assert report["ok"] is False
    codes = [item["code"] for item in report["violations"]]
    assert codes == ["tile_position_mismatch"]
    assert report["violations"][0]["position"] == "MELEE"


def test_ranged_on_ground_is_rejected() -> None:
    report = check(simple(actions=[{"type": "Deploy", "name": "克洛丝", "location": [1, 3]}]))
    assert report["ok"] is False
    assert report["violations"][0]["code"] == "tile_position_mismatch"


def test_matching_positions_pass() -> None:
    report = check(
        simple(
            actions=[
                {"type": "Deploy", "name": "黑角", "location": [1, 3]},
                {"type": "Deploy", "name": "克洛丝", "location": [1, 1]},
            ]
        )
    )
    assert report["ok"] is True


def test_unknown_operator_name_is_rejected() -> None:
    report = check(simple(opers=[{"name": "并不存在的干员"}]))
    assert report["ok"] is False
    codes = [item["code"] for item in report["violations"]]
    assert "operator_unknown" in codes


def test_exactly_one_support_slot_is_allowed() -> None:
    """缺一名可以借助战（Q42）；缺两名就拒（错误码是 support_slot_exceeded）。"""
    one = check(simple(opers=[{"name": "稀音"}, {"name": "夜莺"}]))
    assert one["ok"] is True
    assert one["support_used"] == 1

    two = check(simple(opers=[{"name": "夜莺"}, {"name": "白面鸮"}]))
    assert two["ok"] is False
    assert [item["code"] for item in two["violations"]] == ["support_slot_exceeded"]

    owned = check(simple(opers=[{"name": "稀音"}, {"name": "克洛丝"}]))
    assert owned["ok"] is True
    assert owned["support_used"] == 0


def test_support_can_be_disabled() -> None:
    report = check(simple(opers=[{"name": "夜莺"}]), allow_support=False)
    assert report["ok"] is False
    assert report["violations"][0]["code"] == "support_slot_exceeded"


def test_too_many_deploys_is_rejected() -> None:
    actions = [
        {"type": "Deploy", "name": "克洛丝", "location": [1, 1]},
        {"type": "Deploy", "name": "克洛丝", "location": [2, 1]},
        {"type": "Deploy", "name": "黑角", "location": [1, 3]},
    ]
    report = check(simple(actions=actions), deploy_limit=2)
    assert report["ok"] is False
    assert [item["code"] for item in report["violations"]] == ["deploy_limit_exceeded"]


def test_skill_index_above_the_operator_skill_count_is_rejected() -> None:
    # 黑角 2★ → 1 个技能；作业指定第 2 个。
    report = check(simple(opers=[{"name": "黑角", "skill": 2}]))
    assert report["ok"] is False
    assert [item["code"] for item in report["violations"]] == ["skill_index_exceeded"]


def test_skill_zero_is_not_checked() -> None:
    report = check(simple(opers=[{"name": "黑角", "skill": 0}]))
    assert report["ok"] is True


def test_skill_index_check_covers_group_members() -> None:
    report = check(
        simple(
            groups=[
                {
                    "name": "任意先锋",
                    "opers": [
                        {"name": "芬", "skill": 3},
                        {"name": "黑角", "skill": 2},
                    ],
                }
            ]
        )
    )
    codes = [item["code"] for item in report["violations"]]
    assert codes == ["skill_index_exceeded", "skill_index_exceeded"]


def test_missing_map_is_skipped_not_failed() -> None:
    report = check(simple(actions=[{"type": "Deploy", "name": "稀音", "location": [8, 2]}]), layout=None)
    assert report["ok"] is True
    assert "map" in report["skipped"]


def test_missing_operator_data_is_skipped_not_failed() -> None:
    report = check(simple(actions=[{"type": "Deploy", "name": "稀音", "location": [8, 2]}]), operators=None)
    assert report["ok"] is True
    assert "operator_position" in report["skipped"]
    assert "operator_skills" in report["skipped"]


def test_missing_deploy_limit_is_skipped_not_failed() -> None:
    report = check(full_content(22311), deploy_limit=None)
    assert report["ok"] is True
    assert "deploy_limit" in report["skipped"]


def test_click_and_swipe_are_not_checked() -> None:
    report = check(
        simple(
            actions=[
                {"type": "Click", "location": [0, 0]},
                {"type": "Swipe", "begin": [0, 0, 1, 1], "end": [1, 1, 1, 1]},
            ]
        )
    )
    assert report["ok"] is True
    assert report["deploy_count"] == 0


# ---------------------------------------------------------------------------
# 草稿服务
# ---------------------------------------------------------------------------
def make_factory(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@async_test
async def test_save_writes_an_asset_with_the_spec_meta(retention_engine) -> None:
    factory = make_factory(retention_engine)
    draft, resources = service(factory)
    result = await draft.save(
        content=full_content(22311),
        note="第一次尝试",
        session_id="session-1",
    )
    assert resources.saved[0]["content"]["stage_name"] == "main_01-07"
    meta = result["meta"]
    assert meta["source"] == "ai"
    assert meta["stage_id"] == "main_01-07"
    assert meta["site_id"] is None
    assert meta["parent_id"] is None
    assert meta["session_id"] == "session-1"
    assert meta["note"] == "第一次尝试"
    assert meta["attempts"] == []
    assert meta["retain"] is False
    # ResourceService 自己的 meta 保留下来（资源列表按它展示）。
    assert meta["stage_name"] == "main_01-07"
    assert result["source"] == "ai"
    assert result["checks"]["ok"] is True
    assert result["checks"]["protocol"] == PROTOCOL_NOTE

    # 真的落库了，而不是只返回了一个对象。
    async with factory() as session:
        stored = await ResourceAssetRepository(session).get(result["id"])
    assert stored is not None
    assert str(stored.kind) == ResourceAssetKind.COPILOT.value
    assert stored.meta["session_id"] == "session-1"


@async_test
async def test_save_by_site_id_fetches_and_marks_the_source(retention_engine) -> None:
    factory = make_factory(retention_engine)
    row = CopilotRow(
        site_id=22311,
        uploader="Genicur",
        views=833,
        rating_ratio=0.9,
        content=json.dumps(full_content(22311), ensure_ascii=False),
    )
    site = FakeSiteClient([row])
    draft, _resources = service(factory, site_client=site)
    result = await draft.save(site_id=22311, note="导入", session_id="s")
    assert site.calls == [22311]
    assert result["source"] == "site"
    assert result["site_id"] == 22311
    assert result["meta"]["site_id"] == 22311
    assert result["meta"]["uploader"] == "Genicur"
    assert result["link"] == "https://prts.maa.plus/copilot/22311"


@async_test
async def test_save_accepts_a_json_string_and_a_parent(retention_engine) -> None:
    factory = make_factory(retention_engine)
    draft, _resources = service(factory)
    result = await draft.save(
        content=json.dumps(full_content(22311), ensure_ascii=False),
        parent_id="a" * 32,
        note="第二版",
    )
    assert result["parent_id"] == "a" * 32
    assert result["meta"]["source"] == "user"  # 没有 session_id


@async_test
async def test_save_rejects_bad_input(retention_engine) -> None:
    factory = make_factory(retention_engine)
    draft, resources = service(factory)
    with pytest.raises(AppError) as info:
        await draft.save(content=full_content(22311), site_id=22311)
    assert info.value.code is ErrorCode.INVALID_PARAMETER

    with pytest.raises(AppError) as info:
        await draft.save()
    assert info.value.code is ErrorCode.INVALID_PARAMETER

    with pytest.raises(AppError) as info:
        await draft.save(content=full_content(22311), note="x" * (MAX_NOTE_CHARS + 1))
    assert info.value.code is ErrorCode.INVALID_PARAMETER

    with pytest.raises(AppError) as info:
        await draft.save(content="{not json")
    assert info.value.code is ErrorCode.COPILOT_JSON_INVALID

    with pytest.raises(AppError) as info:
        await draft.save(content=full_content(22311), source="bogus")
    assert info.value.code is ErrorCode.INVALID_PARAMETER

    assert resources.saved == []


@async_test
async def test_save_rejects_static_violations(retention_engine) -> None:
    """AI 草稿与作业站副本都过静态检查：坐标出界就不落库（规格 §9.5）。"""
    factory = make_factory(retention_engine)
    draft, resources = service(factory)
    broken = full_content(22311)
    broken["actions"][1] = {**broken["actions"][1], "location": [99, 2]}
    with pytest.raises(AppError) as info:
        await draft.save(content=broken)
    assert info.value.code is ErrorCode.COPILOT_JSON_INVALID
    assert info.value.details["violations"][0]["code"] == "location_out_of_bounds"
    assert resources.saved == []


@async_test
async def test_save_records_skipped_checks(retention_engine) -> None:
    factory = make_factory(retention_engine)
    draft, _resources = service(factory, layout=None, operators=None)
    result = await draft.save(content=full_content(22311))
    assert result["checks"]["skipped"]
    assert result["meta"]["checks_skipped"]


@async_test
async def test_check_does_not_write_anything(retention_engine) -> None:
    factory = make_factory(retention_engine)
    draft, resources = service(factory)
    report = await draft.check(full_content(22311))
    assert report["ok"] is True
    assert report["protocol"] == []
    assert report["static"]["deploy_count"] == 5
    assert resources.saved == []

    bad = await draft.check({"stage_name": "x"})
    assert bad["ok"] is False
    assert bad["protocol"][0]["code"] == ErrorCode.COPILOT_JSON_INVALID.value


@async_test
async def test_deploy_limit_comes_from_the_stage_level_service(retention_engine) -> None:
    factory = make_factory(retention_engine)
    stage_level = FakeStageLevel(limit=2)
    draft, _resources = service(factory, stage_level=stage_level)
    report = await draft.check(full_content(22311))
    assert stage_level.calls == ["main_01-07"]
    assert report["ok"] is False
    assert report["violations"][0]["code"] == "deploy_limit_exceeded"


@async_test
async def test_stage_level_failure_does_not_block_saving(retention_engine) -> None:
    class Broken:
        async def level_data(self, stage: str, **kwargs: Any) -> dict[str, Any]:
            raise AppError(ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE, "关卡文件取不到")

    factory = make_factory(retention_engine)
    draft, _resources = service(factory, stage_level=Broken())
    report = await draft.check(full_content(22311))
    assert report["ok"] is True
    assert "deploy_limit" in report["skipped"]

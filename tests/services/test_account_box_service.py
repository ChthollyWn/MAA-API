"""干员表服务的逻辑契约（规格 §7.3、§7.5）：用假仓储驱动真实服务代码。

与 ``test_account_box.py`` 的分工：
- 本文件**不碰数据库**，把 ``AccountOperatorRepository`` 等三个类换成内存假替身，
  验证合并规则（覆盖 / 保留 / 差异）、编辑后的写入与来源标记、概要计数与过期判定。
- ``test_account_box.py`` 是同一个服务的 SQLite 集成版本。两者共用同一批场景名，
  这样「假仓储绿了、真仓储没绿」时能一眼看出是仓储接口的差异，而不是业务逻辑。

假仓储只实现服务用到的那些方法，签名与 ``db/repositories/account.py`` 冻结的接口
一致；差异出现时在这里加一层适配，而不是让服务层为测试让步。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import Settings

from maa_api.services.account import box as box_module  # noqa: E402
from maa_api.services.account.box import OperatorBoxService  # noqa: E402

NOW = datetime(2026, 10, 10, 3, 0, 0)


def _utc(*, days_ago: int = 0) -> datetime:
    return NOW - timedelta(days=days_ago)


class _Row:
    """一条干员行：属性名与 ``account_operator`` 的列同名。"""

    def __init__(self, char_id: str, **fields: Any) -> None:
        self.char_id = char_id
        self.name: str | None = None
        self.rarity: int | None = None
        self.profession: str | None = None
        self.own = False
        self.elite = 0
        self.level = 0
        self.potential = 0
        self.skill_level = 1
        self.masteries: dict[str, int] = {}
        self.modules: dict[str, int] = {}
        self.recognized_values: dict[str, Any] = {}
        self.field_sources: dict[str, str] = {}
        self.recognized_at: datetime | None = None
        self.updated_at: datetime | None = None
        for key, value in fields.items():
            setattr(self, key, value)


class _Diff:
    """一条差异行：属性名与 ``account_operator_diff`` 的列同名（wire 渲染按属性读）。"""

    def __init__(self, **fields: Any) -> None:
        self.id = 0
        self.char_id = ""
        self.name: str | None = None
        self.field = ""
        self.manual_value: Any = None
        self.recognized_value: Any = None
        self.created_at: datetime | None = None
        self.dismissed_at: datetime | None = None
        for key, value in fields.items():
            setattr(self, key, value)


class _Store:
    """三个假仓储共享的内存状态。"""

    def __init__(self) -> None:
        self.rows: dict[str, _Row] = {}
        self.diffs: list[_Diff] = []
        self.recognitions: list[dict[str, Any]] = []
        self.next_diff_id = 1


def _make_fakes(store: _Store) -> dict[str, Any]:
    """构造与冻结接口同形的假仓储类。"""

    class AccountOperatorRepository:
        def __init__(self, session: Any = None) -> None:
            self.session = session

        def new_row(self, char_id: str, **fields: Any) -> _Row:
            return _Row(char_id, **fields)

        async def get(self, char_id: str) -> _Row | None:
            return store.rows.get(char_id)

        async def upsert(self, row: _Row) -> None:
            store.rows[row.char_id] = row

        def _filtered(
            self,
            *,
            query: str | None,
            owned_only: bool,
            rarity_min: int | None,
            profession: str | None,
            elite_min: int | None,
        ) -> list[_Row]:
            rows = sorted(store.rows.values(), key=lambda item: item.char_id)
            if owned_only:
                rows = [row for row in rows if row.own]
            if query:
                needle = query.casefold()
                rows = [
                    row
                    for row in rows
                    if needle in (row.name or "").casefold()
                    or needle in row.char_id.casefold()
                ]
            if rarity_min is not None:
                rows = [row for row in rows if (row.rarity or 0) >= rarity_min]
            if profession:
                rows = [row for row in rows if row.profession == profession]
            if elite_min is not None:
                rows = [row for row in rows if row.elite >= elite_min]
            return rows

        async def count(
            self,
            *,
            query: str | None = None,
            owned_only: bool = False,
            rarity_min: int | None = None,
            profession: str | None = None,
            elite_min: int | None = None,
        ) -> int:
            return len(
                self._filtered(
                    query=query,
                    owned_only=owned_only,
                    rarity_min=rarity_min,
                    profession=profession,
                    elite_min=elite_min,
                )
            )

        async def list(
            self,
            *,
            query: str | None = None,
            owned_only: bool = False,
            rarity_min: int | None = None,
            profession: str | None = None,
            elite_min: int | None = None,
            limit: int = 50,
            offset: int = 0,
        ) -> list[_Row]:
            rows = self._filtered(
                query=query,
                owned_only=owned_only,
                rarity_min=rarity_min,
                profession=profession,
                elite_min=elite_min,
            )
            return rows[offset : offset + limit]

        async def last_recognized_at(self) -> datetime | None:
            stamps = [
                row.recognized_at for row in store.rows.values() if row.recognized_at
            ]
            return max(stamps) if stamps else None

    class AccountOperatorDiffRepository:
        def __init__(self, session: Any = None) -> None:
            self.session = session

        async def create(
            self,
            *,
            char_id: str,
            field: str,
            manual_value: Any,
            recognized_value: Any,
            created_at: datetime,
        ) -> dict[str, Any]:
            row = _Diff(
                id=store.next_diff_id,
                char_id=char_id,
                name=store.rows[char_id].name if char_id in store.rows else None,
                field=field,
                manual_value=manual_value,
                recognized_value=recognized_value,
                created_at=created_at,
            )
            store.next_diff_id += 1
            store.diffs.append(row)
            return row

        async def list(
            self, *, include_dismissed: bool = False, limit: int = 200
        ) -> list[_Diff]:
            rows = [
                row
                for row in store.diffs
                if include_dismissed or row.dismissed_at is None
            ]
            return rows[:limit]

        async def count_pending(self) -> int:
            return sum(1 for row in store.diffs if row.dismissed_at is None)

        async def dismiss(self, ids: Any = None, **_: Any) -> int:
            changed = 0
            for row in store.diffs:
                if row.dismissed_at is not None:
                    continue
                if ids is not None and row.id not in ids:
                    continue
                row.dismissed_at = _utc()
                changed += 1
            return changed

    class AccountRecognitionRepository:
        def __init__(self, session: Any = None) -> None:
            self.session = session

        async def record(
            self,
            *,
            kind: str,
            pipeline_id: str | None,
            task_id: str | None,
            client_type: str | None,
            item_count: int,
            recognized_at: datetime,
        ) -> dict[str, Any]:
            row = {
                "kind": kind,
                "pipeline_id": pipeline_id,
                "task_id": task_id,
                "client_type": client_type,
                "item_count": item_count,
                "recognized_at": recognized_at,
            }
            store.recognitions.append(row)
            return row

        async def last_at(self, kind: str) -> datetime | None:
            stamps = [
                row["recognized_at"]
                for row in store.recognitions
                if row["kind"] == kind
            ]
            return max(stamps) if stamps else None

    return {
        "AccountOperatorRepository": AccountOperatorRepository,
        "AccountOperatorDiffRepository": AccountOperatorDiffRepository,
        "AccountRecognitionRepository": AccountRecognitionRepository,
    }


@pytest.fixture
def box(monkeypatch):
    """``(service, store, session_factory)``：真实服务 + 假仓储（三个仓储名被换掉）。"""
    store = _Store()
    fakes = _make_fakes(store)
    # 服务层的仓储入口是懒加载的 ``_repositories()``（模块级不 import db.repositories），
    # 这里把它整个换掉。
    monkeypatch.setattr(
        box_module,
        "_repositories",
        lambda: (
            fakes["AccountOperatorRepository"],
            fakes["AccountOperatorDiffRepository"],
            fakes["AccountRecognitionRepository"],
        ),
    )

    class _Session:
        async def commit(self) -> None:
            return None

        async def rollback(self) -> None:
            return None

        async def close(self) -> None:
            return None

    @asynccontextmanager
    async def session_factory():
        yield _Session()

    service = OperatorBoxService(
        session_factory, settings_provider=lambda: _settings(stale_days=14)
    )
    return service, store, session_factory


def _seed(store: _Store, char_id: str, **fields: Any) -> _Row:
    elite = int(fields.get("elite", 2) or 0)
    # 专精与模组只在精二时存在：种子行不能造出编辑约束不允许的状态，否则后续
    # PATCH 会被跨字段校验拦下（测的就不是本意了）。
    defaults: dict[str, Any] = dict(
        name="测试干员",
        rarity=6,
        profession="近卫",
        own=True,
        elite=elite,
        level=90,
        potential=6,
        skill_level=7,
        masteries={"1": 3, "2": 0, "3": 0} if elite >= 2 else {"1": 0, "2": 0, "3": 0},
        modules={"X": 3} if elite >= 2 else {},
        recognized_values={
            "own": True,
            "elite": 2,
            "level": fields.get("recognized_level", 90),
            "potential": 6,
        },
        field_sources={
            "own": "recognition",
            "elite": "recognition",
            "level": "recognition",
            "potential": "recognition",
            "skill_level": "manual",
            "masteries": "manual",
            "modules": "manual",
        },
        recognized_at=_utc(),
        updated_at=_utc(),
    )
    defaults.update(fields)
    defaults.pop("recognized_level", None)
    row = _Row(char_id, **defaults)
    store.rows[char_id] = row
    return row


# ---------------------------------------------------------------------------
# 识别合并
# ---------------------------------------------------------------------------


def test_first_recognition_creates_rows_and_records_fields(box):
    service, store, _factory = box

    async def scenario():
        result = await service.apply_recognition(
            {
                "char_002_amiya": {
                    "name": "阿米娅",
                    "rarity": 5,
                    "profession": "术师",
                    "own": True,
                    "elite": 2,
                    "level": 80,
                    "potential": 6,
                }
            },
            now=NOW,
            recognized_at=NOW,
        )
        assert result == {
            "created": 1,
            "updated": 0,
            "diffs": 0,
            "kept_owned": [],
        }
        detail = await service.get_operator("char_002_amiya")
        assert detail["name"] == "阿米娅"
        assert detail["rarity"] == 5
        assert detail["profession"] == "术师"
        assert detail["elite"] == 2
        assert detail["level"] == 80
        assert detail["potential"] == 6
        assert detail["field_sources"] == {
            "own": "recognition",
            "elite": "recognition",
            "level": "recognition",
            "potential": "recognition",
        }
        assert detail["recognized_at"] == "2026-10-10T03:00:00Z"
        assert detail["updated_at"] == "2026-10-10T03:00:00Z"
        assert detail["masteries"] == {"1": 0, "2": 0, "3": 0}
        assert detail["modules"] == {}
        # 识别也在 account_recognition 里留了一条 operbox 记录。
        assert [item["kind"] for item in store.recognitions] == ["operbox"]

    asyncio.run(scenario())


def test_recognition_overwrites_only_its_fields_and_keeps_manual_ones(box):
    service, store, _factory = box
    _seed(store, "char_002_amiya", level=90, elite=2)

    async def scenario():
        result = await service.apply_recognition(
            {
                "char_002_amiya": {
                    "name": "阿米娅",
                    "rarity": 6,
                    "own": True,
                    "elite": 1,
                    "level": 55,
                    "potential": 3,
                }
            },
            now=NOW,
            recognized_at=NOW,
        )
        assert result["diffs"] == 0
        detail = await service.get_operator("char_002_amiya")
        assert (detail["elite"], detail["level"], detail["potential"]) == (1, 55, 3)
        assert detail["skill_level"] == 7
        assert detail["masteries"] == {"1": 3, "2": 0, "3": 0}
        assert detail["modules"] == {"X": 3}
        assert detail["field_sources"]["masteries"] == "manual"
        assert detail["field_sources"]["skill_level"] == "manual"

    asyncio.run(scenario())


def test_recognition_records_a_diff_when_it_overwrites_a_manual_value(box):
    service, store, _factory = box
    _seed(
        store,
        "char_002_amiya",
        level=55,
        recognized_level=80,
        field_sources={
            "own": "recognition",
            "elite": "recognition",
            "level": "manual",
            "potential": "recognition",
        },
    )

    async def scenario():
        result = await service.apply_recognition(
            {"char_002_amiya": {"own": True, "elite": 2, "level": 90, "potential": 6}},
            now=NOW,
            recognized_at=NOW,
        )
        assert result["diffs"] == 1
        detail = await service.get_operator("char_002_amiya")
        assert detail["level"] == 90
        assert detail["field_sources"]["level"] == "recognition"

        diffs = await service.list_diffs()
        assert diffs["total"] == 1
        item = diffs["items"][0]
        assert item["char_id"] == "char_002_amiya"
        assert item["name"] == "测试干员"
        assert item["field"] == "level"
        assert item["manual_value"] == 55
        assert item["recognized_value"] == 90
        assert item["dismissed_at"] is None
        assert item["created_at"] == "2026-10-10T03:00:00Z"

    asyncio.run(scenario())


def test_recognition_does_not_record_a_diff_when_the_values_agree(box):
    service, store, _factory = box
    _seed(
        store,
        "char_002_amiya",
        level=90,
        recognized_level=80,
        field_sources={
            "own": "recognition",
            "elite": "recognition",
            "level": "manual",
            "potential": "recognition",
        },
    )

    async def scenario():
        result = await service.apply_recognition(
            {"char_002_amiya": {"own": True, "elite": 2, "level": 90, "potential": 6}},
            now=NOW,
            recognized_at=NOW,
        )
        assert result["diffs"] == 0
        assert (await service.list_diffs())["total"] == 0
        # 值没变，但来源跟着识别走（识别重新确认了它）。
        assert store.rows["char_002_amiya"].field_sources["level"] == "recognition"

    asyncio.run(scenario())


def test_recognition_marks_operators_missing_from_the_owned_set_as_not_owned(box):
    """``all_opers`` 里、但本次识别为已拥有的那批（``operators``）里没有的 → ``own=False``。

    ``operators`` 在 capture 层由 ``all_opers`` 里 ``own=true`` 的条目拼出（M33 §4.2），
    不是内核载荷里的 ``own_opers`` 字段（见 `docs/13-决策记录.md` §8.48 二）。
    """
    service, store, _factory = box
    _seed(store, "char_002_amiya")

    async def scenario():
        result = await service.apply_recognition(
            {},
            all_operators=["char_002_amiya", "char_103_angel"],
            now=NOW,
            recognized_at=NOW,
        )
        assert result == {
            "created": 1,
            "updated": 1,
            "diffs": 0,
            "kept_owned": [],
        }
        owned = await service.list_operators(owned_only=True)
        assert [item["char_id"] for item in owned["items"]] == []
        everyone = await service.list_operators(owned_only=False)
        assert {item["char_id"] for item in everyone["items"]} == {
            "char_002_amiya",
            "char_103_angel",
        }
        assert all(item["own"] is False for item in everyone["items"])

    asyncio.run(scenario())


def test_recognition_with_keep_owned_keeps_the_value_and_reports_it(box):
    """M33 护栏 G2（规格 §4.3，用户裁决「识别只加不减」）：识别说未拥有也不改已拥有。"""
    service, store, _factory = box
    _seed(store, "char_103_angel", own=True)

    async def scenario():
        result = await service.apply_recognition(
            {"char_103_angel": {"name": "能天使", "rarity": 6, "own": False}},
            all_operators=["char_103_angel"],
            now=NOW,
            recognized_at=NOW,
            keep_owned=True,
        )
        assert result["kept_owned"] == ["char_103_angel"]
        row = store.rows["char_103_angel"]
        assert row.own is True
        # 值没变，所以没有差异要用户确认；来源仍按本次识别记。
        assert (await service.list_diffs())["total"] == 0
        assert row.field_sources["own"] == "recognition"
        assert row.recognized_values["own"] is True
        # 练度不受影响（识别这次没给）。
        assert (row.elite, row.level, row.potential) == (2, 90, 6)

        # 关掉开关时仍是「识别说了算」——默认行为没有变。
        result = await service.apply_recognition(
            {"char_103_angel": {"own": False}},
            all_operators=["char_103_angel"],
            now=NOW,
            recognized_at=NOW,
        )
        assert result["kept_owned"] == []
        assert store.rows["char_103_angel"].own is False

    asyncio.run(scenario())


def test_recognition_keep_owned_does_not_protect_new_rows(box):
    """新建行的 ``own=True`` 只是占位：本次新出现的 id 要按识别值落成未拥有。"""
    service, store, _factory = box

    async def scenario():
        result = await service.apply_recognition(
            {"char_103_angel": {"name": "能天使", "rarity": 6, "own": False}},
            all_operators=["char_103_angel"],
            now=NOW,
            recognized_at=NOW,
            keep_owned=True,
        )
        assert result["kept_owned"] == []
        assert store.rows["char_103_angel"].own is False

    asyncio.run(scenario())


def test_recognition_keep_owned_respects_a_manual_unown(box):
    """裁决 A 的另一半：真要取消拥有走手动 / Agent 编辑，识别不会再改回来。"""
    service, store, _factory = box
    _seed(store, "char_103_angel", own=True)

    async def scenario():
        await service.patch_operator("char_103_angel", {"own": False})
        assert store.rows["char_103_angel"].own is False
        assert store.rows["char_103_angel"].field_sources["own"] == "manual"

        result = await service.apply_recognition(
            {"char_103_angel": {"own": False}},
            all_operators=["char_103_angel"],
            now=NOW,
            recognized_at=NOW,
            keep_owned=True,
        )
        assert result["kept_owned"] == []
        assert store.rows["char_103_angel"].own is False

    asyncio.run(scenario())


def test_recognition_without_all_opers_does_not_clear_other_rows(box):
    service, store, _factory = box
    _seed(store, "char_002_amiya", own=True)

    async def scenario():
        await service.apply_recognition(
            {"char_103_angel": {"name": "能天使", "rarity": 6, "own": True}},
            now=NOW,
            recognized_at=NOW,
        )
        ami = await service.get_operator("char_002_amiya")
        assert ami["own"] is True
        assert ami["level"] == 90

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 编辑约束
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rarity", "cap"), [(1, 0), (2, 0), (3, 1), (4, 2), (5, 2), (6, 2)]
)
def test_elite_cap_follows_rarity(box, rarity, cap):
    service, store, _factory = box
    _seed(store, "char_x", rarity=rarity, elite=0)

    async def scenario():
        assert (await service.patch_operator("char_x", {"elite": cap}))["elite"] == cap
        with pytest.raises(AppError) as caught:
            await service.patch_operator("char_x", {"elite": cap + 1})
        assert caught.value.code == ErrorCode.VALIDATION_ERROR
        assert caught.value.http_status == 422
        if cap < 2:
            # 稀有度上限本身就是拒绝理由（1–3★）。
            assert caught.value.details == {
                "field": "elite",
                "reason": f"{rarity}★ 干员的精英化上限是 {cap}",
                "allowed": {"maximum": cap, "rarity": rarity},
            }
        else:
            # 4–6★ 的上限就是全表上限 2：越界先撞字段范围。
            assert caught.value.details == {
                "field": "elite",
                "reason": "超出取值范围",
                "allowed": {"minimum": 0, "maximum": 2},
            }

    asyncio.run(scenario())


def test_masteries_require_elite_two_and_skill_seven(box):
    service, store, _factory = box
    _seed(store, "char_x", elite=1, skill_level=7, masteries={})

    async def scenario():
        with pytest.raises(AppError) as caught:
            await service.patch_operator("char_x", {"masteries": {"1": 1}})
        assert caught.value.details["field"] == "masteries"
        assert caught.value.details["reason"] == "专精只在精二时可填"

        await service.patch_operator("char_x", {"elite": 2, "skill_level": 6})
        with pytest.raises(AppError) as caught:
            await service.patch_operator("char_x", {"masteries": {"1": 1}})
        assert caught.value.details["reason"] == "专精要求技能等级为 7"
        assert caught.value.details["allowed"] == {"skill_level_required": 7}

        detail = await service.patch_operator(
            "char_x", {"skill_level": 7, "masteries": {"1": 3, "2": 0, "3": 0}}
        )
        assert detail["masteries"] == {"1": 3, "2": 0, "3": 0}
        assert detail["field_sources"]["masteries"] == "manual"
        assert detail["field_sources"]["skill_level"] == "manual"

    asyncio.run(scenario())


def test_lowering_skill_level_below_seven_is_rejected_while_masteries_exist(box):
    """已有专精时技能等级必须保持 7；先清空专精才能降技能等级。

    报错字段落在 ``masteries`` 上（是「专精要求技能 7」这条约束不满足了），
    与 PATCH 只带 masteries 时的同一条文案一致。
    """
    service, store, _factory = box
    _seed(store, "char_x", elite=2, skill_level=7, masteries={"1": 3})

    async def scenario():
        with pytest.raises(AppError) as caught:
            await service.patch_operator("char_x", {"skill_level": 6})
        assert caught.value.details["field"] == "masteries"
        assert caught.value.details["reason"] == "专精要求技能等级为 7"

        # 先把专精清零，再降技能等级：合法。
        await service.patch_operator("char_x", {"masteries": {"1": 0, "2": 0, "3": 0}})
        assert (await service.patch_operator("char_x", {"skill_level": 6}))[
            "skill_level"
        ] == 6

    asyncio.run(scenario())


def test_mastery_slots_beyond_the_rarity_skill_count_are_zeroed(box):
    service, store, _factory = box
    _seed(
        store,
        "char_x",
        rarity=4,
        elite=2,
        skill_level=7,
        masteries={},
        modules={},
    )

    async def scenario():
        detail = await service.patch_operator(
            "char_x", {"masteries": {"1": 3, "2": 2, "3": 3}}
        )
        assert detail["masteries"] == {"1": 3, "2": 2, "3": 0}

    asyncio.run(scenario())


def test_mastery_value_and_key_ranges_are_enforced(box):
    service, store, _factory = box
    _seed(store, "char_x", elite=2, skill_level=7, masteries={})

    async def scenario():
        with pytest.raises(AppError) as caught:
            await service.patch_operator("char_x", {"masteries": {"1": 4}})
        assert caught.value.details["allowed"] == {"minimum": 0, "maximum": 3}
        with pytest.raises(AppError) as caught:
            await service.patch_operator("char_x", {"masteries": {"4": 1}})
        assert caught.value.details["field"] == "masteries"

    asyncio.run(scenario())


def test_modules_require_elite_two_and_fixed_types(box):
    service, store, _factory = box
    _seed(store, "char_x", elite=1, modules={})

    async def scenario():
        with pytest.raises(AppError) as caught:
            await service.patch_operator("char_x", {"modules": {"X": 1}})
        assert caught.value.details["reason"] == "模组只在精二时可填"

        await service.patch_operator("char_x", {"elite": 2})
        detail = await service.patch_operator(
            "char_x", {"modules": {"X": 1, "Y": 2, "D": 3, "A": 1}}
        )
        assert detail["modules"] == {"X": 1, "Y": 2, "D": 3, "A": 1}

        with pytest.raises(AppError) as caught:
            await service.patch_operator("char_x", {"modules": {"Z": 1}})
        assert caught.value.details["allowed"] == {"types": ["X", "Y", "D", "A"]}
        with pytest.raises(AppError) as caught:
            await service.patch_operator("char_x", {"modules": {"X": 4}})
        assert caught.value.details["allowed"] == {"minimum": 1, "maximum": 3}
        with pytest.raises(AppError) as caught:
            await service.patch_operator("char_x", {"modules": {"X": 0}})
        assert caught.value.details["field"] == "modules.X"

    asyncio.run(scenario())


def test_setting_elite_below_two_clears_masteries_and_modules(box):
    service, store, _factory = box
    _seed(store, "char_x", elite=2, masteries={"1": 3}, modules={"X": 3})

    async def scenario():
        detail = await service.patch_operator("char_x", {"elite": 1})
        assert detail["masteries"] == {"1": 0, "2": 0, "3": 0}
        assert detail["modules"] == {}
        assert detail["field_sources"]["masteries"] == "manual"
        assert detail["field_sources"]["modules"] == "manual"

    asyncio.run(scenario())


def test_patch_marks_field_sources_and_rejects_unknown_fields(box):
    service, store, _factory = box
    _seed(store, "char_x", field_sources={}, level=10, potential=0)

    async def scenario():
        detail = await service.patch_operator(
            "char_x", {"level": 42, "potential": 2}, source="agent"
        )
        assert detail["field_sources"] == {"level": "agent", "potential": "agent"}
        assert detail["elite"] == 2
        assert detail["skill_level"] == 7

        # 原样回传未改动的字段：不标来源、不刷新可见状态。
        unchanged = await service.patch_operator("char_x", {"level": 42})
        assert unchanged["field_sources"] == {"level": "agent", "potential": "agent"}

        with pytest.raises(AppError) as caught:
            await service.patch_operator("char_x", {"rarity": 6})
        assert caught.value.details["allowed"]["fields"] == ["rarity"]

        with pytest.raises(AppError) as caught:
            await service.patch_operator("char_x", {"level": 91})
        assert caught.value.details["field"] == "level"

        with pytest.raises(AppError) as caught:
            await service.patch_operator("char_x", {"level": True})
        assert caught.value.details["field"] == "level"

        with pytest.raises(AppError) as caught:
            await service.patch_operator("char_x", {"level": 1}, source="recognition")
        assert caught.value.details["field"] == "source"

    asyncio.run(scenario())


def test_patch_missing_operator_is_404(box):
    service, _store, _factory = box

    async def scenario():
        with pytest.raises(AppError) as caught:
            await service.get_operator("char_none")
        assert caught.value.code == ErrorCode.ACCOUNT_DATA_NOT_FOUND
        assert caught.value.http_status == 404
        assert caught.value.details == {"char_id": "char_none"}

        with pytest.raises(AppError) as caught:
            await service.patch_operator("char_none", {"level": 10})
        assert caught.value.code == ErrorCode.ACCOUNT_DATA_NOT_FOUND

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 列表、概要与差异
# ---------------------------------------------------------------------------


def test_list_operators_filters_and_paginates(box):
    service, store, _factory = box
    _seed(store, "char_a", name="阿米娅", rarity=5, profession="术师", elite=2)
    _seed(store, "char_b", name="陈", rarity=6, profession="近卫", elite=1)
    _seed(store, "char_c", name="能天使", rarity=6, profession="狙击", own=False, elite=0)

    async def scenario():
        page = await service.list_operators(limit=1, offset=0)
        assert page["total"] == 2
        assert page["limit"] == 1 and page["offset"] == 0
        assert len(page["items"]) == 1

        assert (await service.list_operators(rarity_min=6))["total"] == 1
        assert (await service.list_operators(profession="狙击", owned_only=False))["total"] == 1
        assert (await service.list_operators(elite_min=2))["total"] == 1
        assert (await service.list_operators(query="能天", owned_only=False))["total"] == 1
        assert (await service.list_operators(query="  "))["total"] == 2

        second = await service.list_operators(limit=1, offset=1)
        assert second["total"] == 2 and len(second["items"]) == 1
        assert second["items"][0]["char_id"] != page["items"][0]["char_id"]

        clamped = await service.list_operators(limit=0, offset=-5)
        assert clamped["limit"] == 1 and clamped["offset"] == 0

    asyncio.run(scenario())


def test_summary_counts_elite_two_and_pending_diffs(box):
    service, store, _factory = box
    _seed(store, "char_a", elite=2)
    _seed(store, "char_b", elite=1)
    _seed(store, "char_c", own=False, elite=0)

    async def scenario():
        summary = await service.summary()
        assert summary == {
            "total": 2,
            "elite_two": 1,
            "recognized_at": "2026-10-10T03:00:00Z",
            "stale": False,
            "stale_days": 14,
            "unrecognized": 1,
            "pending_diffs": 0,
        }

    asyncio.run(scenario())


def test_summary_uses_the_configured_stale_threshold(box):
    service, store, factory = box
    _seed(store, "char_a", recognized_at=_utc(days_ago=5))

    async def scenario():
        assert (await service.summary())["stale"] is False

        strict = OperatorBoxService(
            factory, settings_provider=lambda: _settings(stale_days=3)
        )
        assert (await strict.summary())["stale"] is True

    asyncio.run(scenario())


def _settings(*, stale_days: int) -> Settings:
    settings = Settings()
    settings.agent.box_stale_days = stale_days
    return settings


def test_summary_is_stale_when_nothing_was_ever_recognized(box):
    service, _store, _factory = box

    async def scenario():
        assert await service.summary() == {
            "total": 0,
            "elite_two": 0,
            "recognized_at": None,
            "stale": True,
            "stale_days": 14,
            "unrecognized": 0,
            "pending_diffs": 0,
        }

    asyncio.run(scenario())


def test_dismiss_diffs_marks_selected_or_all(box):
    service, store, _factory = box
    _seed(store, "char_a")
    _seed(store, "char_b")

    async def scenario():
        for char_id, manual, recognized in (
            ("char_a", 55, 90),
            ("char_b", 1, 2),
        ):
            store.diffs.append(
                _Diff(
                    id=store.next_diff_id,
                    char_id=char_id,
                    name=store.rows[char_id].name,
                    field="level",
                    manual_value=manual,
                    recognized_value=recognized,
                    created_at=NOW,
                )
            )
            store.next_diff_id += 1

        diffs = await service.list_diffs()
        assert diffs["total"] == 2
        first_id = diffs["items"][0]["id"]

        assert await service.dismiss_diffs([first_id]) == 1
        assert await service.dismiss_diffs([first_id]) == 0  # 幂等
        assert (await service.list_diffs())["total"] == 1

        everything = await service.list_diffs(include_dismissed=True)
        assert len(everything["items"]) == 2
        assert everything["total"] == 1  # total 始终是未处理数

        assert await service.dismiss_diffs(None) == 1
        assert (await service.list_diffs())["items"] == []

    asyncio.run(scenario())


def test_summary_counts_diffs_created_by_recognition(box):
    """概要里的 pending_diffs 与差异列表同源（页面提示条与列表不会不一致）。"""
    service, store, _factory = box
    _seed(
        store,
        "char_a",
        level=55,
        recognized_level=80,
        field_sources={"level": "manual"},
    )

    async def scenario():
        await service.apply_recognition(
            {"char_a": {"own": True, "elite": 2, "level": 90, "potential": 6}},
            now=NOW,
            recognized_at=NOW,
        )
        assert (await service.summary())["pending_diffs"] == 1
        assert (await service.list_diffs())["total"] == 1

    asyncio.run(scenario())

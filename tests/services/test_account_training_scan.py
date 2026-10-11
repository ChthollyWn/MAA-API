"""练度扫描服务的契约（M36 规格 §4.2–§4.6、§4.9）。

全部离线：流水线服务、内核客户端、账号服务、资源服务都是替身，详情页截图用 PIL 现画
（只画专精圆点，别的像素不参与读数）。节点定义与像素读数另有覆盖
（``test_account_scan_tasks.py`` / ``test_account_scan_image.py``），这里只管
「一步一条流水线」的驱动、读数合并、写库口径、取消与并发。

几个真机结论沉淀成了这里的用例：

* 内核会沿提交节点的 ``next`` 一路走完整条链，所以一条链一次流水线（§4.2），每个节点的
  出路都带 ``Done`` 兜底，读不出来的节点当场跳过、链尾干净收尾；
* 读数只从内核实时回调里来（识别结果不进 ``log_entry``），服务层订阅 ``CALLBACK``；
* 专精在 Python 里数亮点（读不到截图就按未知处理，不写库）；
* 同一个人只扫一次：翻屏没翻动时第二屏会又读到老面孔，重复点开会把「没找到的人」挤掉；
* 写库口径是「读到什么写什么」：``None`` 一律表示没读到，不拿 0 冒充。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from maa_api.services.account.scan import TrainingScanStatus
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.account import scan as scan_module
from maa_api.services.account import scan_image
from maa_api.services.account import scan_tasks as st
from maa_api.services.account.capture import OperatorMeta
from maa_api.services.account.scan import (
    CARDS_PER_SCREEN,
    MAX_LIMIT,
    SCREEN_MARGIN,
    TrainingScanMode,
    TrainingScanService,
    _module,
    _name_index,
    _name_token,
    _readings,
    _screen_cap,
    _skill_level,
    cell_of,
)

#: 干员目录替身：两套阿米娅专门用来验证「同名多形态不猜」。
CATALOG: dict[str, OperatorMeta] = {
    "char_123_bagpipe": OperatorMeta(name="风笛", rarity=6, profession="先锋"),
    "char_172_silverash": OperatorMeta(name="凛御银灰", rarity=6, profession="近卫"),
    "char_101_amiya": OperatorMeta(name="阿米娅", rarity=5, profession="术师"),
    "char_102_amiya": OperatorMeta(name="阿米娅", rarity=5, profession="术师"),
}

#: 一屏里目标的位置：上排第 4 列（风笛）、下排第 2 列（凛御银灰）。
BAGPIPE_CELL = ("top", 3)
SILVERASH_CELL = ("bottom", 1)


# ---------------------------------------------------------------------------
# 回调替身：内核把识别结果放在 ``details.details.result`` 里
# ---------------------------------------------------------------------------
def _callback(short: str, result: Any) -> dict[str, Any]:
    """一条「读到东西了」的回调（信封层级与内核一致）。"""
    return {
        "msg": 20003,
        "details": {
            "uuid": "u",
            "what": "Recognized",
            "details": {"task": f"{st.SCAN_TASK_PREFIX}@{short}", "result": result},
        },
    }


def _text_callback(
    short: str, text: str, *, score: float = 0.99, rect: Sequence[int] = (0, 0, 1, 1)
) -> dict[str, Any]:
    """OCR 节点的回调：``{"text", "rect", "score"}``。"""
    return _callback(short, {"text": text, "rect": list(rect), "score": score})


def _score_callback(short: str, score: float) -> dict[str, Any]:
    """模板节点的回调：``{"score", "box"}``。"""
    return _callback(short, {"score": score, "box": [0, 0, 1, 1]})


def _screen_callbacks(names: Mapping[tuple[str, int], str]) -> list[dict[str, Any]]:
    """一屏 16 个卡名节点的回调。

    真机上读不出来的格子也会回一条（只有 rect、没有文字），所以这里每格都给一条：
    这正是链式读法能一路走完 16 格的原因。
    """
    callbacks: list[dict[str, Any]] = []
    for row in st.ROW_KEYS:
        for column in range(st.GRID_COLUMNS):
            name = names.get((row, column))
            callbacks.append(
                _text_callback(
                    f"CardName{row.title()}Col{column}",
                    name or "",
                    rect=(column, 0, 5, 5),
                )
            )
    return callbacks


def _detail_start_callbacks(*, skill: str = "7", elite: int = 2) -> list[dict[str, Any]]:
    """详情链第一段（从 ``SkillLevel`` 起）：读到第一个匹配不上的节点就停。

    ``elite`` 是这次能匹配上的徽记：真机上精二干员也会先把 ``Elite2`` 打中，链停在
    ``Elite1`` 上（0.87 低于 0.9 阈值）。
    """
    callbacks = [_text_callback("SkillLevel", skill)]
    if elite == 2:
        callbacks.append(_score_callback("Elite2", 0.9993))
    return callbacks


def _module_callbacks(code: str = "CHG X", stage: str = "3", *, score: float = 0.91) -> list[dict[str, Any]]:
    """模组编号 + 等级两个节点的回调。"""
    return [
        _text_callback("ModuleCode", code, score=0.99),
        _text_callback("ModuleStage", stage, score=score),
    ]


# ---------------------------------------------------------------------------
# 替身
# ---------------------------------------------------------------------------
def _task_label(task: Any) -> str:
    """流水线里一个任务的标签：``Custom`` 取链首节点，其余（起手 ``StartUp``）取任务名。"""
    names = getattr(task, "task_names", None) or []
    if names:
        return str(names[0])
    return str(getattr(task, "name", "") or "")


class FakePipelineService:
    """流水线服务替身：按脚本给每一步发回调，可指定某几步的终态。

    :param plan: 第 n 次 ``submit`` 时这一步要发的回调（用完为空）。
    :param step_status: 第 n 步（从 1 起，**不计起手 ``StartUp``**）的终态，缺省 ``completed``。
    :param client: 内核客户端替身；给了就在提交时把这些回调 ``emit`` 出去（生产路径）。
    :param gate: 给了就卡在 ``wait_terminal`` 上（测并发与取消）。

    每轮第一步是起手的 ``StartUp``（M37 §4.3）：它不产读数、也不吃 :param:`plan`，
    这样各用例的脚本仍然按「进列表 → 读屏 → …」写。
    """

    def __init__(
        self,
        plan: Sequence[Sequence[dict[str, Any]]] = (),
        *,
        step_status: Mapping[int, str] | None = None,
        client: FakeCoreClient | None = None,
        gate: asyncio.Event | None = None,
    ) -> None:
        # 一条回调写成裸 dict 也当「这一步只发这一条」（踩过：list(dict) 变成键的列表）
        self.plan = [
            [batch] if isinstance(batch, Mapping) else list(batch) for batch in plan
        ]
        self.step_status = dict(step_status or {})
        self.client = client
        self.gate = gate
        self.submitted: list[list[str]] = []
        self.titles: list[str] = []
        self.status_of: dict[str, str] = {}
        self.callbacks: dict[str, list[Any]] = {}
        self.cancelled: list[str] = []
        self.cancel_marks_current = True
        self.node_steps = 0  # 非起手的步数（``step_status`` 按它编号）
        self.task_payloads: list[list[dict[str, Any]]] = []  # 原样记下每步下发的任务参数

    async def submit(self, payload: Any, *, source: Any = None, agent_session_id: Any = None) -> Any:
        step = len(self.submitted) + 1
        labels = [_task_label(task) for task in payload.tasks]
        self.submitted.append(labels)
        self.task_payloads.append([task.model_dump(exclude_none=True) for task in payload.tasks])
        self.titles.append(payload.title)
        pipeline_id = f"step-{step}"
        if "StartUp" in labels:
            # 起手 StartUp 不产读数（只有生命周期消息），也不占脚本里的「第 n 步」。
            self.callbacks[pipeline_id] = []
            self.status_of[pipeline_id] = "completed"
        else:
            self.node_steps += 1
            self.callbacks[pipeline_id] = self.plan.pop(0) if self.plan else []
            self.status_of[pipeline_id] = self.step_status.get(self.node_steps, "completed")
        if self.client is not None:  # 真机读数只从实时回调里来，替身也走这条路
            for batch in self.callbacks[pipeline_id]:
                self.client.emit(batch)
        return SimpleNamespace(id=pipeline_id)

    async def wait_terminal(self, pipeline_id: str, timeout_seconds: float) -> tuple[Any, bool]:
        if self.gate is not None:
            await self.gate.wait()
        status = self.status_of.get(pipeline_id, "completed")
        return SimpleNamespace(id=pipeline_id, status=status), True

    async def callbacks_for(self, pipeline_id: str, *, session: Any = None) -> list[Any]:
        return list(self.callbacks.get(pipeline_id, []))

    async def request_cancel(self, pipeline_id: str) -> None:
        self.cancelled.append(pipeline_id)
        if self.cancel_marks_current:
            self.status_of[pipeline_id] = "cancelled"

    def nodes(self) -> list[str]:
        """提交过的节点（按提交顺序拍平）。"""
        return [node for batch in self.submitted for node in batch]

    def short_nodes(self) -> list[str]:
        return [st.node_key(node) for node in self.nodes()]


class FakeBox:
    """账号服务替身：记下写库调用，可按 ``results`` 让某一行回 ``None``（不在表里）。"""

    def __init__(
        self,
        *,
        operators: Sequence[Mapping[str, Any]] = (),
        results: Mapping[str, Any] | None = None,
        record_error: Exception | None = None,
        async_list: bool = False,
    ) -> None:
        self.operators = [dict(item) for item in operators]
        self.results = dict(results or {})
        self.record_error = record_error
        # 生产里的 ``OperatorBoxService.list_operators`` 是 async 的：漏 await 会拿到协程
        # 对象、名单静默变空（M37 §2.1）。默认给同步替身，需要复现时打开这个开关。
        self.async_list = async_list
        self.applied: list[dict[str, Any]] = []
        self.records: list[dict[str, Any]] = []
        self.list_kwargs: dict[str, Any] = {}

    async def _page_later(self, **kwargs: Any) -> Mapping[str, Any]:
        return self._page_now(**kwargs)

    def list_operators(self, **kwargs: Any) -> Any:
        """生产里的 ``OperatorBoxService.list_operators`` 是 async 的：``async_list`` 打开时
        返回协程对象，用来复现「漏 await → 名单静默变空」（M37 §2.1）。"""
        if self.async_list:
            return self._page_later(**kwargs)
        return self._page_now(**kwargs)

    def _page_now(self, **kwargs: Any) -> Mapping[str, Any]:
        self.list_kwargs = kwargs
        offset = int(kwargs.get("offset") or 0)
        limit = kwargs.get("limit")
        items = self.operators[offset:]
        if limit is not None:
            items = items[: int(limit)]
        return {"items": items, "total": len(self.operators)}

    async def apply_detail_scan(self, char_id: str, **fields: Any) -> dict[str, Any] | None:
        self.applied.append({"char_id": char_id, **fields})
        if char_id in self.results:
            outcome = self.results[char_id]
            return None if outcome is None else dict(outcome)
        written = [
            name
            for name in ("skill_level", "masteries", "modules")
            if fields.get(name) is not None
        ]
        return {"char_id": char_id, "updated": written, "skipped": []}

    async def record_training_scan(self, **kwargs: Any) -> None:
        if self.record_error is not None:
            raise self.record_error
        self.records.append(kwargs)


class FakePlans:
    """养成计划服务替身（``list_plans`` 是异步的）。"""

    def __init__(self, plans: Sequence[Mapping[str, Any]] = ()) -> None:
        self.plans = [dict(plan) for plan in plans]

    async def list_plans(self) -> Mapping[str, Any]:
        return {"plans": list(self.plans), "total": len(self.plans)}


class FakeCoreClient:
    """内核客户端替身：取一帧截图，外加一条 ``CALLBACK`` 事件流。

    真机读数只从实时回调里来（识别结果不进 ``log_entry``），所以替身也照这条路走：
    ``FakePipelineService`` 提交一步时 ``emit`` 这一步的回调。

    :param frames: 全量扫描（M39）要「前后两帧比像素」才能确认左滑换没换人，所以这里
        可以给一串截图，按取图次数轮着返回（每次都不一样＝画面在动）；只给 ``path``
        时每次都是同一张，用来复现「左滑划不动」。
    """

    def __init__(
        self,
        path: Path | None = None,
        *,
        error: AppError | None = None,
        frames: Sequence[Path] | None = None,
    ) -> None:
        self.path = path
        self.error = error
        self.frames = list(frames or [])
        self.calls = 0
        self.handlers: dict[str, list[Any]] = {}
        self.emitted: list[Any] = []

    def on(self, event: str, handler: Any) -> None:
        self.handlers.setdefault(event, []).append(handler)

    def emit(self, payload: Any) -> None:
        self.emitted.append(payload)
        for handler in list(self.handlers.get("CALLBACK", [])):
            handler(payload)

    def get_image(self, save_to: str | None = None) -> Mapping[str, Any]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        if self.frames:
            return {"path": str(self.frames[(self.calls - 1) % len(self.frames)])}
        if self.path is None:
            return {}
        return {"path": str(self.path)}


class FakeResourceService:
    """资源服务替身：``resource_root`` + 计数用的 ``reload_resources``。"""

    def __init__(self, root: Path) -> None:
        self.resource_root = root
        self.reloads = 0

    def reload_resources(self) -> None:
        self.reloads += 1


class FakeResourceServiceWithoutReload:
    """只有 ``resource_root`` 的资源服务（内核重载回调没装配的那种）。"""

    def __init__(self, root: Path) -> None:
        self.resource_root = root


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _detail_image(path: Path, masteries: Mapping[int, int], *, width: int = st.KERNEL_WIDTH) -> Path:
    """画一张详情页截图：给定的专精圆点画亮（255），其余画暗（60）。"""
    image = Image.new("L", (width, 720), 0)
    pixels = image.load()
    assert pixels is not None
    scale = width / st.KERNEL_WIDTH
    for slot, level in masteries.items():
        for index, position in enumerate(st.MASTER_PIP_POSITIONS):
            box = scan_image.scale_box(st.MASTER_PIP_ROIS[(slot, position)], scale)
            value = 255 if index < level else 60
            for x in range(box[0], box[2]):
                for y in range(box[1], box[3]):
                    pixels[x, y] = value
    image.convert("RGB").save(path)
    return path


def _service(
    *,
    pipeline: FakePipelineService | None = None,
    box: FakeBox | None = None,
    plans: FakePlans | None = None,
    core: FakeCoreClient | None = None,
    resource: Any = None,
    catalog: Mapping[str, OperatorMeta] | None = None,
    client_type: Any = None,
) -> TrainingScanService:
    """装配一个全替身的服务（``step_timeout`` 给短一点，卡住能快点报出来）。

    流水线与内核客户端替身对接：这一步的回调经 ``CALLBACK`` 事件流进服务，跟真机一样。
    """
    core_client = core if core is not None else FakeCoreClient()
    pipeline_service = pipeline if pipeline is not None else FakePipelineService()
    if pipeline_service.client is None:
        pipeline_service.client = core_client
    return TrainingScanService(
        None,
        box=box if box is not None else FakeBox(),
        pipeline_service=pipeline_service,
        plans=plans if plans is not None else FakePlans(),
        core_client=core_client,
        catalog=lambda: dict(catalog if catalog is not None else CATALOG),
        resource_service=resource,
        step_timeout_seconds=5.0,
        client_type_provider=client_type,
    )


async def _wait_status(service: TrainingScanService, *statuses: str, timeout: float = 5.0) -> str:
    """等这一轮离开 running（替身都是立即返回，所以只是让出几次事件循环）。"""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        current = service.snapshot()["status"]
        if current in statuses:
            return current
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"扫描没在预期时间内结束，当前状态 {current!r}")
        await asyncio.sleep(0.01)


async def _wait_for(predicate: Any, *, what: str = "条件", timeout: float = 5.0) -> None:
    """等某个副作用出现（取消之后驱动协程的收尾是异步的）。"""
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"{what}没在预期时间内出现")
        await asyncio.sleep(0.01)


# ---------------------------------------------------------------------------
# 读数解析
# ---------------------------------------------------------------------------
def test_readings_merge_the_three_envelope_levels():
    callbacks = [
        _text_callback("SkillLevel", "7", score=0.999351),
        _score_callback("Elite2", 0.999294),
        "MaaApi@OperatorScan@CardNameTopCol3",  # 纯字符串回调：只认任务名
        {"msg": "TaskChainError"},  # 没有任务名的回调：忽略
    ]

    readings = _readings(callbacks)

    assert set(readings) == {"SkillLevel", "Elite2", "CardNameTopCol3"}
    assert readings["SkillLevel"]["text"] == "7"
    assert readings["SkillLevel"]["score"] == pytest.approx(0.999351)
    assert readings["SkillLevel"]["rect"] == [0, 0, 1, 1]
    assert readings["Elite2"]["score"] == pytest.approx(0.999294)
    assert readings["Elite2"]["box"] == [0, 0, 1, 1]
    assert readings["CardNameTopCol3"] == {}


def test_readings_take_the_latest_and_keep_all_candidates():
    callbacks = [
        _callback(
            "SkillLevel",
            [{"text": "1", "score": 0.4}, {"text": "7", "score": 0.99}],
        ),
        _text_callback("SkillLevel", "7", score=0.9993),
    ]

    readings = _readings(callbacks)

    assert readings["SkillLevel"]["text"] == "7"
    assert readings["SkillLevel"]["score"] == pytest.approx(0.9993)


def test_skill_level_only_accepts_one_to_seven():
    def reading(text: str) -> dict[str, Any]:
        return _readings([_text_callback("SkillLevel", text)])

    assert _skill_level(reading("7")) == 7
    assert _skill_level(reading("1")) == 1
    assert _skill_level(reading("0")) is None
    assert _skill_level(reading("8")) is None
    assert _skill_level(reading("")) is None
    assert _skill_level({}) is None


def test_module_needs_both_code_and_a_confident_stage():
    def reading(code: str, stage: str, *, score: float) -> dict[str, Any]:
        return _readings(
            [_text_callback("ModuleCode", code, score=0.99), _text_callback("ModuleStage", stage, score=score)]
        )

    assert _module(reading("CHG X", "3", score=0.9106)) == {"X": 3}
    assert _module(reading("MER Y", "1", score=0.91)) == {"Y": 1}
    # 没装模组：面板上是灰字「模组」，OCR 会读成垃圾（真机 score 0.39）
    assert _module(reading("", "8", score=0.387)) is None
    # 有编号但等级读花了：宁可不写
    assert _module(reading("CHG X", "8", score=0.91)) is None
    assert _module(reading("CHG X", "3", score=0.38)) is None
    assert _module({}) is None


def test_screen_cap_grows_with_the_list():
    cells = st.GRID_COLUMNS * len(st.ROW_KEYS)
    assert _screen_cap(0) == 3 + SCREEN_MARGIN
    assert _screen_cap(cells) == 3 + SCREEN_MARGIN
    assert _screen_cap(cells + 1) == 6 + SCREEN_MARGIN


def test_name_token_strips_form_suffixes_and_spaces():
    assert _name_token(" 风笛 ") == "风笛"
    assert _name_token("凛御银灰·新") == "凛御银灰"
    assert _name_token("阿米娅（复制人）") == "阿米娅"
    assert _name_token("丰川 祥子") == "丰川祥子"


def test_name_index_drops_ambiguous_names():
    index, ambiguous = _name_index(CATALOG)

    assert index["风笛"] == "char_123_bagpipe"
    assert "阿米娅" in ambiguous
    assert "阿米娅" not in index


# ---------------------------------------------------------------------------
# 名单
# ---------------------------------------------------------------------------
def test_resolve_targets_merges_box_and_active_plans():
    box = FakeBox(operators=[{"char_id": "char_123_bagpipe", "name": "风笛"}])
    plans = FakePlans(
        [
            {"status": "active", "targets": [{"operator": "凛御银灰"}]},
            {"status": "done", "targets": [{"operator": "陈"}]},  # 不在进行中：不收
        ]
    )
    service = _service(box=box, plans=plans)

    async def scenario() -> None:
        resolved = await service.resolve_targets(limit=10)

        assert resolved["total"] == 2
        assert resolved["targets"] == [
            {"char_id": "char_123_bagpipe", "name": "风笛"},
            {"char_id": "char_172_silverash", "name": "凛御银灰"},
        ]
        assert resolved["deferred"] == []
        assert resolved["unresolved"] == []
        # 名单来源按上限取一整页（deferred 要看清后面还有谁），再由服务层截到 limit
        assert box.list_kwargs == {
            "owned_only": True,
            "elite_min": 2,
            "limit": MAX_LIMIT,
            "offset": 0,
        }

    asyncio.run(scenario())


def test_resolve_targets_reports_unresolved_names():
    plans = FakePlans(
        [
            {
                "status": "active",
                "targets": [{"operator": "阿米娅"}, {"operator": "查无此人"}, {"operator": "风笛"}],
            }
        ]
    )
    box = FakeBox()
    service = _service(box=box, plans=plans)

    async def scenario() -> None:
        resolved = await service.resolve_targets()

        assert resolved["targets"] == [{"char_id": "char_123_bagpipe", "name": "风笛"}]
        assert resolved["unresolved"] == ["阿米娅", "查无此人"]

    asyncio.run(scenario())


def test_resolve_targets_defers_beyond_the_limit():
    box = FakeBox(
        operators=[
            {"char_id": "char_1_a", "name": "甲"},
            {"char_id": "char_2_b", "name": "乙"},
            {"char_id": "char_3_c", "name": "丙"},
        ]
    )
    service = _service(box=box, catalog={"char_1_a": OperatorMeta(name="甲")})

    async def scenario() -> None:
        resolved = await service.resolve_targets(limit=1)

        assert resolved["total"] == 1
        assert resolved["targets"] == [{"char_id": "char_1_a", "name": "甲"}]
        assert resolved["deferred"] == ["乙", "丙"]

    asyncio.run(scenario())


def test_resolve_targets_can_skip_plan_targets():
    plans = FakePlans([{"status": "active", "targets": [{"operator": "凛御银灰"}]}])
    box = FakeBox(operators=[{"char_id": "char_123_bagpipe", "name": "风笛"}])
    service = _service(box=box, plans=plans)

    async def scenario() -> None:
        resolved = await service.resolve_targets(include_plan_targets=False)

        assert [entry["name"] for entry in resolved["targets"]] == ["风笛"]

    asyncio.run(scenario())


def test_resolve_targets_uses_the_given_char_ids():
    box = FakeBox(operators=[{"char_id": "char_123_bagpipe", "name": "风笛"}])
    service = _service(box=box)

    async def scenario() -> None:
        resolved = await service.resolve_targets(char_ids=["char_172_silverash", "", "char_999_unknown"])

        assert resolved["targets"] == [{"char_id": "char_172_silverash", "name": "凛御银灰"}]
        # 目录里没有的 id：连名字都拿不到，如实记进 unresolved
        assert resolved["unresolved"] == ["char_999_unknown"]
        assert box.list_kwargs == {}  # 指定了 char_ids 就不去读账号表

    asyncio.run(scenario())


@pytest.mark.parametrize("limit", [0, -1, MAX_LIMIT + 1])
def test_resolve_targets_validates_the_limit(limit: int):
    service = _service()

    async def scenario() -> None:
        with pytest.raises(AppError) as caught:
            await service.resolve_targets(limit=limit)

        assert caught.value.code is ErrorCode.VALIDATION_ERROR

    asyncio.run(scenario())


def test_submit_rejects_an_empty_list():
    service = _service()

    async def scenario() -> None:
        with pytest.raises(AppError) as caught:
            await service.submit()

        assert caught.value.code is ErrorCode.VALIDATION_ERROR
        assert service.is_running() is False
        assert service.snapshot()["status"] == TrainingScanStatus.IDLE.value

    asyncio.run(scenario())


def test_submit_needs_the_resource_service():
    box = FakeBox(operators=[{"char_id": "char_123_bagpipe", "name": "风笛"}])
    service = _service(box=box)

    async def scenario() -> None:
        with pytest.raises(AppError) as caught:
            await service.submit(limit=10)

        assert caught.value.code is ErrorCode.SERVICE_UNAVAILABLE
        assert service.is_running() is False

    asyncio.run(scenario())


def test_submit_needs_the_plan_service_when_plan_targets_are_wanted():
    box = FakeBox(operators=[{"char_id": "char_123_bagpipe", "name": "风笛"}])
    service = TrainingScanService(
        None,
        box=box,
        pipeline_service=FakePipelineService(),
        plans=None,
        catalog=lambda: dict(CATALOG),
        resource_service=FakeResourceService(Path("/tmp")),
    )

    async def scenario() -> None:
        # 没有计划服务时，默认（要计划目标）应当明确报「没装配好」，而不是静默漏掉来源二
        with pytest.raises(AppError) as caught:
            await service.resolve_targets()

        assert caught.value.code is ErrorCode.SERVICE_UNAVAILABLE

        resolved = await service.resolve_targets(include_plan_targets=False)
        assert [entry["name"] for entry in resolved["targets"]] == ["风笛"]

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 一整轮
# ---------------------------------------------------------------------------
def _detail_batch(
    *,
    skill: str | None = "7",
    elite: int | None = 2,
    modules: bool = True,
    stage_score: float = 0.91,
    name_en: str | None = None,
) -> list[dict[str, Any]]:
    """读一张详情页要发的回调（整条链一次流水线，读不出来的节点没有回调）。

    真机上精二干员会把 ``Elite2`` 打中（0.9993），``Elite1`` 的 0.87 低于 0.9 阈值、
    读不出来；模组那两个节点只有装了模组的干员才有。``name_en`` 是左下角那行拉丁名
    （M40 身份核对用；读不出来时不给回调）。
    """
    callbacks: list[dict[str, Any]] = []
    if skill is not None:
        callbacks.append(_text_callback("SkillLevel", skill))
    if elite is not None:
        callbacks.append(_score_callback(f"Elite{elite}", 0.9993))
    if modules:
        callbacks.extend(_module_callbacks(score=stage_score))
    if name_en:
        callbacks.append(_text_callback("OperNameEn", name_en, rect=(0, 452, 524, 32)))
    return callbacks


def _happy_plan(masteries: Mapping[int, int] = {1: 3, 2: 0, 3: 3}) -> list[list[dict[str, Any]]]:
    """「1 名干员、上排第 4 列、精二、技能 7、专精 3/0/3、模组 CHG X 等级 3」的脚本。

    步骤与内核行为一一对应：进列表 → 一次读完整屏卡名 → 点开并一次读完详情页 → 回列表
    （``masteries`` 只影响截图那份像素，脚本里用不上，留着当说明书）。
    """
    return [
        [],  # EnterBox
        _screen_callbacks({BAGPIPE_CELL: "风笛"}),  # 读一屏卡名（16 个节点一次流水线）
        # M38：点开与读详情并成一条链，一次提交收齐 SkillLevel + Elite2 + 模组两节点
        _detail_batch(),
        [],  # Back
    ]


def test_a_full_round_reads_writes_and_records(tmp_path: Path):
    image = _detail_image(tmp_path / "detail.png", {1: 3, 2: 0, 3: 3})
    pipeline = FakePipelineService(_happy_plan())
    box = FakeBox(operators=[{"char_id": "char_123_bagpipe", "name": "风笛"}])
    resource = FakeResourceService(tmp_path / "layer")
    core = FakeCoreClient(image)
    service = _service(pipeline=pipeline, box=box, core=core, resource=resource)

    async def scenario() -> None:
        accepted = await service.submit(limit=10)

        # POST 形状（规格 §4.6）：这一步的 pipeline_id + 名单
        assert accepted["total"] == 1
        assert accepted["targets"] == [{"char_id": "char_123_bagpipe", "name": "风笛"}]
        assert accepted["deferred"] == []
        assert accepted["unresolved"] == []
        assert isinstance(accepted["pipeline_id"], str) and accepted["pipeline_id"]

        await _wait_status(service, TrainingScanStatus.SUCCEEDED.value)
        state = service.snapshot()

        assert state["scanned"] == 1
        assert state["updated"] == 1
        assert state["skipped"] == 0
        assert state["unresolved"] == 0
        assert state["mismatched"] == 0
        assert state["total"] == 1
        assert state["started_at"] is not None and state["finished_at"] is not None
        assert "写入 1 名" in (state["message"] or "")

        # 一步一步的流水线：起手启动 → 进列表 → 读屏 → 点开并读详情 → 回列表
        # （M38 把「点开」与「读详情」并成一条链，所以少了单独那一步 ``SkillLevel``）
        assert pipeline.short_nodes() == [
            "StartUp",
            "EnterBox",
            "CardNameTopCol0",
            "ClickTopCol3",
            "Back",
        ]
        assert pipeline.titles[0] == "练度扫描：启动游戏"
        assert pipeline.titles[1] == "练度扫描：进干员列表"
        assert pipeline.titles[3] == "练度扫描：读 风笛 的详情页"

        # 写库：读到什么写什么（专精走截图，模组走节点读数）
        assert box.applied == [
            {
                "char_id": "char_123_bagpipe",
                "skill_level": 7,
                "masteries": {1: 3, 2: 0, 3: 3},
                "modules": {"X": 3},
            }
        ]
        # 一轮只写一条识别记录，指向这一轮的第一条流水线
        assert len(box.records) == 1
        assert box.records[0]["updated"] == 1
        assert box.records[0]["pipeline_id"] == "step-1"

    asyncio.run(scenario())


def test_readings_come_from_the_live_callback_stream(tmp_path: Path):
    """识别结果不进日志：读数必须靠内核实时回调，``callbacks_for`` 回读拿不到（docs/13 §8.51）。

    真机上 ``log_entry`` 只留下「开始/完成/全部完成」三条生命周期消息，识别回调一条都不落库；
    所以这里把回读掐掉，读数还是得齐。
    """
    image = _detail_image(tmp_path / "detail.png", {1: 3, 2: 0, 3: 3})
    core = FakeCoreClient(image)
    pipeline = FakePipelineService(_happy_plan(), client=core)
    box = FakeBox(operators=[{"char_id": "char_123_bagpipe", "name": "风笛"}])

    async def no_readback(pipeline_id: str, *, session: Any = None) -> list[Any]:
        return []

    pipeline.callbacks_for = no_readback  # type: ignore[method-assign]
    resource = FakeResourceService(tmp_path / "layer")
    service = _service(pipeline=pipeline, box=box, core=core, resource=resource)

    async def scenario() -> None:
        await service.submit(limit=10)
        await _wait_status(service, TrainingScanStatus.SUCCEEDED.value)
        state = service.snapshot()

        assert core.emitted, "读数没有从内核回调里来"
        assert state["scanned"] == 1
        assert state["updated"] == 1
        assert box.applied == [
            {
                "char_id": "char_123_bagpipe",
                "skill_level": 7,
                "masteries": {1: 3, 2: 0, 3: 3},
                "modules": {"X": 3},
            }
        ]

    asyncio.run(scenario())


def test_screenshot_scale_is_respected(tmp_path: Path):
    """1440p 截图（宽 1920）也要读出同样的专精等级。"""
    image = _detail_image(tmp_path / "detail.png", {1: 0, 2: 3, 3: 1}, width=1920)
    pipeline = FakePipelineService(_happy_plan())
    box = FakeBox(operators=[{"char_id": "char_123_bagpipe", "name": "风笛"}])
    service = _service(
        pipeline=pipeline,
        box=box,
        core=FakeCoreClient(image),
        resource=FakeResourceService(tmp_path / "layer"),
    )

    async def scenario() -> None:
        await service.submit(limit=10)
        await _wait_status(service, TrainingScanStatus.SUCCEEDED.value)

        assert box.applied[0]["masteries"] == {1: 0, 2: 3, 3: 1}

    asyncio.run(scenario())


def test_missing_screenshot_leaves_masteries_unknown(tmp_path: Path):
    pipeline = FakePipelineService(_happy_plan())
    box = FakeBox(operators=[{"char_id": "char_123_bagpipe", "name": "风笛"}])
    core = FakeCoreClient(None)
    service = _service(
        pipeline=pipeline,
        box=box,
        core=core,
        resource=FakeResourceService(tmp_path / "layer"),
    )

    async def scenario() -> None:
        await service.submit(limit=10)
        await _wait_status(service, TrainingScanStatus.SUCCEEDED.value)

        # 没取到截图：专精按「没读到」处理（不写），其它两列照写
        assert core.calls == 1
        assert box.applied[0]["masteries"] is None
        assert box.applied[0]["skill_level"] == 7
        assert box.applied[0]["modules"] == {"X": 3}
        assert service.snapshot()["updated"] == 1

    asyncio.run(scenario())


def test_masteries_and_modules_are_held_back_below_elite_two(tmp_path: Path):
    image = _detail_image(tmp_path / "detail.png", {1: 3, 2: 3, 3: 3})
    plan = [
        [],
        _screen_callbacks({BAGPIPE_CELL: "风笛"}),
        # 精一：Elite2 读不出来（没有回调）、Elite1 匹配上（0.95）。M38 的精英化短路让真机上
        # 后面两个模组节点根本不会跑，这里仍旧喂进来，压的是服务层的「未到精二不提交」护栏。
        [
            _text_callback("SkillLevel", "7"),
            _score_callback("Elite1", 0.95),
            *_module_callbacks(),
        ],
        [],  # 回列表
    ]
    pipeline = FakePipelineService(plan)
    box = FakeBox(operators=[{"char_id": "char_123_bagpipe", "name": "风笛"}])
    service = _service(
        pipeline=pipeline,
        box=box,
        core=FakeCoreClient(image),
        resource=FakeResourceService(tmp_path / "layer"),
    )

    async def scenario() -> None:
        await service.submit(limit=10)
        await _wait_status(service, TrainingScanStatus.SUCCEEDED.value)

        # 没到精二：专精与模组不提交（提交了也会被 box 拒），技能等级照写
        assert box.applied == [
            {
                "char_id": "char_123_bagpipe",
                "skill_level": 7,
                "masteries": None,
                "modules": None,
            }
        ]
        assert service.snapshot()["updated"] == 1

    asyncio.run(scenario())


def test_a_mismatched_operator_is_not_written(tmp_path: Path):
    plan = _happy_plan()
    pipeline = FakePipelineService(plan)
    box = FakeBox(
        operators=[{"char_id": "char_123_bagpipe", "name": "风笛"}],
        results={"char_123_bagpipe": None},  # 账号表里没有这一行
    )
    service = _service(
        pipeline=pipeline,
        box=box,
        core=FakeCoreClient(_detail_image(tmp_path / "detail.png", {1: 3, 2: 0, 3: 3})),
        resource=FakeResourceService(tmp_path / "layer"),
    )

    async def scenario() -> None:
        await service.submit(limit=10)
        await _wait_status(service, TrainingScanStatus.SUCCEEDED.value)
        state = service.snapshot()

        assert state["scanned"] == 1
        assert state["updated"] == 0
        assert state["mismatched"] == 1

    asyncio.run(scenario())


def test_one_broken_cell_does_not_stop_the_round(tmp_path: Path):
    plan = [
        [],  # EnterBox
        _screen_callbacks({("top", 3): "风笛", ("top", 5): "凛御银灰"}),
        [],  # 点开风笛并读详情：这一步内核报 failed
        [],  # 回列表（这一格的收尾照跑）
        [*_detail_start_callbacks(), *_module_callbacks()],  # 点开凛御银灰并读详情（一条链）
        [],  # 回列表
    ]
    # 步号是「非起手的第几步」：1 进列表、2 读屏、3 点开并读详情……M38 合并之后这条是第 3 步。
    pipeline = FakePipelineService(plan, step_status={3: "failed"})
    box = FakeBox(
        operators=[
            {"char_id": "char_123_bagpipe", "name": "风笛"},
            {"char_id": "char_172_silverash", "name": "凛御银灰"},
        ]
    )
    service = _service(
        pipeline=pipeline,
        box=box,
        core=FakeCoreClient(_detail_image(tmp_path / "detail.png", {1: 3, 2: 0, 3: 3})),
        resource=FakeResourceService(tmp_path / "layer"),
    )

    async def scenario() -> None:
        await service.submit(limit=10)
        await _wait_status(service, TrainingScanStatus.SUCCEEDED.value)
        state = service.snapshot()

        assert state["scanned"] == 2
        assert state["updated"] == 1
        assert state["skipped"] == 1
        assert [entry["char_id"] for entry in box.applied] == ["char_172_silverash"]
        # 第一格失败之后仍然回了列表（不然第二格会点错地方）
        assert pipeline.short_nodes().count("Back") == 2

    asyncio.run(scenario())


def test_a_cell_without_a_reading_does_not_hide_the_later_cells(tmp_path: Path):
    """一格卡名读不出来（没有回调）不会挡住它后面的格子（靠 ``Done`` 兜底，见 §4.1）。"""
    batch = [
        _text_callback("CardNameTopCol0", "甲", rect=(0, 0, 5, 5)),
        _text_callback("CardNameTopCol3", "风笛", rect=(3, 0, 5, 5)),
    ]  # 真机实测：读不出来的那几格压根没有回调
    plan = [
        [],  # EnterBox
        batch,  # 读一屏卡名（一次流水线，链上读不出来的节点被跳过）
        _detail_batch(),  # 点开风笛并读这一页（M38：一条链）
        [],  # Back
    ]
    pipeline = FakePipelineService(plan)
    box = FakeBox(operators=[{"char_id": "char_123_bagpipe", "name": "风笛"}])
    service = _service(
        pipeline=pipeline,
        box=box,
        core=FakeCoreClient(_detail_image(tmp_path / "detail.png", {1: 3, 2: 0, 3: 3})),
        resource=FakeResourceService(tmp_path / "layer"),
    )

    async def scenario() -> None:
        await service.submit(limit=10)
        await _wait_status(service, TrainingScanStatus.SUCCEEDED.value)

        assert pipeline.short_nodes()[2:4] == ["CardNameTopCol0", "ClickTopCol3"]
        assert service.snapshot()["scanned"] == 1

    asyncio.run(scenario())


def test_a_target_on_the_next_screen_is_found_after_a_swipe(tmp_path: Path):
    plan = [
        [],  # EnterBox
        _screen_callbacks({("top", 7): "泥岩"}),  # 第一屏有别的干员、没有目标
        [],  # SwipeNextPage
        _screen_callbacks({BAGPIPE_CELL: "风笛"}),  # 第二屏有
        [*_detail_start_callbacks(), *_module_callbacks()],  # 点开并读详情（一条链）
        [],  # 回列表
    ]
    pipeline = FakePipelineService(plan)
    box = FakeBox(operators=[{"char_id": "char_123_bagpipe", "name": "风笛"}])
    service = _service(
        pipeline=pipeline,
        box=box,
        core=FakeCoreClient(_detail_image(tmp_path / "detail.png", {1: 0, 2: 0, 3: 0})),
        resource=FakeResourceService(tmp_path / "layer"),
    )

    async def scenario() -> None:
        await service.submit(limit=10)
        await _wait_status(service, TrainingScanStatus.SUCCEEDED.value)
        state = service.snapshot()

        assert service._state["screens"] == 2  # noqa: SLF001 - 翻屏次数是内部进度
        assert state["scanned"] == 1
        assert "SwipeNextPage" in pipeline.short_nodes()

    asyncio.run(scenario())


def test_a_screen_that_did_not_move_does_not_scan_anyone_twice(tmp_path: Path):
    """翻屏没翻动时第二屏又读到老面孔：只算一次，别把「已经推进到第几名」算多。

    真机实测（M36 W7）：列表是横向翻页的，竖着划一格都不动；修复前第二屏把第一屏的 6 名
    又扫了一遍（``scanned`` 记 11），而 ``position += matched`` 把没找到的那位挤出名单，
    既不在 ``skipped`` 也不在 ``deferred`` 里——静默丢掉。
    """
    plan = [
        [],  # EnterBox
        _screen_callbacks({BAGPIPE_CELL: "风笛"}),  # 第一屏：风笛
        _detail_start_callbacks(),  # 点开风笛并读详情（一条链）
        [],  # 回列表
        [],  # SwipeNextPage（这一步真机上是「没翻动」）
        _screen_callbacks({BAGPIPE_CELL: "风笛", SILVERASH_CELL: "凛御银灰"}),  # 第二屏：老面孔 + 新人
        _detail_start_callbacks(),  # 点开凛御银灰并读详情（一条链）
        [],  # 回列表
    ]
    pipeline = FakePipelineService(plan)
    box = FakeBox(
        operators=[
            {"char_id": "char_123_bagpipe", "name": "风笛"},
            {"char_id": "char_172_silverash", "name": "凛御银灰"},
        ]
    )
    service = _service(
        pipeline=pipeline,
        box=box,
        core=FakeCoreClient(_detail_image(tmp_path / "detail.png", {1: 0, 2: 0, 3: 0})),
        resource=FakeResourceService(tmp_path / "layer"),
    )

    async def scenario() -> None:
        await service.submit(limit=10)
        await _wait_status(service, TrainingScanStatus.SUCCEEDED.value)
        state = service.snapshot()
        nodes = pipeline.short_nodes()

        assert state["scanned"] == 2
        assert nodes.count("ClickTopCol3") == 1  # 风笛只点开一次
        assert nodes.count("ClickBottomCol1") == 1  # 凛御银灰点开一次
        assert [item["char_id"] for item in box.applied] == [
            "char_123_bagpipe",
            "char_172_silverash",
        ]
        assert state["skipped"] == 0

    asyncio.run(scenario())


def test_operators_left_over_are_reported_as_skipped(tmp_path: Path):
    """名单里有 2 名、屏幕上只找得到 1 名时，另一名进 ``skipped``（不是静默丢掉）。"""
    plan = [
        [],
        _screen_callbacks({BAGPIPE_CELL: "风笛"}),
        [*_detail_start_callbacks(), *_module_callbacks()],  # 点开并读详情（一条链）
        [],  # 回列表
    ]
    # 之后一直找不到第二名：每屏都读一遍，直到屏数上限
    for _ in range(12):
        plan.extend([_screen_callbacks({}), []])
    pipeline = FakePipelineService(plan)
    box = FakeBox(
        operators=[
            {"char_id": "char_123_bagpipe", "name": "风笛"},
            {"char_id": "char_172_silverash", "name": "凛御银灰"},
        ]
    )
    service = _service(
        pipeline=pipeline,
        box=box,
        core=FakeCoreClient(_detail_image(tmp_path / "detail.png", {1: 3, 2: 0, 3: 3})),
        resource=FakeResourceService(tmp_path / "layer"),
    )

    async def scenario() -> None:
        await service.submit(limit=10)
        await _wait_status(service, TrainingScanStatus.SUCCEEDED.value)
        state = service.snapshot()

        assert state["scanned"] == 1
        assert state["skipped"] == 1
        assert service._state["screens"] == _screen_cap(2)  # noqa: SLF001

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 并发与取消
# ---------------------------------------------------------------------------
def test_a_second_submit_is_rejected_while_running(tmp_path: Path):
    gate = asyncio.Event()
    pipeline = FakePipelineService([[], _screen_callbacks({("top", 7): "泥岩"})], gate=gate)
    box = FakeBox(operators=[{"char_id": "char_123_bagpipe", "name": "风笛"}])
    service = _service(pipeline=pipeline, box=box, resource=FakeResourceService(tmp_path / "layer"))

    async def scenario() -> None:
        await service.submit(limit=10)

        with pytest.raises(AppError) as caught:
            await service.submit(limit=10)
        assert caught.value.code is ErrorCode.PIPELINE_ALREADY_RUNNING

        # 放行之后这一轮会自己走完（名单里的那位这次没读出来，按跳过收尾）
        gate.set()
        await _wait_status(service, TrainingScanStatus.SUCCEEDED.value)
        assert service.snapshot()["skipped"] == 1

    asyncio.run(scenario())


def test_cancel_stops_the_round_and_keeps_what_was_read(tmp_path: Path):
    gate = asyncio.Event()
    pipeline = FakePipelineService(_happy_plan(), gate=gate)
    box = FakeBox(operators=[{"char_id": "char_123_bagpipe", "name": "风笛"}])
    service = _service(pipeline=pipeline, box=box, resource=FakeResourceService(tmp_path / "layer"))

    async def scenario() -> None:
        await service.submit(limit=10)
        state = await service.cancel()

        assert state["status"] == TrainingScanStatus.CANCELLED.value
        assert pipeline.cancelled == ["step-1"]

        gate.set()
        # 这一步的终态是 cancelled：驱动会在这里停下，收尾（写识别记录）随后完成
        await _wait_for(lambda: box.records, what="这一轮的识别记录")

        # 取消之后不再往下读：提交过的只有起手那一步（``submit`` 等它进队列就返回）
        assert pipeline.submitted == [["StartUp"]]
        # 这一轮的识别记录照写（已写入的练度保留）
        assert len(box.records) == 1
        assert box.records[0]["pipeline_id"] == "step-1"

    asyncio.run(scenario())


def test_cancel_on_an_idle_service_is_a_no_op():
    service = _service()

    async def scenario() -> None:
        state = await service.cancel()

        assert state["status"] == TrainingScanStatus.IDLE.value

    asyncio.run(scenario())


def test_the_record_failure_does_not_break_the_round(tmp_path: Path):
    pipeline = FakePipelineService(_happy_plan())
    box = FakeBox(
        operators=[{"char_id": "char_123_bagpipe", "name": "风笛"}],
        record_error=RuntimeError("库写不动"),
    )
    service = _service(
        pipeline=pipeline,
        box=box,
        core=FakeCoreClient(_detail_image(tmp_path / "detail.png", {1: 3, 2: 0, 3: 3})),
        resource=FakeResourceService(tmp_path / "layer"),
    )

    async def scenario() -> None:
        await service.submit(limit=10)
        await _wait_status(service, TrainingScanStatus.SUCCEEDED.value)
        state = service.snapshot()

        assert state["status"] == TrainingScanStatus.SUCCEEDED.value
        assert state["updated"] == 1

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 节点落地与状态形状
# ---------------------------------------------------------------------------
def test_ensure_tasks_writes_the_layer_once(tmp_path: Path):
    resource = FakeResourceService(tmp_path / "layer")
    service = _service(resource=resource)

    async def scenario() -> None:
        first = await service._ensure_tasks()

        assert first["written"] is True
        assert first["reloaded"] is True
        assert first["cached"] is False
        assert resource.reloads == 1
        path = Path(first["path"])
        assert path == st.scan_task_path(tmp_path / "layer")
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert set(payload) == set(st.operator_scan_tasks())
        assert st.ENTER_BOX in payload

        second = await service._ensure_tasks()

        assert second["cached"] is True
        assert second["written"] is False
        assert resource.reloads == 1  # 进程内确认过一次就不再重载内核资源

        # 文件被清理掉之后会重新写一遍
        path.unlink()
        third = await service._ensure_tasks()

        assert third["cached"] is False
        assert third["written"] is True
        assert resource.reloads == 2

    asyncio.run(scenario())


def test_ensure_tasks_survives_a_missing_reload_hook(tmp_path: Path):
    resource = FakeResourceServiceWithoutReload(tmp_path / "layer")
    service = _service(resource=resource)

    async def scenario() -> None:
        outcome = await service._ensure_tasks()

        assert outcome["written"] is True
        assert outcome["reloaded"] is False
        assert Path(outcome["path"]).is_file()

    asyncio.run(scenario())


def test_every_chain_node_can_fall_through_to_the_rest_of_the_chain():
    """链上每个节点的出路必须是「**整条尾巴** + Done」（真机实测的必需写法）。

    候选只放紧邻的下一个节点时，精二干员在 ``Elite2`` 命中后试 ``Elite1`` 不中就直接落到
    ``Done``——后面的 ``ModuleCode`` / ``ModuleStage`` 一个都没跑（模组读数空缺）。

    M38 加了精英化短路（:data:`st.ELITE_SHORTCUTS`）：命中一个徽记之后不再试另外两个，
    所以尾巴按短路表算。
    """
    payload = st.operator_scan_tasks()
    detail = list(st.detail_screen_nodes())

    for names in (list(st.box_screen_nodes()), detail):
        for index, name in enumerate(names):
            node = payload[name]
            override = st.ELITE_SHORTCUTS.get(name)
            tail = list(names[index + 1 :]) if override is None else list(override)
            assert node["next"] == [*tail, st.DONE]
            assert node["exceededNext"] == node["next"]
            assert node["maxTimes"] == 1
        assert payload[names[-1]]["next"] == [st.DONE]


def test_the_elite_shortcut_skips_the_other_badges():
    """命中一个精英化徽记之后不再试另外两个：精二直奔模组，精一/精零直接收尾（M38）.

    M40 起短路之后还要读一次详情页左下角的拉丁名（核对这一页是谁），所以尾巴是它。
    """
    payload = st.operator_scan_tasks()
    names = st.DETAIL_NODE_NAMES

    assert payload[names["Elite2"]]["next"] == [
        names["ModuleCode"],
        names["ModuleStage"],
        names["OperNameEn"],
        st.DONE,
    ]
    # 模组要求精二：低精英化的这一页没有模组可读，白试两次识别。
    assert payload[names["Elite1"]]["next"] == [names["OperNameEn"], st.DONE]
    assert payload[names["Elite0"]]["next"] == [names["OperNameEn"], st.DONE]


def test_a_click_node_reads_the_detail_page_in_the_same_chain():
    """点开与读详情在同一条链里：省掉一次提交、排队与终态往返（M38，真机每次约 0.8 秒）."""
    payload = st.operator_scan_tasks()
    tail = [*st.detail_screen_nodes(), st.DONE]

    for row in st.ROW_KEYS:
        for column in range(st.GRID_COLUMNS):
            node = payload[st.click_cell_node(row, column)]
            assert node["next"] == tail
            assert node["exceededNext"] == tail
            # 详情页推出来的等待时间就在这一段里，不能再压。
            assert node["postDelay"] == st.CLICK_POST_DELAY


def test_snapshot_shape_matches_the_spec():
    service = _service()
    state = service.snapshot()

    assert set(state) == {
        "status",
        "mode",
        "pipeline_id",
        "started_at",
        "finished_at",
        "scanned",
        "updated",
        "screens",
        "skipped",
        "unresolved",
        "mismatched",
        "realigned",
        "unverified",
        "message",
        "total",
        "targets",
        "deferred",
    }
    assert state["status"] == TrainingScanStatus.IDLE.value
    assert state["mode"] == TrainingScanMode.SINGLE.value
    assert isinstance(state["scanned"], int)
    assert isinstance(state["screens"], int)
    assert state["realigned"] == 0
    assert state["unverified"] == 0


# ---------------------------------------------------------------------------
# M37：可用性修复（名单来源、名字参数、起手启动、空屏护栏）
# ---------------------------------------------------------------------------
def test_an_async_operator_source_is_awaited(tmp_path: Path):
    """默认名单来源是 async 的：漏 await 会拿到协程、被当空名单（M37 §2.1）。

    生产实测：``OperatorBoxService.list_operators`` 是 ``async def``，漏 await 之后 103 名精二
    干员一个都进不了名单，接口回「精二名单是空的」——而单测一直注入同步替身，五轮真机验收
    又都显式传了 ``char_ids``，这条分支从来没被跑到。
    """
    box = FakeBox(operators=[{"char_id": "char_123_bagpipe", "name": "风笛"}], async_list=True)
    service = _service(box=box, resource=FakeResourceService(tmp_path / "layer"))

    async def scenario() -> None:
        resolved = await service.resolve_targets(limit=10)

        assert resolved["targets"] == [{"char_id": "char_123_bagpipe", "name": "风笛"}]
        assert resolved["total"] == 1
        assert box.list_kwargs == {
            "owned_only": True,
            "elite_min": 2,
            "limit": 200,
            "offset": 0,
        }

    asyncio.run(scenario())


def test_char_ids_accept_a_name(tmp_path: Path):
    """``char_ids`` 允许直接给名字（Agent 工具的描述就是这么写的，M37 §2.2）。"""
    service = _service(resource=FakeResourceService(tmp_path / "layer"))

    async def scenario() -> None:
        resolved = await service.resolve_targets(
            limit=10,
            char_ids=["风笛", "char_172_silverash", "不存在的人"],
            include_plan_targets=False,
        )

        assert resolved["targets"] == [
            {"char_id": "char_123_bagpipe", "name": "风笛"},
            {"char_id": "char_172_silverash", "name": "凛御银灰"},
        ]
        assert resolved["unresolved"] == ["不存在的人"]

    asyncio.run(scenario())


def test_the_round_starts_the_client_first(tmp_path: Path):
    """起手先跑 ``StartUp``（带上默认渠道），再进干员列表（M37 §4.3）。"""
    pipeline = FakePipelineService(_happy_plan())
    box = FakeBox(operators=[{"char_id": "char_123_bagpipe", "name": "风笛"}])
    service = _service(
        pipeline=pipeline,
        box=box,
        core=FakeCoreClient(_detail_image(tmp_path / "detail.png", {1: 3, 2: 0, 3: 3})),
        resource=FakeResourceService(tmp_path / "layer"),
        client_type=lambda: "Bilibili",
    )

    async def scenario() -> None:
        await service.submit(limit=10)
        await _wait_status(service, TrainingScanStatus.SUCCEEDED.value)

        assert pipeline.submitted[0] == ["StartUp"]
        assert pipeline.task_payloads[0] == [
            {
                "enable": True,
                "name": "StartUp",
                "start_game_enabled": True,
                "client_type": "Bilibili",
            }
        ]
        assert pipeline.titles[0] == "练度扫描：启动游戏"
        assert pipeline.submitted[1] == [st.ENTER_BOX]

    asyncio.run(scenario())


def test_the_round_without_a_default_channel_lets_the_core_decide(tmp_path: Path):
    """取不到默认渠道时不下发 ``client_type``，由内核用设置里的渠道（M37 §4.3）。"""
    pipeline = FakePipelineService(_happy_plan())
    box = FakeBox(operators=[{"char_id": "char_123_bagpipe", "name": "风笛"}])
    service = _service(
        pipeline=pipeline,
        box=box,
        core=FakeCoreClient(_detail_image(tmp_path / "detail.png", {1: 3, 2: 0, 3: 3})),
        resource=FakeResourceService(tmp_path / "layer"),
    )

    async def scenario() -> None:
        await service.submit(limit=10)
        await _wait_status(service, TrainingScanStatus.SUCCEEDED.value)

        assert pipeline.task_payloads[0] == [
            {"enable": True, "name": "StartUp", "start_game_enabled": True}
        ]

    asyncio.run(scenario())


def test_the_first_screen_must_show_the_operator_list(tmp_path: Path):
    """第一屏一个卡名都读不出来 → 立刻失败并说清原因（M37 §4.4）。

    修复前这一轮会安静地读完 6 屏再回「全部跳过」，用户界面上看到的是「无操作」。
    """
    pipeline = FakePipelineService([[], _screen_callbacks({})])
    box = FakeBox(operators=[{"char_id": "char_123_bagpipe", "name": "风笛"}])
    service = _service(
        pipeline=pipeline,
        box=box,
        core=FakeCoreClient(),
        resource=FakeResourceService(tmp_path / "layer"),
    )

    async def scenario() -> None:
        await service.submit(limit=10)
        await _wait_status(service, TrainingScanStatus.FAILED.value)
        state = service.snapshot()

        assert "一个卡名都没读出来" in (state["message"] or "")
        assert state["scanned"] == 0
        # 快速失败：只读到第一屏就判定画面不对，不再翻屏找。
        assert pipeline.short_nodes() == ["StartUp", "EnterBox", "CardNameTopCol0"]
        assert "SwipeNextPage" not in pipeline.short_nodes()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# M39：全量扫描（规格 §4.2–§4.5）
# ---------------------------------------------------------------------------
def _full_screen_plan(
    names: Mapping[tuple[str, int], str],
    *,
    screen_name: str = "screen",
) -> list[list[dict[str, Any]]]:
    """一屏全量扫描的脚本：读卡名 → 16 张详情页 → 回列表。

    每张详情页是两条流水线（M39 真机：左滑与读详情务必分开走）；左滑那一步不产读数，
    所以一批空回调。
    """
    plan = [
        _screen_callbacks(names),  # 读这一屏卡名
        _detail_batch(),  # 点开这一格（cell_of(0)）读第一张
    ]
    for _ in range(CARDS_PER_SCREEN - 1):
        plan.append([])  # 详情页左滑（不产读数）
        plan.append(_detail_batch())  # 读这一张
    plan.append([])  # 回列表（Back）
    plan.append([])  # 翻到下一屏（SwipeNextPage，同样不产读数）
    return plan


def _frames(tmp_path: Path, count: int = 16) -> list[Path]:
    """一串「换人了」的详情页截图：专精圆点照画，另外每帧抹一条随帧移动的亮带。

    只画圆点的话前后两帧的平均灰度差不到 1（几个小圆点占不了几个像素），
    ``scan_image.frame_changed`` 会判成「没动」——真机换人时整张立绘都变了，
    这条亮带就是那件事的最小替身。
    """
    frames: list[Path] = []
    for index in range(count):
        path = _detail_image(tmp_path / f"frame{index}.png", {1: 3, 2: 0, 3: 3})
        with Image.open(path) as image:
            pixels = image.load()
            assert pixels is not None
            # 亮带画在**左半边**：专精圆点在最右边（§4.6 的 ROI），别把读数盖歪了。
            top = (index % 8) * 90
            for x in range(0, 600):
                for y in range(top, top + 90):
                    pixels[x, y] = (240, 240, 240)
            image.save(path)
        frames.append(path)
    return frames


def test_full_round_walks_the_whole_screen_and_stops_at_the_end(tmp_path: Path):
    """全量扫描：一屏 16 张详情页按「车厢顺序」扫完，翻页后没有新人就收尾（§4.2）。"""
    frames = _frames(tmp_path)
    pipeline = FakePipelineService(
        [
            [],  # EnterBox
            *_full_screen_plan({BAGPIPE_CELL: "风笛", SILVERASH_CELL: "凛御银灰"}),
            _screen_callbacks({BAGPIPE_CELL: "风笛", SILVERASH_CELL: "凛御银灰"}),  # 第 2 屏：老面孔
        ]
    )
    box = FakeBox()
    service = _service(
        pipeline=pipeline,
        box=box,
        core=FakeCoreClient(frames=frames),
        resource=FakeResourceService(tmp_path / "layer"),
    )

    async def scenario() -> None:
        accepted = await service.submit(mode="full")

        # POST 形状：全量扫描没有名单，`total` 是 `null`（§3）
        assert accepted["mode"] == "full"
        assert accepted["total"] is None
        assert accepted["targets"] == []
        assert accepted["deferred"] == []
        assert accepted["unresolved"] == []

        await _wait_status(service, TrainingScanStatus.SUCCEEDED.value)
        state = service.snapshot()

        # 点了 16 张详情页，但只有两格读出了卡名 → 只写两个人
        assert state["scanned"] == 16
        assert state["updated"] == 2
        assert state["screens"] == 2  # 第二屏整屏都是老面孔 ⇒ 收尾
        assert state["mode"] == "full"
        assert state["total"] is None
        assert "全量扫描" in (state["message"] or "")

        # 身份＝卡名 × 车厢顺序：cell_of(3) 是下排第 2 列、cell_of(6) 是上排第 4 列
        # （偶数格在上排、奇数格在下排：详情页左滑第 0 张＝上排第 1 格、第 1 张＝下排第 1 格）
        assert cell_of(0) == ("top", 0)
        assert cell_of(3) == SILVERASH_CELL
        assert cell_of(6) == BAGPIPE_CELL
        assert [item["char_id"] for item in box.applied] == [
            "char_172_silverash",
            "char_123_bagpipe",
        ]
        for item in box.applied:
            assert item["skill_level"] == 7
            assert item["masteries"] == {1: 3, 2: 0, 3: 3}
            assert item["modules"] == {"X": 3}

        # 一步步的流水线：读名 → 点开第一格 → 15 组「左滑」「读详情」→ 回列表 → 翻页 → 再读名
        nodes = pipeline.short_nodes()
        assert nodes[0:3] == ["StartUp", "EnterBox", "CardNameTopCol0"]
        assert nodes[3] == "ClickTopCol0"
        assert nodes[4:34] == ["SwipeNextDetail", "SkillLevel"] * 15
        assert nodes[34:36] == ["Back", "SwipeNextPage"]
        assert nodes[36] == "CardNameTopCol0"
        assert pipeline.short_nodes().count("SwipeNextPage") == 1
        assert pipeline.titles[3] == "练度扫描：读第 1 张详情页"
        assert pipeline.titles[4] == "练度扫描：详情页左滑"
        assert pipeline.titles[5] == "练度扫描：读第 2 张详情页"
        # 一轮只写一条识别记录
        assert len(box.records) == 1

    asyncio.run(scenario())


def test_full_round_stops_when_the_page_is_not_the_operator_list(tmp_path: Path):
    """整屏卡名跟干员目录一个都对不上 → 画面已经跑歪，立刻失败收尾（§4.2）。

    真机教训（M39 台账）：屏内只要有一次点击没点开详情页、或者返回多退了一层，画面就会
    漂到主界面；此后每屏读到的都是主界面碎片（`Lv`、`=xx`、`-----`…），而「新面孔」判据
    永远成立，一轮能白转几十屏（实测 26 屏、零写库）。这条护栏就是那次跑飞的产物。
    """
    frames = _frames(tmp_path)
    pipeline = FakePipelineService(
        [
            [],
            *_full_screen_plan({BAGPIPE_CELL: "风笛"}),
            # 第 2 屏：主界面碎片，目录里一个都没有
            _screen_callbacks({("top", 0): "Lv", ("top", 4): "=xx", ("bottom", 0): "-----"}),
        ]
    )
    box = FakeBox()
    service = _service(
        pipeline=pipeline,
        box=box,
        core=FakeCoreClient(frames=frames),
        resource=FakeResourceService(tmp_path / "layer"),
    )

    async def scenario() -> None:
        await service.submit(mode="full")
        await _wait_status(service, TrainingScanStatus.FAILED.value)
        state = service.snapshot()

        assert state["screens"] == 2  # 读到第 2 屏就认出来画面不对，不再往下翻
        assert "对不上" in (state["message"] or "")
        assert pipeline.short_nodes().count("SwipeNextPage") == 1
        # 第 1 屏的读数照旧写库，第 2 屏的碎片一个都不写
        assert [item["char_id"] for item in box.applied] == ["char_123_bagpipe"]
        assert state["unresolved"] == 0

    asyncio.run(scenario())


def test_full_round_skips_a_screen_that_never_opens_a_detail_page(tmp_path: Path):
    """第一格点不开详情页 → 这一屏跳过，而且**不按返回键**（按了就退出干员列表了）。

    真机教训（M39 台账）：点空一次之后，后面 15 次左滑会把**列表**当详情页滑，最后那次
    返回直接退回主界面，整轮从第 7 屏起空转到第 33 屏。所以这里点开前后比像素、再看详情
    读数有没有东西，两样都说「没点开」就重试一次，再不行就跳过这一屏继续翻页。
    """
    still = _detail_image(tmp_path / "still.png", {1: 3, 2: 0, 3: 3})
    pipeline = FakePipelineService(
        [
            [],
            _screen_callbacks({("top", 0): "风笛"}),
            [],  # 点开第一格：详情链一条读数都没有（点空了）
            [],  # 重试一次，还是没点开
            [],  # 翻到下一屏（跳过的那一屏不按返回，所以这里没有 Back）
            _screen_callbacks({("top", 0): "风笛"}),  # 第 2 屏：老面孔 ⇒ 收尾
        ]
    )
    box = FakeBox()
    service = _service(
        pipeline=pipeline,
        box=box,
        core=FakeCoreClient(path=still),
        resource=FakeResourceService(tmp_path / "layer"),
    )

    async def scenario() -> None:
        await service.submit(mode="full")
        await _wait_status(service, TrainingScanStatus.SUCCEEDED.value)
        state = service.snapshot()

        assert state["screens"] == 2
        assert state["scanned"] == 0  # 这一屏一张都没读成
        assert box.applied == []
        nodes = pipeline.short_nodes()
        assert nodes.count("ClickTopCol0") == 2  # 点了两次（第一次 + 重试）
        assert nodes.count("SwipeNextDetail") == 0  # 没点开就别往下滑
        assert "Back" not in nodes  # 跳过这一屏时不能按返回键
        assert "SwipeNextPage" in nodes  # 照常翻下一页

    asyncio.run(scenario())


def test_full_round_stops_when_the_carousel_does_not_move(tmp_path: Path):
    """左滑划不动＝这一屏到底了：重试几次还是没动就收尾，别把读数记到错的人头上（§4.2）。"""
    image = _detail_image(tmp_path / "still.png", {1: 3, 2: 0, 3: 3})
    pipeline = FakePipelineService(
        [
            [],
            _screen_callbacks({("top", 0): "风笛"}),
            _detail_batch(),  # 点开第一格
            *([[], _detail_batch()] * 3),  # 三次「左滑 + 读详情」都划不动
            [],  # 回列表（Back）
        ]
    )
    box = FakeBox()
    service = _service(
        pipeline=pipeline,
        box=box,
        core=FakeCoreClient(image),  # 每次取图都是同一张 ⇒ 画面没动
        resource=FakeResourceService(tmp_path / "layer"),
    )

    async def scenario() -> None:
        await service.submit(mode="full")
        await _wait_status(service, TrainingScanStatus.SUCCEEDED.value)
        state = service.snapshot()

        # 第 1 张（上排第 1 格＝风笛）照常写；第 2 张开始左滑划不动，重试 2 次后收尾
        assert state["scanned"] == 1
        assert state["updated"] == 1
        assert [item["char_id"] for item in box.applied] == ["char_123_bagpipe"]
        assert state["screens"] == 1
        nodes = pipeline.short_nodes()
        assert nodes[3] == "ClickTopCol0"
        # 每张卡片都是「左滑一条流水线 + 读详情一条流水线」（M39 真机：并成一条链时
        # 链尾那一帧永远比不出变化）；划不动时照常重试两次，一共 3 组
        assert nodes[4:10] == ["SwipeNextDetail", "SkillLevel"] * 3
        # 划不动也要把设备带回列表（不然设备停在详情页）
        assert nodes[10] == "Back"
        assert "SwipeNextPage" not in nodes

    asyncio.run(scenario())


def test_full_round_counts_unreadable_and_unknown_cards(tmp_path: Path):
    """卡名读不出来、目录里查不到、同名多形态：都不写库，记进各自的计数（§4.5）。"""
    frames = _frames(tmp_path)
    pipeline = FakePipelineService(
        [
            [],
            *_full_screen_plan(
                {
                    ("top", 0): "查无此人",  # 不在干员目录里
                    ("top", 1): "阿米娅",  # 目录里有两个形态 → 不猜
                    ("top", 2): "风笛",  # 正常
                }
            ),
            _screen_callbacks({("top", 0): "查无此人"}),
        ]
    )
    box = FakeBox()
    service = _service(
        pipeline=pipeline,
        box=box,
        core=FakeCoreClient(frames=frames),
        resource=FakeResourceService(tmp_path / "layer"),
    )

    async def scenario() -> None:
        await service.submit(mode="full")
        await _wait_status(service, TrainingScanStatus.SUCCEEDED.value)
        state = service.snapshot()

        assert state["scanned"] == 16
        assert state["updated"] == 1
        assert state["unresolved"] == 2  # 「查无此人」+「阿米娅」
        assert [item["char_id"] for item in box.applied] == ["char_123_bagpipe"]

    asyncio.run(scenario())


def test_full_round_stops_at_the_screen_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """屏数上限是跑飞保护：到了就收尾，不再翻（§4.2）。"""
    monkeypatch.setattr(scan_module, "FULL_SCAN_MAX_SCREENS", 1)
    frames = _frames(tmp_path)
    pipeline = FakePipelineService([[], *_full_screen_plan({BAGPIPE_CELL: "风笛"})])
    service = _service(
        pipeline=pipeline,
        box=FakeBox(),
        core=FakeCoreClient(frames=frames),
        resource=FakeResourceService(tmp_path / "layer"),
    )

    async def scenario() -> None:
        await service.submit(mode="full")
        await _wait_status(service, TrainingScanStatus.SUCCEEDED.value)
        state = service.snapshot()

        assert state["screens"] == 1
        assert state["scanned"] == 16
        assert "SwipeNextPage" not in pipeline.short_nodes()

    asyncio.run(scenario())


def test_full_mode_does_not_need_a_roster(tmp_path: Path):
    """全量扫描不组名单：账号表空着也照样受理（点名扫会回 422，§4.3）。"""
    frames = _frames(tmp_path)
    pipeline = FakePipelineService([[], _screen_callbacks({}), _screen_callbacks({})])
    box = FakeBox()
    service = _service(
        pipeline=pipeline,
        box=box,
        plans=FakePlans(),
        core=FakeCoreClient(frames=frames),
        resource=FakeResourceService(tmp_path / "layer"),
    )

    async def scenario() -> None:
        accepted = await service.submit(mode="full")

        assert accepted["total"] is None
        assert accepted["targets"] == []
        assert service.snapshot()["mode"] == "full"

        # 第一屏一个卡名都没读出来 → 与点名扫一样的快速失败
        await _wait_status(service, TrainingScanStatus.FAILED.value)
        assert "一个卡名都没读出来" in (service.snapshot()["message"] or "")

    asyncio.run(scenario())


def test_full_mode_rejects_an_unknown_mode():
    """`mode` 只认 `single`/`full`，别的值当场报错，不当成 single 静默跑。"""
    service = _service()

    async def scenario() -> None:
        with pytest.raises(ValueError):
            await service.submit(mode="all")

    asyncio.run(scenario())


def test_the_detail_swipe_node_starts_on_the_illustration():
    """详情页左滑的起手必须落在立绘上（M39 §2 的真机结论）。

    起手落在左侧属性面板、或落在右侧技能/模组面板上，这一划纹丝不动（五组变体平均灰度差
    0.00–0.05）；只有从立绘中间往左划才换人（``(650,400) → (250,400)``，mean 24–48）。
    起手点还要避开屏幕最右侧的**职业筛选栏**（内核 x ≥ 1100 那一条）——M39 探针把它拖开过
    一次，列表被过滤、栅格整体左移，卡名只能读到碎片。
    """
    payload = st.operator_scan_tasks()
    node = payload[st.SWIPE_NEXT_DETAIL]

    assert node["algorithm"] == "JustReturn"
    assert node["action"] == "Swipe"
    assert node["specificRect"] == list(st.DETAIL_SWIPE_FROM)
    assert node["rectMove"] == list(st.DETAIL_SWIPE_TO)
    assert node["specialParams"] == list(st.SWIPE_PARAMS)
    assert node["postDelay"] == st.DETAIL_SWIPE_POST_DELAY
    assert node["maxTimes"] == 1
    assert node["next"] == [st.DONE]
    assert node["exceededNext"] == [st.DONE]

    # 起手与落点都在立绘区（内核 1280 基准）：左边不碰属性面板、右边不碰技能/筛选栏
    start_x, start_y = st.DETAIL_SWIPE_FROM[0], st.DETAIL_SWIPE_FROM[1]
    end_x = st.DETAIL_SWIPE_TO[0]
    assert 200 < start_x < 900 and end_x < start_x
    assert 200 < start_y < 600


# ---------------------------------------------------------------------------
# M40：拉丁名核对身份（不回列表也能知道这一页是谁）
#
# 2026-10-11 那轮全量扫描的真实缺陷：详情页左滑是**甩动**，一次可能前进两张，而「画面换了
# 没有」判不出「换了几张」——屏内自某张起整体错位，部分干员的练度写成了邻居的读数
# （``char_502_nblade`` 夜刀 2★ 却存着 3 级模组与 3/3 专精）。修法：详情页左下角那行
# **拉丁名**（内核 ASCII OCR 读得动，中文大名字是艺术字读不出来）当身份信号，跟这一屏
# 卡名清单逐张核对，对不上就回列表重新点开这一格。
# ---------------------------------------------------------------------------
#: 带拉丁名的目录替身：风笛在 ``cell_of(6)``、凛御银灰在 ``cell_of(3)``。
EN_CATALOG: dict[str, OperatorMeta] = {
    "char_123_bagpipe": OperatorMeta(name="风笛", rarity=6, profession="先锋", name_en="Bagpipe"),
    "char_172_silverash": OperatorMeta(
        name="凛御银灰", rarity=6, profession="近卫", name_en="SilverAsh the Reignfrost"
    ),
}


def test_name_en_matching_survives_a_maimed_reading_but_not_a_twin():
    """拉丁名比对：掉首字母/多一个字母还认得出，粘了杂字或两个都像就不下结论（M40）."""
    expected = {0: "Bagpipe", 1: "Ines", 2: "Shu"}

    assert scan_module._name_en_hit("Bagpipe", expected) == 0
    assert scan_module._name_en_hit("Bagipipe", expected) == 0  # 真机 Bagpipe→Bagipipe
    assert scan_module._name_en_hit("nes", expected) == 1  # 真机 Ines→nes（掉首字母）
    assert scan_module._name_en_hit("", expected) is None
    assert scan_module._name_en_hit("ne", expected) is None  # 太短，不猜
    assert scan_module._name_en_hit("ShuT7T X", expected) is None  # 粘了立绘杂字

    # 本体与异格同名（Bubble/Rubble、Irene/Arene）：两个都像时不说话，宁可当没读到
    twins = {0: "Bubble", 1: "Rubble"}
    assert scan_module._name_en_hit("ubble", twins) is None
    # 这一屏只有一个候选时可以认（真机上同一屏出现同名前缀的概率极低）
    assert scan_module._name_en_hit("ubble", {0: "Bubble"}) == 0


def test_expected_name_en_only_covers_cells_with_a_readable_card_name():
    """期望值只在这一格卡名读出来、且目录里有拉丁名时才建（M40）."""
    lookup, _ambiguous = _name_index(EN_CATALOG)
    names = {BAGPIPE_CELL: "风笛", SILVERASH_CELL: "凛御银灰", ("top", 1): "谁啊"}

    expected = TrainingScanService._expected_name_en(names, lookup, EN_CATALOG)

    assert expected == {6: "Bagpipe", 3: "SilverAsh the Reignfrost"}


def _full_screen_plan_with_names(
    names: Mapping[tuple[str, int], str], reported: Mapping[int, str]
) -> list[list[dict[str, Any]]]:
    """``_full_screen_plan`` 的 M40 版：第 ``i`` 张详情页报出 ``reported[i]`` 那行拉丁名。"""
    plan = [_screen_callbacks(names), _detail_batch(name_en=reported.get(0))]
    for index in range(1, CARDS_PER_SCREEN):
        plan.append([])  # 左滑（不产读数）
        plan.append(_detail_batch(name_en=reported.get(index)))
    plan.append([])  # 回列表
    plan.append([])  # 翻到下一屏
    return plan


def test_full_round_writes_when_the_latin_name_agrees(tmp_path: Path):
    """对得上就照常写库，两个计数器都不动（M40）."""
    frames = _frames(tmp_path, count=40)
    names = {BAGPIPE_CELL: "风笛", SILVERASH_CELL: "凛御银灰"}
    pipeline = FakePipelineService(
        [
            [],
            *_full_screen_plan_with_names(
                names, {3: "SilverAsh the Reignfrost", 6: "Bagpipe"}
            ),
            _screen_callbacks(names),
        ]
    )
    box = FakeBox()
    service = _service(
        pipeline=pipeline,
        box=box,
        core=FakeCoreClient(frames=frames),
        resource=FakeResourceService(tmp_path / "layer"),
        catalog=EN_CATALOG,
    )

    async def scenario() -> None:
        await service.submit(mode="full")
        await _wait_status(service, TrainingScanStatus.SUCCEEDED.value)
        state = service.snapshot()

        assert state["updated"] == 2
        assert state["realigned"] == 0
        assert state["unverified"] == 0
        assert [item["char_id"] for item in box.applied] == [
            "char_172_silverash",
            "char_123_bagpipe",
        ]

    asyncio.run(scenario())


def test_full_round_realigns_when_the_carousel_overshoots(tmp_path: Path):
    """画面里是别人 ⇒ 回列表重新点开这一格，用重锚那一帧的读数写库（M40）.

    真机缺陷：甩动一次滑两张 ⇒ 第 6 张位置上是第 3 格那位。修法是回列表点回第 6 格，
    拿重锚后的读数写库——写进去的技能等级来自重锚那一帧（4），不是错位那一帧的 7。
    """
    frames = _frames(tmp_path, count=40)
    names = {BAGPIPE_CELL: "风笛", SILVERASH_CELL: "凛御银灰"}
    plan = _full_screen_plan_with_names(
        names, {3: "SilverAsh the Reignfrost", 6: "SilverAsh the Reignfrost"}
    )
    # 第 6 张错位（画面里是凛御银灰）⇒ 回列表 + 点回这一格 + 重读（这次报风笛、技能 4）
    plan.insert(2 + (6 - 1) * 2 + 2, [])  # Back
    plan.insert(2 + (6 - 1) * 2 + 3, _detail_batch(skill="4", name_en="Bagpipe"))
    pipeline = FakePipelineService([[], *plan, _screen_callbacks(names)])
    box = FakeBox()
    service = _service(
        pipeline=pipeline,
        box=box,
        core=FakeCoreClient(frames=frames),
        resource=FakeResourceService(tmp_path / "layer"),
        catalog=EN_CATALOG,
    )

    async def scenario() -> None:
        await service.submit(mode="full")
        await _wait_status(service, TrainingScanStatus.SUCCEEDED.value)
        state = service.snapshot()

        assert state["realigned"] == 1
        assert state["unverified"] == 0
        assert [item["char_id"] for item in box.applied] == [
            "char_172_silverash",
            "char_123_bagpipe",
        ]
        written = box.applied[-1]
        assert written["skill_level"] == 4  # 重锚那一帧的读数
        nodes = pipeline.short_nodes()
        assert "Back" in nodes
        assert "ClickTopCol3" in nodes

    asyncio.run(scenario())


def test_full_round_skips_a_card_that_still_does_not_match_after_realigning(tmp_path: Path):
    """重锚之后还是对不上 ⇒ 这一张的读数不写库，进 ``skipped``，不打断整屏（M40）."""
    frames = _frames(tmp_path, count=40)
    names = {BAGPIPE_CELL: "风笛", SILVERASH_CELL: "凛御银灰"}
    plan = _full_screen_plan_with_names(names, {3: "Bagpipe", 6: "Bagpipe"})
    # 第 3 张错位（画面里是风笛）⇒ 回列表点回第 3 格，可这次还是报风笛 ⇒ 放弃这一张
    plan.insert(2 + (3 - 1) * 2 + 2, [])
    plan.insert(2 + (3 - 1) * 2 + 3, _detail_batch(name_en="Bagpipe"))
    pipeline = FakePipelineService([[], *plan, _screen_callbacks(names)])
    box = FakeBox()
    service = _service(
        pipeline=pipeline,
        box=box,
        core=FakeCoreClient(frames=frames),
        resource=FakeResourceService(tmp_path / "layer"),
        catalog=EN_CATALOG,
    )

    async def scenario() -> None:
        await service.submit(mode="full")
        await _wait_status(service, TrainingScanStatus.SUCCEEDED.value)
        state = service.snapshot()

        assert state["realigned"] == 1
        assert state["updated"] == 1
        # 重锚之后还是对不上：这一张的读数不写库（快照里的 skipped 是计数）
        assert [item["char_id"] for item in box.applied] == ["char_123_bagpipe"]
        assert state["skipped"] == 1

    asyncio.run(scenario())


def test_full_round_writes_by_card_name_when_the_latin_name_says_nothing(tmp_path: Path):
    """拉丁名读成谁也认不出的东西 ⇒ 按卡名照常写库，只把 ``unverified`` 加一（M40）."""
    frames = _frames(tmp_path, count=40)
    names = {BAGPIPE_CELL: "风笛"}
    pipeline = FakePipelineService(
        [
            [],
            *_full_screen_plan_with_names(names, {6: "Sword of the Silent Night"}),
            _screen_callbacks(names),
        ]
    )
    box = FakeBox()
    service = _service(
        pipeline=pipeline,
        box=box,
        core=FakeCoreClient(frames=frames),
        resource=FakeResourceService(tmp_path / "layer"),
        catalog=EN_CATALOG,
    )

    async def scenario() -> None:
        await service.submit(mode="full")
        await _wait_status(service, TrainingScanStatus.SUCCEEDED.value)
        state = service.snapshot()

        assert state["unverified"] == 1
        assert state["realigned"] == 0
        assert state["updated"] == 1
        assert [item["char_id"] for item in box.applied] == ["char_123_bagpipe"]

    asyncio.run(scenario())

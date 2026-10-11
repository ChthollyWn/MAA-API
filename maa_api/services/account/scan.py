"""干员详情练度扫描的服务层（M36 规格 §4.2–§4.5、§4.9）。

一句话：把「一格一格点开干员、读详情页、写库」这条慢流程做成普通流水线，由一条后台
协程串起来——接口只负责启动、看进度、取消。

**为什么不是一条流水线**：内核只跑 ``task_names`` 里第一个匹配上的任务（``Custom`` 的
语义，见 :class:`~maa_api.domain.task.CustomTask`），而这里要做的是「读这一屏 → 判断点
哪一格 → 读详情 → 回列表 → 翻屏」这种带分支的循环，任务表里写不出来。所以拆成一串
**小流水线**：一步一条，每条只提交一个自定义节点，循环与取消都留在 Python 侧。好处是
每一步都能进队列正常排队、状态与日志照旧可见，取消也能在步与步之间立刻生效。

**每个节点的出路都带 ``Done`` 兜底**（真机实测）：只提交一个节点时，内核会沿着它的
``next`` 一路走下去；某个候选一直识别不出来就重试 20 次再报 ``TaskChainError``，而 app
侧不把这条当终态，这一步就会一直等到超时。所以链上每个节点的 ``next`` 都是「下一个节点
+ ``Done``」（``JustReturn`` + ``Stop``，必定命中）：读得出来的节点依次跑完，读不出来的
节点被当场跳过、链立刻收尾；提交时再把 ``Done`` 追加成第二个候选，连链首节点读不出来
的情况也不会卡住——读数有就有、没有就没有。

**读数拿什么**：每一步跑完等它进终态（:func:`~maa_api.services.copilot.result.wait_pipeline`），
再回读这一步的回调——自定义节点的识别结果落在 ``details.details.result`` 里，M29 的
:func:`~maa_api.services.copilot.result.callback_signal` 认得三种信封层级。OCR 节点给
``{"text", "rect"}``、模板节点给 ``{"score", "box"}``。

**专精为什么不在链上读**：内核的多模板节点只认数组第一项、单亮点模板对暗点又没有区分度
（真机实测，见 :mod:`maa_api.services.account.scan_tasks` 顶部那一段），所以详情页节点跑完
之后取一帧截图，用 :mod:`maa_api.services.account.scan_image` 数亮点。截图取的是内核
``AsstGetImage`` 的那一份，与同批 OCR 是同一张画面。

**写库口径**（用户裁决）：读到什么写什么，没读到的不动——``apply_detail_scan`` 只写
``DETAIL_SCAN_FIELDS`` 三列，``None`` 一律表示「这次没读到」，绝不拿 0 冒充。
"""

from __future__ import annotations

import asyncio
import inspect
import io
import logging
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PIL import Image

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.domain.enums import PipelineStatus
from maa_api.services.account import scan_image
from maa_api.services.account import scan_tasks as st

if TYPE_CHECKING:  # pragma: no cover - 只为注解；运行时走 default_catalog 的延迟 import
    from maa_api.services.account.capture import OperatorMeta

logger = logging.getLogger(__name__)


def _result_helpers() -> Any:
    """``copilot.result`` 的延迟入口（:func:`submit_pipeline` / :func:`wait_pipeline` 等）。

    那个模块为了 ``Message`` 常量 import ``maa_api.core.enums``，而本模块被账号路由
    import——顶层直接 import 会把内核相关模块拖进 ``import maa_api.main`` 的依赖图
    （``tests/api/test_app_skeleton.py`` 的 import 隔离门禁）。M29 在 main.py 里装配
    ``CopilotResultService`` 时也是延迟 import，同一条路子。
    """

    from maa_api.services.copilot import result

    return result

#: 默认扫多少名干员（规格 §4.3），以及上限。
DEFAULT_LIMIT = 40
MAX_LIMIT = 200

#: 干员名字段里要去掉的形态后缀（界面上「XX·新」「XX·复制人」指的还是同一个 char_id）。
NAME_SUFFIXES: tuple[str, ...] = ("·复制人", "·新", "（复制人）", "(复制人)", "·", "·")

#: 卡名短到只剩一两个字时多半是 OCR 读花了，不当成名字用。
NAME_MIN_LENGTH = 2

#: 模组等级读数的分数门槛：真机「STAGE 3」约 0.91；灰字「模组」那种垃圾读数会低一截，
#: 低于门槛就按「没读到」处理（不写库，也不当成 0）。
MODULE_STAGE_MIN_SCORE = 0.6

#: 专精与模组的精英化门槛（与 ``rules`` 的编辑约束一致）：详情页读到低于精二就不提交这两列。
MIN_MASTERY_ELITE = 2

#: 单步超时（秒）：一步只做一件事，90 秒还没进终态基本是卡住了。
DEFAULT_STEP_TIMEOUT_SECONDS = 90.0

#: 起手 ``StartUp`` 的超时（秒）：拉客户端比单步慢，冷启动实测 7 秒左右，留足余量。
DEFAULT_STARTUP_TIMEOUT_SECONDS = 180.0

#: 扫完名单还差人时要多翻几屏：留 3 倍屏数再加 3 屏余量（卡名漏检会让「已扫数」落后）。
SCREEN_FACTOR = 3
SCREEN_MARGIN = 3

#: ``submit`` 等第一步进队列的上限（秒）。等不到就当这一轮没开起来。
SUBMIT_STARTED_TIMEOUT_SECONDS = 5.0

#: 流水线标题前缀：``练度扫描：读卡名`` 这样，便于在流水线列表里一眼认出来。
TASK_TITLE_PREFIX = "练度扫描"


class _Cancelled(Exception):
    """这一轮被取消了（内部信号，不对外抛）。"""


class TrainingScanStatus(StrEnum):
    """练度扫描一轮的推进状态（M36 规格 §4.6）。

    与 ``PipelineStatus`` 分开：一轮扫描会跑**多条**流水线（准备一条、每格一条、读详情
    一条、回列表一条），所以它自己得有一份状态，不能说「流水线完成了扫描就完了」。
    用 ``succeeded`` 而不是 ``completed``，与前端 ``TrainingScanStatus`` 一致。

    放在服务层而不是 :mod:`maa_api.domain.enums`：这是运行期状态、不落库，与
    ``CoreState`` / ``DeviceState`` / ``ResourceSource`` 同款（docs/04 §4 只收领域枚举，
    ``tests/domain/test_enums.py`` 会盯着这条线）。
    """

    IDLE = "idle"  # 还没扫过，或上一轮已被新的受理覆盖
    RUNNING = "running"  # 正在跑
    SUCCEEDED = "succeeded"  # 跑完了（个别干员读失败不算整体失败）
    FAILED = "failed"  # 整体失败（内核不可用、准备阶段就没进列表页等）
    CANCELLED = "cancelled"  # 被用户取消

    @property
    def is_running(self) -> bool:
        return self is TrainingScanStatus.RUNNING


def _title(step: str) -> str:
    """这一步的流水线标题。"""
    return f"{TASK_TITLE_PREFIX}：{step}"


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def _initial_state() -> dict[str, Any]:
    """一轮扫描的内部状态（公开形状由 :meth:`TrainingScanService.snapshot` 组装）。

    ``targets`` / ``deferred`` / ``unresolved`` / ``skipped`` / ``mismatched`` 在这里都是
    **明细列表**（``skipped`` / ``mismatched`` 是 ``{"char_id", "name", ...}``，
    ``unresolved`` / ``deferred`` 是名字），对外只暴露 ``skip``/``unresolved``/
    ``mismatched`` 的**计数**——规格 §4.6 的 GET 形状是计数，POST 形状才是数组。
    """
    return {
        "status": TrainingScanStatus.IDLE.value,
        "pipeline_id": None,
        "first_pipeline_id": None,
        "started_at": None,
        "finished_at": None,
        "scanned": 0,
        "updated": 0,
        "screens": 0,
        "total": None,
        "message": None,
        "cancel_requested": False,
        "targets": [],
        "deferred": [],
        "unresolved": [],
        "skipped": [],
        "mismatched": [],
    }


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _text_of(reading: Any) -> str:
    """读数里的文字：OCR 给 ``text``，个别版本把候选放在 ``all`` / ``best`` 里。"""
    if not isinstance(reading, Mapping):
        return ""
    text = reading.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()
    best_text = ""
    best_score = float("-inf")
    for key in ("all", "best"):
        for candidate in _as_list(reading.get(key)):
            if not isinstance(candidate, Mapping):
                continue
            candidate_text = candidate.get("text")
            if not isinstance(candidate_text, str) or not candidate_text.strip():
                continue
            raw_score = candidate.get("score")
            score = float(raw_score) if isinstance(raw_score, (int, float)) else 0.0
            if score > best_score:
                best_text, best_score = candidate_text.strip(), score
    return best_text


def _name_token(raw: str) -> str:
    """卡名 OCR 的原始文字 → 干净的干员名。"""
    token = raw.strip()
    for suffix in NAME_SUFFIXES:
        token = token.removesuffix(suffix)
    return "".join(token.split())


def _readings(callbacks: Iterable[Any]) -> dict[str, dict[str, Any]]:
    """把一步的回调收成 ``{节点短名: {"text", "score", "rect", "box"}}``（同节点后到覆盖先到）。

    识别结果在 ``details.details.result`` 里：OCR 给 ``{"text", "rect"}``、模板给
    ``{"score", "box"}``；同一个节点可能出现多条（多候选或重试），排在后面的更晚。
    """
    readings: dict[str, dict[str, Any]] = {}
    for item in callbacks:
        signal = _result_helpers().callback_signal(item)
        node = st.node_key(getattr(signal, "task", None))
        if not node:
            continue
        details = signal.details if isinstance(signal.details, Mapping) else {}
        inner = details.get("details")
        result = inner.get("result") if isinstance(inner, Mapping) else details.get("result")
        reading = readings.setdefault(node, {})
        for candidate in _as_list(result):
            if not isinstance(candidate, Mapping):
                continue
            text = _text_of(candidate)
            if text:
                reading["text"] = text
            score = candidate.get("score")
            if isinstance(score, (int, float)):
                reading["score"] = float(score)
            for key in ("rect", "box"):
                box = candidate.get(key)
                if isinstance(box, (list, tuple)) and len(box) == 4:
                    reading[key] = [int(value) for value in box]
    return readings


def _elite_level(readings: Mapping[str, Mapping[str, Any]]) -> int | None:
    """三个精英化模板节点里分数最高的那个 → 0/1/2；都没过阈值给 ``None``。"""
    best_level: int | None = None
    best_score = float("-inf")
    for node, level in st.ELITE_NODE_LEVELS.items():
        score = readings.get(st.node_key(node), {}).get("score")
        if isinstance(score, (int, float)) and float(score) > best_score:
            best_level, best_score = level, float(score)
    return best_level


def _skill_level(readings: Mapping[str, Mapping[str, Any]]) -> int | None:
    """技能等级（``RANK n``）：读出来落在 1..7 才算数。"""
    node = st.node_key(st.DETAIL_NODE_NAMES["SkillLevel"])
    text = _text_of(readings.get(node, {}))
    if not text.isdigit():
        return None
    level = int(text)
    return level if 1 <= level <= 7 else None


def _module(readings: Mapping[str, Mapping[str, Any]]) -> dict[str, int] | None:
    """模组那一列怎么填：读全了给 ``{类型: 等级}``（如 ``{"X": 3}``），读不全给 ``None``。

    两道门控（都来自真机标定）：

    - **先有编号才有等级**：没装模组的干员那块面板是灰字「模组」，OCR 会把灰字读成
      ``8`` 之类的垃圾（实测 score 0.39）；编号读不出来就一律当成未知。
    - **等级读数要够分**：``STAGE 3`` 真机约 0.91，垃圾读数低一截，低于
      :data:`MODULE_STAGE_MIN_SCORE` 就不要。

    「确认没装模组」在像素上分不清，所以服务层**不写** ``modules={}``——读不到就是未知。

    返回的键是**模块类型字母**（``X``/``Y``/``D``/``A``）：库里那份 ``modules`` 就是这么
    存的，``rules.normalize_modules`` 只认这几个键（写成 ``module1`` 会被判非法）。
    """
    code = _text_of(readings.get(st.node_key(st.MODULE_CODE), {}))
    module_type = st.module_type_of(code)
    if module_type is None:
        return None
    stage = readings.get(st.node_key(st.MODULE_STAGE), {})
    score = stage.get("score")
    if not isinstance(score, (int, float)) or float(score) < MODULE_STAGE_MIN_SCORE:
        return None
    level = st.module_stage_of(_text_of(stage))
    if level is None:
        return None
    return {module_type: level}


def _screen_cap(total: int) -> int:
    """一轮最多翻几屏：``名单 / 每屏格数`` 的三倍再加三屏余量。"""
    cells = st.GRID_COLUMNS * len(st.ROW_KEYS)
    needed = math.ceil(total / cells) if total else 1
    return SCREEN_FACTOR * max(needed, 1) + SCREEN_MARGIN


def _cancel_caller(service: Any) -> Callable[[str], Any] | None:
    """找出流水线服务上「取消某条流水线」的入口（兼容直接给 runner 的装配）。"""
    for candidate in (
        service,
        getattr(service, "runner", None),
        getattr(service, "pipeline_runner", None),
    ):
        caller = getattr(candidate, "request_cancel", None)
        if callable(caller):
            return caller
    return None


def default_catalog() -> dict[str, OperatorMeta]:
    """默认干员目录：读本机 ``battle_data.json``。

    延迟 import ``capture``：它为了 ``Message`` 常量拉进 ``maa_api.core.enums``，而这个模块
    被 API 路由 import，会把内核相关模块带进 ``import maa_api.main`` 的依赖图
    （``tests/api/test_app_skeleton.py`` 的 import 隔离门禁盯着这件事）。
    """

    from maa_api.services.account.capture import load_operator_catalog

    return load_operator_catalog()


class TrainingScanService:
    """干员详情练度扫描（规格 §4.3–§4.5、§4.9）。

    :param db_session: 会话工厂（``app.state.db_session``）。
    :param box: :class:`~maa_api.services.account.box.OperatorBoxService`，读写账号表。
    :param pipeline_service: 能 ``submit`` 流水线的服务（``app.state.pipeline_runner``）。
    :param plans: 养成计划服务（``app.state.upgrade_plan_service``），取「进行中计划的目标」。
    :param core_client: 内核客户端，只为 :meth:`_grab_image` 取一帧截图。
    :param catalog: 干员目录工厂，默认读本机 ``battle_data.json``（测试注入替身）。
    :param list_operators: 读账号表干员列表，默认走 ``box.list_operators``。
    :param resource_service: 资源服务（``resource_root`` + ``reload_resources``）。
    :param client_type_provider: 取默认渠道（``settings.channel.client_type``），起手
        ``StartUp`` 用；不给就不带这个字段，交给内核用默认渠道。
    :param step_timeout_seconds: 单步等终态的上限。
    :param startup_timeout_seconds: 起手 ``StartUp`` 等终态的上限（拉客户端比单步慢）。
    :param screenshot_dir: 给了就把每张详情页截图落盘（排查用），不给不留痕。
    """

    def __init__(
        self,
        db_session: Any,
        *,
        box: Any,
        pipeline_service: Any,
        plans: Any = None,
        core_client: Any = None,
        catalog: Callable[[], dict[str, OperatorMeta]] = default_catalog,
        list_operators: Callable[..., Any] | None = None,
        resource_service: Any = None,
        client_type_provider: Callable[[], str | None] | None = None,
        step_timeout_seconds: float = DEFAULT_STEP_TIMEOUT_SECONDS,
        startup_timeout_seconds: float = DEFAULT_STARTUP_TIMEOUT_SECONDS,
        screenshot_dir: str | Path | None = None,
    ) -> None:
        self._db_session = db_session
        self._box = box
        self._pipeline_service = pipeline_service
        self._plans = plans
        self._core_client = core_client
        self._catalog = catalog
        self._list_operators = list_operators or self._operators_from_box
        self._resource_service = resource_service
        self._client_type_provider = client_type_provider
        self._step_timeout = float(step_timeout_seconds)
        self._startup_timeout = float(startup_timeout_seconds)
        self._screenshot_dir = Path(screenshot_dir) if screenshot_dir else None
        self._ensured = False
        self._lock = asyncio.Lock()
        self._driver: asyncio.Task[None] | None = None
        self._started: asyncio.Event | None = None
        self._state = _initial_state()

    # -- 对外：名单、启动、状态、取消 -------------------------------------

    async def resolve_targets(
        self,
        *,
        limit: int = DEFAULT_LIMIT,
        char_ids: Sequence[str] | None = None,
        include_plan_targets: bool = True,
    ) -> dict[str, Any]:
        """按规格 §4.3 组这一轮的名单（只算名单，不碰设备）。

        两个来源：账号表里精二及以上的干员（``list_operators(elite_min=2)``），以及
        **进行中**养成计划的目标（名字 → ``char_id`` 用干员目录解析）。合并去重后按
        ``char_id`` 排序取前 ``limit`` 名，多出来的进 ``deferred``（不是失败）。

        ``char_ids`` 给了就以它为准（仍会补上计划目标）。
        """
        limit = int(limit)
        if not 1 <= limit <= MAX_LIMIT:
            raise AppError(
                ErrorCode.VALIDATION_ERROR, f"limit 只能是 1 到 {MAX_LIMIT}，收到 {limit}"
            )

        catalog = self._catalog() or {}
        index, ambiguous = _name_index(catalog)

        candidates: list[dict[str, str]] = []
        seen: set[str] = set()
        unresolved: list[str] = []

        for raw in char_ids or ():
            token = str(raw).strip()
            if not token:
                continue
            # 允许直接给名字（Agent 工具的 ``char_ids`` 描述就是这么写的）：先按 ``char_id``
            # 查目录，查不到再按名字索引解析；同名多形态的名字不进名单（不猜是哪一个）。
            char_id = token if token in catalog else index.get(token)
            meta = catalog.get(char_id) if char_id else None
            if char_id is None or meta is None or not meta.name:
                # 目录里既没有这个 id、也没有这个名字：认不出卡片，如实记进 unresolved。
                unresolved.append(token)
                continue
            _add_target(candidates, seen, char_id, meta.name)

        if include_plan_targets:
            for name in await self._plan_operator_names():
                char_id = index.get(name)
                if char_id is None:
                    reason = "同名多个形态" if name in ambiguous else "目录里没有这个干员"
                    unresolved.append(name)
                    continue
                meta = catalog.get(char_id)
                _add_target(candidates, seen, char_id, (meta.name if meta else None) or name)

        if not char_ids:
            # 取满一页再看（不按 limit 截断）：deferred 要列出「排在后面的还有谁」，
            # 交给账号服务截断就看不到他们了。MAX_LIMIT 也是接口允许的上限。
            page = self._list_operators(
                owned_only=True, elite_min=2, limit=MAX_LIMIT, offset=0
            )
            if inspect.isawaitable(page):
                # 默认来源（账号服务的 ``list_operators``）是 async 的：漏 await 会拿到协程，
                # 这里当空名单处理就会错报「精二名单是空的」（M37 规格 §2.1）。
                page = await page
            items = page.get("items") if isinstance(page, Mapping) else None
            for item in items or []:
                if not isinstance(item, Mapping):
                    continue
                char_id = str(item.get("char_id") or "").strip()
                if not char_id:
                    continue
                name = str(item.get("name") or "").strip()
                if not name:
                    meta = catalog.get(char_id)
                    name = (meta.name if meta else None) or char_id
                _add_target(candidates, seen, char_id, name)

        candidates.sort(key=lambda entry: entry["char_id"])
        chosen = candidates[:limit]
        deferred = [entry["name"] for entry in candidates[limit:]]
        return {
            "targets": chosen,
            "deferred": deferred,
            "unresolved": unresolved,
            "total": len(chosen),
        }

    async def submit(
        self,
        *,
        limit: int = DEFAULT_LIMIT,
        char_ids: Sequence[str] | None = None,
        include_plan_targets: bool = True,
    ) -> dict[str, Any]:
        """受理一轮扫描（规格 §4.6 的 POST 形状）：组名单、铺节点、起后台驱动。

        同一时刻只允许一轮（规格 §4.9）：还在跑就 ``409``。返回时已经确认**第一步**
        进了队列——回一个 ``pipeline_id`` 是空的「受理成功」没有意义。
        """
        async with self._lock:
            if self.is_running():
                raise AppError(
                    ErrorCode.PIPELINE_ALREADY_RUNNING,
                    "已经有练度扫描在跑，先等它结束或取消",
                )

            resolved = await self.resolve_targets(
                limit=limit, char_ids=char_ids, include_plan_targets=include_plan_targets
            )
            if not resolved["targets"]:
                raise AppError(
                    ErrorCode.VALIDATION_ERROR,
                    "这一轮没有可扫的干员：精二名单是空的，先跑一次干员识别，或检查养成计划",
                )

            # 节点先落地再起驱动：驱动一开始就要提交第一步，节点不在内核里就白跑。
            # 幂等语义照 M29 的 ``ensure_tasks``：内容没变不重写，进程内确认过一次就不再
            # 重载内核资源（文件被清理掉才会再来一遍）。
            await self._ensure_tasks()

            self._state = _initial_state()
            self._state.update(
                {
                    "status": TrainingScanStatus.RUNNING.value,
                    "started_at": _iso(datetime.now(timezone.utc)),
                    "total": resolved["total"],
                    "targets": list(resolved["targets"]),
                    "deferred": list(resolved["deferred"]),
                    "unresolved": list(resolved["unresolved"]),
                }
            )
            self._started = asyncio.Event()
            self._driver = asyncio.create_task(
                self._drive(list(resolved["targets"])), name="training-scan"
            )

        try:
            await asyncio.wait_for(self._started.wait(), timeout=SUBMIT_STARTED_TIMEOUT_SECONDS)
        except TimeoutError:
            raise AppError(
                ErrorCode.CORE_COMMAND_TIMEOUT,
                f"练度扫描没能及时开跑（等第一步超过 {SUBMIT_STARTED_TIMEOUT_SECONDS:.0f} 秒）",
            ) from None

        if self._state["pipeline_id"] is None:
            raise AppError(
                ErrorCode.CORE_COMMAND_FAILED,
                self._state["message"] or "练度扫描没能提交第一步，请稍后重试",
            )
        return self.submission()

    def snapshot(self) -> dict[str, Any]:
        """当前状态（规格 §4.6 的 GET 形状）：计数 + 名单，明细不外露。"""
        state = self._state
        return {
            "status": state["status"],
            "pipeline_id": state["pipeline_id"],
            "started_at": state["started_at"],
            "finished_at": state["finished_at"],
            "scanned": state["scanned"],
            "updated": state["updated"],
            "skipped": len(state["skipped"]),
            "unresolved": len(state["unresolved"]),
            "mismatched": len(state["mismatched"]),
            "message": state["message"],
            "total": state["total"],
            "targets": [dict(entry) for entry in state["targets"]],
            "deferred": list(state["deferred"]),
        }

    def submission(self) -> dict[str, Any]:
        """受理结果（规格 §4.6 的 POST 形状，``pipeline_id`` 是**当前这一步**的流水线）。"""
        state = self._state
        return {
            "pipeline_id": state["pipeline_id"],
            "total": state["total"] or 0,
            "targets": [dict(entry) for entry in state["targets"]],
            "deferred": list(state["deferred"]),
            "unresolved": list(state["unresolved"]),
        }

    def is_running(self) -> bool:
        """这一轮还在跑吗（规格 §4.9 的并发锁就看它）。"""
        return self._state["status"] == TrainingScanStatus.RUNNING.value

    async def cancel(self) -> dict[str, Any]:
        """请求取消（规格 §4.9）：先标状态，再让当前这一步停。

        已经读到的干员保留（他们的读数早就写库了）；停在**步与步之间**，不会把设备
        丢在详情页——当前这一步的收尾（回列表）照跑。
        """
        state = self._state
        if not self.is_running():
            return self.snapshot()
        state["cancel_requested"] = True
        state["status"] = TrainingScanStatus.CANCELLED.value
        state["message"] = "已请求取消：这一步跑完就停，已经读到的练度保留"
        pipeline_id = state["pipeline_id"]
        caller = _cancel_caller(self._pipeline_service)
        if pipeline_id and caller is not None:
            outcome = caller(pipeline_id)
            if inspect.isawaitable(outcome):
                await outcome
        return self.snapshot()

    # -- 驱动 --------------------------------------------------------------

    async def _drive(self, targets: Sequence[Mapping[str, str]]) -> None:
        """一轮扫描的驱动：起手 → 进列表 → 读屏 → 逐格读详情 → 翻屏 → 收尾写记录。"""
        state = self._state
        position = 0
        try:
            await self._start_game()
            await self._enter_box()
            position = await self._scan(targets)
            state["status"] = TrainingScanStatus.SUCCEEDED.value
            state["message"] = f"扫完 {state['scanned']} 名干员，写入 {state['updated']} 名"
            logger.info(
                "练度扫描完成：名单 %d 名，读屏 %d 次，写入 %d 名，跳过 %d 名",
                len(targets),
                state["screens"],
                state["updated"],
                len(state["skipped"]),
            )
        except _Cancelled:
            state["status"] = TrainingScanStatus.CANCELLED.value
            state["message"] = "扫描被取消；已经写库的练度保留"
            logger.info("练度扫描被取消：已扫 %d 名", state["scanned"])
        except asyncio.CancelledError:
            state["status"] = TrainingScanStatus.CANCELLED.value
            state["message"] = "扫描被取消；已经写库的练度保留"
            raise
        except AppError as error:
            state["status"] = TrainingScanStatus.FAILED.value
            state["message"] = str(error.message or error)
            logger.warning("练度扫描失败：%s", state["message"])
        except Exception as error:  # noqa: BLE001 - 后台任务不能让异常逃逸
            state["status"] = TrainingScanStatus.FAILED.value
            state["message"] = f"扫描中断：{error}"
            logger.exception("练度扫描异常退出")
        finally:
            state["finished_at"] = _iso(datetime.now(timezone.utc))
            self._driver = None
            if self._started is not None:
                self._started.set()  # 别让 submit 一直等
            try:
                await self._record(state["updated"])
            except Exception:  # noqa: BLE001 - 识别记录写不上不该盖掉扫描结果
                logger.warning("练度扫描的识别记录写入失败", exc_info=True)
            self._state["cancel_requested"] = False

    async def _scan(self, targets: Sequence[Mapping[str, str]]) -> int:
        """逐屏扫，返回「已经推进到名单的第几名」（收尾时用来记没扫到的人）。

        同一个人只扫一次（``done``）：翻屏可能翻不动或翻过头，一屏里再读到已经扫过的
        卡名时直接跳过，不然会重复点开、重复写库，还会把「已经推进到第几名」算多——
        多出来的空位会把真正没找到的人静默吞掉（真机实测：7 人名单纯翻屏一次时
        ``scanned`` 记了 11，没找到的那位既不在 ``skipped`` 也不在 ``deferred``）。
        """
        state = self._state
        if not targets:
            return 0
        index: dict[str, str] = {}
        for target in targets:
            name = str(target.get("name") or "")
            if name:
                index.setdefault(name, str(target.get("char_id") or ""))
        wanted = {str(target.get("char_id") or "") for target in targets}
        cap = _screen_cap(len(targets))
        position = 0
        done: set[str] = set()

        while position < len(targets):
            if state["cancel_requested"]:
                raise _Cancelled
            screen = await self._read_box_screen()
            state["screens"] += 1
            if not screen and state["screens"] == 1:
                # 第一屏一个卡名都没读出来：画面多半不是干员列表页（M37 规格 §4.4）。
                # 不快速失败的话，这一轮会安静地跑完再报「全跳过」，用户看到的是「无操作」。
                raise AppError(
                    ErrorCode.CORE_COMMAND_FAILED,
                    f"{_title('读卡名')}一个卡名都没读出来"
                    "（画面可能不是干员列表页，先让游戏进到干员列表）",
                )
            matched = 0
            for row in st.ROW_KEYS:
                for column in range(st.GRID_COLUMNS):
                    if state["cancel_requested"]:
                        raise _Cancelled
                    name = screen.get((row, column))
                    if not name:
                        continue  # 这一格卡名没读出来：跳过（见 docs/13 的固定漏检）
                    char_id = index.get(name)
                    if char_id is None or char_id not in wanted:
                        continue  # 不在名单里（或同名多形态指不定是谁）
                    if char_id in done:
                        continue  # 翻屏没翻动／翻过头，这一格是刚扫过的人
                    done.add(char_id)
                    matched += 1
                    await self._scan_cell(char_id, name, row, column)
                    state["scanned"] += 1
            position += matched
            if position >= len(targets):
                break
            if state["screens"] >= cap:
                logger.info(
                    "练度扫描到屏数上限 %d，还有 %d 名没扫", cap, len(targets) - position
                )
                break
            await self._swipe_next_page()

        if position < len(targets):
            for target in targets[position:]:
                state["skipped"].append(
                    {
                        "char_id": str(target.get("char_id") or ""),
                        "name": str(target.get("name") or ""),
                        "reason": "这一轮没在列表里找到（可能超出屏数上限，或卡名没读出来）",
                    }
                )
        return position

    async def _read_box_screen(self) -> dict[tuple[str, int], str]:
        """读一屏卡名 → ``{(行, 列): 干员名}``（读不出来的格不在这张表里）。"""
        readings = await self._run_chain(st.box_screen_nodes(), title=_title("读卡名"))
        names: dict[tuple[str, int], str] = {}
        for reported, reading in readings.items():
            position = st.parse_card_name_node(reported)
            name = _name_token(_text_of(reading))
            if position is None or len(name) < NAME_MIN_LENGTH:
                continue
            names[position] = name
        logger.debug("练度扫描读屏：%d 格有读数 %s", len(names), names)
        return names

    async def _scan_cell(self, char_id: str, name: str, row: str, column: int) -> None:
        """点开一格 → 读详情 → 写库 → 回列表。

        单人格失败不打断整轮（规格 §4.9）：记进 ``skipped`` 继续下一格。回列表放在
        ``finally`` 里，保证这一格出事也把设备留在列表页。
        """
        state = self._state
        try:
            await self._run_step(st.click_cell_node(row, column), title=_title(f"点开 {name}"))
            detail = await self._detail_scan()
            # 精英化不写库（``apply_detail_scan`` 只收三列），但它是专精/模组的前置条件：
            # 没到精二这两列本来就写不进去（box 会拒），索性不提交，日志也就不会把「规则
            # 拒绝」误报成「读花了」。读数本身仍留在 detail 里（诊断用）。
            elite = detail["elite"]
            ready = elite is None or elite >= MIN_MASTERY_ELITE
            if not ready:
                logger.info(
                    "练度扫描：%s（%s）只有精英化 %s，专精与模组按未解锁处理（不写库）",
                    name,
                    char_id,
                    elite,
                )
            result = await self._box.apply_detail_scan(
                char_id,
                skill_level=detail["skill_level"],
                # 全 0 也是读数，要写；空表只可能是没取到截图，那才当「没读到」。
                masteries=(detail["masteries"] or None) if ready else None,
                modules=detail["modules"] if ready else None,
            )
            if result is None:
                state["mismatched"].append({"char_id": char_id, "name": name})
                logger.info("练度扫描：%s（%s）不在账号表里，读到的不写库", name, char_id)
                return
            written = list(result.get("updated") or [])
            rejected = list(result.get("skipped") or [])
            if written:
                state["updated"] += 1
            if rejected:
                logger.warning(
                    "练度扫描：%s（%s）这几个字段读到的值越界/读花了、没写：%s",
                    name,
                    char_id,
                    "、".join(rejected),
                )
            logger.info(
                "练度扫描：%s（%s）%s",
                name,
                char_id,
                "、".join(written) if written else "没有可写的字段",
            )
        except AppError as error:
            reason = str(error.message or error)
            state["skipped"].append({"char_id": char_id, "name": name, "reason": reason})
            logger.warning("练度扫描：%s（%s）这一格没读成：%s", name, char_id, reason)
        finally:
            await self._best_effort_back()

    async def _detail_scan(self) -> dict[str, Any]:
        """读一张详情页：节点读数 + 一帧截图数专精。"""
        readings = await self._run_chain(st.detail_screen_nodes(), title=_title("读详情页"))
        image = await self._grab_image()
        masteries = scan_image.mastery_levels_from_image(image) if image is not None else {}
        return {
            "elite": _elite_level(readings),
            "skill_level": _skill_level(readings),
            "masteries": masteries,
            "modules": _module(readings),
        }

    async def _start_game(self) -> None:
        """起手把客户端拉起来（M37 规格 §4.3）。

        全仓既有口径：MaaCore 的任务都从主界面起手，只有 ``StartUp`` 会拉起客户端
        （``maa_api/agent/preconditions.py``）。内核实测 ``StartUp`` 只负责拉起 / 等在主界面，
        **不把游戏内页面带回主界面**，所以进列表仍交给 ``OperBoxBegin`` 链。

        参数按 :class:`~maa_api.domain.task.StartUpInput` 的字段平铺下发（``extra="forbid"``，
        没有 ``params`` 这个口子）：``start_game_enabled`` 固定为真，``client_type`` 只在
        取得到默认渠道时才带，取不到就让内核用设置里的渠道。
        """
        task: dict[str, Any] = {"name": "StartUp", "start_game_enabled": True}
        provider = self._client_type_provider
        client_type = (provider() if callable(provider) else None) or None
        if client_type:
            task["client_type"] = client_type
        await self._run_tasks(
            [task],
            title=_title("启动游戏"),
            timeout=self._startup_timeout,
            timeout_hint="游戏没起来或设备掉了",
        )

    async def _enter_box(self) -> None:
        """进干员列表页（内核 ``OperBoxBegin`` 链会顺带按「等级」排序）。"""
        await self._run_step(st.ENTER_BOX, title=_title("进干员列表"))

    async def _swipe_next_page(self) -> None:
        """列表翻到下一屏（横向从右往左划，见 :data:`st.SWIPE_FROM`）。"""
        await self._run_step(st.SWIPE_NEXT_PAGE, title=_title("翻到下一屏"))

    async def _best_effort_back(self) -> None:
        """回列表（尽力而为）：失败只记日志，不盖掉这一格真正的原因。"""
        try:
            await self._run_step(st.BACK, title=_title("回到列表"))
        except _Cancelled:
            raise
        except Exception as error:  # noqa: BLE001 - 收尾步骤不该冒泡
            logger.warning("练度扫描回列表失败：%s", error)

    async def _grab_image(self) -> Image.Image | None:
        """取内核最近一帧（``AsstGetImage``，与刚跑的 OCR 同一张画面）→ PIL 图。

        取不到就给 ``None``：这一张的专精按未知处理（不写库），其它读数照写。
        """
        client = self._core_client
        if client is None:
            return None
        save_to: str | None = None
        if self._screenshot_dir is not None:
            self._screenshot_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
            save_to = str(self._screenshot_dir / f"detail-{stamp}.png")
        try:
            outcome = client.get_image(save_to=save_to) if save_to else client.get_image()
            payload = await outcome if inspect.isawaitable(outcome) else outcome
        except AppError as error:
            logger.warning("练度扫描取截图失败，这一张的专精按未知处理：%s", error.message or error)
            return None
        path = payload.get("path") if isinstance(payload, Mapping) else None
        data = payload.get("data") if isinstance(payload, Mapping) else None
        try:
            if isinstance(path, (str, Path)) and str(path):
                with Image.open(path) as handle:
                    return handle.convert("RGB").copy()
            if isinstance(data, (bytes, bytearray)):
                with Image.open(io.BytesIO(bytes(data))) as handle:
                    return handle.convert("RGB").copy()
        except (OSError, ValueError) as error:
            logger.warning("练度扫描读截图失败，这一张的专精按未知处理：%s", error)
        return None

    # -- 一步流水线 --------------------------------------------------------

    async def _run_step(self, node: str, *, title: str) -> dict[str, dict[str, Any]]:
        """提交一条以 ``node`` 开头的链，等它进终态，收这一步的识别读数。

        ``Done`` 追加在 ``task_names`` 末尾（见模块 docstring）：链首这个节点识别不出来
        时，内核在同一轮里就命中它并收尾，不会重试到报错、也不会把这一步拖到超时。

        读数取自**内核实时回调**（:meth:`tap`）：识别结果不进 ``log_entry``，真机实测
        ``callbacks_for`` 回读一条读数都拿不到（只有「开始/完成/全部完成」三条生命周期
        消息，见 ``docs/13`` §8.51）。``callbacks_for`` 那份留着兜底，不做主路径。
        """
        return await self._run_tasks(
            [{"name": "Custom", "task_names": [node, st.DONE]}], title=title
        )

    async def _run_tasks(
        self,
        tasks: Sequence[Mapping[str, Any]],
        *,
        title: str,
        timeout: float | None = None,
        timeout_hint: str = "设备可能掉线或正忙",
    ) -> dict[str, dict[str, Any]]:
        """提交一条流水线、等它进终态、收读数（:meth:`_run_step` 与起手 ``StartUp`` 共用）。"""
        limit = self._step_timeout if timeout is None else float(timeout)
        with self.tap() as tap:
            pipeline_id = await _result_helpers().submit_pipeline(
                self._pipeline_service,
                tasks=[dict(task) for task in tasks],
                title=title,
            )
            if pipeline_id:
                # ``pipeline_id`` 对外是「当前这一步」，识别记录里留的是「这一轮的第一步」——
                # 一轮有很多条流水线，指向开头那条才看得出扫描从哪起的。
                self._state["pipeline_id"] = pipeline_id
                if self._state.get("first_pipeline_id") is None:
                    self._state["first_pipeline_id"] = pipeline_id
            if self._started is not None:
                self._started.set()  # submit 等到第一步进队列就能返回
            if not pipeline_id:
                raise AppError(ErrorCode.CORE_COMMAND_FAILED, f"{title}没能进队列")
            pipeline, finished = await _result_helpers().wait_pipeline(
                self._pipeline_service, pipeline_id, timeout_seconds=limit
            )
            if not finished:
                raise AppError(
                    ErrorCode.CORE_COMMAND_TIMEOUT,
                    f"{title}超过 {limit:.0f} 秒还没结束（{timeout_hint}）",
                )
            status = str(getattr(pipeline, "status", "") or "")
            if status == PipelineStatus.CANCELLED.value:
                raise _Cancelled
            if status == PipelineStatus.FAILED.value:
                raise AppError(
                    ErrorCode.CORE_COMMAND_FAILED, f"{title}没跑成（内核报 failed）"
                )
        return _readings([*tap.callbacks, *await self._callbacks(pipeline_id)])

    def tap(self, client: Any = None) -> Any:
        """订阅内核 ``CALLBACK``，把这一步期间到的回调攒进 tap。

        与 ``CopilotResultService.tap`` 同一套路：内核的 ``on`` 没有对应的 ``off``，所以
        每一步新开一个 tap，退出了 ``with`` 就不再收集（``CallbackTap.active``）。
        """
        tap = _result_helpers().CallbackTap()
        register = getattr(client if client is not None else self._core_client, "on", None)
        if callable(register):
            register("CALLBACK", tap.handle)
        return tap


    async def _run_chain(
        self, nodes: Sequence[str], *, title: str
    ) -> dict[str, dict[str, Any]]:
        """一次流水线读完整条链，收下回调里出现的读数。

        链上每个节点的 ``next`` 都带 ``Done`` 兜底（真机实测的必需写法），所以内核会沿着
        链往下走、**跳过识别不出来的节点**，一次提交就能拿到这条链上所有读得出来的读数：
        读不出来的节点只是没有回调，不会截断后面的节点。只收这条链里的节点，免得把同一步
        里别的读数混进来。
        """
        step = await self._run_step(nodes[0], title=title)
        wanted = {st.node_key(node) for node in nodes}
        return {key: value for key, value in step.items() if key in wanted}

    async def _callbacks(self, pipeline_id: str) -> list[Any]:
        """回读一步的识别回调（``callbacks_for`` 自己翻页，一步的量很小）。"""
        reader = getattr(self._pipeline_service, "callbacks_for", None)
        if reader is None:
            return []
        items = reader(pipeline_id)
        if inspect.isawaitable(items):
            items = await items
        return list(items or [])

    # -- 落地 --------------------------------------------------------------

    async def _ensure_tasks(self, *, force: bool = False) -> dict[str, Any]:
        """把扫描节点写进自定义资源层并让内核重载一次（幂等，返回落地结果）。

        与 M29 的结果识别任务同一套路（``CopilotResultService.ensure_tasks``）：写**增量
        层**，不碰内核自己的资源文件；内容没变不重写（``written=False``），进程内确认过
        一次就不再重载内核资源（``cached=True``）——每次扫描都 ``AsstLoadResource`` 一遍
        是没必要的开销。文件被清理掉、或调用方显式 ``force=True`` 时才再来一遍。

        返回 ``{"written", "reloaded", "cached", "path", "tasks"}``。
        """
        service = self._resource_service
        root = getattr(service, "resource_root", None)
        if service is None or root is None:
            raise AppError(
                ErrorCode.SERVICE_UNAVAILABLE,
                "练度扫描还没装配好（缺少资源服务，节点写不进内核）",
            )
        path = st.scan_task_path(root)
        tasks = st.operator_scan_tasks()
        if self._ensured and not force and path.is_file():
            return {
                "written": False,
                "reloaded": False,
                "cached": True,
                "path": str(path),
                "tasks": sorted(tasks),
            }
        written = _result_helpers()._atomic_json_write(path, tasks)
        reloaded = await _result_helpers()._reload(service)
        self._ensured = reloaded
        if not reloaded:
            logger.warning(
                "练度扫描节点已写入 %s，但没有可用的资源重载回调；内核重启前看不到这些节点",
                path,
            )
        elif written:
            logger.info("练度扫描节点已写入 %s", path)
        return {
            "written": written,
            "reloaded": reloaded,
            "cached": False,
            "path": str(path),
            "tasks": sorted(tasks),
        }

    async def _record(self, updated: int) -> None:
        """写一条 ``training`` 识别记录（规格 §4.5，一轮只写一条）。"""
        await self._box.record_training_scan(
            updated=updated, pipeline_id=self._state.get("first_pipeline_id")
        )

    async def _operators_from_box(self, **kwargs: Any) -> Mapping[str, Any]:
        """默认的干员列表来源：账号服务的 ``list_operators``。

        那个方法是 async 的（M37 规格 §3 根因一）：这里是它的唯一入口，必须等出结果再返回，
        不能把协程当名单交出去。
        """
        page = self._box.list_operators(**kwargs)
        return await page if inspect.isawaitable(page) else page

    async def _plan_operator_names(self) -> list[str]:
        """进行中养成计划的目标干员名（规格 §4.3 的来源二）。"""
        if self._plans is None:
            raise AppError(
                ErrorCode.SERVICE_UNAVAILABLE,
                "练度扫描还没装配好（缺少养成计划服务，取不到计划目标）",
            )
        payload = self._plans.list_plans()
        if inspect.isawaitable(payload):
            payload = await payload
        names: list[str] = []
        for plan in (payload or {}).get("plans") or []:
            if not isinstance(plan, Mapping) or plan.get("status") != "active":
                continue
            for target in plan.get("targets") or []:
                if not isinstance(target, Mapping):
                    continue
                name = str(target.get("operator") or "").strip()
                if name and name not in names:
                    names.append(name)
        return names


def _name_index(catalog: Mapping[str, OperatorMeta]) -> tuple[dict[str, str], set[str]]:
    """名字 → ``char_id``；同名多个形态的名字另收一份（不猜是哪一个）。"""
    index: dict[str, str] = {}
    ambiguous: set[str] = set()
    for char_id, meta in catalog.items():
        name = (meta.name or "").strip()
        if not name:
            continue
        if name in index:
            ambiguous.add(name)
            continue
        index[name] = char_id
    for name in ambiguous:
        index.pop(name, None)
    return index, ambiguous


def _add_target(
    candidates: list[dict[str, str]], seen: set[str], char_id: str, name: str
) -> None:
    """把一名干员收进候选名单（同一个 ``char_id`` 只收一次）。"""
    if not char_id or char_id in seen:
        return
    seen.add(char_id)
    candidates.append({"char_id": char_id, "name": name})

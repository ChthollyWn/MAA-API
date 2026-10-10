"""作业结果识别与判定（M29，规格 §9.7 的 outcome 表、T6）。

一次尝试结束后（不论成功还是失败）立刻跑一组**只读**识别任务，把画面判成
``three_star`` / ``two_star`` / ``clear`` / ``failed`` / ``sanity_insufficient`` /
``not_started``，识别不出来就是 ``unknown``（交给模型用 ``record_copilot_result`` 兜底）。
本模块三块内容：

.. _m29-result-tasks:

一、识别任务文件（``copilot_result_tasks()``）
--------------------------------------------

写入 custom 层的**独立文件**（不碰 ``ResourceService`` 聚合出来的
``tasks.json``，也不与用户的 ``Custom_*`` 任务混在一个文件里）::

    <resource_root>/maa-layers/custom/resource/tasks/MaaApi@CopilotResult.json   # 识别
    <resource_root>/maa-layers/custom/resource/tasks/MaaApi@CopilotExit.json     # 退出结算

内核的增量资源加载会读这一层 ``resource/tasks/`` 下的每个 ``*.json``（M29 实施期用
本机内核实测：新增该文件 + ``AsstLoadResource`` 之后
``AsstAppendTask("Custom", {"task_names": ["MaaApi@Probe@Task"]})`` 返回非 0，删掉名字
返回 0），所以「独立文件」这条路是通的。文件名与任务名同前缀：

- ``MaaApi@CopilotResult@Start``：入口，``algorithm=JustReturn``，``next`` 按顺序列出
  全部识别项；内核 ``ProcessTask`` 取**第一个匹配上**的项，因此一次识别只会命中一项。
- ``MaaApi@CopilotResult@<Case>``：单项识别，全部 ``action=DoNothing``（只读、不点屏幕）：

  ====================  ==========================================================
  Case                 判据（照内核 ``tasks.json`` 的 roi 与文本）
  ====================  ==========================================================
  ``ThreeStar``        模板 ``StageDrops-Stars-3.png``，roi ``[50,270,250,100]``
  ``Adverse``          模板 ``StageDrops-Stars-Adverse.png``，roi ``[70,285,90,95]``
  ``TwoStar``          模板 ``StageDrops-Stars-2.png``，roi ``[50,270,250,100]``
  ``EndOfAction``      模板 ``EndOfAction.png``（结算界面出现但星级不明 → ``clear``）
  ``Failed``           OCR「行动失败」「任务失败」，roi 照内核 ``FightMissionFailed``
  ``SanityInsufficient`` OCR「理智不足」「使用理智药」（T1 探针口径的临时实现）
  ``StartButton``      OCR「开始行动」「开始作战」「开始推演」，roi 照 `StartButton1`
  ``Unknown``          ``JustReturn`` 兜底：什么都没匹配上就是 ``unknown``
  ====================  ==========================================================

- ``MaaApi@CopilotResult@<Case>@Hit``：**命中标记**。单个识别项命中后跳到自己同名的
  ``@Hit`` 任务，它 ``maxTimes: 1`` 且 ``next: ["#self"]``：第一次执行成功，第二次执行
  触发内核的 ``ExceededLimit`` 回调（``SubTaskExtraInfo``，带任务名），随后
  ``Runout`` → ``exceededNext`` 为空 → 整条链正常结束。这样「命中了哪一项」会**落进
  ``log_entry``**（``content`` 是「已达到次数上限：… 1/1」），不依赖进程内订阅内核回调，
  重启、换进程、事后排障都读得到；同时进程内如果挂了 live tap，也能更早拿到同样的信号。

退出结算任务（``copilot_exit_tasks()``，同样是只读/复位动作）::

    MaaApi@CopilotExit@Start     → 依次尝试下面两条
    MaaApi@CopilotExit@BackToStart   baseTask Copilot@ClickCornerUntilStartButton
                                     （内核自带的「三星之后回开始行动」链）
    MaaApi@CopilotExit@ClosePopup    OCR「取消」「确定」点掉弹窗（理智不足那条路）

二、判定（``judge``）
---------------------

``judge(callbacks)`` 是纯函数：把回调序列（内核原始信封、``log_entry.raw``、
``LogEntry`` 对象、或 task 名字符串）判成 outcome。顺序是：

1. **识别标记优先**：序列里第一个 ``MaaApi@CopilotResult@<Case>``（或它的 ``@Hit``）
   决定 outcome；顺带认内核 copilot 自带的结算链（``Copilot@StageDrops-Stars-3`` →
   ``three_star``、``Copilot@EndOfAction`` → ``clear``、``FightMissionFailed`` → ``failed``）。
2. **编队信号**：``BattleFormationOperUnavailable`` 或 ``SubTaskError(why=OperatorMissing)``
   → 允许助战时是 ``support_unavailable``（需要补位但没借到，Q42），不允许助战时是
   ``not_started``（干员缺失、根本没开打）。
3. 其它情况 → ``unknown``（附结算截图，由模型用 ``record_copilot_result`` 判定）。

``error`` / ``interrupted`` **不由本模块产出**：它们是流水线出错/崩溃与用户中断的结论，
由 :class:`~maa_api.services.copilot.attempt.CopilotAttemptService` 按流水线终态给出。

三、采集（``recognize``）
------------------------

``recognize()`` 负责：确保任务文件在 → 提交一条 ``Custom`` 识别流水线 → 等它结束 →
收集回调（进程内 live tap + ``log_entry`` 回读，后者要等 LogHub 刷盘，所以有一个
有界的重试窗口）→ ``judge``。``ensure_tasks()`` 通过 ``resource_service`` 的公开属性
``resource_root`` 与 ``reload_resources`` 落盘并重载；没有资源服务时抛
``SERVICE_UNAVAILABLE``（宁可报错，也不把文件写到猜出来的路径上）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from maa_api.core.enums import Message
from maa_api.db.repositories.log import LogRepository
from maa_api.domain.enums import PipelineSource
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.domain.task import PipelineCreate

__all__ = [
    "CASE_OUTCOMES",
    "DEFAULT_RECOGNITION_TIMEOUT",
    "EXIT_TASK_ENTRY",
    "EXIT_TASK_FILE",
    "EXIT_TASK_PREFIX",
    "HIT_SUFFIX",
    "JUDGEABLE_OUTCOMES",
    "KERNEL_RESULT_SIGNALS",
    "RESULT_TASK_ENTRY",
    "RESULT_TASK_FILE",
    "RESULT_TASK_PREFIX",
    "CallbackSignal",
    "CallbackTap",
    "CopilotResultService",
    "callback_signal",
    "copilot_exit_tasks",
    "copilot_result_tasks",
    "exit_task_path",
    "judge_callbacks",
    "result_task_path",
    "submit_pipeline",
    "wait_pipeline",
]

logger = logging.getLogger(__name__)

#: 识别任务的命名前缀与入口（规格 §9.7 第 2 步点名 ``MaaApi@CopilotResult…``）。
RESULT_TASK_PREFIX = "MaaApi@CopilotResult"
RESULT_TASK_ENTRY = f"{RESULT_TASK_PREFIX}@Start"
RESULT_TASK_FILE = "MaaApi@CopilotResult.json"

#: 退出结算任务的命名前缀与入口（规格 §9.7 第 3 步）。
EXIT_TASK_PREFIX = "MaaApi@CopilotExit"
EXIT_TASK_ENTRY = f"{EXIT_TASK_PREFIX}@Start"
EXIT_TASK_FILE = "MaaApi@CopilotExit.json"

#: 命中标记的后缀：真正的「我命中了」信号由它发出（见模块 docstring）。
HIT_SUFFIX = "@Hit"

#: custom 层里放任务定义的相对路径（与 ``ResourceService._custom_task_path`` 同一层）。
TASKS_SUBDIR = ("maa-layers", "custom", "resource", "tasks")

#: 识别项（任务名后缀）→ outcome。顺序即内核的匹配顺序（第一个匹配上的生效）。
CASE_OUTCOMES: dict[str, str] = {
    f"{RESULT_TASK_PREFIX}@ThreeStar": "three_star",
    f"{RESULT_TASK_PREFIX}@Adverse": "three_star",
    f"{RESULT_TASK_PREFIX}@TwoStar": "two_star",
    f"{RESULT_TASK_PREFIX}@EndOfAction": "clear",
    f"{RESULT_TASK_PREFIX}@Failed": "failed",
    f"{RESULT_TASK_PREFIX}@SanityInsufficient": "sanity_insufficient",
    f"{RESULT_TASK_PREFIX}@StartButton": "not_started",
    f"{RESULT_TASK_PREFIX}@Unknown": "unknown",
}

#: 内核 copilot 自带的结算链任务名 → outcome（第二信号源，见模块 docstring）。
KERNEL_RESULT_SIGNALS: dict[str, str] = {
    "Copilot@StageDrops-Stars-3": "three_star",
    "Copilot@StageDrops-Stars-Adverse": "three_star",
    "Copilot@StageDrops-Stars-2": "two_star",
    "Copilot@EndOfAction": "clear",
    "Copilot@EndOfAction-Sandbox": "clear",
    "Copilot@FightMissionFailed": "failed",
    "FightMissionFailed": "failed",
    "FightMissionFailedAndStop": "failed",
    "UnsupportedLevel": "not_started",
}

#: :meth:`CopilotResultService.judge` 能给出的全部取值（``error`` / ``interrupted`` 不在此列）。
JUDGEABLE_OUTCOMES: tuple[str, ...] = (
    "three_star",
    "two_star",
    "clear",
    "failed",
    "support_unavailable",
    "sanity_insufficient",
    "not_started",
    "unknown",
)

#: ``recognize()`` 等识别流水线的默认超时（秒）：识别只是看一眼画面。
DEFAULT_RECOGNITION_TIMEOUT = 120.0

#: LogHub 攒批刷盘的等待上限（秒）：等「识别标记已经落库」。
DEFAULT_FLUSH_WAIT = 1.5

#: 内核 ``SubTaskError`` 里表示「干员缺失」的 ``why``（``BattleFormationTask``）。
_OPERATOR_MISSING = "OperatorMissing"

#: 允许助战时的编队信号 → ``support_unavailable``（需要补位但没借到，Q42）。
_SUPPORT_SIGNAL_WHATS = frozenset({"BattleFormationOperUnavailable"})


def _case_task(case: str, body: Mapping[str, Any], doc: str) -> dict[str, Any]:
    """一个识别项：只读、命中后跳到自己的 ``@Hit`` 标记。"""
    name = f"{RESULT_TASK_PREFIX}@{case}"
    return {
        "Doc": doc,
        "action": "DoNothing",
        "maxTimes": 1,
        **dict(body),
        "next": [f"{name}{HIT_SUFFIX}"],
    }


def _hit_marker(case: str) -> dict[str, Any]:
    """命中标记：``maxTimes: 1`` + ``next: ["#self"]`` → 第二次执行发 ``ExceededLimit``。"""
    return {
        "Doc": f"{RESULT_TASK_PREFIX}@{case} 命中的持久标记（ExceededLimit 回调）",
        "algorithm": "JustReturn",
        "action": "DoNothing",
        "maxTimes": 1,
        "next": ["#self"],
    }


def copilot_result_tasks() -> dict[str, dict[str, Any]]:
    """``MaaApi@CopilotResult…`` 系列只读识别任务（写进 ``RESULT_TASK_FILE``）。

    返回的对象直接就是一个 ``tasks.json`` 片段：键是任务名，值是任务定义。全部
    ``action=DoNothing``（只识别、不点屏幕），判据与 roi 见模块 docstring 的表。
    """
    cases: tuple[tuple[str, dict[str, Any], str], ...] = (
        (
            "ThreeStar",
            {"template": "StageDrops-Stars-3.png", "roi": [50, 270, 250, 100], "templThreshold": 0.8},
            "三星结算（StageDrops-Stars-3 模板，roi 照内核）",
        ),
        (
            "Adverse",
            {"template": "StageDrops-Stars-Adverse.png", "roi": [70, 285, 90, 95], "templThreshold": 0.8},
            "突袭（Adverse）三星结算（roi 照内核）",
        ),
        (
            "TwoStar",
            {"template": "StageDrops-Stars-2.png", "roi": [50, 270, 250, 100], "templThreshold": 0.8},
            "二星结算（「通关即可」算成功）",
        ),
        (
            "EndOfAction",
            {"template": "EndOfAction.png", "roi": [0, 290, 70, 60], "templThreshold": 0.8},
            "结算界面出现但星级不明（内核没有一星模板）",
        ),
        (
            "Failed",
            {
                "algorithm": "OcrDetect",
                "text": ["行动失败", "任务失败"],
                "roi": [36, 226, 482, 269],
            },
            "行动失败文字（roi 照内核 FightMissionFailed）",
        ),
        (
            "SanityInsufficient",
            {
                "algorithm": "OcrDetect",
                "text": ["理智不足", "使用理智药"],
                "roi": [340, 250, 600, 220],
            },
            "理智不足弹窗（T1 探针口径的临时实现，待真机核对 roi）",
        ),
        (
            "StartButton",
            {
                "algorithm": "OcrDetect",
                "text": ["开始行动", "开始作战", "开始推演"],
                "roi": [1010, 625, 260, 61],
            },
            "还在「开始行动」界面：没开打（编队失败、干员缺失等）",
        ),
        (
            "Unknown",
            {"algorithm": "JustReturn"},
            "兜底：什么都没匹配上 → unknown（模型用 record_copilot_result 判定）",
        ),
    )
    tasks: dict[str, dict[str, Any]] = {
        RESULT_TASK_ENTRY: {
            "Doc": "作业结果识别入口：按顺序取第一个匹配上的识别项",
            "algorithm": "JustReturn",
            "action": "DoNothing",
            "maxTimes": 1,
            "next": [f"{RESULT_TASK_PREFIX}@{case}" for case, _body, _doc in cases],
        }
    }
    for case, body, doc in cases:
        tasks[f"{RESULT_TASK_PREFIX}@{case}"] = _case_task(case, body, doc)
        tasks[f"{RESULT_TASK_PREFIX}@{case}{HIT_SUFFIX}"] = _hit_marker(case)
    return tasks


def copilot_exit_tasks() -> dict[str, dict[str, Any]]:
    """退出结算任务（规格 §9.7 第 3 步）：回到「开始行动」界面，为下一次尝试做准备。"""
    return {
        EXIT_TASK_ENTRY: {
            "Doc": "退出结算：先按内核的三星退出链走，失败时再尝试关掉弹窗",
            "algorithm": "JustReturn",
            "action": "DoNothing",
            "maxTimes": 1,
            "next": [
                f"{EXIT_TASK_PREFIX}@BackToStart",
                f"{EXIT_TASK_PREFIX}@ClosePopup",
            ],
        },
        f"{EXIT_TASK_PREFIX}@BackToStart": {
            "Doc": "内核自带的「结算后回开始行动」链（点角落直到出现开始行动界面）",
            "baseTask": "Copilot@ClickCornerUntilStartButton",
        },
        f"{EXIT_TASK_PREFIX}@ClosePopup": {
            "Doc": "理智不足/吃药弹窗：点掉「取消」类按钮（待真机核对文案与 roi）",
            "algorithm": "OcrDetect",
            "text": ["取消", "确定"],
            "roi": [640, 380, 640, 340],
            "action": "ClickSelf",
            "maxTimes": 3,
            "next": [f"{EXIT_TASK_PREFIX}@BackToStart"],
        },
    }


def result_task_path(root: str | Path) -> Path:
    """识别任务文件的绝对路径（独立文件，不与聚合的 ``tasks.json`` 同名）。"""
    return Path(root).joinpath(*TASKS_SUBDIR, RESULT_TASK_FILE)


def exit_task_path(root: str | Path) -> Path:
    """退出结算任务文件的绝对路径。"""
    return Path(root).joinpath(*TASKS_SUBDIR, EXIT_TASK_FILE)


# ---------------------------------------------------------------------------
# 回调信号
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class CallbackSignal:
    """一条回调里与判定有关的部分（消息名 / ``what`` / 任务名 / 细节）。"""

    message: str | None
    what: str | None
    task: str | None
    details: Mapping[str, Any] = field(default_factory=dict)

    @property
    def marker(self) -> str | None:
        """识别标记对应的 outcome；不是识别标记（含 ``@Hit``）时返回 ``None``。"""
        task = self.task or ""
        if task.endswith(HIT_SUFFIX):
            task = task[: -len(HIT_SUFFIX)]
        if task.startswith(f"{RESULT_TASK_PREFIX}@"):
            return CASE_OUTCOMES.get(task)
        return None

    @property
    def kernel_signal(self) -> str | None:
        """内核 copilot 结算链上的任务名 → outcome。"""
        return KERNEL_RESULT_SIGNALS.get(self.task or "")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def callback_signal(item: Any) -> CallbackSignal:
    """把一条回调（信封 / ``log_entry.raw`` / ``LogEntry`` / 字符串）解析成信号。

    内核回调的载荷层级见 ``CallbackTranslator._flatten``：任务名在
    ``details.details.task``，插件回调只在信封上有 ``subtask``；``what`` 在外层
    ``details.what``（``log_entry.raw`` 把它复制到了顶层）。三种层级都认。
    """
    if isinstance(item, str):
        return CallbackSignal(message=None, what=None, task=item.strip() or None)
    raw: Any = item
    if not isinstance(item, Mapping):
        # ``LogEntry`` 把回调信封放在 ``meta`` 列（``LogHub._to_entry``），
        # 而 ``LogRecord`` / 测试替身用 ``raw`` 这个属性名；两种都认。
        raw = getattr(item, "raw", None)
        if not isinstance(raw, Mapping):
            raw = getattr(item, "meta", None)
    if not isinstance(raw, Mapping):
        return CallbackSignal(message=None, what=None, task=None)

    message: str | None = None
    msg = raw.get("msg")
    if msg is not None:
        try:
            message = Message(int(msg)).name
        except (TypeError, ValueError):
            message = str(msg)
    elif raw.get("message") is not None:
        message = str(raw.get("message"))

    outer = _mapping(raw.get("details"))
    inner = _mapping(outer.get("details"))
    details = {**outer, **inner}
    what = details.get("what") or raw.get("what") or None
    task = (
        inner.get("task")
        or outer.get("task")
        or raw.get("task")
        or outer.get("subtask")
        or raw.get("subtask")
    )
    return CallbackSignal(
        message=message,
        what=str(what) if what else None,
        task=str(task) if task else None,
        details=details,
    )


def _iter_callbacks(callbacks: Any) -> list[Any]:
    if callbacks is None:
        return []
    if isinstance(callbacks, (str, Mapping)) or hasattr(callbacks, "raw") or hasattr(
        callbacks, "meta"
    ):
        return [callbacks]
    if isinstance(callbacks, Iterable):
        return list(callbacks)
    return [callbacks]


def judge_callbacks(callbacks: Any, *, support: str = "fill_one") -> str:
    """按规格 §9.7 的表把回调序列判成 outcome（纯函数，``judge`` 的实现）。

    ``support`` 是这次尝试是否允许助战（``fill_one`` / ``none``）：同一条编队信号在
    允许助战时是 ``support_unavailable``（可继续），不允许时是 ``not_started``（停下问用户）。
    """
    signals = [callback_signal(item) for item in _iter_callbacks(callbacks)]

    # 1) 识别标记 / 内核结算链：序列里第一个出现的生效（内核一次识别只命中一项）。
    for signal in signals:
        outcome = signal.marker or signal.kernel_signal
        if outcome:
            return outcome

    # 2) 编队信号：没开打的两种情况靠它区分。
    for signal in signals:
        if signal.what in _SUPPORT_SIGNAL_WHATS or (
            signal.message == Message.SubTaskError.name
            and str(signal.details.get("why") or "") == _OPERATOR_MISSING
        ):
            return "support_unavailable" if support == "fill_one" else "not_started"

    # 3) 什么都没认出来：附结算截图，由模型判定。
    return "unknown"


# ---------------------------------------------------------------------------
# 回调采集
# ---------------------------------------------------------------------------
class CallbackTap:
    """订阅 ``CoreClient`` 的 ``CALLBACK`` 事件，把原始载荷攒起来。

    内核的 ``CoreClient.on`` 没有对应的 ``off``（处理器只在实例销毁时消失），所以退出
    ``with`` 之后处理器还在，但 ``active`` 已置假、不再收集——对齐「订阅是幂等无害的」
    这一既有事实，也避免多次尝试把回调越攒越多。
    """

    def __init__(self) -> None:
        self.callbacks: list[dict[str, Any]] = []
        self.active = False

    def __enter__(self) -> "CallbackTap":
        self.active = True
        return self

    def __exit__(self, *exc: Any) -> bool:
        self.active = False
        return False

    def handle(self, payload: Any) -> None:
        if not self.active:
            return
        if isinstance(payload, Mapping):
            self.callbacks.append(dict(payload))


class CopilotResultService:
    """结果识别：任务文件、回调采集与判定（规格 §9.7）。"""

    def __init__(
        self,
        session_factory: Any = None,
        *,
        resource_service: Any = None,
        client_provider: Any = None,
        flush_wait_seconds: float = DEFAULT_FLUSH_WAIT,
    ) -> None:
        self._session_factory = session_factory
        self._resource_service = resource_service
        self._client_provider = client_provider
        self._flush_wait_seconds = max(float(flush_wait_seconds), 0.0)
        #: 本进程是否已经确认过识别任务文件（见 ``ensure_tasks`` 的缓存口径）。
        self._ensured = False
        #: 由 ``feed`` 塞进来的实时回调（LogHub / 外部接线用；tap 走自己的缓冲）。
        self._fed: list[Any] = []

    # -- 任务文件 ---------------------------------------------------------
    def tasks(self) -> dict[str, dict[str, Any]]:
        return copilot_result_tasks()

    def exit_tasks(self) -> dict[str, dict[str, Any]]:
        return copilot_exit_tasks()

    async def ensure_tasks(
        self, *, resource_service: Any = None, force: bool = False
    ) -> dict[str, Any]:
        """把识别与退出任务写进 custom 层的独立文件，并触发一次资源重载。

        幂等：内容没变不重写（``written=False``）。进程内还缓存「已经确认过」这一次：
        第二次调用只要两个文件都还在就立刻返回（``cached=True``），不再重载内核资源
        —— 每次尝试都 ``AsstLoadResource`` 一遍模板是没必要的开销。文件被清理掉、
        或调用方显式 ``force=True`` 时重新写并重载。

        返回 ``{"written", "reloaded", "cached", "path", "exit_path", "tasks"}``。
        """
        service = resource_service or self._resource_service
        root = getattr(service, "resource_root", None)
        if service is None or root is None:
            raise AppError(
                ErrorCode.SERVICE_UNAVAILABLE,
                "结果识别任务尚未装配（缺少 resource_service）",
            )
        paths = (result_task_path(root), exit_task_path(root))
        if self._ensured and not force and all(path.is_file() for path in paths):
            return {
                "written": False,
                "reloaded": False,
                "cached": True,
                "path": str(paths[0]),
                "exit_path": str(paths[1]),
                "tasks": sorted(copilot_result_tasks()),
            }
        written = False
        for path, payload in (
            (paths[0], copilot_result_tasks()),
            (paths[1], copilot_exit_tasks()),
        ):
            written = _atomic_json_write(path, payload) or written
        reloaded = await _reload(service)
        self._ensured = reloaded
        if not reloaded:
            logger.warning(
                "结果识别任务已写入 %s，但没有可用的资源重载回调；"
                "内核重启前不会看到这些任务",
                result_task_path(root),
            )
        return {
            "written": written,
            "reloaded": reloaded,
            "cached": False,
            "path": str(paths[0]),
            "exit_path": str(paths[1]),
            "tasks": sorted(copilot_result_tasks()),
        }

    @property
    def resource_service(self) -> Any:
        """资源服务：构造时没给就是 ``None``（调用方可以后置 ``bind_resource_service``）。"""
        return self._resource_service

    def bind_resource_service(self, service: Any) -> None:
        """后置绑定资源服务（与 ``PipelineRunner.bind_notify_service`` 同一惯例）。

        ``main.py`` 的装配顺序里资源服务先于作业服务建好，但服务是分开构造的；工具层
        拿到 ``app.state.resource_service`` 之后补一次绑定，识别任务文件才写得出去。
        已经绑过就不覆盖（显式注入优先）。
        """
        if self._resource_service is None:
            self._resource_service = service

    @property
    def session_factory(self) -> Any:
        """会话工厂：构造时没给就用进程默认的（同 ``default_upgrade_plan_service`` 口径）。"""
        if self._session_factory is None:
            from maa_api.db import session as db_session

            self._session_factory = db_session.session_factory
        return self._session_factory

    # -- 判定 -------------------------------------------------------------
    def judge(self, callbacks: Any = None, *, support: str = "fill_one") -> str:
        """回调序列（缺省用 ``feed`` 攒下来的）→ outcome；不可判时 ``unknown``。"""
        items = list(self._fed) if callbacks is None else list(_iter_callbacks(callbacks))
        return judge_callbacks(items, support=support)

    def feed(self, payload: Any) -> None:
        """外部接线投递一条回调（LogHub 刷盘路径或测试用）；判完可 ``clear``。"""
        self._fed.append(payload)

    def clear(self) -> None:
        self._fed.clear()

    def tap(self, client: Any) -> CallbackTap:
        """订阅一个 ``CoreClient``（或同形替身）的 ``CALLBACK`` 事件。"""
        tap = CallbackTap()
        client.on("CALLBACK", tap.handle)
        return tap

    # -- 采集 -------------------------------------------------------------
    async def callbacks_for(
        self, pipeline_id: str, *, session: Any = None
    ) -> list[dict[str, Any]]:
        """回读一条流水线的日志，取出其中的内核回调信封（``log_entry.raw``）。"""
        if not pipeline_id:
            return []
        async def _read(scope: Any) -> list[dict[str, Any]]:
            page = await LogRepository(scope).query(
                pipeline_id=pipeline_id, page=1, size=500, order="asc"
            )
            return [
                dict(envelope)
                for entry in page.items
                for envelope in (_entry_envelope(entry),)
                if envelope is not None
            ]

        if session is not None:
            return await _read(session)
        async with self.session_factory() as own_session:
            return await _read(own_session)

    async def recognize(
        self,
        *,
        pipeline_service: Any,
        session_id: str | None = None,
        support: str = "fill_one",
        timeout_seconds: float = DEFAULT_RECOGNITION_TIMEOUT,
        extra_callbacks: Sequence[Any] = (),
        client: Any = None,
    ) -> dict[str, Any]:
        """跑一次结果识别并判定（规格 §9.7 第 2 步）。

        返回 ``{"outcome", "judged_by", "pipeline_id", "finished", "callbacks"}``：
        ``judged_by`` 在判定成功时是 ``"core"``，``unknown`` 时是 ``None``
        （等模型 ``record_copilot_result`` 记成 ``model``）。
        """
        await self.ensure_tasks()
        live_client = client
        if live_client is None and callable(self._client_provider):
            try:
                live_client = self._client_provider()
            except Exception:  # noqa: BLE001 - 拿不到内核连接不影响日志回读
                live_client = None
        tap = self.tap(live_client) if live_client is not None else None
        pipeline_id: str | None = None
        finished = False
        try:
            pipeline_id = await submit_pipeline(
                pipeline_service,
                tasks=[{"name": "Custom", "task_names": [RESULT_TASK_ENTRY]}],
                title="作业结果识别",
                session_id=session_id,
            )
            if pipeline_id is not None:
                _pipeline, finished = await wait_pipeline(
                    pipeline_service, pipeline_id, timeout_seconds=timeout_seconds
                )
            callbacks = await self._collect(
                pipeline_id,
                tap=tap,
                extra_callbacks=extra_callbacks,
                support=support,
            )
        finally:
            if tap is not None:
                tap.__exit__()
        outcome = judge_callbacks(callbacks, support=support)
        return {
            "outcome": outcome,
            "judged_by": "core" if outcome != "unknown" else None,
            "pipeline_id": pipeline_id,
            "finished": finished,
            "callbacks": callbacks,
        }

    async def _collect(
        self,
        pipeline_id: str | None,
        *,
        tap: CallbackTap | None,
        extra_callbacks: Sequence[Any],
        support: str,
    ) -> list[Any]:
        """live tap + 调用方给的回调 + ``log_entry`` 回读（等 LogHub 刷盘，有界重试）。"""
        collected: list[Any] = list(extra_callbacks)
        if tap is not None:
            collected.extend(tap.callbacks)
        collected.extend(self._fed)
        deadline = time.monotonic() + self._flush_wait_seconds
        while True:
            logged = await self.callbacks_for(pipeline_id) if pipeline_id else []
            merged = [*collected, *logged]
            if (
                judge_callbacks(merged, support=support) != "unknown"
                or time.monotonic() >= deadline
            ):
                return merged
            await asyncio.sleep(0.1)


# ---------------------------------------------------------------------------
# 流水线小工具（本里程碑的辅助流水线共用）
# ---------------------------------------------------------------------------
async def submit_pipeline(
    pipeline_service: Any,
    *,
    tasks: Sequence[Mapping[str, Any]],
    title: str,
    session_id: str | None = None,
    notify_on_finish: bool = False,
) -> str | None:
    """提交流水线，返回 id。

    兼容两种装配：对象自己就有 ``submit``（``QueueService`` 或测试替身），或它是
    ``PipelineRunner``（``submit`` 在它的 ``queue_service`` 上）。M29 的辅助流水线
    （作业尝试、结果识别、退出结算）都不发终态通知：这一轮的结论由
    ``run_copilot_attempt`` 自己汇报、由通知服务在授权终态时统一发。
    """
    submitter = getattr(pipeline_service, "submit", None)
    if not callable(submitter):
        submitter = getattr(getattr(pipeline_service, "queue_service", None), "submit", None)
    if not callable(submitter):
        raise AppError(
            ErrorCode.SERVICE_UNAVAILABLE,
            "流水线服务尚未装配（缺少 submit）",
        )
    payload = PipelineCreate.model_validate(
        {
            "tasks": [dict(task) for task in tasks],
            "title": title,
            "notify_on_finish": notify_on_finish,
        }
    )
    # 来源标 agent（与 ``submit_pipeline`` 工具同一口径）：队列优先级与执行历史里
    # 这是「Agent 提交的」，不是用户手点的。
    created = await submitter(
        payload, source=PipelineSource.AGENT, agent_session_id=session_id
    )
    pipeline = created[0] if isinstance(created, tuple) else created
    if isinstance(pipeline, Mapping):
        return str(pipeline.get("id")) if pipeline.get("id") else None
    identifier = getattr(pipeline, "id", None)
    return str(identifier) if identifier else None


async def wait_pipeline(
    pipeline_service: Any, pipeline_id: str, *, timeout_seconds: float
) -> tuple[Any, bool]:
    """等流水线进终态，返回 ``(pipeline, finished)``。

    ``PipelineRunner.wait_terminal`` 是生产实现；测试替身可以直接给
    ``(pipeline, True)`` 或一个 ``pipeline`` 对象。
    """
    waiter = getattr(pipeline_service, "wait_terminal", None)
    if not callable(waiter):
        raise AppError(
            ErrorCode.SERVICE_UNAVAILABLE,
            "流水线服务尚未装配（缺少 wait_terminal）",
        )
    result = await waiter(pipeline_id, max(float(timeout_seconds), 0.0))
    if isinstance(result, tuple) and len(result) == 2:
        pipeline, finished = result
        return pipeline, bool(finished)
    return result, True


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------
def _entry_envelope(entry: Any) -> Mapping[str, Any] | None:
    """一条 ``log_entry`` 里的回调信封：``meta`` 列（生产）或 ``raw``（替身）。"""
    for attribute in ("meta", "raw"):
        value = getattr(entry, attribute, None)
        if isinstance(value, Mapping):
            return value
    return None


def _atomic_json_write(path: Path, payload: Mapping[str, Any]) -> bool:
    """整文件原子写入；内容与现有文件逐字节相同则不写，返回是否真的写了。"""
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.is_file() and path.read_text(encoding="utf-8") == encoded:
            return False
    except OSError:  # pragma: no cover - 读不动就照写
        pass
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(encoded, encoding="utf-8")
    os.replace(temporary, path)
    return True


async def _reload(service: Any) -> bool:
    """触发一次内核资源重载：用 ``ResourceService`` 的公开 ``reload_resources`` 回调。"""
    hook = getattr(service, "reload_resources", None)
    if not callable(hook):
        return False
    result = hook()
    if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
        await result
    return True

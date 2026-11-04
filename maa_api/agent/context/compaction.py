"""摘要压缩与检查点（M74 规格 §4.2.5、§4.5.2–§4.5.4，v3 06 §4）。

压缩把检查点覆盖范围（``seq ≤ upto_seq``）里的消息换成一条确定性渲染的摘要消息：
摘要部分由 ``summarizer`` 后台代理产出（失败时退回 :func:`mechanical_summary`），
引用 ID 不依赖模型（:func:`complete_refs`），压缩前的工单、委托与待处理事项由
:mod:`maa_api.agent.context.durable` 直接读出。检查点写库与旧检查点的置旧在同一个
事务里完成，读侧（:func:`maa_api.agent.context.layout.load_history`）只认 active。

本模块的纯函数（选边界、渲染、机械摘要、补引用）不读写数据库，便于单测；只有
:class:`Compactor` 与 :class:`CompactionMiddleware` 碰运行状态。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from maa_api.agent.context.clearing import (
    apply_clearing,
    clearing_placeholder,
    plan_clearing,
    tool_rounds,
)
from maa_api.agent.context.durable import (
    DurableState,
    collect_durable_state,
    render_durable,
)
from maa_api.agent.context.layout import (
    ContextLayout,
    ContextParts,
    load_history,
    parts_of,
)
from maa_api.agent.context.tokens import (
    TokenEstimator,
    budget_for,
    estimate_text,
)
from maa_api.agent.harness.context import ModelRequest, RunContext
from maa_api.agent.harness.middleware import Middleware
from maa_api.agent.llm.schemas.compaction_summary import (
    Action,
    CompactionSummary,
    Ref,
    UserRequest,
)
from maa_api.agent.llm.structured import complete_structured
from maa_api.agent.specs.registry import builtin_spec
from maa_api.agent.specs.schema import LEGACY_SPEC_NAMES, AgentSpec
from maa_api.db.models import AgentMessage, CompactionCheckpoint
from maa_api.db.repositories.agent import (
    AgentMessageRepository,
    AgentRunRepository,
    AgentSessionRepository,
)
from maa_api.db.repositories.compaction import CompactionCheckpointRepository
from maa_api.domain.enums import AgentRunKind, AgentRunStatus
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import get_settings

if TYPE_CHECKING:  # pragma: no cover - 只用于类型标注
    from maa_api.agent.llm.profiles import ResolvedProfile

__all__ = [
    "MIN_COMPACT_MESSAGES",
    "MIN_KEEP_TOOL_ROUNDS",
    "PROTECTED_MAX_RATIO",
    "RETRY_GROWTH_RATIO",
    "SUMMARIZER_INPUT_RATIO",
    "TRANSCRIPT_TEXT_MAX",
    "TRANSCRIPT_TOOL_HEAD",
    "TRANSCRIPT_TOOL_TAIL",
    "CompactionMiddleware",
    "CompactionResult",
    "Compactor",
    "complete_refs",
    "mechanical_summary",
    "render_checkpoint_message",
    "render_transcript",
    "select_upto_seq",
]

logger = logging.getLogger(__name__)

#: 长单用户轮次时受保护部分的估算上限（窗口的比例），超过就缩小保护范围。
PROTECTED_MAX_RATIO = 0.5
#: 长单轮保护缩小时保留的最近工具往返数。
MIN_KEEP_TOOL_ROUNDS = 4
#: 压缩边界至少要在第几条消息之后：太短的历史不压缩。
MIN_COMPACT_MESSAGES = 2
#: 摘要输入里工具结果保留的头尾字符数。
TRANSCRIPT_TOOL_HEAD, TRANSCRIPT_TOOL_TAIL = 1500, 500
#: 摘要输入里用户 / 助手正文的截断长度。
TRANSCRIPT_TEXT_MAX = 2000
#: 单次摘要输入最多占摘要档案窗口的比例。
SUMMARIZER_INPUT_RATIO = 0.5
#: 自动压缩失败后，估算再增长窗口的这个比例之前不重试。
RETRY_GROWTH_RATIO = 0.1

#: 机械摘要的固定正文（06 §4.5，逐字）。
MECHANICAL_SUMMARY_TEXT = "较早的消息已按规则压缩，细节可以按 ID 重新查询"
#: 机械摘要的列表上限与用户要求正文长度。
MECHANICAL_USER_REQUESTS = 20
MECHANICAL_USER_TEXT = 200
#: 机械摘要的操作上限。
MECHANICAL_ACTIONS = 50
#: 补引用时最多保留的条数，超出时从最前面删。
REFS_LIMIT = 100
#: 摘要 JSON 里由系统追加、模型不生成的四个键。
SYSTEM_SUMMARY_KEYS = (
    "schema_version",
    "upto_seq",
    "previous_checkpoint_id",
    "degraded",
)

#: 摘要输入三节的固定小标题（06 §4.5）。
_PREVIOUS_HEADER = "【已有摘要】"
_MESSAGES_HEADER = "【需要并入的消息】"
_DURABLE_HEADER = "【确定性状态】"

#: 机械摘要没有显式 registry 时使用的工具目录（进程内只建一次）。
_default_registry: Any = None


@dataclass(frozen=True, slots=True)
class CompactionResult:
    """一次压缩的结果；``skipped`` 为真时其余字段只有 token 估算有意义。"""

    checkpoint_id: str | None
    skipped: bool
    tokens_before: int
    tokens_after: int
    upto_seq: int | None
    degraded: bool


# --------------------------------------------------------------------------- #
# 消息读取的小工具（同时接受 ORM 行与 dict，纯函数用例可以直接用 dict）
# --------------------------------------------------------------------------- #
def _field(message: Any, key: str, default: Any = None) -> Any:
    if isinstance(message, Mapping):
        return message.get(key, default)
    return getattr(message, key, default)


def _role(message: Any) -> str:
    return str(_field(message, "role") or "")


def _seq(message: Any) -> int | None:
    value = _field(message, "seq")
    return None if value is None else int(value)


def _message_dict(message: Any) -> dict[str, Any]:
    """ORM 行 → 与 ``prepare_history_messages`` 相同的字段形状。"""
    if isinstance(message, Mapping):
        return dict(message)
    return {
        "role": str(_field(message, "role") or ""),
        "content": _field(message, "content"),
        "reasoning_content": _field(message, "reasoning_content"),
        "tool_calls": _field(message, "tool_calls"),
        "tool_call_id": _field(message, "tool_call_id"),
        "tool_name": _field(message, "tool_name"),
    }


def _content_text(content: Any) -> str:
    """参与渲染与估算的正文：字符串原样；数组取 text 部分之和；其余 JSON。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        parts: list[str] = []
        for part in content:
            if not isinstance(part, Mapping):
                parts.append(str(part))
            elif "text" in part:
                parts.append(str(part.get("text") or ""))
            else:
                parts.append(json.dumps(part, ensure_ascii=False, default=str))
        return "".join(parts)
    return json.dumps(content, ensure_ascii=False, default=str)


def _parse_json(content: Any) -> Any:
    text = _content_text(content)
    if not text:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def _payload_field(value: Any, key: str) -> Any:
    """从结果的顶层或它的 ``result`` 字段取一个键（与清理同一口径）。"""
    if not isinstance(value, Mapping):
        return None
    if value.get(key):
        return value[key]
    result = value.get("result")
    if isinstance(result, Mapping) and result.get(key):
        return result[key]
    return None


def _tool_call_records(message: Any) -> list[Mapping[str, Any]]:
    calls = _field(message, "tool_calls")
    if not isinstance(calls, (list, tuple)):
        return []
    return [call for call in calls if isinstance(call, Mapping)]


def _call_name(call: Mapping[str, Any]) -> str:
    function = call.get("function")
    if isinstance(function, Mapping) and function.get("name"):
        return str(function["name"])
    return str(call.get("name") or "")


def _call_arguments(call: Mapping[str, Any]) -> str:
    function = call.get("function")
    raw = function.get("arguments") if isinstance(function, Mapping) else None
    if raw is None:
        raw = call.get("arguments")
    if isinstance(raw, str):
        return raw
    if raw is None:
        return "{}"
    return json.dumps(raw, ensure_ascii=False, default=str)


def _tool_names(messages: Sequence[Any]) -> dict[str, str]:
    """``tool_call_id → 工具名``（assistant 的 tool_calls 里解析）。"""
    names: dict[str, str] = {}
    for message in messages:
        for call in _tool_call_records(message):
            call_id = call.get("id")
            name = _call_name(call)
            if call_id and name:
                names[str(call_id)] = name
    return names


def _tool_name(message: Any, names: Mapping[str, str]) -> str:
    explicit = _field(message, "tool_name")
    if explicit:
        return str(explicit)
    call_id = _field(message, "tool_call_id")
    return names.get(str(call_id), "") if call_id is not None else ""


# --------------------------------------------------------------------------- #
# 纯函数：选边界、渲染、机械摘要、补引用
# --------------------------------------------------------------------------- #
def _prepare(messages: Sequence[Any]) -> list[dict[str, Any]]:
    """落库消息 → 模型副本（只用于估算与工具往返切分，按视觉可用口径）。"""
    from maa_api.agent.harness.builtin_middleware import prepare_history_messages

    return prepare_history_messages(messages, vision_enabled=True)


def select_upto_seq(
    messages: Sequence[AgentMessage],
    *,
    keep_recent_turns: int,
    window: int,
    estimator: TokenEstimator,
) -> tuple[int | None, AgentMessage | None]:
    """选压缩边界与（可能的）要写进摘要的那条用户消息（06 §4.2）。

    默认保留最近 ``keep_recent_turns`` 个用户轮次。只有一个很长的用户轮次时，
    若受保护部分超过 ``PROTECTED_MAX_RATIO × window``，保护缩小为最近
    :data:`MIN_KEEP_TOOL_ROUNDS` 个完整工具往返，并把这一轮的用户原话交给调用方
    以原文写进 ``user_requests``。边界不拆开工具配对。
    """
    users = [
        index for index, message in enumerate(messages) if _role(message) == "user"
    ]
    start = users[-keep_recent_turns] if len(users) > keep_recent_turns else 0
    active: AgentMessage | None = None
    if start == 0:
        if not users:
            return None, None
        prepared = _prepare(messages)
        if estimator.raw_estimate(prepared) <= PROTECTED_MAX_RATIO * window:
            return None, None
        # 只数第一条用户消息之后的完整往返：窗口可能从工具配对中间开始。
        rounds = [
            round_start
            for round_start, _round_end in tool_rounds(prepared)
            if round_start > users[0]
        ]
        if len(rounds) <= MIN_KEEP_TOOL_ROUNDS:
            return None, None
        start = rounds[-MIN_KEEP_TOOL_ROUNDS]
        if users[-1] < start:
            active = messages[users[-1]]
    if start < MIN_COMPACT_MESSAGES:
        return None, None
    # ``start`` 处是 user 或带 tool_calls 的 assistant：边界不会拆开配对。
    return _seq(messages[start - 1]), active


def _truncate_tool_result(text: str) -> str:
    if len(text) <= TRANSCRIPT_TOOL_HEAD + TRANSCRIPT_TOOL_TAIL:
        return text
    omitted = len(text) - TRANSCRIPT_TOOL_HEAD - TRANSCRIPT_TOOL_TAIL
    return (
        f"{text[:TRANSCRIPT_TOOL_HEAD]}"
        f"[中间省略 {omitted} 字]"
        f"{text[-TRANSCRIPT_TOOL_TAIL:]}"
    )


def _render_lines(
    messages: Sequence[Any], *, cleared: Mapping[int, str]
) -> list[str]:
    """把每条消息渲染成一行（06 §4.5 的格式）。"""
    names = _tool_names(messages)
    lines: list[str] = []
    for message in messages:
        seq = _seq(message)
        role = _role(message)
        prefix = f"[seq {seq}] "
        if role == "user":
            text = _content_text(_field(message, "content"))[:TRANSCRIPT_TEXT_MAX]
            lines.append(f"{prefix}用户：{text}")
        elif role == "assistant":
            text = _content_text(_field(message, "content"))[:TRANSCRIPT_TEXT_MAX]
            calls: list[str] = []
            for call in _tool_call_records(message):
                name = _call_name(call)
                arguments = _call_arguments(call)[:300]
                calls.append(f"{name} {arguments}".strip())
            line = f"{prefix}助手：{text}"
            if calls:
                line += f"（调用 {'、'.join(calls)}）"
            lines.append(line)
        elif role == "tool":
            message_id = _field(message, "id")
            placeholder = (
                cleared.get(message_id)
                if isinstance(message_id, int) and message_id in cleared
                else None
            )
            if placeholder is not None:
                text = placeholder
            else:
                text = _truncate_tool_result(
                    _content_text(_field(message, "content"))
                )
            lines.append(f"{prefix}工具 {_tool_name(message, names)}：{text}")
        else:
            text = _content_text(_field(message, "content"))[:TRANSCRIPT_TEXT_MAX]
            lines.append(f"{prefix}{role}：{text}")
    return lines


def render_transcript(
    messages: Sequence[AgentMessage], *, cleared: Mapping[int, str]
) -> str:
    """摘要代理输入里的「需要并入的消息」：每条消息一行，不带思考内容。"""
    return "\n".join(_render_lines(messages, cleared=cleared))


def _default_refetchable() -> Callable[[str], bool]:
    """没有显式 registry 时按进程内工具目录判断 ``refetchable``。"""
    global _default_registry
    if _default_registry is None:
        from maa_api.agent.tools import build_registry

        _default_registry = build_registry()

    def is_refetchable(name: str) -> bool:
        try:
            return bool(_default_registry.get(name).refetchable)
        except Exception:  # noqa: BLE001 - 未知工具按不可重取处理
            return False

    return is_refetchable


def mechanical_summary(
    messages: Sequence[AgentMessage],
    *,
    is_refetchable: Callable[[str], bool] | None = None,
) -> CompactionSummary:
    """摘要模型不可用时的机械摘要（06 §4.5）。

    用户消息每条取前 200 字进 ``user_requests``（最近 20 条）；有副作用工具的结果
    进 ``actions``（最近 50 条）。其余字段留空，随后由 :func:`complete_refs` 补 ID。
    """
    refetchable = is_refetchable or _default_refetchable()
    names = _tool_names(messages)

    requests: list[UserRequest] = []
    actions: list[Action] = []

    for message in messages:
        if _role(message) == "user":
            text = _content_text(_field(message, "content"))[:MECHANICAL_USER_TEXT]
            if text:
                requests.append(
                    UserRequest(seq=_seq(message), text=text, status="pending")
                )
            continue
        if _role(message) != "tool":
            continue
        name = _tool_name(message, names)
        if name == "ask_user" or refetchable(name):
            continue
        content = _field(message, "content")
        value = _parse_json(content)
        errored = isinstance(value, Mapping) and "error" in value
        if errored:
            error = value.get("error")
            text = (
                error
                if isinstance(error, str)
                else json.dumps(error, ensure_ascii=False, default=str)
            )
        else:
            text = _content_text(content)
        text = (text or f"调用 {name}")[:MECHANICAL_USER_TEXT]
        actions.append(
            Action(
                tool=name or "tool",
                outcome="error" if errored else "ok",
                text=text,
            )
        )

    return CompactionSummary(
        summary=MECHANICAL_SUMMARY_TEXT,
        user_requests=requests[-MECHANICAL_USER_REQUESTS:],
        actions=actions[-MECHANICAL_ACTIONS:],
    )


def _ref_pairs(message: Any, name: str) -> list[tuple[str, str]]:
    """按 06 §4.3 的提取表从一条 tool 消息里取 ``(kind, id)``。"""
    value = _parse_json(_field(message, "content"))
    pairs: list[tuple[str, str]] = []
    if name == "submit_pipeline":
        pairs.append(("pipeline", _payload_field(value, "pipeline_id")))
    elif name == "create_ticket":
        pairs.append(("ticket", _payload_field(value, "ticket_id")))
    elif name == "create_schedule":
        schedule_id = _payload_field(value, "schedule_id") or _payload_field(
            value, "id"
        )
        pairs.append(("schedule", schedule_id))
    elif name == "get_screenshot":
        pairs.append(("screenshot", _payload_field(value, "screenshot_id")))
    elif name == "ask_user":
        pairs.append(("question", _payload_field(value, "question_id")))
    pairs.append(("confirmation", _payload_field(value, "confirmation_id")))
    pairs.append(("artifact", _payload_field(value, "artifact_id")))
    return [(kind, str(item)) for kind, item in pairs if item]


def complete_refs(
    summary: CompactionSummary, messages: Sequence[AgentMessage]
) -> CompactionSummary:
    """从被压缩的 tool 结果里确定性补全引用 ID（模型漏掉的不会丢）。"""
    refs = list(summary.refs)
    known = {(ref.kind, ref.id) for ref in refs}
    names = _tool_names(messages)
    for message in messages:
        if _role(message) != "tool":
            continue
        name = _tool_name(message, names)
        for kind, identifier in _ref_pairs(message, name):
            key = (kind, identifier)
            if key in known:
                continue
            if len(identifier) > 64:
                continue
            known.add(key)
            refs.append(Ref(kind=kind, id=identifier, label=name))  # type: ignore[arg-type]
    if len(refs) > REFS_LIMIT:
        refs = refs[-REFS_LIMIT:]
    return summary.model_copy(update={"refs": refs})


def _section(lines: list[str], title: str, items: list[str]) -> None:
    if not items:
        return
    lines.append(f"{title}：")
    lines.extend(items)


def _item_text(item: Mapping[str, Any]) -> str:
    return str(item.get("text") or "")


def render_checkpoint_message(
    summary_json: Mapping[str, Any], durable: DurableState, upto_seq: int
) -> str:
    """把摘要 JSON 与 durable 状态渲染成插进历史的摘要消息正文（06 §4.6）。"""
    lines = [
        f"【压缩摘要】以下是本会话较早消息（seq ≤ {upto_seq}）的摘要，"
        "由系统生成，不是用户本人说的话。",
        str(summary_json.get("summary") or ""),
    ]

    requests = [
        f"- {_item_text(item)}（{item.get('status', '')}）"
        for item in summary_json.get("user_requests") or ()
        if isinstance(item, Mapping) and _item_text(item)
    ]
    _section(lines, "用户要求", requests)

    decisions = [
        f"- {_item_text(item)}（{item.get('by', '')}）"
        for item in summary_json.get("decisions") or ()
        if isinstance(item, Mapping) and _item_text(item)
    ]
    _section(lines, "已定的事", decisions)

    facts = [
        f"- {_item_text(item)}（来源：{item.get('source', '')}）"
        for item in summary_json.get("facts") or ()
        if isinstance(item, Mapping) and _item_text(item)
    ]
    _section(lines, "事实", facts)

    actions: list[str] = []
    for item in summary_json.get("actions") or ():
        if not isinstance(item, Mapping) or not _item_text(item):
            continue
        line = f"- {item.get('tool', '')}：{_item_text(item)}（{item.get('outcome', '')}）"
        refs = [str(ref) for ref in item.get("refs") or () if ref]
        if refs:
            line += f"［{', '.join(refs)}］"
        actions.append(line)
    _section(lines, "操作", actions)

    open_items = [
        f"- [{item.get('kind', '')}] {_item_text(item)}"
        for item in summary_json.get("open_items") or ()
        if isinstance(item, Mapping) and _item_text(item)
    ]
    _section(lines, "未完成", open_items)

    errors = [
        f"- {item.get('code', '')}：{_item_text(item)}"
        f"（{'已解决' if item.get('resolved') else '未解决'}）"
        for item in summary_json.get("errors") or ()
        if isinstance(item, Mapping) and _item_text(item)
    ]
    _section(lines, "错误", errors)

    refs = [
        f"- {item.get('kind', '')} {item.get('id', '')}"
        + (f"：{item['label']}" if item.get("label") else "")
        for item in summary_json.get("refs") or ()
        if isinstance(item, Mapping) and item.get("id")
    ]
    _section(lines, "引用", refs)

    preferences = [
        f"- {text}" for text in summary_json.get("user_preferences") or () if text
    ]
    _section(lines, "偏好", preferences)

    next_step = str(summary_json.get("next_step") or "").strip()
    if next_step:
        lines.append(f"下一步：{next_step}")

    durable_text = render_durable(durable)
    lines.append(f"【压缩时的状态】截至 {durable.captured_at}：")
    lines.append(durable_text or "无")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Compactor
# --------------------------------------------------------------------------- #
def _session_factory_for_app(app_state: Any) -> Any:
    factory = getattr(app_state, "session_factory", None)
    if factory is None:
        factory = getattr(getattr(app_state, "agent_runtime", None), "session_factory", None)
    return factory


def _registry_of_app(app_state: Any) -> Any:
    service = getattr(app_state, "confirmation_service", None)
    return getattr(service, "registry", None)


def _refetchable_check(registry: Any) -> Callable[[str], bool]:
    def is_refetchable(name: str) -> bool:
        if registry is None:
            return False
        try:
            return bool(registry.get(name).refetchable)
        except Exception:  # noqa: BLE001 - 未知工具按不可重取处理
            return False

    return is_refetchable


class Compactor:
    """会话级压缩入口：自动（``CompactionMiddleware``）与手动（REST）共用。

    依赖在用到时才从 ``app_state`` 取（装配顺序不受影响），每个会话一把
    ``asyncio.Lock``：同一会话的自动压缩与手动压缩串行，后到的会在锁内发现
    active 检查点已变并返回 ``skipped``。
    """

    def __init__(
        self,
        app_state: Any,
        session_factory: Any,
        *,
        broadcast: Callable[[str, Any], Any] | None = None,
    ) -> None:
        self._app_state = app_state
        self._session_factory = session_factory
        self._broadcast = broadcast
        self._locks: dict[str, asyncio.Lock] = {}
        self._local_layout: ContextLayout | None = None
        #: 压缩后记忆抽取的后台任务；持有引用避免任务被回收（M79 §4.2、§8）。
        self._memory_tasks: set[asyncio.Task[Any]] = set()
        #: 检查点写 ``history`` 来源的后台任务（M79 §4.5、07 §6）。
        self._history_tasks: set[asyncio.Task[Any]] = set()

    # ---- 延迟解析的装配依赖 ------------------------------------------------ #
    def _lazy(self, name: str) -> Any:
        return getattr(self._app_state, name, None)

    @property
    def _router(self) -> Any:
        return self._lazy("model_router")

    @property
    def _ledger(self) -> Any:
        return self._lazy("cost_ledger")

    @property
    def _run_service(self) -> Any:
        return self._lazy("run_service")

    @property
    def _spec_registry(self) -> Any:
        return self._lazy("agent_spec_registry")

    @property
    def _layout(self) -> ContextLayout:
        service = self._lazy("context_layout")
        if isinstance(service, ContextLayout):
            return service
        if self._local_layout is None:
            self._local_layout = ContextLayout(TokenEstimator())
        return self._local_layout

    def _lock(self, session_id: str) -> asyncio.Lock:
        lock = self._locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session_id] = lock
        return lock

    def _estimate_history(
        self,
        messages: Sequence[AgentMessage],
        summary: str | None,
        *,
        profile: ResolvedProfile,
    ) -> int:
        rendered: list[dict[str, Any]] = []
        if summary is not None:
            rendered.append({"role": "user", "content": summary})
        rendered.extend(_prepare(messages))
        return self._layout.estimator.estimate(rendered, key=profile.key)

    @staticmethod
    def _summary_message(text: str) -> dict[str, Any]:
        return {"role": "user", "content": text}

    def _estimate_after(
        self,
        text: str,
        messages: Sequence[AgentMessage],
        upto_seq: int,
        *,
        profile: ResolvedProfile,
    ) -> int:
        remaining = [message for message in messages if (_seq(message) or -1) > upto_seq]
        rendered = [self._summary_message(text), *_prepare(remaining)]
        return self._layout.estimator.estimate(rendered, key=profile.key)

    def _cleared_placeholders(
        self, compacted: Sequence[AgentMessage], settings: Any
    ) -> dict[int, str]:
        """按清理规则算出被压缩范围内每条 tool 消息的占位（message.id → 文本）。"""
        if not compacted:
            return {}
        rows: list[dict[str, Any]] = []
        for message in compacted:
            row = _message_dict(message)
            row["id"] = _field(message, "id")
            rows.append(row)
        plan = plan_clearing(
            rows,
            keep_rounds=int(settings.context.clear_tool_results_after),
            is_refetchable=_refetchable_check(_registry_of_app(self._app_state)),
            estimator=self._layout.estimator,
        )
        if not plan.message_indexes:
            return {}
        names = _tool_names(rows)
        cleared: dict[int, str] = {}
        for index in plan.message_indexes:
            message = rows[index]
            message_id = message.get("id")
            if not isinstance(message_id, int):
                continue
            cleared[message_id] = clearing_placeholder(
                message, _tool_name(message, names)
            )
        return cleared

    async def _start_summary_run(
        self,
        *,
        parent_run_id: str | None,
        session_id: str,
        spec: AgentSpec,
    ) -> str | None:
        service = self._run_service
        if service is None:
            return None
        run = await service.start(
            agent_name="summarizer",
            kind=AgentRunKind.COMPACTION,
            session_id=None,
            parent_run_id=parent_run_id,
            spec_version=spec.spec_version,
            meta={"target_session_id": session_id},
        )
        return str(run.id)

    async def _finish_summary_run(
        self,
        run_id: str | None,
        *,
        status: AgentRunStatus,
        error_code: str | None = None,
    ) -> None:
        service = self._run_service
        if service is None or run_id is None:
            return
        try:
            await service.finish(
                run_id,
                status=status,
                finish_reason="stop" if status == AgentRunStatus.SUCCEEDED else "error",
                error_code=error_code,
            )
        except Exception:  # noqa: BLE001 - 运行收尾失败不改变压缩结果
            logger.warning("摘要运行收尾失败：%s", run_id, exc_info=True)

    @staticmethod
    def _summarizer_input(previous: str, chunk: str, durable: DurableState) -> str:
        return (
            f"{_PREVIOUS_HEADER}\n{previous}\n"
            f"{_MESSAGES_HEADER}\n{chunk}\n"
            f"{_DURABLE_HEADER}\n{render_durable(durable)}"
        )

    def _chunks(
        self,
        compacted: Sequence[AgentMessage],
        cleared: Mapping[int, str],
        *,
        previous: str,
        durable: DurableState,
        window: int,
    ) -> list[str]:
        """按消息边界切摘要输入，每块估算不超过档案窗口的一半。"""
        durable_text = render_durable(durable)
        overhead = estimate_text(
            f"{_PREVIOUS_HEADER}\n{previous}\n{_MESSAGES_HEADER}\n\n"
            f"{_DURABLE_HEADER}\n{durable_text}"
        )
        limit = int(SUMMARIZER_INPUT_RATIO * window) - overhead
        lines = _render_lines(compacted, cleared=cleared)
        chunks: list[str] = []
        current: list[str] = []
        for line in lines:
            candidate = "\n".join([*current, line])
            if current and estimate_text(candidate) > limit:
                chunks.append("\n".join(current))
                current = [line]
            else:
                current.append(line)
        if current:
            chunks.append("\n".join(current))
        return chunks or [""]

    async def _summarize(
        self,
        compacted: Sequence[AgentMessage],
        current: CompactionCheckpoint | None,
        durable: DurableState,
        cleared: Mapping[int, str],
        *,
        session_id: str,
        trigger: str,
        parent_run_id: str | None,
    ) -> tuple[CompactionSummary, str | None, bool, str | None]:
        """调摘要代理；失败时自动压缩退回机械摘要、手动压缩抛错。"""
        registry = self._spec_registry
        spec = (
            registry.get("summarizer")
            if registry is not None
            else builtin_spec("summarizer")
        )
        router = self._router
        ledger = self._ledger
        settings = get_settings()
        summarizer_profile = await router.resolve("summarizer")
        window = budget_for(summarizer_profile, settings).window
        previous = "无"
        if current is not None:
            filtered = {
                key: value
                for key, value in (current.summary_json or {}).items()
                if key not in SYSTEM_SUMMARY_KEYS
            }
            previous = json.dumps(filtered, ensure_ascii=False, default=str)

        run_id = await self._start_summary_run(
            parent_run_id=parent_run_id, session_id=session_id, spec=spec
        )
        try:
            result: Any = None
            for chunk in self._chunks(
                compacted,
                cleared,
                previous=previous,
                durable=durable,
                window=window,
            ):
                user = self._summarizer_input(previous, chunk, durable)
                result = await complete_structured(
                    router=router,
                    ledger=ledger,
                    role="summarizer",
                    schema_name="compaction_summary",
                    messages=[
                        {"role": "system", "content": spec.body},
                        {"role": "user", "content": user},
                    ],
                    agent_name="summarizer",
                    run_id=run_id,
                )
                previous = result.value.model_dump_json()
        except AppError as exc:
            await self._finish_summary_run(
                run_id, status=AgentRunStatus.FAILED, error_code=str(exc.code)
            )
            if trigger == "manual":
                if exc.code == ErrorCode.BUDGET_EXCEEDED:
                    raise
                raise AppError(
                    ErrorCode.COMPACTION_FAILED,
                    "压缩失败：摘要模型没有给出可用的结果，稍后重试",
                    {"cause": str(exc.code)},
                ) from exc
            return (
                mechanical_summary(
                    compacted,
                    is_refetchable=_refetchable_check(
                        _registry_of_app(self._app_state)
                    ),
                ),
                None,
                True,
                run_id,
            )
        await self._finish_summary_run(run_id, status=AgentRunStatus.SUCCEEDED)
        return result.value, summarizer_profile.model, False, run_id

    @staticmethod
    def _ensure_active_request(
        summary: CompactionSummary, active_request: AgentMessage | None
    ) -> CompactionSummary:
        """长单轮被压进摘要时，把用户原话补进 ``user_requests``（in_progress）。"""
        if active_request is None:
            return summary
        seq = _seq(active_request)
        text = _content_text(_field(active_request, "content"))[:500]
        if not text:
            return summary
        requests = list(summary.user_requests)
        for index, item in enumerate(requests):
            if item.seq == seq:
                requests[index] = item.model_copy(
                    update={"status": "in_progress", "text": text}
                )
                break
        else:
            requests.append(
                UserRequest(seq=seq, text=text, status="in_progress")
            )
        if len(requests) > 20:
            requests = requests[-20:]
        return summary.model_copy(update={"user_requests": requests})

    async def _broadcast_created(
        self,
        *,
        session_id: str,
        checkpoint_id: str,
        run_id: str | None,
        trigger: str,
        tokens_before: int,
        tokens_after: int,
        upto_seq: int,
        degraded: bool,
    ) -> None:
        if self._broadcast is None:
            return
        payload = {
            "session_id": session_id,
            "event": "compaction_created",
            "payload": {
                "checkpoint_id": checkpoint_id,
                "run_id": run_id,
                "trigger": trigger,
                "tokens_before": tokens_before,
                "tokens_after": tokens_after,
                "upto_seq": upto_seq,
                "degraded": degraded,
            },
        }
        result = self._broadcast("agent_event", payload)
        if inspect.isawaitable(result):
            await result

    def _schedule_memory_extraction(
        self,
        *,
        session_id: str,
        session_kind: str,
        ticket_id: str | None,
        compacted: Sequence[AgentMessage],
        upto_seq: int,
        parent_run_id: str | None,
    ) -> None:
        """检查点提交并广播后，后台抽取 ``seq ≤ upto_seq`` 的那一段（M79 §4.2）。

        只对总控生效：Worker 的压缩段属于工单过程，终态时已经整体抽过一次。取不到
        记忆服务、模型路由或账本时静默跳过；抽取本身失败由后台任务吞掉。
        """
        if session_kind != "orchestrator":
            return
        memory_service = self._lazy("memory_service")
        router = self._router
        ledger = self._ledger
        if memory_service is None or router is None or ledger is None:
            return
        archive = [
            {
                "role": str(_field(message, "role") or ""),
                "content": _content_text(_field(message, "content")),
            }
            for message in compacted
            if _role(message) in ("user", "assistant")
        ]
        if not archive:
            return
        try:
            task = asyncio.create_task(
                self._run_memory_extraction(
                    memory_service=memory_service,
                    router=router,
                    ledger=ledger,
                    session_id=session_id,
                    ticket_id=ticket_id,
                    archive=archive,
                    parent_run_id=parent_run_id,
                ),
                name=f"memory-extract-{session_id}-{upto_seq}",
            )
        except RuntimeError:  # pragma: no cover - 正常压缩总在事件循环里
            logger.warning("压缩后记忆抽取没有事件循环：session=%s", session_id)
            return
        self._memory_tasks.add(task)
        task.add_done_callback(self._memory_tasks.discard)

    async def _run_memory_extraction(
        self,
        *,
        memory_service: Any,
        router: Any,
        ledger: Any,
        session_id: str,
        ticket_id: str | None,
        archive: Sequence[dict[str, Any]],
        parent_run_id: str | None,
    ) -> None:
        """后台执行压缩段抽取；失败只记日志，不改变检查点。"""
        from maa_api.agent.memory.extract import extract_candidates

        try:
            await extract_candidates(
                memory_service,
                session_factory=self._session_factory,
                router=router,
                ledger=ledger,
                run_service=self._run_service,
                ticket_id=ticket_id,
                session_id=session_id,
                parent_run_id=parent_run_id,
                archive=archive,
                knowledge_service=self._lazy("knowledge_service"),
                spec_registry=self._spec_registry,
                trigger="compaction",
            )
        except Exception:  # noqa: BLE001 - 压缩结果不受抽取失败影响
            logger.warning(
                "压缩后记忆抽取失败：session=%s", session_id, exc_info=True
            )

    def _invalidate_memory_cache(self, session_id: str) -> None:
        """压缩后让 ``MemoryInjectionMiddleware`` 下一轮重新选择（07 §5.4）。"""
        cache = self._lazy("memory_injection_cache")
        if cache is None:
            return
        try:
            cache.invalidate(session_id)
        except Exception:  # noqa: BLE001 - 失效失败不能影响压缩
            logger.warning("失效记忆注入缓存失败：session=%s", session_id, exc_info=True)

    def _schedule_history_index(self, checkpoint_id: str) -> None:
        """检查点提交后异步写 ``checkpoint:<id>`` 文档；失败只记日志。"""
        history_indexer = self._lazy("history_indexer")
        if history_indexer is None:
            return
        try:
            task = asyncio.create_task(
                history_indexer.index_checkpoint(checkpoint_id),
                name=f"history-index-checkpoint-{checkpoint_id}",
            )
        except RuntimeError:  # pragma: no cover - 正常压缩总在事件循环里
            logger.warning("检查点历史索引没有事件循环：checkpoint=%s", checkpoint_id)
            return
        self._history_tasks.add(task)
        task.add_done_callback(self._history_tasks.discard)

    async def compact(
        self,
        *,
        session_id: str,
        session_kind: str,
        ticket_id: str | None,
        spec: AgentSpec,
        profile: ResolvedProfile,
        trigger: str,
        parent_run_id: str | None = None,
        durable: DurableState | None = None,
        expected_checkpoint_id: str | None = None,
    ) -> CompactionResult:
        """压缩一次会话；同一会话的调用由进程内锁串行。"""
        settings = get_settings()
        async with self._lock(session_id):
            async with self._session_factory() as session:
                repo = CompactionCheckpointRepository(session)
                current = await repo.active_for_session(session_id)
                after = current.upto_seq if current is not None else -1
                messages = await AgentMessageRepository(session).list_after_seq(
                    session_id, after
                )
                current_id = current.id if current is not None else None
                current_summary = (
                    current.summary if current is not None else None
                )
                current_upto = current.upto_seq if current is not None else None

            if (
                expected_checkpoint_id is not None
                and current_id != expected_checkpoint_id
            ):
                tokens = self._estimate_history(
                    messages, current_summary, profile=profile
                )
                return CompactionResult(
                    checkpoint_id=None,
                    skipped=True,
                    tokens_before=tokens,
                    tokens_after=tokens,
                    upto_seq=current_upto,
                    degraded=False,
                )

            upto_seq, active_request = select_upto_seq(
                messages,
                keep_recent_turns=int(settings.context.keep_recent_turns),
                window=budget_for(profile, settings).window,
                estimator=self._layout.estimator,
            )
            if upto_seq is None:
                tokens = self._estimate_history(
                    messages, current_summary, profile=profile
                )
                return CompactionResult(
                    checkpoint_id=None,
                    skipped=True,
                    tokens_before=tokens,
                    tokens_after=tokens,
                    upto_seq=current_upto,
                    degraded=False,
                )

            compacted = [
                message for message in messages if (_seq(message) or -1) <= upto_seq
            ]
            if durable is None:
                durable = await collect_durable_state(
                    self._app_state,
                    session_id=session_id,
                    session_kind=session_kind,
                    ticket_id=ticket_id,
                )
            cleared = self._cleared_placeholders(compacted, settings)
            summary, model, degraded, run_id = await self._summarize(
                compacted,
                current,
                durable,
                cleared,
                session_id=session_id,
                trigger=trigger,
                parent_run_id=parent_run_id,
            )
            summary = complete_refs(summary, compacted)
            summary = self._ensure_active_request(summary, active_request)
            summary_json = summary.model_dump() | {
                "schema_version": 1,
                "upto_seq": upto_seq,
                "previous_checkpoint_id": current_id,
                "degraded": degraded,
            }
            text = render_checkpoint_message(summary_json, durable, upto_seq)
            tokens_before = self._estimate_history(
                messages, current_summary, profile=profile
            )
            tokens_after = self._estimate_after(
                text, messages, upto_seq, profile=profile
            )

            async with self._session_factory() as session:
                repo = CompactionCheckpointRepository(session)
                await repo.supersede_active(session_id)
                stored = await repo.add(
                    CompactionCheckpoint(
                        session_id=session_id,
                        run_id=run_id,
                        upto_seq=upto_seq,
                        summary=text,
                        summary_json=summary_json,
                        durable=durable.model_dump(),
                        tokens_before=tokens_before,
                        tokens_after=tokens_after,
                        model=model,
                        trigger=trigger,
                    )
                )
                checkpoint_id = str(stored.id)
                await session.commit()

            await self._broadcast_created(
                session_id=session_id,
                checkpoint_id=checkpoint_id,
                run_id=run_id,
                trigger=trigger,
                tokens_before=tokens_before,
                tokens_after=tokens_after,
                upto_seq=upto_seq,
                degraded=degraded,
            )
            self._schedule_memory_extraction(
                session_id=session_id,
                session_kind=session_kind,
                ticket_id=ticket_id,
                compacted=compacted,
                upto_seq=upto_seq,
                parent_run_id=run_id,
            )
            # M79：检查点换掉之后总控的记忆选择必须重选（07 §5.4）；检查点正文
            # 异步写进 ``history`` 来源。两者失败都不影响压缩结果。
            self._invalidate_memory_cache(session_id)
            self._schedule_history_index(checkpoint_id)
            return CompactionResult(
                checkpoint_id=checkpoint_id,
                skipped=False,
                tokens_before=tokens_before,
                tokens_after=tokens_after,
                upto_seq=upto_seq,
                degraded=degraded,
            )

    async def compact_session(self, session_id: str) -> CompactionResult:
        """手动压缩入口：不经过运行，只发 WebSocket 事件。"""
        async with self._session_factory() as session:
            stored = await AgentSessionRepository(session).get(session_id)
            if stored is None:
                raise AppError(
                    ErrorCode.AGENT_SESSION_NOT_FOUND, "Agent 会话不存在"
                )
            kind = str(stored.kind)
            archived = stored.archived_at is not None
            ticket_id = stored.ticket_id
            active = await AgentRunRepository(session).get_active_for_session(
                session_id
            )
            active_id = str(active.id) if active is not None else None
            latest = await AgentRunRepository(session).list_page(
                session_id=session_id, root_only=False, page=1, size=1
            )
            agent_name = latest.items[0].agent_name if latest.items else None
        if kind not in ("orchestrator", "worker") or archived:
            raise AppError(ErrorCode.INVALID_PARAMETER, "这个会话不支持压缩")
        run_service = self._run_service
        if run_service is not None:
            active = await run_service.active_run(session_id)
            active_id = str(active.id) if active is not None else None
        if active_id is not None:
            raise AppError(
                ErrorCode.RUN_ALREADY_ACTIVE,
                "该会话已有运行在进行",
                {"session_id": session_id, "run_id": active_id},
            )
        if not agent_name:
            return CompactionResult(None, True, 0, 0, None, False)
        registry = self._spec_registry
        spec = (
            registry.get(agent_name)
            if registry is not None
            else builtin_spec(agent_name)
        )
        router = self._router
        profile = await router.resolve_for_spec(spec)
        return await self.compact(
            session_id=session_id,
            session_kind=kind,
            ticket_id=ticket_id,
            spec=spec,
            profile=profile,
            trigger="manual",
            parent_run_id=None,
            expected_checkpoint_id=None,
        )


# --------------------------------------------------------------------------- #
# 自动压缩中间件
# --------------------------------------------------------------------------- #
def _layout_for(ctx: RunContext) -> ContextLayout:
    service = getattr(getattr(ctx.app, "state", None), "context_layout", None)
    if isinstance(service, ContextLayout):
        return service
    local = ctx.state.get("compaction.context_layout")
    if isinstance(local, ContextLayout):
        return local
    local = ContextLayout(TokenEstimator())
    ctx.state["compaction.context_layout"] = local
    return local


def _compactor_for(ctx: RunContext) -> Any:
    return getattr(getattr(ctx.app, "state", None), "compactor", None)


class CompactionMiddleware(Middleware):
    """估算超过阈值时自动压缩（M74 规格 §4.5.2）。

    只对总控与 Worker 生效，legacy 定义直接返回。估算口径与清理中间件一致：
    先按清理规则替换占位，再渲染并计 token；达到 ``budget.trigger_tokens`` 才压缩。
    """

    name = "compaction"
    applies_to = frozenset({"orchestrator", "worker"})

    def __init__(self, *, settings_provider: Callable[[], Any] | None = None) -> None:
        self._settings_provider = settings_provider or get_settings

    async def before_model(self, ctx: RunContext, req: ModelRequest) -> ModelRequest:
        if ctx.spec.name in LEGACY_SPEC_NAMES:
            return req
        compactor = _compactor_for(ctx)
        if compactor is None or ctx.session_id is None:
            return req
        parts = req.meta.get("layout_parts")
        if parts is None:
            return req
        profile = req.profile
        if profile is None:
            model_state = ctx.state.get("model")
            if isinstance(model_state, Mapping):
                profile = model_state.get("profile")
        if profile is None:
            return req

        settings = self._settings_provider()
        if inspect.isawaitable(settings):
            settings = await settings
        budget = budget_for(profile, settings)
        layout = _layout_for(ctx)
        registry = getattr(
            getattr(getattr(ctx.app, "state", None), "confirmation_service", None),
            "registry",
            None,
        )
        plan = plan_clearing(
            parts.history,
            keep_rounds=int(settings.context.clear_tool_results_after),
            is_refetchable=_refetchable_check(registry),
            estimator=layout.estimator,
        )
        candidate = plan
        history = (
            apply_clearing(parts.history, candidate)
            if plan.message_indexes
            else parts.history
        )
        candidate_parts = ContextParts(
            system=parts.system,
            history=history,
            tail=parts.tail,
            tools=parts.tools,
            position=parts.position,
        )
        estimated = layout.estimator.estimate(
            layout.render(candidate_parts),
            parts.tools,
            key=profile.key,
        )
        if estimated < budget.trigger_tokens:
            return req

        state = ctx.state.setdefault("compaction", {})
        failed_at = state.get("failed_at")
        if (
            failed_at is not None
            and estimated < failed_at + RETRY_GROWTH_RATIO * budget.window
        ):
            return req
        try:
            result = await compactor.compact(
                session_id=ctx.session_id,
                session_kind=ctx.spec.kind,
                ticket_id=ctx.ticket_id,
                spec=ctx.spec,
                profile=profile,
                trigger="auto",
                parent_run_id=ctx.run_id,
                durable=ctx.state.get("durable"),
                expected_checkpoint_id=ctx.state.get("context_layout", {}).get(
                    "checkpoint_id"
                ),
            )
        except Exception:  # noqa: BLE001 - 压缩失败不能让这一轮失败
            logger.exception(
                "自动压缩失败：session=%s run=%s", ctx.session_id, ctx.run_id
            )
            state["failed_at"] = estimated
            return req
        if result.skipped:
            return req

        parts.history, checkpoint_id = await load_history(
            ctx, profile=profile
        )
        ctx.state.setdefault("context_layout", {})["checkpoint_id"] = checkpoint_id
        req = layout.apply(ctx, req, parts, profile=profile)
        req.profile = profile
        await ctx.emit(
            "compaction",
            {
                "run_id": ctx.run_id,
                "checkpoint_id": result.checkpoint_id,
                "tokens_before": result.tokens_before,
                "tokens_after": result.tokens_after,
            },
        )
        return req

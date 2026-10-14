"""侦察 subagent：把长探索挡在父上下文之外（M48 规格 §4.3 / D4 / D5）。

父 agent 调一次 ``delegate_recon``，这里的子循环最多跑 ``max_steps`` 步：
每步一次流式补全，只带 ``list_files`` / ``read_file`` / ``search_code`` 三个只读工具。
子 agent 的对话、被读出来的文件正文、被截断的原始输出**都不进入父会话历史**——
父会话只收到一份结构化报告（结论 + ``path:line`` 证据 + 不确定项 + 预算用量）。

预算三层（规格 D4）：步数上限、单次工具输出截断、整轮累计字符预算。任何一层到顶都
是「收尾并说明未完成」，不把错误抛给父 agent。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Sequence

from maa_api.agent.recon import explore
from maa_api.agent.registry import ToolContext, ToolRegistry
from maa_api.domain.errors import AppError, ErrorCode

__all__ = ["ReconBudget", "ReconDeps", "ReconStep", "budget_from_settings", "run_recon"]

#: 子 agent 允许使用的工具：只有这三个（规格 D4）。
RECON_TOOL_NAMES: tuple[str, ...] = ("list_files", "read_file", "search_code")
#: 一步里最多执行的工具调用数：模型偶尔一次发一长串，执行太多会把预算瞬间吃光。
MAX_CALLS_PER_STEP = 4
#: 报告 summary 的字符上限：父上下文只该看到结论，不是子 agent 的转述。
SUMMARY_MAX_CHARS = 1500
#: 单条 finding 的 detail 上限。
DETAIL_MAX_CHARS = 600

SYSTEM_PROMPT = """你是 MAA-API 的只读侦察员。你的上层 agent 让你查清一个具体问题，你只负责查、不负责改。

规矩：
- 只能用 list_files / read_file / search_code 三个只读工具。不能写文件、不能跑测试、不能碰设备。
- 每条结论都要带证据，证据写成 `相对路径:行号`（例如 `maa_api/agent/runtime.py:1243`）。没有证据的猜测不要写进结论。
- 先 search_code 定位，再 read_file 读片段；不要为了「看全」把整个文件读完。
- 查不到、被拒绝（密钥/二进制/越界）就如实写进 uncertainties，不要绕路猜。
- 不扩大范围：只回答上层给的问题，相邻的坑不要顺手挖。

最后一步必须只输出一段 JSON（不要 markdown 代码块、不要前后缀文字）：
{"summary": "3 句以内的结论", "findings": [{"title": "一句话结论", "detail": "理由与影响", "evidence": ["路径:行号"]}], "uncertainties": ["没查清或查不了的点"]}
findings 最多 8 条，每条证据最多 4 个路径。如果到这一步仍未查清，就把 summary 写成「没查清 + 卡在哪」，findings 留空。"""


@dataclass(frozen=True, slots=True)
class ReconDeps:
    """子循环需要的外部依赖：工作区根目录、设置对象、模型与客户端。"""

    settings: Any
    client: Any
    model: str
    max_steps: int
    max_chars: int
    output_chars: int


@dataclass(slots=True)
class ReconBudget:
    """预算用量：步数、字符累计、是否提前收尾。"""

    max_steps: int
    max_chars: int
    output_chars: int
    steps: int = 0
    chars: int = 0
    exhausted: bool = False

    def spend(self, text: str) -> bool:
        """记一次字符消耗；返回 ``False`` 表示预算已尽、该收尾了。"""

        self.chars += len(text)
        if self.chars >= self.max_chars:
            self.exhausted = True
            return False
        return True

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_steps": self.max_steps,
            "max_chars": self.max_chars,
            "output_chars": self.output_chars,
            "chars_used": self.chars,
            "exhausted": self.exhausted,
        }


@dataclass(slots=True)
class ReconStep:
    """一步的模型输出：正文、工具调用片段、用量。"""

    text: str = ""
    calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)


def budget_from_settings(settings: Any) -> ReconBudget:
    """从 ``agent.recon_*`` 取三层预算；字段缺省时用规格默认值。"""

    agent = getattr(settings, "agent", None)
    max_steps = getattr(agent, "recon_max_steps", None)
    max_chars = getattr(agent, "recon_max_chars", None)
    return ReconBudget(
        max_steps=int(max_steps) if isinstance(max_steps, int) and max_steps > 0 else 8,
        max_chars=int(max_chars) if isinstance(max_chars, int) and max_chars > 0 else 200_000,
        output_chars=explore.read_limit(settings),
    )


def recon_tool_schemas(registry: ToolRegistry) -> list[dict[str, Any]]:
    """只导出三个只读工具的 schema（子 agent 看不到其它任何工具）。"""

    names = set(RECON_TOOL_NAMES)
    return [
        {
            "type": "function",
            "function": {
                "name": definition.name,
                "description": definition.description,
                "parameters": definition.params_model.model_json_schema(
                    by_alias=True, mode="validation"
                ),
            },
        }
        for definition in registry.list()
        if definition.name in names
    ]


async def dispatch_tool(
    registry: ToolRegistry,
    settings: Any,
    name: str,
    arguments: Mapping[str, Any],
) -> str:
    """执行一次子 agent 工具调用，把结构化结果压成一句可回灌的 JSON。

    这里刻意不接受 ``ToolContext``：子 agent 的工具只需要设置（工作区根目录），
    没有数据库会话、没有设备、没有确认服务——拿不到就写不了。工具处理器读的是
    ``ctx.request.app.state.settings_provider``，所以这里造一个只含这一项的最小
    request：**不要**把父 agent 的真实 state 透给子 agent，否则它就顺着拿到设备与确认服务。
    """

    context = ToolContext(
        caller=ctx_caller(),
        session_id=None,
        request_id=None,
        request=SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(settings_provider=lambda: settings),
            )
        ),
        db_session=None,
    )
    try:
        result = await registry.execute(name, arguments, context)
    except AppError as exc:
        # 被拒绝本身也是侦察结论（例如「这个文件读不了」），回给子 agent 让它换路。
        return json.dumps(
            {"error": exc.code.value, "message": exc.message}, ensure_ascii=False
        )
    return json.dumps(result, ensure_ascii=False, default=str)


def ctx_caller() -> Any:
    """工具上下文里的调用方身份：侦察子 agent 仍是内置 agent 自己。"""

    from maa_api.domain.enums import CallerType

    return CallerType.INTERNAL


async def run_recon(
    deps: ReconDeps,
    *,
    question: str,
    hints: Sequence[str] = (),
    registry: ToolRegistry | None = None,
    step_runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """跑一次有界侦察，返回给父上下文看的报告（规格 D5）。

    :param deps: 工作区设置、模型客户端、三层预算。
    :param question: 上层要查清的问题。
    :param hints: 「去哪找」的提示，最多 5 条。
    :param registry: 已注册工具表的注册表；``None`` 时现建一份（只用得上 recon 组）。
    :param step_runner: 单步流式补全的调用口，默认用 ``deps.client``；测试注入假客户端。
    :returns: ``{question, summary, findings, uncertainties, steps_used, budget}``。
    """

    explore.resolve_root(deps.settings)
    tools = registry if registry is not None else _recon_registry()
    schemas = recon_tool_schemas(tools)
    budget = ReconBudget(
        max_steps=deps.max_steps,
        max_chars=deps.max_chars,
        output_chars=deps.output_chars,
    )
    runner = step_runner or _default_step_runner
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _question_block(question, hints)},
    ]

    steps: list[ReconStep] = []
    stopped: str | None = None
    for index in range(budget.max_steps):
        budget.steps = index + 1
        step = await runner(deps, messages, schemas)
        steps.append(step)
        if not budget.spend(step.text or ""):
            stopped = "budget"
            break
        # 只放行白名单里的工具；这一步同时把内部形态（name/arguments 在顶层）折成
        # 回灌模型要的 OpenAI 形态，下一步不可信的调用名在这里就被丢掉。
        parts = [_call_parts(call) for call in step.calls]
        allowed = [part for part in parts if part[0] in RECON_TOOL_NAMES]
        if not allowed:
            stopped = "answered"
            break
        selected = allowed[:MAX_CALLS_PER_STEP]
        messages.append(
            {
                "role": "assistant",
                "content": step.text or "",
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": json.dumps(arguments, ensure_ascii=False),
                        },
                    }
                    for name, call_id, arguments in selected
                ],
            }
        )
        for name, call_id, arguments in selected:
            payload = await dispatch_tool(tools, deps.settings, name, arguments)
            body = payload[: budget.output_chars]
            if not budget.spend(body):
                stopped = "budget"
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": body,
                }
            )
            if stopped == "budget":
                break
        if stopped == "budget":
            break
    else:
        stopped = "max_steps"

    final_text = steps[-1].text if steps else ""
    return _report(
        question=question,
        final_text=final_text,
        budget=budget,
        stopped=stopped or "max_steps",
    )


def _recon_registry() -> ToolRegistry:
    """现建一份只含 recon 组的注册表（避免与父 agent 的注册表耦合）。"""

    registry = ToolRegistry()
    explore.register_tools(registry)
    return registry


async def _default_step_runner(
    deps: ReconDeps,
    messages: Sequence[dict[str, Any]],
    schemas: Sequence[dict[str, Any]],
) -> ReconStep:
    """一次流式补全：把文本、工具调用片段与用量攒成一个 :class:`ReconStep`。"""

    step = ReconStep()
    fragments: list[dict[str, Any]] = []
    async for chunk in deps.client.stream_chat_completion(deps.model, messages, schemas):
        if not isinstance(chunk, Mapping):
            raise AppError(ErrorCode.LLM_REQUEST_FAILED, "LLM 返回了无效的响应块")
        for call in _chunk_tool_calls(chunk):
            fragments.append(call)
        for choice in chunk.get("choices") or []:
            if not isinstance(choice, Mapping):
                continue
            delta = choice.get("delta") or {}
            if isinstance(delta, Mapping):
                value = delta.get("content")
                if isinstance(value, str) and value:
                    step.text += value
        usage = chunk.get("usage")
        if isinstance(usage, Mapping):
            for key in ("prompt_tokens", "completion_tokens"):
                value = usage.get(key)
                if isinstance(value, int) and value >= 0:
                    step.usage[key] = value
    step.calls = _merge_tool_calls(fragments)
    return step


def _chunk_tool_calls(chunk: Mapping[str, Any]) -> list[dict[str, Any]]:
    """从流式块里取工具调用片段（与 runtime 同一读数口径）。"""

    collected: list[dict[str, Any]] = []
    for choice in chunk.get("choices") or []:
        if not isinstance(choice, Mapping):
            continue
        delta = choice.get("delta") or {}
        if not isinstance(delta, Mapping):
            continue
        calls = delta.get("tool_calls")
        if not isinstance(calls, list):
            continue
        for entry in calls:
            if isinstance(entry, Mapping):
                collected.append(dict(entry))
    return collected


def _merge_tool_calls(fragments: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """把按 index 分片的增量拼成完整调用，输出的就是回灌模型用的 OpenAI 形态。

    形态必须是 ``{"id", "type": "function", "function": {"name", "arguments"}}``：
    这条 assistant 消息会原样进入子 agent 的下一次请求，真实 OpenAI 兼容端点对
    其它形态直接 400（规格 §4.3 的「回灌」路径）。内部读取用 :func:`_call_parts`。
    """

    merged: dict[int, dict[str, Any]] = {}
    for fragment in fragments:
        index = fragment.get("index")
        key = index if isinstance(index, int) else len(merged)
        current = merged.setdefault(key, {"name": "", "arguments": "", "id": None})
        if isinstance(fragment.get("id"), str) and fragment["id"]:
            current["id"] = fragment["id"]
        function = fragment.get("function")
        if isinstance(function, Mapping):
            if isinstance(function.get("name"), str) and function["name"]:
                current["name"] = function["name"]
            if isinstance(function.get("arguments"), str):
                current["arguments"] += function["arguments"]
    calls: list[dict[str, Any]] = []
    for key in sorted(merged):
        item = merged[key]
        if not item["name"]:
            continue
        calls.append(
            {
                "id": item["id"] or item["name"],
                "type": "function",
                "function": {"name": item["name"], "arguments": item["arguments"]},
            }
        )
    return calls


def _call_parts(call: Mapping[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """从一条调用里取 ``(name, id, arguments)``，兼容扁平与 OpenAI 两种形态。"""

    function = call.get("function")
    source = function if isinstance(function, Mapping) else call
    name = str(source.get("name") or "")
    call_id = str(call.get("id") or name)
    raw = source.get("arguments")
    arguments: dict[str, Any] = {}
    if isinstance(raw, Mapping):
        arguments = dict(raw)
    elif isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, Mapping):
            arguments = dict(parsed)
    return name, call_id, arguments


def _question_block(question: str, hints: Sequence[str]) -> str:
    lines = [f"要查清的问题：{question}"]
    if hints:
        lines.append("可以先看的地方：" + "；".join(str(hint) for hint in list(hints)[:5]))
    lines.append("查完只输出那段 JSON 报告。")
    return "\n".join(lines)


def _report(
    *,
    question: str,
    final_text: str,
    budget: ReconBudget,
    stopped: str,
) -> dict[str, Any]:
    """把子 agent 的收尾文本整成结构化报告；JSON 非法时退化为 summary。"""

    parsed = _parse_report(final_text)
    summary = str(parsed.get("summary") or final_text or "").strip()[:SUMMARY_MAX_CHARS]
    findings: list[dict[str, Any]] = []
    for item in parsed.get("findings") or []:
        if not isinstance(item, Mapping):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        evidence = [
            str(entry).strip()
            for entry in (item.get("evidence") or [])
            if isinstance(entry, (str, int))
        ][:4]
        findings.append(
            {
                "title": title[:200],
                "detail": str(item.get("detail") or "").strip()[:DETAIL_MAX_CHARS],
                "evidence": evidence,
            }
        )
        if len(findings) >= 8:
            break
    uncertainties = [
        str(entry).strip()[:300]
        for entry in (parsed.get("uncertainties") or [])
        if isinstance(entry, str) and entry.strip()
    ][:6]
    if stopped == "budget":
        uncertainties.append("侦察预算用尽，报告可能不完整（未跑完就收尾）。")
    elif stopped == "max_steps":
        uncertainties.append(f"步数用尽（{budget.max_steps} 步），报告可能不完整。")
    if not parsed:
        uncertainties.append("子 agent 没有按约定输出 JSON，上面是它的收尾原文。")
    return {
        "question": question,
        "summary": summary,
        "findings": findings,
        "uncertainties": uncertainties,
        "steps_used": budget.steps,
        "stopped": stopped,
        "budget": budget.as_dict(),
    }


def _parse_report(text: str) -> dict[str, Any]:
    """从收尾文本里抠出 JSON 报告；抠不到就返回空字典走退化路径。"""

    if not text.strip():
        return {}
    candidates: list[str] = []
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidates.extend(fenced)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}

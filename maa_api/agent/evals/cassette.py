"""评测录制文件与模型传输层（M68 规格 §4.2.6、§4.5.2、§4.5.3）。

录制文件是 JSONL：第一行是头，之后每行一次模型调用。回放时传输层按通道消费录制
的应答，把 ``reply`` 展开成 OpenAI 流式分片交给真实的 ``OpenAICompatibleClient``；
录制时把上游分片原样收下，参数里来自工具结果的 UUID 换成占位符，回放才能复用。

这里不联网、不启动应用，也不依赖 runner；传输层只做「按录制作答」与「转发并记录」。
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx

__all__ = [
    "CASSETTE_FORMAT",
    "DEFAULT_AUX_TEXT",
    "PLACEHOLDER_RE",
    "UUID_RE",
    "VISION_PROBE_REPLY",
    "CapturedRequest",
    "Cassette",
    "CassetteCall",
    "CassetteError",
    "CassetteHeader",
    "CassetteTransport",
    "Channel",
    "CountingTransport",
    "RecordingTransport",
    "RequestFingerprint",
    "classify_request",
    "expand_reply",
    "fingerprint_request",
    "parse_sse_body",
    "render_placeholders",
    "sse_body",
    "summarize_request",
    "templatize_chunks",
]

#: 录制文件格式版本；头里的 ``format`` 不等于它时拒绝加载。
CASSETTE_FORMAT = 1
Channel = Literal["agent", "aux", "probe", "judge"]
#: 手写应答里引用工具结果的占位符：``{{result:<tool_call_id>:<字段路径>}}``。
PLACEHOLDER_RE = re.compile(r"\{\{result:([^:}]+):([^}]+)\}\}")
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
#: 会话标题等辅助调用没有录制时的固定应答。
DEFAULT_AUX_TEXT = "评测会话"
#: 视觉探查没有录制时的应答；与 ``runtime._VISION_PROBE_MARKER`` 一致。
VISION_PROBE_REPLY = "blue"
#: 能力探查没有录制时回的工具调用 id。
_PROBE_CALL_ID = "eval-probe"
#: ``expand_reply`` 里工具参数序列化后的默认值。
_EMPTY_ARGUMENTS = "{}"


class CassetteError(RuntimeError):
    """录制文件或回放过程不合法。

    ``code`` 是给 runner 分类用的稳定标识：``missing`` 文件不存在、``format``
    内容不是合法录制、``mismatch`` 与录制时的请求指纹/工具调用 id 不一致、
    ``exhausted`` 录制用完、``unused`` 有录制没用完、``placeholder`` 占位符
    解析失败、``limit`` 超过本次允许的模型调用次数。
    """

    code: Literal[
        "missing", "format", "mismatch", "exhausted", "unused", "placeholder", "limit"
    ]

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code  # type: ignore[assignment]
        super().__init__(message or code)


@dataclass
class CassetteHeader:
    """录制文件的头：谁录的、什么模型、哪种来源。"""

    scenario: str
    source: Literal["handwritten", "recorded"]
    recorded_at: str
    model: str
    agent: str
    spec_version: str | None = None
    notes: str = ""
    format: int = CASSETTE_FORMAT


@dataclass
class RequestFingerprint:
    """一次请求的形状指纹；不存正文，只存能发现漂移的摘要。"""

    model: str
    roles: list[str]
    tool_call_ids: list[str]
    tools_sha: str
    system_sha: str


@dataclass
class CassetteCall:
    """一次录制的模型调用，三种应答二选一（或记录上游错误）。"""

    channel: Channel
    request: RequestFingerprint | None = None
    reply: dict[str, Any] | None = None
    chunks: list[dict[str, Any]] | None = None
    error: dict[str, Any] | None = None


@dataclass
class CapturedRequest:
    """一次实际发出的请求摘要，供判分器看请求形状。"""

    channel: Channel
    fingerprint: RequestFingerprint
    reasoning_messages: int
    chars: int
    tool_count: int


# --------------------------------------------------------------------------- #
# 录制文件读写
# --------------------------------------------------------------------------- #


def _header_to_dict(header: CassetteHeader) -> dict[str, Any]:
    return {
        "type": "header",
        "format": header.format,
        "scenario": header.scenario,
        "source": header.source,
        "recorded_at": header.recorded_at,
        "model": header.model,
        "agent": header.agent,
        "spec_version": header.spec_version,
        "notes": header.notes,
    }


def _fingerprint_to_dict(fingerprint: RequestFingerprint) -> dict[str, Any]:
    return {
        "model": fingerprint.model,
        "roles": list(fingerprint.roles),
        "tool_call_ids": list(fingerprint.tool_call_ids),
        "tools_sha": fingerprint.tools_sha,
        "system_sha": fingerprint.system_sha,
    }


def _fingerprint_from_dict(value: Mapping[str, Any]) -> RequestFingerprint:
    try:
        return RequestFingerprint(
            model=str(value["model"]),
            roles=[str(item) for item in value.get("roles") or []],
            tool_call_ids=[str(item) for item in value.get("tool_call_ids") or []],
            tools_sha=str(value["tools_sha"]),
            system_sha=str(value["system_sha"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CassetteError("format", f"录制文件的请求指纹不合法：{exc}") from exc


def _call_to_dict(call: CassetteCall) -> dict[str, Any]:
    payload: dict[str, Any] = {"type": "call", "channel": call.channel}
    if call.request is not None:
        payload["request"] = _fingerprint_to_dict(call.request)
    if call.reply is not None:
        payload["reply"] = call.reply
    if call.chunks is not None:
        payload["chunks"] = call.chunks
    if call.error is not None:
        payload["error"] = call.error
    return payload


def _call_from_dict(value: Mapping[str, Any], line_number: int) -> CassetteCall:
    channel = value.get("channel")
    if channel not in ("agent", "aux", "probe", "judge"):
        raise CassetteError(
            "format", f"录制文件第 {line_number} 行的 channel 不合法：{channel!r}"
        )
    request_value = value.get("request")
    request = (
        _fingerprint_from_dict(request_value)
        if isinstance(request_value, Mapping)
        else None
    )
    reply = value.get("reply")
    chunks = value.get("chunks")
    error = value.get("error")
    if reply is not None and not isinstance(reply, Mapping):
        raise CassetteError("format", f"录制文件第 {line_number} 行的 reply 不是对象")
    if chunks is not None and not isinstance(chunks, list):
        raise CassetteError("format", f"录制文件第 {line_number} 行的 chunks 不是列表")
    if error is not None and not isinstance(error, Mapping):
        raise CassetteError("format", f"录制文件第 {line_number} 行的 error 不是对象")
    if reply is None and chunks is None and error is None:
        raise CassetteError(
            "format", f"录制文件第 {line_number} 行没有 reply、chunks 或 error"
        )
    return CassetteCall(
        channel=channel,  # type: ignore[arg-type]
        request=request,
        reply=dict(reply) if isinstance(reply, Mapping) else None,
        chunks=[dict(item) for item in chunks] if isinstance(chunks, list) else None,
        error=dict(error) if isinstance(error, Mapping) else None,
    )


def _header_from_dict(value: Mapping[str, Any]) -> CassetteHeader:
    try:
        header = CassetteHeader(
            scenario=str(value["scenario"]),
            source=value["source"],
            recorded_at=str(value["recorded_at"]),
            model=str(value["model"]),
            agent=str(value["agent"]),
            spec_version=(
                str(value["spec_version"])
                if value.get("spec_version") is not None
                else None
            ),
            notes=str(value.get("notes") or ""),
            format=int(value.get("format", CASSETTE_FORMAT)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CassetteError("format", f"录制文件的头不合法：{exc}") from exc
    if header.source not in ("handwritten", "recorded"):
        raise CassetteError("format", f"录制文件的 source 不合法：{header.source!r}")
    if header.format != CASSETTE_FORMAT:
        raise CassetteError(
            "format", f"不支持的录制文件格式 {header.format}（期望 {CASSETTE_FORMAT}）"
        )
    return header


@dataclass
class Cassette:
    """一个场景的录制文件：头加按通道消费的调用序列。"""

    header: CassetteHeader
    calls: list[CassetteCall] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> Cassette:
        """读取 JSONL 录制文件；不存在、不是合法 YAML/JSON 或格式版本不对时报错。"""
        try:
            text = Path(path).read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise CassetteError("missing", f"录制文件不存在：{path}") from exc
        except UnicodeDecodeError as exc:
            raise CassetteError("format", f"录制文件不是合法 UTF-8：{path}") from exc
        lines = [
            (number, line)
            for number, line in enumerate(text.splitlines(), start=1)
            if line.strip()
        ]
        if not lines:
            raise CassetteError("format", f"录制文件是空的：{path}")
        number, first = lines[0]
        try:
            header_value = json.loads(first)
        except json.JSONDecodeError as exc:
            raise CassetteError(
                "format", f"录制文件第 {number} 行不是合法 JSON：{exc}"
            ) from exc
        if not isinstance(header_value, Mapping):
            raise CassetteError("format", f"录制文件第 {number} 行不是对象")
        if header_value.get("type") != "header":
            raise CassetteError("format", f"录制文件第 {number} 行不是头")
        calls: list[CassetteCall] = []
        for number, line in lines[1:]:
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CassetteError(
                    "format", f"录制文件第 {number} 行不是合法 JSON：{exc}"
                ) from exc
            if not isinstance(value, Mapping) or value.get("type") != "call":
                raise CassetteError("format", f"录制文件第 {number} 行不是调用")
            calls.append(_call_from_dict(value, number))
        return cls(header=_header_from_dict(header_value), calls=calls)

    @classmethod
    def new(cls, header: CassetteHeader) -> Cassette:
        return cls(header=header, calls=[])

    def save(self, path: Path) -> None:
        """写成 JSONL；目录不存在时一并创建。"""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(_header_to_dict(self.header), ensure_ascii=False)]
        lines.extend(
            json.dumps(_call_to_dict(call), ensure_ascii=False) for call in self.calls
        )
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def pop(self, channel: Channel) -> CassetteCall | None:
        """取该通道最靠前的一条调用；没有时返回 ``None``（不报错）。"""
        for index, call in enumerate(self.calls):
            if call.channel == channel:
                return self.calls.pop(index)
        return None

    def remaining(self, channel: Channel) -> int:
        return sum(1 for call in self.calls if call.channel == channel)

    def append(self, call: CassetteCall) -> None:
        self.calls.append(call)


# --------------------------------------------------------------------------- #
# 请求分类、指纹与摘要
# --------------------------------------------------------------------------- #


def _messages(body: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    messages = body.get("messages")
    if not isinstance(messages, list):
        return []
    return [message for message in messages if isinstance(message, Mapping)]


def _tools(body: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    tools = body.get("tools")
    if not isinstance(tools, list):
        return []
    return [tool for tool in tools if isinstance(tool, Mapping)]


def _tool_name(tool: Mapping[str, Any]) -> str | None:
    function = tool.get("function")
    if isinstance(function, Mapping) and isinstance(function.get("name"), str):
        return function["name"]
    return None


def _is_probe_tool(body: Mapping[str, Any]) -> bool:
    return any(_tool_name(tool) == "capability_probe" for tool in _tools(body))


def _has_image_part(content: Any) -> bool:
    return isinstance(content, list) and any(
        isinstance(part, Mapping) and part.get("type") == "image_url" for part in content
    )


def classify_request(body: Mapping[str, Any]) -> Channel:
    """按规格 §4.5.3 判断一次请求属于哪个通道。"""
    if _is_probe_tool(body):
        return "probe"
    messages = _messages(body)
    if not _tools(body) and messages and _has_image_part(messages[-1].get("content")):
        return "probe"
    if not _tools(body) and (not messages or messages[0].get("role") != "system"):
        return "aux"
    return "agent"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _content_text(content: Any) -> str:
    """把消息 content 归一成文本；多模态只取文本片段，其它类型原样序列化。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, Mapping):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    if content is None:
        return ""
    return json.dumps(content, ensure_ascii=False, default=str)


def _trailing_tool_ids(messages: Sequence[Mapping[str, Any]]) -> list[str]:
    ids: list[str] = []
    for message in reversed(messages):
        if message.get("role") != "tool":
            break
        call_id = message.get("tool_call_id")
        if isinstance(call_id, str):
            ids.append(call_id)
    ids.reverse()
    return ids


def fingerprint_request(body: Mapping[str, Any]) -> RequestFingerprint:
    """生成请求指纹：模型、角色序列、末尾工具 id、工具名与系统提示的摘要。"""
    messages = _messages(body)
    tool_names = [
        name for tool in _tools(body) if (name := _tool_name(tool)) is not None
    ]
    system_parts = [
        _content_text(message.get("content"))
        for message in messages
        if message.get("role") == "system"
    ]
    return RequestFingerprint(
        model=str(body.get("model") or ""),
        roles=[str(message.get("role") or "") for message in messages],
        tool_call_ids=_trailing_tool_ids(messages),
        tools_sha=_sha(json.dumps(tool_names, ensure_ascii=False)),
        system_sha=_sha("\n".join(system_parts)),
    )


def summarize_request(body: Mapping[str, Any], channel: Channel) -> CapturedRequest:
    """记下请求的可判分形状；不保留正文，字符数用来约束上下文规模。"""
    messages = _messages(body)
    reasoning_messages = sum(
        1
        for message in messages
        if isinstance(message.get("reasoning_content"), str)
        and message["reasoning_content"]
    )
    chars = len(json.dumps(messages, ensure_ascii=False, default=str))
    return CapturedRequest(
        channel=channel,
        fingerprint=fingerprint_request(body),
        reasoning_messages=reasoning_messages,
        chars=chars,
        tool_count=len(_tools(body)),
    )


# --------------------------------------------------------------------------- #
# 应答展开
# --------------------------------------------------------------------------- #


def _token_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        if isinstance(value, float):
            return int(value)
        return 0
    return max(0, value)


def _normalized_usage(usage: Any) -> dict[str, Any]:
    raw = usage if isinstance(usage, Mapping) else {}
    cached = raw.get("cached_tokens")
    if cached is None:
        details = raw.get("prompt_tokens_details")
        if isinstance(details, Mapping):
            cached = details.get("cached_tokens")
    if cached is None:
        cached = raw.get("prompt_cache_hit_tokens")
    prompt_tokens = _token_count(raw.get("prompt_tokens"))
    completion_tokens = _token_count(raw.get("completion_tokens"))
    total_tokens = _token_count(raw.get("total_tokens")) or (
        prompt_tokens + completion_tokens
    )
    cached_tokens = _token_count(cached)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "prompt_tokens_details": {"cached_tokens": cached_tokens},
        "prompt_cache_hit_tokens": cached_tokens,
    }


def expand_reply(
    reply: Mapping[str, Any], *, model: str, seq: int
) -> list[dict[str, Any]]:
    """把手写 ``reply`` 展开成与 ``tests/fakes/fake_llm.py`` 同形的流式分片。"""

    def chunk(
        delta: dict[str, Any],
        *,
        finish_reason: str | None = None,
        usage: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": f"chatcmpl-eval-{seq}",
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [
                {"index": 0, "delta": delta, "finish_reason": finish_reason}
            ],
        }
        if usage is not None:
            payload["usage"] = usage
        return payload

    chunks: list[dict[str, Any]] = []
    reasoning = reply.get("reasoning")
    if isinstance(reasoning, str) and reasoning:
        chunks.append(chunk({"reasoning_content": reasoning}))
    text = reply.get("text")
    if isinstance(text, str) and text:
        chunks.append(chunk({"content": text}))

    raw_calls = reply.get("tool_calls")
    tool_calls: list[dict[str, Any]] = []
    if isinstance(raw_calls, list):
        for index, raw_call in enumerate(raw_calls):
            if not isinstance(raw_call, Mapping):
                continue
            arguments = raw_call.get("arguments", {})
            if not isinstance(arguments, str):
                arguments = json.dumps(
                    {} if arguments is None else arguments, ensure_ascii=False
                )
            function = raw_call.get("function")
            name = raw_call.get("name")
            if name is None and isinstance(function, Mapping):
                name = function.get("name")
            tool_calls.append(
                {
                    "index": index,
                    "id": str(raw_call.get("id") or f"call_{index}"),
                    "type": "function",
                    "function": {
                        "name": str(name or ""),
                        "arguments": arguments,
                    },
                }
            )
    if tool_calls:
        chunks.append(chunk({"tool_calls": tool_calls}))

    finish_reason = reply.get("finish_reason")
    if not isinstance(finish_reason, str) or not finish_reason:
        finish_reason = "tool_calls" if tool_calls else "stop"
    chunks.append(
        chunk(
            {},
            finish_reason=finish_reason,
            usage=_normalized_usage(reply.get("usage")),
        )
    )
    return chunks


def _tool_results(messages: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """把请求里的 tool 消息解析成 ``{tool_call_id: result}``。"""
    results: dict[str, Any] = {}
    for message in messages:
        if message.get("role") != "tool":
            continue
        call_id = message.get("tool_call_id")
        if not isinstance(call_id, str):
            continue
        content = message.get("content")
        try:
            payload = json.loads(content) if isinstance(content, str) else content
        except (TypeError, ValueError):
            continue
        if isinstance(payload, Mapping) and "result" in payload:
            payload = payload["result"]
        results[call_id] = payload
    return results


def _placeholder_value(
    call_id: str, path: str, results: Mapping[str, Any]
) -> Any:
    if call_id not in results:
        raise CassetteError(
            "placeholder", f"占位符引用的工具调用 {call_id!r} 不在请求消息里"
        )
    current = results[call_id]
    for segment in path.split("."):
        try:
            if isinstance(current, Mapping):
                current = current[segment]
            elif isinstance(current, list):
                current = current[int(segment)]
            else:
                raise KeyError(segment)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise CassetteError(
                "placeholder", f"占位符 {{result:{call_id}:{path}}} 找不到对应字段"
            ) from exc
    return current


def _render_value(value: Any, results: Mapping[str, Any]) -> Any:
    if isinstance(value, str):
        full = PLACEHOLDER_RE.fullmatch(value)
        if full is not None:
            return _placeholder_value(full.group(1), full.group(2), results)

        def replace(match: re.Match[str]) -> str:
            resolved = _placeholder_value(match.group(1), match.group(2), results)
            return (
                resolved
                if isinstance(resolved, str)
                else json.dumps(resolved, ensure_ascii=False, default=str)
            )

        return PLACEHOLDER_RE.sub(replace, value)
    if isinstance(value, Mapping):
        return {key: _render_value(item, results) for key, item in value.items()}
    if isinstance(value, list):
        return [_render_value(item, results) for item in value]
    return value


def render_placeholders(
    reply: Mapping[str, Any], messages: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """把应答里的 ``{{result:...}}`` 换成请求中工具结果里的值。"""
    rendered = _render_value(reply, _tool_results(messages))
    if not isinstance(rendered, dict):
        raise CassetteError("placeholder", "待渲染的应答必须是对象")
    return rendered


# --------------------------------------------------------------------------- #
# 录制时的参数模板化
# --------------------------------------------------------------------------- #


def _walk_tool_result(
    value: Any, path: tuple[str, ...], found: dict[str, tuple[str, str]], call_id: str
) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            _walk_tool_result(item, (*path, str(key)), found, call_id)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _walk_tool_result(item, (*path, str(index)), found, call_id)
    elif isinstance(value, str) and UUID_RE.match(value):
        if path:
            found.setdefault(value, (call_id, ".".join(path)))


def _uuid_locations(messages: Sequence[Mapping[str, Any]]) -> dict[str, tuple[str, str]]:
    """收集请求里前序工具结果中的 UUID 及其路径，作为模板化候选。"""
    found: dict[str, tuple[str, str]] = {}
    for message in messages:
        if message.get("role") != "tool":
            continue
        call_id = message.get("tool_call_id")
        if not isinstance(call_id, str):
            continue
        content = message.get("content")
        try:
            payload = json.loads(content) if isinstance(content, str) else content
        except (TypeError, ValueError):
            continue
        if isinstance(payload, Mapping) and "result" in payload:
            payload = payload["result"]
        _walk_tool_result(payload, (), found, call_id)
    return found


def _replace_uuids(value: Any, locations: Mapping[str, tuple[str, str]]) -> Any:
    if isinstance(value, str):
        location = locations.get(value)
        if location is not None:
            call_id, path = location
            return f"{{{{result:{call_id}:{path}}}}}"
        return value
    if isinstance(value, Mapping):
        return {key: _replace_uuids(item, locations) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_uuids(item, locations) for item in value]
    return value


def templatize_chunks(
    chunks: list[dict[str, Any]], messages: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """把工具参数里来自前序工具结果的 UUID 换成占位符。

    参数在流式分片里可能被拆成多段，所以先拼回完整 JSON，替换后写回最后一段
    参数分片、清空其余片段；返回新的分片列表，不修改入参。
    """
    copied = copy.deepcopy(chunks)
    locations = _uuid_locations(messages)
    if not locations:
        return copied

    # index -> 参数片段位置；id/name 按流式约定取最先出现的非空值即可。
    fragments: dict[int, list[tuple[int, int, int]]] = {}
    merged: dict[int, dict[str, Any]] = {}
    for chunk_index, chunk in enumerate(copied):
        choices = chunk.get("choices")
        if not isinstance(choices, list):
            continue
        for choice_index, choice in enumerate(choices):
            if not isinstance(choice, Mapping):
                continue
            delta = choice.get("delta")
            if not isinstance(delta, Mapping):
                continue
            raw_calls = delta.get("tool_calls")
            if not isinstance(raw_calls, list):
                continue
            for position, raw_call in enumerate(raw_calls):
                if not isinstance(raw_call, Mapping):
                    continue
                try:
                    index = int(raw_call.get("index", position))
                except (TypeError, ValueError):
                    index = position
                info = merged.setdefault(
                    index,
                    {"id": "", "name": "", "arguments": ""},
                )
                call_id = raw_call.get("id")
                if isinstance(call_id, str) and call_id:
                    info["id"] = info["id"] or call_id
                function = raw_call.get("function")
                if not isinstance(function, Mapping):
                    continue
                name = function.get("name")
                if isinstance(name, str) and name:
                    info["name"] = info["name"] or name
                arguments = function.get("arguments")
                if isinstance(arguments, str):
                    info["arguments"] += arguments
                    fragments.setdefault(index, []).append(
                        (chunk_index, choice_index, position)
                    )

    for index, info in merged.items():
        raw_arguments = info["arguments"]
        if not raw_arguments:
            continue
        try:
            arguments = json.loads(raw_arguments)
        except (TypeError, ValueError):
            continue
        if not isinstance(arguments, Mapping):
            continue
        replaced = _replace_uuids(arguments, locations)
        if replaced == arguments:
            continue
        positions = fragments.get(index) or []
        if not positions:
            continue
        serialized = json.dumps(replaced, ensure_ascii=False)
        for chunk_index, choice_index, position in positions[:-1]:
            copied[chunk_index]["choices"][choice_index]["delta"]["tool_calls"][
                position
            ]["function"]["arguments"] = ""
        last_chunk, last_choice, last_position = positions[-1]
        copied[last_chunk]["choices"][last_choice]["delta"]["tool_calls"][
            last_position
        ]["function"]["arguments"] = serialized
    return copied


# --------------------------------------------------------------------------- #
# SSE
# --------------------------------------------------------------------------- #


def sse_body(chunks: Sequence[Mapping[str, Any]]) -> bytes:
    """把分片列表编码成 SSE 正文，末尾带 ``[DONE]``。"""
    parts = [
        "data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n" for chunk in chunks
    ]
    parts.append("data: [DONE]\n\n")
    return "".join(parts).encode("utf-8")


def parse_sse_body(body: bytes) -> list[dict[str, Any]]:
    """解析 SSE 正文成 JSON 对象列表；忽略注释、事件名与 ``[DONE]``。"""
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CassetteError("format", f"SSE 正文不是合法 UTF-8：{exc}") from exc
    events: list[dict[str, Any]] = []
    data_lines: list[str] = []

    def flush() -> None:
        if not data_lines:
            return
        data = "\n".join(data_lines)
        data_lines.clear()
        if data == "[DONE]":
            return
        try:
            value = json.loads(data)
        except json.JSONDecodeError as exc:
            raise CassetteError("format", f"SSE 数据不是合法 JSON：{exc}") from exc
        if not isinstance(value, dict):
            raise CassetteError("format", "SSE 数据不是对象")
        events.append(value)

    for line in text.splitlines():
        if not line:
            flush()
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
            continue
        if line.startswith(("event:", "id:", "retry:")):
            continue
        raise CassetteError("format", f"无法识别的 SSE 行：{line[:80]}")
    flush()
    return events


# --------------------------------------------------------------------------- #
# 传输层
# --------------------------------------------------------------------------- #


def _message_tool_ids(call: CassetteCall) -> set[str]:
    """一条录制应答里的工具调用 id 集合。"""
    ids: set[str] = set()
    if call.reply is not None:
        raw_calls = call.reply.get("tool_calls")
        if isinstance(raw_calls, list):
            for raw_call in raw_calls:
                if isinstance(raw_call, Mapping) and isinstance(
                    raw_call.get("id"), str
                ):
                    ids.add(raw_call["id"])
    for chunk in call.chunks or []:
        choices = chunk.get("choices")
        if not isinstance(choices, list):
            continue
        for choice in choices:
            if not isinstance(choice, Mapping):
                continue
            delta = choice.get("delta")
            if not isinstance(delta, Mapping):
                continue
            raw_calls = delta.get("tool_calls")
            if not isinstance(raw_calls, list):
                continue
            for raw_call in raw_calls:
                if isinstance(raw_call, Mapping) and isinstance(
                    raw_call.get("id"), str
                ):
                    ids.add(raw_call["id"])
    return ids


def _json_response(
    status: int, payload: Mapping[str, Any], headers: Mapping[str, str] | None = None
) -> httpx.Response:
    return httpx.Response(
        status,
        content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"content-type": "application/json", **dict(headers or {})},
    )


def _error_response(message: str, code: str) -> httpx.Response:
    return _json_response(400, {"error": {"message": message, "code": code}})


def _sse_response(chunks: Sequence[Mapping[str, Any]]) -> httpx.Response:
    return httpx.Response(
        200,
        content=sse_body(chunks),
        headers={"content-type": "text/event-stream"},
    )


def _recorded_error_response(error: Mapping[str, Any]) -> httpx.Response:
    try:
        status = int(error.get("status") or 500)
    except (TypeError, ValueError):
        status = 500
    headers = error.get("headers")
    response_headers = {
        str(key): str(value)
        for key, value in (headers.items() if isinstance(headers, Mapping) else ())
    }
    body = error.get("body")
    if isinstance(body, bytes):
        content = body
    elif isinstance(body, str):
        content = body.encode("utf-8")
    else:
        content = json.dumps(
            body if body is not None else {}, ensure_ascii=False
        ).encode("utf-8")
        response_headers.setdefault("content-type", "application/json")
    return httpx.Response(status, content=content, headers=response_headers)


def _response_headers(response: httpx.Response) -> dict[str, str]:
    content_type = response.headers.get("content-type")
    return {"content-type": content_type} if content_type else {}


class _BaseEvalTransport(httpx.AsyncBaseTransport):
    """三个传输层共用的请求解析、计数与错误记录。"""

    def __init__(
        self, *, max_calls: int, channel_override: Channel | None = None
    ) -> None:
        self.max_calls = max(0, int(max_calls))
        self.channel_override = channel_override
        self.requests: list[CapturedRequest] = []
        self.warnings: list[str] = []
        self.agent_calls = 0
        #: 本次传输层记下的第一个错误；runner 在用例结束时读取。
        self.error: CassetteError | None = None

    def _capture(
        self, request: httpx.Request
    ) -> tuple[Mapping[str, Any], Channel]:
        try:
            body = json.loads(request.content)
        except (TypeError, ValueError) as exc:
            raise CassetteError("format", f"请求体不是合法 JSON：{exc}") from exc
        if not isinstance(body, Mapping):
            raise CassetteError("format", "请求体不是 JSON 对象")
        channel = self.channel_override or classify_request(body)
        self.requests.append(summarize_request(body, channel))
        return body, channel

    def _fail(self, code: str, message: str) -> CassetteError:
        if self.error is None:
            self.error = CassetteError(code, message)
        return self.error

    def _check_limit(self, channel: Channel) -> httpx.Response | None:
        if channel != "agent":
            return None
        self.agent_calls += 1
        if self.agent_calls <= self.max_calls:
            return None
        self._fail("limit", f"超过 {self.max_calls} 次模型调用")
        return _error_response("eval max_model_calls reached", "eval_limit")


class CassetteTransport(_BaseEvalTransport):
    """回放：按录制文件作答，不访问网络。"""

    def __init__(
        self,
        cassette: Cassette,
        *,
        max_calls: int,
        strict: bool = False,
        channel_override: Channel | None = None,
    ) -> None:
        super().__init__(max_calls=max_calls, channel_override=channel_override)
        self.cassette = cassette
        self.strict = strict
        #: tool call id → 发出它的那条应答的 id 集合（同一批多个调用共享同一个集合）。
        self._issued: dict[str, set[str]] = {}
        self._seq = 0

    def _warn_drift(
        self, call: CassetteCall, body: Mapping[str, Any], channel: Channel
    ) -> httpx.Response | None:
        if call.request is None:
            return None
        current = fingerprint_request(body)
        drifted: list[str] = []
        if call.request.tools_sha != current.tools_sha:
            drifted.append("工具表")
        if call.request.system_sha != current.system_sha:
            drifted.append("系统提示")
        if not drifted:
            return None
        message = f"{channel} 请求的{'与'.join(drifted)}与录制时不同"
        self.warnings.append(message)
        if not self.strict:
            return None
        self._fail("mismatch", message)
        return _error_response(message, "eval_mismatch")

    def _check_tool_ids(
        self,
        call: CassetteCall,
        body: Mapping[str, Any],
        channel: Channel,
    ) -> httpx.Response | None:
        messages = _messages(body)
        trailing: list[str] = []
        for message in reversed(messages):
            if message.get("role") != "tool":
                break
            call_id = message.get("tool_call_id")
            if isinstance(call_id, str):
                trailing.append(call_id)
        if trailing:
            expected: set[str] = set()
            for call_id in trailing:
                expected |= self._issued.get(call_id, set())
            if expected != set(trailing):
                message = (
                    f"{channel} 请求的工具结果 id {sorted(set(trailing))} "
                    f"与已发出的调用 {sorted(expected)} 不一致"
                )
                self._fail("mismatch", message)
                return _error_response(message, "eval_mismatch")
        reply_ids = _message_tool_ids(call)
        for call_id in reply_ids:
            self._issued[call_id] = set(reply_ids)
        return None

    def _default_reply(
        self, channel: Channel, body: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        if channel == "probe":
            if _is_probe_tool(body):
                return {
                    "tool_calls": [
                        {
                            "id": _PROBE_CALL_ID,
                            "name": "capability_probe",
                            "arguments": {},
                        }
                    ],
                    "finish_reason": "tool_calls",
                }
            return {"text": VISION_PROBE_REPLY}
        if channel == "aux":
            return {"text": DEFAULT_AUX_TEXT}
        return None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        try:
            body, channel = self._capture(request)
        except CassetteError as exc:
            self._fail(exc.code, str(exc))
            return _error_response(str(exc), f"eval_{exc.code}")

        if channel in ("agent", "judge"):
            limited = self._check_limit(channel)
            if limited is not None:
                return limited
            call = self.cassette.pop(channel)
            if call is None:
                message = f"录制文件没有剩余的 {channel} 应答"
                self._fail("exhausted", message)
                return _error_response(message, "eval_exhausted")
            mismatch = self._check_tool_ids(call, body, channel)
            if mismatch is None:
                mismatch = self._warn_drift(call, body, channel)
            if mismatch is not None:
                return mismatch
            if call.error is not None:
                return _recorded_error_response(call.error)
            self._seq += 1
            if call.chunks is not None:
                chunks = call.chunks
            else:
                chunks = expand_reply(
                    render_placeholders(call.reply or {}, _messages(body)),
                    model=self.cassette.header.model,
                    seq=self._seq,
                )
            return _sse_response(chunks)

        # probe / aux：有录制就用，没有就给固定应答。
        call = self.cassette.pop(channel)
        if call is None:
            reply = self._default_reply(channel, body)
            self._seq += 1
            chunks = expand_reply(
                reply or {}, model=self.cassette.header.model, seq=self._seq
            )
            return _sse_response(chunks)
        mismatch = self._warn_drift(call, body, channel)
        if mismatch is not None:
            return mismatch
        if call.error is not None:
            return _recorded_error_response(call.error)
        self._seq += 1
        if call.chunks is not None:
            chunks = call.chunks
        else:
            chunks = expand_reply(
                render_placeholders(call.reply or {}, _messages(body)),
                model=self.cassette.header.model,
                seq=self._seq,
            )
        return _sse_response(chunks)

    def check_unused(self) -> None:
        """agent 通道还有剩余录制时算错（系统比录制少调了模型）。"""
        remaining = self.cassette.remaining("agent")
        if remaining:
            raise CassetteError(
                "unused", f"录制里还有 {remaining} 条 agent 应答没有被用到"
            )


class RecordingTransport(_BaseEvalTransport):
    """录制：转发给真实端点，并把应答分片写进新录制文件。"""

    def __init__(
        self,
        cassette: Cassette,
        *,
        max_calls: int,
        inner: httpx.AsyncBaseTransport | None = None,
        channel_override: Channel | None = None,
    ) -> None:
        super().__init__(max_calls=max_calls, channel_override=channel_override)
        self.cassette = cassette
        self.inner = inner if inner is not None else httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        try:
            body, channel = self._capture(request)
        except CassetteError as exc:
            self._fail(exc.code, str(exc))
            return _error_response(str(exc), f"eval_{exc.code}")

        limited = self._check_limit(channel)
        if limited is not None:
            return limited
        request_fingerprint = fingerprint_request(body)
        response = await self.inner.handle_async_request(request)
        content = await response.aread()
        headers = _response_headers(response)
        content_type = headers.get("content-type", "")
        if 200 <= response.status_code < 300 and "text/event-stream" in content_type:
            chunks = templatize_chunks(parse_sse_body(content), _messages(body))
            self.cassette.append(
                CassetteCall(
                    channel=channel,
                    request=request_fingerprint,
                    chunks=chunks,
                )
            )
        else:
            self.cassette.append(
                CassetteCall(
                    channel=channel,
                    request=request_fingerprint,
                    error={
                        "status": response.status_code,
                        "headers": headers,
                        "body": content.decode("utf-8", errors="replace"),
                    },
                )
            )
        return httpx.Response(
            response.status_code, content=content, headers=headers, request=request
        )

    async def aclose(self) -> None:
        await self.inner.aclose()


class CountingTransport(_BaseEvalTransport):
    """实测：转发给真实端点，只计数并记录请求摘要，不写录制。"""

    def __init__(
        self,
        *,
        max_calls: int,
        inner: httpx.AsyncBaseTransport | None = None,
        channel_override: Channel | None = None,
    ) -> None:
        super().__init__(max_calls=max_calls, channel_override=channel_override)
        self.inner = inner if inner is not None else httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        try:
            _, channel = self._capture(request)
        except CassetteError as exc:
            self._fail(exc.code, str(exc))
            return _error_response(str(exc), f"eval_{exc.code}")
        limited = self._check_limit(channel)
        if limited is not None:
            return limited
        return await self.inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self.inner.aclose()

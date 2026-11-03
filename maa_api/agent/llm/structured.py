"""结构化输出（M67 规格 §4.2.7、§4.5.3）。

业务 schema 都是 pydantic 模型，注册在 :data:`SCHEMAS` 里；:func:`json_schema_for`
把模型导出成发给上游的 JSON Schema。如果档案支持 JSON Schema，第一次请求用
``response_format`` 约束上游；否则（或上游以 400/422 拒绝该参数时）把同一份
JSON Schema 写进提示词。两种模式都在本地用 ``model_validate`` 校验，失败时把字段
路径与说明带回模型重试一次，仍失败抛 ``STRUCTURED_OUTPUT_INVALID``（HTTP 502）。

上游少数字段（``delta.content`` 之外的思考内容、工具调用）在这里不关心：结构化
输出只需要正文与 usage。每次成功收流都记一笔成本账本，重试的两次分别记账。
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from maa_api.agent.llm.budget import check_budget
from maa_api.agent.llm.cost import CostLedger
from maa_api.agent.llm.roles import ModelRouter
from maa_api.agent.llm.usage import Usage, normalize_usage
from maa_api.domain.errors import AppError, ErrorCode

__all__ = [
    "SCHEMAS",
    "STRUCTURED_PROMPT",
    "STRUCTURED_RETRY_PROMPT",
    "StructuredResult",
    "complete_structured",
    "extract_json_object",
    "get_schema",
    "json_schema_for",
    "register_schema",
]

#: 提示词模式的约束文案。模型可见文本，逐字照规格，不要改标点或换行。
STRUCTURED_PROMPT = "只输出一个 JSON 对象：不要输出其他文字，不要用代码块包裹。这个对象必须符合下面的 JSON Schema：\n{schema}"
#: 第一次输出不合法时的重试文案；``errors`` 是「字段路径: 说明」的行。
STRUCTURED_RETRY_PROMPT = "上一次的输出不符合要求：\n{errors}\n重新输出完整的 JSON 对象，只输出 JSON。"

#: schema 名 → pydantic 模型。M70 起的业务 schema 在模块末尾调用 :func:`register_schema`。
SCHEMAS: dict[str, type[BaseModel]] = {}

#: 重试提示里最多回传几条字段错误：太多会淹没真正的错，也浪费 token。
MAX_REPORTED_ERRORS = 5


def register_schema(name: str, model: type[BaseModel]) -> None:
    """注册业务 schema；重名抛 ``ValueError``（注册发生在 import 时，重名是编码缺陷）。"""
    if name in SCHEMAS:
        raise ValueError(f"schema 名称重复: {name}")
    SCHEMAS[name] = model


def get_schema(name: str) -> type[BaseModel]:
    """按名字取模型；未注册时沿用字典的 ``KeyError``。"""
    return SCHEMAS[name]


def json_schema_for(name: str) -> dict[str, Any]:
    """导出校验用的 JSON Schema；``mode="validation"`` 不包含默认值等序列化信息。"""
    return get_schema(name).model_json_schema(mode="validation")


@dataclass(frozen=True, slots=True)
class StructuredResult:
    """一次成功的结构化输出。"""

    value: BaseModel
    #: 所有成功收流的尝试之和（第一次不合法时包含那一次的花费）。
    usage: Usage
    #: 产生最终结果的是第几次尝试：1 或 2。
    attempts: int
    mode: Literal["json_schema", "prompt"]


def extract_json_object(text: str) -> Any:
    """从模型正文里取出第一个 JSON 对象并解析。

    容忍提示词模式常见的包装：首尾空白、````` ````` 围栏、对象前后的说明文字。
    取第一个 ``{`` 到最后一个 ``}`` 之间的文本交给 ``json.loads``；找不到花括号或
    解析失败都统一抛 ``ValueError("没有找到 JSON 对象")``，调用方只按「不合法」处理。
    """
    candidate = text.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if len(lines) >= 2:
            lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            candidate = "\n".join(lines).strip()
        else:
            candidate = candidate[3:].strip()

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start < 0 or end < start:
        raise ValueError("没有找到 JSON 对象")
    try:
        return json.loads(candidate[start : end + 1])
    except json.JSONDecodeError:
        raise ValueError("没有找到 JSON 对象") from None


def _append_prompt(messages: list[dict[str, Any]], prompt: str) -> None:
    """把提示词空一行后并入最后一条 user 消息；没有 user 消息就追加一条。"""
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, list):
            # 多模态消息：保留原有图片，再补一个文本块。
            content.append({"type": "text", "text": prompt})
        elif isinstance(content, str) and content:
            message["content"] = f"{content}\n\n{prompt}"
        else:
            message["content"] = prompt
        return
    messages.append({"role": "user", "content": prompt})


def _error_lines(exc: Exception) -> list[str]:
    """把校验失败压成最多 :data:`MAX_REPORTED_ERRORS` 条「字段路径: 说明」。"""
    if isinstance(exc, ValidationError):
        lines: list[str] = []
        for item in exc.errors()[:MAX_REPORTED_ERRORS]:
            location = ".".join(str(part) for part in item.get("loc", ()))
            lines.append(f"{location or '(root)'}: {item.get('msg', '')}")
        return lines
    return [str(exc)]


async def _stream_text(
    client: Any,
    model: str,
    messages: Sequence[dict[str, Any]],
    *,
    response_format: Mapping[str, Any] | None = None,
    max_tokens: int | None = None,
) -> tuple[str, Usage]:
    """收完一条流，只拼 ``delta.content`` 与最后一份非空 usage。"""
    options: dict[str, Any] = {}
    if response_format is not None:
        options["response_format"] = response_format
    if max_tokens is not None:
        options["max_tokens"] = max_tokens

    text_parts: list[str] = []
    raw_usage: dict[str, Any] = {}
    async for chunk in client.stream_chat_completion(model, messages, [], **options):
        if not isinstance(chunk, Mapping):
            raise AppError(ErrorCode.LLM_REQUEST_FAILED, "LLM 返回了无效的响应块")
        for choice in chunk.get("choices") or []:
            if not isinstance(choice, Mapping):
                continue
            delta = choice.get("delta") or {}
            if not isinstance(delta, Mapping):
                continue
            content = delta.get("content")
            if isinstance(content, str) and content:
                text_parts.append(content)
        usage = chunk.get("usage")
        if isinstance(usage, Mapping) and usage:
            raw_usage = dict(usage)
    return "".join(text_parts), normalize_usage(raw_usage)


async def complete_structured(
    *,
    router: ModelRouter,
    ledger: CostLedger,
    role: str,
    schema_name: str,
    messages: list[dict[str, Any]],
    agent_name: str,
    run_id: str | None = None,
    session_id: str | None = None,
    max_tokens: int | None = None,
) -> StructuredResult:
    """按角色调用模型并得到一个通过本地校验的 pydantic 对象。

    流程见规格 §4.5.3：解析档案 → 取 schema → 选模式 → 最多两次尝试。第二次尝试
    要么来自第一次输出不合法（带上原文与字段错误），要么来自上游以 400/422 拒绝
    ``response_format``（改为提示词模式）。预算在每次调用前检查，成功的调用逐次记账。
    """
    profile = await router.resolve(role)
    model_cls = get_schema(schema_name)
    schema = json_schema_for(schema_name)
    mode: Literal["json_schema", "prompt"] = (
        "json_schema" if profile.supports_json_schema is True else "prompt"
    )
    msgs = copy.deepcopy(messages)
    prompt = STRUCTURED_PROMPT.format(schema=json.dumps(schema, ensure_ascii=False))
    if mode == "prompt":
        _append_prompt(msgs, prompt)

    total_usage = Usage()
    for attempt in (1, 2):
        await check_budget(ledger, run_id=run_id)
        handle = router.client_for(profile)
        response_format: Mapping[str, Any] | None = None
        if mode == "json_schema":
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "schema": schema,
                    # pydantic 导出的可选字段与 OpenAI 严格模式冲突；严格性由本地校验保证。
                    "strict": False,
                },
            }
        try:
            text, attempt_usage = await _stream_text(
                handle.client,
                profile.model,
                msgs,
                response_format=response_format,
                max_tokens=max_tokens,
            )
        except AppError as exc:
            # 上游不认 response_format 时只降级一次；第二次仍失败就原样抛出。
            if (
                attempt == 1
                and mode == "json_schema"
                and exc.code == ErrorCode.LLM_REQUEST_FAILED
                and (exc.details or {}).get("upstream_status") in (400, 422)
            ):
                mode = "prompt"
                _append_prompt(msgs, prompt)
                continue
            raise
        finally:
            await handle.aclose()

        await ledger.record(
            run_id=run_id,
            session_id=session_id,
            agent_name=agent_name,
            role=role,
            profile=profile,
            usage=attempt_usage,
        )
        total_usage = total_usage + attempt_usage

        try:
            value = model_cls.model_validate(extract_json_object(text))
        except (ValueError, ValidationError) as exc:
            errors = _error_lines(exc)
            if attempt == 2:
                raise AppError(
                    ErrorCode.STRUCTURED_OUTPUT_INVALID,
                    "模型两次都没有给出符合要求的结构化结果",
                    {"schema": schema_name, "errors": errors},
                ) from exc
            msgs.append({"role": "assistant", "content": text})
            msgs.append(
                {
                    "role": "user",
                    "content": STRUCTURED_RETRY_PROMPT.format(
                        errors="\n".join(errors)
                    ),
                }
            )
            continue

        return StructuredResult(
            value=value, usage=total_usage, attempts=attempt, mode=mode
        )

    raise AssertionError("complete_structured 的两次尝试既没有返回也没有抛错")

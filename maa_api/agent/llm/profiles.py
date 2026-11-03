"""模型档案：请求/视图模型、能力探查与 ``ModelProfileService``（M67 规格 §4.2.3）。

``default`` 档案不入表：它由 ``llm.base_url`` / ``llm.api_key`` / ``llm.model``
合成，只读，能力探查的结果只存在进程内存（:attr:`ModelProfileService._default_probe`）。
其余档案存 ``model_profile`` 表，API 密钥用 ``MAA_LLM_ENCRYPTION_KEY`` 加密。

能力探查分四项：

- 工具调用与视觉理解：从 v2 ``AgentRuntime._probe_capabilities`` 原样搬出，请求
  消息、探针工具、64×64 蓝色 PNG 与判定逻辑不变；
- JSON Schema 结构化输出：发一次带 ``response_format`` 的请求，能解析出
  ``{"ok": true}`` 即支持，上游 400/422 明确不支持；
- 尾部位置：按 docs/v3/06 §2.4 的两步探查决定 ``tail_system`` / ``tail_user``。

本模块不导入 ``maa_api.agent.context.tokens``：``TokenEstimator`` 属于 T7，探查
只在真正需要时通过 :func:`_token_estimator` 延迟导入（测试可替换该函数）。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.agent.harness.chunks import chunk_tool_calls, merge_tool_call_deltas
from maa_api.agent.llm.client import OpenAICompatibleClient
from maa_api.agent.secrets import (
    LLMEncryptionKeyInvalid,
    decrypt_api_key,
    encrypt_api_key,
)
from maa_api.db.models import ModelProfile, utcnow
from maa_api.db.repositories.model_profile import ModelProfileRepository
from maa_api.domain.enums import (
    DynamicContextPosition,
    ModelProfileKind,
    ModelProvider,
    ModelRole,
)
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import Settings, get_settings

if TYPE_CHECKING:  # pragma: no cover - 只用于类型标注，运行期由 T7 提供
    from maa_api.agent.context.tokens import TokenEstimator

__all__ = [
    "DEFAULT_PROFILE_NAME",
    "PROBE_FAILURE_TTL_SECONDS",
    "PROFILE_NAME_PATTERN",
    "TAIL_PROBE_FILLER",
    "ModelProfileCreate",
    "ModelProfileList",
    "ModelProfileService",
    "ModelProfileUpdate",
    "ModelProfileView",
    "ProbeResult",
    "ProbeResultView",
    "ResolvedProfile",
    "probe_json_schema",
    "probe_tail_position",
    "probe_tool_calling",
    "probe_vision",
]

DEFAULT_PROFILE_NAME = "default"
PROFILE_NAME_PATTERN = r"^[a-z0-9][a-z0-9-]{1,47}$"
PROBE_FAILURE_TTL_SECONDS = 60.0
#: 尾部位置探查的固定前缀正文。约 1 400 token，够触发服务端的前缀缓存。
TAIL_PROBE_FILLER = "前缀缓存探查用的固定文本，不需要回答。" * 120
#: 尾部位置探查两次请求之间的间隔：给服务端留出写入缓存的时间。
TAIL_PROBE_SLEEP_SECONDS = 1.0
#: 判定 ``tail_system`` 的最低命中量：前缀估算值的一半（docs/v3/06 §2.4）。
TAIL_PROBE_HIT_RATIO = 0.5

#: 视觉探查用的 64×64 纯蓝 PNG（132 字节）。1×1 的 PNG 会被上游以
#: "You have uploaded an unsupported image" 当成无效图片拒收，从而把支持视觉的模型
#: 误判成不支持；也不能只用「有没有返回文本」判定，因为不接受图片的模型会回一句
#: "Unable to view image content" 而照样有文本。所以这里用能被识别的纯色图，并要求
#: 模型答出颜色。
_VISION_PROBE_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAS0lEQVR42u3PQQkAAAgAsetf"
    "WiP4FgYrsGqeExAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQ"
    "EBA4LMf88OL0EKXAAAAAAElFTkSuQmCC"
)
_VISION_PROBE_PROMPT = "What color is this image? Answer with one word."
_VISION_PROBE_MARKER = "blue"

#: 工具调用探查的探针工具：模型只要回一个同名 function call 就算支持。
_PROBE_TOOL = {
    "type": "function",
    "function": {
        "name": "capability_probe",
        "description": "No-op capability probe. Return this function call; it is never executed.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
}
_TOOL_PROBE_MESSAGE = (
    "Call capability_probe with an empty object to confirm function calling support."
)

#: JSON Schema 探查的请求体（规格 §4.2.3）。strict 与 additionalProperties 都要带上，
#: 否则不支持 strict 的上游可能返回 400，被误判成「不支持结构化输出」。
_JSON_SCHEMA_PROBE_PROMPT = "Return a JSON object with ok set to true."
_JSON_SCHEMA_PROBE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "probe",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
    },
}


@dataclass(frozen=True, slots=True)
class ResolvedProfile:
    """进程内使用的完整档案（API 密钥是明文，``repr`` 里不出现）。"""

    name: str
    kind: str
    provider: str
    base_url: str
    api_key: str = field(repr=False)
    model: str
    context_window: int | None
    max_output_tokens: int | None
    supports_vision: bool | None
    supports_tools: bool
    supports_json_schema: bool | None
    dynamic_context_position: str
    price_input: float | None
    price_cached_input: float | None
    price_output: float | None
    currency: str
    embedding_dim: int | None
    local_model_path: str | None
    readonly: bool

    @property
    def key(self) -> tuple[str, str]:
        """按「档案名 + 模型名」区分上下文估算与缓存口径。"""
        return (self.name, self.model)

    def window(self, fallback: int) -> int:
        """档案没写上下文窗口时用调用方给的兜底值。"""
        return self.context_window or fallback


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """一次能力探查的结论；``None`` 表示这一项因上游失败没能判定。"""

    supports_tools: bool | None
    supports_vision: bool | None
    supports_json_schema: bool | None
    dynamic_context_position: str | None
    latency_ms: int
    #: 至少一项因上游失败没能判定（决定自动探查的失败 TTL）。
    failed: bool


class ModelProfileView(BaseModel):
    """档案的对外视图：密钥只给 ``"***"``，``default`` 的 id 与时间戳为 null。"""

    id: str | None
    name: str
    kind: Literal["chat", "embedding"]
    provider: Literal["openai_compatible", "local_onnx"]
    base_url: str | None
    api_key: Literal["***"] | None
    model: str
    context_window: int | None
    max_output_tokens: int | None
    supports_vision: bool | None
    supports_tools: bool
    supports_json_schema: bool | None
    dynamic_context_position: Literal["tail_system", "tail_user"]
    price_input: float | None
    price_cached_input: float | None
    price_output: float | None
    currency: str
    embedding_dim: int | None
    local_model_path: str | None
    enabled: bool
    readonly: bool
    created_at: str | None
    updated_at: str | None


class ModelProfileList(BaseModel):
    """``GET /api/agent/model-profiles`` 的响应体。"""

    items: list[ModelProfileView]


class ModelProfileCreate(BaseModel):
    """新建档案；跨字段约束见 :meth:`_check_connection`。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=PROFILE_NAME_PATTERN)
    kind: Literal["chat", "embedding"] = "chat"
    provider: Literal["openai_compatible", "local_onnx"] = "openai_compatible"
    base_url: str | None = Field(default=None, max_length=255)
    api_key: str | None = Field(default=None, max_length=512)
    model: str = Field(min_length=1, max_length=128)
    context_window: int | None = Field(default=None, ge=1024, le=10_000_000)
    max_output_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    supports_tools: bool = True
    dynamic_context_position: Literal["tail_system", "tail_user"] = "tail_system"
    price_input: float | None = Field(default=None, ge=0, le=100_000)
    price_cached_input: float | None = Field(default=None, ge=0, le=100_000)
    price_output: float | None = Field(default=None, ge=0, le=100_000)
    currency: str = Field(default="CNY", pattern=r"^[A-Z]{3}$")
    embedding_dim: int | None = Field(default=None, ge=1, le=65536)
    local_model_path: str | None = Field(default=None, max_length=255)
    enabled: bool = True

    @model_validator(mode="after")
    def _check_connection(self) -> ModelProfileCreate:
        if self.provider == ModelProvider.OPENAI_COMPATIBLE:
            if not self.base_url:
                raise ValueError("OpenAI 兼容档案必须填写 base_url")
            if not self.base_url.startswith(("http://", "https://")):
                raise ValueError("base_url 必须以 http:// 或 https:// 开头")
            if self.local_model_path:
                raise ValueError("OpenAI 兼容档案不能填写 local_model_path")
        else:
            if self.kind != ModelProfileKind.EMBEDDING:
                raise ValueError("local_onnx 档案的 kind 必须是 embedding")
            if self.base_url or self.api_key:
                raise ValueError("local_onnx 档案不能填写 base_url 或 api_key")
            if not self.local_model_path:
                raise ValueError("local_onnx 档案必须填写 local_model_path")
        if self.kind == ModelProfileKind.EMBEDDING and self.embedding_dim is None:
            raise ValueError("向量档案必须填写 embedding_dim")
        return self


class ModelProfileUpdate(BaseModel):
    """修改档案；字段全部可选，实际改哪些字段看 ``model_fields_set``。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["chat", "embedding"] | None = None
    provider: Literal["openai_compatible", "local_onnx"] | None = None
    base_url: str | None = Field(default=None, max_length=255)
    #: ``"***"`` 表示不改，``""`` 表示清除，其他值加密后替换。
    api_key: str | None = Field(default=None, max_length=512)
    model: str | None = Field(default=None, min_length=1, max_length=128)
    context_window: int | None = Field(default=None, ge=1024, le=10_000_000)
    max_output_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    supports_tools: bool | None = None
    dynamic_context_position: Literal["tail_system", "tail_user"] | None = None
    price_input: float | None = Field(default=None, ge=0, le=100_000)
    price_cached_input: float | None = Field(default=None, ge=0, le=100_000)
    price_output: float | None = Field(default=None, ge=0, le=100_000)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    embedding_dim: int | None = Field(default=None, ge=1, le=65536)
    local_model_path: str | None = Field(default=None, max_length=255)
    enabled: bool | None = None


class ProbeResultView(BaseModel):
    """``POST /api/agent/model-profiles/{name}/probe`` 的响应体。"""

    supports_vision: bool | None
    supports_tools: bool | None
    supports_json_schema: bool | None
    latency_ms: int


# ---------------------------------------------------------------------------
# 时间与视图转换
# ---------------------------------------------------------------------------


def _monotonic() -> float:
    """单调时钟；单独成函数是为了测试能替换失败 TTL 的计时。"""
    return time.monotonic()


def _utc_z(value: datetime | None) -> str | None:
    """把库里的 naive UTC 时间渲染成 ``...Z``；``None`` 原样返回。"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _view_from_row(row: ModelProfile) -> ModelProfileView:
    return ModelProfileView(
        id=row.id,
        name=row.name,
        kind=row.kind,
        provider=row.provider,
        base_url=row.base_url,
        api_key="***" if row.api_key_encrypted else None,
        model=row.model,
        context_window=row.context_window,
        max_output_tokens=row.max_output_tokens,
        supports_vision=row.supports_vision,
        supports_tools=row.supports_tools,
        supports_json_schema=row.supports_json_schema,
        dynamic_context_position=row.dynamic_context_position,
        price_input=row.price_input,
        price_cached_input=row.price_cached_input,
        price_output=row.price_output,
        currency=row.currency,
        embedding_dim=row.embedding_dim,
        local_model_path=row.local_model_path,
        enabled=row.enabled,
        readonly=False,
        created_at=_utc_z(row.created_at),
        updated_at=_utc_z(row.updated_at),
    )


def _settings_for_profile(settings: Settings, profile: ResolvedProfile) -> Settings:
    """把档案的连接三项覆盖进 ``Settings.llm``（注入的工厂只认这一种入参）。"""
    return settings.model_copy(
        update={
            "llm": settings.llm.model_copy(
                update={
                    "base_url": profile.base_url,
                    "api_key": profile.api_key,
                    "model": profile.model,
                }
            )
        }
    )


# ---------------------------------------------------------------------------
# 四项能力探查
# ---------------------------------------------------------------------------


def _chunk_text(chunk: Mapping[str, Any]) -> list[str]:
    """取一个流式 chunk 里的文本增量；结构与 v2 的视觉探查逐字段一致。"""
    return [
        choice["delta"]["content"]
        for choice in chunk.get("choices") or []
        if isinstance(choice, Mapping)
        and isinstance(choice.get("delta"), Mapping)
        and isinstance(choice["delta"].get("content"), str)
    ]


async def _collect_text_with_usage(
    client: Any,
    model: str,
    messages: Sequence[dict[str, Any]],
    *,
    tools: Sequence[dict[str, Any]] = (),
    **options: Any,
) -> tuple[str, Mapping[str, Any] | None]:
    """收完一次流，返回拼接后的文本与最后一个 usage 原始映射。"""
    parts: list[str] = []
    usage: Mapping[str, Any] | None = None
    async for chunk in client.stream_chat_completion(
        model, list(messages), list(tools), **options
    ):
        parts.extend(_chunk_text(chunk))
        raw_usage = chunk.get("usage") if isinstance(chunk, Mapping) else None
        if isinstance(raw_usage, Mapping):
            usage = raw_usage
    return "".join(parts), usage


def _upstream_status(error: AppError) -> int | None:
    """取 ``details.upstream_status``；不是整数就当没有上游响应。"""
    details = error.details if isinstance(error.details, Mapping) else {}
    status = details.get("upstream_status")
    if isinstance(status, bool) or not isinstance(status, int):
        return None
    return status


def _rejected_format(error: AppError) -> bool:
    """这次失败是不是「上游明确拒绝这个能力」：400/422 都算。"""
    return _upstream_status(error) in (400, 422)


async def probe_tool_calling(client: Any, model: str) -> tuple[bool, AppError | None]:
    """探一次 function calling：模型回了 ``capability_probe`` 才算支持。"""
    chunks: list[dict[str, Any]] = []
    try:
        async for chunk in client.stream_chat_completion(
            model,
            [{"role": "user", "content": _TOOL_PROBE_MESSAGE}],
            [_PROBE_TOOL],
        ):
            chunks.append(chunk)
        probed_calls = merge_tool_call_deltas(
            [call for chunk in chunks for call in chunk_tool_calls(chunk)]
        )
        supported = any(
            call.get("function", {}).get("name") == "capability_probe"
            for call in probed_calls
        )
        return supported, None
    except AppError as exc:
        return False, exc


async def probe_vision(client: Any, model: str) -> tuple[bool, AppError | None]:
    """探一次图片输入：模型必须答出图片颜色才算支持（只说有文本不够）。"""
    image_message = [
        {"type": "text", "text": _VISION_PROBE_PROMPT},
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{_VISION_PROBE_PNG}"},
        },
    ]
    vision_text: list[str] = []
    try:
        async for chunk in client.stream_chat_completion(
            model, [{"role": "user", "content": image_message}], []
        ):
            vision_text.extend(_chunk_text(chunk))
        return _VISION_PROBE_MARKER in "".join(vision_text).casefold(), None
    except AppError as exc:
        return False, exc


async def probe_json_schema(
    client: Any, model: str
) -> tuple[bool | None, AppError | None]:
    """探一次 JSON Schema 结构化输出。

    200 且输出能解析成 ``{"ok": true}`` → ``True``；上游 400/422 明确拒绝
    ``response_format`` → ``False``；其他失败 → ``(None, error)``。
    """
    try:
        text, _usage = await _collect_text_with_usage(
            client,
            model,
            [{"role": "user", "content": _JSON_SCHEMA_PROBE_PROMPT}],
            response_format=_JSON_SCHEMA_PROBE_FORMAT,
            max_tokens=16,
        )
    except AppError as exc:
        if _rejected_format(exc):
            return False, None
        return None, exc
    try:
        parsed = json.loads(text.strip())
    except (TypeError, ValueError):
        return False, None
    return parsed == {"ok": True}, None


def _has_cache_field(raw: Mapping[str, Any] | None) -> bool:
    """原始 usage 里有没有出现任一种缓存命中字段（只判键是否存在）。"""
    if not isinstance(raw, Mapping):
        return False
    if "prompt_cache_hit_tokens" in raw or "cached_tokens" in raw:
        return True
    details = raw.get("prompt_tokens_details")
    return isinstance(details, Mapping) and "cached_tokens" in details


def _cache_hit_tokens(raw: Mapping[str, Any] | None) -> int:
    """按 06 §2.8 的优先顺序取缓存命中量，不做 ``cached ≤ prompt`` 截断。

    ``normalize_usage`` 会把命中截到 ``prompt_tokens`` 以内；但探查的第二次请求
    可能只返回 ``prompt_cache_hit_tokens`` 与 ``prompt_cache_miss_tokens`` 里的
    前者，此时截断会把命中算成 0。阈值判断要看原始命中量。
    """
    if not isinstance(raw, Mapping):
        return 0

    def _non_negative(value: Any) -> int | None:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        return value

    hit = _non_negative(raw.get("prompt_cache_hit_tokens"))
    if hit is not None:
        return hit
    details = raw.get("prompt_tokens_details")
    if isinstance(details, Mapping):
        hit = _non_negative(details.get("cached_tokens"))
        if hit is not None:
            return hit
    return _non_negative(raw.get("cached_tokens")) or 0


async def probe_tail_position(
    client: Any, model: str, estimator: TokenEstimator
) -> tuple[str | None, AppError | None]:
    """按 docs/v3/06 §2.4 的两步探查决定动态上下文的落点。

    1. 固定前缀 P 依次追加 ``system("probe tail A")`` 与 ``system("probe tail B")``
       各发一次，间隔 1 秒；任一被上游 400/422 拒绝 → ``tail_user``。
    2. 两次都成功：第二次返回缓存字段且命中 ≥ P 的估算值一半 → ``tail_system``；
       有缓存字段但达不到 → ``tail_user``；完全不返回缓存字段 → ``tail_system``。
    """
    prefix: list[dict[str, Any]] = [
        {"role": "system", "content": "You are a probe. Reply with OK."},
        {"role": "user", "content": TAIL_PROBE_FILLER},
    ]
    try:
        await _collect_text_with_usage(
            client,
            model,
            [*prefix, {"role": "system", "content": "probe tail A"}],
            max_tokens=8,
        )
    except AppError as exc:
        if _rejected_format(exc):
            return DynamicContextPosition.TAIL_USER.value, None
        return None, exc

    await asyncio.sleep(TAIL_PROBE_SLEEP_SECONDS)

    try:
        _text, usage = await _collect_text_with_usage(
            client,
            model,
            [*prefix, {"role": "system", "content": "probe tail B"}],
            max_tokens=8,
        )
    except AppError as exc:
        if _rejected_format(exc):
            return DynamicContextPosition.TAIL_USER.value, None
        return None, exc

    if not _has_cache_field(usage):
        return DynamicContextPosition.TAIL_SYSTEM.value, None
    hit = _cache_hit_tokens(usage)
    threshold = estimator.raw_estimate(prefix) * TAIL_PROBE_HIT_RATIO
    if hit >= threshold:
        return DynamicContextPosition.TAIL_SYSTEM.value, None
    return DynamicContextPosition.TAIL_USER.value, None


def _token_estimator() -> TokenEstimator:
    """延迟导入 T7 的 ``TokenEstimator``；探查流程外没有人需要它。"""
    from maa_api.agent.context.tokens import TokenEstimator

    return TokenEstimator()


# ---------------------------------------------------------------------------
# 服务
# ---------------------------------------------------------------------------


class ModelProfileService:
    """模型档案的读写、加解密、能力探查与 ``default`` 合成。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        env: Mapping[str, str] | None = None,
        settings_provider: Callable[[], Settings] = get_settings,
        client_factory_provider: Callable[[], Callable[[Settings], Any] | None] = lambda: None,
    ) -> None:
        self._session_factory = session_factory
        self._env = env
        self._settings_provider = settings_provider
        self._client_factory_provider = client_factory_provider
        #: ``default`` 档案的能力探查结果，键是 ``(base_url, model)``。
        self._default_probe: dict[tuple[str, str], ProbeResult] = {}
        #: 自动探查的串行锁，键是 ``(name, base_url, model)``。
        self._probe_locks: dict[tuple[str, str, str], asyncio.Lock] = {}
        #: 探查失败后的冷却截止时刻（``time.monotonic()``），键同上。
        self._probe_failures: dict[tuple[str, str, str], float] = {}

    # -- 设置与加密 --------------------------------------------------------

    def _current_settings(self) -> Settings:
        return self._settings_provider()

    def _encryption_key(self) -> str:
        env = os.environ if self._env is None else self._env
        return env.get("MAA_LLM_ENCRYPTION_KEY") or ""

    def _encrypt(self, api_key: str) -> str:
        try:
            return encrypt_api_key(api_key, self._encryption_key())
        except LLMEncryptionKeyInvalid:
            raise AppError(
                ErrorCode.LLM_ENCRYPTION_KEY_INVALID,
                "缺少或无效的 LLM 加密密钥，请在环境变量 MAA_LLM_ENCRYPTION_KEY 里配置",
            ) from None

    def _decrypt(self, name: str, value: str) -> str:
        try:
            return decrypt_api_key(value, self._encryption_key())
        except LLMEncryptionKeyInvalid:
            raise AppError(
                ErrorCode.LLM_ENCRYPTION_KEY_INVALID,
                f"档案「{name}」的 API 密钥无法解密，请重新填写",
            ) from None

    # -- 合成与解析 --------------------------------------------------------

    def _synthesize_default(self, settings: Settings) -> ResolvedProfile:
        """``default`` 档案：连接与模型来自 ``llm.*``，价格与窗口都不设。"""
        base_url = settings.llm.base_url or ""
        model = settings.llm.model
        probe = self._default_probe.get((base_url, model))
        return ResolvedProfile(
            name=DEFAULT_PROFILE_NAME,
            kind=ModelProfileKind.CHAT,
            provider=ModelProvider.OPENAI_COMPATIBLE,
            base_url=base_url,
            api_key=settings.llm.api_key or "",
            model=model,
            context_window=None,
            max_output_tokens=None,
            supports_vision=probe.supports_vision if probe else None,
            supports_tools=(
                probe.supports_tools
                if probe and probe.supports_tools is not None
                else True
            ),
            supports_json_schema=probe.supports_json_schema if probe else None,
            dynamic_context_position=(
                probe.dynamic_context_position
                if probe and probe.dynamic_context_position is not None
                else DynamicContextPosition.TAIL_SYSTEM.value
            ),
            price_input=None,
            price_cached_input=None,
            price_output=None,
            currency="CNY",
            embedding_dim=None,
            local_model_path=None,
            readonly=True,
        )

    def _resolved_from_row(self, row: ModelProfile) -> ResolvedProfile:
        api_key = (
            self._decrypt(row.name, row.api_key_encrypted)
            if row.api_key_encrypted
            else ""
        )
        return ResolvedProfile(
            name=row.name,
            kind=row.kind,
            provider=row.provider,
            base_url=row.base_url or "",
            api_key=api_key,
            model=row.model,
            context_window=row.context_window,
            max_output_tokens=row.max_output_tokens,
            supports_vision=row.supports_vision,
            supports_tools=row.supports_tools,
            supports_json_schema=row.supports_json_schema,
            dynamic_context_position=row.dynamic_context_position,
            price_input=row.price_input,
            price_cached_input=row.price_cached_input,
            price_output=row.price_output,
            currency=row.currency,
            embedding_dim=row.embedding_dim,
            local_model_path=row.local_model_path,
            readonly=False,
        )

    async def _load(self, name: str) -> ResolvedProfile:
        """按名字取档案；不存在或已停用 → 404，不做连接字段检查。"""
        if name == DEFAULT_PROFILE_NAME:
            return self._synthesize_default(self._current_settings())
        async with self._session_factory() as session:
            row = await ModelProfileRepository(session).get_by_name(name)
            if row is None or not row.enabled:
                raise AppError(
                    ErrorCode.MODEL_PROFILE_NOT_FOUND,
                    f"模型档案「{name}」不存在或已停用",
                )
            return self._resolved_from_row(row)

    async def resolve(self, name: str) -> ResolvedProfile:
        """取进程内档案；缺连接字段的 OpenAI 兼容档案返回 503。"""
        profile = await self._load(name)
        if profile.provider == ModelProvider.OPENAI_COMPATIBLE and (
            not profile.base_url or not profile.api_key
        ):
            raise AppError(
                ErrorCode.LLM_NOT_CONFIGURED,
                "LLM 配置不完整，请先配置 base_url 与 API 密钥",
            )
        return profile

    # -- 读 ----------------------------------------------------------------

    def _default_view(self, settings: Settings) -> ModelProfileView:
        profile = self._synthesize_default(settings)
        return ModelProfileView(
            id=None,
            name=DEFAULT_PROFILE_NAME,
            kind=ModelProfileKind.CHAT,
            provider=ModelProvider.OPENAI_COMPATIBLE,
            base_url=settings.llm.base_url or None,
            api_key="***" if settings.llm.api_key else None,
            model=settings.llm.model,
            context_window=None,
            max_output_tokens=None,
            supports_vision=profile.supports_vision,
            supports_tools=profile.supports_tools,
            supports_json_schema=profile.supports_json_schema,
            dynamic_context_position=profile.dynamic_context_position,
            price_input=None,
            price_cached_input=None,
            price_output=None,
            currency="CNY",
            embedding_dim=None,
            local_model_path=None,
            enabled=True,
            readonly=True,
            created_at=None,
            updated_at=None,
        )

    async def list_views(self) -> list[ModelProfileView]:
        """``default`` 在最前，其余按 ``name`` 升序。"""
        views = [self._default_view(self._current_settings())]
        async with self._session_factory() as session:
            rows = await ModelProfileRepository(session).list_all()
            views.extend(_view_from_row(row) for row in rows)
        return views

    async def get_view(self, name: str) -> ModelProfileView:
        if name == DEFAULT_PROFILE_NAME:
            return self._default_view(self._current_settings())
        async with self._session_factory() as session:
            row = await ModelProfileRepository(session).get_by_name(name)
            if row is None:
                raise AppError(
                    ErrorCode.MODEL_PROFILE_NOT_FOUND,
                    f"模型档案「{name}」不存在",
                )
            return _view_from_row(row)

    # -- 写 ----------------------------------------------------------------

    async def create(self, payload: ModelProfileCreate) -> ModelProfileView:
        if payload.name == DEFAULT_PROFILE_NAME:
            raise AppError(
                ErrorCode.MODEL_PROFILE_EXISTS,
                "default 档案由 llm.* 设置合成，不能新建",
            )
        api_key_encrypted = self._encrypt(payload.api_key) if payload.api_key else None
        row = ModelProfile(
            name=payload.name,
            kind=payload.kind,
            provider=payload.provider,
            base_url=payload.base_url,
            api_key_encrypted=api_key_encrypted,
            model=payload.model,
            context_window=payload.context_window,
            max_output_tokens=payload.max_output_tokens,
            supports_tools=payload.supports_tools,
            dynamic_context_position=payload.dynamic_context_position,
            price_input=payload.price_input,
            price_cached_input=payload.price_cached_input,
            price_output=payload.price_output,
            currency=payload.currency,
            embedding_dim=payload.embedding_dim,
            local_model_path=payload.local_model_path,
            enabled=payload.enabled,
        )
        async with self._session_factory() as session:
            repository = ModelProfileRepository(session)
            if await repository.get_by_name(row.name) is not None:
                raise AppError(
                    ErrorCode.MODEL_PROFILE_EXISTS,
                    f"模型档案「{row.name}」已存在",
                )
            try:
                stored = await repository.create(row)
            except IntegrityError:
                await session.rollback()
                raise AppError(
                    ErrorCode.MODEL_PROFILE_EXISTS,
                    f"模型档案「{row.name}」已存在",
                ) from None
            view = _view_from_row(stored)
            await session.commit()
            return view

    async def update(self, name: str, payload: ModelProfileUpdate) -> ModelProfileView:
        if name == DEFAULT_PROFILE_NAME:
            raise AppError(
                ErrorCode.MODEL_PROFILE_READONLY,
                "default 档案由 llm.* 设置合成，不能修改",
            )
        async with self._session_factory() as session:
            repository = ModelProfileRepository(session)
            row = await repository.get_by_name(name)
            if row is None:
                raise AppError(
                    ErrorCode.MODEL_PROFILE_NOT_FOUND,
                    f"模型档案「{name}」不存在",
                )
            fields = payload.model_fields_set
            if "enabled" in fields and payload.enabled is False:
                roles = self.roles_using(name)
                if roles:
                    raise AppError(
                        ErrorCode.MODEL_PROFILE_IN_USE,
                        f"模型档案「{name}」正被角色使用，不能停用",
                        {"roles": roles},
                    )

            # 先按「合并后的整份档案」校验（规格 §4.3 的 Update 规则）。
            current: dict[str, Any] = {
                "name": row.name,
                "kind": row.kind,
                "provider": row.provider,
                "base_url": row.base_url,
                "api_key": "***" if row.api_key_encrypted else None,
                "model": row.model,
                "context_window": row.context_window,
                "max_output_tokens": row.max_output_tokens,
                "supports_tools": row.supports_tools,
                "dynamic_context_position": row.dynamic_context_position,
                "price_input": row.price_input,
                "price_cached_input": row.price_cached_input,
                "price_output": row.price_output,
                "currency": row.currency,
                "embedding_dim": row.embedding_dim,
                "local_model_path": row.local_model_path,
                "enabled": row.enabled,
            }
            for field_name in fields:
                if field_name == "api_key":
                    continue
                current[field_name] = getattr(payload, field_name)
            try:
                validated = ModelProfileCreate.model_validate(current)
            except ValidationError as exc:
                first = exc.errors()[0]
                message = str(first["msg"])
                raise AppError(ErrorCode.VALIDATION_ERROR, message) from None

            old_base_url = row.base_url
            old_model = row.model
            row.kind = validated.kind
            row.provider = validated.provider
            row.base_url = validated.base_url
            row.model = validated.model
            row.context_window = validated.context_window
            row.max_output_tokens = validated.max_output_tokens
            row.supports_tools = validated.supports_tools
            row.dynamic_context_position = validated.dynamic_context_position
            row.price_input = validated.price_input
            row.price_cached_input = validated.price_cached_input
            row.price_output = validated.price_output
            row.currency = validated.currency
            row.embedding_dim = validated.embedding_dim
            row.local_model_path = validated.local_model_path
            row.enabled = validated.enabled

            if "api_key" in fields:
                if payload.api_key == "***":
                    pass
                elif not payload.api_key:
                    row.api_key_encrypted = None
                else:
                    row.api_key_encrypted = self._encrypt(payload.api_key)

            # 连接目标变了，旧的能力结论就不作数了（规格 §4.3）。
            if row.base_url != old_base_url or row.model != old_model:
                row.supports_vision = None
                row.supports_json_schema = None
            row.updated_at = utcnow()

            await session.flush()
            view = _view_from_row(row)
            await session.commit()
            return view

    async def delete(self, name: str) -> None:
        if name == DEFAULT_PROFILE_NAME:
            raise AppError(
                ErrorCode.MODEL_PROFILE_READONLY,
                "default 档案由 llm.* 设置合成，不能删除",
            )
        async with self._session_factory() as session:
            repository = ModelProfileRepository(session)
            row = await repository.get_by_name(name)
            if row is None:
                raise AppError(
                    ErrorCode.MODEL_PROFILE_NOT_FOUND,
                    f"模型档案「{name}」不存在",
                )
            roles = self.roles_using(name)
            if roles:
                raise AppError(
                    ErrorCode.MODEL_PROFILE_IN_USE,
                    f"模型档案「{name}」正被角色使用，不能删除",
                    {"roles": roles},
                )
            await repository.delete(row)
            await session.commit()

    # -- 探查 --------------------------------------------------------------

    def _make_client(self, profile: ResolvedProfile) -> tuple[Any, bool]:
        factory = self._client_factory_provider()
        if factory is not None:
            settings = _settings_for_profile(self._current_settings(), profile)
            return factory(settings), False
        return (
            OpenAICompatibleClient(
                base_url=profile.base_url, api_key=profile.api_key
            ),
            True,
        )

    def _merge_probe(
        self, previous: ProbeResult | None, result: ProbeResult
    ) -> ProbeResult:
        """``None`` 的项不覆盖上一次的结论（与写回表里的规则一致）。"""
        if previous is None:
            return result
        return ProbeResult(
            supports_tools=(
                result.supports_tools
                if result.supports_tools is not None
                else previous.supports_tools
            ),
            supports_vision=(
                result.supports_vision
                if result.supports_vision is not None
                else previous.supports_vision
            ),
            supports_json_schema=(
                result.supports_json_schema
                if result.supports_json_schema is not None
                else previous.supports_json_schema
            ),
            dynamic_context_position=(
                result.dynamic_context_position
                if result.dynamic_context_position is not None
                else previous.dynamic_context_position
            ),
            latency_ms=result.latency_ms,
            failed=result.failed,
        )

    async def _probe_profile(self, profile: ResolvedProfile) -> ProbeResult:
        client, owned = self._make_client(profile)
        try:
            started = _monotonic()
            supports_tools, tool_error = await probe_tool_calling(
                client, profile.model
            )
            latency_ms = int((_monotonic() - started) * 1000)
            if tool_error is not None:
                # 工具调用探查失败说明连接/鉴权整体不可用，直接抛出去且不写回。
                raise tool_error

            supports_vision, vision_error = await probe_vision(client, profile.model)
            supports_json_schema, json_error = await probe_json_schema(
                client, profile.model
            )
            dynamic_position, tail_error = await probe_tail_position(
                client, profile.model, _token_estimator()
            )
        finally:
            if owned:
                await client.aclose()

        failed = any(
            error is not None for error in (vision_error, json_error, tail_error)
        )
        result = ProbeResult(
            supports_tools=supports_tools,
            # 探查请求失败时这一项没判定，记 None 而不是 v2 返回的 False。
            supports_vision=None if vision_error is not None else supports_vision,
            supports_json_schema=supports_json_schema,
            dynamic_context_position=dynamic_position,
            latency_ms=latency_ms,
            failed=failed,
        )
        await self._store_probe(profile, result)
        return result

    async def _store_probe(
        self, profile: ResolvedProfile, result: ProbeResult
    ) -> None:
        if profile.readonly:
            key = (profile.base_url, profile.model)
            self._default_probe[key] = self._merge_probe(
                self._default_probe.get(key), result
            )
            return
        async with self._session_factory() as session:
            repository = ModelProfileRepository(session)
            row = await repository.get_by_name(profile.name)
            if row is None:
                return
            row.supports_tools = (
                result.supports_tools
                if result.supports_tools is not None
                else row.supports_tools
            )
            if result.supports_vision is not None:
                row.supports_vision = result.supports_vision
            if result.supports_json_schema is not None:
                row.supports_json_schema = result.supports_json_schema
            if result.dynamic_context_position is not None:
                row.dynamic_context_position = result.dynamic_context_position
            row.updated_at = utcnow()
            await session.commit()

    async def probe(self, name: str) -> ProbeResult:
        """显式探查并写回；向量档案与 ``local_onnx`` 不支持探查。"""
        profile = await self._load(name)
        if profile.kind != ModelProfileKind.CHAT or profile.provider == ModelProvider.LOCAL_ONNX:
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                "向量档案在启用检索后才能检测",
            )
        if not profile.base_url or not profile.api_key:
            raise AppError(
                ErrorCode.LLM_NOT_CONFIGURED,
                "LLM 配置不完整，请先配置 base_url 与 API 密钥",
            )
        return await self._probe_profile(profile)

    async def ensure_probed(self, profile: ResolvedProfile) -> ResolvedProfile:
        """档案第一次被使用时自动探查一次；失败结果按 TTL 冷却（v2 §8.13）。"""
        if profile.kind != ModelProfileKind.CHAT or profile.supports_vision is not None:
            return profile

        key = (profile.name, profile.base_url, profile.model)
        lock = self._probe_locks.setdefault(key, asyncio.Lock())
        async with lock:
            # 等锁期间可能已经被别的调用探查过：重新读一次，避免重复请求。
            current = await self._load(profile.name)
            if current.supports_vision is not None:
                return current
            deadline = self._probe_failures.get(key)
            if deadline is not None and _monotonic() < deadline:
                return current

            result = await self.probe(profile.name)
            if result.failed:
                self._probe_failures[key] = (
                    _monotonic() + PROBE_FAILURE_TTL_SECONDS
                )
            return await self._load(profile.name)

    def roles_using(self, name: str) -> list[str]:
        """当前生效设置里指向 ``name`` 的角色，按 ``ModelRole`` 的顺序。"""
        settings = self._current_settings()
        return [
            str(role)
            for role in ModelRole
            if getattr(settings.llm, f"role_{role}", "").strip() == name
        ]

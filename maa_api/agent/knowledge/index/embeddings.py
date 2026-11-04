"""EmbeddingProvider 的三个实现与按设置解析的 EmbeddingResolver（M76 spec §4.2）。

- :class:`NullEmbeddingProvider`：``rag.embedding_provider=none``，只用全文检索。
- :class:`RemoteEmbeddingProvider`：OpenAI 兼容的 ``POST <base_url>/embeddings``，
  每批 16 条，按 ``data[].index`` 排序后做 L2 归一化。
- :class:`LocalOnnxEmbeddingProvider`：onnxruntime + tokenizers 在本机推理，依赖
  可选安装（``embed-local``），第一次 ``embed`` 时才 import。

失败一律抛 ``EMBEDDING_UNAVAILABLE``；错误消息与 ``details`` 先过
:func:`maa_api.agent.redaction.redact_remote_data`，不泄漏 API key。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx
import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.agent.redaction import redact_remote_data
from maa_api.db.models import ModelProfile
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import REPO_ROOT

__all__ = [
    "EmbeddingProvider",
    "EmbeddingResolver",
    "EmbeddingStatus",
    "LocalOnnxEmbeddingProvider",
    "NullEmbeddingProvider",
    "RemoteEmbeddingProvider",
]

Purpose = Literal["document", "query"]
_MAX_UPSTREAM_CHARS = 300
_EMBEDDING_ROLE = "embedding"
_MISSING = object()


class EmbeddingProvider(Protocol):
    """一个能算文档与查询向量的服务；返回 ``float32``、已 L2 归一化的矩阵。"""

    name: str
    dim: int

    async def embed(
        self, texts: Sequence[str], *, purpose: Purpose
    ) -> np.ndarray: ...


class NullEmbeddingProvider:
    """未启用向量服务；任何调用都抛 ``EMBEDDING_UNAVAILABLE``。"""

    name = "none"
    dim = 0

    async def embed(self, texts: Sequence[str], *, purpose: Purpose) -> np.ndarray:
        raise _unavailable("向量服务未启用")


class RemoteEmbeddingProvider:
    """OpenAI 兼容的远端 embedding 服务。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        dim: int | None,
        on_usage: Callable[[int], Awaitable[None]] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 30.0,
        batch_size: int = 16,
    ) -> None:
        self.base_url = (base_url or "").strip()
        self.api_key = api_key or ""
        self.model = model
        self.name = f"openai_compatible:{model}"
        self.dim = int(dim) if dim else 0
        self._on_usage = on_usage
        self._transport = transport
        self._timeout_seconds = timeout_seconds
        self._batch_size = max(1, int(batch_size))
        self._status_callback: Callable[[bool, str | None], None] | None = None

    @property
    def url(self) -> str:
        """档案的 ``base_url`` 去掉末尾 ``/`` 后接 ``/embeddings``。"""
        base = self.base_url.rstrip("/")
        if base.endswith("/embeddings"):
            return base
        return f"{base}/embeddings"

    async def embed(
        self, texts: Sequence[str], *, purpose: Purpose
    ) -> np.ndarray:
        batch = [str(item) for item in texts]
        if not batch:
            return np.zeros((0, self.dim), dtype=np.float32)
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=self._timeout_seconds
            ) as client:
                parts = [
                    await self._embed_batch(client, batch[start : start + self._batch_size])
                    for start in range(0, len(batch), self._batch_size)
                ]
            matrix = np.concatenate(parts, axis=0).astype(np.float32, copy=False)
            self._check_dim(int(matrix.shape[1]))
            self._notify(True, None)
            return matrix
        except AppError as exc:
            self._notify(False, exc.message or str(exc.code))
            raise

    async def _embed_batch(
        self, client: httpx.AsyncClient, batch: Sequence[str]
    ) -> np.ndarray:
        payload = {
            "model": self.model,
            "input": list(batch),
            "encoding_format": "float",
        }
        response = await self._post(client, payload)
        try:
            body = response.json()
        except (ValueError, UnicodeDecodeError):
            raise _unavailable("向量服务返回了无法解析的响应") from None
        if not isinstance(body, Mapping):
            raise _unavailable("向量服务返回了无法解析的响应")
        data = body.get("data")
        if not isinstance(data, list):
            raise _unavailable("向量服务返回了无法解析的响应")

        entries: list[tuple[int, list[float]]] = []
        for position, item in enumerate(data):
            if not isinstance(item, Mapping):
                raise _unavailable("向量服务返回了无法解析的响应")
            embedding = item.get("embedding")
            if not isinstance(embedding, list):
                raise _unavailable("向量服务返回了无法解析的响应")
            index = item.get("index", position)
            if not isinstance(index, int):
                index = position
            try:
                entries.append((index, [float(value) for value in embedding]))
            except (TypeError, ValueError):
                raise _unavailable("向量服务返回了无法解析的响应") from None
        entries.sort(key=lambda entry: entry[0])
        if len(entries) != len(batch):
            raise _unavailable(
                "向量服务返回的条数不对",
                details={"expected": len(batch), "actual": len(entries)},
            )
        lengths = {len(embedding) for _, embedding in entries}
        if len(lengths) != 1:
            raise _unavailable("向量服务返回的维度不一致")
        self._check_dim(lengths.pop())

        matrix = np.asarray([embedding for _, embedding in entries], dtype=np.float32)
        _normalize_rows(matrix)
        await self._report_usage(body.get("usage"))
        return matrix

    async def _report_usage(self, usage: Any) -> None:
        if self._on_usage is None or not isinstance(usage, Mapping):
            return
        prompt_tokens = usage.get("prompt_tokens")
        if isinstance(prompt_tokens, int):
            await self._on_usage(prompt_tokens)

    async def _post(
        self, client: httpx.AsyncClient, payload: Mapping[str, Any]
    ) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        for attempt in (0, 1):
            try:
                response = await client.post(self.url, json=dict(payload), headers=headers)
            except httpx.TimeoutException as exc:
                if attempt == 0:
                    continue
                raise _unavailable(
                    "向量服务请求超时",
                    details={"attempts": 2, "reason": str(exc)},
                ) from None
            except httpx.TransportError as exc:
                if attempt == 0:
                    continue
                raise _unavailable(
                    "向量服务连接失败",
                    details={"attempts": 2, "reason": str(exc)},
                ) from None

            status = response.status_code
            if status in (401, 403):
                raise _unavailable(
                    "向量服务拒绝了请求，检查档案的 API key",
                    details={"status": status},
                )
            if status == 429:
                retry_after = response.headers.get("Retry-After")
                details: dict[str, Any] = {"status": status}
                if retry_after is not None:
                    details["retry_after"] = retry_after
                headers_out = {"Retry-After": retry_after} if retry_after else None
                raise _unavailable(
                    "向量服务限流，请稍后重试",
                    details=details,
                    headers=headers_out,
                )
            if status >= 500:
                if attempt == 0:
                    continue
                raise _unavailable(
                    f"向量服务返回错误（HTTP {status}）",
                    details={
                        "status": status,
                        "attempts": 2,
                        "upstream": _upstream_message(response),
                    },
                )
            if status >= 400:
                raise _unavailable(
                    f"向量服务返回错误（HTTP {status}）",
                    details={"status": status, "upstream": _upstream_message(response)},
                )
            return response
        raise _unavailable("向量服务请求失败")

    def _check_dim(self, actual: int) -> None:
        if actual <= 0:
            raise _unavailable("向量服务返回了空向量")
        if self.dim and self.dim != actual:
            raise _unavailable(
                "向量维度与档案不一致",
                details={"expected": self.dim, "actual": actual},
            )
        self.dim = actual

    def _notify(self, ok: bool, message: str | None) -> None:
        if self._status_callback is not None:
            self._status_callback(ok, message)


class LocalOnnxEmbeddingProvider:
    """本地 ONNX 推理；依赖与模型文件都在第一次 ``embed`` 时检查。"""

    def __init__(
        self,
        model_dir: Path,
        *,
        model: str,
        dim: int | None,
        session_loader: Callable[[Path, int], Any] | None = None,
        tokenizer_loader: Callable[[Path], Any] | None = None,
        threads: int = 2,
        batch_size: int = 16,
    ) -> None:
        self.model_dir = Path(model_dir)
        self.model = model
        self.name = f"local_onnx:{model}"
        self.dim = int(dim) if dim else 0
        self._session_loader = session_loader
        self._tokenizer_loader = tokenizer_loader
        self._threads = max(1, int(threads))
        self._batch_size = max(1, int(batch_size))
        self._session: Any = None
        self._tokenizer: Any = None
        self._config: dict[str, Any] = _default_embedding_config()
        self._loaded = False
        self._status_callback: Callable[[bool, str | None], None] | None = None

    async def embed(
        self, texts: Sequence[str], *, purpose: Purpose
    ) -> np.ndarray:
        items = [str(item) for item in texts]
        if not items:
            return np.zeros((0, self.dim), dtype=np.float32)
        try:
            await self._ensure_loaded()
            prefix = self._config["query_prefix"] if purpose == "query" else ""
            prepared = [f"{prefix}{item}" for item in items]
            parts = [
                await asyncio.to_thread(
                    self._infer_batch, prepared[start : start + self._batch_size]
                )
                for start in range(0, len(prepared), self._batch_size)
            ]
            matrix = np.concatenate(parts, axis=0).astype(np.float32, copy=False)
            self._check_dim(int(matrix.shape[1]))
            self._notify(True, None)
            return matrix
        except AppError as exc:
            self._notify(False, exc.message or str(exc.code))
            raise
        except Exception as exc:  # noqa: BLE001 - 统一映射成 EMBEDDING_UNAVAILABLE
            error = _unavailable(f"本地向量推理失败：{exc}")
            self._notify(False, error.message or str(error.code))
            raise error from None

    async def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        await asyncio.to_thread(self._load_sync)

    def _load_sync(self) -> None:
        missing = [
            name
            for name in ("model.onnx", "tokenizer.json")
            if not (self.model_dir / name).is_file()
        ]
        if missing:
            raise _unavailable(f"模型文件不全：{'、'.join(missing)}")
        if self._session_loader is None or self._tokenizer_loader is None:
            try:
                import onnxruntime  # noqa: F401
                import tokenizers  # noqa: F401
            except ImportError:
                raise _unavailable(
                    "没装本地向量依赖：安装可选依赖 embed-local"
                ) from None
        self._config = _read_embedding_config(self.model_dir)
        session_loader = self._session_loader or _default_session_loader
        tokenizer_loader = self._tokenizer_loader or _default_tokenizer_loader
        try:
            self._session = session_loader(self.model_dir, self._threads)
            self._tokenizer = tokenizer_loader(self.model_dir)
        except AppError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _unavailable(f"本地模型加载失败：{exc}") from None
        max_length = int(self._config["max_length"])
        if hasattr(self._tokenizer, "enable_truncation"):
            self._tokenizer.enable_truncation(max_length=max_length)
        if hasattr(self._tokenizer, "enable_padding"):
            self._tokenizer.enable_padding()
        self._loaded = True

    def _infer_batch(self, texts: Sequence[str]) -> np.ndarray:
        encodings = self._tokenizer.encode_batch(list(texts))
        ids = [np.asarray(encoding.ids, dtype=np.int64) for encoding in encodings]
        masks = [
            np.asarray(
                getattr(encoding, "attention_mask", [1] * len(encoding.ids)),
                dtype=np.int64,
            )
            for encoding in encodings
        ]
        types = [
            np.asarray(
                getattr(encoding, "type_ids", [0] * len(encoding.ids)),
                dtype=np.int64,
            )
            for encoding in encodings
        ]
        input_ids = np.stack(ids)
        attention_mask = np.stack(masks)
        token_type_ids = np.stack(types)

        available = {
            getattr(item, "name", "") for item in self._session.get_inputs()
        }
        candidates = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "token_type_ids": token_type_ids,
        }
        feeds = {name: value for name, value in candidates.items() if name in available}
        if not feeds:
            raise _unavailable("本地模型缺少输入：input_ids")
        outputs = self._session.run(None, feeds)
        hidden = np.asarray(outputs[0], dtype=np.float32)
        if hidden.ndim != 3:
            raise _unavailable("本地模型输出形状不对")
        if self._config["pooling"] == "mean":
            weights = attention_mask.astype(np.float32)[..., None]
            pooled = (hidden * weights).sum(axis=1) / np.maximum(
                attention_mask.sum(axis=1, keepdims=True), 1
            )
        else:
            pooled = hidden[:, 0, :]
        pooled = pooled.astype(np.float32, copy=False)
        _normalize_rows(pooled)
        return pooled

    def _check_dim(self, actual: int) -> None:
        if actual <= 0:
            raise _unavailable("本地模型返回了空向量")
        if self.dim and self.dim != actual:
            raise _unavailable(
                "向量维度与档案不一致",
                details={"expected": self.dim, "actual": actual},
            )
        self.dim = actual

    def _notify(self, ok: bool, message: str | None) -> None:
        if self._status_callback is not None:
            self._status_callback(ok, message)


@dataclass(frozen=True, slots=True)
class EmbeddingStatus:
    """向量服务的当前状态；``status()`` 返回最近一次解析或推理的结果。"""

    provider: Literal["none", "remote", "local"]
    model: str | None
    status: Literal["disabled", "ready", "unavailable"]
    error: str | None


class EmbeddingResolver:
    """按当前设置给出 provider，并按（provider、档案名、档案 updated_at）缓存实例。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        settings_provider: Callable[[], Any],
        model_router: Any,
        on_usage: Callable[[int], Awaitable[None]] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings_provider = settings_provider
        self._model_router = model_router
        # 远端向量服务的用量出口（M76 T9）：装配层把它接到成本账本。
        self._on_usage = on_usage
        self._status = EmbeddingStatus(
            provider="none", model=None, status="disabled", error=None
        )
        self._cached_key: tuple[str, str, Any] | None = None
        self._cached_provider: EmbeddingProvider | None = None

    async def current(self) -> EmbeddingProvider:
        """按 ``rag.embedding_provider`` 解析 provider；档案缺失时退回 Null。"""
        mode = str(self._settings_provider().rag.embedding_provider)
        if mode == "none":
            self._cached_key = None
            self._cached_provider = None
            self._status = EmbeddingStatus(
                provider="none", model=None, status="disabled", error=None
            )
            return NullEmbeddingProvider()
        try:
            if mode == "remote":
                return await self._remote_provider()
            return await self._local_provider()
        except AppError as exc:
            self._cached_key = None
            self._cached_provider = None
            message = str(exc.message or exc.code)
            self._status = EmbeddingStatus(
                provider=mode,  # type: ignore[arg-type]
                model=None,
                status="unavailable",
                error=message,
            )
            return NullEmbeddingProvider()

    def status(self) -> EmbeddingStatus:
        """最近一次 :meth:`current` 或 provider ``embed`` 的结果。"""
        return self._status

    # -- remote ---------------------------------------------------------
    async def _remote_provider(self) -> EmbeddingProvider:
        profile = await self._model_router.resolve(_EMBEDDING_ROLE)
        name = str(getattr(profile, "name", "") or "")
        if getattr(profile, "kind", None) != "embedding":
            raise AppError(
                ErrorCode.MODEL_PROFILE_NOT_FOUND,
                f"向量档案「{name}」的类型不是 embedding",
                {"reason": "kind_mismatch"},
            )
        if getattr(profile, "provider", None) != "openai_compatible":
            raise AppError(
                ErrorCode.MODEL_PROFILE_NOT_FOUND,
                f"向量档案「{name}」不是 openai_compatible",
                {"reason": "provider_mismatch"},
            )
        if not getattr(profile, "base_url", "") or not getattr(profile, "api_key", ""):
            raise AppError(
                ErrorCode.EMBEDDING_UNAVAILABLE,
                "向量档案缺少 base_url 或 API key",
            )
        updated_at = await self._profile_updated_at(profile)
        key = ("remote", name, updated_at)
        provider = self._cached(key)
        if provider is None:
            provider = RemoteEmbeddingProvider(
                base_url=profile.base_url,
                api_key=profile.api_key,
                model=profile.model,
                dim=profile.embedding_dim,
                on_usage=self._on_usage,
            )
            self._remember(key, provider)
        self._status = EmbeddingStatus(
            provider="remote", model=profile.model, status="ready", error=None
        )
        return provider

    # -- local ----------------------------------------------------------
    async def _local_provider(self) -> EmbeddingProvider:
        profile = await self._resolve_role_profile()
        if (
            profile is not None
            and getattr(profile, "kind", None) == "embedding"
            and getattr(profile, "provider", None) == "local_onnx"
        ):
            return await self._local_from_profile(
                name=str(profile.name),
                model=str(profile.model),
                dim=profile.embedding_dim,
                local_model_path=profile.local_model_path,
                updated_at=await self._profile_updated_at(profile),
            )
        row = await self._first_local_profile()
        if row is None:
            raise AppError(ErrorCode.EMBEDDING_UNAVAILABLE, "没有配置本地向量档案")
        return await self._local_from_profile(
            name=row.name,
            model=row.model,
            dim=row.embedding_dim,
            local_model_path=row.local_model_path,
            updated_at=row.updated_at,
        )

    async def _local_from_profile(
        self,
        *,
        name: str,
        model: str,
        dim: int | None,
        local_model_path: str | None,
        updated_at: Any,
    ) -> EmbeddingProvider:
        if not local_model_path:
            raise AppError(
                ErrorCode.EMBEDDING_UNAVAILABLE,
                f"向量档案「{name}」缺少 local_model_path",
            )
        key = ("local", name, updated_at)
        provider = self._cached(key)
        if provider is None:
            provider = LocalOnnxEmbeddingProvider(
                _resolve_model_dir(local_model_path), model=model, dim=dim
            )
            self._remember(key, provider)
        self._status = EmbeddingStatus(
            provider="local", model=model, status="ready", error=None
        )
        return provider

    async def _resolve_role_profile(self) -> Any:
        try:
            return await self._model_router.resolve(_EMBEDDING_ROLE)
        except AppError:
            return None

    async def _first_local_profile(self) -> ModelProfile | None:
        statement = (
            select(ModelProfile)
            .where(
                ModelProfile.kind == "embedding",
                ModelProfile.provider == "local_onnx",
                ModelProfile.enabled.is_(True),
            )
            .order_by(ModelProfile.name.asc())
            .limit(1)
        )
        async with self._session_factory() as session:
            result = await session.execute(statement)
            return result.scalar_one_or_none()

    async def _profile_updated_at(self, profile: Any) -> Any:
        updated = getattr(profile, "updated_at", _MISSING)
        if updated is not _MISSING:
            return updated
        name = getattr(profile, "name", "")
        if not name:
            return None
        async with self._session_factory() as session:
            result = await session.execute(
                select(ModelProfile.updated_at).where(ModelProfile.name == name)
            )
            return result.scalar_one_or_none()

    # -- cache ----------------------------------------------------------
    def _cached(self, key: tuple[str, str, Any]) -> EmbeddingProvider | None:
        if self._cached_key == key:
            return self._cached_provider
        return None

    def _remember(self, key: tuple[str, str, Any], provider: EmbeddingProvider) -> None:
        self._cached_key = key
        self._cached_provider = provider
        setattr(provider, "_status_callback", self._note_embed)

    def _note_embed(self, ok: bool, message: str | None) -> None:
        status = self._status
        if status.provider not in ("remote", "local"):
            return
        if ok:
            if status.status != "ready":
                self._status = EmbeddingStatus(
                    provider=status.provider,
                    model=status.model,
                    status="ready",
                    error=None,
                )
        else:
            self._status = EmbeddingStatus(
                provider=status.provider,
                model=status.model,
                status="unavailable",
                error=message,
            )


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------
def _unavailable(
    message: str,
    *,
    details: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> AppError:
    """构造脱敏后的 ``EMBEDDING_UNAVAILABLE``。"""
    clean_message = str(redact_remote_data(message))
    clean_details = redact_remote_data(dict(details)) if details else None
    return AppError(
        ErrorCode.EMBEDDING_UNAVAILABLE,
        clean_message,
        clean_details,
        headers,
    )


def _upstream_message(response: httpx.Response) -> str | None:
    """取上游 ``error.message``，脱敏并截断。"""
    try:
        payload = response.json()
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, Mapping):
        return None
    error = payload.get("error")
    if not isinstance(error, Mapping):
        return None
    message = error.get("message")
    if not isinstance(message, str) or not message.strip():
        return None
    return str(redact_remote_data(message.strip()))[:_MAX_UPSTREAM_CHARS]


def _normalize_rows(matrix: np.ndarray) -> None:
    """按行做 L2 归一化；零向量保持为零。"""
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    np.divide(matrix, norms, out=matrix, where=norms > 0)


def _default_embedding_config() -> dict[str, Any]:
    return {"pooling": "cls", "query_prefix": "", "max_length": 512}


def _read_embedding_config(model_dir: Path) -> dict[str, Any]:
    """读可选的 ``maa_embedding.json``；缺文件或字段非法时用默认值。"""
    config = _default_embedding_config()
    path = model_dir / "maa_embedding.json"
    if not path.is_file():
        return config
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return config
    if not isinstance(raw, Mapping):
        return config
    pooling = raw.get("pooling")
    if pooling in ("cls", "mean"):
        config["pooling"] = pooling
    query_prefix = raw.get("query_prefix")
    if isinstance(query_prefix, str):
        config["query_prefix"] = query_prefix
    max_length = raw.get("max_length")
    if isinstance(max_length, int) and max_length > 0:
        config["max_length"] = max_length
    return config


def _resolve_model_dir(local_model_path: str) -> Path:
    """相对路径拼到 ``REPO_ROOT/resource`` 下；绝对路径原样用。"""
    path = Path(local_model_path).expanduser()
    if path.is_absolute():
        return path
    return REPO_ROOT / "resource" / local_model_path


def _default_session_loader(model_dir: Path, threads: int) -> Any:
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    return ort.InferenceSession(
        str(model_dir / "model.onnx"),
        sess_options=options,
        providers=["CPUExecutionProvider"],
    )


def _default_tokenizer_loader(model_dir: Path) -> Any:
    from tokenizers import Tokenizer

    return Tokenizer.from_file(str(model_dir / "tokenizer.json"))


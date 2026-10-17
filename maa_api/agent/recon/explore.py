"""只读探索工具：边界口径、路径解析与三个工具的实现（M48 规格 §4.2）。

这一层是给模型看的只读窗口，不做通用文件 API。所有校验都在读之前完成：

- 根目录来自 ``settings.workspace.root``，空串表示关闭探索能力（规格 D1）；
- 相对路径解析后必须仍在根目录内，软链接按真实路径判定；
- 密钥名单里的文件直接拒读（不做只列不读的省略）：``.env`` / ``config.yaml`` / ``*.pem`` /
  ``*.key`` / ``id_rsa*`` / ``*credential*`` / ``*secret*`` / ``.dsh/``；
- 单文件超 ``MAX_FILE_BYTES`` 拒读，前 ``BINARY_PROBE_BYTES`` 含 NUL 判二进制拒读；
- 忽略目录（``node_modules`` / ``.venv`` / ``__pycache__`` …）默认不列举、不搜索；
- 单次输出按 ``agent.recon_output_chars`` 截断，并给出 ``truncated.next_offset`` 续读。

三个工具都是 ``risk=SAFE`` / ``read_only=True``；写操作、shell、设备都不在这里。
"""

from __future__ import annotations

import asyncio
import fnmatch
import inspect
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk
from maa_api.domain.errors import AppError, ErrorCode

__all__ = [
    "ListFilesParams",
    "ReadFileParams",
    "SearchCodeParams",
    "is_configured",
    "list_dir",
    "read_text",
    "register_tools",
    "resolve_root",
    "search_content",
    "search_git",
]

#: 单个文件可读上限。2 MiB 足够覆盖仓库里最大的源码/文档，又不至于让一次读把预算吃光。
MAX_FILE_BYTES = 2 * 1024 * 1024
#: 判定二进制时探测的前缀字节数。
BINARY_PROBE_BYTES = 8 * 1024
#: 目录列举与搜索的硬上限：即使调用方给了更大的 max_results 也不越过。
MAX_RESULTS_CAP = 100
#: ``list_files`` 的 ``depth`` 上限。
MAX_DEPTH_CAP = 3
#: git 只读动作白名单。远端操作、checkout、clean、reset 都不在其中（规格 D3）。
GIT_ACTIONS = ("status_local", "diff_local", "log_path", "blame_path")
#: 每个 git 动作对应的参数白名单：键是动作，值是允许附加的位置参数个数上限。
_GIT_ACTION_ARGS: dict[str, int] = {
    "status_local": 0,
    "diff_local": 1,
    "log_path": 1,
    "blame_path": 1,
}
#: 拒绝读取的路径名单（按 basename 或相对路径匹配）。
BLOCKED_PATTERNS: tuple[str, ...] = (
    ".env",
    ".env.*",
    "config.yaml",
    "config.*.yaml",
    "*.pem",
    "*.key",
    "id_rsa*",
    "id_ed25519*",
    "*credential*",
    "*secret*",
    ".dsh/*",
)
#: 默认不列举、不搜索的目录名（任意层级）。
IGNORED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".superpowers",
        ".archify",
        ".idea",
        ".mypy_cache",
        ".ruff_cache",
        ".pnpm-store",
        "dist",
        "build",
        "coverage",
    }
)
#: 二进制目录前缀（相对根目录）：库文件目录整块跳过。
IGNORED_RELATIVE_PREFIXES: tuple[str, ...] = ("resource/lib",)
#: 密钥正文探测：命中即拒读（这类文件往往没有敏感文件名，但内容一眼可辨）。
_SECRET_BODY_PATTERN = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|(?:api[_-]?key|access[_-]?token|secret)\s*[:=]\s*\S{16,}",
    re.IGNORECASE,
)
#: git 子进程超时：只读查询不应该长时间占住一次工具调用。
_GIT_TIMEOUT_SECONDS = 20


def resolve_root(settings: Any) -> Path:
    """取工作区根目录；未配置时按规格 D1 直接报错，不做静默降级。"""

    workspace = getattr(settings, "workspace", None)
    raw = str(getattr(workspace, "root", "") or "").strip()
    if not raw:
        raise AppError(
            ErrorCode.WORKSPACE_NOT_CONFIGURED,
            "未配置工作区根目录（settings.workspace.root），只读探索与侦察不可用",
        )
    root = Path(raw).expanduser()
    try:
        root = root.resolve()
    except OSError as exc:  # pragma: no cover - 只有权限/IO 异常才会走到
        raise AppError(
            ErrorCode.WORKSPACE_NOT_CONFIGURED,
            f"工作区根目录不可解析：{raw}",
        ) from exc
    if not root.is_dir():
        raise AppError(
            ErrorCode.WORKSPACE_NOT_CONFIGURED,
            f"工作区根目录不存在或不是目录：{root}",
        )
    return root


def is_configured(settings: Any) -> bool:
    """是否配置了工作区（不抛错的轻量判定，供提示词与降级路径用）。"""

    try:
        resolve_root(settings)
    except AppError:
        return False
    return True


def read_limit(settings: Any) -> int:
    """单次输出的字符上限（``agent.recon_output_chars``，未配置时取规格默认 20000）。"""

    agent = getattr(settings, "agent", None)
    value = getattr(agent, "recon_output_chars", None)
    return int(value) if isinstance(value, int) and value > 0 else 20_000


@dataclass(frozen=True, slots=True)
class _Target:
    """一次路径解析的结果：真实路径、相对根目录的展示路径、是否命中密钥名单。"""

    root: Path
    resolved: Path
    relative: str
    blocked: bool


def _is_blocked(relative: str) -> bool:
    """密钥名单判定：basename 与相对路径都参与匹配。"""

    candidate = relative.strip("/")
    if not candidate:
        return False
    name = candidate.rsplit("/", 1)[-1]
    for pattern in BLOCKED_PATTERNS:
        if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(candidate, pattern):
            return True
    return False


def _resolve(settings: Any, raw: str) -> _Target:
    """把调用方给的路径解析成根目录内的真实路径，越界即拒绝（含软链接）。"""

    root = resolve_root(settings)
    candidate = (raw or ".").strip() or "."
    if Path(candidate).is_absolute():
        raise AppError(
            ErrorCode.WORKSPACE_PATH_DENIED,
            f"只接受相对工作区根目录的路径：{raw}",
        )
    try:
        resolved = (root / candidate).resolve()
    except OSError as exc:
        raise AppError(
            ErrorCode.WORKSPACE_PATH_DENIED,
            f"路径不可解析：{raw}",
        ) from exc
    if resolved != root and root not in resolved.parents:
        raise AppError(
            ErrorCode.WORKSPACE_PATH_DENIED,
            f"路径超出工作区：{raw}",
        )
    relative = "." if resolved == root else resolved.relative_to(root).as_posix()
    return _Target(
        root=root,
        resolved=resolved,
        relative=relative,
        blocked=_is_blocked(relative),
    )


def _ignored_dir(name: str) -> bool:
    return name in IGNORED_DIRS


def _ignored_relative(relative: str) -> bool:
    return any(
        relative == prefix or relative.startswith(f"{prefix}/")
        for prefix in IGNORED_RELATIVE_PREFIXES
    )


def _require_readable(target: _Target) -> None:
    """读出之前的三道闸：密钥名单、存在性、常规文件。"""

    if target.blocked:
        raise AppError(
            ErrorCode.WORKSPACE_PATH_DENIED,
            f"{target.relative} 在禁止读取的名单里（密钥或凭据）",
        )
    if not target.resolved.exists():
        raise AppError(
            ErrorCode.NOT_FOUND,
            f"工作区里没有这个路径：{target.relative}",
        )
    if not target.resolved.is_file():
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            f"不是文件：{target.relative}",
        )


def _read_bytes(target: _Target) -> bytes:
    """读字节并做大小与二进制判定；两道闸都在解码之前。"""

    size = target.resolved.stat().st_size
    if size > MAX_FILE_BYTES:
        raise AppError(
            ErrorCode.WORKSPACE_FILE_TOO_LARGE,
            f"{target.relative} 有 {size} 字节，超过 {MAX_FILE_BYTES} 字节上限；"
            "用 search_code 定位后再读片段",
        )
    payload = target.resolved.read_bytes()
    if b"\x00" in payload[:BINARY_PROBE_BYTES]:
        raise AppError(
            ErrorCode.WORKSPACE_PATH_DENIED,
            f"{target.relative} 是二进制文件，不能按文本读",
        )
    return payload


def _decode(target: _Target, payload: bytes) -> str:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AppError(
            ErrorCode.WORKSPACE_PATH_DENIED,
            f"{target.relative} 不是 UTF-8 文本",
        ) from exc
    if _SECRET_BODY_PATTERN.search(text[:BINARY_PROBE_BYTES]):
        raise AppError(
            ErrorCode.WORKSPACE_PATH_DENIED,
            f"{target.relative} 正文里出现疑似密钥，拒绝读取",
        )
    return text


# --------------------------------------------------------------------------- #
# 工具的返回体
# --------------------------------------------------------------------------- #
def list_dir(settings: Any, params: "ListFilesParams") -> dict[str, Any]:
    """列举一个目录（不递归进被忽略的目录），密钥名单的条目标 ``blocked``。"""

    target = _resolve(settings, params.path)
    if not target.resolved.exists():
        raise AppError(
            ErrorCode.NOT_FOUND,
            f"工作区里没有这个路径：{target.relative}",
        )
    if not target.resolved.is_dir():
        raise AppError(ErrorCode.INVALID_PARAMETER, f"不是目录：{target.relative}")

    entries: list[dict[str, Any]] = []
    for path in _walk(target, depth=params.depth, pattern=params.pattern):
        relative = path.relative_to(target.root).as_posix()
        is_dir = path.is_dir()
        entries.append(
            {
                "path": relative,
                "kind": "dir" if is_dir else "file",
                "bytes": None if is_dir else path.stat().st_size,
                "blocked": _is_blocked(relative),
            }
        )
    entries.sort(key=lambda item: item["path"])
    truncated = len(entries) > MAX_RESULTS_CAP
    return {
        "root": str(target.root),
        "path": target.relative,
        "entries": entries[:MAX_RESULTS_CAP],
        "truncated": {"reason": "max_results"} if truncated else None,
    }


def read_text(settings: Any, params: "ReadFileParams") -> dict[str, Any]:
    """按字符偏移读文件片段；超出上限时给出下一段的 ``next_offset``。"""

    target = _resolve(settings, params.path)
    _require_readable(target)
    text = _decode(target, _read_bytes(target))
    # 调用方给的 max_chars 只能往下要、不能往上要：否则父 agent 一次 read_file 就能把
    # 整个文件拉进上下文，与 M48「长探索不进父上下文」的动机相反（规格 §4.2）。
    ceiling = read_limit(settings)
    limit = ceiling if params.max_chars is None else min(params.max_chars, ceiling)
    chunk = text[params.offset : params.offset + limit]
    next_offset = params.offset + len(chunk)
    truncated = next_offset < len(text)
    return {
        "path": target.relative,
        "text": chunk,
        "bytes": target.resolved.stat().st_size,
        "chars": len(text),
        "start_line": text.count("\n", 0, params.offset) + 1,
        "truncated": {"next_offset": next_offset, "reason": "max_chars"} if truncated else None,
    }


def search_content(settings: Any, params: "SearchCodeParams") -> dict[str, Any]:
    """按正则搜索工作区内的文本文件，返回 ``path`` / ``line`` / ``text``。"""

    try:
        pattern = re.compile(params.pattern)
    except re.error as exc:
        raise AppError(
            ErrorCode.TOOL_ARGS_INVALID,
            f"正则表达式非法：{exc}",
        ) from exc

    target = _resolve(settings, params.path)
    if not target.resolved.is_dir():
        raise AppError(ErrorCode.INVALID_PARAMETER, f"不是目录：{target.relative}")

    limit = min(params.max_results, MAX_RESULTS_CAP)
    matches: list[dict[str, Any]] = []
    scanned = 0
    for path in _walk(target, depth=MAX_DEPTH_CAP + 7, pattern=params.glob):
        if len(matches) >= limit:
            break
        relative = path.relative_to(target.root).as_posix()
        if path.is_dir() or _is_blocked(relative):
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            continue
        try:
            payload = path.read_bytes()
        except OSError:  # pragma: no cover - 只读竞态
            continue
        if b"\x00" in payload[:BINARY_PROBE_BYTES]:
            continue
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            continue
        # 与 read_file 同一道闸：正文里出现私钥或凭据的文件整个跳过，否则搜索会成为
        # 「read_file 拒读、search_code 照读」的旁路（规格 §5 的风险对策）。
        if _SECRET_BODY_PATTERN.search(text[:BINARY_PROBE_BYTES]):
            continue
        scanned += 1
        lines = text.splitlines()
        for index, line in enumerate(lines):
            hit = pattern.search(line)
            if hit is None:
                continue
            low = max(0, index - params.context_lines)
            high = min(len(lines), index + params.context_lines + 1)
            matches.append(
                {
                    "path": relative,
                    "line": index + 1,
                    "column": hit.start() + 1,
                    "text": "\n".join(lines[low:high]),
                }
            )
            if len(matches) >= limit:
                break
    return {
        "pattern": params.pattern,
        "matches": matches,
        "scanned_files": scanned,
        "truncated": {"reason": "max_results"} if len(matches) >= limit else None,
    }


def search_git(settings: Any, params: "SearchCodeParams") -> dict[str, Any]:
    """四个只读 git 动作；白名单外的动作直接拒绝（规格 D3）。"""

    action = params.action or ""
    if action not in _GIT_ACTION_ARGS:
        raise AppError(
            ErrorCode.WORKSPACE_PATH_DENIED,
            f"git 只允许 {', '.join(GIT_ACTIONS)} 这四个只读动作，收到：{action or '(空)'}",
        )
    target = _resolve(settings, params.path)
    root = target.root
    if not (root / ".git").exists():
        raise AppError(
            ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE,
            f"{root} 不是 git 仓库",
        )
    argv = _git_argv(action, target)
    try:
        completed = subprocess.run(  # noqa: S603 - argv 是白名单构造的，不经过 shell
            argv,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        raise AppError(
            ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE,
            "本机没有可用的 git",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise AppError(
            ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE,
            f"git {action} 超过 {_GIT_TIMEOUT_SECONDS} 秒未返回",
        ) from exc
    output = completed.stdout if completed.returncode == 0 else completed.stderr
    limit = read_limit(settings)
    truncated = len(output) > limit
    return {
        "action": action,
        "argv": argv[2:],
        "exit_code": completed.returncode,
        "output": output[:limit],
        "truncated": {"reason": "max_chars"} if truncated else None,
    }


def _git_argv(action: str, target: _Target) -> list[str]:
    """白名单动作到 argv 的映射；路径参数只做相对根目录的过滤表达式。"""

    argv = ["git", "-C", str(target.root)]
    if action == "status_local":
        return [*argv, "status", "--short", "--branch"]
    scope = ["--", target.relative] if target.relative != "." else []
    if action == "diff_local":
        return [*argv, "diff", "--stat", *scope]
    if action == "log_path":
        return [*argv, "log", "--oneline", "-n", "20", *scope]
    return [*argv, "blame", "--line-porcelain", *scope]


def _walk(target: _Target, *, depth: int, pattern: str | None) -> list[Path]:
    """按深度与 glob 列举；忽略目录在被进入之前就剪掉（不靠事后过滤）。

    搜索要在整个仓库上跑，所以这里用 ``os.walk`` 的原地剪枝：``node_modules`` 与
    ``.venv`` 这类大目录不会被遍历，否则一次 ``search_code`` 会白扫几十万个文件。

    ``depth`` 取相对根目录的层数：``depth=1`` 只列根目录的直接子项（目录与文件），
    ``depth=2`` 再往下一层。软链接按解析后的真实路径判定，指向根目录之外的条目直接
    跳过，否则「列举/搜索」会变成绕过 ``read_file`` 越界拦截的后门。
    """

    results: list[Path] = []
    base_depth = len(target.resolved.parts)
    for dirpath, dirnames, filenames in os.walk(target.resolved):
        current = Path(dirpath)
        current_depth = len(current.parts) - base_depth
        kept: list[str] = []
        for name in sorted(dirnames):
            child = current / name
            if not _inside_root(target, child):
                continue
            relative = child.relative_to(target.root).as_posix()
            if _ignored_dir(name) or _ignored_relative(relative):
                continue
            if len(child.parts) - base_depth > depth:
                continue
            kept.append(name)
            if pattern is None or fnmatch.fnmatch(name, pattern):
                results.append(child)
        dirnames[:] = [] if current_depth >= depth else kept
        for name in sorted(filenames):
            child = current / name
            if not _inside_root(target, child):
                continue
            if pattern and not fnmatch.fnmatch(name, pattern):
                continue
            results.append(child)
    return results


def _inside_root(target: _Target, path: Path) -> bool:
    """解析后的真实路径是否仍在根目录内；软链接因此与 ``_resolve`` 同一口径。"""

    try:
        resolved = path.resolve()
    except OSError:  # pragma: no cover - 只读竞态（链接在两步之间被删）
        return False
    return resolved == target.root or target.root in resolved.parents


# --------------------------------------------------------------------------- #
# 参数模型与注册
# --------------------------------------------------------------------------- #
class ListFilesParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(default=".", max_length=512, description="相对工作区根目录的目录")
    depth: int = Field(default=1, ge=1, le=MAX_DEPTH_CAP, description="递归层数")
    pattern: str | None = Field(default=None, max_length=128, description="可选的文件名 glob")


class ReadFileParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=512, description="相对工作区根目录的文件")
    offset: int = Field(default=0, ge=0, description="起始字符偏移，续读时用上次的 next_offset")
    max_chars: int | None = Field(default=None, ge=1, description="本次最多返回的字符数")


class SearchCodeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = Field(default="content", pattern="^(content|git)$", description="content 或 git")
    pattern: str = Field(min_length=1, max_length=512, description="content 模式是正则；git 模式忽略")
    path: str = Field(default=".", max_length=512)
    glob: str | None = Field(default=None, max_length=128)
    max_results: int = Field(default=40, ge=1, le=MAX_RESULTS_CAP)
    context_lines: int = Field(default=0, ge=0, le=3)
    action: str | None = Field(
        default=None,
        max_length=32,
        description=f"mode=git 时的动作：{', '.join(GIT_ACTIONS)}",
    )


async def _required_settings(ctx: ToolContext) -> Any:
    """工具设置来自 ``app.state.settings_provider``（与其它工具同一口径）。

    M56 热修：``SettingService.refresh`` 是 async 的，装配层接进来的 provider 返回协程；
    这里如果不 await，``list_dir``/``read_text``/``search_content`` 会在设置对象上
    拿不到工作区根而报错（实机表现是三个探索工具全 AGENT_DISABLED）。
    """

    state = getattr(getattr(ctx.request, "app", None), "state", None)
    provider = getattr(state, "settings_provider", None)
    if not callable(provider):
        raise AppError(ErrorCode.AGENT_DISABLED, "设置服务尚未启动")
    value = provider()
    if inspect.isawaitable(value):
        value = await value
    return value


def register_tools(registry: ToolRegistry) -> None:
    """注册三个只读探索工具（``delegate_recon`` 在 ``recon.delegate``）。"""

    registry.register(
        ToolDefinition(
            name="list_files",
            group="recon",
            risk=ToolRisk.SAFE,
            description=(
                "列举工作区里的目录与文件（带大小）。看不清结构时先列一层，"
                "再决定读哪个文件；被禁止读取的条目会标 blocked，不要尝试读它们。"
            ),
            params_model=ListFilesParams,
            handler=_list_files,
            read_only=True,
        )
    )
    registry.register(
        ToolDefinition(
            name="read_file",
            group="recon",
            risk=ToolRisk.SAFE,
            description=(
                "读工作区里一个文本文件的片段。内容超出上限时返回 truncated.next_offset，"
                "带上它接着读就能拼回全文；二进制、超大文件与密钥文件会被拒绝。"
            ),
            params_model=ReadFileParams,
            handler=_read_file,
            read_only=True,
        )
    )
    registry.register(
        ToolDefinition(
            name="search_code",
            group="recon",
            risk=ToolRisk.SAFE,
            description=(
                "在工作区里按正则搜代码与文档（mode=content），或查只读的本地 git 信息"
                "（mode=git，动作限 status_local / diff_local / log_path / blame_path）。"
                "比逐个读文件快得多，先搜再读。"
            ),
            params_model=SearchCodeParams,
            handler=_search_code,
            read_only=True,
        )
    )


async def _list_files(params: ListFilesParams, ctx: ToolContext) -> dict[str, Any]:
    return list_dir(await _required_settings(ctx), params)


async def _read_file(params: ReadFileParams, ctx: ToolContext) -> dict[str, Any]:
    return read_text(await _required_settings(ctx), params)


async def _search_code(params: SearchCodeParams, ctx: ToolContext) -> dict[str, Any]:
    settings = await _required_settings(ctx)
    if params.mode == "git":
        return await asyncio.to_thread(search_git, settings, params)
    return await asyncio.to_thread(search_content, settings, params)

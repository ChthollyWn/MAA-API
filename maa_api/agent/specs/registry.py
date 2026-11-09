"""内置代理定义的加载、校验、版本哈希与注册表（规格 §4.2.15）。"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from functools import lru_cache
from importlib import resources
from importlib.resources.abc import Traversable
from types import MappingProxyType

from pydantic import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from maa_api.agent.registry import ToolRegistry
from maa_api.agent.specs.schema import DEVICE_LEVELS, AgentSpec, AgentSpecFrontmatter
from maa_api.agent.tools.catalog import DEFERRED_TOOL_GROUPS
from maa_api.domain.errors import AppError, ErrorCode

__all__ = [
    "BUILTIN_DIRNAME",
    "BUILTIN_PACKAGE",
    "COMMON_FILENAME",
    "DEFERRED_GROUP_SPECS",
    "AgentSpecError",
    "AgentSpecRegistry",
    "builtin_common_prompt",
    "builtin_spec",
    "builtin_specs",
    "load_builtin_specs",
    "load_common_prompt",
    "parse_spec_file",
]

BUILTIN_PACKAGE = "maa_api.agent.specs"
BUILTIN_DIRNAME = "builtin"
#: 各定义共用的约定；放在 ``specs/`` 根目录，不是一份代理定义（M70 §4.5.10）。
COMMON_FILENAME = "common.md"
#: 只有这两个代理可以按需开启扩展工具组。
DEFERRED_GROUP_SPECS = frozenset({"legacy_chat", "general"})

#: 与 ``knowledge/__init__.py`` 同一份写法：``---`` 包起来的 YAML 头 + 正文。
_FRONTMATTER = re.compile(
    r"\A---[ \t]*\r?\n(?P<meta>.*?)\r?\n---[ \t]*(?:\r?\n(?P<body>.*))?\Z",
    re.DOTALL,
)
_FRONTMATTER_FIELDS = frozenset(AgentSpecFrontmatter.model_fields)


class AgentSpecError(RuntimeError):
    """内置代理定义不合法；属于代码错误，启动时直接失败。"""


def _invalid(file_name: str, field: str, reason: str) -> AgentSpecError:
    return AgentSpecError(f"代理定义 {file_name} 的 {field} 不合法：{reason}")


def _check_field_rules(spec: AgentSpecFrontmatter, file_name: str, body: str) -> None:
    """逐份定义检查规则 3（文件名）、4–13；Pydantic 已覆盖规则 2、4–7 的取值形状。"""
    expected_name = file_name[:-3] if file_name.endswith(".md") else file_name
    if spec.name != expected_name:
        raise _invalid(file_name, "name", f"必须与文件名一致（{expected_name}）")
    if spec.kind == "subagent" and spec.max_steps <= 0:
        raise _invalid(file_name, "max_steps", "subagent 必须大于 0")
    for field in ("tools", "delegates", "deferred_groups", "skills"):
        values = getattr(spec, field)
        if len(set(values)) != len(values):
            raise _invalid(file_name, field, "不能有重复项")
    unknown_groups = sorted(set(spec.deferred_groups) - DEFERRED_TOOL_GROUPS)
    if unknown_groups:
        raise _invalid(file_name, "deferred_groups", f"未知工具组 {unknown_groups}")
    if spec.deferred_groups and spec.name not in DEFERRED_GROUP_SPECS:
        raise _invalid(
            file_name,
            "deferred_groups",
            f"只有 {sorted(DEFERRED_GROUP_SPECS)} 可以按需开启扩展组",
        )
    if spec.delegates and spec.kind != "worker":
        raise _invalid(file_name, "delegates", "只有 worker 可以委派")
    if spec.verify_before_ready and spec.kind != "worker":
        raise _invalid(file_name, "verify_before_ready", "只有 worker 可以为 true")
    if spec.kind in {"subagent", "background"} and not spec.output_schema:
        raise _invalid(file_name, "output_schema", f"{spec.kind} 必填")
    if spec.kind == "orchestrator" and spec.device != "none":
        raise _invalid(file_name, "device", "orchestrator 必须是 none")
    if not body:
        raise _invalid(file_name, "body", "正文不能为空")


def parse_spec_file(file_name: str, data: bytes) -> AgentSpec:
    """解析一份定义文件；任何不合法都抛 :class:`AgentSpecError`（规则 1–13）。"""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AgentSpecError(f"代理定义 {file_name} 不是合法 UTF-8：{exc}") from exc
    match = _FRONTMATTER.match(text)
    if match is None:
        raise AgentSpecError(f"代理定义 {file_name} 缺 frontmatter（--- 包起来的 YAML 头）")
    try:
        meta = YAML(typ="safe").load(match.group("meta"))
    except YAMLError as exc:
        raise AgentSpecError(
            f"代理定义 {file_name} 的 frontmatter 不是合法 YAML：{exc}"
        ) from exc
    if not isinstance(meta, Mapping):
        raise AgentSpecError(f"代理定义 {file_name} 的 frontmatter 不是键值映射")

    unknown = sorted(str(key) for key in set(meta) - _FRONTMATTER_FIELDS)
    if unknown:
        raise _invalid(file_name, "frontmatter", f"有未知字段 {unknown}")
    try:
        spec = AgentSpecFrontmatter.model_validate(meta)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(part) for part in first["loc"]) or "frontmatter"
        raise _invalid(file_name, field, str(first["msg"])) from exc

    body = (match.group("body") or "").strip()
    _check_field_rules(spec, file_name, body)
    return AgentSpec(
        **spec.model_dump(),
        body=body,
        spec_version=hashlib.sha256(data).hexdigest()[:12],
        source_file=file_name,
    )


def _check_cross_definition(specs: Mapping[str, AgentSpec]) -> None:
    """规则 14–15：orchestrator 至多一个；委派目标必须是已加载的 subagent。"""
    orchestrators = sorted(
        (spec for spec in specs.values() if spec.kind == "orchestrator"),
        key=lambda spec: spec.name,
    )
    if len(orchestrators) > 1:
        raise AgentSpecError(
            f"代理定义 {orchestrators[1].source_file} 不合法："
            f"orchestrator 至多一个（已有 {orchestrators[0].source_file}）"
        )
    for spec in specs.values():
        for delegate in spec.delegates:
            target = specs.get(delegate)
            if target is None:
                raise _invalid(
                    spec.source_file, "delegates", f"委派目标 {delegate!r} 不存在"
                )
            if target.kind != "subagent":
                raise _invalid(
                    spec.source_file, "delegates", f"委派目标 {delegate!r} 不是 subagent"
                )


def load_common_prompt(root: Traversable | None = None) -> str:
    """读 ``common.md`` 的正文（M70 §4.5.10）；文件缺失或为空时启动失败。

    默认读 ``specs/common.md``。测试传自定义定义目录时先找该目录下的
    ``common.md``，没有就用包内的那一份；扫描定义只认 ``builtin/*.md``，所以
    ``common.md`` 不会被当成代理定义。
    """
    if root is not None:
        candidate = root.joinpath(COMMON_FILENAME)
        path = (
            candidate
            if candidate.is_file()
            else resources.files(BUILTIN_PACKAGE).joinpath(COMMON_FILENAME)
        )
    else:
        path = resources.files(BUILTIN_PACKAGE).joinpath(COMMON_FILENAME)
    if not path.is_file():
        raise AgentSpecError(f"共用约定文件 {COMMON_FILENAME} 缺失：{path}")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise AgentSpecError(
            f"共用约定 {COMMON_FILENAME} 不是合法 UTF-8：{exc}"
        ) from exc
    prompt = text.strip()
    if not prompt:
        raise AgentSpecError(f"共用约定 {COMMON_FILENAME} 不能为空")
    return prompt


@lru_cache(maxsize=1)
def builtin_common_prompt() -> str:
    """包内的共用约定（按文件内容只读一次）。"""
    return load_common_prompt()


def load_builtin_specs(root: Traversable | None = None) -> dict[str, AgentSpec]:
    """读一个目录下的全部 ``*.md``，按 name 排序返回规则 1–15 都通过的定义。

    默认只扫描 ``specs/builtin/*.md``；``specs/common.md`` 是共用约定，不是定义。
    """
    directory = (
        root
        if root is not None
        else resources.files(BUILTIN_PACKAGE).joinpath(BUILTIN_DIRNAME)
    )
    specs: dict[str, AgentSpec] = {}
    for item in sorted(directory.iterdir(), key=lambda path: path.name):
        if not item.name.endswith(".md"):
            continue
        spec = parse_spec_file(item.name, item.read_bytes())
        if spec.name in specs:
            raise AgentSpecError(
                f"代理定义名字重复: {spec.name!r}（{specs[spec.name].source_file} 与 {item.name}）"
            )
        specs[spec.name] = spec
    if not specs:
        raise AgentSpecError(f"内置代理定义目录里没有 .md 文件: {directory}")
    _check_cross_definition(specs)
    return dict(sorted(specs.items()))


@lru_cache(maxsize=1)
def builtin_specs() -> Mapping[str, AgentSpec]:
    """全部内置定义（按 name 排序，只解析一次）。"""
    return MappingProxyType(load_builtin_specs())


def builtin_spec(name: str) -> AgentSpec:
    """按名字取一份内置定义；没有就抛 :class:`AgentSpecError`。"""
    try:
        return builtin_specs()[name]
    except KeyError:
        raise AgentSpecError(f"没有名为 {name!r} 的代理定义") from None


class AgentSpecRegistry:
    """启动时加载并校验内置定义，供门面、recon 与 REST 目录共用。"""

    def __init__(self, tool_registry: ToolRegistry, *, root: Traversable | None = None) -> None:
        self._tool_registry = tool_registry
        self._root = root
        self._specs: dict[str, AgentSpec] | None = None
        self._common_prompt: str | None = None

    @property
    def common_prompt(self) -> str:
        """``common.md`` 全文；未加载就抛 :class:`AgentSpecError`。"""
        if self._common_prompt is None:
            raise AgentSpecError("代理定义尚未加载")
        return self._common_prompt

    def load(self) -> None:
        """解析全部定义与共用约定并检查规则 16–17；失败不留下半加载状态。"""
        common_prompt = load_common_prompt(self._root)
        specs = load_builtin_specs(self._root)
        tools = {definition.name: definition for definition in self._tool_registry.list()}
        for spec in specs.values():
            for name in spec.tools:
                definition = tools.get(name)
                if definition is None:
                    raise _invalid(
                        spec.source_file, "tools", f"工具 {name!r} 不在 ToolRegistry 里"
                    )
                if DEVICE_LEVELS.index(definition.device) > DEVICE_LEVELS.index(spec.device):
                    raise _invalid(
                        spec.source_file,
                        "tools",
                        f"工具 {name!r} 的 device={definition.device} "
                        f"高于代理定义的 {spec.device}",
                    )
        self._specs = specs
        self._common_prompt = common_prompt

    def list(self) -> list[AgentSpec]:
        """按 name 排序的全部定义；未加载就抛 :class:`AgentSpecError`。"""
        return list(self._loaded().values())

    def get(self, name: str) -> AgentSpec:
        """按名字取定义；没有就抛 ``AppError(AGENT_SPEC_NOT_FOUND)``。"""
        try:
            return self._loaded()[name]
        except KeyError:
            raise AppError(
                ErrorCode.AGENT_SPEC_NOT_FOUND,
                f"没有名为 {name} 的代理定义，可用的名字见代理定义列表",
            ) from None

    def _loaded(self) -> dict[str, AgentSpec]:
        if self._specs is None:
            raise AgentSpecError("代理定义尚未加载")
        return self._specs

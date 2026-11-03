"""评测场景的 schema 与加载（M68 规格 §4.2.6、10 §6.4）。

一个 YAML 文件一个场景，文件名主干等于 ``id``。所有模型 ``extra="forbid"``；
YAML 用 ruamel.yaml 的 safe/pure 解析（仓库已有依赖，不引入 PyYAML）。加载错误
统一抛 :class:`ScenarioError`，消息里带文件路径，方便直接在评测输出里定位。
"""

from __future__ import annotations

import re
from collections.abc import Collection
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from maa_api.settings import SETTING_KEYS

__all__ = [
    "SCENARIO_ID_RE",
    "AnswerSpec",
    "Entry",
    "EntryKind",
    "GraderSpec",
    "HttpFixture",
    "HumanPolicy",
    "InterruptSpec",
    "Limits",
    "LlmJudgeSpec",
    "OutcomeStateSpec",
    "Scenario",
    "ScenarioError",
    "SeedLog",
    "SeedPipeline",
    "SeedTask",
    "Suite",
    "TextContainsSpec",
    "ToolSequenceSpec",
    "ToolStep",
    "Turn",
    "World",
    "WorldSeed",
    "load_scenario",
    "load_scenarios",
]

SCENARIO_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
Suite = Literal["regression", "capability"]
EntryKind = Literal["legacy_session", "orchestrator", "ticket"]

#: 所有场景模型的公共配置：未知字段一律报错，避免拼错的字段被静默忽略。
_MODEL_CONFIG = ConfigDict(extra="forbid")
#: HttpFixture 的线上字段叫 ``json``，与 Python 的 ``json`` 模块重名，所以属性叫
#: ``json_body``；同时允许按属性名构造，测试与代码里更直观。
_FIXTURE_CONFIG = ConfigDict(extra="forbid", populate_by_name=True)

#: ticket 入口必须给齐的四个字段（规格 §4.2.6）。
_TICKET_FIELDS = ("worker", "title", "brief", "scope")


class ScenarioError(ValueError):
    """场景文件不合法；消息里带文件路径。"""


class Entry(BaseModel):
    model_config = _MODEL_CONFIG

    kind: EntryKind
    worker: str | None = None
    title: str | None = None
    brief: str | None = None
    scope: str | None = None


class AnswerSpec(BaseModel):
    model_config = _MODEL_CONFIG

    answer: list[str] = Field(min_length=1, max_length=6)


class InterruptSpec(BaseModel):
    model_config = _MODEL_CONFIG

    on: Literal["tool_start", "tool_result"]
    tool: str
    delay_seconds: float = Field(default=0.5, ge=0, le=60)


class HumanPolicy(BaseModel):
    model_config = _MODEL_CONFIG

    confirmations: Literal["approve", "reject", "ignore"] = "reject"
    questions: Literal["ignore", "skip"] | AnswerSpec = "ignore"
    interrupt: InterruptSpec | None = None


class Turn(BaseModel):
    model_config = _MODEL_CONFIG

    user: str = Field(min_length=1, max_length=32000)
    human: HumanPolicy = Field(default_factory=HumanPolicy)


class HttpFixture(BaseModel):
    model_config = _FIXTURE_CONFIG

    match: str
    status: int = 200
    json_body: Any = Field(default=None, alias="json")
    body_file: str | None = None

    @model_validator(mode="after")
    def _exactly_one_body(self) -> HttpFixture:
        has_json = self.json_body is not None
        has_file = bool(self.body_file)
        if has_json == has_file:
            raise ValueError("json 与 body_file 必须恰好提供一个")
        return self


class SeedTask(BaseModel):
    model_config = _MODEL_CONFIG

    type_name: str
    status: str = "completed"
    params: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None


class SeedLog(BaseModel):
    model_config = _MODEL_CONFIG

    task: int = 0
    level: Literal["debug", "info", "warning", "error", "critical"] = "info"
    content: str = Field(min_length=1)


class SeedPipeline(BaseModel):
    model_config = _MODEL_CONFIG

    title: str | None = None
    status: Literal["completed", "failed", "cancelled"]
    minutes_ago: int = 10
    error_code: str | None = None
    error_message: str | None = None
    tasks: list[SeedTask]
    logs: list[SeedLog] = Field(default_factory=list)


class WorldSeed(BaseModel):
    model_config = _MODEL_CONFIG

    pipelines: list[SeedPipeline] = Field(default_factory=list)


class World(BaseModel):
    model_config = _MODEL_CONFIG

    core_script: Literal["success", "failure", "stuck", "disconnect"] = "success"
    device_connected: bool = True
    screen: Literal["home", "desktop", "stage", "battle", "settlement"] = "home"
    http: list[HttpFixture] = Field(default_factory=list)
    seed: WorldSeed = Field(default_factory=WorldSeed)


class Limits(BaseModel):
    model_config = _MODEL_CONFIG

    timeout_seconds: float = Field(default=60, ge=1, le=900)
    max_model_calls: int = Field(default=30, ge=1, le=500)


class ToolStep(BaseModel):
    model_config = _MODEL_CONFIG

    tool: str
    args: Any = None
    status: Literal["ok", "error"] | None = None
    error_code: str | None = None
    next: bool = False


class ToolSequenceSpec(BaseModel):
    model_config = _MODEL_CONFIG

    grader: Literal["tool_sequence"]
    match: Literal["subsequence", "exact"] = "subsequence"
    steps: list[ToolStep] = Field(default_factory=list)
    forbid: list[str] = Field(default_factory=list)
    max_calls: dict[str, int] = Field(default_factory=dict)
    min_calls: dict[str, int] = Field(default_factory=dict)


class OutcomeStateSpec(BaseModel):
    model_config = _MODEL_CONFIG

    grader: Literal["outcome_state"]
    runs: Any = None
    pipelines: Any = None
    confirmations: Any = None
    questions: Any = None
    messages: Any = None
    model_calls: Any = None
    requests: Any = None
    spans: Any = None


class TextContainsSpec(BaseModel):
    model_config = _MODEL_CONFIG

    grader: Literal["text_contains"]
    target: Literal["final", "all"] = "final"
    all_of: list[str] = Field(default_factory=list)
    any_of: list[str] = Field(default_factory=list)
    none_of: list[str] = Field(default_factory=list)
    ignore_case: bool = True


class LlmJudgeSpec(BaseModel):
    model_config = _MODEL_CONFIG

    grader: Literal["llm_judge"]
    rubric: str = Field(min_length=1, max_length=2000)
    pass_score: float = Field(default=0.7, ge=0, le=1)


GraderSpec = Annotated[
    ToolSequenceSpec | OutcomeStateSpec | TextContainsSpec | LlmJudgeSpec,
    Field(discriminator="grader"),
]


class Scenario(BaseModel):
    """一个评测场景；``cassette_name`` 给出对应的录制文件名。"""

    model_config = _MODEL_CONFIG

    id: str = Field(pattern=SCENARIO_ID_RE.pattern)
    title: str = Field(min_length=1, max_length=40)
    suite: Suite
    agent: str
    source: str | None = None
    tags: list[str] = Field(default_factory=list)
    entry: Entry
    settings: dict[str, Any] = Field(default_factory=dict)
    world: World = Field(default_factory=World)
    turns: list[Turn] = Field(default_factory=list)
    expect: list[GraderSpec] = Field(min_length=1)
    cassette: str | None = None
    limits: Limits = Field(default_factory=Limits)

    @field_validator("settings")
    @classmethod
    def _known_settings(cls, value: dict[str, Any]) -> dict[str, Any]:
        unknown = sorted(key for key in value if key not in SETTING_KEYS)
        if unknown:
            raise ValueError(f"未知设置项：{', '.join(unknown)}")
        return value

    @model_validator(mode="after")
    def _cross_field_rules(self) -> Scenario:
        if self.suite == "regression" and any(
            spec.grader == "llm_judge" for spec in self.expect
        ):
            raise ValueError("regression 场景不能使用 llm_judge")
        if self.entry.kind in ("legacy_session", "orchestrator") and not self.turns:
            raise ValueError(f"{self.entry.kind} 入口至少要有一条 turns")
        if self.entry.kind == "ticket":
            missing = [
                name
                for name in _TICKET_FIELDS
                if not getattr(self.entry, name) or not str(
                    getattr(self.entry, name)
                ).strip()
            ]
            if missing:
                raise ValueError(f"ticket 入口缺少 {', '.join(missing)}")
        return self

    def cassette_name(self) -> str:
        return self.cassette or f"{self.id}.jsonl"


def _format_validation_error(path: Path, exc: ValidationError) -> ScenarioError:
    first = exc.errors()[0]
    location = ".".join(str(part) for part in first["loc"])
    message = str(first["msg"])
    if message.startswith("Value error, "):
        message = message[len("Value error, ") :]
    prefix = f"{path}"
    if location:
        return ScenarioError(f"{prefix}：{location}：{message}")
    return ScenarioError(f"{prefix}：{message}")


def _read_yaml(path: Path) -> Any:
    yaml = YAML(typ="safe", pure=True)
    try:
        with path.open("r", encoding="utf-8") as handle:
            return yaml.load(handle)
    except FileNotFoundError as exc:
        raise ScenarioError(f"{path}：场景文件不存在") from exc
    except UnicodeDecodeError as exc:
        raise ScenarioError(f"{path}：场景文件不是合法 UTF-8：{exc}") from exc
    except YAMLError as exc:
        raise ScenarioError(f"{path}：不是合法 YAML：{exc}") from exc


def load_scenario(path: Path) -> Scenario:
    """读一个场景文件并校验；``id`` 必须等于文件名主干。"""
    target = Path(path)
    raw = _read_yaml(target)
    if not isinstance(raw, dict):
        raise ScenarioError(f"{target}：场景文件必须是键值映射")
    try:
        scenario = Scenario.model_validate(raw)
    except ValidationError as exc:
        raise _format_validation_error(target, exc) from exc
    if scenario.id != target.stem:
        raise ScenarioError(
            f"{target}：id {scenario.id!r} 必须等于文件名主干 {target.stem!r}"
        )
    return scenario


def load_scenarios(
    directory: Path,
    *,
    suites: Collection[Suite] | None = None,
    ids: Collection[str] = (),
    agent: str | None = None,
    tags: Collection[str] = (),
) -> list[Scenario]:
    """加载目录下全部场景，按 ``id`` 排序；可按套件、id、代理与标签过滤。"""
    root = Path(directory)
    if not root.is_dir():
        raise ScenarioError(f"{root}：场景目录不存在")
    paths = sorted(
        path
        for path in root.iterdir()
        if path.is_file() and path.suffix in (".yaml", ".yml")
    )
    scenarios: list[Scenario] = []
    seen: dict[str, Path] = {}
    for path in paths:
        scenario = load_scenario(path)
        previous = seen.get(scenario.id)
        if previous is not None:
            raise ScenarioError(
                f"{root}：场景 id {scenario.id!r} 重复（{previous} 与 {path}）"
            )
        seen[scenario.id] = path
        scenarios.append(scenario)
    wanted_ids = set(ids)
    wanted_suites = set(suites) if suites is not None else None
    wanted_tags = set(tags)
    selected = [
        scenario
        for scenario in scenarios
        if (wanted_suites is None or scenario.suite in wanted_suites)
        and (not wanted_ids or scenario.id in wanted_ids)
        and (agent is None or scenario.agent == agent)
        and (not wanted_tags or wanted_tags.intersection(scenario.tags))
    ]
    return sorted(selected, key=lambda scenario: scenario.id)

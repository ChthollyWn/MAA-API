"""``compaction_summary`` schema：会话较早消息的结构化摘要（M74 规格 §4.2.1、06 §4.3）。

摘要代理把「已有摘要」「需要并入的消息」「确定性状态」三节合并成一份新摘要；所有
对象 ``extra="forbid"``，长度、枚举与列表上限逐字照 06 §4.3，列表字段默认空列表。
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from maa_api.agent.llm.structured import register_schema

__all__ = [
    "Action",
    "CompactionSummary",
    "Decision",
    "ErrorNote",
    "Fact",
    "OpenItem",
    "Ref",
    "UserRequest",
]


class UserRequest(BaseModel):
    """用户提出过的一条要求；``seq`` 为空表示合并转述了多条消息。"""

    model_config = ConfigDict(extra="forbid")

    seq: int | None
    text: str = Field(min_length=1, max_length=500)
    status: Literal["done", "in_progress", "pending", "dropped"]


class Decision(BaseModel):
    """已经定下的一件事，以及是谁定的。"""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=200)
    by: Literal["user", "agent"]
    seq: int | None


class Fact(BaseModel):
    """查到的一条事实或关键数字，``source`` 是工具名或 ``user``。"""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=64)
    seq: int | None


class Action(BaseModel):
    """一个有副作用的操作及结果；``refs`` 是 ``pipeline:<id>`` 这类引用。"""

    model_config = ConfigDict(extra="forbid")

    tool: str = Field(min_length=1, max_length=64)
    outcome: Literal["ok", "error", "rejected", "expired", "pending"]
    text: str = Field(min_length=1, max_length=200)
    refs: list[Annotated[str, Field(max_length=80)]] = Field(
        default_factory=list, max_length=5
    )


class OpenItem(BaseModel):
    """没做完或正在等待的一件事。"""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=200)
    kind: Literal["todo", "waiting_user", "waiting_event", "blocked"]


class ErrorNote(BaseModel):
    """遇到过的错误码，以及是否已经解决。"""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(max_length=48)
    text: str = Field(min_length=1, max_length=200)
    resolved: bool


class Ref(BaseModel):
    """之后可能要按 ID 召回的一个对象。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "ticket",
        "pipeline",
        "artifact",
        "checkpoint",
        "report",
        "confirmation",
        "question",
        "schedule",
        "screenshot",
        "run",
        "plan",
    ]
    id: str = Field(min_length=1, max_length=64)
    label: str = Field(max_length=60)


class CompactionSummary(BaseModel):
    """摘要代理的最终输出：叙述、分类条目、引用与下一步。"""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=2000)
    user_requests: list[UserRequest] = Field(default_factory=list, max_length=20)
    decisions: list[Decision] = Field(default_factory=list, max_length=30)
    facts: list[Fact] = Field(default_factory=list, max_length=50)
    actions: list[Action] = Field(default_factory=list, max_length=50)
    open_items: list[OpenItem] = Field(default_factory=list, max_length=20)
    errors: list[ErrorNote] = Field(default_factory=list, max_length=20)
    refs: list[Ref] = Field(default_factory=list, max_length=100)
    user_preferences: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=20
    )
    next_step: str = Field(default="", max_length=300)


register_schema("compaction_summary", CompactionSummary)

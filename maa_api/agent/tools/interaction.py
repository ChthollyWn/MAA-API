"""Agent 主动提问工具（M21，规格 §3.1）。

只有 ``ask_user`` 一个工具：它本身**不执行任何操作**，只是把「需要用户裁决的参数」
变成一张带选项的卡片，然后由 runtime 挂起本轮等待。真正的等待、落库与终态判定都在
:class:`~maa_api.agent.questions.QuestionService` 里，这里只负责参数形状与 REST 拒绝
——工具实现不做「谁在等、等多久」的判断，那是 runtime 的事。

REST handler 直接失败（规格 §2.2）：``/api/agent/tools/ask_user/invoke`` 是一次同步
HTTP 请求，没有人会挂在那里等 10 分钟，让调用方拿到明确的错误比让它超时好。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk
from maa_api.domain.errors import AppError, ErrorCode

__all__ = ["AskUserOption", "AskUserParams", "register_tools"]

ASK_USER_DESCRIPTION = (
    "需要用户在若干候选之间做决定时（例如客户端渠道、刷哪个关卡、理智药与次数策略），"
    "用本工具发一张选择卡片，不要写在回复正文里让用户打字回答。"
    "用法：把每个候选写成 options 里的一项 label（2–6 个，<=80 字），需要解释时补 "
    "description；问题本身只写「要让用户定什么」，不要把候选或推荐答案写进 question。"
    "multiple=true 表示可多选，默认单选。"
    "返回值里 status=answered 时，selected 就是用户的裁决，直接照它执行、不要再问一次；"
    "status=expired 或 skipped 表示用户没有选择，本轮会就此结束，不要重试同一个提问。"
)


class AskUserOption(BaseModel):
    """一个候选项：label 是用户看到的选项正文，也是回答时回填的值。"""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=200)


class AskUserParams(BaseModel):
    """``ask_user`` 的参数（规格 §3.1）。

    上限（2–6 个选项、选项 80 字、问题 500 字）是**卡片渲染**的约束：一屏放得下的
    选项才叫选择题，超出的部分应改成多轮提问或正文询问。
    """

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=500)
    options: list[AskUserOption] = Field(min_length=2, max_length=6)
    multiple: bool = False


def register_tools(registry: ToolRegistry) -> None:
    """注册 ``interaction`` 工具组（当前只有一个 ``ask_user``）。"""

    registry.register(
        ToolDefinition(
            name="ask_user",
            group="interaction",
            risk=ToolRisk.SAFE,
            description=ASK_USER_DESCRIPTION,
            params_model=AskUserParams,
            handler=ask_user,
            # 提问不授权任何操作，所以 risk=SAFE；但**必须**与写操作串行：
            # read_only=False 让整批走串行路径，排在后面的 submit_pipeline 一定等用户
            # 答完才执行（规格 §3.1「先问再动手」）。
            read_only=False,
            model_visible=True,
        )
    )


async def ask_user(params: AskUserParams, ctx: ToolContext) -> dict[str, Any]:
    """REST 调用一律失败：提问只能在会话内由 runtime 挂起等待。"""
    del params, ctx
    raise AppError(
        ErrorCode.INVALID_PARAMETER,
        "ask_user 只能由内置 Agent 在会话内调用：REST 调用无法等待用户选择",
    )

"""M70 T9：工单标题生成（spec §4.7.7、§6 的一条）。

模型用 ``tests/fakes.fake_llm.FakeLLM``；运行服务与注册表用轻量替身，账本由
``TicketTitler`` 在运行服务没有 ``session_factory`` 时退化成空账本，测试不碰库。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from maa_api.agent.workers.titler import TicketTitler
from maa_api.domain.enums import AgentRunStatus
from maa_api.domain.errors import AppError, ErrorCode
from tests.fakes.fake_llm import FakeLLM, text_response

_TITLER_SPEC = SimpleNamespace(
    name="titler",
    kind="background",
    body="你给工单起标题。",
    model_role="title",
    spec_version="abcdef123456",
)
_CONTENT = "帮我刷 1-7 五次"
_FIRST_LINE = "刷 1-7 五次并领取日常奖励和信用"


class _Registry:
    def get(self, name: str) -> SimpleNamespace:
        if name == "titler":
            return _TITLER_SPEC
        raise AppError(
            ErrorCode.AGENT_SPEC_NOT_FOUND, f"没有名为 {name} 的代理定义"
        )


class _Handle:
    def __init__(self, llm: FakeLLM) -> None:
        self.client = llm

    async def aclose(self) -> None:
        return None


class _Router:
    def __init__(self, llm: FakeLLM) -> None:
        self.llm = llm
        self.roles: list[str] = []
        self.profile = SimpleNamespace(
            model="title-model",
            base_url="https://llm.test/v1",
            api_key="sk-test",
            supports_json_schema=False,
        )

    async def resolve(self, role: str) -> SimpleNamespace:
        self.roles.append(str(role))
        return self.profile

    def client_for(self, profile: SimpleNamespace) -> _Handle:
        return _Handle(self.llm)


class _RunService:
    """记录 start / finish；没有 ``session_factory``，标题调用不写成本账本。"""

    def __init__(self) -> None:
        self.started: list[dict] = []
        self.finished: list[dict] = []
        self._sequence = 0

    async def start(self, **kwargs) -> SimpleNamespace:
        self.started.append(kwargs)
        self._sequence += 1
        return SimpleNamespace(id=f"title-run-{self._sequence}")

    async def finish(self, run_id: str, *, status, finish_reason: str) -> None:
        self.finished.append(
            {
                "run_id": run_id,
                "status": status,
                "finish_reason": finish_reason,
            }
        )


def _titler(llm: FakeLLM, runs: _RunService) -> tuple[TicketTitler, _Router]:
    router = _Router(llm)
    return (
        TicketTitler(
            spec_registry=_Registry(), run_service=runs, model_router=router
        ),
        router,
    )


def test_title_from_schema_and_fallback() -> None:
    async def scenario() -> None:
        # 成功：取结构化结果；系统提示是 titler 正文，输入截到 300 字。
        llm = FakeLLM(
            [
                text_response('{"title": "刷 1-7 五次"}'),
                text_response('{"title": "长说明"}'),
            ]
        )
        runs = _RunService()
        titler, router = _titler(llm, runs)

        assert await titler.title_for(_CONTENT) == "刷 1-7 五次"
        assert runs.started[0]["kind"] == "title"
        assert runs.started[0]["agent_name"] == "titler"
        assert runs.started[0]["session_id"] is None
        assert runs.finished[-1]["status"] == AgentRunStatus.SUCCEEDED
        assert router.roles == ["title"]
        system, user = llm.requests[0][1]
        assert system == {"role": "system", "content": "你给工单起标题。"}
        # 档案不支持 JSON Schema：约束提示词空一行后追加到最后一条 user 消息。
        assert user["role"] == "user"
        assert user["content"].startswith(f"{_CONTENT}\n\n只输出一个 JSON 对象")

        assert await titler.title_for("长" * 400) == "长说明"
        long_input = llm.requests[1][1][-1]["content"]
        assert long_input.startswith("长" * 300)
        assert len("长" * 300) < len(long_input)

        # 失败：任何异常都退回第一行前 16 字，并把那次运行收成 failed。
        failing = FakeLLM([AppError(ErrorCode.LLM_REQUEST_FAILED, "上游挂了")])
        fallback_runs = _RunService()
        fallback_titler, _ = _titler(failing, fallback_runs)
        result = await fallback_titler.title_for(f"{_FIRST_LINE}\n第二行")
        assert result == _FIRST_LINE[:16]
        assert fallback_runs.finished[-1]["status"] == AgentRunStatus.FAILED

        # 全空：不调模型，直接用「新工单」。
        assert await fallback_titler.title_for("   ") == "新工单"

    asyncio.run(scenario())

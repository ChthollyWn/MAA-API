"""M66 T4：启动恢复与运行服务的装配接线（docs/v3/specs/m66-durable-runs §4.2.6）。

装配缺陷只有在 ``create_app()`` 的 lifespan 里才看得见：``RunService`` 与
``RunRecovery`` 必须真的建出来、绑到 ``app.state`` 上，并且恢复发生在所有新运行
之前。这里跑两遍 lifespan：第一遍只留下上一进程的运行，第二遍断言它被收尾。
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from maa_api.agent.runs import RunService
from maa_api.agent.runs.recovery import (
    RECOVERY_FINISH_REASON,
    RECOVERY_NOTICE,
    RECOVERY_TOOL_STUB,
)
from maa_api.db import session as db_session
from maa_api.db.models import AgentMessage, AgentRun, AgentSession
from maa_api.db.repositories.agent import (
    AgentMessageRepository,
    AgentRunRepository,
    AgentSessionRepository,
)
from maa_api.domain.enums import AgentRunKind, AgentRunStatus
from maa_api.main import create_app

MODEL = "wiring-model"
PREVIOUS_BOOT = "previous-boot"


class _BindableRuntime:
    """lifespan 的 runtime 替身：T5 才在 ``AgentRuntime`` 上加 ``bind_run_service``。

    T4 只验证装配会调用这个钩子；替身没有真实运行能力，启动路径不需要它。
    """

    def __init__(self) -> None:
        self.run_service: RunService | None = None

    def bind_run_service(self, run_service: RunService) -> None:
        self.run_service = run_service


def _runtime_factory(
    _confirmation_service: Any, *, question_service: Any = None
) -> _BindableRuntime:
    del question_service
    return _BindableRuntime()


def _lifespan(app):
    """进入/退出应用 lifespan 的上下文（不依赖 HTTP 栈）。"""

    from fastapi.testclient import TestClient

    return TestClient(app, raise_server_exceptions=False)


def test_lifespan_sets_boot_id_and_run_service(tmp_settings, isolated_db) -> None:
    """``boot_id`` 与 ``RunService`` 必须由 lifespan 建出并绑定到 runtime。"""

    app = create_app()
    app.state.agent_runtime_factory = _runtime_factory
    with _lifespan(app):
        boot_id = app.state.boot_id
        assert isinstance(boot_id, str)
        assert len(boot_id) == 36
        assert str(uuid.UUID(boot_id)) == boot_id

        run_service = app.state.run_service
        assert isinstance(run_service, RunService)
        assert run_service.boot_id == boot_id
        assert app.state.agent_runtime.run_service is run_service


def test_lifespan_recovers_runs_left_by_previous_process(
    tmp_settings, isolated_db
) -> None:
    """第二次启动必须收尾上一进程留下的活动运行，并给会话补桩与恢复提示。"""

    app = create_app()
    session_id = "session-previous-boot"
    run_id = "run-previous-boot"

    async def seed_previous_process() -> None:
        async with db_session.session_factory() as session:
            await AgentSessionRepository(session).create(
                AgentSession(id=session_id, model=MODEL, message_count=1)
            )
            await AgentMessageRepository(session).create(
                AgentMessage(
                    session_id=session_id,
                    seq=0,
                    role="assistant",
                    tool_calls=[
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "get_device_status",
                                "arguments": "{}",
                            },
                        }
                    ],
                )
            )
            await AgentRunRepository(session).create(
                AgentRun(
                    id=run_id,
                    session_id=session_id,
                    root_run_id=run_id,
                    agent_name="legacy_chat",
                    kind=AgentRunKind.LEGACY_TURN,
                    status=AgentRunStatus.RUNNING,
                    boot_id=PREVIOUS_BOOT,
                )
            )
            await session.commit()

    with _lifespan(app) as client:
        first_boot = app.state.boot_id
        # 恢复只发生在启动时；在第一遍 lifespan 里写，第二遍才轮到它。
        client.portal.call(seed_previous_process)

    with _lifespan(app) as client:
        assert app.state.boot_id != first_boot

        async def read_recovered() -> tuple[AgentRun, list[AgentMessage], AgentSession]:
            async with db_session.session_factory() as session:
                run = await session.get(AgentRun, run_id, populate_existing=True)
                messages = await AgentMessageRepository(session).list_by_session(
                    session_id
                )
                stored_session = await session.get(
                    AgentSession, session_id, populate_existing=True
                )
            assert run is not None
            assert stored_session is not None
            return run, messages, stored_session

        run, messages, stored_session = client.portal.call(read_recovered)
        assert run.status == AgentRunStatus.INTERRUPTED
        assert run.finish_reason == RECOVERY_FINISH_REASON
        assert run.finished_at is not None
        assert [message.role for message in messages] == [
            "assistant",
            "tool",
            "system",
        ]
        stub = messages[1]
        assert stub.tool_call_id == "call-1"
        assert stub.tool_name == "get_device_status"
        assert stub.run_id == run_id
        assert json.loads(stub.content or "") == RECOVERY_TOOL_STUB
        notice = messages[2]
        assert notice.content == RECOVERY_NOTICE
        assert notice.run_id == run_id
        assert notice.finish_reason == RECOVERY_FINISH_REASON
        assert stored_session.message_count == 3

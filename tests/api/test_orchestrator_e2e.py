"""M70 端到端：总控 → 工单 → Worker → 流水线 → 汇报 → 总控（spec §6 的七条）。

用生产 ``create_app()`` 装配与应用 lifespan、真实路由 / 服务 / 临时 SQLite，只有
MaaCore 与设备边界换成 ``tests/fakes/lifespan.py`` 的适配器、模型换成
``tests/fakes/scripted_llm.py`` 的按调用方分队列替身。FakeAsst 用 success 剧本，
所以提交流水线会真的走完队列与终态钩子。

几条刻意的测试设施：

- **LLM 环境变量在进入 lifespan 之后才设并 refresh**：M67 的档案解析会在启动期
  创建总控会话时探查一次模型；启动时没有 key 就立即按未配置收尾，不碰网络，也不
  让每个用例白等重试。进入 lifespan 后设值再 ``setting_service.refresh()``，效果与
  启动时读环境变量一致。
- **动态知识段置空**：``render_dynamic_context`` 会去 PRTS 取当期活动/卡池，端到端
  不该依赖外网；稳定段（索引、术语表、档案）照常从临时库读。
- ``TestClient`` 会缓冲整条 SSE 响应，``client.stream`` 拿不到增量帧；SSE 调用在
  后台线程里 ``client.post`` 读完再 ``parse_events``。需要与流交互的用例（第二条
  消息、取消）靠 ``SseCall`` 线程、REST 轮询与 ``Hold.started`` 协调，不猜时间。
- 每个用例结束都放开仍被 Hold 的调用，后台任务随 lifespan 关闭；没有脚本遗漏
  时 ``ScriptedLLM.remaining()`` 全为 0。
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from functools import partial
from typing import Any

import pytest
from sqlalchemy import select

from maa_api.agent.harness.events import RunEvent
from maa_api.db import session as db_session
from maa_api.db.models import (
    AgentAudit,
    AgentInbox,
    AgentSession,
    Confirmation,
    Pipeline,
)
from maa_api.domain.enums import PipelineStatus
from maa_api.main import create_app
from tests.fakes.fake_llm import (
    FakeResponse,
    parse_events,
    text_response,
    tool_call,
    tool_response,
)
from tests.fakes.lifespan import install_fake_lifespan
from tests.fakes.scripted_llm import Hold, ScriptedLLM, install_llm, route_by_marker

#: 异步等待期限（spec §6 风险表：所有等待都有 8 秒期限）。
WAIT_SECONDS = 8.0
#: 轮询间隔。
POLL_SECONDS = 0.02


# ----------------------------------------------------------------------
# 夹具
# ----------------------------------------------------------------------
@dataclass
class E2E:
    """一个用例的应用、客户端与共享状态。"""

    app: Any
    client: Any
    llm: ScriptedLLM | None = None
    holds: list[Hold] = field(default_factory=list)
    events: list[RunEvent] = field(default_factory=list)

    @property
    def portal(self) -> Any:
        return self.client.portal

    def use_llm(self, scripts: dict[str, Any], *, route: Any = route_by_marker) -> ScriptedLLM:
        """登记脚本、收集 Hold 并装到 M67 的测试替换点。"""
        llm = ScriptedLLM(scripts, route=route)
        for items in scripts.values():
            for item in items:
                if isinstance(item, Hold):
                    self.holds.append(item)
        install_llm(self.app, llm)
        self.llm = llm
        return llm


@pytest.fixture
def e2e(tmp_settings, isolated_db, make_client, monkeypatch) -> Any:
    """生产装配 + 假 MaaCore/设备 + 临时库；退出时放开所有 Hold。"""
    app = create_app()
    install_fake_lifespan(app)
    client = make_client(app)

    monkeypatch.setenv("MAA_LLM_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("MAA_LLM_API_KEY", "sk-test")
    monkeypatch.setenv("MAA_LLM_MODEL", "test-model")
    client.portal.call(app.state.setting_service.refresh)

    async def _empty_dynamic() -> str:
        return ""

    app.state.knowledge_service.render_dynamic_context = _empty_dynamic

    state = E2E(app=app, client=client)
    received: list[RunEvent] = []

    async def sink(event: RunEvent) -> None:
        received.append(event)

    unsubscribe = client.portal.call(app.state.run_event_bus.subscribe, sink)
    state.events = received
    try:
        yield state
    finally:
        for hold in state.holds:
            hold.gate.set()
        try:
            unsubscribe()
        except Exception:  # noqa: BLE001 - 收尾尽力而为
            pass


# ----------------------------------------------------------------------
# 等待与读取
# ----------------------------------------------------------------------
def wait_until(condition, *, message: str, timeout: float = WAIT_SECONDS):
    """轮询直到 ``condition()`` 返回真值；超时把最后一次结果带进断言。"""
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        last = condition()
        if last:
            return last
        time.sleep(POLL_SECONDS)
    raise AssertionError(f"{message} 未在 {timeout:.0f}s 内成立（最后结果={last!r}）")


@dataclass
class SseCall:
    """在后台线程发一个会阻塞到流关闭的 SSE 请求。

    ``TestClient`` 会把 ``StreamingResponse`` 的整个响应体缓冲完才返回，所以
    ``client.stream`` 拿不到增量帧；需要与流交互的用例把完整请求放进线程，主线程
    用数据库 / REST 状态协调，最后一并取回 ``response.text`` 再解析。
    """

    client: Any
    url: str
    payload: dict[str, Any]
    method: str = "POST"
    response: Any = None
    error: BaseException | None = None
    _thread: threading.Thread | None = None

    def start(self) -> "SseCall":
        self._thread = threading.Thread(
            target=self._run, name="m70-e2e-sse", daemon=True
        )
        self._thread.start()
        return self

    def _run(self) -> None:
        try:
            if self.method == "POST":
                self.response = self.client.post(self.url, json=self.payload)
            else:
                self.response = self.client.get(self.url, params=self.payload)
        except BaseException as exc:  # noqa: BLE001 - 交给主线程断言
            self.error = exc

    def finish(self, timeout: float = WAIT_SECONDS):
        assert self._thread is not None
        self._thread.join(timeout)
        assert not self._thread.is_alive(), "SSE 请求没有在期限内完成"
        assert self.error is None, f"SSE 请求异常：{self.error!r}"
        assert self.response.status_code == 200, (
            f"SSE 状态码 {self.response.status_code}: {self.response.text[:300]}"
        )
        return parse_events([self.response.text])


def post_sse(e2e: E2E, url: str, content: str) -> list[tuple[str, dict[str, Any]]]:
    """同步读完一条 SSE（流在目标运行 ``run_end`` 后关闭）。"""
    reader = SseCall(e2e.client, url, {"content": content}).start()
    return reader.finish()


def event_names(events: list[tuple[str, Any]]) -> list[str]:
    return [name for name, _data in events]


def event_data(events: list[tuple[str, Any]], name: str) -> dict[str, Any]:
    return next(data for event, data in events if event == name)


def text_deltas(events: list[tuple[str, Any]]) -> str:
    return "".join(
        str(data.get("text") or "") for event, data in events if event == "text_delta"
    )


# ----------------------------------------------------------------------
# 领域读取与断言辅助
# ----------------------------------------------------------------------
def orchestrator_state(e2e: E2E) -> dict[str, Any]:
    response = e2e.client.get("/api/agent/orchestrator")
    assert response.status_code == 200, response.text
    return response.json()


def orchestrator_messages(e2e: E2E) -> dict[str, Any]:
    response = e2e.client.get("/api/agent/orchestrator/messages")
    assert response.status_code == 200, response.text
    return response.json()


def ticket_detail(e2e: E2E, ticket_id: str) -> dict[str, Any]:
    response = e2e.client.get(f"/api/agent/tickets/{ticket_id}")
    assert response.status_code == 200, response.text
    return response.json()


def list_runs(e2e: E2E, session_id: str) -> dict[str, Any]:
    response = e2e.client.get(
        "/api/agent/runs", params={"session_id": session_id}
    )
    assert response.status_code == 200, response.text
    return response.json()


def wait_orchestrator_created(e2e: E2E) -> str:
    """等总控建出工单，返回 ticket_id。"""
    def _find() -> str | None:
        response = e2e.client.get("/api/agent/tickets")
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        return items[0]["id"] if items else None

    return wait_until(_find, message="总控建出工单")


def wait_ticket_status(
    e2e: E2E, ticket_id: str, status: str
) -> dict[str, Any]:
    def _check() -> dict[str, Any] | None:
        detail = ticket_detail(e2e, ticket_id)
        return detail if detail["ticket"]["status"] == status else None

    return wait_until(_check, message=f"工单状态变为 {status}")


def wait_session_runs(
    e2e: E2E, session_id: str, count: int, *, terminal: bool = True
) -> dict[str, Any]:
    """等某会话的根运行到 ``count`` 条；``terminal`` 时还要求全部已结束。"""

    def _check() -> dict[str, Any] | None:
        data = list_runs(e2e, session_id)
        items = data["items"]
        if data["total"] != count or len(items) != count:
            return None
        if terminal and any(
            item["status"] in {"running", "waiting_human"} for item in items
        ):
            return None
        return data

    return wait_until(_check, message=f"会话 {session_id} 的根运行到 {count} 条")


def wait_message_contains(e2e: E2E, text: str) -> dict[str, Any]:
    """等总控消息里出现某段正文（渲染后的 user 消息或助手回复）。"""

    def _check() -> dict[str, Any] | None:
        page = orchestrator_messages(e2e)
        for item in page["items"]:
            if text in (item.get("content") or ""):
                return page
        return None

    return wait_until(_check, message=f"总控消息出现 {text!r}")


async def _inbox_rows(session_id: str) -> list[tuple[str, str, dict[str, Any]]]:
    async with db_session.session_factory() as db:
        rows = (
            await db.execute(
                select(AgentInbox)
                .where(AgentInbox.session_id == session_id)
                .order_by(AgentInbox.id.asc())
            )
        ).scalars().all()
    return [
        (str(row.kind), str(row.status), dict(row.payload or {}))
        for row in rows
    ]


def inbox_rows(e2e: E2E, session_id: str) -> list[tuple[str, str, dict[str, Any]]]:
    return e2e.portal.call(_inbox_rows, session_id)


async def _count_rows(model: Any, **filters: Any) -> int:
    async with db_session.session_factory() as db:
        statement = select(model)
        for name, value in filters.items():
            statement = statement.where(getattr(model, name) == value)
        return len((await db.execute(statement)).scalars().all())


def count_rows(e2e: E2E, model: Any, **filters: Any) -> int:
    return e2e.portal.call(partial(_count_rows, model, **filters))


async def _subagent_sessions(parent_id: str) -> list[dict[str, Any]]:
    async with db_session.session_factory() as db:
        rows = (
            await db.execute(
                select(AgentSession).where(
                    AgentSession.parent_session_id == parent_id
                )
            )
        ).scalars().all()
    return [
        {"session_id": row.id, "kind": str(row.kind), "title": row.title}
        for row in rows
    ]


def subagent_sessions(e2e: E2E, parent_id: str) -> list[dict[str, Any]]:
    return e2e.portal.call(_subagent_sessions, parent_id)


async def _insert_late_pipeline(session_id: str) -> str:
    async with db_session.session_factory() as db:
        pipeline = Pipeline(
            source="agent",
            priority=1,
            status=PipelineStatus.COMPLETED.value,
            title="迟到的流水线",
            task_count=0,
            agent_session_id=session_id,
        )
        db.add(pipeline)
        await db.commit()
        return pipeline.id


def insert_late_pipeline(e2e: E2E, session_id: str) -> str:
    return e2e.portal.call(_insert_late_pipeline, session_id)


async def _open_ticket(e2e: E2E, **overrides: Any) -> Any:
    kwargs: dict[str, Any] = {
        "kind": "general",
        "title": "刷 1-7",
        "brief": "帮用户刷 1-7 一次。",
        "scope": "刷 1-7 一次",
        "origin": "system",
        "strength": "inferred",
        "source": "system",
        "sender": "orchestrator",
        "priority": 1,
    }
    kwargs.update(overrides)
    return await e2e.app.state.worker_service.open_ticket(**kwargs)


def open_ticket(e2e: E2E, **overrides: Any) -> Any:
    return e2e.portal.call(partial(_open_ticket, e2e, **overrides))


# ----------------------------------------------------------------------
# 脚本工厂
# ----------------------------------------------------------------------
def create_ticket_call(
    call_id: str = "o-create",
    *,
    title: str = "刷 1-7",
    brief: str = "帮用户刷 1-7 一次。",
    scope: str = "刷 1-7 一次",
) -> dict[str, Any]:
    return tool_call(
        call_id,
        "create_ticket",
        {"kind": "general", "title": title, "brief": brief, "scope": scope},
    )


def submit_call(
    call_id: str = "w-submit",
    *,
    title: str = "刷 1-7",
    medicine: int | None = None,
    times: int = 1,
) -> dict[str, Any]:
    task: dict[str, Any] = {"name": "Fight", "stage": "1-7", "times": times}
    if medicine is not None:
        task["medicine"] = medicine
    return tool_call(call_id, "submit_pipeline", {"title": title, "tasks": [task]})


def report_call(
    call_id: str,
    status: str,
    summary: str,
    *,
    index: int = 0,
    human_action: dict[str, Any] | None = None,
) -> dict[str, Any]:
    arguments: dict[str, Any] = {"status": status, "summary": summary}
    if human_action is not None:
        arguments["human_action"] = human_action
    return tool_call(call_id, "report", arguments, index=index)


def pipeline_id_from_history(messages: list[dict[str, Any]]) -> str:
    """从工具结果消息里找 ``submit_pipeline`` 的 pipeline_id。

    上下文组装会把 ``tool_name`` 从消息里去掉，所以只认工具消息的 JSON 正文。
    """
    for message in messages:
        if message.get("role") != "tool":
            continue
        try:
            payload = json.loads(message.get("content") or "null")
        except (TypeError, ValueError):
            continue
        body = payload.get("result", payload) if isinstance(payload, dict) else None
        if isinstance(body, dict) and body.get("pipeline_id"):
            return str(body["pipeline_id"])
    raise AssertionError("上下文里没有 submit_pipeline 的工具结果")


def ready_after_pipeline(messages: list[dict[str, Any]]) -> FakeResponse:
    """第二轮 Worker：先 get_pipeline 看终态，再以 ready 收尾。"""
    pipeline_id = pipeline_id_from_history(messages)
    return tool_response(
        tool_call("w-get", "get_pipeline", {"pipeline_id": pipeline_id}),
        report_call(
            "w-ready", "ready", "1-7 已完成，战斗 1 次。", index=1
        ),
    )


RESEARCHER_REPORT = {
    "summary": "1-7 的掉率样本足够，可以刷。",
    "findings": [
        {
            "title": "1-7 有稳定掉落",
            "detail": "企鹅物流样本数大于 100。",
            "evidence": ["企鹅物流·1-7"],
        }
    ],
    "uncertainties": [],
    "artifacts": [],
}


def researcher_response() -> list[dict[str, Any]]:
    return text_response(json.dumps(RESEARCHER_REPORT, ensure_ascii=False))


# ----------------------------------------------------------------------
# 用例
# ----------------------------------------------------------------------
def test_daily_flow_from_message_to_ready_report(e2e: E2E) -> None:
    """总控建单 → Worker 提交流水线 → progress → 终态唤醒 → ready → 总控转述。"""
    llm = e2e.use_llm(
        {
            "orchestrator": [
                tool_response(create_ticket_call()),
                text_response("工单已经建好，Worker 正在处理。"),
                text_response("刷完了，1-7 已完成。"),
            ],
            "general": [
                tool_response(
                    submit_call(),
                    report_call(
                        "w-progress", "progress", "流水线已提交，等结果。", index=1
                    ),
                ),
                ready_after_pipeline,
            ],
        }
    )

    events = post_sse(e2e, "/api/agent/orchestrator/messages", "把 1-7 刷一次")
    names = event_names(events)
    assert names[0] == "run_start"
    assert "ticket_created" in names
    assert "tool_result" in names
    assert names[-1] == "run_end"
    assert text_deltas(events) == "工单已经建好，Worker 正在处理。"

    ticket_id = event_data(events, "ticket_created")["ticket_id"]
    detail = wait_ticket_status(e2e, ticket_id, "done")
    worker_session_id = detail["worker_session_id"]

    # 两轮 Worker、两轮总控，全部正常收尾。
    worker_runs = wait_session_runs(e2e, worker_session_id, 2)
    assert [item["kind"] for item in worker_runs["items"]] == [
        "worker_turn",
        "worker_turn",
    ]
    assert all(item["status"] == "succeeded" for item in worker_runs["items"])
    orchestrator_session_id = orchestrator_state(e2e)["session_id"]
    orchestrator_runs = wait_session_runs(e2e, orchestrator_session_id, 2)
    assert all(
        item["status"] == "succeeded" for item in orchestrator_runs["items"]
    )

    # 汇报两条，只有 ready 转发；progress 不进总控。
    reports = {
        item["status"]: item for item in detail["recent_reports"]
    }
    assert set(reports) == {"progress", "ready"}
    assert reports["progress"]["forwarded"] is False
    assert reports["ready"]["forwarded"] is True

    page = wait_message_contains(e2e, "刷完了，1-7 已完成。")
    contents = [item.get("content") or "" for item in page["items"]]
    assert any(
        content.startswith("【工单汇报】") and "已完成" in content
        for content in contents
    )
    assert not any(
        content.startswith("【工单汇报】") and "进行中" in content
        for content in contents
    )
    assert llm.remaining() == {"orchestrator": 0, "general": 0}
    assert [key for key, _m, _t in llm.requests].count("general") == 2
    assert [key for key, _m, _t in llm.requests].count("orchestrator") == 3


def test_full_duplex_orchestrator_answers_while_worker_runs(e2e: E2E) -> None:
    """Worker 卡在 Hold 时，第二条总控消息照常完成并关流；放开后 Worker 完成。"""
    worker_hold = Hold(
        response=tool_response(
            submit_call(),
            report_call("w-progress", "progress", "流水线已提交，等结果。", index=1),
        )
    )
    e2e.use_llm(
        {
            "orchestrator": [
                tool_response(create_ticket_call()),
                text_response("工单已经建好。"),
                text_response("设备已连接，队列是空的。"),
                text_response("工单回报：日常已经做完。"),
            ],
            "general": [
                worker_hold,
                tool_response(
                    report_call("w-ready", "ready", "日常已经做完。")
                ),
            ],
        }
    )

    post_sse(e2e, "/api/agent/orchestrator/messages", "把日常做了")
    ticket_id = wait_orchestrator_created(e2e)
    wait_until(
        lambda: worker_hold.started.is_set(), message="Worker 进入 Hold"
    )

    second = post_sse(
        e2e, "/api/agent/orchestrator/messages", "现在设备连上了吗"
    )
    assert event_names(second)[0] == "run_start"
    assert event_names(second)[-1] == "run_end"
    assert text_deltas(second) == "设备已连接，队列是空的。"
    # 总控答完时 Worker 仍卡在 Hold 里，没有被串行化。
    assert worker_hold.gate.is_set() is False
    assert ticket_detail(e2e, ticket_id)["turn_running"] is True

    worker_hold.gate.set()
    detail = wait_ticket_status(e2e, ticket_id, "done")
    assert detail["ticket"]["last_report_status"] == "ready"
    orchestrator_session_id = orchestrator_state(e2e)["session_id"]
    wait_session_runs(e2e, orchestrator_session_id, 3)
    wait_message_contains(e2e, "工单回报：日常已经做完。")


def test_second_message_during_orchestrator_run_streams_both_turns(e2e: E2E) -> None:
    """第二条消息的流先收到第一轮剩余事件，再收到消费它的一轮。"""
    first_hold = Hold(response=text_response("第一轮回答。"))
    e2e.use_llm(
        {
            "orchestrator": [
                first_hold,
                text_response("第二轮回答。"),
            ]
        }
    )

    first = SseCall(
        e2e.client, "/api/agent/orchestrator/messages", {"content": "第一条"}
    ).start()
    wait_until(
        lambda: first_hold.started.is_set(), message="第一轮模型调用开始"
    )

    second = SseCall(
        e2e.client, "/api/agent/orchestrator/messages", {"content": "第二条"}
    ).start()
    # 路由先建流再写消息；第二条在收件箱里排队，等第一轮结束才被消费。
    wait_until(
        lambda: orchestrator_state(e2e)["pending_inbox"] >= 1,
        message="第二条消息进入总控收件箱",
    )

    first_hold.gate.set()
    first_events = first.finish()
    second_events = second.finish()

    assert event_names(first_events)[-1] == "run_end"
    assert text_deltas(first_events) == "第一轮回答。"
    # 第二条流先看到第一轮剩余的事件（text_delta + run_end），再看到第二轮。
    second_names = event_names(second_events)
    first_end = second_names.index("run_end")
    second_start = second_names.index("run_start")
    assert first_end < second_start
    assert second_events[first_end][1]["run_id"] == first_events[-1][1]["run_id"]
    consumed = second_events[second_start][1]["consumed_inbox_ids"]
    assert consumed
    assert second_names[-1] == "run_end"
    assert text_deltas(second_events) == "第一轮回答。第二轮回答。"


def test_direct_ticket_chat_reaches_worker_without_orchestrator_turn(
    e2e: E2E,
) -> None:
    """工单内直接对话只唤醒 Worker；总控只收到 passive 摘要。"""
    e2e.use_llm(
        {
            "orchestrator": [
                tool_response(create_ticket_call()),
                text_response("工单已经建好。"),
                text_response("知道了，你继续。"),
            ],
            "general": [
                tool_response(
                    report_call("w-progress", "progress", "开始处理。")
                ),
                text_response("收到，我在工单里处理。"),
            ],
        }
    )

    post_sse(e2e, "/api/agent/orchestrator/messages", "把 1-7 刷一次")
    ticket_id = wait_orchestrator_created(e2e)
    detail = wait_ticket_status(e2e, ticket_id, "open")
    worker_session_id = detail["worker_session_id"]
    wait_session_runs(e2e, worker_session_id, 1)
    orchestrator_session_id = orchestrator_state(e2e)["session_id"]
    wait_session_runs(e2e, orchestrator_session_id, 1)

    ticket_events = post_sse(
        e2e, f"/api/agent/tickets/{ticket_id}/messages", "顺便看下仓库"
    )
    assert event_names(ticket_events)[-1] == "run_end"
    assert text_deltas(ticket_events) == "收到，我在工单里处理。"

    worker_messages = e2e.client.get(
        f"/api/agent/tickets/{ticket_id}/messages"
    ).json()["items"]
    assert any(
        item["role"] == "assistant"
        and item["content"] == "收到，我在工单里处理。"
        for item in worker_messages
    )

    # 没有新的总控运行；收件箱里多一条 passive 的 system 项。
    assert list_runs(e2e, orchestrator_session_id)["total"] == 1
    rows = wait_until(
        lambda: [
            row
            for row in inbox_rows(e2e, orchestrator_session_id)
            if row[0] == "system" and row[2].get("passive") is True
        ],
        message="总控收到 passive 摘要",
    )
    assert any("用户在工单「刷 1-7」里说" in row[2].get("text", "") for row in rows)

    # 下一次总控运行能看到【系统】前缀的摘要。
    post_sse(e2e, "/api/agent/orchestrator/messages", "继续")
    page = wait_message_contains(e2e, "知道了，你继续。")
    wait_session_runs(e2e, orchestrator_session_id, 2)
    contents = [item.get("content") or "" for item in page["items"]]
    assert any(
        content.startswith("【系统】用户在工单「刷 1-7」里说") for content in contents
    )
    assert list_runs(e2e, orchestrator_session_id)["total"] == 2


def test_parallel_delegation_inside_worker_turn(e2e: E2E) -> None:
    """一批两个 researcher 并行；两条 subagent_start/end；两个子会话。"""
    first_hold = Hold(response=researcher_response())
    second_hold = Hold(response=researcher_response())
    delegate_calls = [
        tool_call(
            "w-delegate-1",
            "delegate",
            {"agent": "researcher", "task": "查 1-7 的掉率"},
            index=0,
        ),
        tool_call(
            "w-delegate-2",
            "delegate",
            {"agent": "researcher", "task": "查 CE-6 的掉率"},
            index=1,
        ),
    ]
    e2e.use_llm(
        {
            "orchestrator": [
                tool_response(create_ticket_call()),
                text_response("工单已经建好。"),
                text_response("两路都查完了。"),
            ],
            # 只有整批都是只读工具时 toolexec 才并发；report 留到下一批。
            "general": [
                tool_response(*delegate_calls),
                tool_response(
                    report_call("w-ready", "ready", "两路都查到了，可以刷 1-7。")
                ),
            ],
            "researcher": [first_hold, second_hold],
        }
    )

    post_sse(e2e, "/api/agent/orchestrator/messages", "查一下 1-7 和 CE-6 的掉率")
    ticket_id = wait_orchestrator_created(e2e)
    detail = ticket_detail(e2e, ticket_id)
    worker_session_id = detail["worker_session_id"]
    # 两路都停在同一个模型调用上，才算并行。
    wait_until(
        lambda: first_hold.started.is_set() and second_hold.started.is_set(),
        message="两路 researcher 同时到达模型",
    )
    first_hold.gate.set()
    second_hold.gate.set()
    ticket = wait_ticket_status(e2e, ticket_id, "done")
    assert ticket["ticket"]["last_report_status"] == "ready"
    assert detail["worker_session_id"] == worker_session_id
    orchestrator_session_id = orchestrator_state(e2e)["session_id"]
    wait_session_runs(e2e, orchestrator_session_id, 2)
    wait_message_contains(e2e, "两路都查完了。")

    subagent_starts = [event for event in e2e.events if event.name == "subagent_start"]
    subagent_ends = [event for event in e2e.events if event.name == "subagent_end"]
    wait_until(
        lambda: len(subagent_ends) == 2,
        message="两条 subagent_end 事件",
    )
    assert len(subagent_starts) == 2
    assert all(event.data["agent"] == "researcher" for event in subagent_starts)
    # 事件由父运行的总线转发，`event.run_id` 是父运行；子运行 id 在 data 里。
    assert len({event.data["run_id"] for event in subagent_starts}) == 2
    assert all(event.data["status"] == "succeeded" for event in subagent_ends)

    wait_until(
        lambda: len(subagent_sessions(e2e, worker_session_id)) == 2,
        message="两个子代理会话",
    )
    sessions = subagent_sessions(e2e, worker_session_id)
    assert all(item["kind"] == "subagent" for item in sessions)
    assert len({item["session_id"] for item in sessions}) == 2


def test_mandate_ceiling_blocks_inferred_ticket_consumption(e2e: E2E) -> None:
    """推断委托只能做无需确认的操作；带 medicine 的 Fight 被拦且不建确认。"""
    e2e.use_llm(
        {
            "orchestrator": [text_response("范围不够，需要另开工单。")],
            "general": [
                tool_response(
                    submit_call(medicine=2, times=5),
                    report_call(
                        "w-blocked",
                        "blocked",
                        "超出委托范围，不能用药。",
                        index=1,
                        human_action={
                            "type": "decision",
                            "prompt": "要另开工单允许用药吗？",
                        },
                    ),
                )
            ],
        }
    )

    opened = open_ticket(
        e2e,
        title="刷 1-7 五次",
        brief="刷 1-7 五次。",
        scope="刷 1-7 五次",
    )
    detail = wait_ticket_status(e2e, opened.ticket_id, "blocked")
    assert detail["mandate"]["strength"] == "inferred"
    assert detail["mandate"]["risk_ceiling"] == "none"

    messages = e2e.client.get(
        f"/api/agent/tickets/{opened.ticket_id}/messages"
    ).json()["items"]
    submit_messages = [
        item
        for item in messages
        if item["role"] == "tool" and item["tool_name"] == "submit_pipeline"
    ]
    assert len(submit_messages) == 1
    payload = json.loads(submit_messages[0]["content"])
    assert payload["error"]["code"] == "MANDATE_SCOPE_EXCEEDED"
    # 被中间件拦下的调用不进确认服务，不建确认与审计行。
    assert count_rows(e2e, Confirmation, action="submit_pipeline") == 0
    assert (
        count_rows(
            e2e,
            AgentAudit,
            session_id=opened.worker_session_id,
            tool_name="submit_pipeline",
        )
        == 0
    )
    # 报 blocked 后转发总控，总控据此回复。
    page = wait_message_contains(e2e, "范围不够，需要另开工单。")
    wait_session_runs(e2e, orchestrator_state(e2e)["session_id"], 1)
    contents = [item.get("content") or "" for item in page["items"]]
    assert any(
        content.startswith("【工单汇报】") and "受阻" in content
        for content in contents
    )


def test_cancel_ticket_while_worker_runs(e2e: E2E) -> None:
    """Worker 卡在 Hold 时取消；运行与工单都 cancelled；迟到终态不唤醒任何人。"""
    worker_hold = Hold(response=text_response("这一轮不该走完。"))
    e2e.use_llm({"general": [worker_hold]})

    opened = open_ticket(e2e)
    wait_until(
        lambda: worker_hold.started.is_set(), message="Worker 进入 Hold"
    )
    cancelled: dict[str, Any] = {}

    def _cancel() -> None:
        cancelled["response"] = e2e.client.post(
            f"/api/agent/tickets/{opened.ticket_id}/cancel",
            json={"reason": "不要了"},
        )

    cancel_thread = threading.Thread(target=_cancel, daemon=True, name="m70-cancel")
    cancel_thread.start()
    # 取消事件在模型调用里不会被立即看到：泵按 5 秒宽限取消这一轮。
    cancel_thread.join(WAIT_SECONDS)
    assert not cancel_thread.is_alive(), "取消请求没有返回"
    worker_hold.gate.set()
    response = cancelled["response"]
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ticket"]["status"] == "cancelled"
    assert body["result"]["cancelled_by"] == "user"

    runs = wait_session_runs(e2e, opened.worker_session_id, 1)
    assert runs["items"][0]["status"] == "cancelled"
    assert runs["items"][0]["finish_reason"] == "cancelled"

    # 工单结束后迟到的流水线终态：不投递、不唤醒。
    pipeline_id = insert_late_pipeline(e2e, opened.worker_session_id)
    e2e.portal.call(
        partial(
            e2e.app.state.worker_service.on_pipeline_finished,
            pipeline_id,
            PipelineStatus.COMPLETED,
        )
    )
    time.sleep(0.3)
    assert list_runs(e2e, opened.worker_session_id)["total"] == 1
    assert all(
        status != "pending" for _kind, status, _payload in inbox_rows(
            e2e, opened.worker_session_id
        )
    )
    assert not any(
        kind == "thread_event"
        for kind, _status, _payload in inbox_rows(
            e2e, opened.worker_session_id
        )
    )

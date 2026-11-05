"""M82 T9：总控建目标的回归回放（M68 cassette 格式手写，pytest 级）。

M68 的场景 schema 有 ``tool_sequence`` 判分，但 runner 的 ``DRIVERS`` 只有
``legacy_session`` 一个驱动；把 ``entry.kind=orchestrator`` 写成 regression 场景
只会得到 skipped，并把 regression 的 1.0 通过率拉下来。所以这里照 M81 T10 的
``test_trigger_eval_scenarios.py`` 先例保留 pytest 级等价用例：用同一份手写
cassette（``source: handwritten``，不带请求指纹）驱动真实的总控服务、工具注册表
与 ``create_goal`` 工具，回放「月底前把银灰练到精二 → 建目标、不建工单」。

回放要经过完整生产装配，因此直接复用 ``tests/evals/environment.py`` 的假世界。
总控档案首次解析时做尾部位置探查，探查请求带 system、在 M68 的分类里落到 agent
通道，所以 cassette 的头两条是探查应答。规划工单建立时会唤醒规划 Worker；本用例
只回放总控，先关掉 Worker 的泵，避免它的模型调用抢走 cassette 的 agent 应答。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from maa_api.agent.evals.cassette import Cassette, CassetteTransport
from maa_api.agent.evals.runner import REPLAY_ENDPOINT
from maa_api.agent.evals.scenario import Scenario
from tests.evals.environment import EVAL_TOKEN, FakeWorldEnvironment

REPO_ROOT = Path(__file__).resolve().parents[3]
CASSETTES_DIR = REPO_ROOT / "tests" / "evals" / "cassettes"
FIXTURES_DIR = REPO_ROOT / "tests" / "evals" / "fixtures"

CASSETTE_NAME = "orchestrator_create_goal"
USER_TEXT = "月底前把银灰练到精二"
HEADERS = {"X-Token": EVAL_TOKEN}


def _scenario() -> Scenario:
    """只用来给假世界参数；真正的判分在下面逐条断言。"""
    return Scenario(
        id="orchestrator-create-goal",
        title="总控建目标",
        suite="regression",
        agent="orchestrator",
        source="M82 T9",
        tags=["goals"],
        entry={"kind": "orchestrator"},
        turns=[{"user": USER_TEXT}],
        expect=[
            {
                "grader": "tool_sequence",
                "steps": [{"tool": "create_goal"}],
                "forbid": ["create_ticket"],
            }
        ],
    )


def _parse_events(text: str) -> list[tuple[str, dict[str, Any]]]:
    """把 SSE 响应体解析成（事件名, data）；忽略 keep-alive 注释帧。"""
    events: list[tuple[str, dict[str, Any]]] = []
    for block in text.split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue
        fields: dict[str, str] = {}
        for line in block.splitlines():
            if ": " in line:
                key, value = line.split(": ", 1)
                fields[key] = value
        if "event" in fields and "data" in fields:
            events.append((fields["event"], json.loads(fields["data"])))
    return events


def _load_cassette() -> Cassette:
    cassette = Cassette.load(CASSETTES_DIR / f"{CASSETTE_NAME}.jsonl")
    assert cassette.header.source == "handwritten"
    assert cassette.header.agent == "orchestrator"
    assert cassette.header.scenario == CASSETTE_NAME
    return cassette


def test_orchestrator_create_goal_replays_handwritten_cassette(tmp_path: Path) -> None:
    cassette = _load_cassette()
    transport = CassetteTransport(cassette, max_calls=4)
    environment = FakeWorldEnvironment(repo_root=REPO_ROOT, fixtures_dir=FIXTURES_DIR)
    with environment.open(
        _scenario(),
        llm=REPLAY_ENDPOINT,
        workdir=tmp_path,
        transport=transport,
    ) as eval_app:
        app = eval_app.app
        worker_service = getattr(app.state, "worker_service", None)
        assert worker_service is not None, "生产装配没有 Worker 服务"
        # 规划工单的 Worker 会在建目标时被唤醒；本用例只回放总控这一条链。
        eval_app.portal.call(worker_service.close)

        response = eval_app.client.post(
            "/api/agent/orchestrator/messages",
            json={"content": USER_TEXT},
            headers=HEADERS,
        )
        assert response.status_code == 200, response.text[:400]
        events = _parse_events(response.text)
        tool_starts = [
            data.get("name") for name, data in events if name == "tool_start"
        ]
        assert tool_starts == ["create_goal"]
        assert "create_ticket" not in tool_starts

        goals_response = eval_app.client.get("/api/agent/goals", headers=HEADERS)
        assert goals_response.status_code == 200, goals_response.text[:400]
        goals = goals_response.json()
        assert goals["total"] == 1
        goal = goals["items"][0]
        assert goal["title"] == USER_TEXT

        detail_response = eval_app.client.get(
            f"/api/agent/goals/{goal['id']}", headers=HEADERS
        )
        assert detail_response.status_code == 200, detail_response.text[:400]
        detail = detail_response.json()
        planning_ticket_id = detail["planning_ticket_id"]
        assert planning_ticket_id
        assert [item["id"] for item in detail["tickets"]] == [planning_ticket_id]
        assert detail["tickets"][0]["kind"] == "planner"

        # 只有规划工单：总控没有调 create_ticket，报告里也没有第二张工单。
        tickets_response = eval_app.client.get(
            "/api/agent/tickets", params={"size": 200}, headers=HEADERS
        )
        assert tickets_response.status_code == 200, tickets_response.text[:400]
        tickets = tickets_response.json()
        assert tickets["total"] == 1
        assert [item["id"] for item in tickets["items"]] == [planning_ticket_id]
        assert tickets["items"][0]["kind"] == "planner"

        ticket_response = eval_app.client.get(
            f"/api/agent/tickets/{planning_ticket_id}", headers=HEADERS
        )
        assert ticket_response.status_code == 200, ticket_response.text[:400]
        ticket_detail = ticket_response.json()
        assert ticket_detail["ticket"]["origin"] == "goal"
        assert ticket_detail["mandate"]["strength"] == "explicit"

        assert cassette.remaining("agent") == 0

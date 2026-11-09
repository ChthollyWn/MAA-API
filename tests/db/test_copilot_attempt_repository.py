"""M87 T2：``CopilotAttemptRepository.list_for_session``（规格 §4.2、§6）。

尝试记录存在 ``kind=copilot`` 资源的 ``meta["attempts"]`` 里（M29 §9.5）；
闸门按 Worker 会话取本工单的尝试，所以仓储要跨多份作业资源按 ``session_id``
过滤并按 ``started_at`` 升序。非 ``copilot`` 的资源不参与。
"""

from __future__ import annotations

import asyncio

from maa_api.db.models import ResourceAsset
from maa_api.db.repositories.copilot import CopilotAttemptRepository
from tests.fakes.v3_db import make_session_factory


def _attempt(attempt_id: str, session_id: str, started_at: str) -> dict[str, object]:
    return {
        "id": attempt_id,
        "session_id": session_id,
        "pipeline_id": f"pipeline-{attempt_id}",
        "outcome": "clear",
        "judged_by": "core",
        "counted": True,
        "success": True,
        "attempt_number": 1,
        "stage_code": "1-7",
        "started_at": started_at,
        "finished_at": started_at,
    }


def test_list_for_session_filters_and_orders(tmp_path):
    async def scenario():
        _engine, factory = await make_session_factory(tmp_path / "attempts.db")
        async with factory() as db:
            db.add(
                ResourceAsset(
                    id="asset-a",
                    kind="copilot",
                    name="作业甲",
                    content={"actions": []},
                    meta={
                        "attempts": [
                            _attempt("a-2", "session-b", "2026-11-20T08:05:00+00:00"),
                            _attempt("a-1", "session-a", "2026-11-20T08:03:00+00:00"),
                            _attempt("a-3", "session-a", "2026-11-20T08:01:00+00:00"),
                        ],
                        "stage_code": "1-7",
                    },
                )
            )
            db.add(
                ResourceAsset(
                    id="asset-b",
                    kind="copilot",
                    name="作业乙",
                    content={"actions": []},
                    meta={
                        "attempts": [
                            _attempt("b-1", "session-a", "2026-11-20T08:02:00+00:00"),
                            _attempt("b-2", "session-b", "2026-11-20T08:04:00+00:00"),
                        ],
                    },
                )
            )
            await db.commit()
            rows = await CopilotAttemptRepository(db).list_for_session("session-a")

        assert [row["id"] for row in rows] == ["a-3", "b-1", "a-1"]
        assert all(row["session_id"] == "session-a" for row in rows)
        # 每项保持资源里存的键（不裁剪、不改名）。
        assert rows[0] == _attempt("a-3", "session-a", "2026-11-20T08:01:00+00:00")

    asyncio.run(scenario())


def test_list_for_session_ignores_non_copilot_assets(tmp_path):
    async def scenario():
        _engine, factory = await make_session_factory(tmp_path / "attempts.db")
        async with factory() as db:
            db.add(
                ResourceAsset(
                    id="asset-artifact",
                    kind="artifact",
                    name="报告",
                    content={"actions": []},
                    meta={
                        "attempts": [
                            _attempt("x-1", "session-a", "2026-11-20T08:00:00+00:00")
                        ]
                    },
                )
            )
            db.add(
                ResourceAsset(
                    id="asset-copilot",
                    kind="copilot",
                    name="作业甲",
                    content={"actions": []},
                    meta={
                        "attempts": [
                            _attempt("c-1", "session-a", "2026-11-20T08:01:00+00:00")
                        ]
                    },
                )
            )
            await db.commit()
            rows = await CopilotAttemptRepository(db).list_for_session("session-a")

        assert [row["id"] for row in rows] == ["c-1"]

    asyncio.run(scenario())

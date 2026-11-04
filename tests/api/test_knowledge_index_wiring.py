"""M76 T9：检索层的 lifespan 装配（规格 §5 T9、§6 的 wiring 用例）。

从生产 ``create_app()`` 的真实 lifespan 断言：索引服务与检索器挂到了 ``app.state``，
``GET /api/agent/retrieval/sources`` 列出 builtin / user 两个来源，且启动后的后台
首次同步确实把内置条目写进了索引（``builtin.doc_count > 0``）。
"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from maa_api.agent.knowledge.index.indexer import KnowledgeIndexService
from maa_api.agent.knowledge.index.retriever import Retriever
from maa_api.main import create_app

#: 后台首次同步的等待上限；正常几百毫秒内完成，留足余量只防慢机器。
_INDEX_TIMEOUT_SECONDS = 20.0


def test_lifespan_indexes_builtin_entries(tmp_settings, isolated_db) -> None:
    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)
    with client:
        index_service = getattr(app.state, "knowledge_index_service", None)
        assert isinstance(index_service, KnowledgeIndexService), (
            "lifespan 没有设置 app.state.knowledge_index_service"
        )
        assert isinstance(getattr(app.state, "retriever", None), Retriever), (
            "lifespan 没有设置 app.state.retriever"
        )

        # 等启动时的后台首次同步跑完再发请求：同步写库期间并发轮询会抢 SQLite
        # 写锁，这个用例要验证的是装配本身，不是锁竞争。
        deadline = time.monotonic() + _INDEX_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            pending = [task for task in index_service._tasks if not task.done()]
            if not pending:
                break
            time.sleep(0.05)

        response = client.get("/api/agent/retrieval/sources")
        assert response.status_code == 200
        payload = response.json()
        items = {item["id"]: item for item in payload["items"]}

    assert {"builtin", "user"} <= set(items), f"来源列表缺少内置来源：{sorted(items)}"
    assert items["builtin"]["doc_count"] > 0, "后台首次同步没有把内置条目写进索引"
    # 向量默认关闭：列表要如实报 disabled，而不是报错。
    assert payload["embedding"]["provider"] == "none"
    assert payload["embedding"]["status"] == "disabled"

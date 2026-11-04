"""M70 流水线终态钩子扇出（规格 §4.2.1、§6）。

``PipelineRunner`` 的终态槽位只有一个，养成计划与话题路由共用它：装配处绑扇出器，
订阅者挂在扇出器上。四条用例分别钉住转发、隔离、登记校验与真实 binder 的接线。
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from maa_api.agent.hooks_fanout import FinishHookFanout
from maa_api.domain.enums import PipelineStatus
from maa_api.services.pipeline_runner import PipelineRunner


class _Client:
    """``PipelineRunner`` 构造时只对 client 调用 ``on`` 注册回调。"""

    def on(self, event: str, handler: object) -> None:
        del event, handler


class _Registry:
    def get(self, core_id: str) -> _Client:
        del core_id
        return _Client()


def _runner() -> PipelineRunner:
    """真实 ``PipelineRunner``（只用到终态钩子槽位，其余依赖不参与）。"""
    return PipelineRunner(_Registry(), object(), None)  # type: ignore[arg-type]


def test_fanout_calls_every_hook_with_pipeline_and_status() -> None:
    calls: list[tuple[str, str, object]] = []

    def sync_hook(pipeline_id: str, status: object) -> None:
        calls.append(("sync", pipeline_id, status))

    async def async_hook(pipeline_id: str, status: object) -> None:
        calls.append(("async", pipeline_id, status))

    fanout = FinishHookFanout()
    fanout.add("sync", sync_hook)
    fanout.add("async", async_hook)
    assert fanout.names == ("sync", "async")

    status = PipelineStatus.COMPLETED
    asyncio.run(fanout("pipeline-1", status))

    # 两个订阅者都收到完整参数；同步函数与协程都能挂。
    assert calls == [
        ("sync", "pipeline-1", status),
        ("async", "pipeline-1", status),
    ]


def test_one_failing_hook_does_not_block_the_others(caplog: pytest.LogCaptureFixture) -> None:
    done: list[str] = []

    def boom(pipeline_id: str, status: object) -> None:
        raise RuntimeError("炸了")

    async def good(pipeline_id: str, status: object) -> None:
        await asyncio.sleep(0)
        done.append(pipeline_id)

    fanout = FinishHookFanout()
    fanout.add("boom", boom)
    fanout.add("good", good)

    with caplog.at_level(logging.ERROR, logger="maa_api.agent.hooks_fanout"):
        asyncio.run(fanout("pipeline-2", PipelineStatus.FAILED))

    assert done == ["pipeline-2"]
    # 异常进日志，带订阅者名与流水线 id，便于定位是谁炸了。
    assert "boom" in caplog.text
    assert "pipeline-2" in caplog.text


def test_duplicate_or_empty_name_is_rejected() -> None:
    fanout = FinishHookFanout()
    fanout.add("one", lambda pipeline_id, status: None)

    with pytest.raises(ValueError):
        fanout.add("one", lambda pipeline_id, status: None)
    with pytest.raises(ValueError):
        fanout.add("", lambda pipeline_id, status: None)


def test_upgrade_plan_hook_still_receives_terminal_status() -> None:
    finished: list[tuple[str, object]] = []

    class Plans:
        async def on_pipeline_finished(self, pipeline_id: str, status: object = None) -> None:
            finished.append((pipeline_id, status))

    fanout = FinishHookFanout()
    fanout.add("upgrade_plans", Plans().on_pipeline_finished)

    runner = _runner()
    runner.bind_finish_hook(fanout)

    async def trigger() -> None:
        runner._schedule_finish_hook("pipeline-9", PipelineStatus.FAILED)
        tasks = tuple(runner._notify_tasks)
        assert len(tasks) == 1
        await asyncio.gather(*tasks)

    asyncio.run(trigger())

    assert finished == [("pipeline-9", PipelineStatus.FAILED)]

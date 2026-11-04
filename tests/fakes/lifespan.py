"""可注入的 lifespan 适配器（M70 T14 从 ``scripts/agent_smoke.py`` 搬出）。

四个类原来以 ``_Smoke*`` 的名字私有地活在冒烟脚本里。总控与 Worker 的端到端用例
需要同一套「不加载 MaaCore、不连真机」的边界替身，所以搬到 ``tests/fakes`` 供两边
共用；名字去掉下划线前缀，行为逐字不变：

- :class:`SmokeCoreSupervisor`：报告 READY、不加载动态库的 CoreSupervisor；
- :class:`SmokeCoreClient`：直接驱动 :class:`~tests.fakes.fake_asst.FakeAsst` 的客户端；
- :class:`SmokeDeviceManager`：永远可用、记录点击的 DeviceManager；
- :class:`SmokePipelineRunner`：生产队列/执行路径，只把广播换成空实现。

:func:`install_fake_lifespan` 在进入应用 lifespan **之前**调用，把四个工厂写到
``app.state``；``maa_api.main.lifespan`` 用 ``getattr(app.state, ...)`` 取它们。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from maa_api.services.pipeline_runner import PipelineRunner
from tests.fakes.fake_asst import SUCCESS_SCRIPT, FakeAsst

__all__ = [
    "SmokeCoreClient",
    "SmokeCoreSupervisor",
    "SmokeDeviceManager",
    "SmokePipelineRunner",
    "install_fake_lifespan",
]

#: 四个工厂在 ``app.state`` 上的属性名（与 ``maa_api/main.py`` 的读取处一致）。
FAKE_LIFESPAN_STATE_NAMES = (
    "core_supervisor_factory",
    "core_client_factory",
    "device_manager_factory",
    "pipeline_runner_factory",
)


class SmokeCoreSupervisor:
    """Lifespan adapter that reports READY without loading MaaCore."""

    def __init__(self, _config: Any, *, on_crash: Any, on_state_change: Any) -> None:
        del on_crash
        from maa_api.core.supervisor import CoreState

        self.state = CoreState.READY
        self.generation = 1
        self.pid = 11011
        self._on_state_change = on_state_change

    async def start(self, **_kwargs: Any) -> None:
        self._on_state_change(self.state)

    async def stop(self, **_kwargs: Any) -> None:
        return None

    class _Maintenance:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *_exc: Any) -> None:
            return None

    def acquire_maintenance(self) -> _Maintenance:
        return self._Maintenance()

    async def restart(self) -> None:
        return None


class SmokeCoreClient:
    def __init__(self, _supervisor: Any, **_kwargs: Any) -> None:
        self.handlers: dict[str, list[Any]] = {}
        self.loop = asyncio.get_running_loop()
        self.asst = FakeAsst(script=SUCCESS_SCRIPT, delay_scale=0)
        self.copilot_call: dict[str, Any] | None = None
        self.copilot_calls: list[dict[str, Any]] = []
        self.asst.set_callback(self._on_asst_callback)

    def on(self, event: str, handler: Any) -> None:
        self.handlers.setdefault(event, []).append(handler)

    def _on_asst_callback(self, message: int, details: bytes, _arg: Any) -> None:
        payload = {"msg": message, "details": json.loads(details)}
        for handler in self.handlers.get("CALLBACK", []):
            self.loop.call_soon_threadsafe(handler, payload)

    def start_consumer(self) -> None:
        return None

    async def _send(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"ret": 0}

    async def append_task(self, type_name: str, params: dict[str, Any]) -> int:
        if type_name == "Copilot":
            filename = Path(params["filename"])
            self.copilot_call = {
                "type_name": type_name,
                "params": dict(params),
                "path": filename,
                "content": json.loads(filename.read_text(encoding="utf-8")),
                "existed_at_append": filename.is_file(),
            }
            self.copilot_calls.append(self.copilot_call)
        task_id = self.asst.append_task(type_name, params)
        if task_id <= 0:
            raise RuntimeError("FakeAsst rejected task")
        return task_id

    async def start(self) -> bool:
        return self.asst.start()

    async def stop(self) -> bool:
        return self.asst.stop()

    def close(self) -> None:
        self.asst.stop()


class SmokeDeviceManager:
    def __init__(self, _settings: Any, _client: Any, *, broadcast: Any, core_id: str) -> None:
        self.broadcast = broadcast
        self.core_id = core_id
        self.address = "smoke:5555"
        self.common_ports: list[int] = []
        self.reconnect_retry_attempts = 1
        self.clicks: list[tuple[int, int]] = []
        self.closed = False

    async def connect_with_retry(self, **_kwargs: Any) -> bool:
        return False

    async def ensure_available(self, *, timeout: float = 10.0) -> bool:
        del timeout
        return True

    def on_core_connection_event(self, _what: str, _details: dict[str, Any]) -> None:
        return None

    def snapshot(self) -> dict[str, Any]:
        return {"core_id": self.core_id, "state": "disconnected", "address": self.address}

    async def click(self, x: int, y: int) -> None:
        self.clicks.append((x, y))

    async def close(self) -> None:
        self.closed = True


class SmokePipelineRunner(PipelineRunner):
    """Production queue/runner path backed by FakeAsst instead of MaaCore."""

    async def publish_pipeline(self, _pipeline_id: str) -> None:
        return None

    async def publish_queue_changed(self) -> None:
        return None


def install_fake_lifespan(app: Any) -> None:
    """把四个生命周期工厂装到 ``app.state``（进入 lifespan 之前调用）。"""
    for name, factory in zip(
        FAKE_LIFESPAN_STATE_NAMES,
        (
            SmokeCoreSupervisor,
            SmokeCoreClient,
            SmokeDeviceManager,
            SmokePipelineRunner,
        ),
    ):
        setattr(app.state, name, factory)

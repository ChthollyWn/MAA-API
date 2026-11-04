"""PipelineRunner 终态钩子的扇出。

PipelineRunner.bind_finish_hook 只有一个槽位，养成计划与话题路由都要在流水线进入终态时
被叫到：装配处把扇出器绑进槽位，订阅者挂在扇出器上。订阅者之间互不影响，一个抛异常只记
日志，其余照常执行。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

__all__ = ["FinishHook", "FinishHookFanout"]

logger = logging.getLogger(__name__)

FinishHook = Callable[[str, Any], Awaitable[None] | None]


class FinishHookFanout:
    """把一次流水线终态通知并发转给全部订阅者。"""

    def __init__(self) -> None:
        self._hooks: dict[str, FinishHook] = {}

    def add(self, name: str, hook: FinishHook) -> None:
        if not name or name in self._hooks:
            raise ValueError(f"终态钩子名为空或重复：{name!r}")
        if not callable(hook):
            raise TypeError("hook 必须可调用")
        self._hooks[name] = hook

    def remove(self, name: str) -> None:
        self._hooks.pop(name, None)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._hooks)

    async def __call__(self, pipeline_id: str, status: Any = None) -> None:
        hooks = list(self._hooks.items())
        if hooks:
            await asyncio.gather(
                *(self._run_one(name, hook, pipeline_id, status) for name, hook in hooks)
            )

    @staticmethod
    async def _run_one(name: str, hook: FinishHook, pipeline_id: str, status: Any) -> None:
        try:
            result = hook(pipeline_id, status)
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001 - 一个订阅者失败不能拖累其余订阅者
            logger.exception("流水线终态钩子 %s 失败（pipeline_id=%s）", name, pipeline_id)

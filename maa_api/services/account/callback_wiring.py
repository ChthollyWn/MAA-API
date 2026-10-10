"""账号识别回调的接线（M33，规格 §4.1）。

M27 建好的 :class:`~maa_api.services.account.capture.AccountCaptureService` 在生产代码里
没有任何调用方，干员 / 仓库的识别结果从来没有落库（``docs/13`` §8.48）。这里把它挂到内核
事件的 ``CALLBACK`` 上。

内核在**事件循环线程**里同步派发回调（``CoreClient._handle_event`` 经
``loop.call_soon_threadsafe`` 投回），所以本模块只做「调度」：真正的解析与落库都在
``handle_callback`` 里，它自己吞掉所有异常并记 warning——采集失败绝不能影响流水线回调。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from typing import Any

from maa_api.core.enums import Message

logger = logging.getLogger(__name__)


def install_account_capture(
    client: Any,
    capture: Any,
    *,
    context_provider: Callable[[Any, dict[str, Any]], tuple[str, str] | None] | None = None,
    client_type_provider: Callable[[], str | None] | None = None,
) -> None:
    """把内核的 ``CALLBACK`` 接到账号采集上。

    :param client: ``CoreClient``（或提供 ``on(event_type, handler)`` 的替身）。
    :param capture: ``AccountCaptureService``（或同签名的替身）。
    :param context_provider: ``(msg, details) → (pipeline_id, task_id)``；流水线里用
        ``PipelineRunner.log_context``，与日志同一套归属，日志页与账号页对得上。
    :param client_type_provider: 当前渠道（``channel.client_type``），写进识别记录；
        读不到就记 ``None``。
    """
    on = getattr(client, "on", None)
    if not callable(on):
        raise TypeError("client 必须提供 on(event_type, handler)")

    #: 调度中的任务：``asyncio`` 只持弱引用，不留下引用可能被 GC 掉。
    tasks: set[asyncio.Task[Any]] = set()

    def _finish(task: asyncio.Task[Any]) -> None:
        tasks.discard(task)
        # 取一次异常：既记日志，也避免 asyncio 打「Task exception was never retrieved」。
        if not task.cancelled() and task.exception() is not None:
            logger.warning(
                "账号识别采集任务异常（已忽略，不影响流水线）",
                exc_info=task.exception(),
            )

    def on_callback(payload: Any) -> None:
        if not isinstance(payload, Mapping):
            return
        details = payload.get("details")
        if not isinstance(details, Mapping):
            return
        try:
            message = Message(int(payload.get("msg")))
        except (TypeError, ValueError):
            return
        # 只做消息类型粗筛（``SubTaskExtraInfo`` 正是采集服务自己的第一道判断），
        # 任务链 / 掉落 / 连接事件不必进采集；``what`` 仍交给采集服务分发。
        if message is not Message.SubTaskExtraInfo:
            return

        pipeline_id = task_id = None
        if context_provider is not None:
            try:
                context = context_provider(message, details)
            except Exception:  # noqa: BLE001 - 上下文查不到不能挡住落库
                logger.exception("查找账号识别回调的流水线上下文失败")
                context = None
            if context is not None:
                pipeline_id, task_id = context

        client_type = None
        if client_type_provider is not None:
            try:
                client_type = client_type_provider()
            except Exception:  # noqa: BLE001 - 读设置失败只是少一个渠道字段
                logger.exception("读取当前渠道（client_type）失败")

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # 没有运行中的事件循环：丢这条回调也不能抛出去
            logger.warning("账号识别回调没有可调度的事件循环，本次不落库", exc_info=True)
            return
        task = loop.create_task(
            capture.handle_callback(
                details,
                message=message,
                pipeline_id=pipeline_id,
                task_id=task_id,
                client_type=client_type,
            )
        )
        tasks.add(task)
        task.add_done_callback(_finish)

    on("CALLBACK", on_callback)

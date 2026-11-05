"""进程内记录器：内核 CALLBACK 与流水线生命周期的最后一个标记（M83 spec §4.2）。

记录器是**进程内字典，重启后清空**。它只记时间戳，不读数据库、不阻塞回调线程：

- :meth:`WatchdogRecorder.note_core_callback`：挂在 ``CoreClient.on("CALLBACK", ...)``
  的出口（G2 接在 ``core/client.py`` 已有分发之后，不另挂一套）；
- :meth:`WatchdogRecorder.note_pipeline`（以及 :meth:`note_pipeline_started` /
  :meth:`note_pipeline_progress` / :meth:`note_pipeline_finished`）：挂在
  ``PipelineRunner`` 的任务开始、进度回调出口与任务结束三处。spec §4.2 只写了
  ``note_pipeline(pipeline_id)``；这里保留同一入口，用 ``phase`` 区分三处，
  避免 G2 为三个出口起三套命名。

快照 :meth:`WatchdogRecorder.snapshot` 返回正在执行的流水线（:class:`PipelineSample`）
与它们的最后一个回调时间。最后一个回调取「内核 CALLBACK」与「流水线进度」中较晚者；
若它早于任务开始时间，说明本次任务开始后还没采样，样本的
:attr:`PipelineSample.no_sample_yet` 为真，``pipeline_no_callback`` 不告警
（spec §4.2，别把「还没采样」当成冻结）。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable

from maa_api.agent.watchdog.checks import PipelineSample

#: :meth:`WatchdogRecorder.note_pipeline` 的 phase 取值。
PIPELINE_STARTED = "started"
PIPELINE_PROGRESS = "progress"
PIPELINE_FINISHED = "finished"
_PIPELINE_PHASES = frozenset({PIPELINE_STARTED, PIPELINE_PROGRESS, PIPELINE_FINISHED})


def _utcnow() -> datetime:
    """库与检查统一用的 naive UTC（与 ``db.models.utcnow`` 同义，避免反向依赖）。"""
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass
class _PipelineMark:
    """一条流水线的进程内标记。"""

    started_at: datetime
    device_id: str | None
    last_progress_at: datetime | None = None


@dataclass(frozen=True)
class RecorderSnapshot:
    """某一刻的记录器快照；``pipelines`` 只含正在执行的那些。"""

    last_core_callback_at: datetime | None
    pipelines: tuple[PipelineSample, ...] = ()

    def pipeline(self, pipeline_id: str) -> PipelineSample | None:
        """按 id 取一条流水线样本；不在执行中或没记录过时返回 ``None``。"""
        for sample in self.pipelines:
            if sample.pipeline_id == pipeline_id:
                return sample
        return None


class WatchdogRecorder:
    """线程安全的进程内标记表；核心回调与流水线回调都从各自线程调用。"""

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or _utcnow
        self._lock = threading.Lock()
        self._last_core_callback_at: datetime | None = None
        self._pipelines: dict[str, _PipelineMark] = {}

    # -- 标记 ---------------------------------------------------------------
    def note_core_callback(self, *, at: datetime | None = None) -> None:
        """记录一次内核 CALLBACK；挂在 ``CoreClient.on("CALLBACK", ...)`` 出口。"""
        stamp = at or self._clock()
        with self._lock:
            self._last_core_callback_at = stamp

    def note_pipeline(
        self,
        pipeline_id: str,
        phase: str = PIPELINE_PROGRESS,
        *,
        device_id: str | None = None,
        at: datetime | None = None,
    ) -> None:
        """流水线生命周期入口：``started`` / ``progress`` / ``finished``。

        spec §4.2 的 ``recorder.note_pipeline(pipeline_id)`` 就是本方法；
        ``phase`` 省略时按 ``progress`` 处理。
        """
        if phase not in _PIPELINE_PHASES:
            raise ValueError(f"未知的流水线阶段：{phase!r}")
        stamp = at or self._clock()
        with self._lock:
            if phase == PIPELINE_FINISHED:
                self._pipelines.pop(pipeline_id, None)
                return
            mark = self._pipelines.get(pipeline_id)
            if mark is None:
                # 进度先于开始标记到达（回调线程与任务线程交错）：进度本身就是采样。
                self._pipelines[pipeline_id] = _PipelineMark(
                    started_at=stamp,
                    device_id=device_id,
                    last_progress_at=stamp if phase == PIPELINE_PROGRESS else None,
                )
                return
            if phase == PIPELINE_STARTED:
                # 新任务（或同一 id 的新一轮）开始：清掉上一轮的采样。
                self._pipelines[pipeline_id] = _PipelineMark(
                    started_at=stamp,
                    device_id=device_id,
                )
                return
            if device_id is not None:
                mark.device_id = device_id
            mark.last_progress_at = stamp

    def note_pipeline_started(
        self,
        pipeline_id: str,
        *,
        device_id: str | None = None,
        at: datetime | None = None,
    ) -> None:
        self.note_pipeline(pipeline_id, PIPELINE_STARTED, device_id=device_id, at=at)

    def note_pipeline_progress(
        self,
        pipeline_id: str,
        *,
        device_id: str | None = None,
        at: datetime | None = None,
    ) -> None:
        self.note_pipeline(pipeline_id, PIPELINE_PROGRESS, device_id=device_id, at=at)

    def note_pipeline_finished(
        self, pipeline_id: str, *, at: datetime | None = None
    ) -> None:
        self.note_pipeline(pipeline_id, PIPELINE_FINISHED, at=at)

    # -- 查询 ---------------------------------------------------------------
    @property
    def last_core_callback_at(self) -> datetime | None:
        with self._lock:
            return self._last_core_callback_at

    def pipeline_sample(self, pipeline_id: str) -> PipelineSample | None:
        """一条正在执行的流水线的快照；没记录过或已结束时返回 ``None``。"""
        with self._lock:
            mark = self._pipelines.get(pipeline_id)
            if mark is None:
                return None
            return self._sample(pipeline_id, mark, self._last_core_callback_at)

    def snapshot(self) -> RecorderSnapshot:
        """只含正在执行的流水线；内核回调时间为全局最后一个。"""
        with self._lock:
            callback = self._last_core_callback_at
            samples = tuple(
                self._sample(pipeline_id, mark, callback)
                for pipeline_id, mark in self._pipelines.items()
            )
        return RecorderSnapshot(
            last_core_callback_at=callback,
            pipelines=samples,
        )

    def reset(self) -> None:
        """清空全部标记（测试与重启后的显式复位用）。"""
        with self._lock:
            self._last_core_callback_at = None
            self._pipelines.clear()

    @staticmethod
    def _sample(
        pipeline_id: str,
        mark: _PipelineMark,
        last_core_callback_at: datetime | None,
    ) -> PipelineSample:
        last_callback = last_core_callback_at
        if mark.last_progress_at is not None and (
            last_callback is None or mark.last_progress_at > last_callback
        ):
            last_callback = mark.last_progress_at
        if last_callback is not None and last_callback < mark.started_at:
            # 回调发生在任务开始之前：本次任务开始后还没采样。
            last_callback = None
        return PipelineSample(
            pipeline_id=pipeline_id,
            started_at=mark.started_at,
            device_id=mark.device_id,
            last_callback_at=last_callback,
        )

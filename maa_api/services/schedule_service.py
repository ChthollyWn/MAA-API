"""Schedule CRUD, cron registration, and queue submission."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.jobstores.base import JobLookupError
from apscheduler.triggers.cron import CronTrigger
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.db.models import Pipeline, Schedule, utcnow
from maa_api.db.repositories.pipeline import PipelineRepository
from maa_api.db.repositories.schedule import ScheduleRepository
from maa_api.domain.enums import PipelineSource, PipelineStatus, Priority
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.domain.task import PipelineCreate, TaskInput

logger = logging.getLogger(__name__)

SCHEDULE_JOB_PREFIX = "maa-schedule-"
RECENT_RUNS_LIMIT = 5
_WEEKDAYS = {
    "SUN": 0,
    "MON": 1,
    "TUE": 2,
    "WED": 3,
    "THU": 4,
    "FRI": 5,
    "SAT": 6,
}


class ScheduleWrite(BaseModel):
    """Validated create/full-replacement payload for a schedule."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    cron: str = Field(min_length=1, max_length=64)
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=48)
    template: list[TaskInput] = Field(min_length=1, max_length=32)
    enabled: bool = True
    priority: int = Field(default=int(Priority.SCHEDULED), ge=0, le=2)
    misfire_grace_seconds: int = Field(default=300, ge=1, le=86400)
    catch_up: bool = False
    skip_if_running: bool = True

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("名称不能为空")
        return name


class SchedulePatch(BaseModel):
    """Partial schedule updates supported by PATCH."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    cron: str | None = Field(default=None, min_length=1, max_length=64)
    priority: int | None = Field(default=None, ge=0, le=2)


class ScheduleRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    priority: int | None = Field(default=None, ge=0, le=2)


class ScheduleRunView(BaseModel):
    pipeline_id: str | None
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    error: dict[str, Any] | None


class ScheduleView(BaseModel):
    id: str
    name: str
    cron: str
    timezone: str
    template: list[dict[str, Any]]
    enabled: bool
    priority: int
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_run_result: ScheduleRunView | None
    recent_runs: list[ScheduleRunView]


class SchedulePage(BaseModel):
    items: list[ScheduleView]
    total: int


def _posix_raw_day_number(value: str) -> int:
    upper = value.upper()
    if upper in _WEEKDAYS:
        return _WEEKDAYS[upper]
    if not re.fullmatch(r"\d+", value):
        raise ValueError("invalid weekday")
    number = int(value)
    if not 0 <= number <= 7:
        raise ValueError("weekday must be in 0..7")
    return number


def _apscheduler_weekday_field(value: str) -> str:
    """Map POSIX Sunday=0 weekdays to APScheduler Monday=0 weekdays."""
    if value == "*":
        return "*"
    selected: set[int] = set()
    for segment in value.split(","):
        if not segment:
            raise ValueError("empty weekday component")
        if segment.count("/") > 1:
            raise ValueError("invalid weekday step")
        base, slash, step_text = segment.partition("/")
        step = 1
        if slash:
            if not step_text.isdigit() or int(step_text) < 1:
                raise ValueError("invalid weekday step")
            step = int(step_text)

        if base == "*":
            start, end = 0, 7
        elif "-" in base:
            if base.count("-") != 1:
                raise ValueError("invalid weekday range")
            raw_start, raw_end = base.split("-", 1)
            start = _posix_raw_day_number(raw_start)
            end = _posix_raw_day_number(raw_end)
            if start > end:
                raise ValueError("weekday ranges must be ascending")
        else:
            start = _posix_raw_day_number(base)
            end = 7 if slash else start

        for day in range(start, end + 1, step):
            if not 0 <= day <= 7:
                raise ValueError("weekday must be in 0..7")
            selected.add(0 if day == 7 else day)

    if not selected:
        raise ValueError("weekday field is empty")
    if len(selected) == 7:
        return "*"
    translated = sorted((day - 1) % 7 for day in selected)
    return ",".join(str(day) for day in translated)


def cron_trigger(cron: str, timezone_name: str) -> CronTrigger:
    """Validate five-field POSIX cron and IANA timezone for APScheduler 3."""
    fields = cron.split()
    if len(fields) != 5:
        raise ValueError("cron must contain five fields")
    try:
        zone = ZoneInfo(timezone_name)
        aps_fields = fields[:4] + [_apscheduler_weekday_field(fields[4])]
        return CronTrigger.from_crontab(" ".join(aps_fields), timezone=zone)
    except (ValueError, TypeError, ZoneInfoNotFoundError) as exc:
        raise ValueError("invalid cron expression or IANA timezone") from exc


def _next_run_as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _api_datetime(value: datetime | None) -> datetime | None:
    """Render database UTC-naive timestamps as timezone-aware UTC API values."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _dump_template(template: list[TaskInput]) -> list[dict[str, Any]]:
    return [
        item.model_dump(mode="json", by_alias=True, exclude_unset=True)
        for item in template
    ]


def _validate_template(template: list[dict[str, Any]], priority: int) -> PipelineCreate:
    try:
        return PipelineCreate.model_validate(
            {"tasks": template, "priority": priority}
        )
    except ValidationError as exc:
        issues = [
            {
                "loc": list(issue.get("loc", ())),
                "msg": issue.get("msg", "参数无效"),
                "type": issue.get("type", "validation_error"),
            }
            for issue in exc.errors(include_context=False)
        ]
        raise AppError(
            ErrorCode.TASK_PARAM_INVALID,
            "定时任务模板参数校验失败",
            {"issues": issues},
        ) from exc


def _name_conflict(exc: IntegrityError) -> bool:
    message = str(exc.orig).lower()
    return "schedule.name" in message or "uq_schedule_name" in message


class ScheduleService:
    """The single entry point for schedule persistence and APScheduler jobs."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        scheduler: Any,
        queue_service: Any,
        pipeline_runner: Any,
    ) -> None:
        self.session_factory = session_factory
        self.scheduler = scheduler
        self.queue_service = queue_service
        self.pipeline_runner = pipeline_runner
        self._owned_job_ids: set[str] = set()

    @staticmethod
    def _job_id(schedule_id: str) -> str:
        return SCHEDULE_JOB_PREFIX + schedule_id

    @staticmethod
    def _cron_error(cron: str, timezone_name: str) -> AppError:
        return AppError(
            ErrorCode.SCHEDULE_CRON_INVALID,
            "cron 必须是有效的五段标准表达式，时区须为 IANA 名称",
            {"cron": cron, "timezone": timezone_name},
        )

    def _trigger(self, cron: str, timezone_name: str) -> CronTrigger:
        try:
            return cron_trigger(cron, timezone_name)
        except ValueError:
            raise self._cron_error(cron, timezone_name) from None

    def _checked_payload(self, payload: ScheduleWrite) -> tuple[ScheduleWrite, list[dict[str, Any]]]:
        self._trigger(payload.cron, payload.timezone)
        template = _dump_template(payload.template)
        _validate_template(template, payload.priority)
        return payload, template

    async def list(
        self, *, enabled: bool | None = None
    ) -> SchedulePage:
        async with self.session_factory() as session:
            schedules = await ScheduleRepository(session).list(enabled=enabled)
            pipeline_repo = PipelineRepository(session)
            views = []
            for schedule in schedules:
                history = await pipeline_repo.list(
                    schedule_id=schedule.id, page=1, size=RECENT_RUNS_LIMIT
                )
                views.append(self._view(schedule, history.items))
            return SchedulePage(
                items=views,
                total=len(schedules),
            )

    async def get(self, schedule_id: str) -> ScheduleView:
        async with self.session_factory() as session:
            repo = ScheduleRepository(session)
            schedule = await repo.get(schedule_id)
            if schedule is None:
                raise AppError(ErrorCode.SCHEDULE_NOT_FOUND, "定时任务不存在")
            history = await PipelineRepository(session).list(
                schedule_id=schedule.id, page=1, size=RECENT_RUNS_LIMIT
            )
            return self._view(schedule, history.items)

    async def create(self, payload: ScheduleWrite) -> ScheduleView:
        payload, template = self._checked_payload(payload)
        schedule = Schedule(
            name=payload.name,
            cron=payload.cron,
            timezone=payload.timezone,
            template=template,
            enabled=payload.enabled,
            priority=payload.priority,
            misfire_grace_seconds=payload.misfire_grace_seconds,
            catch_up=payload.catch_up,
            skip_if_running=payload.skip_if_running,
        )
        try:
            async with self.session_factory() as session:
                stored = await ScheduleRepository(session).create(schedule)
                await session.commit()
                stored_id = stored.id
        except IntegrityError as exc:
            if _name_conflict(exc):
                raise AppError(
                    ErrorCode.SCHEDULE_NAME_CONFLICT,
                    "已存在同名定时任务",
                    {"name": payload.name},
                ) from exc
            raise

        if stored.enabled:
            await self._register(stored)
        return await self.get(stored_id)

    async def update(self, schedule_id: str, payload: ScheduleWrite) -> ScheduleView:
        payload, template = self._checked_payload(payload)
        try:
            async with self.session_factory() as session:
                repo = ScheduleRepository(session)
                current = await repo.get(schedule_id)
                if current is None:
                    raise AppError(ErrorCode.SCHEDULE_NOT_FOUND, "定时任务不存在")
                current.name = payload.name
                current.cron = payload.cron
                current.timezone = payload.timezone
                current.template = template
                current.enabled = payload.enabled
                current.priority = payload.priority
                current.misfire_grace_seconds = payload.misfire_grace_seconds
                current.catch_up = payload.catch_up
                current.skip_if_running = payload.skip_if_running
                current.updated_at = utcnow()
                await repo.update(current)
                await session.commit()
        except IntegrityError as exc:
            if _name_conflict(exc):
                raise AppError(
                    ErrorCode.SCHEDULE_NAME_CONFLICT,
                    "已存在同名定时任务",
                    {"name": payload.name},
                ) from exc
            raise

        await self._sync(current)
        return await self.get(schedule_id)

    async def patch(self, schedule_id: str, changes: Mapping[str, Any]) -> ScheduleView:
        async with self.session_factory() as session:
            current = await ScheduleRepository(session).get(schedule_id)
            if current is None:
                raise AppError(ErrorCode.SCHEDULE_NOT_FOUND, "定时任务不存在")
            values = {
                "name": current.name,
                "cron": changes.get("cron", current.cron),
                "timezone": current.timezone,
                "template": current.template,
                "enabled": changes.get("enabled", current.enabled),
                "priority": changes.get("priority", current.priority),
                "misfire_grace_seconds": current.misfire_grace_seconds,
                "catch_up": current.catch_up,
                "skip_if_running": current.skip_if_running,
            }
        payload = ScheduleWrite.model_validate(values)
        return await self.update(schedule_id, payload)

    async def delete(self, schedule_id: str) -> None:
        async with self.session_factory() as session:
            deleted = await ScheduleRepository(session).delete(schedule_id)
            if not deleted:
                raise AppError(ErrorCode.SCHEDULE_NOT_FOUND, "定时任务不存在")
            await session.commit()
        self._remove(schedule_id)

    async def run_now(
        self, schedule_id: str, *, priority: int | None = None
    ) -> Pipeline:
        schedule = await self._load(schedule_id)
        selected_priority = schedule.priority if priority is None else priority
        return await self._submit_schedule(
            schedule,
            priority=selected_priority,
            skip_if_running=False,
            propagate_error=True,
        )

    async def load_enabled(self) -> None:
        """Load enabled schedules into the lifespan-owned scheduler."""
        async with self.session_factory() as session:
            schedules = await ScheduleRepository(session).list_enabled()
        for schedule in schedules:
            await self._register(schedule)

    async def close(self) -> None:
        """Remove this service's jobs without disturbing other scheduler jobs."""
        for schedule_id in tuple(self._owned_job_ids):
            self._remove(schedule_id)

    async def _load(self, schedule_id: str) -> Schedule:
        async with self.session_factory() as session:
            schedule = await ScheduleRepository(session).get(schedule_id)
            if schedule is None:
                raise AppError(ErrorCode.SCHEDULE_NOT_FOUND, "定时任务不存在")
            return schedule

    async def _register(self, schedule: Schedule) -> None:
        trigger = self._trigger(schedule.cron, schedule.timezone)
        job_id = self._job_id(schedule.id)
        job = self.scheduler.add_job(
            self._scheduled_trigger,
            trigger=trigger,
            kwargs={"schedule_id": schedule.id},
            id=job_id,
            name=schedule.name,
            replace_existing=True,
            max_instances=1,
            misfire_grace_time=schedule.misfire_grace_seconds,
            coalesce=not schedule.catch_up,
        )
        self._owned_job_ids.add(job_id)
        next_run_at = _next_run_as_utc(getattr(job, "next_run_time", None))
        if next_run_at is None:
            next_run_at = _next_run_as_utc(
                trigger.get_next_fire_time(None, datetime.now(timezone.utc))
            )
        await self._set_next_run(schedule.id, next_run_at)

    async def _sync(self, schedule: Schedule) -> None:
        if schedule.enabled:
            await self._register(schedule)
        else:
            self._remove(schedule.id)
            await self._set_next_run(schedule.id, None)

    def _remove(self, schedule_id: str) -> None:
        job_id = self._job_id(schedule_id)
        try:
            self.scheduler.remove_job(job_id)
        except JobLookupError:
            pass
        self._owned_job_ids.discard(job_id)

    async def _set_next_run(
        self, schedule_id: str, next_run_at: datetime | None
    ) -> None:
        async with self.session_factory() as session:
            changed = await ScheduleRepository(session).set_next_run_at(
                schedule_id, next_run_at
            )
            if changed:
                await session.commit()

    async def _scheduled_trigger(self, schedule_id: str) -> None:
        try:
            schedule = await self._load(schedule_id)
            if not schedule.enabled:
                return
            trigger = self._trigger(schedule.cron, schedule.timezone)
            next_run_at = _next_run_as_utc(
                trigger.get_next_fire_time(None, datetime.now(timezone.utc))
            )
            await self._submit_schedule(
                schedule,
                priority=schedule.priority,
                skip_if_running=schedule.skip_if_running,
                next_run_at=next_run_at,
                propagate_error=False,
            )
        except Exception:
            logger.exception("定时任务 %s 执行失败", schedule_id)

    async def _submit_schedule(
        self,
        schedule: Schedule,
        *,
        priority: int,
        skip_if_running: bool,
        next_run_at: datetime | None = None,
        propagate_error: bool,
    ) -> Pipeline | None:
        fired_at = utcnow()
        if skip_if_running and schedule.skip_if_running:
            async with self.session_factory() as session:
                repo = ScheduleRepository(session)
                if await repo.has_unfinished(schedule.id):
                    await repo.record_run(
                        schedule.id,
                        last_run_at=fired_at,
                        next_run_at=next_run_at or schedule.next_run_at,
                        pipeline_id=None,
                        result="skipped",
                    )
                    await session.commit()
                    return None

        try:
            template = schedule.template
            payload = _validate_template(template, priority)
            pipeline, _duplicate = await self.queue_service.submit(
                payload,
                source=PipelineSource.SCHEDULED,
                schedule_id=schedule.id,
            )
        except Exception:
            if propagate_error:
                raise
            async with self.session_factory() as session:
                await ScheduleRepository(session).record_run(
                    schedule.id,
                    last_run_at=fired_at,
                    next_run_at=next_run_at or schedule.next_run_at,
                    pipeline_id=None,
                    result="failed",
                )
                await session.commit()
            logger.exception("定时任务 %s 未能提交到队列", schedule.id)
            return None

        async with self.session_factory() as session:
            await ScheduleRepository(session).record_run(
                schedule.id,
                last_run_at=fired_at,
                next_run_at=next_run_at or schedule.next_run_at,
                pipeline_id=pipeline.id,
                result=PipelineStatus.PENDING,
            )
            await session.commit()
        try:
            await self.pipeline_runner.publish_pipeline(pipeline.id)
            await self.pipeline_runner.publish_queue_changed()
        except Exception:
            # QueueService.submit already wakes the single consumer. A WebSocket
            # broadcast failure must not turn a successful admission into a 500.
            logger.exception("发布 schedule 流水线 %s 的状态事件失败", pipeline.id)
        return pipeline

    def _view(
        self, schedule: Schedule, pipelines: list[Pipeline]
    ) -> ScheduleView:
        recent = [self._pipeline_view(pipeline) for pipeline in pipelines]
        if schedule.last_pipeline_id is None and schedule.last_result:
            synthetic = ScheduleRunView(
                pipeline_id=None,
                status=schedule.last_result,
                started_at=_api_datetime(schedule.last_run_at),
                finished_at=_api_datetime(schedule.last_run_at),
                error=None,
            )
            recent.insert(0, synthetic)
            recent = recent[:RECENT_RUNS_LIMIT]
        latest = recent[0] if recent else None
        return ScheduleView(
            id=schedule.id,
            name=schedule.name,
            cron=schedule.cron,
            timezone=schedule.timezone,
            template=schedule.template,
            enabled=schedule.enabled,
            priority=schedule.priority,
            next_run_at=_api_datetime(schedule.next_run_at),
            last_run_at=_api_datetime(schedule.last_run_at),
            last_run_result=latest,
            recent_runs=recent,
        )

    @staticmethod
    def _pipeline_view(pipeline: Pipeline) -> ScheduleRunView:
        error = None
        if pipeline.error_code or pipeline.error_message:
            error = {
                "code": pipeline.error_code,
                "message": pipeline.error_message,
            }
        return ScheduleRunView(
            pipeline_id=pipeline.id,
            status=str(pipeline.status),
            started_at=_api_datetime(pipeline.started_at),
            finished_at=_api_datetime(pipeline.finished_at),
            error=error,
        )

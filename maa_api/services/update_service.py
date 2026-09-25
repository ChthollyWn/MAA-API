"""Shared orchestration for MaaCore, resource and game updates (docs/07 §5).

The individual workflows own network, validation, filesystem and device details.
This service owns update records, one in-process maintenance lock, progress events,
inspection caching, retry policy and scheduled availability notifications.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from maa_api.db.models import ResourceAsset, UpdateRecord, utcnow
from maa_api.db.repositories.resource import ResourceAssetRepository
from maa_api.db.repositories.setting import SettingRepository
from maa_api.db.repositories.update import UpdateRepository
from maa_api.db.session import session_factory as default_session_factory
from maa_api.domain.enums import (
    NotifyEvent,
    ResourceAssetKind,
    ResourceChannel,
    UpdatePhase,
    UpdateStatus,
    UpdateTarget,
)
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.core_update import (
    CORE_ARCHIVE_CACHE_TTL_SECONDS,
    resolve_core_release,
)
from maa_api.services.game_update import validate_apk
from maa_api.services.log_hub import create_task_without_request_id

logger = logging.getLogger(__name__)

_NOTIFIED_VERSIONS_KEY = "updates.notified_versions"
_NOTIFICATION_CACHE_SECONDS = 300.0
_WS_INTERVAL_SECONDS = 0.5
_DB_INTERVAL_SECONDS = 5.0
_TERMINAL_STATUSES = {
    UpdateStatus.SUCCESS,
    UpdateStatus.FAILED,
    UpdateStatus.CANCELLED,
    UpdateStatus.SKIPPED,
}


async def _call(callback: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    result = callback(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _value(value: Any) -> Any:
    return getattr(value, "value", value)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _error_code(exc: BaseException) -> str:
    code = getattr(exc, "code", None)
    if code:
        return str(_value(code))
    message = str(exc)
    prefix, separator, _ = message.partition(":")
    if separator and prefix.replace("_", "").isalnum() and prefix.upper() == prefix:
        return prefix[:48]
    return "UPDATE_FAILED"


def _error_message(exc: BaseException) -> str:
    message = getattr(exc, "message", None)
    return str(message if message is not None else exc)[:4000]


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "__dict__"):
        return {
            key: item
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return {}


class UpdateService:
    """One public orchestrator shared by all three update targets.

    ``session_factory`` is an ``async_sessionmaker``. Each background operation
    opens its own sessions; no ORM session crosses task boundaries. Workflow
    instances are injected so the API composition root can bind the real core,
    pipeline, resource layers and safe APK installer.
    """

    def __init__(
        self,
        session_factory: Callable[[], AsyncSession] = default_session_factory,
        *,
        core_workflow: Any = None,
        resource_workflow: Any = None,
        game_workflow: Any = None,
        broadcast: Callable[[str, dict[str, Any]], Any] | None = None,
        notify: Any = None,
        prepare_update: Callable[[UpdateTarget, Mapping[str, Any]], Any] | None = None,
        resource_layers_loaded: Callable[[], Any] | None = None,
        temp_root: str | Path = Path("resource") / "temp" / "updates",
        cache_ttl: float = _NOTIFICATION_CACHE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.session_factory = session_factory
        self.core_workflow = core_workflow
        self.resource_workflow = resource_workflow
        self.game_workflow = game_workflow
        self.broadcast = broadcast or (lambda _kind, _data: None)
        self.notify = notify
        self.prepare_update = prepare_update
        self.resource_layers_loaded = resource_layers_loaded
        self._process_id = uuid.uuid4().hex
        self.temp_root = Path(temp_root)
        self.cache_ttl = max(float(cache_ttl), 0.0)
        self._clock = clock
        self._lock = asyncio.Lock()
        self._current_id: str | None = None
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._options: dict[str, dict[str, Any]] = {}
        self._staged: dict[str, list[dict[str, Any]]] = {}
        self._status_cache: tuple[float, dict[str, Any], dict[str, Any]] | None = None
        self._progress_state: dict[str, dict[str, Any]] = {}
        self._notification_state: dict[str, str] | None = None
        self._status_checked_at: str | None = None
        self._last_announced: list[dict[str, Any]] = []

    # ---- read API ---------------------------------------------------------

    async def status(self, refresh: bool = False) -> dict[str, Any]:
        """Return cached availability for five minutes unless explicitly refreshed.

        A failed or unavailable source is represented as ``available=None``;
        callers can never mistake a network failure for ``SKIPPED``/up-to-date.
        This method is also used by automation and deliberately creates no
        ``update_record`` rows.
        """
        now = self._clock()
        if (
            not refresh
            and self._status_cache is not None
            and now - self._status_cache[0] < self.cache_ttl
        ):
            internal, public = self._status_cache[1], self._status_cache[2]
            return await self._status_view(internal, public, cached=True)

        internal: dict[str, Any] = {}
        public: dict[str, Any] = {}
        for target in UpdateTarget:
            try:
                state = await self._inspect_target(target)
                internal[target.value] = state
                public[target.value] = await self._public_state(target, state)
            except Exception as exc:
                logger.warning("update check failed for %s: %s", target.value, exc)
                state = {"error": _error_message(exc), "available": None}
                internal[target.value] = state
                public[target.value] = await self._public_state(target, state)

        self._status_cache = (self._clock(), internal, public)
        self._status_checked_at = _iso(utcnow())
        self._last_announced = await self._announce_available(public)
        return await self._status_view(internal, public, cached=False)

    async def get(self, update_id: str) -> UpdateRecord:
        async with self.session_factory() as session:
            record = await UpdateRepository(session).get(update_id)
            if record is None:
                raise AppError(ErrorCode.UPDATE_NOT_FOUND, "更新记录不存在")
            return record

    async def list(
        self,
        *,
        target: UpdateTarget | str | None = None,
        status: UpdateStatus | str | None = None,
        page: int = 1,
        size: int = 20,
    ):
        normalized = UpdateTarget(target) if target is not None else None
        normalized_status = UpdateStatus(status) if status is not None else None
        async with self.session_factory() as session:
            return await UpdateRepository(session).list(
                target=normalized,
                status=normalized_status,
                page=page,
                size=size,
            )

    # ---- update lifecycle ------------------------------------------------

    async def start(
        self,
        target: UpdateTarget | str,
        options: Mapping[str, Any] | None = None,
        caller: str = "manual",
    ) -> UpdateRecord:
        """Create a RUNNING row and launch its workflow without blocking the API."""
        selected = UpdateTarget(target)
        opts = dict(options or {})
        opts.setdefault("_caller", self._caller(caller))
        if self._lock.locked():
            raise await self._already_running_error()
        # asyncio.Lock acquisition is immediate if still unlocked; no await is
        # performed between the locked check and acquisition by another task.
        await self._lock.acquire()
        record: UpdateRecord | None = None
        try:
            existing = await self._running_record()
            if existing is not None:
                raise self._busy_error(existing)
            await self._prune_expired_caches(selected)
            channel = self._record_channel(selected, opts)
            record = UpdateRecord(
                target=selected,
                channel=channel,
                status=UpdateStatus.RUNNING,
                phase=UpdatePhase.CHECKING,
                progress=0,
                triggered_by=self._caller(caller),
                log=json.dumps({"options": self._public_options(opts)}, ensure_ascii=False),
            )
            async with self.session_factory() as session:
                stored = await UpdateRepository(session).create(record)
                await session.commit()
                record = stored
            self._current_id = record.id
            self._options[record.id] = opts
            task = create_task_without_request_id(self._run(record.id, opts))
            self._tasks[record.id] = task
            return record
        except IntegrityError as exc:
            await self._rollback_session()
            self._lock.release()
            raise AppError(
                ErrorCode.UPDATE_ALREADY_RUNNING,
                "已有更新任务正在执行",
                details={"target": selected.value},
            ) from exc
        except BaseException:
            if record is None or record.id != self._current_id:
                if self._lock.locked():
                    self._lock.release()
            raise

    async def cancel(self, update_id: str) -> UpdateRecord:
        record = await self.get(update_id)
        task = self._tasks.get(update_id)
        if record.status != UpdateStatus.RUNNING or task is None or task.done():
            raise AppError(
                ErrorCode.UPDATE_NOT_CANCELLABLE,
                "该更新当前不能取消",
                details={"update_id": update_id, "status": str(record.status)},
            )
        current_phase = self._progress_state.get(
            update_id, {}
        ).get("phase", str(_value(record.phase)))
        if current_phase in {UpdatePhase.APPLYING.value, UpdatePhase.RESTARTING.value}:
            raise AppError(
                ErrorCode.UPDATE_NOT_CANCELLABLE,
                "更新已进入应用或重启阶段，不能取消",
                details={"update_id": update_id, "phase": str(_value(record.phase))},
            )
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await self._cleanup_temporary_files(update_id)
        return await self.get(update_id)

    async def retry(self, update_id: str, caller: str = "manual") -> UpdateRecord:
        original = await self.get(update_id)
        if original.status != UpdateStatus.FAILED:
            raise AppError(
                ErrorCode.UPDATE_NOT_CANCELLABLE,
                "只有失败的更新可以重试",
                details={"update_id": update_id, "status": str(original.status)},
            )
        try:
            log = json.loads(original.log or "{}")
        except (TypeError, json.JSONDecodeError):
            log = {}
        options = dict(log.get("options") or self._options.get(update_id) or {})
        staged = log.get("staged") or self._staged.get(update_id) or []
        if staged:
            options["_retry_staged"] = staged
        options["_retry_of"] = update_id
        return await self.start(original.target, options, caller)

    async def rollback_core(
        self,
        *,
        force_interrupt: bool = False,
        caller: str = "manual",
    ) -> UpdateRecord:
        """Queue an audited core rollback through the same global update lock."""
        return await self.start(
            UpdateTarget.CORE,
            {
                "channel": "stable",
                "_operation": "rollback",
                "force_interrupt": bool(force_interrupt),
            },
            caller,
        )

    async def recover_interrupted(self) -> int:
        """Close RUNNING rows left behind by a killed process as interrupted."""
        interrupted: list[UpdateRecord] = []
        async with self.session_factory() as session:
            repo = UpdateRepository(session)
            page = 1
            while True:
                result = await repo.list(page=page, size=500)
                interrupted.extend(
                    item for item in result.items
                    if item.status == UpdateStatus.RUNNING
                )
                if len(result.items) < 500:
                    break
                page += 1
            for record in interrupted:
                await repo.mark_terminal(
                    record.id,
                    UpdateStatus.FAILED,
                    error_code="UPDATE_INTERRUPTED",
                    error_message="服务重启时更新仍处于运行状态",
                )
            await session.commit()
        if self._current_id and any(row.id == self._current_id for row in interrupted):
            self._current_id = None
        return len(interrupted)

    # ---- scheduled availability notifications ---------------------------

    async def daily_check(self) -> dict[str, Any]:
        """Inspect all targets, persist check timestamps and notify each version once."""
        for target in UpdateTarget:
            if await self._running_target_matches(target):
                continue
            await self._prune_expired_caches(target)
        snapshot = await self.status(refresh=True)
        states = snapshot["updates"]
        return {
            "checked_at": snapshot["checked_at"],
            "updates": states,
            "notified": list(self._last_announced),
        }

    async def _announce_available(self, states: Mapping[str, Any]) -> list[dict[str, Any]]:
        candidates = self._available_targets(states)
        notified = await self._load_notification_state()
        fresh = [
            item for item in candidates
            if notified.get(self._notification_key(item)) != self._fingerprint(item)
        ]
        if not fresh:
            return []
        payload = {"targets": fresh}
        try:
            await _call(self.broadcast, "update_available", payload)
        except Exception:
            logger.exception("could not broadcast update_available")
        try:
            await self._send_notification(payload)
        except Exception:
            logger.exception("could not deliver update_available notification")
        for item in fresh:
            notified[self._notification_key(item)] = self._fingerprint(item)
        await self._save_notification_state(notified)
        return fresh

    # ---- workflow orchestration ------------------------------------------

    async def _run(self, update_id: str, options: dict[str, Any]) -> None:
        try:
            record = await self.get(update_id)
            target = UpdateTarget(record.target)
            force = bool(options.get("force", False))
            is_core_rollback = (
                target is UpdateTarget.CORE
                and options.get("_operation") == "rollback"
            )
            retry_staged = options.get("_retry_staged") or []
            if is_core_rollback:
                workflow = self._required_workflow(UpdateTarget.CORE)
                current = None
                callback = getattr(workflow, "current_version", None)
                if callback is not None:
                    try:
                        current = await _call(callback, workflow.core_client)
                    except Exception:
                        logger.info("could not read the running MaaCore version before rollback")
                state = {"current": current, "latest": None, "available": True}
            elif (
                target is UpdateTarget.RESOURCE
                and retry_staged
                and self._retry_files_available(retry_staged)
            ):
                state = self._retry_resource_state(retry_staged)
                await self._emit_progress(update_id, target, {"phase": "checking"})
            else:
                state = await self._inspect_for_update(
                    target, update_id, refresh=force
                )
            if state.get("error") and target is UpdateTarget.CORE:
                raise RuntimeError(state["error"])
            requested_core_version = options.get("version")
            if (
                target is UpdateTarget.CORE
                and not is_core_rollback
                and requested_core_version is not None
                and str(requested_core_version) != str(state.get("latest"))
            ):
                raise AppError(
                    ErrorCode.INVALID_PARAMETER,
                    "只允许安装当前 stable 清单中的 MaaCore 版本",
                    {
                        "requested_version": str(requested_core_version),
                        "stable_version": state.get("latest"),
                    },
                )
            if target is UpdateTarget.GAME:
                game_channel = str(options.get("channel", "Official"))
                game_state = state.get("channels", {}).get(game_channel, {})
                if game_state.get("error"):
                    raise RuntimeError(game_state["error"])
            if target is UpdateTarget.RESOURCE and not any(
                not _field(check, "error") for check in state.get("checks", {}).values()
            ):
                raise RuntimeError(state.get("error") or "资源更新检查不可用")
            if target is UpdateTarget.RESOURCE:
                selected_channel = ResourceChannel(options.get("channel", ResourceChannel.ALL))
                if selected_channel is ResourceChannel.ALL:
                    ota_check = state.get("checks", {}).get(ResourceChannel.OTA)
                    if ota_check is None or _field(ota_check, "error"):
                        raise RuntimeError(
                            _field(ota_check, "error") or "OTA 资源检查不可用"
                        )
            if (
                not is_core_rollback
                and self._is_confirmed_current(target, state, options)
                and not force
            ):
                await self._emit_progress(update_id, target, {"phase": "done", "percent": 100})
                await self._terminal(update_id, UpdateStatus.SKIPPED)
                return

            await self._save_versions(update_id, target, state)
            reload_mode = str(options.get("reload_mode", "wait"))
            if self.prepare_update is not None and not (
                target is UpdateTarget.RESOURCE and reload_mode == "defer"
            ):
                preparation_options = dict(options)
                if target is UpdateTarget.RESOURCE and reload_mode == "force":
                    preparation_options["force_interrupt"] = True
                await _call(self.prepare_update, target, preparation_options)
            if target is UpdateTarget.CORE:
                applied = await self._run_core(update_id, options)
                if applied is None:
                    await self._emit_progress(update_id, target, {"phase": "done", "percent": 100})
                    await self._terminal(update_id, UpdateStatus.SKIPPED)
                    return
                if is_core_rollback or applied is not None:
                    await self._store_reload_pending(False)
            elif target is UpdateTarget.RESOURCE:
                changed = await self._run_resource(update_id, options, state)
                if not changed:
                    await self._emit_progress(
                        update_id,
                        target,
                        {"phase": "done", "percent": 100},
                    )
                    await self._terminal(update_id, UpdateStatus.SKIPPED)
                    return
            else:
                await self._run_game(update_id, options, state)
            await self._emit_progress(update_id, target, {"phase": "done", "percent": 100})
            await self._terminal(update_id, UpdateStatus.SUCCESS)
        except asyncio.CancelledError:
            try:
                record = await self.get(update_id)
                await self._emit_progress(
                    update_id,
                    UpdateTarget(record.target),
                    {"phase": "failed", "error": "cancelled"},
                )
                await self._terminal(
                    update_id,
                    UpdateStatus.CANCELLED,
                    error_code="UPDATE_CANCELLED",
                    error_message="更新已由用户取消",
                )
            finally:
                raise
        except Exception as exc:
            logger.exception("update %s failed", update_id)
            try:
                record = await self.get(update_id)
                await self._emit_progress(
                    update_id,
                    UpdateTarget(record.target),
                    {"phase": "failed", "error": _error_message(exc)},
                )
                await self._terminal(
                    update_id,
                    UpdateStatus.FAILED,
                    error_code=_error_code(exc),
                    error_message=_error_message(exc),
                )
            except Exception:
                logger.exception("could not persist failure for update %s", update_id)
        finally:
            if self._current_id == update_id:
                self._current_id = None
            self._status_cache = None
            self._options.pop(update_id, None)
            self._tasks.pop(update_id, None)
            self._progress_state.pop(update_id, None)
            if self._lock.locked():
                self._lock.release()

    async def _run_core(self, update_id: str, options: Mapping[str, Any]) -> Any:
        workflow = self._required_workflow(UpdateTarget.CORE)
        target = UpdateTarget.CORE
        async with self._workflow_progress(workflow, update_id, target):
            if options.get("_operation") == "rollback":
                await self._emit_progress(
                    update_id,
                    target,
                    {"phase": "applying", "message": "正在回滚 MaaCore"},
                )
                version = await _call(workflow.rollback)
                if version is not None:
                    async with self.session_factory() as session:
                        record = await UpdateRepository(session).get(update_id)
                        if record is not None:
                            record.to_version = str(version)[:64]
                            await session.commit()
                return version
            return await _call(
                workflow.update,
                channel=str(options.get("channel", "stable")),
                force=bool(options.get("force", False)),
            )

    async def _run_resource(
        self, update_id: str, options: dict[str, Any], state: dict[str, Any]
    ) -> bool:
        workflow = self._required_workflow(UpdateTarget.RESOURCE)
        channel = ResourceChannel(options.get("channel", ResourceChannel.ALL))
        checks: dict[ResourceChannel, Any] = state["checks"]
        changed = [
            check for key, check in checks.items()
            if key in self._channels(channel)
            and not _field(check, "error")
            and bool(_field(check, "changed"))
        ]
        if not changed:
            errors = [
                (key, str(_field(check, "error")))
                for key, check in checks.items()
                if _field(check, "error")
            ]
            if (
                channel is ResourceChannel.ALL
                and not _field(checks.get(ResourceChannel.OTA), "error")
                and checks.get(ResourceChannel.OTA) is not None
                and not bool(_field(checks[ResourceChannel.OTA], "changed"))
            ):
                if errors:
                    await self._save_log(
                        update_id,
                        {
                            "options": self._public_options(options),
                            "optional_errors": [
                                {"channel": key.value, "error": message}
                                for key, message in errors
                            ],
                        },
                    )
                    logger.warning(
                        "resource OTA is current; optional channel errors: %s",
                        "; ".join(message for _, message in errors),
                    )
                return False
            if errors:
                raise RuntimeError("; ".join(message for _, message in errors))
            return False

        staged: list[Any] = []
        stage_errors: list[tuple[ResourceChannel, BaseException]] = []
        if channel is ResourceChannel.ALL:
            for checked_channel, check in checks.items():
                error = _field(check, "error")
                if error and checked_channel is not ResourceChannel.OTA:
                    stage_errors.append(
                        (ResourceChannel(_value(checked_channel)), RuntimeError(str(error)))
                    )
        retry_staged = options.get("_retry_staged") or []
        if retry_staged:
            for item in retry_staged:
                restored = self._restore_staged(item)
                if restored is None:
                    continue
                if Path(restored.path).exists():
                    staged.append(restored)
                    continue
                restage_cached = getattr(workflow, "restage_cached", None)
                if (
                    restored.archive_path is not None
                    and Path(restored.archive_path).is_file()
                    and callable(restage_cached)
                ):
                    staged.append(await _call(restage_cached, restored))

        already = {ResourceChannel(_value(item.channel)) for item in staged}
        for check in changed:
            selected = ResourceChannel(_value(_field(check, "channel")))
            if selected in already:
                continue
            try:
                result = await _call(workflow.stage, check)
                staged.append(result)
                already.add(selected)
            except Exception as exc:
                stage_errors.append((selected, exc))
                # In channel=all the repo layer is optional: a failed repo
                # archive must not discard a successfully staged OTA manifest.
                if selected is ResourceChannel.OTA:
                    raise
                logger.warning("resource %s stage failed: %s", selected.value, exc)

        if not staged:
            if stage_errors:
                raise stage_errors[0][1]
            raise RuntimeError("没有可安装的资源更新")

        serialized = [self._serialize_staged(item) for item in staged]
        self._staged[update_id] = serialized
        await self._save_log(
            update_id,
            {
                "options": self._public_options(options),
                "staged": serialized,
                "optional_errors": [
                    {"channel": channel.value, "error": _error_message(error)}
                    for channel, error in stage_errors
                ],
            },
        )
        self._temp_paths(update_id, staged)

        async with self._workflow_progress(workflow, update_id, UpdateTarget.RESOURCE):
            install_all = getattr(workflow, "install_all", None)
            reload_mode = str(options.get("reload_mode", "wait"))
            if callable(install_all):
                if reload_mode != "defer":
                    await self._emit_progress(update_id, UpdateTarget.RESOURCE, {"phase": "waiting_idle"})
                await self._emit_progress(update_id, UpdateTarget.RESOURCE, {"phase": "applying"})
                await self._call_with_supported_kwargs(
                    install_all, staged, reload_mode=reload_mode
                )
            elif len(staged) == 1 and reload_mode != "defer":
                await self._emit_progress(update_id, UpdateTarget.RESOURCE, {"phase": "waiting_idle"})
                await self._emit_progress(update_id, UpdateTarget.RESOURCE, {"phase": "applying"})
                await _call(workflow.install, staged[0])
            else:
                raise RuntimeError("多通道资源更新需要 ResourceUpdateWorkflow.install_all")

        staged_by_channel = {
            ResourceChannel(_value(item.channel)): item for item in staged
        }
        for check in changed:
            selected = ResourceChannel(_value(_field(check, "channel")))
            staged_result = staged_by_channel.get(selected)
            if staged_result is not None:
                await self._persist_installed_resource(
                    check,
                    actual_version=getattr(staged_result, "version", None),
                )
        if stage_errors:
            logger.warning(
                "resource update completed partially; optional repo stage failed: %s",
                "; ".join(str(error) for _, error in stage_errors),
            )
        if reload_mode == "defer":
            setattr(workflow, "reload_pending", True)
            await self._store_reload_pending(True)
            await self._save_log(
                update_id,
                {
                    "options": self._public_options(options),
                    "staged": serialized,
                    "reload_pending": True,
                    "optional_errors": [
                        {"channel": item.value, "error": _error_message(error)}
                        for item, error in stage_errors
                    ],
                },
            )
        else:
            setattr(workflow, "reload_pending", False)
            await self._store_reload_pending(False)
        return True

    async def _run_game(
        self, update_id: str, options: Mapping[str, Any], state: dict[str, Any]
    ) -> None:
        workflow = self._required_workflow(UpdateTarget.GAME)
        channel = str(options.get("channel", "Official"))
        info = state["channels"].get(channel)
        if info is None or info.get("error"):
            raise RuntimeError(f"游戏渠道 {channel!r} 检查不可用")
        game_info = info.get("info")
        latest = _field(info, "latest")
        current = _field(info, "current")
        size = _field(info, "remote_size")
        self.temp_root.mkdir(parents=True, exist_ok=True)
        destination = self.temp_root / f"game-{channel.lower()}.apk"
        manifest_path = destination.with_suffix(destination.suffix + ".verified.json")
        reused = False
        try:
            metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
            digest = self._file_sha256(destination)
            if (
                destination.is_file()
                and metadata.get("channel") == channel
                and metadata.get("latest") == latest
                and metadata.get("sha256") == digest
            ):
                validate_apk(destination, expected_size=size)
                reused = True
        except (OSError, ValueError, TypeError, KeyError):
            reused = False

        async with self._workflow_progress(workflow, update_id, UpdateTarget.GAME):
            self._temp_paths(update_id, [destination, manifest_path])
            if not reused:
                await self._emit_progress(update_id, UpdateTarget.GAME, {"phase": "downloading"})
                validation = await _call(
                    workflow.download_latest,
                    channel,
                    destination,
                    on_progress=lambda done, total: self._game_download_progress(
                        update_id, done, total
                    ),
                )
                validate_apk(destination, expected_size=size)
                manifest_path.write_text(
                    json.dumps(
                        {
                            "channel": channel,
                            "latest": latest,
                            "size": int(_field(validation, "size", destination.stat().st_size)),
                            "sha256": self._file_sha256(destination),
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
            await self._emit_progress(update_id, UpdateTarget.GAME, {"phase": "waiting_idle"})
            await _call(
                workflow.install,
                channel,
                destination,
                expected_size=size,
                expected_md5=_field(game_info, "md5"),
                on_progress=lambda message="": self._game_install_progress(
                    update_id, message
                ),
            )
        self._temp_paths(update_id, [destination, manifest_path])

    async def _game_download_progress(self, update_id: str, done: int, total: int) -> None:
        await self._emit_progress(
            update_id,
            UpdateTarget.GAME,
            {"phase": "downloading", "downloaded": done, "total": total},
        )

    async def _game_install_progress(self, update_id: str, message: str = "") -> None:
        await self._emit_progress(
            update_id,
            UpdateTarget.GAME,
            {"phase": "applying", "message": message},
        )

    @staticmethod
    async def _call_with_supported_kwargs(
        callback: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> Any:
        try:
            parameters = inspect.signature(callback).parameters.values()
            accepts_kwargs = any(item.kind is inspect.Parameter.VAR_KEYWORD for item in parameters)
            accepted = kwargs if accepts_kwargs else {
                key: value for key, value in kwargs.items()
                if key in inspect.signature(callback).parameters
            }
        except (TypeError, ValueError):
            accepted = {}
        return await _call(callback, *args, **accepted)

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    # ---- inspection and serialization ------------------------------------

    async def _inspect_target(
        self, target: UpdateTarget, progress_id: str | None = None
    ) -> dict[str, Any]:
        workflow = self._required_workflow(target)

        async def inspect() -> dict[str, Any]:
            if target is UpdateTarget.CORE:
                return await self._inspect_core()
            if target is UpdateTarget.RESOURCE:
                return await self._inspect_resources()
            return await self._inspect_game()

        if progress_id is None:
            return await inspect()
        async with self._workflow_progress(workflow, progress_id, target):
            return await inspect()

    async def _inspect_for_update(
        self, target: UpdateTarget, update_id: str, *, refresh: bool
    ) -> dict[str, Any]:
        await self._emit_progress(update_id, target, {"phase": "checking"})
        now = self._clock()
        if (
            not refresh
            and self._status_cache is not None
            and now - self._status_cache[0] < self.cache_ttl
            and target.value in self._status_cache[1]
        ):
            return self._status_cache[1][target.value]
        return await self._inspect_target(target, progress_id=update_id)

    async def _inspect_core(self) -> dict[str, Any]:
        workflow = self._required_workflow(UpdateTarget.CORE)
        custom_check = getattr(workflow, "check", None)
        if callable(custom_check):
            try:
                result = await _call(custom_check)
                current = _field(result, "current_version", _field(result, "current"))
                latest = _field(result, "latest_version", _field(result, "latest"))
                available = _field(result, "available")
                if available is None and latest is not None and current is not None:
                    available = latest != current
                return {
                    "current": current,
                    "latest": latest,
                    "available": available,
                    "release": _field(result, "release"),
                }
            except Exception as exc:
                logger.info("MaaCore update check unavailable: %s", exc)
                return {"current": None, "latest": None, "available": None, "error": _error_message(exc)}

        try:
            client = getattr(workflow, "http_client", getattr(workflow, "client", None))
            if client is None:
                raise RuntimeError("MaaCore workflow has no injectable HTTP client")
            release = await resolve_core_release(
                client,
                system=getattr(workflow, "system", None),
                machine=getattr(workflow, "machine", None),
            )
            current = None
            callback = getattr(workflow, "current_version", None)
            if callback is not None:
                try:
                    current = await _call(callback, workflow.core_client)
                except Exception:
                    logger.info("could not read the installed MaaCore version")
            return {
                "current": current,
                "latest": release.version,
                "available": release.version != current if current is not None else None,
                "release": release,
            }
        except Exception as exc:
            logger.info("MaaCore update check unavailable: %s", exc)
            return {"current": None, "latest": None, "available": None, "error": _error_message(exc)}

    async def _inspect_resources(self) -> dict[str, Any]:
        workflow = self._required_workflow(UpdateTarget.RESOURCE)
        rows = await self._resource_metadata()
        repo = rows.get(ResourceAssetKind.REPO_RESOURCE.value)
        ota = rows.get(ResourceAssetKind.OTA_RESOURCE.value)
        checks = await _call(
            workflow.check,
            ResourceChannel.ALL,
            ota_metadata=ota,
            repo_version=_field(repo, "remote_version"),
        )
        normalized: dict[ResourceChannel, Any] = {
            ResourceChannel(_value(key)): value for key, value in checks.items()
        }
        await self._persist_resource_checks(normalized)
        channels: dict[str, Any] = {}
        for channel, check in normalized.items():
            if channel is ResourceChannel.OTA:
                current = _field(ota, "checksum")
                latest = _field(check, "checksum") or _field(check, "etag")
                available = bool(_field(check, "changed")) if not _field(check, "error") else None
            else:
                current = _field(repo, "remote_version")
                latest = _field(check, "version")
                available = bool(_field(check, "changed")) if not _field(check, "error") else None
            channels[channel.value] = {
                "channel": channel.value,
                "current": current,
                "latest": latest,
                "available": available,
                "error": _field(check, "error"),
                "check": check,
            }
        known = [item["available"] for item in channels.values() if item["available"] is not None]
        errors = [item["error"] for item in channels.values() if item["error"]]
        available = True if True in known else False if known and not errors else None
        return {"channels": channels, "available": available, "error": "; ".join(errors) or None, "checks": normalized}

    async def _inspect_game(self) -> dict[str, Any]:
        workflow = self._required_workflow(UpdateTarget.GAME)
        channels: dict[str, Any] = {}
        errors: list[str] = []
        for channel in ("Official", "Bilibili"):
            try:
                info = await _call(workflow.inspect, channel)
                current = _field(_field(info, "installed"), "version_name")
                latest = _field(info, "latest_version") or _field(info, "remote_updated_at")
                available = _field(info, "update_may_be_available")
                if available is None and _field(info, "latest_version") is not None and current is not None:
                    available = _field(info, "latest_version") != current
                channels[channel] = {
                    "channel": channel,
                    "current": current,
                    "latest": latest,
                    "remote_size": _field(info, "remote_size"),
                    "available": available,
                    "info": info,
                }
            except Exception as exc:
                errors.append(f"{channel}: {_error_message(exc)}")
                channels[channel] = {"channel": channel, "available": None, "error": _error_message(exc)}
        available_values = [row["available"] for row in channels.values() if row.get("available") is not None]
        available = True if True in available_values else False if available_values and len(available_values) == len(channels) else None
        return {"channels": channels, "available": available, "error": "; ".join(errors) or None}

    def _retry_resource_state(self, staged_items: list[Mapping[str, Any]]) -> dict[str, Any]:
        checks: dict[ResourceChannel, dict[str, Any]] = {}
        channels: dict[str, dict[str, Any]] = {}
        for item in staged_items:
            staged = self._restore_staged(item)
            if staged is None or not Path(staged.path).exists():
                continue
            channel = ResourceChannel(_value(staged.channel))
            check = {
                "channel": channel,
                "changed": True,
                "version": staged.version,
                "checksum": staged.checksum,
                "etag": staged.etag,
                "last_modified": staged.last_modified,
                "error": None,
            }
            checks[channel] = check
            channels[channel.value] = {
                "channel": channel.value,
                "current": None,
                "latest": staged.version or staged.checksum or staged.etag,
                "available": True,
                "error": None,
                "check": check,
            }
        if ResourceChannel.OTA not in checks:
            checks[ResourceChannel.OTA] = {
                "channel": ResourceChannel.OTA,
                "changed": False,
                "error": None,
            }
            channels[ResourceChannel.OTA.value] = {
                "channel": ResourceChannel.OTA.value,
                "current": None,
                "latest": None,
                "available": False,
                "error": None,
                "check": checks[ResourceChannel.OTA],
            }
        return {
            "checks": checks,
            "channels": channels,
            "available": bool(checks),
            "error": None if checks else "已验证的资源临时文件已不存在",
        }

    def _retry_files_available(self, staged_items: list[Mapping[str, Any]]) -> bool:
        if not staged_items:
            return False
        for item in staged_items:
            staged = self._restore_staged(item)
            if staged is None or not (
                Path(staged.path).exists()
                or (
                    staged.archive_path is not None
                    and Path(staged.archive_path).is_file()
                )
            ):
                return False
        return True

    async def _public_state(self, target: UpdateTarget, state: dict[str, Any]) -> dict[str, Any]:
        if target is UpdateTarget.CORE:
            return {
                "target": target.value,
                "current": state.get("current"),
                "latest": state.get("latest"),
                "available": state.get("available"),
                "error": state.get("error"),
            }
        if target is UpdateTarget.RESOURCE:
            channels = {
                name: {key: value for key, value in row.items() if key != "check"}
                for name, row in state.get("channels", {}).items()
            }
            return {
                "target": target.value,
                "available": state.get("available"),
                "channels": channels,
                "reload_pending": await self._get_reload_pending(),
                "error": state.get("error"),
            }
        channels = {
            name: {key: value for key, value in row.items() if key != "info"}
            for name, row in state.get("channels", {}).items()
        }
        return {
            "target": target.value,
            "available": state.get("available"),
            "channels": channels,
            "error": state.get("error"),
        }

    async def _status_view(
        self,
        internal: dict[str, Any],
        public: dict[str, Any],
        *,
        cached: bool,
    ) -> dict[str, Any]:
        running = None
        if self._current_id is not None:
            try:
                record = await self.get(self._current_id)
                running = self._record_dict(record) if record is not None else None
            except Exception:
                running = None
        return {
            "updates": public,
            "checked_at": self._status_checked_at or _iso(utcnow()),
            "cached": cached,
            "running": running,
        }

    @staticmethod
    def _record_dict(record: UpdateRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "target": str(_value(record.target)),
            "channel": record.channel,
            "status": str(_value(record.status)),
            "phase": str(_value(record.phase)) if record.phase else None,
            "progress": record.progress,
            "bytes_total": record.bytes_total,
            "bytes_done": record.bytes_done,
            "from_version": record.from_version,
            "to_version": record.to_version,
            "error_code": record.error_code,
            "error_message": record.error_message,
            "created_at": _iso(record.created_at),
            "started_at": _iso(record.started_at),
            "finished_at": _iso(record.finished_at),
        }

    def _is_confirmed_current(
        self, target: UpdateTarget, state: dict[str, Any], options: Mapping[str, Any]
    ) -> bool:
        if target is UpdateTarget.CORE:
            return state.get("available") is False
        if target is UpdateTarget.RESOURCE:
            selected = ResourceChannel(options.get("channel", ResourceChannel.ALL))
            channels = self._channels(selected)
            rows = [state.get("channels", {}).get(item.value, {}) for item in channels]
            return bool(rows) and all(row.get("available") is False and not row.get("error") for row in rows)
        channel = str(options.get("channel", "Official"))
        row = state.get("channels", {}).get(channel, {})
        return row.get("available") is False and not row.get("error")

    # ---- progress, persistence, locking ----------------------------------

    @asynccontextmanager
    async def _workflow_progress(self, workflow: Any, update_id: str, target: UpdateTarget):
        callback = lambda event: self._emit_progress(update_id, target, event)
        had_progress = hasattr(workflow, "progress")
        previous = getattr(workflow, "progress", None)
        try:
            setattr(workflow, "progress", callback)
            yield
        finally:
            if had_progress:
                setattr(workflow, "progress", previous)
            else:
                try:
                    delattr(workflow, "progress")
                except AttributeError:
                    pass

    async def _emit_progress(
        self, update_id: str, target: UpdateTarget, event: Any
    ) -> None:
        details = _as_mapping(event)
        raw_phase = str(_value(details.get("phase", "checking"))).lower()
        phase_map = {
            "stopping_core": "waiting_idle",
            "extracting": "verifying",
            "installing": "applying",
            "starting_core": "restarting",
            "skipped": "done",
        }
        raw_phase = phase_map.get(raw_phase, raw_phase)
        try:
            phase = UpdatePhase(raw_phase)
        except ValueError:
            phase = UpdatePhase.CHECKING
        done = details.get("downloaded", details.get("bytes_done"))
        total = details.get("total", details.get("bytes_total"))
        percent = details.get("percent", details.get("progress"))
        if percent is None and isinstance(done, (int, float)) and isinstance(total, (int, float)) and total > 0:
            percent = float(done) * 100 / float(total)
        if phase is UpdatePhase.DONE:
            percent = 100.0
        try:
            percent = max(0.0, min(100.0, float(percent))) if percent is not None else None
        except (TypeError, ValueError):
            percent = None

        now = self._clock()
        state = self._progress_state.setdefault(
            update_id,
            {
                "ws_at": float("-inf"),
                "ws_percent": None,
                "ws_phase": None,
                "db_at": float("-inf"),
                "db_phase": None,
            },
        )
        state["phase"] = phase.value
        phase_changed = state["ws_phase"] != phase.value
        percent_changed = (
            percent is not None
            and (state["ws_percent"] is None or abs(percent - state["ws_percent"]) >= 1.0)
        )
        should_broadcast = phase_changed or percent_changed or now - state["ws_at"] >= _WS_INTERVAL_SECONDS
        if not should_broadcast:
            return

        payload = {
            "update_id": update_id,
            "target": target.value,
            "phase": phase.value,
            "percent": percent,
            "downloaded": done,
            "total": total,
            "speed": details.get("speed"),
            "eta": details.get("eta"),
            "message": details.get("message"),
            "error": details.get("error"),
        }
        try:
            await _call(self.broadcast, "update_progress", payload)
        except Exception:
            logger.exception("could not broadcast update progress")
        state["ws_at"] = now
        state["ws_phase"] = phase.value
        if percent is not None:
            state["ws_percent"] = percent

        if now - state["db_at"] >= _DB_INTERVAL_SECONDS:
            try:
                async with self.session_factory() as session:
                    await UpdateRepository(session).update_progress(
                        update_id,
                        phase=phase,
                        progress=int(percent) if percent is not None else None,
                        bytes_total=int(total) if isinstance(total, (int, float)) else None,
                        bytes_done=int(done) if isinstance(done, (int, float)) else None,
                    )
                    await session.commit()
                state["db_at"] = now
                state["db_phase"] = phase.value
            except Exception:
                logger.exception("could not persist update progress")

    async def _terminal(
        self,
        update_id: str,
        status: UpdateStatus,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        if status not in _TERMINAL_STATUSES:
            raise ValueError(f"not a terminal update status: {status}")
        async with self.session_factory() as session:
            repo = UpdateRepository(session)
            final_phase = (
                UpdatePhase.DONE
                if status in {UpdateStatus.SUCCESS, UpdateStatus.SKIPPED}
                else UpdatePhase.FAILED
                if status is UpdateStatus.FAILED
                else None
            )
            if final_phase is not None:
                await repo.update_progress(
                    update_id,
                    phase=final_phase,
                    progress=100 if final_phase is UpdatePhase.DONE else None,
                )
            await repo.mark_terminal(
                update_id,
                status,
                error_code=error_code,
                error_message=error_message,
            )
            await session.commit()

    async def _save_versions(
        self, update_id: str, target: UpdateTarget, state: dict[str, Any]
    ) -> None:
        current: Any = None
        latest: Any = None
        if target is UpdateTarget.CORE:
            current, latest = state.get("current"), state.get("latest")
        elif target is UpdateTarget.RESOURCE:
            channels = state.get("channels", {})
            row = channels.get("repo") or channels.get("ota") or {}
            current, latest = row.get("current"), row.get("latest")
        else:
            channels = state.get("channels", {})
            row = channels.get("Official") or channels.get("Bilibili") or {}
            current, latest = row.get("current"), row.get("latest")
        async with self.session_factory() as session:
            record = await UpdateRepository(session).get(update_id)
            if record is not None:
                record.from_version = str(current)[:64] if current is not None else None
                record.to_version = str(latest)[:64] if latest is not None else None
                await session.commit()

    async def _already_running_error(self) -> AppError:
        current = None
        if self._current_id:
            try:
                current = await self.get(self._current_id)
            except AppError:
                current = None
        return self._busy_error(current)

    def _busy_error(self, current: UpdateRecord | None) -> AppError:
        details = {}
        message = "已有更新任务正在执行"
        if current is not None:
            details = {
                "update_id": current.id,
                "target": str(_value(current.target)),
                "phase": str(_value(current.phase)) if current.phase else None,
            }
            message = f"已有 {current.target} 更新任务正在执行"
        return AppError(ErrorCode.UPDATE_ALREADY_RUNNING, message, details=details)

    async def _running_record(self) -> UpdateRecord | None:
        async with self.session_factory() as session:
            repo = UpdateRepository(session)
            for target in UpdateTarget:
                record = await repo.current(target)
                if record is not None:
                    return record
        return None

    async def _rollback_session(self) -> None:
        # The insert transaction belongs to the per-call session; its context
        # closes and rolls back automatically. This hook is kept for clarity.
        return None

    def _required_workflow(self, target: UpdateTarget) -> Any:
        workflow = {
            UpdateTarget.CORE: self.core_workflow,
            UpdateTarget.RESOURCE: self.resource_workflow,
            UpdateTarget.GAME: self.game_workflow,
        }[target]
        if workflow is None:
            raise RuntimeError(f"{target.value} update workflow is not configured")
        return workflow

    # ---- resource metadata ------------------------------------------------

    async def _resource_metadata(self) -> dict[str, ResourceAsset | None]:
        async with self.session_factory() as session:
            repo = ResourceAssetRepository(session)
            repo_row = await repo.get_by_kind_name(ResourceAssetKind.REPO_RESOURCE, "MaaResource")
            ota_row = await repo.get_by_kind_name(ResourceAssetKind.OTA_RESOURCE, "resource/tasks.json")
            return {
                ResourceAssetKind.REPO_RESOURCE.value: repo_row,
                ResourceAssetKind.OTA_RESOURCE.value: ota_row,
            }

    async def _persist_resource_checks(self, checks: Mapping[ResourceChannel, Any]) -> None:
        workflow = self.resource_workflow
        layers_root = Path(getattr(workflow, "layers_root", Path("resource") / "maa-layers"))
        now = utcnow()
        async with self.session_factory() as session:
            repo = ResourceAssetRepository(session)
            for channel, check in checks.items():
                if _field(check, "error"):
                    continue
                if channel is ResourceChannel.REPO:
                    kind, name = ResourceAssetKind.REPO_RESOURCE, "MaaResource"
                    path = self._relative_resource_path(layers_root / "repo")
                else:
                    kind, name = ResourceAssetKind.OTA_RESOURCE, "resource/tasks.json"
                    path = self._relative_resource_path(layers_root / "cache" / "tasks.json")
                values: dict[str, Any] = {"last_checked_at": now}
                if await repo.get_by_kind_name(kind, name) is None:
                    values["path"] = path
                await repo.upsert_by_kind_name(
                    kind,
                    name,
                    **values,
                )
            await session.commit()

    async def _persist_installed_resource(
        self, check: Any, *, actual_version: str | None = None
    ) -> None:
        channel = ResourceChannel(_value(_field(check, "channel")))
        workflow = self.resource_workflow
        layers_root = Path(getattr(workflow, "layers_root", Path("resource") / "maa-layers"))
        now = utcnow()
        async with self.session_factory() as session:
            repo = ResourceAssetRepository(session)
            if channel is ResourceChannel.REPO:
                await repo.upsert_by_kind_name(
                    ResourceAssetKind.REPO_RESOURCE,
                    "MaaResource",
                    path=self._relative_resource_path(layers_root / "repo"),
                    remote_version=actual_version or _field(check, "version"),
                    last_checked_at=now,
                )
            else:
                await repo.upsert_by_kind_name(
                    ResourceAssetKind.OTA_RESOURCE,
                    "resource/tasks.json",
                    path=self._relative_resource_path(layers_root / "cache" / "tasks.json"),
                    checksum=_field(check, "checksum"),
                    etag=_field(check, "etag"),
                    last_modified=_field(check, "last_modified"),
                    last_checked_at=now,
                )
            await session.commit()

    @staticmethod
    def _relative_resource_path(path: Path) -> str:
        # docs/04 paths are relative to the application resource/ directory.
        try:
            return path.resolve().relative_to(Path("resource").resolve()).as_posix()
        except ValueError:
            return Path(path.name).as_posix()

    # ---- durable retry metadata and cleanup -------------------------------

    @staticmethod
    def _serialize_staged(staged: Any) -> dict[str, Any]:
        archive_path = getattr(staged, "archive_path", None)
        return {
            "channel": str(_value(staged.channel)),
            "path": str(staged.path),
            "version": staged.version,
            "checksum": staged.checksum,
            "etag": staged.etag,
            "last_modified": staged.last_modified,
            "cleanup_path": str(staged.cleanup_path) if staged.cleanup_path else None,
            "archive_path": str(archive_path) if archive_path else None,
        }

    @staticmethod
    def _restore_staged(item: Mapping[str, Any]) -> Any | None:
        try:
            from maa_api.services.resource_update import StagedResource

            return StagedResource(
                channel=ResourceChannel(item["channel"]),
                path=Path(item["path"]),
                version=item.get("version"),
                checksum=item.get("checksum"),
                etag=item.get("etag"),
                last_modified=item.get("last_modified"),
                cleanup_path=Path(item["cleanup_path"]) if item.get("cleanup_path") else None,
                archive_path=Path(item["archive_path"]) if item.get("archive_path") else None,
            )
        except (KeyError, TypeError, ValueError):
            return None

    def _temp_paths(self, update_id: str, values: list[Any]) -> None:
        paths = []
        for value in values:
            candidate = value if isinstance(value, (Path, str)) else getattr(value, "cleanup_path", None)
            if candidate is None:
                candidate = getattr(value, "path", None)
            if candidate is not None:
                paths.append(str(candidate))
            archive = getattr(value, "archive_path", None)
            if archive is not None:
                paths.append(str(archive))
                paths.append(str(Path(archive).with_suffix(".json")))
        self._staged.setdefault(update_id, [])
        self._temp_files[update_id] = paths

    @property
    def _temp_files(self) -> dict[str, list[str]]:
        if not hasattr(self, "__temp_files"):
            self.__temp_files: dict[str, list[str]] = {}
        return self.__temp_files

    async def _cleanup_temporary_files(self, update_id: str) -> None:
        for raw_path in self._temp_files.pop(update_id, []):
            path = Path(raw_path)
            try:
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
                    path.with_suffix(path.suffix + ".part").unlink(missing_ok=True)
            except OSError:
                logger.warning("could not remove cancelled update temporary file %s", path)

    async def _prune_expired_caches(self, target: UpdateTarget) -> None:
        """Remove only this target's inactive retry cache older than seven days."""
        if await self._running_target_matches(target):
            return
        roots: list[tuple[Path, bool]] = []
        if target is UpdateTarget.CORE and self.core_workflow is not None:
            root = getattr(self.core_workflow, "temp_root", None)
            if root is not None:
                roots.append((Path(root), True))
        elif target is UpdateTarget.RESOURCE and self.resource_workflow is not None:
            root = getattr(self.resource_workflow, "temp_root", None)
            if root is not None:
                roots.append((Path(root), False))
        elif target is UpdateTarget.GAME:
            roots.append((self.temp_root, False))

        cutoff = time.time() - CORE_ARCHIVE_CACHE_TTL_SECONDS
        for root, version_dirs_only in roots:
            if not root.is_dir() or root.is_symlink():
                continue
            try:
                entries = list(root.iterdir())
            except OSError:
                continue
            for entry in entries:
                # Workflow staging directories are dot-prefixed and may contain
                # an active extraction. Never prune them opportunistically.
                if entry.name.startswith(".") or entry.is_symlink():
                    continue
                if version_dirs_only and not entry.is_dir():
                    continue
                try:
                    if entry.is_dir():
                        children = list(entry.iterdir())
                        if version_dirs_only and any(child.name.startswith(".stage-") for child in children):
                            continue
                    if self._latest_mtime(entry) >= cutoff:
                        continue
                    if entry.is_dir():
                        shutil.rmtree(entry)
                    else:
                        entry.unlink()
                except OSError:
                    logger.warning("could not prune expired update cache %s", entry)

    @staticmethod
    def _latest_mtime(path: Path) -> float:
        latest = path.stat().st_mtime
        if path.is_dir():
            for child in path.rglob("*"):
                try:
                    latest = max(latest, child.stat().st_mtime)
                except OSError:
                    continue
        return latest

    async def _running_target_matches(self, target: UpdateTarget) -> bool:
        if self._current_id is not None:
            try:
                current = await self.get(self._current_id)
                if current.status is UpdateStatus.RUNNING and current.target == target:
                    return True
            except AppError:
                pass
        async with self.session_factory() as session:
            return await UpdateRepository(session).current(target) is not None

    async def _store_reload_pending(self, pending: bool) -> None:
        runtime_token: Any = None
        if pending and self.resource_layers_loaded is not None:
            try:
                runtime_token = await _call(self.resource_layers_loaded)
            except Exception:
                logger.exception("could not identify active core for deferred resource marker")
        marker = str(runtime_token) if runtime_token else self._process_id
        async with self.session_factory() as session:
            repository = SettingRepository(session)
            await repository.set(
                "updates.resource_reload_pending", bool(pending), updated_by="system"
            )
            await repository.set(
                "updates.resource_reload_pending_process",
                marker if pending else "",
                updated_by="system",
            )
            await session.commit()

    async def _get_reload_pending(self) -> bool:
        async with self.session_factory() as session:
            repository = SettingRepository(session)
            value = await repository.get("updates.resource_reload_pending")
            pending_process = await repository.get(
                "updates.resource_reload_pending_process"
            )
        pending = bool(value)
        # A READY child with a different process/generation token has loaded the
        # deferred resource tree. A same-generation READY event does not.
        if pending and self.resource_layers_loaded is not None:
            try:
                runtime_token = await _call(self.resource_layers_loaded)
                if runtime_token and str(runtime_token) != str(pending_process or self._process_id):
                    pending = False
                    await self._store_reload_pending(False)
            except Exception:
                logger.exception("could not confirm resource layers were loaded")
        if self.resource_workflow is not None:
            setattr(self.resource_workflow, "reload_pending", pending)
        return pending

    async def _save_log(self, update_id: str, value: dict[str, Any]) -> None:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        async with self.session_factory() as session:
            record = await UpdateRepository(session).get(update_id)
            if record is not None:
                record.log = encoded[:64 * 1024]
                await session.commit()

    @staticmethod
    def _public_options(options: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: _value(value)
            for key, value in options.items()
            if not key.startswith("_") and isinstance(_value(value), (str, int, float, bool, type(None)))
        }

    @staticmethod
    def _record_channel(target: UpdateTarget, options: Mapping[str, Any]) -> str | None:
        if target is UpdateTarget.CORE:
            return str(options.get("channel", "stable"))
        if target is UpdateTarget.RESOURCE:
            return str(ResourceChannel(options.get("channel", ResourceChannel.ALL)).value)
        return None

    @staticmethod
    def _caller(caller: str) -> str:
        mapping = {
            "manual": "manual", "rest": "manual", "user": "manual",
            "agent": "agent", "internal": "agent",
            "scheduled": "scheduled", "schedule": "scheduled",
        }
        return mapping.get(str(caller).lower(), "manual")

    @staticmethod
    def _channels(selected: ResourceChannel) -> tuple[ResourceChannel, ...]:
        if selected is ResourceChannel.ALL:
            return (ResourceChannel.OTA, ResourceChannel.REPO)
        return (selected,)

    # ---- notification de-duplication ------------------------------------

    async def _load_notification_state(self) -> dict[str, str]:
        async with self.session_factory() as session:
            value = await SettingRepository(session).get(_NOTIFIED_VERSIONS_KEY)
        if not isinstance(value, dict):
            value = {}
        self._notification_state = {str(key): str(item) for key, item in value.items()}
        return dict(self._notification_state)

    async def _save_notification_state(self, state: dict[str, str]) -> None:
        async with self.session_factory() as session:
            await SettingRepository(session).set(
                _NOTIFIED_VERSIONS_KEY, state, updated_by="system"
            )
            await session.commit()
        self._notification_state = dict(state)

    async def _send_notification(self, payload: dict[str, Any]) -> None:
        if self.notify is None:
            return
        if callable(self.notify):
            await _call(self.notify, NotifyEvent.UPDATE_AVAILABLE, payload)
            return
        sender = getattr(self.notify, "send_event", None)
        if callable(sender):
            await _call(sender, NotifyEvent.UPDATE_AVAILABLE, payload)

    @staticmethod
    def _available_targets(states: Mapping[str, Any]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        core = states.get(UpdateTarget.CORE.value, {})
        if core.get("available") is True:
            result.append({
                "target": UpdateTarget.CORE.value,
                "current": core.get("current"),
                "latest": core.get("latest"),
            })
        resource = states.get(UpdateTarget.RESOURCE.value, {})
        for channel, info in resource.get("channels", {}).items():
            if info.get("available") is True:
                result.append({
                    "target": UpdateTarget.RESOURCE.value,
                    "channel": channel,
                    "current": info.get("current"),
                    "latest": info.get("latest"),
                })
        game = states.get(UpdateTarget.GAME.value, {})
        for channel, info in game.get("channels", {}).items():
            if info.get("available") is True:
                result.append({
                    "target": UpdateTarget.GAME.value,
                    "channel": channel,
                    "current": info.get("current"),
                    "latest": info.get("latest"),
                })
        return result

    @staticmethod
    def _notification_key(item: Mapping[str, Any]) -> str:
        return ":".join(str(item.get(key, "")) for key in ("target", "channel"))

    @staticmethod
    def _fingerprint(item: Mapping[str, Any]) -> str:
        return str(item.get("latest", ""))


__all__ = ["UpdateService"]

"""导航执行器与设备适配（M86 spec §4.2、§4.5.4、§4.5.5）。

:class:`Navigator` 把一次 ``navigate_to`` 拆成「识别 → 规划 → 执行 → 校验」的循环：
每一步执行后截图校验，走错了从实际所在界面重新规划，认不出画面就回主界面重来；
全程不超过 ``max_steps`` 步。:class:`AppNavDevice` 把每个写设备动作包进
``raw.run_atomic(context, "navigate_to", ...)``，因此一次导航只需要一次原子操作授权。

所有截图归档走 :func:`archive_screenshot`：成功加样本前先 ``commit``（工具处理器的
会话不替它提交），归档失败只记 warning，不阻断导航。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from maa_api.agent.nav.graph import (
    DUPLICATE_DISTANCE,
    EXEMPLAR_SOURCES,
    HOME_NODE_ID,
    NODE_ID_PATTERN,
    NavGraphService,
    PathStep,
)
from maa_api.agent.nav.recognizer import (
    LOCATE_MIN_CONFIDENCE,
    UNKNOWN_RETRY_MS,
    Recognition,
    Recognizer,
    _node_rois,
    distances,
    signature,
)
from maa_api.agent.registry import ToolContext
from maa_api.agent.tools import raw
from maa_api.db.models import AgentAudit, NavNode
from maa_api.domain.enums import ScreenshotBackend, ScreenshotTrigger
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.screenshot_archive import archive_screenshot

__all__ = [
    "LEARNABLE_TOOLS",
    "MAX_LEARNED_SEQUENCE",
    "AppNavDevice",
    "NavDevice",
    "NavResult",
    "Navigator",
    "Performed",
]

logger = logging.getLogger(__name__)

#: ``remember`` 一次最多把几步审计换成 sequence 动作（spec §4.5.5）。
MAX_LEARNED_SEQUENCE = 8
#: 能换成导航动作的审计工具名。
LEARNABLE_TOOLS = frozenset({"click", "swipe", "key_event"})
#: 回主界面时内核返回 True 的锚点样本来源。
ANCHOR_SOURCE = "anchor"

#: ``stage:1-7`` 这样的主线关卡代号；其余 ``stage:*`` 归到活动页。
_MAIN_STAGE_PATTERN = re.compile(r"\d{1,2}-\d{1,2}")


class NavDevice(Protocol):
    """导航设备（坐标都是设备像素）。"""

    async def screenshot(self) -> Image.Image: ...

    async def resolution(self) -> tuple[int, int]: ...

    async def tap(self, x: int, y: int) -> None: ...

    async def swipe(
        self, x1: int, y1: int, x2: int, y2: int, duration_ms: int
    ) -> None: ...

    async def key(self, key: str) -> None: ...

    async def home(self) -> bool: ...


class AppNavDevice:
    """包装 ``DeviceManager``：写设备动作都走 ``run_atomic``，截图走 ADB。"""

    def __init__(self, context: ToolContext) -> None:
        self._context = context
        self._resolution: tuple[int, int] | None = None

    def _manager(self) -> Any:
        return raw._device_manager(self._context)

    async def screenshot(self) -> Image.Image:
        result = await self._manager().screenshot(backend="adb")
        return result.image

    async def resolution(self) -> tuple[int, int]:
        if self._resolution is not None:
            return self._resolution
        snapshot = self._manager().snapshot()
        resolution = snapshot.get("resolution") if isinstance(snapshot, dict) else None
        if isinstance(resolution, Mapping):
            width = resolution.get("width")
            height = resolution.get("height")
            if width and height:
                try:
                    self._resolution = (int(width), int(height))
                    return self._resolution
                except (TypeError, ValueError):
                    pass
        # 内核还没上报分辨率时用截图尺寸兜底（16:9 设备才被支持，spec §2）。
        image = await self.screenshot()
        self._resolution = (int(image.width), int(image.height))
        return self._resolution

    async def tap(self, x: int, y: int) -> None:
        await raw.run_atomic(
            self._context, "navigate_to", lambda manager: manager.click(x, y)
        )

    async def swipe(
        self, x1: int, y1: int, x2: int, y2: int, duration_ms: int
    ) -> None:
        await raw.run_atomic(
            self._context,
            "navigate_to",
            lambda manager: manager.swipe(x1, y1, x2, y2, duration_ms),
        )

    async def key(self, key: str) -> None:
        await raw.run_atomic(
            self._context, "navigate_to", lambda manager: manager.key_event(key)
        )

    async def home(self) -> bool:
        return bool(
            await raw.run_atomic(
                self._context, "navigate_to", lambda manager: manager.back_to_home()
            )
        )


@dataclass(slots=True)
class Performed:
    """一步动作的执行结果。"""

    ok: bool
    #: ``locate`` 成功后的归一化点击动作；其余动作为 ``None``。
    tap_action: dict[str, Any] | None = None
    #: ``locate`` 定位用的图、``home`` 成功后的图；其余为 ``None``。
    screenshot: Image.Image | None = None
    error: str | None = None


@dataclass(slots=True)
class NavResult:
    """一次 ``navigate`` 的结果。"""

    arrived: bool
    node_id: str
    steps: int
    elapsed_ms: int
    trail: list[dict[str, Any]]
    learned_edges: int
    learned_exemplars: int


@dataclass(slots=True)
class _RunState:
    """``navigate`` 的累计量（步数、trail、学习计数）。"""

    steps: int = 0
    trail: list[dict[str, Any]] = field(default_factory=list)
    learned_edges: int = 0
    learned_exemplars: int = 0


def _trail_item(
    step: PathStep, *, actual: str | None, ok: bool, ms: int
) -> dict[str, Any]:
    """trail 固定七个键：``edge_id/action/from/expected/actual/ok/ms``。"""
    return {
        "edge_id": step.edge_id,
        "action": str(step.action.get("type", "tap")),
        "from": step.from_node,
        "expected": step.to_node,
        "actual": actual,
        "ok": ok,
        "ms": int(ms),
    }


async def _sleep_ms(milliseconds: int) -> None:
    """等待动作稳定；测试替换成空实现。"""
    await asyncio.sleep(max(0, int(milliseconds)) / 1000)


class Navigator:
    """一次导航的执行器；显式传入 ``learn``/``max_steps``/会话与库会话。"""

    def __init__(
        self,
        graph: NavGraphService,
        recognizer: Recognizer,
        device: NavDevice,
        *,
        learn: bool,
        max_steps: int,
        db_session: AsyncSession | None = None,
        session_id: str | None = None,
    ) -> None:
        self._graph = graph
        self._recognizer = recognizer
        self._device = device
        self._learn = bool(learn)
        self._max_steps = max(1, int(max_steps))
        self._db_session = db_session
        self._session_id = session_id

    # ------------------------------------------------------------------
    # 识别与动作
    # ------------------------------------------------------------------
    async def where_am_i(self) -> tuple[Recognition, Image.Image]:
        """截一张图并识别；认出节点后写 ``last_seen``。"""
        image = await self._device.screenshot()
        recognition = await self._recognizer.recognize(image)
        await self._graph.note_seen(self._session_id, recognition.node_id)
        return recognition, image

    async def perform(self, step: PathStep) -> Performed:
        """执行一步动作；不写图、不记失败，失败计数由 ``navigate`` 处理。"""
        return await self._perform_action(step.action)

    async def _perform_action(self, action: Mapping[str, Any]) -> Performed:
        kind = action.get("type")
        if kind == "tap":
            return await self._perform_tap(action)
        if kind == "swipe":
            return await self._perform_swipe(action)
        if kind == "key":
            return await self._perform_key(action)
        if kind == "sequence":
            steps = action.get("steps") or []
            for step in steps:
                if not isinstance(step, Mapping):
                    return Performed(False, error="sequence")
                result = await self._perform_action(step)
                if not result.ok:
                    return result
            return Performed(True)
        if kind == "locate":
            return await self._perform_locate(action)
        if kind == "home":
            return await self._perform_home()
        return Performed(False, error="unsupported_action")

    async def _perform_tap(self, action: Mapping[str, Any]) -> Performed:
        try:
            width, height = await self._device.resolution()
            await self._device.tap(
                round(float(action.get("x", 0.0)) * width),
                round(float(action.get("y", 0.0)) * height),
            )
        except AppError:
            raise
        except Exception as exc:  # noqa: BLE001 - 设备动作失败算这一步失败
            return Performed(False, error=str(exc) or "tap")
        await _sleep_ms(int(action.get("wait_ms", 1200)))
        return Performed(True)

    async def _perform_swipe(self, action: Mapping[str, Any]) -> Performed:
        try:
            width, height = await self._device.resolution()
            await self._device.swipe(
                round(float(action.get("x1", 0.0)) * width),
                round(float(action.get("y1", 0.0)) * height),
                round(float(action.get("x2", 0.0)) * width),
                round(float(action.get("y2", 0.0)) * height),
                int(action.get("duration_ms", 400)),
            )
        except AppError:
            raise
        except Exception as exc:  # noqa: BLE001
            return Performed(False, error=str(exc) or "swipe")
        await _sleep_ms(int(action.get("wait_ms", 1000)))
        return Performed(True)

    async def _perform_key(self, action: Mapping[str, Any]) -> Performed:
        try:
            await self._device.key(str(action.get("key", "BACK")))
        except AppError:
            raise
        except Exception as exc:  # noqa: BLE001
            return Performed(False, error=str(exc) or "key")
        await _sleep_ms(int(action.get("wait_ms", 1200)))
        return Performed(True)

    async def _perform_locate(self, action: Mapping[str, Any]) -> Performed:
        image = await self._device.screenshot()
        vision = self._vision_client()
        if vision is None:
            return Performed(False, screenshot=image, error="locate")
        try:
            located = await vision.locate(image, str(action.get("query", "")))
        except Exception:  # noqa: BLE001 - 视觉失败只当这一步没走通
            logger.warning("视觉定位失败，按 locate 失败处理", exc_info=True)
            return Performed(False, screenshot=image, error="locate")
        if located is None:
            return Performed(False, screenshot=image, error="locate")
        x, y, confidence = located
        if float(confidence) < LOCATE_MIN_CONFIDENCE:
            return Performed(False, screenshot=image, error="locate")
        width, height = await self._device.resolution()
        await self._device.tap(round(float(x) * width), round(float(y) * height))
        await _sleep_ms(int(action.get("wait_ms", 1200)))
        return Performed(
            True, tap_action={"x": float(x), "y": float(y)}, screenshot=image
        )

    async def _perform_home(self) -> Performed:
        if not await self._device.home():
            return Performed(False, error="home")
        image = await self._device.screenshot()
        return Performed(True, screenshot=image)

    # ------------------------------------------------------------------
    # 导航主循环
    # ------------------------------------------------------------------
    async def navigate(self, target: str) -> NavResult:
        started = time.monotonic()
        nodes, _edges = await self._graph.graph()
        if target not in {node.id for node in nodes}:
            raise AppError(
                ErrorCode.NAV_NODE_NOT_FOUND,
                f"导航图里没有界面 {target}",
                {"target": target, "nearest": await self._nearest_container(target)},
            )

        state = _RunState()
        failed_edges: set[str] = set()
        recognition, image = await self.where_am_i()
        if self._learn and recognition.method == "vision" and recognition.node_id:
            added, _screenshot_id = await self._add_exemplar_from_image(
                recognition.node_id, image, source="agent"
            )
            state.learned_exemplars += added
        current = recognition.node_id
        if current is None:
            current = await self._recover_home(state, current=None)

        while current != target:
            if state.steps >= self._max_steps:
                raise self._step_failed("max_steps", current, state.trail)
            vision_ok = await self._vision_available()
            plan = await self._graph.shortest_path(
                current, target, vision=vision_ok, exclude=failed_edges
            )
            if plan is None:
                raise AppError(
                    ErrorCode.NAV_PATH_NOT_FOUND,
                    f"从 {current} 到 {target} 没有可走的路径",
                    {"from": current, "to": target, "reason": "no_edges"},
                )
            if plan.blocked_by_vision:
                raise AppError(
                    ErrorCode.NAV_PATH_NOT_FOUND,
                    "当前没有可用的视觉模型，走不了按描述定位的跳转",
                    {"from": current, "to": target, "reason": "needs_vision"},
                )
            step = plan.steps[0]
            t0 = time.monotonic()
            performed = await self.perform(step)
            state.steps += 1
            if not performed.ok:
                if step.edge_id is not None:
                    await self._graph.record_failure(step.edge_id)
                    failed_edges.add(step.edge_id)
                state.trail.append(
                    _trail_item(
                        step,
                        actual=None,
                        ok=False,
                        ms=int((time.monotonic() - t0) * 1000),
                    )
                )
                continue

            recognition, image = await self.where_am_i()
            if recognition.node_id is None:
                await _sleep_ms(UNKNOWN_RETRY_MS)
                recognition, image = await self.where_am_i()
            if self._learn and recognition.method == "vision" and recognition.node_id:
                added, _screenshot_id = await self._add_exemplar_from_image(
                    recognition.node_id, image, source="agent"
                )
                state.learned_exemplars += added
            elapsed = int((time.monotonic() - t0) * 1000)

            if recognition.node_id == step.to_node:
                if step.edge_id is not None:
                    await self._graph.record_success(
                        step.edge_id, elapsed_ms=elapsed
                    )
                # 自动学样本只在 nav.learn=true 时发生（spec §4.5.2）。
                if self._learn:
                    added, _screenshot_id = await self._add_exemplar_from_image(
                        step.to_node, image, source="edge"
                    )
                    state.learned_exemplars += added
                await self._learn_locate_tap(
                    step, performed, state, elapsed_ms=elapsed
                )
                state.trail.append(
                    _trail_item(
                        step, actual=step.to_node, ok=True, ms=elapsed
                    )
                )
                current = step.to_node
                continue

            if await self._first_arrival_trust(step, recognition):
                if step.edge_id is not None:
                    await self._graph.record_success(
                        step.edge_id, elapsed_ms=elapsed
                    )
                added, _screenshot_id = await self._add_exemplar_from_image(
                    step.to_node, image, source="edge"
                )
                state.learned_exemplars += added
                # 首达信任也是一次「走通」：locate 跳转同样学成 tap（spec §4.5.6）。
                await self._learn_locate_tap(
                    step, performed, state, elapsed_ms=elapsed
                )
                state.trail.append(
                    _trail_item(step, actual=step.to_node, ok=True, ms=elapsed)
                )
                current = step.to_node
                continue

            if step.edge_id is not None:
                await self._graph.record_failure(step.edge_id)
                failed_edges.add(step.edge_id)
            state.trail.append(
                _trail_item(step, actual=recognition.node_id, ok=False, ms=elapsed)
            )
            if recognition.node_id is not None:
                if (
                    self._learn
                    and recognition.node_id != current
                    and str(step.action.get("type"))
                    in {"tap", "swipe", "key", "sequence"}
                ):
                    learned, created = await self._graph.upsert_edge(
                        from_node=current,
                        to_node=recognition.node_id,
                        action=step.action,
                        origin="learned",
                    )
                    state.learned_edges += int(created)
                    if not created:
                        # 同 action_hash 已存在时只记一次成功，不新建（spec §4.5.4）。
                        await self._graph.record_success(
                            learned.id, elapsed_ms=elapsed
                        )
                current = recognition.node_id
                continue

            if state.steps >= self._max_steps:
                screenshot_id = await self._archive(image)
                raise self._step_failed(
                    "unknown_screen",
                    current,
                    state.trail,
                    screenshot_id=screenshot_id,
                )
            current = await self._recover_home(state, current=current)

        return NavResult(
            arrived=True,
            node_id=target,
            steps=state.steps,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            trail=state.trail,
            learned_edges=state.learned_edges,
            learned_exemplars=state.learned_exemplars,
        )

    async def _recover_home(
        self, state: _RunState, *, current: str | None
    ) -> str:
        """回主界面（计一步）；失败抛 ``NAV_STEP_FAILED(home_failed)``。"""
        if state.steps >= self._max_steps:
            raise self._step_failed("max_steps", current, state.trail)
        t0 = time.monotonic()
        ok = await self._device.home()
        state.steps += 1
        if not ok:
            raise self._step_failed("home_failed", current, state.trail)
        if self._learn:
            image = await self._device.screenshot()
            added, _screenshot_id = await self._add_exemplar_from_image(
                HOME_NODE_ID, image, source=ANCHOR_SOURCE
            )
            state.learned_exemplars += added
        state.trail.append(
            {
                "edge_id": None,
                "action": "home",
                "from": current,
                "expected": HOME_NODE_ID,
                "actual": HOME_NODE_ID,
                "ok": True,
                "ms": int((time.monotonic() - t0) * 1000),
            }
        )
        return HOME_NODE_ID

    async def _learn_locate_tap(
        self,
        step: PathStep,
        performed: Performed,
        state: _RunState,
        *,
        elapsed_ms: int,
    ) -> None:
        """``locate`` 走通后学成一条 tap 跳转（spec §4.5.6）。"""
        if not self._learn or str(step.action.get("type")) != "locate":
            return
        if performed.tap_action is None:
            return
        learned, created = await self._graph.upsert_edge(
            from_node=step.from_node,
            to_node=step.to_node,
            action={"type": "tap", **performed.tap_action},
            origin="learned",
        )
        state.learned_edges += int(created)
        if not created:
            # 学到的 tap 已存在时只记一次成功，不新建（spec §4.5.4）。
            await self._graph.record_success(learned.id, elapsed_ms=elapsed_ms)

    async def _first_arrival_trust(
        self, step: PathStep, recognition: Recognition
    ) -> bool:
        """首达信任的五个条件（spec §4.5.4），全部满足才把「没认出」当成到达。"""
        if not self._learn:
            return False
        if step.origin != "seed" or step.edge_id is None:
            return False
        if str(step.action.get("type")) == "home":
            return False
        if recognition.node_id is not None:
            return False
        node = await self._graph.get_node(step.to_node)
        return node is not None and not (node.exemplars or [])

    async def _nearest_container(self, target: str) -> str:
        """给 ``NAV_NODE_NOT_FOUND`` 的建议落点（spec §4.5.4）。"""
        if not target.startswith("stage:"):
            return HOME_NODE_ID
        code = target.split(":", 1)[1]
        container = "main_episode" if _MAIN_STAGE_PATTERN.fullmatch(code) else "activity_hub"
        nodes, _edges = await self._graph.graph()
        node_ids = {node.id for node in nodes}
        return container if container in node_ids else HOME_NODE_ID

    # ------------------------------------------------------------------
    # 学习
    # ------------------------------------------------------------------
    async def _add_exemplar_from_image(
        self, node_id: str, image: Image.Image, *, source: str
    ) -> tuple[int, str | None]:
        """算签名、去重、归档后加样本；返回 ``(是否新增, screenshot_id)``。"""
        if source not in EXEMPLAR_SOURCES:
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                f"未知的样本来源：{source}",
                {"field": "source"},
            )
        node = await self._graph.get_node(node_id)
        if node is None:
            return 0, None
        phash = signature(image, _node_rois(node))
        if self._is_duplicate(node, phash):
            return 0, None
        screenshot_id = await self._archive(image)
        added = await self._graph.add_exemplar(
            node_id, phash=phash, screenshot_id=screenshot_id, source=source
        )
        return int(added), screenshot_id

    @staticmethod
    def _is_duplicate(node: NavNode, phash: str) -> bool:
        for item in node.exemplars or []:
            existing = item.get("phash") if isinstance(item, Mapping) else None
            if not isinstance(existing, str) or not existing:
                continue
            distance = distances(phash, existing)
            if distance is not None and all(
                value <= DUPLICATE_DISTANCE for value in distance
            ):
                return True
        return False

    async def _archive(self, image: Image.Image) -> str | None:
        """归档截图并立即提交；失败只记 warning 并返回 ``None``。"""
        if self._db_session is None:
            return None
        try:
            _row, stored = await archive_screenshot(
                self._db_session,
                image,
                trigger=ScreenshotTrigger.AGENT,
                backend=ScreenshotBackend.ADB,
            )
            await self._db_session.commit()
            return str(stored["screenshot_id"])
        except Exception:  # noqa: BLE001 - 归档失败不能阻断导航（spec §4.5.2）
            logger.warning("归档导航截图失败，样本不带截图", exc_info=True)
            try:
                await self._db_session.rollback()
            except Exception:  # noqa: BLE001 - 回滚失败不再掩盖原错误
                logger.warning("回滚截图归档事务失败", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # remember_screen
    # ------------------------------------------------------------------
    async def remember(
        self, *, node_id: str, title: str, kind: str | None
    ) -> dict[str, Any]:
        """把当前画面记成节点；从 ``last_seen`` 与审计学一条进入它的跳转。"""
        if NODE_ID_PATTERN.fullmatch(node_id) is None:
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                "界面 id 不合法",
                {"field": "node_id"},
            )
        if not title or not title.strip():
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                "界面标题不能为空",
                {"field": "title"},
            )
        if node_id.startswith("stage:"):
            if kind is not None and kind != "stage":
                raise AppError(
                    ErrorCode.INVALID_PARAMETER,
                    "stage: 界面只能是 stage 类型",
                    {"field": "kind"},
                )
            resolved_kind = "stage"
        else:
            resolved_kind = kind or "screen"

        image = await self._device.screenshot()
        recognition = await self._recognizer.recognize(image, allow_vision=False)
        if (
            recognition.method == "hash"
            and recognition.node_id is not None
            and recognition.node_id != node_id
        ):
            return {
                "node": None,
                "exemplar_added": False,
                "screenshot_id": None,
                "edge": None,
                "skipped_reason": f"screen_is_{recognition.node_id}",
            }

        existing = await self._graph.get_node(node_id)
        created = existing is None
        if created:
            await self._graph.upsert_node(
                node_id,
                title=title,
                kind=resolved_kind,
                origin="learned",
            )
        added, screenshot_id = await self._add_exemplar_from_image(
            node_id, image, source="agent"
        )
        # 样本重复不拦学边（spec §4.3.1「样本照加，只有学边被跳过」）。
        edge_payload, edge_skip = await self._learn_edge_from_audit(node_id)
        skipped_reason = edge_skip or ("duplicate_exemplar" if not added else None)
        node = await self._graph.get_node(node_id)
        await self._graph.note_seen(self._session_id, node_id)
        return {
            "node": {
                "id": node_id,
                "title": node.title if node is not None else title,
                "kind": node.kind if node is not None else resolved_kind,
                "created": created,
            },
            "exemplar_added": bool(added),
            "screenshot_id": screenshot_id,
            "edge": edge_payload,
            "skipped_reason": skipped_reason,
        }

    async def _learn_edge_from_audit(
        self, node_id: str
    ) -> tuple[dict[str, Any] | None, str | None]:
        """把上一次认出节点后的审计动作换成一条进入 ``node_id`` 的跳转。"""
        if self._session_id is None or self._db_session is None:
            return None, "no_session"
        last_seen = self._graph.last_seen.get(self._session_id)
        if last_seen is None:
            return None, "no_last_seen"
        previous, cursor = last_seen
        rows = list(
            (
                await self._db_session.execute(
                    select(AgentAudit)
                    .where(
                        AgentAudit.session_id == self._session_id,
                        AgentAudit.id > cursor,
                        AgentAudit.status == "success",
                    )
                    .order_by(AgentAudit.id)
                )
            ).scalars()
        )
        learnable = [row for row in rows if row.tool_name in LEARNABLE_TOOLS]
        if not learnable:
            return None, "no_audit"
        if len(learnable) > MAX_LEARNED_SEQUENCE:
            return None, "too_many_steps"
        if any(row.tool_name in {"long_press", "input_text"} for row in rows):
            return None, "unsupported_action"
        if previous == node_id:
            return None, "same_node"
        actions, problem = await self._audit_actions(learnable)
        if problem is not None:
            return None, problem
        action: dict[str, Any] = (
            actions[0] if len(actions) == 1 else {"type": "sequence", "steps": actions}
        )
        edge, created = await self._graph.upsert_edge(
            from_node=previous,
            to_node=node_id,
            action=action,
            origin="learned",
        )
        return (
            {
                "id": edge.id,
                "from": previous,
                "action": str(action["type"]),
                "created": created,
            },
            None,
        )

    async def _audit_actions(
        self, rows: Sequence[AgentAudit]
    ) -> tuple[list[dict[str, Any]], str | None]:
        width, height = await self._device.resolution()
        actions: list[dict[str, Any]] = []
        for row in rows:
            arguments = row.arguments if isinstance(row.arguments, Mapping) else {}
            try:
                if row.tool_name == "click":
                    actions.append(
                        {
                            "type": "tap",
                            "x": round(float(arguments["x"]) / width, 4),
                            "y": round(float(arguments["y"]) / height, 4),
                        }
                    )
                elif row.tool_name == "swipe":
                    actions.append(
                        {
                            "type": "swipe",
                            "x1": round(float(arguments["x1"]) / width, 4),
                            "y1": round(float(arguments["y1"]) / height, 4),
                            "x2": round(float(arguments["x2"]) / width, 4),
                            "y2": round(float(arguments["y2"]) / height, 4),
                            "duration_ms": int(arguments.get("duration_ms", 300)),
                        }
                    )
                elif row.tool_name == "key_event":
                    if str(arguments.get("key", "")).upper() != "BACK":
                        return [], "unsupported_action"
                    actions.append({"type": "key", "key": "BACK"})
                else:  # pragma: no cover - learnable 已过滤
                    return [], "unsupported_action"
            except (KeyError, TypeError, ValueError):
                return [], "unsupported_action"
        return actions, None

    # ------------------------------------------------------------------
    # 小工具
    # ------------------------------------------------------------------
    def _vision_client(self) -> Any:
        """识别器持有视觉客户端；``Navigator`` 的签名不重复收它。"""
        return getattr(self._recognizer, "_vision", None)

    async def _vision_available(self) -> bool:
        client = self._vision_client()
        if client is None:
            return False
        try:
            return bool(await client.available())
        except Exception:  # noqa: BLE001 - 视觉不可用只影响可走的路
            logger.warning("视觉可用性检查失败，按不可用处理", exc_info=True)
            return False

    def _step_failed(
        self,
        reason: str,
        current: str | None,
        trail: list[dict[str, Any]],
        *,
        screenshot_id: str | None = None,
    ) -> AppError:
        return AppError(
            ErrorCode.NAV_STEP_FAILED,
            "导航未能到达目标界面",
            {
                "reason": reason,
                "current": current,
                "trail": trail,
                "screenshot_id": screenshot_id,
            },
        )

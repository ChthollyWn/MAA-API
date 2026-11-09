"""界面导航图服务（M86 spec §4.2、§4.5.3）：动作模型、图的读写、最短路径与记账。

一句话：把「哪个界面、什么动作能到哪个界面」存成图，给导航执行器一条 Dijkstra
最短路径；成功的跳转累计耗时，失败的跳转累计失败次数，达到阈值自动停用。

本模块**不 import navigator**（避免 ``nav`` 包内循环依赖）：``make_device`` 在
``device_factory`` 为空时才懒导入 ``AppNavDevice``；测试与冒烟通过替换
``service.device_factory`` 注入假设备。
"""

from __future__ import annotations

import hashlib
import heapq
import json
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.agent.nav.seed import SEED_EDGES, SEED_NODES
from maa_api.db.models import AgentAudit, NavEdge, NavNode, new_uuid, utcnow
from maa_api.domain.errors import AppError, ErrorCode

__all__ = [
    "ALLOWED_KEYS",
    "DEFAULT_COST_MS",
    "DISABLE_AFTER_FAILS",
    "DUPLICATE_DISTANCE",
    "EXEMPLAR_SOURCES",
    "FAIL_PENALTY_MS",
    "HOME_EDGE_COST_MS",
    "HOME_NODE_ID",
    "MAX_EXEMPLARS",
    "MAX_SEQUENCE_STEPS",
    "NODE_ID_PATTERN",
    "DeviceFactory",
    "KeyAction",
    "LastSeenStore",
    "LocateAction",
    "NavAction",
    "NavGraphService",
    "PathPlan",
    "PathStep",
    "SequenceAction",
    "SwipeAction",
    "TapAction",
    "action_hash",
    "validate_action",
]

logger = logging.getLogger(__name__)

#: 节点 id：小写短 slug（home、operator_list）或关卡代号（stage:1-7）。
NODE_ID_PATTERN = re.compile(
    r"^(?:[a-z][a-z0-9_]{1,47}|stage:[A-Za-z0-9][A-Za-z0-9_-]{0,41})$"
)
#: 主界面节点 id；隐式「回主界面」边的终点（spec §4.5.3）。
HOME_NODE_ID = "home"
#: 导航允许发送的按键（spec §2：不点消耗资源的按钮，按键只允许返回）。
ALLOWED_KEYS = frozenset({"BACK"})
#: 一条 sequence 动作最多几步（spec §4.2）。
MAX_SEQUENCE_STEPS = 8
#: 失败达到这个次数、且不少于成功次数时自动停用（spec §4.5.4）。
DISABLE_AFTER_FAILS = 3
#: 每次失败的代价惩罚（毫秒），让 Dijkstra 绕开不稳的跳转。
FAIL_PENALTY_MS = 2000
#: 没有实测 ``cost_ms`` 时按动作类型的默认耗时；序列取各步之和。
DEFAULT_COST_MS: dict[str, int] = {
    "tap": 1500,
    "swipe": 1500,
    "key": 1500,
    "locate": 6500,
}
#: 隐式「回主界面」边的固定代价；这条边不落库（spec §4.5.3）。
HOME_EDGE_COST_MS = 6000
#: 样本来源（spec §4.5.2）：agent 视觉认定、edge 一步校验通过、user 手动标记、anchor 回首页。
EXEMPLAR_SOURCES = frozenset({"agent", "edge", "user", "anchor"})
#: 每个节点保留的样本上限。
MAX_EXEMPLARS = 8
#: 与已有样本每个 ROI 距离都 ≤ 它就算重复（spec §4.5.2）。
DUPLICATE_DISTANCE = 3

Ratio = Annotated[float, Field(ge=0, le=1)]
WaitMs = Annotated[int, Field(ge=0, le=10000)]


class TapAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["tap"]
    x: Ratio
    y: Ratio
    wait_ms: WaitMs = 1200


class SwipeAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["swipe"]
    x1: Ratio
    y1: Ratio
    x2: Ratio
    y2: Ratio
    duration_ms: int = Field(default=400, ge=100, le=3000)
    wait_ms: WaitMs = 1000


class KeyAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["key"]
    key: Literal["BACK"]
    wait_ms: WaitMs = 1200


class LocateAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["locate"]
    query: str = Field(min_length=2, max_length=120)
    wait_ms: WaitMs = 1200


class SequenceAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["sequence"]
    steps: list[TapAction | SwipeAction | KeyAction] = Field(
        min_length=1, max_length=MAX_SEQUENCE_STEPS
    )


NavAction: TypeAlias = Annotated[
    TapAction | SwipeAction | KeyAction | LocateAction | SequenceAction,
    Field(discriminator="type"),
]

_ACTION_ADAPTER: TypeAdapter[NavAction] = TypeAdapter(NavAction)
#: 参与规范化取整的动作键；其余数值（duration_ms、wait_ms）原样或剔除。
_COORDINATE_KEYS = frozenset({"x", "y", "x1", "y1", "x2", "y2"})


def _canonical(value: Any) -> Any:
    """递归去 ``wait_ms``，坐标保留 3 位小数（§4.2 的规范 JSON）。"""
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key in sorted(value):
            if key == "wait_ms":
                continue
            item = value[key]
            if (
                key in _COORDINATE_KEYS
                and isinstance(item, (int, float))
                and not isinstance(item, bool)
            ):
                item = round(float(item), 3)
            result[key] = _canonical(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def action_hash(action: NavAction) -> str:
    """规范 JSON 的 sha256：键排序、坐标保留 3 位小数、递归去掉 ``wait_ms``。"""
    payload = action.model_dump(mode="json")
    canonical = json.dumps(
        _canonical(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_action(payload: Mapping[str, Any]) -> NavAction:
    """按 :data:`NavAction` 校验并返回动作模型；不合法抛 ``INVALID_PARAMETER``。"""
    try:
        return _ACTION_ADAPTER.validate_python(payload)
    except ValidationError as exc:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            "导航动作不合法",
            {"field": "action"},
        ) from exc


@dataclass(frozen=True, slots=True)
class PathStep:
    """最短路径上的一步；隐式回主界面边的 ``edge_id`` 为 ``None``。"""

    edge_id: str | None
    from_node: str
    to_node: str
    action: dict[str, Any]
    origin: str


@dataclass(frozen=True, slots=True)
class PathPlan:
    """一次规划结果；``blocked_by_vision`` 表示只有 ``locate`` 边可走。"""

    steps: tuple[PathStep, ...]
    blocked_by_vision: bool


#: 入参 ``ToolContext``，返回 ``NavDevice``；graph.py 不 import navigator。
DeviceFactory: TypeAlias = Callable[[Any], Any]


class LastSeenStore:
    """进程内：session_id → (node_id, audit_cursor)。重启后丢失，只影响一次学习。"""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[str, int]] = {}

    def put(self, session_id: str, node_id: str, audit_cursor: int) -> None:
        self._entries[session_id] = (node_id, int(audit_cursor))

    def get(self, session_id: str) -> tuple[str, int] | None:
        return self._entries.get(session_id)


def _default_cost(action: Mapping[str, Any]) -> int:
    """动作的默认耗时；序列取各步默认值之和（spec §4.5.3）。"""
    kind = action.get("type")
    if kind == "sequence":
        steps = action.get("steps") or []
        return sum(
            DEFAULT_COST_MS.get(str(step.get("type")), 1500)
            for step in steps
            if isinstance(step, Mapping)
        ) or DEFAULT_COST_MS["tap"]
    return DEFAULT_COST_MS.get(str(kind), 1500)


def _contains_locate(action: Mapping[str, Any]) -> bool:
    if action.get("type") == "locate":
        return True
    steps = action.get("steps") or []
    return any(
        isinstance(step, Mapping) and step.get("type") == "locate" for step in steps
    )


def _edge_cost(edge: NavEdge) -> int:
    base = edge.cost_ms if edge.cost_ms is not None else _default_cost(edge.action)
    return int(base) + FAIL_PENALTY_MS * int(edge.fail_count)


def _dijkstra(
    nodes: list[NavNode],
    edges: list[NavEdge],
    source: str,
    target: str,
    *,
    allow_locate: bool,
    exclude: set[str],
) -> tuple[PathStep, ...] | None:
    """从 ``source`` 到 ``target`` 的最小代价路径；无边时返回 ``None``。"""
    adjacency: dict[str, list[tuple[str, int, PathStep]]] = {}

    def connect(node_id: str, step: PathStep, cost: int) -> None:
        adjacency.setdefault(node_id, []).append((step.to_node, cost, step))

    for edge in edges:
        if not edge.enabled or edge.id in exclude:
            continue
        if not allow_locate and _contains_locate(edge.action):
            continue
        connect(
            edge.from_node,
            PathStep(
                edge_id=edge.id,
                from_node=edge.from_node,
                to_node=edge.to_node,
                action=dict(edge.action),
                origin=edge.origin,
            ),
            _edge_cost(edge),
        )

    for node in nodes:
        if node.id == HOME_NODE_ID:
            continue
        connect(
            node.id,
            PathStep(
                edge_id=None,
                from_node=node.id,
                to_node=HOME_NODE_ID,
                action={"type": "home"},
                origin="implicit",
            ),
            HOME_EDGE_COST_MS,
        )

    if source == target:
        return ()

    distances: dict[str, int] = {source: 0}
    previous: dict[str, tuple[str, PathStep]] = {}
    queue: list[tuple[int, int, str]] = [(0, 0, source)]
    order = 1
    while queue:
        cost, _sequence, node_id = heapq.heappop(queue)
        if node_id in distances and cost > distances[node_id]:
            continue
        if node_id == target:
            break
        for to_node, step_cost, step in adjacency.get(node_id, ()):
            candidate = cost + step_cost
            if to_node in distances and candidate >= distances[to_node]:
                continue
            distances[to_node] = candidate
            previous[to_node] = (node_id, step)
            heapq.heappush(queue, (candidate, order, to_node))
            order += 1

    if target not in distances:
        return None
    steps: list[PathStep] = []
    cursor = target
    while cursor != source:
        parent, step = previous[cursor]
        steps.append(step)
        cursor = parent
    steps.reverse()
    return tuple(steps)


class NavGraphService:
    """导航图的读写、种子、最短路径与 ``last_seen`` 游标。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        device_factory: DeviceFactory | None = None,
    ) -> None:
        self._session_factory = session_factory
        #: 测试/冒烟注入点：入参 ``ToolContext``，返回 ``NavDevice``。
        self.device_factory = device_factory
        #: session_id → (node_id, agent_audit 最大 id)，``remember`` 用它学边。
        self.last_seen = LastSeenStore()

    def make_device(self, context: Any) -> Any:
        """按 ``device_factory`` 造设备；未注入时懒导入 ``AppNavDevice``。"""
        factory = self.device_factory
        if factory is not None:
            return factory(context)
        from maa_api.agent.nav.navigator import AppNavDevice

        return AppNavDevice(context)

    async def ensure_seed(self) -> int:
        """``nav_node`` 为空时一个事务写入种子，返回写入的节点数；非空返回 0。"""
        async with self._session_factory() as session:
            existing = await session.scalar(select(func.count()).select_from(NavNode))
            if existing:
                return 0
            for spec in SEED_NODES:
                session.add(
                    NavNode(
                        id=str(spec["id"]),
                        title=str(spec["title"]),
                        kind=str(spec["kind"]),
                        recognizer=dict(spec["recognizer"]),
                        exemplars=[],
                        origin="seed",
                    )
                )
            await session.flush()
            for spec in SEED_EDGES:
                action = dict(spec["action"])
                session.add(
                    NavEdge(
                        from_node=str(spec["from_node"]),
                        to_node=str(spec["to_node"]),
                        action=action,
                        action_hash=action_hash(validate_action(action)),
                        origin="seed",
                    )
                )
            await session.commit()
            return len(SEED_NODES)

    async def graph(self) -> tuple[list[NavNode], list[NavEdge]]:
        async with self._session_factory() as session:
            nodes = list(
                (await session.execute(select(NavNode).order_by(NavNode.id))).scalars()
            )
            edges = list(
                (await session.execute(select(NavEdge).order_by(NavEdge.id))).scalars()
            )
        return nodes, edges

    async def get_node(self, node_id: str) -> NavNode | None:
        async with self._session_factory() as session:
            return await session.get(NavNode, node_id)

    async def get_edge(self, edge_id: str) -> NavEdge | None:
        async with self._session_factory() as session:
            return await session.get(NavEdge, edge_id)

    async def upsert_node(
        self,
        node_id: str,
        *,
        title: str,
        kind: str,
        origin: str,
        recognizer: Mapping[str, Any] | None = None,
    ) -> NavNode:
        async with self._session_factory() as session:
            node = await session.get(NavNode, node_id)
            if node is None:
                node = NavNode(
                    id=node_id,
                    title=title,
                    kind=kind,
                    origin=origin,
                    exemplars=[],
                    recognizer=dict(recognizer or {}),
                )
                session.add(node)
            else:
                node.title = title
                node.kind = kind
                if recognizer is not None:
                    node.recognizer = dict(recognizer)
                node.updated_at = utcnow()
            await session.commit()
            return node

    async def add_exemplar(
        self,
        node_id: str,
        *,
        phash: str,
        screenshot_id: str | None,
        source: str,
    ) -> bool:
        """去重并追加样本；重复返回 ``False``，超过上限时挤掉最早的非 user 样本。"""
        async with self._session_factory() as session:
            node = await session.get(NavNode, node_id)
            if node is None:
                raise AppError(
                    ErrorCode.NOT_FOUND, f"界面 {node_id} 不存在", {"node_id": node_id}
                )
            exemplars = [dict(item) for item in (node.exemplars or [])]
            from maa_api.agent.nav.recognizer import distances

            for item in exemplars:
                existing = item.get("phash")
                if not isinstance(existing, str) or not existing:
                    continue
                distance = distances(phash, existing)
                if distance is not None and all(
                    value <= DUPLICATE_DISTANCE for value in distance
                ):
                    return False
            exemplars.append(
                {
                    "phash": phash,
                    "screenshot_id": screenshot_id,
                    "source": source,
                    "added_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                }
            )
            if len(exemplars) > MAX_EXEMPLARS:
                removable = next(
                    (
                        index
                        for index, item in enumerate(exemplars)
                        if item.get("source") != "user"
                    ),
                    None,
                )
                del exemplars[0 if removable is None else removable]
            node.exemplars = exemplars
            node.updated_at = utcnow()
            await session.commit()
            return True

    async def set_exemplars(
        self, node_id: str, exemplars: list[dict[str, Any]]
    ) -> NavNode:
        async with self._session_factory() as session:
            node = await session.get(NavNode, node_id)
            if node is None:
                raise AppError(
                    ErrorCode.NOT_FOUND, f"界面 {node_id} 不存在", {"node_id": node_id}
                )
            node.exemplars = [dict(item) for item in exemplars]
            node.updated_at = utcnow()
            await session.commit()
            return node

    async def upsert_edge(
        self,
        *,
        from_node: str,
        to_node: str,
        action: Mapping[str, Any],
        origin: str,
        edge_id: str | None = None,
    ) -> tuple[NavEdge, bool]:
        """同 ``(from_node, to_node, action_hash)`` 已存在时返回 ``(旧边, False)``。"""
        validated = validate_action(action)
        digest = action_hash(validated)
        stored = dict(action) if isinstance(action, Mapping) else validated.model_dump()
        async with self._session_factory() as session:
            existing = await session.scalar(
                select(NavEdge).where(
                    NavEdge.from_node == from_node,
                    NavEdge.to_node == to_node,
                    NavEdge.action_hash == digest,
                )
            )
            if existing is not None:
                return existing, False
            edge = NavEdge(
                id=edge_id or new_uuid(),
                from_node=from_node,
                to_node=to_node,
                action=stored,
                action_hash=digest,
                origin=origin,
            )
            session.add(edge)
            await session.commit()
            return edge, True

    async def update_edge(
        self,
        edge_id: str,
        *,
        enabled: bool | None = None,
        action: Mapping[str, Any] | None = None,
        cost_ms: int | None = None,
    ) -> NavEdge:
        """只改 ``enabled``/``action``/``cost_ms``；重新启用时清零失败次数。"""
        async with self._session_factory() as session:
            edge = await session.get(NavEdge, edge_id)
            if edge is None:
                raise AppError(
                    ErrorCode.NOT_FOUND, f"跳转 {edge_id} 不存在", {"edge_id": edge_id}
                )
            if action is not None:
                validated = validate_action(action)
                digest = action_hash(validated)
                conflict = await session.scalar(
                    select(NavEdge).where(
                        NavEdge.id != edge_id,
                        NavEdge.from_node == edge.from_node,
                        NavEdge.to_node == edge.to_node,
                        NavEdge.action_hash == digest,
                    )
                )
                if conflict is not None:
                    raise AppError(
                        ErrorCode.INVALID_PARAMETER,
                        "同一对界面已有动作相同的跳转",
                        {"field": "action"},
                    )
                edge.action = (
                    dict(action) if isinstance(action, Mapping) else validated.model_dump()
                )
                edge.action_hash = digest
            if enabled is not None:
                if enabled and not edge.enabled:
                    edge.fail_count = 0
                edge.enabled = enabled
            if cost_ms is not None:
                edge.cost_ms = cost_ms
            edge.updated_at = utcnow()
            await session.commit()
            return edge

    async def delete_edge(self, edge_id: str) -> None:
        async with self._session_factory() as session:
            edge = await session.get(NavEdge, edge_id)
            if edge is None:
                raise AppError(
                    ErrorCode.NOT_FOUND, f"跳转 {edge_id} 不存在", {"edge_id": edge_id}
                )
            await session.delete(edge)
            await session.commit()

    async def record_success(self, edge_id: str, *, elapsed_ms: int) -> None:
        async with self._session_factory() as session:
            edge = await session.get(NavEdge, edge_id)
            if edge is None:
                raise AppError(
                    ErrorCode.NOT_FOUND, f"跳转 {edge_id} 不存在", {"edge_id": edge_id}
                )
            edge.success_count += 1
            edge.last_used_at = utcnow()
            if edge.cost_ms is None:
                edge.cost_ms = int(elapsed_ms)
            else:
                edge.cost_ms = round(0.7 * edge.cost_ms + 0.3 * int(elapsed_ms))
            edge.updated_at = utcnow()
            await session.commit()

    async def record_failure(self, edge_id: str) -> bool:
        """失败 +1；这次触发停用时返回 ``True``。"""
        async with self._session_factory() as session:
            edge = await session.get(NavEdge, edge_id)
            if edge is None:
                raise AppError(
                    ErrorCode.NOT_FOUND, f"跳转 {edge_id} 不存在", {"edge_id": edge_id}
                )
            edge.fail_count += 1
            triggered = False
            if (
                edge.enabled
                and edge.fail_count >= DISABLE_AFTER_FAILS
                and edge.fail_count >= edge.success_count
            ):
                edge.enabled = False
                triggered = True
            edge.updated_at = utcnow()
            await session.commit()
            return triggered

    async def shortest_path(
        self,
        source: str,
        target: str,
        *,
        vision: bool,
        exclude: set[str] = frozenset(),
    ) -> PathPlan | None:
        """Dijkstra 最短路径；只有 ``locate`` 边可走时返回 ``blocked_by_vision``。"""
        if source == target:
            return PathPlan((), False)
        nodes, edges = await self.graph()
        excluded = set(exclude)
        steps = _dijkstra(
            nodes,
            edges,
            source,
            target,
            allow_locate=vision,
            exclude=excluded,
        )
        if steps is not None:
            return PathPlan(steps, False)
        if vision:
            return None
        # vision=False 时再按有视觉算一次：有路说明只是缺视觉，报 needs_vision。
        with_vision = _dijkstra(
            nodes,
            edges,
            source,
            target,
            allow_locate=True,
            exclude=excluded,
        )
        if with_vision is not None:
            return PathPlan((), True)
        return None

    async def note_seen(self, session_id: str | None, node_id: str | None) -> None:
        """把 ``(node_id, 当前会话审计最大 id)`` 写进 ``last_seen``。"""
        if not session_id or not node_id:
            return
        async with self._session_factory() as session:
            max_id = await session.scalar(
                select(func.max(AgentAudit.id)).where(
                    AgentAudit.session_id == session_id
                )
            )
        self.last_seen.put(session_id, node_id, int(max_id or 0))

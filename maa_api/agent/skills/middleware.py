"""技能中间件（M78 规格 §4.2、§4.5）。

四个钩子各管一件事：

- ``on_run_start``：把上一轮运行 ``meta["skills"]`` 里仍对本代理可用的激活项继承
  下来，并算出本轮系统提示第 4 段的正文（技能索引 + 预加载正文 + 压缩前加载过的
  正文，正文总量受 :data:`MAX_INJECTED_BODY_CHARS` 约束）。
- ``before_model``：把这段正文写进稳定段的 ``skills`` 槽位。规格的方法表没有列
  这个钩子，但 ``ContextLayoutMiddleware`` 每轮 ``before_model`` 才重建 system，
  ``on_run_start`` 写不进去（M78 接缝裁决 1），所以照 ``PlanMiddleware`` 的
  ``req.meta["layout_parts"]`` → ``put_segment`` → ``layout.apply()`` 写法补上。
- ``wrap_tool``：先让内层执行（审计照常落库），``load_skill`` 成功后再记录激活、
  把同一轮的二次加载改成 ``already_loaded``，并按技能声明的工具开启按需工具组。
- ``on_run_end``：把激活项合并写回 ``agent_run.meta["skills"]``。``RunService.finish``
  没有 ``meta`` 参数（M78 接缝裁决 3），这里直接读运行行、合并后写回。

状态键 ``ctx.state["skills.active"]`` 每项是 ``{"id", "version", "loaded_seq"}``；
``DurableState.skills`` 仍保持既有 ``list[str]`` 契约，由
:class:`~maa_api.agent.context.durable.DurableContextMiddleware` 从这里取 id。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select

from maa_api.agent.context.layout import (
    SYSTEM_SEGMENT_ORDER,
    ContextLayout,
    Segment,
    put_segment,
)
from maa_api.agent.context.tokens import TokenEstimator
from maa_api.agent.harness.context import (
    ModelRequest,
    RunContext,
    RunOutcome,
    ToolCall,
    ToolOutcome,
)
from maa_api.agent.harness.middleware import CallNext, Middleware
from maa_api.agent.skills.registry import (
    MAX_INJECTED_BODY_CHARS,
    LoadedSkill,
    SkillIndexEntry,
    SkillRegistry,
)
from maa_api.db.models import AgentMessage, AgentRun
from maa_api.db.repositories.agent import AgentRunRepository
from maa_api.db.repositories.compaction import CompactionCheckpointRepository
from maa_api.domain.errors import AppError

__all__ = [
    "ALREADY_LOADED_NOTE",
    "DEFAULT_LOADED_SEQ",
    "PRELOADED_HEADER",
    "RESTORED_HEADER",
    "TRUNCATED_BODY_NOTE",
    "SkillsMiddleware",
]

logger = logging.getLogger(__name__)

#: 同一轮二次加载的提示，逐字照抄规格 §4.5。
ALREADY_LOADED_NOTE = "这个技能已经加载过，正文在前面的结果或系统提示里"
#: 超出 ``MAX_INJECTED_BODY_CHARS`` 的技能只留标题，提示模型重新加载。
TRUNCATED_BODY_NOTE = "正文较长，需要时重新 load_skill"
#: 第 4 段里预加载与压缩补回两段的标题。
PRELOADED_HEADER = "已预加载的技能："
RESTORED_HEADER = "压缩前加载过的技能："
#: 查不到 ``agent_message.seq`` 时的兜底序号（规格 §4.5）：比任何检查点的
#: ``upto_seq`` 都大，表示「这条加载还在当前历史里，不需要补回」。
DEFAULT_LOADED_SEQ = 2**31
#: 本轮第 4 段正文在 ``ctx.state`` 里的键；``before_model`` 每步复用同一份，
#: 「第 4 段在本轮内保持不变」。
SECTION_KEY = "skills.section"
#: 系统段里技能小节的段名（``SYSTEM_SEGMENT_ORDER`` 的第 4 位）。
SEGMENT_NAME = "skills"


def _app_state(ctx: RunContext) -> Any:
    """运行上下文里的应用状态；``ctx.app`` 优先，测试的 ``ctx.request`` 兜底。"""
    state = getattr(getattr(ctx, "app", None), "state", None)
    if state is None:
        state = getattr(
            getattr(getattr(ctx, "request", None), "app", None), "state", None
        )
    return state


def _layout_service(ctx: RunContext) -> ContextLayout:
    """优先用进程内的 ``app.state.context_layout``，测试里退回本轮本地实例。"""
    service = getattr(_app_state(ctx), "context_layout", None)
    if isinstance(service, ContextLayout):
        return service
    local = ctx.state.get("skills.context_layout")
    if isinstance(local, ContextLayout):
        return local
    local = ContextLayout(TokenEstimator())
    ctx.state["skills.context_layout"] = local
    return local


def _clean_active(raw: Any) -> list[dict[str, Any]]:
    """把 ``agent_run.meta["skills"]`` 的形状归一成 ``{id, version, loaded_seq}``。"""
    if not isinstance(raw, (list, tuple)):
        return []
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        skill_id = entry.get("id")
        if not isinstance(skill_id, str) or not skill_id or skill_id in seen:
            continue
        try:
            loaded_seq = int(entry.get("loaded_seq", 0))
        except (TypeError, ValueError):
            continue
        items.append(
            {
                "id": skill_id,
                "version": str(entry.get("version") or ""),
                "loaded_seq": loaded_seq,
            }
        )
        seen.add(skill_id)
    return items


def _loaded_skill(result: Any) -> LoadedSkill | None:
    """工具结果 → :class:`LoadedSkill`；形状不认识时返回 ``None``，原样放行。"""
    if isinstance(result, LoadedSkill):
        return result
    if isinstance(result, Mapping):
        try:
            return LoadedSkill.model_validate(dict(result))
        except ValidationError:
            return None
    return None


def _render_bodies(
    header: str,
    skills: list[LoadedSkill],
    used: int,
) -> tuple[list[str], int]:
    """渲染一段技能正文；超出上限的技能只留标题并提示重新加载。"""
    if not skills:
        return [], used
    lines = [header]
    for skill in skills:
        heading = f"### {skill.title}（{skill.id}）"
        if used + len(skill.body) > MAX_INJECTED_BODY_CHARS:
            lines.append(f"{heading}：{TRUNCATED_BODY_NOTE}")
            continue
        used += len(skill.body)
        lines.append(heading)
        lines.append(skill.body)
    return ["\n".join(lines)], used


class SkillsMiddleware(Middleware):
    """05 §5.2 第 5 位。注入技能索引与需要补回的正文；记录 ``load_skill`` 的激活状态。"""

    name = "skills"
    applies_to = frozenset({"orchestrator", "worker"})
    STATE_KEY = "skills.active"

    def __init__(self, registry: SkillRegistry, *, session_factory: Any = None) -> None:
        self.registry = registry
        self._session_factory = session_factory

    # ------------------------------------------------------------------
    # 本轮开始：继承激活项、算好第 4 段
    # ------------------------------------------------------------------
    async def on_run_start(self, ctx: RunContext) -> None:
        ctx.state[self.STATE_KEY] = []
        ctx.state[SECTION_KEY] = ""
        if not self._settings_enabled():
            return
        index = await self.registry.index_for(ctx.spec.name)
        available = {entry.id for entry in index}
        active = [
            item for item in await self._carried(ctx) if item["id"] in available
        ]
        ctx.state[self.STATE_KEY] = active
        ctx.state[SECTION_KEY] = await self._render_section(ctx, index, active)

    async def _render_section(
        self,
        ctx: RunContext,
        index: list[SkillIndexEntry],
        active: list[dict[str, Any]],
    ) -> str:
        """索引 + 预加载正文 + 压缩前加载过的正文，正文总量不超过上限。"""
        preloaded: list[LoadedSkill] = []
        preloaded_ids: set[str] = set()
        for skill_id in getattr(ctx.spec, "skills", ()) or ():
            loaded = await self._load(ctx, str(skill_id), reason="预加载")
            if loaded is not None:
                preloaded.append(loaded)
                preloaded_ids.add(loaded.id)

        upto = await self._upto_seq(ctx)
        restored: list[LoadedSkill] = []
        for item in active:
            if item["loaded_seq"] > upto or item["id"] in preloaded_ids:
                continue
            loaded = await self._load(ctx, str(item["id"]), reason="压缩补回")
            if loaded is not None:
                restored.append(loaded)

        sections: list[str] = []
        index_text = self.registry.render_index(index)
        if index_text:
            sections.append(index_text)
        used = 0
        for header, skills in ((PRELOADED_HEADER, preloaded), (RESTORED_HEADER, restored)):
            blocks, used = _render_bodies(header, skills, used)
            sections.extend(blocks)
        return "\n\n".join(sections)

    async def _load(
        self, ctx: RunContext, skill_id: str, *, reason: str
    ) -> LoadedSkill | None:
        """加载一份技能；停用、不对该代理开放或设置关闭时跳过并记 debug。"""
        try:
            return await self.registry.load(skill_id, agent_name=ctx.spec.name)
        except AppError:
            logger.debug(
                "技能 %s 的%s跳过：本轮对 %s 不可用",
                skill_id,
                reason,
                ctx.spec.name,
            )
            return None

    # ------------------------------------------------------------------
    # 第 4 段：写进稳定段的 skills 槽位
    # ------------------------------------------------------------------
    async def before_model(self, ctx: RunContext, req: ModelRequest) -> ModelRequest:
        text = ctx.state.get(SECTION_KEY)
        if not isinstance(text, str) or not text:
            return req
        parts = req.meta.get("layout_parts")
        if parts is None:
            return req
        profile = req.profile
        if profile is None:
            model_state = ctx.state.get("model")
            if isinstance(model_state, Mapping):
                profile = model_state.get("profile")
        if profile is None:
            return req
        put_segment(
            parts.system, Segment(SEGMENT_NAME, text), SYSTEM_SEGMENT_ORDER
        )
        return _layout_service(ctx).apply(ctx, req, parts, profile=profile)

    # ------------------------------------------------------------------
    # 工具：load_skill 成功后的激活与工具组
    # ------------------------------------------------------------------
    async def wrap_tool(
        self, ctx: RunContext, call: ToolCall, call_next: CallNext
    ) -> ToolOutcome:
        outcome = await call_next(call)
        if call.name != "load_skill" or outcome.status != "ok":
            return outcome
        loaded = _loaded_skill(outcome.result)
        if loaded is None:
            return outcome

        active = self._active(ctx)
        current = next(
            (item for item in active if item["id"] == loaded.id), None
        )
        preloaded = {
            str(skill_id) for skill_id in (getattr(ctx.spec, "skills", ()) or ())
        }
        if loaded.id in preloaded:
            outcome.result = self._already_loaded(loaded.id)
            return outcome
        if current is not None and current["loaded_seq"] > await self._upto_seq(ctx):
            outcome.result = self._already_loaded(loaded.id)
            return outcome

        loaded_seq = await self._message_seq(ctx, outcome.message_id)
        if current is not None:
            current["version"] = loaded.version
            current["loaded_seq"] = loaded_seq
        else:
            active.append(
                {
                    "id": loaded.id,
                    "version": loaded.version,
                    "loaded_seq": loaded_seq,
                }
            )

        available, unavailable = self._split_tools(ctx, loaded.tools)
        groups = self._groups_to_enable(ctx, available)
        enabled_groups = self._enable_groups(ctx, groups)
        result = (
            dict(outcome.result)
            if isinstance(outcome.result, Mapping)
            else loaded.model_dump()
        )
        result["tools_available"] = available
        result["tools_unavailable"] = unavailable
        result["enabled_groups"] = enabled_groups
        outcome.result = result
        return outcome

    @staticmethod
    def _already_loaded(skill_id: str) -> dict[str, Any]:
        return {
            "id": skill_id,
            "already_loaded": True,
            "note": ALREADY_LOADED_NOTE,
        }

    def _active(self, ctx: RunContext) -> list[dict[str, Any]]:
        active = ctx.state.get(self.STATE_KEY)
        if not isinstance(active, list):
            active = []
            ctx.state[self.STATE_KEY] = active
        return active

    @staticmethod
    def _split_tools(
        ctx: RunContext, tools: list[str]
    ) -> tuple[list[str], list[str]]:
        """技能声明的工具里，哪些在代理的固定工具集里、哪些不在。"""
        spec_tools = {str(name) for name in (getattr(ctx.spec, "tools", ()) or ())}
        available = [name for name in tools if name in spec_tools]
        unavailable = [name for name in tools if name not in spec_tools]
        return available, unavailable

    def _groups_to_enable(
        self, ctx: RunContext, available: list[str]
    ) -> list[str]:
        """可用工具里属于本代理可开启扩展组、且当前还没开的组。"""
        deferred = {
            str(group) for group in (getattr(ctx.spec, "deferred_groups", ()) or ())
        }
        if not deferred:
            return []
        tool_registry = self._tool_registry(ctx)
        runtime = self._runtime(ctx)
        if tool_registry is None or runtime is None:
            logger.debug("没有工具注册表或运行时，不自动开启技能工具组")
            return []
        try:
            enabled = set(runtime.enabled_tool_groups(ctx.session_id or ""))
        except Exception:  # noqa: BLE001 - 开组只是便利，失败不打断本轮
            logger.warning("读取已开启工具组失败", exc_info=True)
            return []
        groups: list[str] = []
        for name in available:
            try:
                definition = tool_registry.get(name)
            except AppError:
                continue
            group = str(definition.group)
            if group in deferred and group not in enabled and group not in groups:
                groups.append(group)
        return groups

    def _enable_groups(self, ctx: RunContext, groups: list[str]) -> list[str]:
        if not groups:
            return []
        runtime = self._runtime(ctx)
        if runtime is None:
            return []
        try:
            enabled = runtime.enable_tool_groups(ctx.session_id or "", groups)
        except Exception:  # noqa: BLE001 - 同上，开组失败只记日志
            logger.warning("开启技能工具组失败：%s", groups, exc_info=True)
            return []
        return sorted(set(groups) & set(enabled))

    @staticmethod
    def _tool_registry(ctx: RunContext) -> Any:
        state = _app_state(ctx)
        registry = getattr(
            getattr(state, "confirmation_service", None), "registry", None
        )
        if registry is None:
            runtime = getattr(state, "agent_runtime", None)
            registry = getattr(
                getattr(runtime, "confirmation_service", None), "registry", None
            )
        return registry

    @staticmethod
    def _runtime(ctx: RunContext) -> Any:
        return getattr(_app_state(ctx), "agent_runtime", None)

    # ------------------------------------------------------------------
    # 运行结束：激活项写进 agent_run.meta
    # ------------------------------------------------------------------
    async def on_run_end(self, ctx: RunContext, outcome: RunOutcome) -> None:
        del outcome  # 终止原因不影响技能的确定性保存
        if self._session_factory is None or not ctx.run_id:
            return
        active = self._active(ctx)
        try:
            async with self._session_factory() as session:
                run = await AgentRunRepository(session).get(ctx.run_id)
                if run is None:
                    return
                meta = dict(run.meta or {})
                meta["skills"] = [dict(item) for item in active]
                run.meta = meta
                await session.commit()
        except Exception:  # noqa: BLE001 - 确定性保存失败不能盖过本轮结果
            logger.warning(
                "写入运行技能的激活状态失败：run=%s", ctx.run_id, exc_info=True
            )

    # ------------------------------------------------------------------
    # 读库小工具
    # ------------------------------------------------------------------
    async def _carried(self, ctx: RunContext) -> list[dict[str, Any]]:
        """同一会话上一个运行（``started_at`` 最近、不是本运行）的激活项。"""
        if self._session_factory is None or not ctx.session_id or not ctx.run_id:
            return []
        try:
            async with self._session_factory() as session:
                result = await session.execute(
                    select(AgentRun)
                    .where(
                        AgentRun.session_id == ctx.session_id,
                        AgentRun.id != ctx.run_id,
                    )
                    .order_by(AgentRun.started_at.desc(), AgentRun.id.desc())
                    .limit(1)
                )
                previous = result.scalars().first()
        except Exception:  # noqa: BLE001 - 读不到就当没有继承项
            logger.warning(
                "读取上一轮技能激活状态失败：session=%s",
                ctx.session_id,
                exc_info=True,
            )
            return []
        if previous is None or not isinstance(previous.meta, Mapping):
            return []
        return _clean_active(previous.meta.get("skills"))

    async def _upto_seq(self, ctx: RunContext) -> int:
        """本会话最新 active 压缩检查点的 ``upto_seq``；没有为 ``-1``。"""
        if self._session_factory is None or not ctx.session_id:
            return -1
        try:
            async with self._session_factory() as session:
                checkpoint = await CompactionCheckpointRepository(
                    session
                ).active_for_session(ctx.session_id)
        except Exception:  # noqa: BLE001 - 读不到检查点就当没有压缩过
            logger.warning(
                "读取压缩检查点失败：session=%s", ctx.session_id, exc_info=True
            )
            return -1
        return checkpoint.upto_seq if checkpoint is not None else -1

    async def _message_seq(self, ctx: RunContext, message_id: Any) -> int:
        """``outcome.message_id`` → ``agent_message.seq``；查不到用兜底序号。"""
        if message_id is None or self._session_factory is None:
            return DEFAULT_LOADED_SEQ
        try:
            async with self._session_factory() as session:
                message = await session.get(AgentMessage, int(message_id))
        except Exception:  # noqa: BLE001 - 形状不对或读库失败都按兜底序号
            logger.debug("查询工具消息的 seq 失败：message=%s", message_id, exc_info=True)
            return DEFAULT_LOADED_SEQ
        return int(message.seq) if message is not None else DEFAULT_LOADED_SEQ

    def _settings_enabled(self) -> bool:
        """``skills.enabled``；设置段尚未装配时按开启处理（与注册表同一口径）。"""
        check = getattr(self.registry, "_settings_enabled", None)
        return True if check is None else bool(check())

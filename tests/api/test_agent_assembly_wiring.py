"""M56：Agent 工具依赖的装配接线（``app.state`` 上的设置/会话/作业服务）。

2026-10-17 的实机会话有两条同源故障：

- ``delegate_recon`` / ``search_code`` / ``list_files`` 全部报
  ``AGENT_DISABLED``「设置服务尚未启动」——工具从 ``app.state.settings_provider``
  取设置，但 lifespan 从未设置它；
- ``search_copilots`` 按 ``app.state.copilot_service`` 取服务，lifespan 也没装配，
  兜底构造又没带账号干员表 → 每份作业都被判成「未拥有」，模型据此去手动拉整张干员表、
  甚至更新资源。

``tests/services`` 里的用例自己 new 服务、显式传依赖，抓不到主装配缺口（M31/M33/M51
的装配缺陷都是这样漏出去的），所以这里从 ``create_app()`` 的 lifespan 断言。
"""

from __future__ import annotations

from maa_api.agent.tickets.inbox import InboxService
from maa_api.agent.tickets.service import TicketService
from maa_api.agent.tickets.threads import ThreadRouter
from maa_api.main import create_app
from maa_api.services.game.copilots import CopilotService
from maa_api.settings import Settings


def test_lifespan_wires_settings_provider_for_agent_tools(tmp_settings, isolated_db) -> None:
    """工具层取设置的唯一入口必须在 lifespan 里接上（且是同步可调用的）。"""

    app = create_app()
    with _lifespan(app):
        provider = getattr(app.state, "settings_provider", None)
        assert callable(provider), (
            "lifespan 没有设置 app.state.settings_provider；侦察/只读探索工具会报"
            "『设置服务尚未启动』"
        )
        settings = provider()
        assert isinstance(settings, Settings)


def test_lifespan_wires_session_factory_and_copilot_search_service(tmp_settings, isolated_db) -> None:
    """作业站搜索要带账号干员表与关卡索引，否则所有作业都会被判成不可行。"""

    app = create_app()
    with _lifespan(app):
        factory = getattr(app.state, "session_factory", None)
        assert callable(factory), "lifespan 没有设置 app.state.session_factory"

        service = getattr(app.state, "copilot_service", None)
        assert isinstance(service, CopilotService)
        # 装上了才能按账号筛作业；``_box_index`` 在 box 缺失时返回 None、整页判不可行。
        assert service.box is not None
        assert service.stage_index is not None


def test_lifespan_wires_ticket_services(tmp_settings, isolated_db) -> None:
    """工单三件套必须挂到 ``app.state``，且 TicketService 复用同一个 InboxService。"""

    app = create_app()
    with _lifespan(app):
        inbox_service = getattr(app.state, "inbox_service", None)
        thread_router = getattr(app.state, "thread_router", None)
        ticket_service = getattr(app.state, "ticket_service", None)
        assert isinstance(inbox_service, InboxService), (
            "lifespan 没有设置 app.state.inbox_service"
        )
        assert isinstance(thread_router, ThreadRouter), (
            "lifespan 没有设置 app.state.thread_router"
        )
        assert isinstance(ticket_service, TicketService), (
            "lifespan 没有设置 app.state.ticket_service"
        )
        assert ticket_service._inbox_service is inbox_service


def test_lifespan_wires_finish_hook_fanout(tmp_settings, isolated_db) -> None:
    """M70/M81：终态钩子槽位绑的是扇出器，养成计划与事件发布各一个订阅者。"""

    from maa_api.agent.hooks_fanout import FinishHookFanout

    app = create_app()
    with _lifespan(app):
        fanout = getattr(app.state, "finish_hooks", None)
        assert isinstance(fanout, FinishHookFanout), (
            "lifespan 没有设置 app.state.finish_hooks"
        )
        assert fanout.names == ("upgrade_plans", "triggers")


def test_lifespan_wires_m70_orchestrator_and_worker_services(
    tmp_settings, isolated_db
) -> None:
    """M70 第 8 步：hub、委派、Worker、总控与广播器都要挂上并对齐。"""

    from maa_api.agent.harness.events import RunEventBus
    from maa_api.agent.orchestrator.service import OrchestratorService
    from maa_api.agent.orchestrator.stream import AgentRunBroadcaster
    from maa_api.agent.workers.delegation import DelegationService
    from maa_api.agent.workers.service import WorkerService
    from maa_api.agent.workers.titler import TicketTitler

    app = create_app()
    with _lifespan(app):
        hub = app.state.run_event_bus
        assert isinstance(hub, RunEventBus), "lifespan 没有设置 app.state.run_event_bus"
        # hub 只做分发，不带运行归属；每轮运行自己建带 run_id 的 per-run 总线。
        assert hub.run_id is None
        assert isinstance(app.state.ticket_titler, TicketTitler)
        assert isinstance(app.state.delegation_service, DelegationService)
        assert isinstance(app.state.worker_service, WorkerService)
        assert isinstance(app.state.orchestrator_service, OrchestratorService)
        assert isinstance(app.state.agent_run_broadcaster, AgentRunBroadcaster)
        assert app.state.worker_service._orchestrator is app.state.orchestrator_service
        assert app.state.ticket_service._orchestrator_resolver is not None
        assert app.state.finish_hooks.names == (
            "upgrade_plans",
            "triggers",
        )


def test_lifespan_wires_trigger_services(tmp_settings, isolated_db) -> None:
    """M81：规则/事件/发布器三件套挂到 ``app.state``，并绑定诊断适配器。"""

    from maa_api.agent.events.bus import TriggerService
    from maa_api.agent.events.rules import TriggerRuleService
    from maa_api.agent.events.wiring import PipelineEventPublisher

    app = create_app()
    with _lifespan(app):
        rule_service = getattr(app.state, "trigger_rule_service", None)
        service = getattr(app.state, "trigger_service", None)
        publisher = getattr(app.state, "pipeline_event_publisher", None)
        assert isinstance(rule_service, TriggerRuleService), (
            "lifespan 没有设置 app.state.trigger_rule_service"
        )
        assert isinstance(service, TriggerService), (
            "lifespan 没有设置 app.state.trigger_service"
        )
        assert isinstance(publisher, PipelineEventPublisher), (
            "lifespan 没有设置 app.state.pipeline_event_publisher"
        )
        assert service._rules is rule_service
        incident = app.state.agent_incident_service
        assert incident._trigger_service is service
        assert incident._publisher is publisher
        assert incident.boot_id == app.state.boot_id


def test_lifespan_wires_goal_services(tmp_settings, isolated_db) -> None:
    """M82 §4.5.7：目标服务与后台推送监听器挂到 ``app.state`` 并注册为汇报监听器。"""

    from maa_api.agent.goals.service import GoalService
    from maa_api.agent.tickets.push import TicketPushNotifier

    app = create_app()
    with _lifespan(app):
        goal_service = getattr(app.state, "goal_service", None)
        notifier = getattr(app.state, "ticket_push_notifier", None)
        assert isinstance(goal_service, GoalService), (
            "lifespan 没有设置 app.state.goal_service"
        )
        assert isinstance(notifier, TicketPushNotifier), (
            "lifespan 没有设置 app.state.ticket_push_notifier"
        )
        ticket_service = app.state.ticket_service
        assert goal_service._ticket_service is ticket_service
        # 复盘 job 要注册在同一个调度器上（schedule_service 构造之后才 start_jobs）。
        assert goal_service.scheduler is app.state.update_scheduler
        listeners = ticket_service._report_listeners
        assert goal_service.on_ticket_report in listeners
        assert notifier.on_report in listeners


def _lifespan(app):
    """进入/退出应用 lifespan 的上下文（不依赖 TestClient 的 HTTP 栈）。"""

    from fastapi.testclient import TestClient

    return TestClient(app, raise_server_exceptions=False)

def test_lifespan_wires_device_lease_service(tmp_settings, isolated_db) -> None:
    """设备租约服务要挂到 app.state，并绑定为 PipelineRunner 的领取闸门。"""

    from maa_api.agent.lease import DeviceLeaseService

    app = create_app()
    with _lifespan(app):
        service = getattr(app.state, "device_lease_service", None)
        assert isinstance(service, DeviceLeaseService), (
            "lifespan 没有设置 app.state.device_lease_service"
        )
        runner = app.state.pipeline_runner
        assert getattr(runner, "_lease_gate", None) is service

"""M31：养成计划的装配接线（计划服务 ↔ 定时任务服务 ↔ 确认策略）。

M27 起就存在的装配缺陷：``UpgradePlanService`` 在 ``main.py`` 的 lifespan 里比
``ScheduleService`` 早构造约 300 行，那时 ``app.state.schedule_service`` 还不存在，
构造参数拿到 ``None`` —— 于是真实启动时「每天建定时任务」「与现有定时任务争抢理智」
两条规格要求（§7.7）静默失效，而 M27 的冒烟自己直接构造服务，永远看不到。
M31 改成装配末尾回填（``bind_schedule_service`` + ``bind_delete_hook``），这个文件
把「接线真的发生在主装配路径上」钉住：只测服务层的单测抓不到它。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from maa_api.main import create_app


def test_lifespan_hands_the_schedule_service_to_the_upgrade_plan_service(
    tmp_settings, isolated_db
) -> None:
    """启动后：计划服务拿到定时任务服务，删除钩子与确认策略的预演提供者都接好。"""
    app = create_app()
    with TestClient(app, raise_server_exceptions=False):
        plans = app.state.upgrade_plan_service
        schedules = app.state.schedule_service
        policy = app.state.confirmation_service.policy

        # ① 每日定时任务与冲突检查的前提：两个服务真的连上了。
        assert plans._schedule_service is schedules
        # ② 用户在定时任务页删掉任务 → 计划同步标 cancelled（规格 §7.7 第 4 条）。
        assert schedules._delete_hook == plans.on_schedule_deleted
        # ③ 确认卡片的计划预演（M31）：装的是计划服务的 bound method。
        assert policy.plan_preview == plans.preview


def test_plan_service_can_be_rebound_after_the_schedule_service_is_created(
    tmp_settings, isolated_db
) -> None:
    """``bind_schedule_service`` 是后置绑定：先构造、后注入，并且以最后一次为准。"""
    from maa_api.services.account.plans import UpgradePlanService

    class _Stub:
        pass

    first, second = _Stub(), _Stub()
    service = UpgradePlanService(lambda: None, schedule_service=first)

    assert service._schedule_service is first
    service.bind_schedule_service(second)
    assert service._schedule_service is second

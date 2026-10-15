"""M51：作业草稿服务的**装配**接线（``resource_service`` 不能漏传）。

用户 2026-10-16 报告的缺陷：Agent 调 ``save_copilot_draft`` 报
``SERVICE_UNAVAILABLE``「资源服务尚未装配（缺少 resource_service）」。
根因在 ``main.py`` 的 lifespan：``CopilotDraftService(...)`` 构造时漏传了
``resource_service``，于是 ``site_id`` 导入（``upload_copilot``）一走到就抛 503。

``tests/services/test_copilot_draft.py`` 抓不到这一类：那个夹具自己 new 了
``CopilotDraftService`` 并显式传入 resource service，绕开了主装配路径
（M31/M33 的装配缺陷教训，见 ``tests/api/test_account_capture_wiring.py``）。
这里把「主装配真的把 resource service 递进去了」钉住。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from maa_api.main import create_app
from maa_api.services.copilot.draft import CopilotDraftService


def test_lifespan_hands_the_resource_service_to_the_copilot_draft_service(
    tmp_settings, isolated_db
) -> None:
    """启动后：``app.state.copilot_draft_service`` 能拿到 resource service。"""

    app = create_app()
    with TestClient(app, raise_server_exceptions=False):
        draft = app.state.copilot_draft_service

    assert isinstance(draft, CopilotDraftService)
    # 属性在缺失时抛 SERVICE_UNAVAILABLE；这里要求它返回真值，即装配确实传了参数。
    assert draft.resource_service is not None, (
        "lifespan 构造 CopilotDraftService 时漏传 resource_service；"
        "site_id 导入会在保存草稿时抛『资源服务尚未装配』"
    )

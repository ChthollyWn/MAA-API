"""M33：账号识别采集的**装配**接线（``CALLBACK`` → ``AccountCaptureService``）。

M27 起就存在的缺陷：``AccountCaptureService`` 在 ``main.py`` 的 lifespan 里构造好了、
也放进了 ``app.state``，但没有任何生产调用方——识别回调进来之后账号数据永远不更新
（用户 2026-10-10 报告的第二个问题：「识别任务完成后，没有更新到账号数据」）。

只测 ``callback_wiring`` 自己的单测（``tests/services/test_account_capture_wiring.py``）
抓不到「``main.py`` 忘了调用它」这一类装配缺陷（M31 的教训，见
``tests/api/test_upgrade_plan_wiring.py``），所以这里把「接线真的发生在主装配路径上」
钉住。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from maa_api.main import create_app
from maa_api.services.account.capture import AccountCaptureService

WIRING_MODULE = "maa_api.services.account.callback_wiring"


def test_lifespan_registers_the_account_capture_on_the_callback_stream(
    tmp_settings, isolated_db
) -> None:
    """启动后：``app.state.account_capture`` 真的挂在核心客户端的 CALLBACK 上。"""

    app = create_app()
    with TestClient(app, raise_server_exceptions=False):
        capture = app.state.account_capture
        client = app.state.core_client

        assert isinstance(capture, AccountCaptureService)
        handlers = [
            handler
            for handler in client._handlers.get("CALLBACK", ())
            if getattr(handler, "__module__", "") == WIRING_MODULE
        ]
        assert handlers, (
            "lifespan 没有把账号识别采集挂到 CALLBACK 上"
            "（main.py 是否调用了 install_account_capture？）"
        )
        # 同一个采集服务只挂一次（重复注册会让每次识别落两遍库）。
        assert len(handlers) == 1

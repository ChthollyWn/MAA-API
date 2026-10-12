"""森空岛（Skland）账号数据接入（M42，规格 §4.0–§4.5）。

- :mod:`maa_api.services.skland.client`：带签名的 HTTP 客户端与扫码登录链路
  （鹰角通行证 + 森空岛两侧的错误归一）。
- ``mapping`` / ``sync`` 由同一里程碑的另两条工作流放进这个包（规格 §4.5）。

**不复用 ``services/external/``**：那层是为「外部情报内容源」设计的分发元数据层（只登记
GET 路径与查询参数、响应进缓存并按档位限流），森空岛是带签名头、带 POST body、带账号凭证
的取数通道，塞进去会把「凭证签名」与「公共内容缓存」两种语义搅在一起（规格 §4.0）。
"""

from __future__ import annotations

from maa_api.services.skland.client import (
    AS_BASE_URL,
    DEFAULT_EXTRA_HEADERS,
    RETRY_BACKOFF_SECONDS,
    SKLAND_APP_CODE,
    USER_AGENT,
    ZONAI_BASE_URL,
    BindingRole,
    ScanLogin,
    SklandClient,
    SklandCredential,
)

__all__ = [
    "AS_BASE_URL",
    "DEFAULT_EXTRA_HEADERS",
    "RETRY_BACKOFF_SECONDS",
    "SKLAND_APP_CODE",
    "USER_AGENT",
    "ZONAI_BASE_URL",
    "BindingRole",
    "ScanLogin",
    "SklandClient",
    "SklandCredential",
]

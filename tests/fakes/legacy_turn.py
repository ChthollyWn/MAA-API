"""测试驱动：把 ``stream_legacy_turn`` 转导出，v2 行为用例继续按原方法调用。

M72 删掉了 v2 写端点与运行时的旧 SSE 方法，实现搬到
``maa_api.agent.legacy_turn``（评测运行器也用同一份）。测试只从这里导入，生产
HTTP 路径不经过它。
"""

from __future__ import annotations

from maa_api.agent.legacy_turn import stream_legacy_turn

__all__ = ["stream_legacy_turn"]

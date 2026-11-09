"""``nav_locate`` schema：视觉模型按描述定位一个按钮（M86 spec §4.5.2）。

``found=false`` 表示截图里找不到；找到时 ``x`` / ``y`` 是中心点的归一化坐标
（左上角 0,0，右下角 1,1），``confidence`` 是 0–1 的把握。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from maa_api.agent.llm.structured import register_schema

__all__ = ["NavLocate"]


class NavLocate(BaseModel):
    """一次视觉定位的输出。"""

    model_config = ConfigDict(extra="forbid")

    found: bool
    x: float = Field(default=0.0, ge=0, le=1)
    y: float = Field(default=0.0, ge=0, le=1)
    confidence: float = Field(ge=0, le=1)


register_schema("nav_locate", NavLocate)

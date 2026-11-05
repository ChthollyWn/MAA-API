"""IM 通道（M85）：绑定、入站与发件箱。

G1 落地发件箱与异常类型；G2 在 :mod:`maa_api.agent.channels.adapter` 里扩展出
完整 :class:`ChannelAdapter` 协议、:class:`Inbound`，并加入四个平台适配器与
``FakeAdapter``。G3 的 ``ChannelService`` 负责绑定与入站处理。
"""

from maa_api.agent.channels.adapter import ChannelAdapter, Inbound
from maa_api.agent.channels.errors import OutboxFatal, OutboxRetry

__all__ = ["ChannelAdapter", "Inbound", "OutboxFatal", "OutboxRetry"]

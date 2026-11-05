"""IM 通道（M85）：绑定、入站与发件箱。

G1 只落地发件箱与异常类型；G2 在 ``adapter.py`` 里扩展完整的
:class:`~maa_api.agent.channels.outbox.ChannelAdapter` 协议并加入四个平台适配器。
"""

from maa_api.agent.channels.errors import OutboxFatal, OutboxRetry

__all__ = ["OutboxFatal", "OutboxRetry"]

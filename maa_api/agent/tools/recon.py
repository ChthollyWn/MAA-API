"""``recon`` 工具组：三个只读探索工具 + 一个侦察委派工具（M48 规格 §4.2/§4.3）。

实现放在 :mod:`maa_api.agent.recon` 包里；这里只负责把这一组接进共享注册表，
与其它工具模块保持同一形状（``register_tools(registry)``）。
"""

from __future__ import annotations

from maa_api.agent.recon.delegate import register_tools as register_delegate_tools
from maa_api.agent.recon.explore import register_tools as register_explore_tools
from maa_api.agent.registry import ToolRegistry

__all__ = ["register_tools"]


def register_tools(registry: ToolRegistry) -> None:
    """注册 ``list_files`` / ``read_file`` / ``search_code`` / ``delegate_recon``。"""

    register_explore_tools(registry)
    register_delegate_tools(registry)

"""CoreRegistry：内核实例注册表（多实例扩展口）。

维持「只跑一个 MaaCore 子进程、绑一台设备」的现有语义（docs/13 ADR-10）：应用装配时用
:data:`DEFAULT_CORE_ID` 注册唯一的 :class:`~maa_api.core.client.CoreClient`，内部所有内核
调用写 ``registry.get(core_id)``，``core_id`` 恒为 ``"default"``（docs/02 §5.4）。将来支持
多设备时，只需让注册表管理多份 ``core_id → CoreClient`` 与设备绑定配置，路由层与数据模型
不需要改动，所以 ``default`` 不做成模块级全局变量。

纯内存映射：不启动子进程、不碰数据库、不碰 Queue，也不负责多实例的进程编排。因此它可以在
任何进程、任何时机被安全 import：运行期不 import ``maa_api.core.client`` /
``maa_api.core.supervisor``（类型标注走 ``TYPE_CHECKING``），不会把内核依赖链带给轻量
调用方。

查询失败抛 :class:`~maa_api.domain.errors.AppError`，错误码复用内核层既有的
``CORE_NOT_READY``（不新增错误码）：「注册表里没有这个 core_id」与「内核未就绪」对调用方
是同一类可重试状态，路由层按既有映射返回固定 503。

注册/注销发生在装配期（单线程），运行期只有 ``get`` 的只读访问，因此不加锁，dict 的读取
在 CPython 下是原子的。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from maa_api.domain.errors import AppError, ErrorCode

if TYPE_CHECKING:  # 仅类型检查期导入：运行期不拉起内核依赖链。
    from maa_api.core.client import CoreClient

__all__ = ["DEFAULT_CORE_ID", "CoreRegistry"]

#: 首版唯一的实例 id；扩展到多设备前，所有内核调用都传它（docs/02 §5.4）。
DEFAULT_CORE_ID = "default"


class CoreRegistry:
    """``core_id`` → CoreClient 的注册表；首版只注册 ``"default"``。"""

    def __init__(self, *, default_id: str = DEFAULT_CORE_ID) -> None:
        self._default_id = default_id
        self._clients: dict[str, CoreClient] = {}

    @property
    def default_id(self) -> str:
        """``get()`` 省略 ``core_id`` 时解析到的 id（首版恒为 ``"default"``）。"""
        return self._default_id

    @property
    def default(self) -> CoreClient:
        """``default_id`` 对应的 CoreClient；未注册时与 :meth:`get` 同样抛 AppError。"""
        return self.get()

    def register(self, core_id: str, client: CoreClient) -> None:
        """登记一个内核实例。

        重复注册同一个 ``core_id`` 抛 ``AppError(CORE_NOT_READY)``：若允许覆盖，
        正在使用旧 client 的调用方会悄悄接到另一个内核；宁可在装配期 fail loud。
        """
        if core_id in self._clients:
            raise AppError(
                ErrorCode.CORE_NOT_READY,
                f"内核实例 {core_id} 已存在，不能重复注册；请先移除同名实例或改用其他名称",
                {"core_id": core_id},
            )
        self._clients[core_id] = client

    def get(self, core_id: str | None = None) -> CoreClient:
        """取 ``core_id`` 对应的 CoreClient；``None`` 表示取默认实例。

        未注册时抛 ``AppError(CORE_NOT_READY)``，``details["core_id"]`` 为解析后的
        id（省略参数时即 :attr:`default_id`），便于路由层把缺省解析结果回给调用方。
        """
        resolved = self._default_id if core_id is None else core_id
        try:
            return self._clients[resolved]
        except KeyError:
            raise AppError(
                ErrorCode.CORE_NOT_READY,
                f"找不到名为 {resolved} 的内核实例；"
                f"当前可用：{'、'.join(self._clients) or '无'}",
                {"core_id": resolved},
            ) from None

    def ids(self) -> list[str]:
        """已注册的 ``core_id`` 列表，**按插入序**（稳定顺序，便于日志与断言）。"""
        return list(self._clients)

    def unregister(self, core_id: str) -> bool:
        """注销并返回该 id 此前是否存在；不存在返回 ``False`` 而不抛（幂等清理）。"""
        if core_id in self._clients:
            del self._clients[core_id]
            return True
        return False

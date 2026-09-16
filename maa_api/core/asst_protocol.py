"""AsstProtocol —— MaaCore 实例的能力契约（docs/03 §8 测试策略）。

worker 只依赖本协议，不依赖具体实现（``maa_api.model.core.asst.Asst``）。
测试时注入 ``tests.fakes.fake_asst.FakeAsst``，执行层、日志汇聚、重试与超时
逻辑即可在没有 MaaCore、没有真机的环境下运行。

约定：

- 用 ``typing.Protocol`` + ``@runtime_checkable``；``isinstance`` 只检查方法是否
  存在，不做签名级校验，签名由本文件与类型检查器约束。
- 方法签名与 ``maa_api/model/core/asst.py`` 的公开方法逐一对齐（``load``、
  ``connect``、``get_image``、``set_instance_option``、``set_static_option``、
  ``set_connection_extras`` 等），``staticmethod`` 照实标注。
- 同时预先声明 docs/03 §2.1 的新增方法签名（``connect_async``、``connected``、
  ``back_to_home``、``click``、``screencap``、``get_image_bgr``、``get_uuid``、
  ``get_tasks_list``、``get_null_size``），它们是 M1 要落地的目标契约。
"""

from __future__ import annotations

import pathlib
from typing import Any, Optional, Protocol, Union, runtime_checkable

from maa_api.model.util.utils import InstanceOptionType, JSON, StaticOptionType

__all__ = ["AsstProtocol", "TaskId"]

TaskId = int
"""任务 ID，等价于内核的 ``AsstTaskId``。"""


@runtime_checkable
class AsstProtocol(Protocol):
    """MaaCore 实例契约，worker 侧只认这个协议。"""

    # ------------------------------------------------------------------
    # 进程级 / 静态接口（与 Asst 的 @staticmethod 一一对应）
    # ------------------------------------------------------------------

    @staticmethod
    def load(path: Union[pathlib.Path, str],
             incremental_path: Optional[Union[pathlib.Path, str]] = None,
             user_dir: Optional[Union[pathlib.Path, str]] = None) -> bool:
        """加载 MaaCore 动态库与资源，返回是否成功。"""

    @staticmethod
    def set_static_option(option_type: StaticOptionType, option_value: str) -> bool:
        """设置进程级参数，返回是否成功。"""

    @staticmethod
    def set_connection_extras(name: str, extras: JSON) -> None:
        """设置连接模拟器端的 Extras。"""

    @staticmethod
    def log(level: str, message: str) -> None:
        """通过内核打印一条日志。"""

    @staticmethod
    def get_null_size() -> int:
        """内核约定的“无效尺寸”哨兵值，``get_image`` 以此判定失败。"""

    # ------------------------------------------------------------------
    # 实例接口
    # ------------------------------------------------------------------

    def set_instance_option(self, option_type: InstanceOptionType, option_value: str) -> bool:
        """设置实例级额外配置，返回是否成功。"""

    def connect(self, adb_path: str, address: str, config: str = "General") -> bool:
        """同步连接设备（官方已废弃），返回是否连接成功。"""

    def connect_async(self, adb_path: str, address: str,
                      config: str = "General", block: bool = True) -> int:
        """异步连接设备，返回 async_call_id，成败看 ``Message.AsyncCallInfo`` 回调。"""

    def connected(self) -> bool:
        """设备当前是否处于连接状态。"""

    def back_to_home(self) -> bool:
        """回到游戏主界面（卡死救援动作），返回是否成功。"""

    def click(self, x: int, y: int, block: bool = True) -> int:
        """点击指定坐标，返回 async_call_id。"""

    def screencap(self, block: bool = True) -> int:
        """触发一次截图，返回 async_call_id；结果由 ``get_image`` 取回。"""

    def get_image(self, size: int) -> bytes | None:
        """取最近一次截图的 RGB 原始字节；失败返回 None。"""

    def get_image_bgr(self, size: int) -> bytes | None:
        """取最近一次截图的 BGR 原始字节；失败返回 None。"""

    def get_uuid(self) -> str | None:
        """设备唯一码；未连接时返回 None。"""

    def get_tasks_list(self) -> list[int]:
        """内核任务队列中的 task id 列表。"""

    def append_task(self, type_name: str, params: JSON = {}) -> TaskId:
        """添加任务，返回任务 ID。"""

    def set_task_params(self, task_id: TaskId, params: JSON) -> bool:
        """动态设置任务参数，返回是否成功。"""

    def start(self) -> bool:
        """开始任务，返回是否成功。"""

    def stop(self) -> bool:
        """停止并清空所有任务，返回是否成功。"""

    def running(self) -> bool:
        """是否正在运行。"""

    def get_version(self) -> str:
        """获取 MaaCore 版本号。"""

    def call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """通用分发入口：按名字调用上面声明的方法，供 worker 命令循环使用。"""

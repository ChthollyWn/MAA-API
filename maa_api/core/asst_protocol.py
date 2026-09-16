"""AsstProtocol —— MaaCore 实例的能力契约（docs/03 §8 测试策略）。

worker 只依赖本协议，不依赖具体实现（``maa_api/core/asst.py`` 的 ``Asst``）；
测试时注入 ``tests.fakes.fake_asst.FakeAsst``，执行层、日志汇聚、重试与超时
逻辑即可在没有 MaaCore、没有真机的环境下运行。

约定：

- 用 ``typing.Protocol`` + ``@runtime_checkable``；运行期 ``isinstance`` 只检查成员
  是否存在，不做签名级校验，签名由本文件与类型检查器约束。
- 方法名、参数名、``staticmethod`` 标注与 ``maa_api/core/asst.py`` 的 ``Asst``
  逐一对齐，M1-05 的结构一致性契约测试会双向校验。
- ``call`` 是 worker 命令循环的通用分发入口。M1-04 的 ``Asst`` 是 ctypes 薄封装、
  没有这个方法（真正提供它的是 ``FakeAsst`` 与 M1-07 的 worker），因此它声明为
  ``Callable`` 属性而不是方法：既保住「协议方法 ⊆ Asst 公开方法」的结构契约，
  又让运行期 ``isinstance`` 继续要求实现方提供 ``call``。
- 类属性 ``__init__`` 是 typing 注入的「协议不可实例化」守卫，不属于协议方法
  集合；这里换成等价的守卫对象，避免它污染上面的按名对齐检查（它不是 ``Asst``
  的公开方法，却会被 ``vars()`` 当成协议方法）。
- 枚举以 ``maa_api/core/enums.py`` 为唯一事实来源；``maa_api/model/util/utils.py``
  是待删的旧模块，本文件不再引用。
"""

from __future__ import annotations

import pathlib
from typing import Any, Callable, Optional, Protocol, Union, runtime_checkable

from maa_api.core.enums import InstanceOptionKey, Message, StaticOptionKey

__all__ = ["AsstProtocol", "AsyncCallInfo", "CallBackType", "JSON", "TaskId"]

TaskId = int
"""任务 ID，等价于内核的 ``AsstTaskId``。"""

CallBackType = Callable[[int, Optional[bytes], Any], None]
"""内核回调签名 ``(msg: int, details: bytes, arg) -> None``，与 ``Asst.CallBackType`` 对齐。

``details`` 是 UTF-8 的 JSON 字节串（``c_char_p`` 边界），不是 dict。
"""

JSON = Union[dict[str, Any], list[Any], int, float, str, bool, None]
"""可 JSON 序列化的纯数据（任务参数、连接 Extras 等）。"""

AsyncCallInfo = Message.AsyncCallInfo
"""异步调用结果所在的消息类型（msg=4）。

``connect_async`` / ``click`` / ``screencap`` 的成败只能认它（M1-01 真机实测）：

- 关联键 ``async_call_id`` 在回调 JSON **顶层**，与方法的返回值相等；
- 成功标志取 ``details.details.ret`` —— 从回调记录 / IPC 事件对象看的双层路径
  （``event.details`` 是内核载荷，载荷顶层的 ``details.ret`` 才是结果）；
- 调用类型在顶层 ``what``（连接为 ``"Connect"``）。

msg=2 的 ``ConnectionInfo`` 会先报出 ``what == "Connected"``，那只是中间态，
不得拿它代替本消息。样本见 ``tests/fixtures/async_call_info_sample.json``。
"""


class _ProtocolMemberPlaceholder:
    """协议里的非方法占位成员。

    ``typing`` 给每个 Protocol 类注入的 ``__init__``、以及本协议里只作声明用的
    ``call``，都不该被「按名对齐 Asst 公开方法」的检查当成方法：占位对象让
    ``inspect.isfunction`` / ``staticmethod`` 检查掠过它们，同时保持两个能力：

    - ``callable()`` 为真 —— ``@runtime_checkable`` 据此把它们当方法成员，
      ``issubclass()`` 不会被禁用；
    - 出现在 ``vars(AsstProtocol)`` 里 —— 运行期 ``isinstance`` 与契约测试
      据此确认成员存在。
    """

    __slots__ = ("_message",)

    def __init__(self, message: str) -> None:
        self._message = message

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        raise TypeError(self._message)


@runtime_checkable
class AsstProtocol(Protocol):
    """MaaCore 实例契约，worker 侧只认这个协议。"""

    # typing 注入的同名守卫是普通函数，会混进「协议方法」集合；换成占位对象。
    __init__ = _ProtocolMemberPlaceholder(
        "AsstProtocol 是 typing.Protocol，不能实例化"
    )

    # ------------------------------------------------------------------
    # 进程级 / 静态接口（与 Asst 的 @staticmethod 一一对应）
    # ------------------------------------------------------------------

    @staticmethod
    def load(path: Union[pathlib.Path, str],
             incremental: bool = False,
             user_dir: Optional[Union[pathlib.Path, str]] = None) -> bool:
        """加载内核动态库与资源，返回本次加载是否成功。

        :param path: ``incremental=False`` 时是内核目录；``True`` 时是一层增量
            资源的根目录。
        :param incremental: 为 True 时只叠加资源层，不重复加载动态库。
        :param user_dir: 用户数据目录（日志、调试图片、crash.log）。
        """

    @staticmethod
    def set_static_option(option_type: StaticOptionKey, option_value: str) -> bool:
        """设置进程级参数，返回是否成功。"""

    @staticmethod
    def set_connection_extras(name: str, extras: JSON) -> None:
        """设置连接模拟器端的 Extras。"""

    @staticmethod
    def get_null_size() -> int:
        """内核约定的「无效尺寸」哨兵值，``get_image`` 以此判定失败。"""

    @staticmethod
    def log(level: str, message: str) -> None:
        """通过内核打印一条日志。"""

    @staticmethod
    def get_map_level_key(key: str) -> Optional[dict[str, Optional[str]]]:
        """关卡名 / code / stageId / levelId 互查（实验性 API）。

        内核未导出该符号或查不到匹配关卡时返回 None，调用方回退本地索引。
        """

    # ------------------------------------------------------------------
    # 实例接口
    # ------------------------------------------------------------------

    def set_instance_option(self, option_type: InstanceOptionKey, option_value: str) -> bool:
        """设置实例级额外配置，返回是否成功。"""

    def connect(self, adb_path: str, address: str, config: str = "General") -> bool:
        """同步连接设备（官方已废弃），返回是否连接成功。"""

    def connect_async(self, adb_path: str, address: str,
                      config: str = "General", block: bool = True) -> int:
        """异步连接设备，返回 ``async_call_id``；成败看 :data:`AsyncCallInfo` 回调。

        返回值只表示「请求已受理」，非零不代表连接成功；``block`` 是内核内部
        是否串行化该调用，不是 Python 侧阻塞。
        """

    def connected(self) -> bool:
        """设备当前是否处于连接状态；比 ``running()`` 更适合做设备健康判定。"""

    def back_to_home(self) -> bool:
        """回到游戏主界面（卡死救援动作），返回是否成功。"""

    def click(self, x: int, y: int, block: bool = True) -> int:
        """点击指定坐标，返回 ``async_call_id``；坐标系为设备原始分辨率。"""

    def screencap(self, block: bool = True) -> int:
        """触发一次截图，返回 ``async_call_id``；结果随后由 ``get_image`` 取回。"""

    def last_resolution(self) -> Optional[tuple[int, int]]:
        """最近一次 ``ResolutionGot`` 缓存的分辨率 ``(width, height)``；未知返回 None。"""

    def get_image(self, size: Optional[int] = None,
                  bgr: bool = False) -> Optional[bytes]:
        """取最近一次截图的字节；失败返回 None。

        ``size`` 缺省时按 :meth:`last_resolution` 的缓存推算，未知则放弃本次取图。
        """

    def get_image_bgr(self, size: int) -> Optional[bytes]:
        """取最近一次截图的 BGR 原始字节；失败返回 None。"""

    def get_uuid(self) -> Optional[str]:
        """设备唯一码；未连接时返回 None。"""

    def get_tasks_list(self) -> list[int]:
        """内核任务队列中的 task id 列表。"""

    def append_task(self, type_name: str, params: Optional[JSON] = None) -> TaskId:
        """添加任务，返回任务 ID；``task_id == 0`` 表示失败。"""

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

    #: 通用分发入口：``call(method, *args, **kwargs)`` —— 按名字调用上面声明的方法，
    #: 供 worker 的命令循环使用。声明成 ``Callable`` 属性而非方法的原因见模块 docstring。
    call: Callable[..., Any] = _ProtocolMemberPlaceholder(
        "AsstProtocol.call 只是分发入口的声明；实现由 FakeAsst / worker 提供"
    )

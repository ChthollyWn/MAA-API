"""MaaCore 内核层枚举：回调消息与实例/静态选项键。

本模块是内核 ABI 在 Python 侧的**唯一**映射表：

- 整数值直接来自内核的 ``AsstMsg`` / ``InstanceOptionKey`` / ``StaticOptionKey``，
  是与 ``resource/lib/maa`` 下本地内核通信的接口契约，**不得重排或改值**。
  取值不连续（如 ``TaskChainError = 10000``）是内核既成事实，不要"顺手"改成连号 ——
  照连号硬编码会把 ``ConnectionInfo`` (2) 当成 ``TaskChainError``。
- 与旧封装 ``maa_api/model/util/utils.py`` 中的同名枚举逐项对齐；旧模块在 M3 删除，
  删除之前两者必须保持数值一致（M1-03 verify 会逐项比对）。

决策依据：docs/02 §6（模块划分）、docs/03 §1.1（C API）与 §2.3（枚举补全）。
"""

from enum import IntEnum, unique

__all__ = [
    "InstanceOptionKey",
    "InstanceOptionType",
    "Message",
    "StaticOptionKey",
    "StaticOptionType",
]


@unique
class Message(IntEnum):
    """内核回调消息类型，对应 C 侧 ``AsstMsg``。

    回调解析的三条实测结论（M1-01，``tests/fixtures/core_probe_findings.md``）：

    - ``AsyncCallInfo`` 的关联键 ``async_call_id`` 在回调 JSON **顶层**；
      成功标志在 **``details.details.ret``（双层嵌套）**；调用类型在顶层 ``what``。
    - ``what == "Connected"`` 的 ``ConnectionInfo`` 会**先于** ``AsyncCallInfo`` 报出，
      异步连接成败只能认 ``AsyncCallInfo``，不得用 ``ConnectionInfo`` 代替。
    - 本节只定义消息类型本身；载荷解析归 M1-04（``core/asst.py``）。

    因为是 ``IntEnum``，内核回调里的原始 ``msg`` 整数值可以直接与成员比较，
    也可以 ``Message(msg)`` 还原成员（``msg`` 不在取值域时抛 ``ValueError``）。
    """

    InternalError = 0
    InitFailed = 1
    ConnectionInfo = 2
    AllTasksCompleted = 3
    AsyncCallInfo = 4
    Destroyed = 5

    TaskChainError = 10000
    TaskChainStart = 10001
    TaskChainCompleted = 10002
    TaskChainExtraInfo = 10003
    TaskChainStopped = 10004

    SubTaskError = 20000
    SubTaskStart = 20001
    SubTaskCompleted = 20002
    SubTaskExtraInfo = 20003
    SubTaskStopped = 20004


@unique
class InstanceOptionKey(IntEnum):
    """``AsstSetInstanceOption`` 的选项键，对应 C 侧 ``InstanceOptionKey``。

    ``ClientType = 6`` 是内核实测值（docs/03 §2.3）：**不是** 1 —— 1 那个位置属于
    已废弃的 ``MinitouchEnabled``。本项目的常规 ADB 连接（``General`` 链）不需要设置
    它；渠道（官服 / B 服）的实际生效途径是各任务的 ``client_type`` 参数，
    而不是实例级选项。
    """

    touch_type = 2
    deployment_with_pause = 3
    adblite_enabled = 4
    kill_on_adb_exit = 5
    ClientType = 6


@unique
class StaticOptionKey(IntEnum):
    """``AsstSetStaticOption`` 的选项键，对应 C 侧 ``StaticOptionKey``。"""

    invalid = 0
    cpu_ocr = 1
    gpu_ocr = 2


# 过渡期兼容别名：M1-04 之前的调用方仍按旧名 *Type 引用这两个枚举。
# 新代码一律使用 *Key；别名在内核层重写完成后（M1-05/M3）移除。
InstanceOptionType = InstanceOptionKey
StaticOptionType = StaticOptionKey

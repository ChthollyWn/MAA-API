"""内核层枚举契约（M1-03）：整数值是内核 ABI，必须与旧封装逐项一致。

这里刻意同时导入新旧两套枚举做**逐项**比对：新枚举一旦写错数值，真机回调就会走进
错误分支（例如把 ``ConnectionInfo`` (2) 当成 ``TaskChainError`` (10000)），
而单元测试不加载内核，只能靠这张对照表兜住。

旧 ``maa_api/model/util/utils.py`` 在 M3 删除，删除前两者必须保持数值一致。
"""

from enum import IntEnum

from maa_api.core.enums import (
    InstanceOptionKey,
    InstanceOptionType,
    Message,
    StaticOptionKey,
    StaticOptionType,
)
from maa_api.model.util.utils import InstanceOptionType as LegacyInstanceOptionType
from maa_api.model.util.utils import Message as LegacyMessage
from maa_api.model.util.utils import StaticOptionType as LegacyStaticOptionType

# M1-01 / M1-02 真机实测到的回调 msg 序列（tests/fixtures/core_probe_findings.md 的
# [2, 2, 2, 2, 2, 4, 5] 与 tests/fixtures/spike_subprocess_result.json 的 callback_msgs）。
PROBE_OBSERVED_MSG = [2, 2, 2, 2, 2, 4, 5, 10001, 20001, 20002, 20003, 10002, 3]

# ABI 硬约束（docs/03 §1.1/§2.3）：数值即内核接口，不得重排。
EXPECTED_MESSAGE_VALUES = {
    "InternalError": 0,
    "InitFailed": 1,
    "ConnectionInfo": 2,
    "AllTasksCompleted": 3,
    "AsyncCallInfo": 4,
    "Destroyed": 5,
    "TaskChainError": 10000,
    "TaskChainStart": 10001,
    "TaskChainCompleted": 10002,
    "TaskChainExtraInfo": 10003,
    "TaskChainStopped": 10004,
    "SubTaskError": 20000,
    "SubTaskStart": 20001,
    "SubTaskCompleted": 20002,
    "SubTaskExtraInfo": 20003,
    "SubTaskStopped": 20004,
}


# ----------------------------------------------------------------------
# Message
# ----------------------------------------------------------------------

def test_message_values_match_legacy_abi_exactly():
    """16 个成员逐项对齐：成员名集合相同，且每个成员的整数值相同。"""
    assert {m.name for m in Message} == {m.name for m in LegacyMessage}
    assert {m.name: int(m) for m in Message} == {
        m.name: m.value for m in LegacyMessage
    }


def test_message_values_match_documented_abi():
    assert {m.name: int(m) for m in Message} == EXPECTED_MESSAGE_VALUES
    assert len(Message) == 16


def test_message_specific_abi_anchors():
    assert int(Message.AsyncCallInfo) == 4
    assert int(Message.AllTasksCompleted) == 3
    assert int(Message.ConnectionInfo) == 2
    assert int(Message.TaskChainError) == 10000
    assert int(Message.TaskChainStopped) == 10004
    assert int(Message.SubTaskError) == 20000
    assert int(Message.SubTaskStopped) == 20004


def test_message_is_int_enum_and_int_convertible():
    """内核回调用 int 报消息类型，成员必须能直接与 int 比较 / 由 int 还原。"""
    assert issubclass(Message, IntEnum)
    assert Message.AsyncCallInfo == 4
    assert Message(4) is Message.AsyncCallInfo
    assert Message(Message.TaskChainStopped) is Message.TaskChainStopped


def test_message_covers_probe_observed_kernel_messages():
    """真机实测到过的 msg 值都能还原成成员，且没有落到错误分支。"""
    names = [Message(v).name for v in PROBE_OBSERVED_MSG]
    assert set(names) <= {m.name for m in Message}
    # msg=2 的 ConnectionInfo 会先于 AsyncCallInfo 报 Connected，两者绝不能混用。
    assert Message(2) is Message.ConnectionInfo
    assert Message(4) is Message.AsyncCallInfo


# ----------------------------------------------------------------------
# InstanceOptionKey / StaticOptionKey
# ----------------------------------------------------------------------

def test_instance_option_key_values_match_legacy_abi_exactly():
    legacy_names = {o.name for o in LegacyInstanceOptionType}
    assert {o.name for o in InstanceOptionKey} == legacy_names | {"ClientType"}
    assert all(
        int(LegacyInstanceOptionType[name]) == int(InstanceOptionKey[name])
        for name in legacy_names
    )
    assert {int(o) for o in InstanceOptionKey} == {2, 3, 4, 5, 6}


def test_instance_option_client_type_is_six_not_one():
    """ClientType 实测为 6；1 是已废弃的 MinitouchEnabled，不能猜成 1（docs/03 §2.3）。"""
    assert int(InstanceOptionKey.ClientType) == 6
    assert int(InstanceOptionKey.ClientType) != 1
    assert not hasattr(InstanceOptionKey, "MinitouchEnabled")


def test_static_option_key_values_match_legacy_abi_exactly():
    assert {o.name for o in StaticOptionKey} == {o.name for o in LegacyStaticOptionType}
    assert {o.name: int(o) for o in StaticOptionKey} == {
        o.name: o.value for o in LegacyStaticOptionType
    }
    assert int(StaticOptionKey.invalid) == 0
    assert int(StaticOptionKey.cpu_ocr) == 1
    assert int(StaticOptionKey.gpu_ocr) == 2


def test_legacy_type_aliases_point_to_new_keys():
    """过渡期别名必须是同一个类对象（M1-04 之前的调用方仍按旧名引用）。"""
    assert InstanceOptionType is InstanceOptionKey
    assert StaticOptionType is StaticOptionKey


def test_key_enums_are_int_enums_with_unique_values():
    for enum_cls in (Message, InstanceOptionKey, StaticOptionKey):
        assert issubclass(enum_cls, IntEnum)
        values = [int(m) for m in enum_cls]
        assert len(values) == len(set(values))

"""AsstProtocol 与 FakeAsst 的骨架测试（docs/03 §8 测试策略）。

全部用例默认运行：不加载 MaaCore、不连接设备、不做崩溃注入
（崩溃注入与 IPC 契约测试属 M1）。
"""

import inspect
import json

from maa_api.core.asst_protocol import AsstProtocol
from maa_api.model.core.asst import Asst
from maa_api.model.util.utils import Message
from tests.fakes import fake_asst as fake_asst_module
from tests.fakes.fake_asst import SCRIPTS, FakeAsst


def _own_public_methods(cls) -> set[str]:
    """类自身声明的公开方法名（不含继承、不含私有与类属性）。"""
    return {
        name
        for name, value in vars(cls).items()
        if not name.startswith("_")
        and (inspect.isfunction(value) or isinstance(value, staticmethod))
    }


def _own_static_methods(cls) -> set[str]:
    return {
        name
        for name, value in vars(cls).items()
        if not name.startswith("_") and isinstance(value, staticmethod)
    }


# ----------------------------------------------------------------------
# 协议本身
# ----------------------------------------------------------------------

def test_asst_protocol_is_runtime_checkable_protocol():
    assert inspect.isclass(AsstProtocol)
    assert getattr(AsstProtocol, "_is_protocol", False), "AsstProtocol 必须是 typing.Protocol"
    assert getattr(AsstProtocol, "_is_runtime_protocol", False), "AsstProtocol 必须 @runtime_checkable"


def test_protocol_covers_all_public_asst_methods():
    """协议必须覆盖现有 Asst 的全部公开方法，且 staticmethod 照实标注。"""
    missing = _own_public_methods(Asst) - set(vars(AsstProtocol))
    assert not missing, f"AsstProtocol 缺少方法：{sorted(missing)}"

    static_mismatch = _own_static_methods(Asst) - _own_static_methods(AsstProtocol)
    assert not static_mismatch, f"这些方法在 Asst 上是 staticmethod，协议里也必须标注：{sorted(static_mismatch)}"

    # worker 命令循环依赖的通用分发入口
    assert "call" in vars(AsstProtocol)


def test_fake_asst_satisfies_asst_protocol(fake_asst):
    assert isinstance(fake_asst, AsstProtocol), "FakeAsst 必须结构性满足 AsstProtocol"
    for script_name in ("success", "failure", "stuck", "disconnect"):
        assert isinstance(FakeAsst(script=script_name), AsstProtocol)


# ----------------------------------------------------------------------
# FakeAsst 剧本：成功 / 失败 / 卡死 / 断开
# ----------------------------------------------------------------------

def test_success_script_emits_expected_sequence(fake_asst_factory):
    fake = fake_asst_factory("success")
    assert fake.connect("adb", "127.0.0.1:5555") is True
    assert fake.connected() is True

    task_id = fake.append_task("Fight", {"stage": "1-7"})
    assert task_id > 0

    assert fake.start() is True
    assert fake.wait_until_script_done(timeout=2.0) is True

    assert fake.messages() == [
        Message.TaskChainStart,
        Message.TaskChainCompleted,
        Message.AllTasksCompleted,
    ]
    assert fake.running() is False

    start = fake.records_of(Message.TaskChainStart)[0]
    assert start.details["taskid"] == task_id
    assert start.details["taskchain"] == "Fight"
    assert fake.calls_of("append_task")[0][1] == ("Fight", {"stage": "1-7"})


def test_callback_shape_matches_native_boundary(fake_asst_factory):
    """回调必须是 (int, bytes-json, arg)，与 Asst.CallBackType 的 c_char_p 对齐。"""
    received = []
    arg = {"worker_id": 1}
    fake = fake_asst_factory(
        "success",
        callback=lambda message, details, custom: received.append((message, details, custom)),
        arg=arg,
    )

    fake.append_task("Fight")
    fake.start()
    assert fake.wait_until_script_done(timeout=2.0) is True

    assert [message for message, _, _ in received] == [
        Message.TaskChainStart.value,
        Message.TaskChainCompleted.value,
        Message.AllTasksCompleted.value,
    ]
    raw_message, raw_details, custom = received[0]
    assert isinstance(raw_message, int)
    assert isinstance(raw_details, bytes)
    assert json.loads(raw_details.decode("utf-8"))["taskchain"] == "Fight"
    assert custom == arg
    assert fake.callback_errors == []


def test_failure_script_is_observable(fake_asst_factory):
    fake = fake_asst_factory("failure")
    task_id = fake.append_task("Fight")
    fake.start()
    assert fake.wait_until_script_done(timeout=2.0) is True

    error = fake.records_of(Message.TaskChainError)
    assert len(error) == 1
    assert error[0].details["taskid"] == task_id
    assert error[0].details["why"] == "scripted failure"
    assert Message.TaskChainCompleted not in fake.messages()
    assert fake.running() is False


def test_stuck_script_is_observable(fake_asst_factory):
    fake = fake_asst_factory("stuck")
    fake.append_task("Fight")
    assert fake.start() is True

    # 前缀事件已到达、剧本线程已结束，但任务永远不算完成
    assert fake.wait_for_record(Message.TaskChainStart, timeout=2.0) is True
    assert fake.wait_until_script_done(timeout=2.0) is True
    assert fake.wait_for_record(Message.TaskChainCompleted, timeout=0.2) is False
    assert fake.running() is True
    assert fake.stuck_observed() is True

    # 超时兜底方可以 stop() 收场，卡死态随之解除
    fake.stop()
    assert fake.running() is False
    assert fake.wait_for_record(Message.TaskChainStopped, timeout=1.0) is True


def test_disconnect_script_is_observable(fake_asst_factory):
    fake = fake_asst_factory("disconnect")
    assert fake.connect("adb", "127.0.0.1:5555") is True

    fake.append_task("Fight")
    fake.start()
    assert fake.wait_until_script_done(timeout=2.0) is True

    assert [record.details.get("what") for record in fake.records_of(Message.ConnectionInfo)] == [
        "Connected",
        "Disconnect",
    ]
    assert fake.connected() is False
    assert fake.running() is False
    assert fake.records_of(Message.TaskChainCompleted) == []
    assert fake.records_of(Message.AllTasksCompleted) == []


def test_scripts_registry_exposes_four_required_scenarios():
    assert set(SCRIPTS) == {"success", "failure", "stuck", "disconnect"}
    assert SCRIPTS["stuck"].stuck is True
    assert SCRIPTS["failure"].stuck is False


# ----------------------------------------------------------------------
# 替身必须完全脱离真实内核
# ----------------------------------------------------------------------

def test_fake_asst_stays_independent_of_real_core():
    source = inspect.getsource(fake_asst_module)
    assert "ctypes" not in source, "FakeAsst 不得引入 FFI"
    assert "Asst.load" not in source, "FakeAsst 不得加载真实内核"
    assert not hasattr(fake_asst_module, "ctypes")
    assert FakeAsst.load("/nonexistent", None, None) is True  # 不碰文件系统也能成功

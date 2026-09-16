"""AsstProtocol / FakeAsst 与 M1-04 ``Asst`` 的结构一致性契约（docs/03 §8）。

本文件只做静态与纯 Python 断言：不加载内核、不连设备、**不触发崩溃注入**
（``crash`` 剧本的 ``os._exit(-11)`` 由 M1-10 在子进程里真正执行）。

覆盖四件事：

1. 协议的方法集合与 ``maa_api/core/asst.py`` 的 ``Asst`` 双向对齐（名称、
   ``staticmethod`` 标注、参数名）；
2. 全部内置剧本（含 ``crash``）构造出的 FakeAsst 都满足 ``isinstance(..., AsstProtocol)``；
3. ``connect_async`` 的 AsyncCallInfo 载荷键名与嵌套层级与 M1-01 真机实测一致；
4. 崩溃注入剧本的退出码与触发点（只做源码级检查）。
"""

from __future__ import annotations

import inspect
import json

from maa_api.core.asst import Asst
from maa_api.core.asst_protocol import AsstProtocol
from maa_api.core.enums import Message
from tests.fakes import fake_asst as fake_asst_module
from tests.fakes.fake_asst import CRASH_SCRIPT, SCRIPTS, FakeAsst, FakeScript


def _declared_methods(cls) -> set[str]:
    """类自身声明的「方法」成员（函数或 staticmethod），与 M1-05 verify 同口径。"""
    return {
        name
        for name, value in vars(cls).items()
        if inspect.isfunction(value) or isinstance(value, staticmethod)
    }


def _public_methods(cls) -> set[str]:
    """类自身声明的公开方法（不含下划线开头、不含数据成员）。"""
    return {name for name in _declared_methods(cls) if not name.startswith("_")}


# ----------------------------------------------------------------------
# 协议 ↔ Asst：名称、staticmethod 标注、参数名
# ----------------------------------------------------------------------

def test_protocol_methods_are_subset_of_asst_public_methods():
    """协议里的方法必须都是 M1-04 ``Asst`` 的公开方法。"""
    asst_public = {name for name in dir(Asst) if not name.startswith("_")}
    extra = sorted(_declared_methods(AsstProtocol) - asst_public)
    assert not extra, f"协议声明了 Asst 没有的方法：{extra}"


def test_asst_public_methods_are_all_declared_in_protocol():
    """反向：``Asst`` 的公开方法一个都不能漏（worker 只依赖协议）。"""
    missing = sorted(_public_methods(Asst) - set(vars(AsstProtocol)))
    assert not missing, f"协议缺少 Asst 的公开方法：{missing}"


def test_staticmethod_annotations_match_both_ways():
    """staticmethod 标注双向校验：同名成员两边必须同为（或同不为）staticmethod。"""
    protocol_statics = {
        name
        for name, value in vars(AsstProtocol).items()
        if isinstance(value, staticmethod)
    }
    bad = sorted(
        name
        for name in protocol_statics
        if not isinstance(inspect.getattr_static(Asst, name), staticmethod)
    )
    assert not bad, f"协议标了 staticmethod 但 Asst 不是：{bad}"

    asst_statics = {
        name for name, value in vars(Asst).items() if isinstance(value, staticmethod)
    }
    not_marked = sorted(
        name
        for name in asst_statics & set(vars(AsstProtocol))
        if not isinstance(inspect.getattr_static(AsstProtocol, name), staticmethod)
    )
    assert not not_marked, f"Asst 的 staticmethod 在协议里没照实标注：{not_marked}"


def _parameter_names(owner, name: str) -> set[str]:
    """方法参数名（实例方法的 ``self`` 不算参数，静态方法本来就没有）。"""
    parameters = list(inspect.signature(getattr(owner, name)).parameters)
    if not isinstance(inspect.getattr_static(owner, name), staticmethod):
        if parameters[:1] == ["self"]:
            parameters = parameters[1:]
    return set(parameters)


def test_method_parameter_names_align_across_protocol_asst_and_fake():
    """共享方法的参数名三方一致：协议 / M1-04 ``Asst`` / FakeAsst。"""
    mismatched: dict[str, dict[str, object]] = {}
    for name in sorted(_declared_methods(AsstProtocol)):
        signatures: dict[str, object] = {}
        for label, owner in (("protocol", AsstProtocol), ("Asst", Asst),
                             ("FakeAsst", FakeAsst)):
            try:
                signatures[label] = _parameter_names(owner, name)
            except (AttributeError, TypeError, ValueError) as exc:  # pragma: no cover
                signatures[label] = f"<{type(exc).__name__}: {exc}>"
        first = signatures["protocol"]
        if any(value != first for value in signatures.values()):
            mismatched[name] = signatures
    assert not mismatched, mismatched


def test_protocol_keeps_the_generic_call_entry():
    """通用分发入口必须有，但它不是 ``Asst`` 的方法（M1-04 不提供）。"""
    assert "call" in vars(AsstProtocol)
    assert "call" not in _declared_methods(AsstProtocol), (
        "call 只是 worker 侧的分发入口，声明成方法会破坏「协议方法 ⊆ Asst 公开方法」"
    )
    assert callable(getattr(AsstProtocol, "call")), (
        "成员必须可调用：运行期 isinstance / issubclass 据此把它当方法成员"
    )


# ----------------------------------------------------------------------
# FakeAsst ↔ 协议：全部内置剧本（含 crash）都可注入
# ----------------------------------------------------------------------

def test_every_builtin_script_satisfies_asst_protocol():
    assert {"success", "failure", "stuck", "disconnect", "crash"} <= set(SCRIPTS)
    for name, script in SCRIPTS.items():
        assert FakeAsst(script=script).script is script
        assert isinstance(FakeAsst(script=name), AsstProtocol), (
            f"剧本 {name} 构造出的 FakeAsst 不满足 AsstProtocol"
        )


# ----------------------------------------------------------------------
# AsyncCallInfo：键名与嵌套层级照 M1-01 真机实测
# ----------------------------------------------------------------------

def test_connect_async_emits_probed_async_call_info_payload():
    received: list[tuple[int, bytes, object]] = []
    fake = FakeAsst(
        script="success",
        callback=lambda message, details, arg: received.append((message, details, arg)),
    )

    call_id = fake.connect_async("adb", "127.0.0.1:5555")
    assert isinstance(call_id, int) and call_id > 0
    assert fake.wait_for_record(Message.AsyncCallInfo, timeout=2.0) is True

    record = fake.records_of(Message.AsyncCallInfo)[0]
    payload = record.details
    # 顶层：关联键 async_call_id 与调用类型 what（实测：不要到嵌套 details 里找关联键）
    assert payload["async_call_id"] == call_id
    assert payload["what"] == "Connect"
    assert {"async_call_id", "details", "what"} <= set(payload)
    assert "async_call_id" not in payload["details"], "关联键只在载荷顶层"
    # 成功标志：记录的 details.details.ret（内核原始 JSON 即顶层 details.ret）
    assert payload["details"]["ret"] is True
    assert "ret" not in payload, "文档原先推测的扁平 ret 已被 M1-01 实测证伪"

    # 回调边界：msg 是 int、details 是 UTF-8 JSON 字节串（对齐 c_char_p）
    raw_message, raw_details, _ = next(
        item for item in received if item[0] == Message.AsyncCallInfo.value
    )
    assert isinstance(raw_message, int)
    assert isinstance(raw_details, bytes)
    parsed = json.loads(raw_details.decode("utf-8"))
    assert parsed == payload
    assert parsed["details"]["ret"] is True


def test_connection_info_connected_precedes_async_call_info():
    """msg=2 的 ConnectionInfo 先报 Connected，但它不是异步连接的结果。"""
    fake = FakeAsst(script="success")
    fake.connect_async("adb", "127.0.0.1:5555")
    assert fake.wait_for_record(Message.AsyncCallInfo, timeout=2.0) is True

    messages = [record.message for record in fake.records]
    assert Message.ConnectionInfo in messages
    assert messages.index(Message.ConnectionInfo) < messages.index(Message.AsyncCallInfo)
    assert fake.records_of(Message.ConnectionInfo)[0].details["what"] == "Connected"
    # 连接成败只认 msg=4：模拟 ConnectionInfo 还在时 AsyncCallInfo 的 ret 才是结果源
    assert fake.records_of(Message.AsyncCallInfo)[0].details["details"]["ret"] is True


# ----------------------------------------------------------------------
# 崩溃注入：能力落在 FakeAsst 上，本文件不真的触发
# ----------------------------------------------------------------------

def test_crash_script_declares_segv_exit_and_uses_os_exit():
    assert FakeScript.crash_exitcode is None, "默认剧本不得崩溃"
    assert CRASH_SCRIPT.crash_exitcode == -11, "崩溃剧本按 SIGSEGV 语义给 -11"
    assert SCRIPTS["crash"] is CRASH_SCRIPT
    assert CRASH_SCRIPT.stuck is False
    assert CRASH_SCRIPT.events, "崩溃前要有首个事件可发，证明回调链路已在工作"

    source = inspect.getsource(fake_asst_module)
    assert "os._exit" in source, "崩溃注入必须落在 FakeAsst 上（M1-10 只消费不修改）"
    start_source = inspect.getsource(FakeAsst.start)
    assert "crash_exitcode" in start_source and "os._exit(" in start_source
    assert start_source.index("_emit") < start_source.index("os._exit("), (
        "崩溃前必须先发出剧本首个事件（READY 已上报、命令循环已在跑）"
    )


# ----------------------------------------------------------------------
# FakeAsst 静态接口：load / 资源层记录
# ----------------------------------------------------------------------

def test_fake_asst_load_records_path_incremental_and_user_dir():
    FakeAsst.reset_class_state()
    try:
        assert FakeAsst.load("/kernel", user_dir="/user") is True
        assert FakeAsst.load("/layer", incremental=True) is True
        assert FakeAsst.resource_load_calls == [
            ("/kernel", False, "/user"),
            ("/layer", True, None),
        ], "load 必须把 (path, incremental, user_dir) 原样记账"
    finally:
        FakeAsst.reset_class_state()


# ----------------------------------------------------------------------
# FakeAsst 的 resolution / get_image / 关卡互查替身
# ----------------------------------------------------------------------

def test_fake_asst_helpers_follow_constructor_inputs():
    fake = FakeAsst(
        script="success",
        resolution=(2, 2),
        screenshot=b"0123456789abcdef",
        map_level_keys={
            "1-7": {
                "stage_id": "main_01-07#f#",
                "code": "1-7",
                "level_id": "obt/main/level_main_01-07",
                "name": "暴君",
            }
        },
    )
    assert fake.last_resolution() == (2, 2)
    assert fake.get_image() == b"0123456789ab"  # size=None → 2*2*3
    assert fake.get_image(size=4) == b"0123"
    assert fake.get_image(bgr=True) == b"0123456789ab"
    assert fake.get_image_bgr() == b"0123456789ab"
    assert fake.get_image_bgr(size=4) == b"0123"
    assert fake.get_map_level_key("1-7")["code"] == "1-7"
    assert fake.get_map_level_key("9-9") is None  # 降级：交给本地索引

    unknown = FakeAsst(script="success", resolution=None, screenshot=b"0123456789abcdef")
    assert unknown.last_resolution() is None
    assert unknown.get_image() is None, "分辨率未知且未显式传 size 时视为取图失败"
    assert unknown.get_image(size=3) == b"012"

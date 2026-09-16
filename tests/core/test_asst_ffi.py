"""``maa_api/core/asst.py`` 的 FFI 行为测试（不加载真实内核）。

用法：``Asst.__new__(Asst)`` + 注入假 ``_Asst__lib`` / ``_Asst__ptr``，
对 8 个新补的 C API、实验性 API 的能力探测降级、以及回调 trampoline
（缓存 ``ResolutionGot`` + 拦截用户回调异常）做行为断言。

真机实测结论（M1-01/M1-04）只用于构造用例数据与注释，不在本文件里 dlopen。
"""

from __future__ import annotations

import ctypes
import json

import pytest

from maa_api.core.asst import Asst

#: docs/03 §1.1 的 26 个跨平台通用 C API。
COMMON_SYMBOLS = (
    "AsstSetUserDir",
    "AsstLoadResource",
    "AsstSetStaticOption",
    "AsstSetConnectionExtras",
    "AsstCreate",
    "AsstCreateEx",
    "AsstDestroy",
    "AsstSetInstanceOption",
    "AsstConnect",
    "AsstAsyncConnect",
    "AsstAppendTask",
    "AsstSetTaskParams",
    "AsstStart",
    "AsstStop",
    "AsstRunning",
    "AsstConnected",
    "AsstBackToHome",
    "AsstAsyncClick",
    "AsstAsyncScreencap",
    "AsstGetImage",
    "AsstGetImageBgr",
    "AsstGetUUID",
    "AsstGetTasksList",
    "AsstGetNullSize",
    "AsstGetVersion",
    "AsstLog",
)

#: 真机 AsstGetNullSize() 的返回值（M1-04 实测：2**64-1）。
NULL_SIZE = 0xFFFFFFFFFFFFFFFF

PTR = 0x1234


class FakeFunction:
    """可记录调用、可设置 ``restype`` / ``argtypes`` 的假 C 函数。"""

    def __init__(self, behavior=None):
        self.behavior = behavior
        self.calls: list[tuple] = []
        self.restype = None
        self.argtypes = None

    def __call__(self, *args):
        self.calls.append(args)
        if self.behavior is None:
            return 0
        return self.behavior(*args)


class FakeLib:
    """假内核库：只挂用例显式给出的符号，缺失符号按 ctypes 语义抛 AttributeError。"""

    def __init__(self, **functions):
        for name, fn in functions.items():
            setattr(self, name, fn)


def install(monkeypatch, lib) -> None:
    monkeypatch.setattr(Asst, "_Asst__lib", lib, raising=False)


def make_asst(monkeypatch, lib, resolution=None):
    install(monkeypatch, lib)
    asst = Asst.__new__(Asst)
    asst._Asst__ptr = PTR
    asst._Asst__resolution = resolution
    asst._Asst__callback = None
    return asst


def null_size_fn() -> FakeFunction:
    return FakeFunction(lambda: NULL_SIZE)


def write_and_return(payload: bytes):
    """返回一个把 ``payload`` 写进缓冲区并报告长度的假行为。"""

    def behavior(ptr, buffer, size):
        ctypes.memmove(buffer, payload, len(payload))
        return len(payload)

    return behavior


def make_lib_with_common_symbols(**overrides) -> tuple[FakeLib, dict]:
    functions = {name: FakeFunction() for name in COMMON_SYMBOLS}
    functions.update(overrides)
    return FakeLib(**functions), functions


# ----------------------------------------------------------------------
# 实验性 API：能力探测 + 优雅降级
# ----------------------------------------------------------------------

def test_set_lib_properties_binds_26_common_apis(monkeypatch):
    lib, functions = make_lib_with_common_symbols()
    install(monkeypatch, lib)

    Asst._set_lib_properties()  # 缺 AsstGetMapLevelKey 时不得抛 AttributeError

    unbound = [name for name, fn in functions.items() if fn.argtypes is None]
    assert not unbound, f"这些通用 API 没有设置 argtypes：{unbound}"
    assert functions["AsstGetImage"].restype is ctypes.c_uint64
    assert functions["AsstGetImage"].argtypes == (
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint64,
    )
    assert functions["AsstAsyncConnect"].restype is ctypes.c_int32
    assert functions["AsstAsyncConnect"].argtypes[-1] is ctypes.c_bool
    assert functions["AsstGetNullSize"].argtypes == ()
    assert not hasattr(lib, "AsstGetMapLevelKey")


def test_set_lib_properties_binds_experimental_symbol_when_present(monkeypatch):
    lib, functions = make_lib_with_common_symbols(
        AsstGetMapLevelKey=FakeFunction()
    )
    install(monkeypatch, lib)

    Asst._set_lib_properties()

    assert functions["AsstGetMapLevelKey"].restype is Asst._MapLevelKey
    assert functions["AsstGetMapLevelKey"].argtypes == (ctypes.c_char_p,)


def test_missing_experimental_symbol_degrades_to_none(monkeypatch):
    lib, _ = make_lib_with_common_symbols()
    install(monkeypatch, lib)

    assert Asst._has_symbol("AsstGetMapLevelKey") is False
    # 不得触碰缺失符号（假库里没有该属性，碰了就 AttributeError）
    assert Asst.get_map_level_key("1-7") is None


def test_get_map_level_key_returns_dict_or_none(monkeypatch):
    def found(key):
        return Asst._MapLevelKey(
            b"main_01-07#f#", b"1-7", b"obt/main/level_main_01-07", "暴君".encode()
        )

    fn = FakeFunction(found)
    lib, _ = make_lib_with_common_symbols(AsstGetMapLevelKey=fn)
    install(monkeypatch, lib)

    assert Asst.get_map_level_key("1-7") == {
        "stage_id": "main_01-07#f#",
        "code": "1-7",
        "level_id": "obt/main/level_main_01-07",
        "name": "暴君",
    }
    assert fn.calls == [(b"1-7",)]

    fn.behavior = lambda key: Asst._MapLevelKey(None, None, None, None)
    assert Asst.get_map_level_key("不存在的关卡") is None


# ----------------------------------------------------------------------
# AsstGetUUID
# ----------------------------------------------------------------------

def test_get_uuid_normal_zero_and_null_size(monkeypatch):
    uuid_fn = FakeFunction(write_and_return(b"f7c1c4ced5e96a23"))
    lib = FakeLib(AsstGetUUID=uuid_fn, AsstGetNullSize=null_size_fn())
    asst = make_asst(monkeypatch, lib)

    assert asst.get_uuid() == "f7c1c4ced5e96a23"
    assert uuid_fn.calls[0][0] == PTR
    assert uuid_fn.calls[0][2] == Asst._UUID_BUFFER_SIZE

    uuid_fn.behavior = lambda ptr, buffer, size: 0
    assert asst.get_uuid() is None

    uuid_fn.behavior = lambda ptr, buffer, size: NULL_SIZE
    assert asst.get_uuid() is None


def test_get_uuid_truncates_at_returned_length_and_nul(monkeypatch):
    # 缓冲区尾部残留内容不得带进结果；内核可能把 NUL 也算进长度。
    uuid_fn = FakeFunction(write_and_return(b"abc123\x00leftover"))
    lib = FakeLib(AsstGetUUID=uuid_fn, AsstGetNullSize=null_size_fn())
    asst = make_asst(monkeypatch, lib)

    assert asst.get_uuid() == "abc123"


# ----------------------------------------------------------------------
# AsstGetTasksList
# ----------------------------------------------------------------------

def test_get_tasks_list_empty_and_null_size(monkeypatch):
    tasks_fn = FakeFunction(lambda ptr, buffer, size: 0)
    lib = FakeLib(AsstGetTasksList=tasks_fn, AsstGetNullSize=null_size_fn())
    asst = make_asst(monkeypatch, lib)

    assert asst.get_tasks_list() == []

    tasks_fn.behavior = lambda ptr, buffer, size: NULL_SIZE
    assert asst.get_tasks_list() == []


def test_get_tasks_list_truncates_by_returned_length(monkeypatch):
    def behavior(ptr, buffer, size):
        ids = (11, 22, 33, 44)
        ctypes.memmove(buffer, (ctypes.c_int32 * len(ids))(*ids), 4 * len(ids))
        return 2  # 报告只写入了前两个

    tasks_fn = FakeFunction(behavior)
    lib = FakeLib(AsstGetTasksList=tasks_fn, AsstGetNullSize=null_size_fn())
    asst = make_asst(monkeypatch, lib)

    assert asst.get_tasks_list() == [11, 22]
    assert tasks_fn.calls[0][2] == Asst._TASKS_LIST_CAPACITY


def test_get_tasks_list_grows_once_when_return_exceeds_capacity(monkeypatch):
    sizes: list[int] = []

    def behavior(ptr, buffer, size):
        sizes.append(size)
        if size == Asst._TASKS_LIST_CAPACITY:
            ids = (1, 2, 3)
            ctypes.memmove(buffer, (ctypes.c_int32 * len(ids))(*ids), 4 * len(ids))
            return 300  # 返回值大于容量 → 按返回值扩容重试一次
        ids = (7, 8, 9)
        ctypes.memmove(buffer, (ctypes.c_int32 * len(ids))(*ids), 4 * len(ids))
        return len(ids)

    tasks_fn = FakeFunction(behavior)
    lib = FakeLib(AsstGetTasksList=tasks_fn, AsstGetNullSize=null_size_fn())
    asst = make_asst(monkeypatch, lib)

    assert asst.get_tasks_list() == [7, 8, 9]
    assert sizes == [Asst._TASKS_LIST_CAPACITY, 300]


# ----------------------------------------------------------------------
# AsstGetImage / AsstGetImageBgr
# ----------------------------------------------------------------------

def test_get_image_default_size_comes_from_resolution_cache(monkeypatch):
    image_fn = FakeFunction(write_and_return(b"\x89PNG\r\n\x1a\n"))
    lib = FakeLib(AsstGetImage=image_fn, AsstGetNullSize=null_size_fn())
    asst = make_asst(monkeypatch, lib, resolution=(4, 2))

    assert asst.get_image() == b"\x89PNG\r\n\x1a\n"
    ptr, buffer, size = image_fn.calls[0]
    assert ptr == PTR
    assert size == 4 * 2 * 3  # _expected_image_size() = w*h*3
    assert isinstance(buffer, ctypes.Array)
    assert len(buffer) == 24

    image_fn.behavior = lambda ptr, buffer, size: NULL_SIZE
    assert asst.get_image() is None


def test_get_image_without_known_size_returns_none_without_calling_kernel(monkeypatch):
    image_fn = FakeFunction(write_and_return(b"x"))
    lib = FakeLib(AsstGetImage=image_fn, AsstGetNullSize=null_size_fn())
    asst = make_asst(monkeypatch, lib, resolution=None)

    assert asst.get_image() is None
    assert image_fn.calls == []


def test_get_image_bgr_uses_bgr_entry_point(monkeypatch):
    bgr_fn = FakeFunction(write_and_return(b"BGRX"))
    rgb_fn = FakeFunction(lambda *args: pytest.fail("bgr=True 时不得走 AsstGetImage"))
    lib = FakeLib(
        AsstGetImage=rgb_fn, AsstGetImageBgr=bgr_fn, AsstGetNullSize=null_size_fn()
    )
    asst = make_asst(monkeypatch, lib)

    assert asst.get_image_bgr(8) == b"BGRX"
    assert len(bgr_fn.calls) == 1
    assert bgr_fn.calls[0][0] == PTR
    assert bgr_fn.calls[0][2] == 8
    assert rgb_fn.calls == []


def test_get_image_zero_length_is_failure(monkeypatch):
    image_fn = FakeFunction(lambda ptr, buffer, size: 0)
    lib = FakeLib(AsstGetImage=image_fn, AsstGetNullSize=null_size_fn())
    asst = make_asst(monkeypatch, lib)

    assert asst.get_image(size=16) is None


# ----------------------------------------------------------------------
# 回调 trampoline：缓存 ResolutionGot + 异常不穿透 C 边界
# ----------------------------------------------------------------------

def test_trampoline_caches_resolution_and_forwards_to_user_callback(monkeypatch):
    received: list[tuple] = []
    create_ex = FakeFunction(lambda callback, arg: 0xBEEF)
    lib = FakeLib(
        AsstCreateEx=create_ex,
        AsstDestroy=FakeFunction(),
        AsstGetNullSize=null_size_fn(),
    )
    install(monkeypatch, lib)

    asst = Asst(
        callback=lambda msg, details, arg: received.append((msg, details, arg)),
        arg={"worker": 1},
    )
    assert asst._Asst__ptr == 0xBEEF
    assert create_ex.calls[0][0] is asst._Asst__callback

    payload = json.dumps(
        {
            "details": {"width": 2560, "height": 1440},
            "uuid": "f7c1c4ced5e96a23",
            "what": "ResolutionGot",
        }
    ).encode()
    asst._Asst__callback(2, payload, None)
    assert asst.last_resolution() == (2560, 1440)
    assert asst._expected_image_size() == 2560 * 1440 * 3
    assert received == [(2, payload, None)]

    # 别的 ConnectionInfo 不覆盖已缓存的分辨率，但仍原样转发
    connected = json.dumps({"details": {"config": "General"}, "what": "Connected"}).encode()
    asst._Asst__callback(2, connected, None)
    assert asst.last_resolution() == (2560, 1440)
    assert [item[0] for item in received] == [2, 2]


def test_trampoline_contain_user_callback_exception(monkeypatch):
    seen: list[int] = []

    def boom(msg, details, arg):
        seen.append(msg)
        raise RuntimeError("用户回调炸了")

    lib = FakeLib(
        AsstCreateEx=FakeFunction(lambda callback, arg: 0xBEEF),
        AsstDestroy=FakeFunction(),
        AsstGetNullSize=null_size_fn(),
    )
    install(monkeypatch, lib)
    asst = Asst(callback=boom)

    payload = json.dumps(
        {"details": {"width": 8, "height": 6}, "what": "ResolutionGot"}
    ).encode()
    asst._Asst__callback(2, payload, None)  # 不得抛异常
    assert asst.last_resolution() == (8, 6)  # 缓存仍已生效

    asst._Asst__callback(10001, b'{"taskid":1}', None)  # 非 ConnectionInfo 也拦截
    assert seen == [2, 10001]

    # 解析失败（坏 JSON）同样不能挡住转发
    asst._Asst__callback(2, b"not-json", None)
    assert seen == [2, 10001, 2]


def test_trampoline_tolerates_missing_details(monkeypatch):
    received: list[tuple] = []
    lib = FakeLib(
        AsstCreateEx=FakeFunction(lambda callback, arg: 1),
        AsstDestroy=FakeFunction(),
        AsstGetNullSize=null_size_fn(),
    )
    install(monkeypatch, lib)
    asst = Asst(callback=lambda msg, details, arg: received.append((msg, details, arg)))

    asst._Asst__callback(2, None, None)
    assert asst.last_resolution() is None
    assert received == [(2, None, None)]


# ----------------------------------------------------------------------
# load()：首次加载 vs 增量资源层
# ----------------------------------------------------------------------

def test_load_incremental_only_calls_load_resource(monkeypatch):
    load_resource = FakeFunction(lambda path: True)
    lib = FakeLib(AsstLoadResource=load_resource)
    install(monkeypatch, lib)

    assert Asst.load("/tmp/incremental-layer", incremental=True) is True
    assert load_resource.calls == [(b"/tmp/incremental-layer",)]


def test_load_incremental_requires_initial_load(monkeypatch):
    monkeypatch.setattr(Asst, "_Asst__lib", None, raising=False)

    with pytest.raises(RuntimeError):
        Asst.load("/tmp/incremental-layer", incremental=True)

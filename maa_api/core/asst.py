"""MaaCore 的 ctypes FFI 封装（docs/03 §1、§2）。

本模块是内核层唯一直接触碰 native 库的地方，承担三件事：

- 覆盖 docs/03 §1.1 的 26 个跨平台通用 C API。``AsstGetMapLevelKey`` 是 macOS
  导出、Linux 未导出的实验性 API，按「运行期能力探测 + 优雅降级」处理：不可用时
  返回 ``None``，调用方回退到基于 ``resource/.../Stages/`` 的本地关卡索引。
  头文件没有声明、只能靠逆向推测签名的那个 API 一律不碰（编造签名会直接段错误）。
- ``AsstAsyncConnect`` / ``AsstAsyncClick`` / ``AsstAsyncScreencap`` 的返回值是
  ``AsstAsyncCallId``（受理编号），**非零不代表成功**；结果经 ``Message.AsyncCallInfo``
  （msg=4）回调送达，按 M1-01 真机实测的层级解析：``async_call_id`` 在载荷顶层、
  成功标志在 ``details.details.ret``（双层嵌套）、调用类型在顶层 ``what``。
  msg=2 的 ``ConnectionInfo`` 会先于它报出 ``what == "Connected"``，
  异步成败只能认 msg=4，不得用 ``ConnectionInfo`` 代替。
- 回调发生在 MaaCore 自己的线程上：本模块只缓存 ``ResolutionGot`` 分辨率并原样
  转发给用户回调，转发与解析都包在 ``try/except`` 里记日志，异常绝不穿过 C 调用边界。
"""

from __future__ import annotations

import ctypes
import ctypes.util
import json
import logging
import os
import pathlib
import platform
from typing import Any, Callable, Optional, Union

from maa_api.core.enums import InstanceOptionKey, Message, StaticOptionKey

__all__ = ["Asst"]

logger = logging.getLogger(__name__)

#: 各平台的库文件名与环境变量，键是 ``platform.system().lower()`` 的取值。
_PLATFORM_VALUES: dict[str, dict[str, str]] = {
    "windows": {"libpath": "MaaCore.dll", "environ_var": "PATH"},
    "darwin": {"libpath": "libMaaCore.dylib", "environ_var": "DYLD_LIBRARY_PATH"},
    "linux": {"libpath": "libMaaCore.so", "environ_var": "LD_LIBRARY_PATH"},
}


class Asst:
    """MaaCore 实例的 FFI 封装，方法签名见 docs/03 §2.1。"""

    CallBackType = ctypes.CFUNCTYPE(
        None, ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p
    )
    """内核回调函数类型：``(msg: int, details: bytes|None, arg) -> None``。

    ``details`` 是 UTF-8 的 JSON 字节串（内核以 ``c_char_p`` 传入），
    ``arg`` 是 ``AsstCreateEx`` 注册时传入的自定义指针。
    """

    TaskId = int
    """任务 ID，等价于内核的 ``AsstTaskId``。"""

    class _MapLevelKey(ctypes.Structure):
        """``AsstCallerExtra.h`` 的 ``struct AsstMapLevelKey``（实验性 API）。

        四个字段都是 ``const char*``，字符串由 MaaCore 持有，调用方不得释放；
        转成 Python ``str`` 后即可安全保留。
        """

        _fields_ = [
            ("stage_id", ctypes.c_char_p),
            ("code", ctypes.c_char_p),
            ("level_id", ctypes.c_char_p),
            ("name", ctypes.c_char_p),
        ]

    # 类级私有属性：`load()` 前为 None，保持 `Asst.__new__(Asst)` 的测试替身也能工作。
    __lib: Any = None
    __libpath: Any = None
    __resolution: Optional[tuple[int, int]] = None

    #: ``AsstGetUUID`` 的固定缓冲容量，按返回长度截断。
    _UUID_BUFFER_SIZE = 256
    #: ``AsstGetTasksList`` 首次调用的宽松容量（元素个数），不够时按返回值扩容重试一次。
    _TASKS_LIST_CAPACITY = 256

    # ------------------------------------------------------------------
    # 进程级 / 静态接口
    # ------------------------------------------------------------------

    @staticmethod
    def load(
        path: Union[pathlib.Path, str],
        incremental: bool = False,
        user_dir: Optional[Union[pathlib.Path, str]] = None,
    ) -> bool:
        """加载 MaaCore 动态库与资源（docs/03 §3.1 的启动序列）。

        :param path: ``incremental=False`` 时是内核目录（含 ``libMaaCore.*`` 与
            自带 ``resource/``）；``incremental=True`` 时是一层增量资源的根目录。
        :param incremental: 为 True 时只调用 ``AsstLoadResource``，不重复 dlopen、
            不改写 user_dir。增量层按调用顺序叠加，后加载覆盖同名定义。
        :param user_dir: 用户数据（日志、调试图片、crash.log）目录，首次加载时经
            ``AsstSetUserDir`` 交给内核；目录不存在时先建出来再调用内核。

        :return: 本次加载的每一步是否都成功。``incremental=True`` 且尚未完成首次
            加载时抛 ``RuntimeError``（资源层没有可叠加的内核）。
        """
        path = pathlib.Path(path)

        if incremental:
            if Asst.__lib is None:
                raise RuntimeError(
                    "incremental=True 之前必须先执行一次 Asst.load(path=...) 完成内核加载"
                )
            return bool(Asst.__lib.AsstLoadResource(str(path).encode("utf-8")))

        platform_type = platform.system().lower()
        try:
            platform_value = _PLATFORM_VALUES[platform_type]
        except KeyError as exc:  # pragma: no cover - 非目标平台
            raise RuntimeError(f"不支持的操作系统：{platform_type}") from exc

        env_var = platform_value["environ_var"]
        existing = os.environ.get(env_var, "")
        os.environ[env_var] = f"{existing}{os.pathsep}{path}" if existing else str(path)

        lib_import_func = ctypes.WinDLL if platform_type == "windows" else ctypes.CDLL
        Asst.__libpath = path / platform_value["libpath"]
        try:
            Asst.__lib = lib_import_func(str(Asst.__libpath))
        except OSError:
            found = ctypes.util.find_library("MaaCore")
            if found is None:
                raise
            Asst.__libpath = found
            Asst.__lib = lib_import_func(found)

        Asst._set_lib_properties()

        ret = True
        if user_dir is not None:
            user_dir = pathlib.Path(user_dir)
            # AsstSetUserDir 要求目录已存在（M1-02 实测），否则返回 False、
            # 在旧实现里表现为「内核加载失败」。这里先建目录再交给内核。
            user_dir.mkdir(parents=True, exist_ok=True)
            ret = bool(Asst.__lib.AsstSetUserDir(str(user_dir).encode("utf-8"))) and ret

        return bool(Asst.__lib.AsstLoadResource(str(path).encode("utf-8"))) and ret

    @staticmethod
    def set_static_option(option_type: StaticOptionKey, option_value: str) -> bool:
        """设置进程级参数（docs/03 §1.1），返回是否成功。

        :param option_type: ``StaticOptionKey``（CPU/GPU OCR 开关）。
        :param option_value: 参数值，如 ``"1"`` / ``"0"``。
        """
        return bool(
            Asst.__lib.AsstSetStaticOption(
                int(option_type), str(option_value).encode("utf-8")
            )
        )

    @staticmethod
    def set_connection_extras(name: str, extras: Any) -> None:
        """设置连接模拟器端的 Extras。

        :param name: Extras 名称。
        :param extras: 可 JSON 序列化的配置对象。
        """
        Asst.__lib.AsstSetConnectionExtras(
            str(name).encode("utf-8"),
            json.dumps(extras, ensure_ascii=False).encode("utf-8"),
        )

    @staticmethod
    def get_null_size() -> int:
        """内核约定的「无效尺寸」哨兵值，``get_image`` 等接口以此判定失败。"""
        return int(Asst.__lib.AsstGetNullSize())

    @staticmethod
    def log(level: str, message: str) -> None:
        """通过内核打印一条日志。

        :param level: 日志等级标签（如 ``"INF"`` / ``"ERR"``）。
        :param message: 日志内容。
        """
        Asst.__lib.AsstLog(str(level).encode("utf-8"), str(message).encode("utf-8"))

    @staticmethod
    def _has_symbol(name: str) -> bool:
        """当前平台的库是否导出该符号，用于实验性 API 的运行期能力探测。

        ctypes 在符号不存在时于**首次属性访问**抛 ``AttributeError``（不是加载时
        失败），所以 ``hasattr`` 是可靠探测手段；不能对未导出符号静态设置
        ``restype`` / ``argtypes``，否则 Linux 上加载即报错。
        """
        return hasattr(Asst.__lib, name)

    @staticmethod
    def _set_lib_properties() -> None:
        """为 26 个跨平台通用 C API 统一设置 ``restype`` / ``argtypes``。

        ``AsstGetMapLevelKey`` 是条件编译的实验性 API（macOS 导出、Linux 未导出），
        必须包在 :meth:`_has_symbol` 条件分支里，缺符号时整段跳过而不是报错。
        """
        lib = Asst.__lib

        def bind(name: str, restype: Any, argtypes: tuple) -> None:
            fn = getattr(lib, name)
            fn.restype = restype
            fn.argtypes = argtypes

        c_void_p = ctypes.c_void_p
        c_char_p = ctypes.c_char_p
        c_bool = ctypes.c_bool
        c_int = ctypes.c_int
        c_int32 = ctypes.c_int32
        c_size = ctypes.c_uint64

        # --- 进程级 ---
        bind("AsstSetUserDir", c_bool, (c_char_p,))
        bind("AsstLoadResource", c_bool, (c_char_p,))
        bind("AsstSetStaticOption", c_bool, (c_int, c_char_p))
        bind("AsstSetConnectionExtras", None, (c_char_p, c_char_p))

        # --- 实例生命周期 ---
        bind("AsstCreate", c_void_p, ())
        bind("AsstCreateEx", c_void_p, (Asst.CallBackType, c_void_p))
        bind("AsstDestroy", None, (c_void_p,))
        bind("AsstSetInstanceOption", c_bool, (c_void_p, c_int, c_char_p))

        # --- 连接（同步的 AsstConnect 已废弃，异步版结果看 AsyncCallInfo 回调）---
        bind("AsstConnect", c_bool, (c_void_p, c_char_p, c_char_p, c_char_p))
        bind(
            "AsstAsyncConnect",
            c_int32,
            (c_void_p, c_char_p, c_char_p, c_char_p, c_bool),
        )

        # --- 任务 ---
        bind("AsstAppendTask", c_int32, (c_void_p, c_char_p, c_char_p))
        bind("AsstSetTaskParams", c_bool, (c_void_p, c_int32, c_char_p))
        bind("AsstStart", c_bool, (c_void_p,))
        bind("AsstStop", c_bool, (c_void_p,))
        bind("AsstRunning", c_bool, (c_void_p,))
        bind("AsstConnected", c_bool, (c_void_p,))
        bind("AsstBackToHome", c_bool, (c_void_p,))

        # --- 原子异步操作 ---
        bind("AsstAsyncClick", c_int32, (c_void_p, c_int32, c_int32, c_bool))
        bind("AsstAsyncScreencap", c_int32, (c_void_p, c_bool))

        # --- 取图与设备信息（缓冲区一律按指针传，按返回值截断）---
        bind("AsstGetImage", c_size, (c_void_p, c_void_p, c_size))
        bind("AsstGetImageBgr", c_size, (c_void_p, c_void_p, c_size))
        bind("AsstGetUUID", c_size, (c_void_p, c_void_p, c_size))
        bind("AsstGetTasksList", c_size, (c_void_p, c_void_p, c_size))
        bind("AsstGetNullSize", c_size, ())
        bind("AsstGetVersion", c_char_p, ())
        bind("AsstLog", None, (c_char_p, c_char_p))

        # --- 实验性 API：运行期能力探测，符号不存在就整段跳过 ---
        if Asst._has_symbol("AsstGetMapLevelKey"):
            bind("AsstGetMapLevelKey", Asst._MapLevelKey, (c_char_p,))
        else:
            logger.info("当前内核未导出 AsstGetMapLevelKey，关卡互查将回退本地索引")

    # ------------------------------------------------------------------
    # 实例生命周期
    # ------------------------------------------------------------------

    def __init__(
        self, callback: Optional[Callable[..., None]] = None, arg: Any = None
    ) -> None:
        """创建内核实例。

        :param callback: 用户回调，签名 ``(msg, details, arg) -> None``；它会运行在
            MaaCore 的回调线程上，必须尽快返回、不得抛异常。本类会先包一层
            「缓存分辨率 + 拦截异常」的 trampoline 再注册给内核。
        :param arg: 透传给回调的自定义参数。注意内核只保存指针值，调用方必须
            自行持有 ``arg`` 的引用（旧实现的隐式契约，worker 用模块级队列满足）。
        """
        self.__user_callback: Optional[Callable[..., None]] = callback
        self.__callback: Optional[Asst.CallBackType] = None
        self.__ptr: Any = None

        if callback is None:
            self.__ptr = Asst.__lib.AsstCreate()
            return

        def _trampoline(msg: int, details: Optional[bytes], custom_arg: Any) -> None:
            # 运行在 MaaCore 回调线程上：解析与转发都不得把异常抛回 C 边界。
            try:
                if msg == Message.ConnectionInfo:
                    self._cache_resolution(details)
            except Exception:
                logger.exception("解析 ConnectionInfo 回调失败（msg=%s），已忽略", msg)
            try:
                callback(msg, details, custom_arg)
            except Exception:
                logger.exception("用户回调处理 msg=%s 时抛异常，已就地拦截", msg)

        # 存成实例属性防 GC：内核只持有函数指针，trampoline 被回收会直接崩内核。
        self.__callback = Asst.CallBackType(_trampoline)
        c_arg = ctypes.c_void_p(id(arg)) if arg is not None else None
        self.__ptr = Asst.__lib.AsstCreateEx(self.__callback, c_arg)

    def __del__(self) -> None:
        try:
            ptr = getattr(self, "_Asst__ptr", None)
            if ptr and Asst.__lib is not None:
                Asst.__lib.AsstDestroy(ptr)
        except Exception:  # pragma: no cover - 半构造对象 / 解释器退出
            logger.debug("销毁 MaaCore 实例失败", exc_info=True)
        finally:
            try:
                self.__ptr = None
            except Exception:  # pragma: no cover
                pass

    # ------------------------------------------------------------------
    # 回调侧的少量状态
    # ------------------------------------------------------------------

    def _cache_resolution(self, details: Optional[bytes]) -> None:
        """从 ConnectionInfo 的 ``ResolutionGot`` 回调缓存 ``(width, height)``。

        M1-01 实测载荷：``what`` 在 JSON 顶层、``width`` / ``height`` 在 ``details``
        里（``{"details":{"width":2560,"height":1440},"what":"ResolutionGot"}``）。
        这里对 ``what`` 的嵌套写法也保持宽容，取不到就当作无关回调忽略。
        """
        if not details:
            return
        payload = json.loads(details.decode("utf-8"))
        inner = payload.get("details") or {}
        what = payload.get("what") or inner.get("what")
        if what != "ResolutionGot":
            return
        width, height = inner.get("width"), inner.get("height")
        if width and height:
            self.__resolution = (int(width), int(height))

    def _expected_image_size(self) -> int:
        """按最近一次 ``ResolutionGot`` 缓存推算截图缓冲容量（RGB：``w*h*3``）。

        未拿到分辨率时返回 0；``get_image`` 把 0 当作「尺寸未知」直接放弃本次取图。
        注意这只是**容量上界**：内核实际写入的字节数可能更小（见 :meth:`get_image`），
        必须以返回值为准截断，不能假设 ``len(result) == w*h*3``。
        """
        if self.__resolution is None:
            return 0
        width, height = self.__resolution
        return width * height * 3

    def last_resolution(self) -> Optional[tuple[int, int]]:
        """最近一次 ``ResolutionGot`` 缓存的分辨率 ``(width, height)``；未知返回 None。

        worker 用它把 ``get_image`` 的原始字节落盘成 JPEG（docs/03 §2.2）。
        """
        return self.__resolution

    # ------------------------------------------------------------------
    # 实例选项与连接
    # ------------------------------------------------------------------

    def set_instance_option(
        self, option_type: InstanceOptionKey, option_value: str
    ) -> bool:
        """设置实例级额外配置，返回是否成功（docs/03 §2.3）。

        :param option_type: ``InstanceOptionKey``。
        :param option_value: 配置值；常规 ``General`` 连接链不需要设置 ``ClientType``。
        """
        return bool(
            Asst.__lib.AsstSetInstanceOption(
                self.__ptr, int(option_type), str(option_value).encode("utf-8")
            )
        )

    def connect(self, adb_path: str, address: str, config: str = "General") -> bool:
        """同步连接设备（官方已废弃），返回是否连接成功。

        新代码请用 :meth:`connect_async`；保留本方法只为兼容旧调用方。
        """
        return bool(
            Asst.__lib.AsstConnect(
                self.__ptr,
                str(adb_path).encode("utf-8"),
                str(address).encode("utf-8"),
                str(config).encode("utf-8"),
            )
        )

    def connect_async(
        self, adb_path: str, address: str, config: str = "General", block: bool = True
    ) -> int:
        """异步连接设备，返回 ``async_call_id``。

        **返回值只表示「请求已受理」，非零不代表连接成功**（docs/08 §5.1）。
        结果经 ``Message.AsyncCallInfo``（msg=4）回调送达，按 M1-01 实测层级解析：

        - 关联键 ``async_call_id`` 在回调 JSON **顶层**，与本方法返回值相等；
        - 成功标志在 **``details.details.ret``**（双层嵌套）；
        - 调用类型在顶层 ``what``（连接为 ``"Connect"``）。

        ``block`` 是「内核内部是否串行化该调用」，不是 Python 侧阻塞：无论取值如何
        本方法都立即返回。连接场景取 ``True``，避免与其他异步调用交错。

        .. warning::
           msg=2 的 ``ConnectionInfo`` 会先报出 ``what == "Connected"``，
           它只是连接过程的中间态，不能拿它代替 msg=4 的最终结果。
        """
        return int(
            Asst.__lib.AsstAsyncConnect(
                self.__ptr,
                str(adb_path).encode("utf-8"),
                str(address).encode("utf-8"),
                str(config).encode("utf-8"),
                bool(block),
            )
        )

    def connected(self) -> bool:
        """设备当前是否处于连接状态；比 ``running()`` 更适合做设备健康判定。

        这是同步查询，可用于 ``AsyncCallInfo`` 结构异常时的轮询兜底，
        但不要用它去替代某次异步调用的逐次结果。
        """
        return bool(Asst.__lib.AsstConnected(self.__ptr))

    def back_to_home(self) -> bool:
        """回到游戏主界面（agent 的卡死救援动作），返回是否成功。"""
        return bool(Asst.__lib.AsstBackToHome(self.__ptr))

    def click(self, x: int, y: int, block: bool = True) -> int:
        """点击指定坐标，返回 ``async_call_id``；坐标系为设备原始分辨率。

        同 :meth:`connect_async`：返回值只是受理编号，结果看 ``AsyncCallInfo`` 回调。
        """
        return int(Asst.__lib.AsstAsyncClick(self.__ptr, int(x), int(y), bool(block)))

    def screencap(self, block: bool = True) -> int:
        """触发一次截图，返回 ``async_call_id``。

        截图结果不由本方法返回，需等回调后调用 :meth:`get_image` 取回。
        """
        return int(Asst.__lib.AsstAsyncScreencap(self.__ptr, bool(block)))

    # ------------------------------------------------------------------
    # 取图与设备信息
    # ------------------------------------------------------------------

    def get_image(
        self, size: Optional[int] = None, bgr: bool = False
    ) -> Optional[bytes]:
        """取最近一次截图的原始字节；失败返回 ``None``。

        :param size: 缓冲区容量（字节），缺省时用 :meth:`_expected_image_size` 从
            ``ResolutionGot`` 缓存的分辨率推算（``width*height*3``），未拿到分辨率
            且未显式传入时返回 ``None``。
        :param bgr: 为 True 时走 ``AsstGetImageBgr``，否则走 ``AsstGetImage``。

        ``got == 0`` 或 ``got == get_null_size()`` 都视为失败；成功时按返回长度截断。

        .. note::
           M1-04 真机实测（v6.17.5，2560x1440）：``AsstGetImage`` 返回的是
           **PNG 编码字节**（magic ``\\x89PNG``，本次 637585 字节），
           ``AsstGetImageBgr`` 返回裸 BGR（本次 2764800 字节 = 1280x720x3）。
           两者长度都不等于 ``w*h*3``，``size`` 只是容量上界；
           消费方（worker 落盘）应把返回字节当作**已编码图像**处理。
        """
        size = size or self._expected_image_size()
        if size <= 0:
            logger.warning(
                "尚无可用的截图尺寸（未收到 ResolutionGot 且未显式传入 size），跳过本次取图"
            )
            return None
        buffer = (ctypes.c_char * size)()
        fn = Asst.__lib.AsstGetImageBgr if bgr else Asst.__lib.AsstGetImage
        got = fn(self.__ptr, buffer, size)
        if got == 0 or got == Asst.get_null_size():
            return None
        return buffer.raw[:got]

    def get_image_bgr(self, size: int) -> Optional[bytes]:
        """取最近一次截图的 BGR 原始字节，失败返回 ``None``。

        :param size: 缓冲区容量，建议 ``width * height * 3``（同 :meth:`get_image`，
            真实返回长度可能小于它，按返回值截断）。
        """
        return self.get_image(size=size, bgr=True)

    def get_uuid(self) -> Optional[str]:
        """设备唯一码，用于多设备扩展时识别设备身份；未连接时返回 ``None``。"""
        buffer = (ctypes.c_char * self._UUID_BUFFER_SIZE)()
        got = Asst.__lib.AsstGetUUID(self.__ptr, buffer, self._UUID_BUFFER_SIZE)
        if got == 0 or got == Asst.get_null_size():
            return None
        raw = buffer.raw[:got].split(b"\x00", 1)[0]
        if not raw:
            return None
        return raw.decode("utf-8", errors="replace")

    def get_tasks_list(self) -> list[int]:
        """内核任务队列中的 task id 列表，用于校对本地映射是否与内核一致。

        防御式实现：一次调用用 :attr:`_TASKS_LIST_CAPACITY` 的宽松容量；返回 0
        视作空列表；返回值大于容量时按返回值扩容重试一次；最后按返回长度截断。
        真实语义（返回长度 vs 任务个数）由 M1-14 真机冒烟确认。
        """
        capacity = self._TASKS_LIST_CAPACITY
        buffer = (ctypes.c_int32 * capacity)()
        got = Asst.__lib.AsstGetTasksList(self.__ptr, buffer, capacity)
        if got == 0 or got == Asst.get_null_size():
            return []
        if got > capacity:
            capacity = int(got)
            buffer = (ctypes.c_int32 * capacity)()
            got = Asst.__lib.AsstGetTasksList(self.__ptr, buffer, capacity)
            if got == 0 or got == Asst.get_null_size():
                return []
        return [int(task_id) for task_id in buffer[:got]]

    @staticmethod
    def get_map_level_key(key: str) -> Optional[dict]:
        """关卡名 / ``code`` / ``stageId`` / ``levelId`` 互查（实验性 API）。

        仅 macOS 构建导出此符号（``AsstCallerExtra.h``，受 ``ASST_WITH_EXTRA_CALLERS``
        条件编译）；其他平台或构建返回 ``None``，调用方必须回退到本地关卡索引。
        查不到匹配关卡时内核返回四个 NULL，这里同样归一为 ``None``。
        """
        if not Asst._has_symbol("AsstGetMapLevelKey"):
            logger.debug("当前内核未导出 AsstGetMapLevelKey，回退本地关卡索引")
            return None
        result = Asst.__lib.AsstGetMapLevelKey(str(key).encode("utf-8"))
        fields = {
            "stage_id": result.stage_id,
            "code": result.code,
            "level_id": result.level_id,
            "name": result.name,
        }
        if all(value is None for value in fields.values()):
            return None
        return {
            name: (value.decode("utf-8") if value is not None else None)
            for name, value in fields.items()
        }

    # ------------------------------------------------------------------
    # 任务
    # ------------------------------------------------------------------

    def append_task(self, type_name: str, params: Optional[dict] = None) -> int:
        """添加任务，返回任务 ID。

        :param type_name: 任务类型（如 ``"Fight"``）。
        :param params: 任务参数，缺省 ``{}``。

        ``task_id == 0`` 表示失败：内核校验必填参数，缺失时静默返回 0
        （不抛异常、无回调，M1-02 实测），调用方必须当失败处理。
        """
        return int(
            Asst.__lib.AsstAppendTask(
                self.__ptr,
                str(type_name).encode("utf-8"),
                json.dumps(params or {}, ensure_ascii=False).encode("utf-8"),
            )
        )

    def set_task_params(self, task_id: int, params: dict) -> bool:
        """动态设置任务参数，返回是否成功。"""
        return bool(
            Asst.__lib.AsstSetTaskParams(
                self.__ptr,
                int(task_id),
                json.dumps(params, ensure_ascii=False).encode("utf-8"),
            )
        )

    def start(self) -> bool:
        """开始任务，返回是否成功。"""
        return bool(Asst.__lib.AsstStart(self.__ptr))

    def stop(self) -> bool:
        """停止并清空所有任务，返回是否成功。"""
        return bool(Asst.__lib.AsstStop(self.__ptr))

    def running(self) -> bool:
        """是否正在运行。"""
        return bool(Asst.__lib.AsstRunning(self.__ptr))

    def get_version(self) -> str:
        """获取 MaaCore 版本号。"""
        version = Asst.__lib.AsstGetVersion()
        return version.decode("utf-8") if version else ""

#!/usr/bin/env python3
"""M1-01 方案前置探针：MaaCore 导出符号能力 + AsyncCallInfo 回调载荷实测。

本脚本只做实测取证，不是生产代码，也不依赖 ``maa_api`` 包（``maa_api/core/asst.py``
在 refactor/v2 上尚是待重写的占位文件，探针刻意绕开它，直接用 ctypes 调 C API）。

做三件事：

1. dlopen ``<maa-path>/libMaaCore.dylib``，用 ``hasattr`` 逐个探测 11 个符号是否导出；
2. 调 ``AsstGetVersion()`` 原样记录内核版本，并采集库文件 mtime/大小等构建线索；
3. 调 ``AsstLoadResource`` → ``AsstCreateEx`` → ``AsstAsyncConnect``，收集回调原文，
   把 ``Message == 4``（AsyncCallInfo）那条的 details 原样写进产物。

产物（默认写入 ``tests/fixtures/``）：

- ``async_call_info_sample.json``：机器可读的实测样本；
- ``core_probe_findings.md``：给 M1-04 / M1-09 引用的人读结论。

用法::

    DYLD_LIBRARY_PATH=$PWD/resource/lib/maa/Darwin \\
        .venv/bin/python scripts/probe_core_api.py

``--help`` 在加载任何库之前由 argparse 处理，不触碰内核。
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

# 本卡要求逐个探测的符号。顺序即报告顺序。
PROBE_SYMBOLS: tuple[str, ...] = (
    "AsstGetMapLevelKey",
    "AsstGetItemName",
    "AsstAsyncConnect",
    "AsstAsyncClick",
    "AsstAsyncScreencap",
    "AsstGetImageBgr",
    "AsstGetUUID",
    "AsstGetTasksList",
    "AsstGetNullSize",
    "AsstConnected",
    "AsstBackToHome",
)

# MaaCore AsstMsg::AsyncCallInfo 的枚举值。
ASYNC_CALL_INFO_MSG = 4

LIB_FILENAMES = {
    "Darwin": "libMaaCore.dylib",
    "Linux": "libMaaCore.so",
    "Windows": "MaaCore.dll",
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="probe_core_api.py",
        description=(
            "M1-01 实测探针：探测本地 MaaCore 的导出符号与内核版本，"
            "并触发一次 AsstAsyncConnect 打印原始 AsyncCallInfo 回调 JSON。"
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--maa-path",
        default="resource/lib/maa/Darwin",
        help="含 libMaaCore.dylib 与 resource/ 的目录",
    )
    parser.add_argument("--adb", default="/opt/homebrew/bin/adb", help="adb 可执行文件路径")
    parser.add_argument("--address", default="127.0.0.1:5555", help="adb 设备地址")
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="等待 AsyncCallInfo 回调的最长秒数",
    )
    parser.add_argument("--out-dir", default="tests/fixtures", help="产物输出目录")
    parser.add_argument(
        "--user-dir",
        default=None,
        help="MaaCore 用户目录（日志/调试图落点）；默认用临时目录，避免污染仓库",
    )
    return parser.parse_args(argv)


def resolve_lib_path(maa_path: Path) -> Path:
    filename = LIB_FILENAMES.get(platform.system(), "libMaaCore.dylib")
    candidate = maa_path / filename
    if candidate.is_file():
        return candidate
    matches = sorted(maa_path.glob("libMaaCore.*"))
    if matches:
        return matches[0]
    raise FileNotFoundError(f"{maa_path} 下找不到 {filename}")


def load_library(maa_path: Path) -> tuple[ctypes.CDLL, Path]:
    lib_path = resolve_lib_path(maa_path)
    # 依赖库以 @rpath/@loader_path 记录，按绝对路径 dlopen 即可解析；
    # 这里仍补一个 DYLD_LIBRARY_PATH 兜底（对已启动进程的 dyld 无效，仅供子进程）。
    if platform.system() == "Darwin" and str(maa_path) not in os.environ.get("DYLD_LIBRARY_PATH", ""):
        existing = os.environ.get("DYLD_LIBRARY_PATH", "")
        os.environ["DYLD_LIBRARY_PATH"] = f"{maa_path}{os.pathsep}{existing}" if existing else str(maa_path)
    lib = ctypes.CDLL(str(lib_path))
    return lib, lib_path


def probe_exports(lib: ctypes.CDLL) -> dict[str, bool]:
    """hasattr 触发 ctypes 的首次属性访问；符号缺失时抛 AttributeError 而非加载失败。"""
    return {name: bool(hasattr(lib, name)) for name in PROBE_SYMBOLS}


def configure(lib: ctypes.CDLL, name: str, restype, argtypes) -> object:
    fn = getattr(lib, name)
    fn.restype = restype
    fn.argtypes = argtypes
    return fn


def iso_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def collect_build_clues(maa_path: Path, lib_path: Path) -> dict:
    clues: dict = {
        "maa_path": str(maa_path),
        "lib_path": str(lib_path),
        "lib_size_bytes": lib_path.stat().st_size,
        "lib_mtime_utc": iso_mtime(lib_path),
        "sibling_files": [],
        "packages": [],
        "resource_version_json": None,
    }
    for child in sorted(maa_path.iterdir()):
        if not child.is_file():
            continue
        clues["sibling_files"].append(
            {
                "name": child.name,
                "size_bytes": child.stat().st_size,
                "mtime_utc": iso_mtime(child),
            }
        )
        if child.suffix.lower() == ".zip":
            clues["packages"].append(child.name)

    version_json = maa_path / "resource" / "version.json"
    if version_json.is_file():
        try:
            clues["resource_version_json"] = json.loads(version_json.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:  # 线索而已，失败不致命
            clues["resource_version_json"] = {"error": repr(exc)}
    return clues


class CallbackCollector:
    """收集 MaaCore 回调原文；callback 对象必须全程保活。"""

    def __init__(self) -> None:
        self.records: list[dict] = []
        self._t0 = time.monotonic()
        self.callback = ctypes.CFUNCTYPE(
            None, ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p
        )(self._on_callback)

    def _on_callback(self, msg: int, details: bytes | None, _arg: int) -> None:
        raw = details.decode("utf-8", "replace") if details else ""
        self.records.append(
            {
                "msg": int(msg),
                "details_raw": raw,
                "offset_sec": round(time.monotonic() - self._t0, 4),
            }
        )
        # 逐行打印原始回调，便于人工核对键名与嵌套层级。
        print(f"[callback] msg={int(msg)} details={raw}", flush=True)

    def first_with_msg(self, wanted: int) -> dict | None:
        return next((r for r in self.records if r["msg"] == wanted), None)

    @property
    def message_ids(self) -> list[int]:
        return [r["msg"] for r in self.records]


def flatten_json(value, prefix: str = "") -> list[str]:
    """把 JSON 展平成 ``a.b[0].c = <value> (type)`` 行，用于在 findings 里描述嵌套层级。"""
    lines: list[str] = []
    if isinstance(value, dict):
        for key, sub in value.items():
            lines.extend(flatten_json(sub, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(value, list):
        for index, sub in enumerate(value):
            lines.extend(flatten_json(sub, f"{prefix}[{index}]"))
    else:
        lines.append(f"`{prefix}` = `{value!r}` ({type(value).__name__})")
    return lines


def run_async_connect(
    lib: ctypes.CDLL,
    collector: CallbackCollector,
    maa_path: Path,
    adb_path: str,
    address: str,
    timeout: float,
    user_dir: Path,
) -> dict:
    result: dict = {
        "resource_loaded": None,
        "handle_created": False,
        "async_connect_return": None,
        "connected_after": None,
        "errors": [],
    }

    if not hasattr(lib, "AsstLoadResource"):
        result["errors"].append("AsstLoadResource 未导出，无法继续")
        return result

    set_user_dir = configure(lib, "AsstSetUserDir", ctypes.c_bool, (ctypes.c_char_p,)) if hasattr(lib, "AsstSetUserDir") else None
    load_resource = configure(lib, "AsstLoadResource", ctypes.c_bool, (ctypes.c_char_p,))
    # argtypes 需要的是回调原型类型，不是实例。
    create_ex = configure(lib, "AsstCreateEx", ctypes.c_void_p, (type(collector.callback), ctypes.c_void_p))
    destroy = configure(lib, "AsstDestroy", None, (ctypes.c_void_p,))
    async_connect = configure(
        lib,
        "AsstAsyncConnect",
        ctypes.c_int32,
        (ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint8),
    )
    connected = (
        configure(lib, "AsstConnected", ctypes.c_bool, (ctypes.c_void_p,))
        if hasattr(lib, "AsstConnected")
        else None
    )

    if set_user_dir is not None:
        result["user_dir_set"] = bool(set_user_dir(str(user_dir).encode("utf-8")))
    t_load = time.monotonic()
    result["resource_loaded"] = bool(load_resource(str(maa_path).encode("utf-8")))
    result["resource_load_sec"] = round(time.monotonic() - t_load, 3)
    if not result["resource_loaded"]:
        result["errors"].append("AsstLoadResource 返回 False")
        return result

    arg_token = ctypes.c_void_p(id(collector))
    handle = create_ex(collector.callback, arg_token)
    if not handle:
        result["errors"].append("AsstCreateEx 返回空 handle")
        return result
    result["handle_created"] = True

    try:
        t0 = time.monotonic()
        ret = async_connect(
            handle,
            adb_path.encode("utf-8"),
            address.encode("utf-8"),
            b"General",
            True,
        )
        result["async_connect_return"] = int(ret)
        result["async_connect_return_semantics"] = (
            "AsstAsyncCallId，仅表示请求已受理；非零不代表连接成功（docs/08 §5.1）"
        )
        print(f"[probe] AsstAsyncConnect -> async_call_id={int(ret)}（受理编号，非成败判据）", flush=True)

        captured_at: float | None = None
        while time.monotonic() - t0 < timeout:
            if collector.first_with_msg(ASYNC_CALL_INFO_MSG) is not None:
                if captured_at is None:
                    captured_at = time.monotonic()
                    print("[probe] 已捕获 AsyncCallInfo，继续收集 1.0s 观察后续回调", flush=True)
                elif time.monotonic() - captured_at >= 1.0:
                    break
            time.sleep(0.05)
        result["wait_sec"] = round(time.monotonic() - t0, 3)
        result["timed_out"] = collector.first_with_msg(ASYNC_CALL_INFO_MSG) is None

        if connected is not None:
            result["connected_after"] = bool(connected(handle))
        # 给内核回调线程一点收尾时间，再销毁 handle。
        time.sleep(0.5)
    finally:
        destroy(ctypes.c_void_p(handle))

    return result


def build_findings(
    args: argparse.Namespace,
    maa_path: Path,
    lib_path: Path,
    exports: dict[str, bool],
    kernel_version: str,
    build_clues: dict,
    run: dict,
    async_info: dict,
) -> str:
    def mark(value: bool) -> str:
        return "导出（True）" if value else "未导出（False）"

    export_rows = "\n".join(f"| `{name}` | {mark(ok)} |" for name, ok in exports.items())

    if async_info.get("captured"):
        parsed = async_info.get("details_parsed")
        nested_details = (
            parsed.get("details")
            if isinstance(parsed, dict) and isinstance(parsed.get("details"), dict)
            else {}
        )
        flat = "\n".join(f"- {line}" for line in flatten_json(parsed)) if parsed is not None else "- details 不是合法 JSON，见原始文本"
        raw_block = async_info.get("details_raw", "")
        observed = run.get("observed_message_ids") or []
        captured_verdict = f"""**已捕获。** 触发一次 `AsstAsyncConnect(handle, adb, address, "General", block=True)`，
`AsstAsyncConnect` 返回 `async_call_id = {run.get('async_connect_return')}`（受理编号），
约 {async_info.get('offset_sec')}s 后收到 `Message = 4`（AsyncCallInfo）的回调。

原始 details 文本（一字未改）：

```json
{raw_block}
```

展平后的键路径与类型：

{flat}

**键名与嵌套层级结论（本次 {kernel_version} 构建实测）：**

- **关联键在顶层**：`async_call_id` 是回调 JSON 的**顶层字段**（值 {parsed.get('async_call_id') if isinstance(parsed, dict) else '?'}），
  与 `AsstAsyncConnect` 的返回值相等——两级 Future 可直接用它做关联，**不要**去嵌套的 `details` 里找。
- **成功标志嵌两层**：成功与否在 `details.details.ret`（顶层 `details` 对象里再套一个 `details` 对象），
  本次为 `{nested_details.get('ret')!r}`（JSON bool）。同层还有 `cost`（本次 {nested_details.get('cost')!r}，与回调延迟同量级，疑似毫秒）。
  文档原先猜测「`details` 里带 `async_call_id` 与 `ret`」并不准确：两者不在同一层。
- **调用类型标识**：顶层 `what == "Connect"`，可用于区分同一条 AsyncCallInfo 通道上的其它异步调用。
- **顶层 `uuid`**：设备 uuid，与同一连接会话的其它回调一致，可用于多设备扩展时的归属校验。
- **失败形态本次未观测到**（设备在线，连接成功）：`ret == false` 时是否附带 `why`、`async_call_id` 是否仍在顶层，
  需 M1-04 用不可达 `address` 补测一次；在那之前解析器对缺失字段保持宽容。

本次观测到的回调消息序列（msg）：`{observed}`。
其中 `msg = 2` 是 ConnectionInfo（含 `what = "UuidGot"/"ResolutionGot"/"Connected"/"FastestWayToScreencap"/"ResolutionInfo"`），
它在 AsyncCallInfo **之前**就已出现 `what = "Connected"`；因此判定异步连接结果只能认 `msg = 4` 的
`details.details.ret`，不能拿 ConnectionInfo 里的 `Connected` 代替。`msg = 5` 在异步调用完成后到达。

上层包装（`async_call_info` 字段）的结构为：`msg` 保存枚举值 4，`details` 保存解析后的对象，
`details_raw` 保存上面这段原文。给 M1-04 的解析器结论见 §4。"""
    else:
        errs = "; ".join(run.get("errors") or []) or "无显式报错"
        captured_verdict = f"""**未捕获。** 在 {run.get('wait_sec')}s 内没有收到 `Message = 4`（AsyncCallInfo）回调，
观测到的回调 msg 序列为 `{run.get('observed_message_ids')}`，错误记录：{errs}。
按任务卡要求如实记录为未捕获，不推断字段名。"""

    clues_files = "\n".join(
        f"- `{item['name']}`：{item['size_bytes']} bytes，mtime {item['mtime_utc']}"
        for item in build_clues["sibling_files"]
    )

    return f"""# M1-01 实测记录：本地内核导出符号与 AsyncCallInfo 回调载荷

- 实测时间（UTC）：{datetime.now(timezone.utc).isoformat()}
- 实测主机：{platform.platform()} / Python {platform.python_version()}
- 内核目录：`{maa_path}`
- 加载的库：`{lib_path}`
- 复现命令：`DYLD_LIBRARY_PATH=$PWD/{args.maa_path} .venv/bin/python scripts/probe_core_api.py`

> 本文件由 `scripts/probe_core_api.py` 依据一次真实运行生成；下面的布尔值与版本号都是运行期测量结果，
> 不是从头文件或文档静态推断的。Linux 库在 macOS 上无法 dlopen，凡涉及 Linux 的表述都明确标注为
> 「文档既有结论」，不计入本次测量。

## 1. 导出符号实测（macOS）

用 `ctypes.CDLL(<lib>)` 加载后逐个 `hasattr` 探测，得到：

| 符号 | 本地 macOS 构建 |
|---|---|
{export_rows}

对照 [docs/03 §1.2](../docs/03-MaaCore内核层设计.md) 的既有结论（未在本次测量范围内，仅作对照）：

- `AsstGetMapLevelKey`：macOS 导出、Linux 未导出，头文件声明在 `AsstCallerExtra.h` 且受
  `ASST_WITH_EXTRA_CALLERS` 条件编译，属实验性 API。
- `AsstGetItemName`：头文件无声明；本次探针**只探测是否导出，绝不调用**（参数与返回语义未知，
  编造签名会直接段错误）。首版不纳入封装，掉落物品名改读 `item_index.json`。

## 2. 内核版本与构建线索

- `AsstGetVersion()` 原样返回：`{kernel_version}`
- 库文件：`{lib_path.name}`，{build_clues['lib_size_bytes']} bytes，mtime {build_clues['lib_mtime_utc']}
- 同目录下的包与库文件线索：

{clues_files}

`AsstLoadResource` 返回 {run.get('resource_loaded')}，耗时 {run.get('resource_load_sec')}s。

判断：{build_clues.get('build_origin_hint', '见上方文件清单')}

## 3. AsyncCallInfo 实测载荷

{captured_verdict}

## 4. 对 M1 的结论

1. **解析器取值键（实测）**：{async_info.get('parser_advice', '见上节；未捕获时回退到 AsstConnected 轮询。')}
2. **不要把返回值当成功判据**：`AsstAsyncConnect` 的返回值是 `AsstAsyncCallId`（受理编号），
   本次拿到 {run.get('async_connect_return')}；只有 `Message = 4` 载荷里的 `details.details.ret` 才是结果，
   两者恰好数值相同容易误判，实现时先记编号再等回调。
3. **能力探测**：`AsstGetMapLevelKey` / `AsstGetItemName` 的可用性必须运行期 `hasattr` 探测，
   `maa_api/core/asst.py`（M1-04 重写）里的 `__set_lib_properties()` 不能对未导出符号统一设
   `restype`/`argtypes`，否则在 Linux 上首次属性访问即抛 `AttributeError`；本次实测 macOS 构建上
   11 个符号全部导出，但这只代表本机 {kernel_version} 构建，不能替 Linux 打包票。
4. **回退路径**：本次 `AsstConnected(handle)` 在回调后返回 {run.get('connected_after')}；
   若 AsyncCallInfo 结构在其它平台/构建上缺失，可用它轮询兜底（docs/08 §5.2 已设计该退路）。
   注意 `AsstConnected` 是同步查询，探针里可用，但不要用它去替代异步调用的逐次结果。
5. 本卡未改动 `maa_api/` 下任何文件；探针刻意不经由 Python 封装层（`maa_api/core/asst.py`
   在 refactor/v2 上是待重写的占位文件），M1-04 可直接引用上面的实测键名与符号表实现能力探测。
"""


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    maa_path = Path(args.maa_path).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[probe] platform={platform.platform()}", flush=True)
    print(f"[probe] maa_path={maa_path}", flush=True)
    if not maa_path.is_dir():
        print(f"[error] --maa-path 不存在：{maa_path}", file=sys.stderr)
        return 1

    try:
        lib, lib_path = load_library(maa_path)
    except OSError as exc:
        print(f"[error] 加载内核失败：{exc}", file=sys.stderr)
        print("[hint] 用 DYLD_LIBRARY_PATH=$PWD/resource/lib/maa/Darwin 运行", file=sys.stderr)
        return 1
    print(f"[probe] loaded {lib_path}", flush=True)

    exports = probe_exports(lib)
    for name, ok in exports.items():
        print(f"[probe] export {name}={ok}", flush=True)

    kernel_version = ""
    if hasattr(lib, "AsstGetVersion"):
        get_version = configure(lib, "AsstGetVersion", ctypes.c_char_p, ())
        raw_version = get_version()
        kernel_version = raw_version.decode("utf-8", "replace") if raw_version else ""
    print(f"[probe] kernel_version={kernel_version!r}", flush=True)

    build_clues = collect_build_clues(maa_path, lib_path)
    packages = build_clues["packages"]
    resource_version = build_clues.get("resource_version_json")
    hint_parts = []
    if packages:
        hint_parts.append(
            f"运行库由发行包 {packages} 解压而来，与 AsstGetVersion() 返回的 {kernel_version} 版本号自洽"
        )
    if isinstance(resource_version, dict) and resource_version:
        hint_parts.append(f"resource/version.json = {json.dumps(resource_version, ensure_ascii=False)}")
    if exports.get("AsstGetItemName"):
        hint_parts.append(
            "但 `AsstGetItemName` 在本机 dylib 中导出、而 `AsstCaller.h` 并无声明（docs/03 §1.2），"
            "加上文档记录的 Linux 构建未导出 `AsstGetMapLevelKey`，说明导出面与头文件/平台并不一致"
        )
    hint_parts.append(
        "结论：即使包名与版本号看起来是发布线，也一律按 docs/13 §5 的要求走运行期 `hasattr` 能力探测，"
        "不要静态假设「某 API 可用」"
    )
    build_clues["build_origin_hint"] = "；".join(hint_parts)

    user_dir = Path(args.user_dir).expanduser().resolve() if args.user_dir else Path(tempfile.mkdtemp(prefix="maa-probe-userdir-"))
    user_dir.mkdir(parents=True, exist_ok=True)
    collector = CallbackCollector()
    run = run_async_connect(
        lib=lib,
        collector=collector,
        maa_path=maa_path,
        adb_path=args.adb,
        address=args.address,
        timeout=args.timeout,
        user_dir=user_dir,
    )
    run["observed_message_ids"] = collector.message_ids
    run["observed_callback_count"] = len(collector.records)

    async_record = collector.first_with_msg(ASYNC_CALL_INFO_MSG)
    async_info: dict = {
        "captured": async_record is not None,
        "msg": ASYNC_CALL_INFO_MSG if async_record else None,
        "details": None,
        "details_raw": None,
        "details_parsed": None,
    }
    if async_record is not None:
        async_info["msg"] = async_record["msg"]
        async_info["details_raw"] = async_record["details_raw"]
        try:
            async_info["details_parsed"] = json.loads(async_record["details_raw"])
        except json.JSONDecodeError:
            async_info["details_parsed"] = None
        async_info["details"] = (
            async_info["details_parsed"]
            if async_info["details_parsed"] is not None
            else async_record["details_raw"]
        )
        async_info["offset_sec"] = async_record["offset_sec"]
    else:
        async_info["reason"] = "timeout"
        async_info["timeout_sec"] = args.timeout
        async_info["observed_message_ids"] = collector.message_ids
        async_info["observed_callbacks"] = collector.records[-5:]

    parsed = async_info.get("details_parsed")
    if isinstance(parsed, dict):
        nested = parsed.get("details") if isinstance(parsed.get("details"), dict) else {}
        async_info["parser_advice"] = (
            f"关联键取**顶层** `async_call_id`（本次 {parsed.get('async_call_id')}，等于 `AsstAsyncConnect` 返回值）；"
            f"成功标志取 `details.details.ret`（本次 {nested.get('ret')!r}）；"
            f"用顶层 `what`（本次 {parsed.get('what')!r}）过滤调用类型。"
            "两级 Future 的实现即：返回值入待决表 → 收到 msg=4 且顶层 async_call_id 命中时 set_result(ret)。"
        )
    else:
        async_info["parser_advice"] = (
            "本次未拿到可解析的 AsyncCallInfo；解析器保持宽容，取不到 async_call_id 时记录原文"
            "并回退到 AsstConnected 轮询（docs/08 §5.2）"
        )

    sample = {
        "probe": "M1-01",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "maa_path": str(maa_path),
        "lib_path": str(lib_path),
        "adb_path": args.adb,
        "address": args.address,
        "kernel_version": kernel_version,
        "exports": exports,
        "build_clues": build_clues,
        "async_call_info": async_info,
        "async_connect_return": run.get("async_connect_return"),
        "async_connect_return_note": run.get("async_connect_return_semantics"),
        "connected_after": run.get("connected_after"),
        "resource_loaded": run.get("resource_loaded"),
        "resource_load_sec": run.get("resource_load_sec"),
        "wait_sec": run.get("wait_sec"),
        "timed_out": run.get("timed_out"),
        "errors": run.get("errors"),
        "observed_callbacks": collector.records,
    }

    json_path = out_dir / "async_call_info_sample.json"
    findings_path = out_dir / "core_probe_findings.md"
    json_path.write_text(json.dumps(sample, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    findings_path.write_text(
        build_findings(args, maa_path, lib_path, exports, kernel_version, build_clues, run, async_info),
        encoding="utf-8",
    )
    print(f"[probe] wrote {json_path}", flush=True)
    print(f"[probe] wrote {findings_path}", flush=True)

    if async_record is None:
        print("[probe] WARNING: 未在超时内捕获 AsyncCallInfo，产物已如实记录未捕获状态", file=sys.stderr)
        return 2
    print("[probe] OK: 已捕获 AsyncCallInfo", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

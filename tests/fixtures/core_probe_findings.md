# M1-01 实测记录：本地内核导出符号与 AsyncCallInfo 回调载荷

- 实测时间（UTC）：2026-09-16T09:09:49.405444+00:00
- 实测主机：macOS-15.4.1-arm64-arm-64bit-Mach-O / Python 3.13.3
- 内核目录：`/Users/chtholly/Developer/WorkSpace/MAA-API/resource/lib/maa/Darwin`
- 加载的库：`/Users/chtholly/Developer/WorkSpace/MAA-API/resource/lib/maa/Darwin/libMaaCore.dylib`
- 复现命令：`DYLD_LIBRARY_PATH=$PWD/resource/lib/maa/Darwin .venv/bin/python scripts/probe_core_api.py`

> 本文件由 `scripts/probe_core_api.py` 依据一次真实运行生成；下面的布尔值与版本号都是运行期测量结果，
> 不是从头文件或文档静态推断的。Linux 库在 macOS 上无法 dlopen，凡涉及 Linux 的表述都明确标注为
> 「文档既有结论」，不计入本次测量。

## 1. 导出符号实测（macOS）

用 `ctypes.CDLL(<lib>)` 加载后逐个 `hasattr` 探测，得到：

| 符号 | 本地 macOS 构建 |
|---|---|
| `AsstGetMapLevelKey` | 导出（True） |
| `AsstGetItemName` | 导出（True） |
| `AsstAsyncConnect` | 导出（True） |
| `AsstAsyncClick` | 导出（True） |
| `AsstAsyncScreencap` | 导出（True） |
| `AsstGetImageBgr` | 导出（True） |
| `AsstGetUUID` | 导出（True） |
| `AsstGetTasksList` | 导出（True） |
| `AsstGetNullSize` | 导出（True） |
| `AsstConnected` | 导出（True） |
| `AsstBackToHome` | 导出（True） |

对照 [docs/03 §1.2](../docs/03-MaaCore内核层设计.md) 的既有结论（未在本次测量范围内，仅作对照）：

- `AsstGetMapLevelKey`：macOS 导出、Linux 未导出，头文件声明在 `AsstCallerExtra.h` 且受
  `ASST_WITH_EXTRA_CALLERS` 条件编译，属实验性 API。
- `AsstGetItemName`：头文件无声明；本次探针**只探测是否导出，绝不调用**（参数与返回语义未知，
  编造签名会直接段错误）。首版不纳入封装，掉落物品名改读 `item_index.json`。

## 2. 内核版本与构建线索

- `AsstGetVersion()` 原样返回：`v6.17.5`
- 库文件：`libMaaCore.dylib`，19313776 bytes，mtime 2026-09-16T02:24:51.135235+00:00
- 同目录下的包与库文件线索：

- `MAA-v6.17.5-macos-runtime-universal.zip`：193765923 bytes，mtime 2026-09-16T02:24:47.180896+00:00
- `adb`：14677904 bytes，mtime 2026-09-16T02:25:09.246519+00:00
- `libMaaAdbControlUnit.dylib`：1454536 bytes，mtime 2026-09-16T02:24:51.252259+00:00
- `libMaaCore.dylib`：19313776 bytes，mtime 2026-09-16T02:24:51.135235+00:00
- `libMaaUtils.dylib`：1348912 bytes，mtime 2026-09-16T02:24:51.109503+00:00
- `libfastdeploy_ppocr.dylib`：9187384 bytes，mtime 2026-09-16T02:24:51.107006+00:00
- `libonnxruntime.1.19.2.dylib`：35138648 bytes，mtime 2026-09-16T02:24:51.247524+00:00
- `libonnxruntime.dylib`：35138648 bytes，mtime 2026-09-16T02:24:51.247524+00:00
- `libopencv_world4.4.12.0.dylib`：25072752 bytes，mtime 2026-09-16T02:24:51.179117+00:00
- `libopencv_world4.dylib`：25072752 bytes，mtime 2026-09-16T02:24:51.179117+00:00

`AsstLoadResource` 返回 True，耗时 0.16s。

判断：运行库由发行包 ['MAA-v6.17.5-macos-runtime-universal.zip'] 解压而来，与 AsstGetVersion() 返回的 v6.17.5 版本号自洽；resource/version.json = {"activity": {"name": "月行水上", "time": 1788476400}, "gacha": {"pool": "石白深蓝之夜", "time": 1788476400}, "last_updated": "2026-09-05 11:08:36.000"}；但 `AsstGetItemName` 在本机 dylib 中导出、而 `AsstCaller.h` 并无声明（docs/03 §1.2），加上文档记录的 Linux 构建未导出 `AsstGetMapLevelKey`，说明导出面与头文件/平台并不一致；结论：即使包名与版本号看起来是发布线，也一律按 docs/13 §5 的要求走运行期 `hasattr` 能力探测，不要静态假设「某 API 可用」

## 3. AsyncCallInfo 实测载荷

**已捕获。** 触发一次 `AsstAsyncConnect(handle, adb, address, "General", block=True)`，
`AsstAsyncConnect` 返回 `async_call_id = 1`（受理编号），
约 1.7937s 后收到 `Message = 4`（AsyncCallInfo）的回调。

原始 details 文本（一字未改）：

```json
{"async_call_id":1,"details":{"cost":1632,"ret":true},"uuid":"f7c1c4ced5e96a23","what":"Connect"}
```

展平后的键路径与类型：

- `async_call_id` = `1` (int)
- `details.cost` = `1632` (int)
- `details.ret` = `True` (bool)
- `uuid` = `'f7c1c4ced5e96a23'` (str)
- `what` = `'Connect'` (str)

**键名与嵌套层级结论（本次 v6.17.5 构建实测）：**

- **关联键在顶层**：`async_call_id` 是回调 JSON 的**顶层字段**（值 1），
  与 `AsstAsyncConnect` 的返回值相等——两级 Future 可直接用它做关联，**不要**去嵌套的 `details` 里找。
- **成功标志嵌两层**：成功与否在 `details.details.ret`（顶层 `details` 对象里再套一个 `details` 对象），
  本次为 `True`（JSON bool）。同层还有 `cost`（本次 1632，与回调延迟同量级，疑似毫秒）。
  文档原先猜测「`details` 里带 `async_call_id` 与 `ret`」并不准确：两者不在同一层。
- **调用类型标识**：顶层 `what == "Connect"`，可用于区分同一条 AsyncCallInfo 通道上的其它异步调用。
- **顶层 `uuid`**：设备 uuid，与同一连接会话的其它回调一致，可用于多设备扩展时的归属校验。
- **失败形态本次未观测到**（设备在线，连接成功）：`ret == false` 时是否附带 `why`、`async_call_id` 是否仍在顶层，
  需 M1-04 用不可达 `address` 补测一次；在那之前解析器对缺失字段保持宽容。

本次观测到的回调消息序列（msg）：`[2, 2, 2, 2, 2, 4, 5]`。
其中 `msg = 2` 是 ConnectionInfo（含 `what = "UuidGot"/"ResolutionGot"/"Connected"/"FastestWayToScreencap"/"ResolutionInfo"`），
它在 AsyncCallInfo **之前**就已出现 `what = "Connected"`；因此判定异步连接结果只能认 `msg = 4` 的
`details.details.ret`，不能拿 ConnectionInfo 里的 `Connected` 代替。`msg = 5` 在异步调用完成后到达。

上层包装（`async_call_info` 字段）的结构为：`msg` 保存枚举值 4，`details` 保存解析后的对象，
`details_raw` 保存上面这段原文。给 M1-04 的解析器结论见 §4。

## 4. 对 M1 的结论

1. **解析器取值键（实测）**：关联键取**顶层** `async_call_id`（本次 1，等于 `AsstAsyncConnect` 返回值）；成功标志取 `details.details.ret`（本次 True）；用顶层 `what`（本次 'Connect'）过滤调用类型。两级 Future 的实现即：返回值入待决表 → 收到 msg=4 且顶层 async_call_id 命中时 set_result(ret)。
2. **不要把返回值当成功判据**：`AsstAsyncConnect` 的返回值是 `AsstAsyncCallId`（受理编号），
   本次拿到 1；只有 `Message = 4` 载荷里的 `details.details.ret` 才是结果，
   两者恰好数值相同容易误判，实现时先记编号再等回调。
3. **能力探测**：`AsstGetMapLevelKey` / `AsstGetItemName` 的可用性必须运行期 `hasattr` 探测，
   `maa_api/core/asst.py`（M1-04 重写）里的 `__set_lib_properties()` 不能对未导出符号统一设
   `restype`/`argtypes`，否则在 Linux 上首次属性访问即抛 `AttributeError`；本次实测 macOS 构建上
   11 个符号全部导出，但这只代表本机 v6.17.5 构建，不能替 Linux 打包票。
4. **回退路径**：本次 `AsstConnected(handle)` 在回调后返回 True；
   若 AsyncCallInfo 结构在其它平台/构建上缺失，可用它轮询兜底（docs/08 §5.2 已设计该退路）。
   注意 `AsstConnected` 是同步查询，探针里可用，但不要用它去替代异步调用的逐次结果。
5. 本卡未改动 `maa_api/` 下任何文件；探针刻意不经由 Python 封装层（`maa_api/core/asst.py`
   在 refactor/v2 上是待重写的占位文件），M1-04 可直接引用上面的实测键名与符号表实现能力探测。

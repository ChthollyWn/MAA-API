# M46 台账：仓库道具数量的 OCR 读取（W0 实验）

- 里程碑：M46
- 分支：`m46-depot-quantity`（从 `refactor/v2` 新建，基点 `eec0f1d`）
- 本轮范围：**只做 W0 实验与文档**，不改 `maa_api/`、不改 `web/`、不 merge/tag/push
- 实验脚本：`/tmp/m46/`（**不提交**，本轮所有脚本都在这里）
- 结论（先给）：**W0-a 跑通（推理链路可用）；W0-b 不达标（标注集 8/15 = 53%，且有系统性掉位）⇒ 按止损线判定「不做」**，数量继续显示「—」

---

## W0-a：推理链路能不能通

### A1. 内核有没有直接可调的 OCR 符号

```
nm -gU resource/lib/maa/Darwin/libMaaCore.dylib | grep -i ocr
```

- **C API（`_Asst*`）里没有任何 OCR**：只有 `AsstCreate/AsstCreateEx/AsstDestroy/AsstConnect/AsstAsyncConnect/AsstConnected/AsstAppendTask/AsstSetTaskParams/AsstStart/AsstStop/AsstRunning/AsstBackToHome/AsstAsyncClick/AsstAsyncScreencap/AsstGetImage/AsstGetImageBgr/AsstGetUUID/AsstGetTasksList/AsstGetVersion/AsstLoadResource/AsstSetUserDir/AsstSetInstanceOption/AsstSetStaticOption/AsstSetConnectionExtras/AsstLog/AsstGetNullSize` + `AsstGetItemName` / `AsstGetMapLevelKey`（道具名/关卡 key 查表，不是 OCR）。
- **C++ 符号确实导出了 OCR**（`T` = 外部可见），主要几条：

| 符号 | 猜测的用法 | 为什么不能直接用 |
| --- | --- | --- |
| `asst::OcrPack::load(filesystem::path const&)` / `check_and_load()` / `recognize(cv::Mat const&, bool, std::optional<Rect> const&)` | 一次性加载 det+rec 的 OCR 包 | 参数/返回值都是 C++ 类型（`cv::Mat`、`std::optional<Rect>`、返回类型未知——mangled name 不带返回类型），没有内核头文件，ctypes 无法安全构造与解读 |
| `asst::CharOcr` / `asst::WordOcr` 单例（`SingletonHolder<...>::get_instance`） | 字/词 OCR | 同上，且要 `MaaNS::SingletonHolder` 的内部状态 |
| `asst::OCRer` / `asst::RegionOCRer`（`analyze` / `bin_left_trim` / `bin_right_trim` / `postproc_trim_`） | 区域 OCR（含二值化裁剪） | 要 `TaskInfo`/`OCRerConfig`（`set_task_info`、`set_required`…），仍要构造 C++ 对象 |
| `asst::TemplDetOCRer`（`set_task_info` / `set_flag_rect_move` / `analyze`） | 模板定位 + OCR | 同上 |

**判定**：内核**没有**可以直接从 Python 调的 OCR 接口；C++ 那一层要么需要 `cv::Mat`/`std::optional`/`TaskInfo` 的 ABI 布局（无头文件、返回类型未知），要么需要自己拼 libc++ 对象，风险远大于收益。**走 ctypes + 内核自带的 `libonnxruntime.1.19.2.dylib` + `PaddleOCR` 模型这条路**。

### A2. onnxruntime 能被 ctypes 驱动吗

事实基础：

- `nm -gU libonnxruntime.1.19.2.dylib` 只导出 **3 个**符号：`_OrtGetApiBase`、`_OrtSessionOptionsAppendExecutionProvider_CPU`、`_OrtSessionOptionsAppendExecutionProvider_CoreML`（`libonnxruntime.dylib` 是它的符号链接）。
- **机器上没有 `onnxruntime_c_api.h`**（`mdfind`/`find` 查过 `/opt/homebrew`、`/usr/local`、`/Applications`、系统 Python 的 site-packages），**也没有网络**（`curl` 连接被重置、web_fetch 失败）⇒ 拿不到官方的 `OrtApi` 结构体顺序。
- 本机 venv 里没有任何推理/OCR 包（`pip list` 只有 numpy/Pillow/scipy 等）。

解决办法：**从内核自带的 `libfastdeploy_ppocr.dylib` 的机器码里把 `OrtApi` 槽位读出来**。fastdeploy 的 `Ort::` C++ 包装（header-only）把 `OrtApi*` 存进全局 `Ort::Global<void>::api_`，每个调用点在反汇编里都是

```
ldr  xR, [xR, #0xa8]   ; literal pool symbol address: __ZN3Ort6GlobalIvE4api_E  → xR = &api_
ldr  xS, [xR]          ; xS = api_
ldr  xT, [xS, #0xNN]   ; api_->slot(0xNN/8)
blr  xT
```

`0xNN/8` 就是槽位号，**调用现场（实参形状）决定它是哪个函数**。命令：

```
llvm-objdump --macho --disassemble --no-show-raw-insn --symbolize-operands \
  resource/lib/maa/Darwin/libfastdeploy_ppocr.dylib > /tmp/m46/fd.txt
```

**实测确认的槽位表**（每条都有调用现场证据）：

| 槽位 | 偏移 | 身份 | 证据 |
| --- | --- | --- | --- |
| 2 | 0x10 | `GetErrorMessage(status)` | `Runtime::Compile`：接上一条的返回（status）→ 返回 `const char*` 后 `strlen` |
| 3 | 0x18 | `CreateEnv(level, logid, OrtEnv**)` | 实参 `w0=2 (WARNING)`、`x1="…"`、`x2=&out` |
| 7 | 0x38 | `CreateSession(env, model_path, options, OrtSession**)` | `InitFromOnnx`：`x0=[this+0x20]`(env)、`x1=char*`(路径)、`x2=[this+0x30]`(options)、`x3=&out`，返回后即 `ReleaseSession(旧)` 再存 `[this+0x28]` |
| 9 | 0x48 | **`Run`** | `InitFromOnnx`/表扫描：唯一吃 8 个实参的槽位；实测喂错输出名会返回 `Invalid output name: …`、喂对则跑出 `(1,T,C)` 的合法输出张量 |
| 23 / 24 | 0xb8 / 0xc0 | `SessionOptionsSetIntraOpNumThreads` / `…InterOpNumThreads` | `BuildOption`：`(options, int)`，判 `>0` 才调 |
| 49 | 0x188 | `CreateTensorWithDataAsOrtValue(meminfo, data, len, shape, ndim, type, out)` | `fastdeploy::CreateOrtValue(FDTensor&)`：7 个实参全对齐 |
| 51 | 0x198 | `GetTensorMutableData(value, void**)` | `OrtValueToFDTensor`：`x0=OrtValue*`、`x1=&out`，返回值直接进 `memcpy` |
| 60 | 0x1e0 | `GetTensorElementType(info, int*)` | `OrtValueToFDTensor`：返回的 int 进「元素类型 → FDDataType」switch（1→FP32、6→INT32、7→INT64、10→FP16、11→DOUBLE，与 ONNX 类型编号一致） |
| 61 | 0x1e8 | `GetDimensionsCount(info, size_t*)` | 同上：返回的 count 用来 `new size_t[count]` + `bzero` |
| 62 | 0x1f0 | `GetDimensions(info, int64_t*, len)` | 同上：`(info, buf, count)` 三参 |
| 64 | 0x200 | `GetTensorShapeElementCount(info, size_t*)` | 同上：返回的 count 左移「元素字节数」后当 `memcpy` 长度 |
| 65 | 0x208 | `GetTensorTypeAndShape(value, info**)` | `OrtValueToFDTensor` 的第一个调用，`x0=[x1]`(OrtValue)、`x1=&out`，其输出给 60/61/62/64 |
| 68 | 0x220 | `CreateMemoryInfo(name, type, id, mem_type, info**)` | `CreateOrtValue`：`x0="Cpu"`、`w1=w2=w3=0`、`x4=&out` |
| 78 | 0x270 | `GetAllocatorWithDefaultOptions(OrtAllocator**)`（**推测**，本轮没用上） | `OrtBackend::Infer`：`add x0, sp, #0x20` 后调用，结果喂给取输出的 helper；按「一出参 + 喂给需要 allocator 的取输出」推的，没有更强证据 |
| 94–99 | 0x2f0… | `ReleaseMemoryInfo / ReleaseSession / ReleaseValue / ReleaseRunOptions / ReleaseTypeInfo / ReleaseTensorTypeAndShapeInfo` | 六个现场一一对应（建完就释放、`OrtValueToFDTensor` 结尾释放 99、输入类型回收释放 98） |
| 133 / 134 / 136 / 138 | 0x428… | `RunWithBinding` / `CreateIoBinding` / `BindInput` / `BindOutputToDevice` | `OrtBackend::Infer` 用 IoBinding 跑推理（内核实际走的就是这条：`session + run_options + binding` 三参） |

**没能定下来的槽位**：`CreateSessionOptions`。候选 4/5/6（三者的机器码挨在一起，紧邻 `CreateEnv`）：槽位 4 直接段错误、槽位 5/6 返回成功但**不写 out 指针**。绕过办法：**给 `CreateSession` 传 `options = NULL`**，ORT 自己造默认 options——实测 `CreateSession(NULL options)` 返回 status=NULL 且 session 非空、后续推理全部正常。`ReleaseSessionOptions` 也就不用调了。

### A3. 模型与字典

`resource/lib/maa/Darwin/resource/`（注意：任务书写的 `resource/PaddleOCR/...` 实际在**内核目录下**）：

| 文件 | 内容 |
| --- | --- |
| `PaddleOCR/det/inference.onnx` | 文本检测（DB），输入 `x[N,3,H,W]`，输出 `sigmoid_0.tmp_0[N,1,H,W]`——**本轮没用** |
| `PaddleOCR/rec/inference.onnx` | 识别，输入 `x[N,3,48,W]`，输出 `softmax_5.tmp_0[N,T,6902]`；`rec/keys.txt` **6901** 行 |
| `PaddleCharOCR/rec/inference.onnx` | 识别（en/字符集），输出 `softmax_2.tmp_0[N,T,97]`；`rec/keys.txt` **96** 行 |
| `ocr_config.json` | `{"equivalence_classes": []}`（与本次无关） |

模型元信息是**自己解析 ONNX protobuf** 得到的（`/tmp/m46/onnx_meta.py`，只用标准库，约 120 行）：图输入名 `x`、输出名 `softmax_2.tmp_0`（char 模型）/`softmax_5.tmp_0`（全字符集模型）——**输出名不是 PaddleOCR 常见的 `save_infer_model/scale_0.tmp_1`**，一开始抄错名字，`Run` 直接回 `Invalid output name`，靠 ONNX 元信息修正。

- 类别数 = dict 行数 + 1（blank）；char 96+1=97 ✓，全字符集 6901+1=6902 ✓。
- 时间步 `T = W/8`：W=320 → T=40（实测输出 `(6,40,97)`）。

### A4. 预处理 / 后处理（实测口径）

- **预处理**：等比缩放到高 48（宽 = round(48·w/h)，不超过设定宽 W）→ `x/127.5 − 1` 归一到 `[-1,1]` → 右侧补 0（＝灰度 127.5）到 W。输入张量 `(N,3,48,W)` float32。
- **后处理（CTC）**：`argmax` → 去连续重复 → **去掉第 0 类**（blank）→ `chars[index-1]`。
  - **这条是实测反证出来的**：合成图 `"1302"` 的 argmax 是 `0,2,0,4,0,1,0,3,0`；按「blank 在最后一类、`chars[index]`」解出 `020401030`，按 blank=0 才得到 `1302`。PaddleOCR 的 `CTCLabelDecode.add_special_char` 确实是 `['blank'] + dict_character`（前置，不是追加）。
  - 置信度 = 保留步的 max 概率均值。

### A5. 合成图端到端实测（`/tmp/m46/w0a_synth.py`）

| 期望 | PaddleCharOCR（96 字符集） | PaddleOCR（6901 字符集） |
| --- | --- | --- |
| `1302` | `1302` ✓ | `\|302` ✗ |
| `475` | `475` ✓ | `475` ✓ |
| `24.4万` | `24.4` ✗（`万` 不在字符集里） | `24.4口` ✗ |
| `7002` | `7002` ✓ | `7002` ✓ |
| `0012` | `0012` ✓ | `0072` ✗ |
| `8` | `8` ✓ | `8` ✓ |
| 合计 | **5/6** | 3/6 |

- 形状/耗时：输入 `(6,3,48,320)`（range −1.0…1.0），输出 `(6,40,97)`，**88 ms / 6 张**（CPU、单线程默认、约 14 ms/张）。
- 环境：`onnxruntime 1.19.2`（`OrtGetApiBase()->GetVersionString()` 实测），`ORT_API_VERSION = 19`。

**W0-a 结论：跑通。** 不需要任何新依赖：内核目录里的 `libonnxruntime.1.19.2.dylib` + `PaddleOCR/PaddleCharOCR` 模型 + 内置 `keys.txt` 就是全部。

---

## W0-b：减法 + 裁剪 + 识别，在真数据上的准确率

### B1. 数据与标注集

用**磁盘上的真机帧**（M45 真机跑出来的内核帧，1280×720；设备本轮没有再操作）：

| 帧 | 扫描（M45 识别器） | 落在库里有准确数量的格子 |
| --- | --- | --- |
| `…/debug/interface/2026.10.13-12.37.19.136_raw.png` | 17 accepted / 4 ambiguous / 3 unknown | `4002`=475、`4003`=244000、`4001`=4722000、`3003`=3092、`4006`=55 |
| `…/12.44.40.312_raw.png` | 7 / 12 / 5 | `32001`=6、`3232`=1、`3262`=2、`3212`=2、`3272`=4 |
| `…/14.16.36.45_raw.png` | 15 / 6 / 3 | `3302`=966、`3301`=4051、`3303`=25、`mod_unlock_token`=60 |
| `…/09.26.18.100_raw.png` | 15 / 6 / 3 | `4006`=55 |
| `tests/services/fixtures/depot/depot_all_tab_1280x720.jpg` | 15 / 6 / 3 | `4006`=55（与 09.26 同页） |

**标注集 = 库里 `count IS NOT NULL` 的那 77 条 ∩ 这些帧上识别出来的格子 = 15 个样本（14 个不同道具：`4006` 在 12.37 与 09.26 两张帧上都出现）**（`resource/maa_api.db` 的 `account_depot_item`；`sources` 字段显示这些 id 都是 `kernel`）。自识别独有的（`count IS NULL`，24 条）在「全部」页首屏上很多，用作人工抽查。

### B2. 角标在哪（实测）

- 数量角标在**道具图标右下角**：白色数字 + 深色描边，压在图标美术上。
- 模板坐标：数字占 `x ∈ [tw−70, tw−8]`、`y ∈ [th−40, th−8]`（tw/th 是模板宽高，117×118）；最右约 8 px、最下约 8 px 是留白。
- **M45 的模板库把这块抹掉了**：`load_item_bank` 照内核 `prepare_cached_templates` 把模板右下 `80×50` 置 0（`DEFAULT_BOTTOM_RIGHT_CUT`），所以 `bank.gray` 在角标区是 0，`格子 − bank模板×0.41` 在那里**什么都不减**。

### B3. 减法口径实测（**与任务书给的口径不一致**）

任务书给的口径是 `subtract(item_templ * 0.41)`。实测下来：

- 用 M45 的模板库（右下被抹 0）做 `格子 − 模板×k`，k 取 0 / 0.41 / 0.8 / 1.0 / 1.2：**角标区的残差完全一样**（模板在那里是 0），减法只影响图标本体。视觉上 `k=0.41` 与 `k=0` 在角标区没有区别。
- 换成**未抹角的原始模板**（`resource/.../template/items/*.png`，居中贴进模板框）再减：`k=1.0` 时**肉眼**最干净（图标美术基本消失、只剩白色数字），但**二值化之后**残留梯度噪声更多（模板渲染与真机渲染不是逐像素一致），端到端反而更差——见下表。
- **没能在内核二进制里验证 `0.41` 这个常量**（本轮没有对 `DepotImageAnalyzer` 做常数搜索取证）。因此台账里只写：**按本机实测，「0.41 + 被抹角的模板」在角标上和「不减」等价；能真正压掉图标美术的是「未抹角的原始模板 × 1.0」，但二值化噪声更大。**

### B4. 参数扫描与准确率（**决定性数字**）

流程：`M45 识别器定格 → 取匹配窗口 → 对齐模板（masked NCC argmax）→ 残差 → 右下 ROI（92×48）→ 二值化（阈值 thr）→ 连通块过滤/成行 → 裁成 48 高的行图 → PaddleOCR rec → CTC → 解析数字（支持 `万`）`。

不带成行分组（`/tmp/m46/sweep.py`，只对 15 格标注样本做 OCR）：

| k \ thr | 170 | 190 | 205 | 220 |
| --- | --- | --- | --- | --- |
| 0（不减） | **8/15 = 53%** | 6/15 = 40% | 8/15 = 53% | 6/15 = 40% |
| 0.41 | 5/15 = 33% | 7/15 = 47% | 3/15 = 20% | 0/15 |
| 0.8 | 4/15 | 0/15 | 1/15 | 0/15 |
| 1.0 | 2/15 | 0/15 | 0/15 | 0/15 |
| 1.2 | 0/15 | 0/15 | 0/15 | 0/15 |

加上「连通块按字高相近 + 横向 gap ≤ 14 px 聚成一行」的分组（`/tmp/m46/sweep2.py`）：最好仍是 **8/15 = 53%**（k=0、thr=150）。**扫描的最好成绩就是 53%。**

典型错误（k=0、thr=170 那一档，`c=`char 模型 / `f=`全字符集模型）：

| 道具 | 库里准确值 | 屏幕显示 | char 读出 | 全字符集读出 | 错误类型 |
| --- | --- | --- | --- | --- | --- |
| `4001` | 4722000 | `472.2万` | `1.472.23` | `h22万` | **首位错**（4→1）+ 杂字 |
| `4003` | 244000 | `24.4万` | `K24.4.3` | `2` | `万` 读成杂字 |
| `3003` | 3092 | `3092` | `30921` | `/33` | 多读一位 |
| `4006` | 55 | `55` | `[]5` | `Q` | 杂字混入 |
| `3301` | 4051 | `4051` | `405` | `14851/` | **掉末位** |
| `3302` | 966 | `966` | `965` | `66` | 末位错 |
| `3232`/`3212`/`3272` | 1 / 2 / 4 | 单位数 | 空 | 空/杂字 | 单位数直接读不出 |

**错误成类，不是随机噪声**：掉末位（4051→405、966→96）、掉首位（4001）、多读一位（3092→30921）、杂字/美术混入（`K`/`[`/`]`/`2`）、`万` 读成杂字。任务书列的止损条件（「有系统性错误（掉首位/多读一位）」）**命中**。

### B5. 自识别独有格的人工抽查（10 格，肉眼读图对照）

把角标原图放大后人工读数（`/tmp/m46/manual.py` → `out/manual.png`）：

| 道具 | 人工读数 | OCR（char） | 判定 |
| --- | --- | --- | --- |
| `7002` 加急许可 | `1302` | `1302` | ✓ |
| EPGS_COIN 寻访参数模型 | `2677` | `2677` | ✓ |
| `randomMaterial_act42side` | `35` | `35` | ✓ |
| `randomMaterial_amiya` | `2` | `2` | ✓ |
| `7003` 寻访凭证 | `42` | `4` | ✗ **掉末位** |
| `randomMaterial_act53side` | `22` | `2` | ✗ **掉首位** |
| `4004` 高级凭证 | `71` | `7` | ✗ **掉末位** |
| `3141` 源石碎片 | `330` | `33` | ✗ **掉末位** |
| `7001` 招聘许可 | `5` | （空） | ✗ 读不出 |
| `4005` 资质凭证 | `1.6万` | `.67` | ✗ `万` 读错 + 掉位 |

- **10 格：对 4、错 6**；错的方式与标注集完全一致（掉首位/掉末位/`万`）。
- 顺带一条正面证据：`7002` 的 `1302` 这次**读对了**（M45 W3b 用任务 JSON 的 OCR 曾读成 `302`，规格 §4.7），说明「rec 模型 + 干净裁剪」本身能读对，问题在**裁剪/分割**。

### B6. 止损判定

任务书给的止损线是「标注集准确率 < 95% **或**有系统性错误（掉首位/多读一位）」：

- 实测最好 **8/15 = 53%**，远低于 95%；
- 错误是**系统性**的掉位/多读/杂字（见 B4/B5）。

⇒ **判定：不做。** M46 到此为止，仓库页里自识别独有的道具数量继续显示「—」。

### B7. 本轮没做到的（不许当结论用）

1. **没有实现 det（文本检测）那一步**。`PaddleCharOCR/det` 与 `PaddleOCR/det` 都可用、链路也已打通（同一个 ORT），但本轮时间只够做「固定 ROI + 阈值 + 连通块」这一条最省的分割路径。**自识别独有的 10 格里有 4 格是对的、且错的全是分割问题**，所以「换成 det 定位文本框再 rec」有可能显著改善——**这条没有证据，属于未验证**。
2. **没有验证内核二进制里的 `0.41`**（见 B3）。
3. **没有在真机上重跑**：用的是磁盘上 M45 留下的真机帧（1280×720），不是本轮新截图；设备（`127.0.0.1:5555`，`state=connected`）本轮没有操作。
4. 标注样本只有 15 格（= 库里有准确数量的 77 条 ∩ 4 张帧上的格子）。样本小，但 53% 与 95% 的差距不需要更大样本才能判。

---

## 附：本轮用到的命令与脚本（都在 `/tmp/m46/`，不提交）

| 脚本 | 作用 |
| --- | --- |
| `onnx_meta.py` | 标准库解析 ONNX protobuf，取图输入/输出名与形状 |
| `ort_ocr.py` | **核心**：ctypes 驱动 `libonnxruntime` + `OrtApi` 槽位表 + `KernelOcr` + CTC 解码 |
| `w0a_synth.py` | W0-a 合成图端到端（PIL 画数字 → 推理 → 解码） |
| `w0b_probe.py` / `w0b_view.py` | 真机帧扫描 + 角标位置/残差可视化 |
| `sweep.py` / `sweep2.py` | k × 阈值参数扫描（含/不含成行分组），出准确率表 |
| `manual.py` | 自识别独有格的人工抽查对照图 |
| `fd.txt` | `libfastdeploy_ppocr.dylib` 的反汇编（还原槽位表用，26 MB） |
| `ort_dis_arm.txt` | `libonnxruntime.1.19.2.dylib` 的 arm64 反汇编（117 MB） |
| `table.py` / `scan_arity.py` / `slots2.py` / `slots3.py` | 槽位表还原与「按实参个数找 Run」的工具 |
| `out/*.png` | 残差/角标/对照图（诊断用） |

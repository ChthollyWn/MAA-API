# M46：读仓库道具数量（内核 OCR 复用）——W0 后判定「不做」

- 里程碑：M46
- 分支：`m46-depot-quantity`（从 `refactor/v2` 新建，基点 `eec0f1d`）
- 状态：**W0 已完成，判定「不做」，W1/W2/W3 不执行**（依据见 §3、§4；台账 `docs/superpowers/ledgers/2026-10-13-m46-depot-quantity.md`）
- 上游：M45 自研仓库识别（merge `eec0f1d`，tag `v2-m45`），`docs/13` §8.65/§8.66
- 台账：`docs/superpowers/ledgers/2026-10-13-m46-depot-quantity.md`

## 1. 来源

用户 2026-10-13 对 M45 遗留的不满：

> 仓库里自己识别出来的道具没有数量

讨论后确定的方向（用户已批准开 M46）：

> 用 MAA 内核自带的 OCR（onnxruntime + PaddleOCR 模型），由我们在 Python 侧补上内核内部那一步「图像减法」，从而复用内核的 OCR 准确度而不新增任何依赖。

要解决的问题：M45 的自研识别链能认出内核拿不到的道具（`4004/4005/7001/7002/7003/EPGS_COIN/…`），但**数量一律写 `count=NULL`，页面显示「—」**。内核那一趟给的 77 条数量是准的（可当标注集），原因就是内核在 C++ 里做了「图标美术减法 + OCR」而我们没有。目标是**用内核自带的模型在 Python 侧补齐这一步**，让这 24 条也有数量。

**判定标准（本轮定的止损线）**：在「库里有准确数量」的标注集上，端到端数字准确率 **≥ 95%**，且没有系统性错误（掉首位/多读一位）。达不到就**不做**，数量继续显示「—」。

## 2. 事实基础（全部本机实测，见台账）

### 2.1 模型与运行时都在内核目录里，venv 一个推理包都没有

- `resource/lib/maa/Darwin/` 下有 `libonnxruntime.1.19.2.dylib`（35 MB，只导出 3 个符号，其中 `_OrtGetApiBase`）、`libfastdeploy_ppocr.dylib`、`libopencv_world4.dylib`、`libMaaCore.dylib`。
- 模型在 `resource/lib/maa/Darwin/resource/`（**不是** `resource/PaddleOCR/`）：`PaddleOCR/{det,rec}/inference.onnx`、`PaddleCharOCR/{det,rec}/inference.onnx`，各自的 `keys.txt`（6901 / 96 行）、`version.txt`、`resource/ocr_config.json`。
- venv（`.venv/bin/pip list`）**没有任何 OCR/推理包**；机器上没有 `onnxruntime_c_api.h`、没有网络。硬约束：**不许 pip install**。
- 内核**没有**可直接调的 OCR 接口：C API 只有 `AsstCreate/AsstConnect/AsstAppendTask/AsstGetImage/AsstGetItemName/…`，C++ 侧虽然有 `asst::OcrPack::recognize(cv::Mat const&, bool, std::optional<Rect> const&)`、`asst::RegionOCRer`、`asst::TemplDetOCRer` 等导出符号，但要构造 `cv::Mat`/`std::optional`/`TaskInfo` 且**返回类型未知**（mangled name 不带返回类型），ctypes 无法安全使用。

### 2.2 W0-a：推理链路**跑通**（关键数字）

- **`OrtApi` 槽位表是从内核自带的 `libfastdeploy_ppocr.dylib` 的机器码里还原的**（fastdeploy 的 `Ort::` 包装把 `api_` 存在全局 `Ort::Global<void>::api_`，每个调用点 `ldr xT,[api,#0xNN] / blr`，`0xNN/8` 即槽位）。实测确认：`3=CreateEnv`、`7=CreateSession`、**`9=Run`**、`23/24=SessionOptionsSet*NumThreads`、`49=CreateTensorWithDataAsOrtValue`、`51=GetTensorMutableData`、`60=GetTensorElementType`、`61=GetDimensionsCount`、`62=GetDimensions`、`64=GetTensorShapeElementCount`、`65=GetTensorTypeAndShape`、`68=CreateMemoryInfo`、`78=GetAllocatorWithDefaultOptions`、`94–99=六个 Release`、`133/134/136/138=RunWithBinding/CreateIoBinding/BindInput/BindOutputToDevice`。
- `CreateSessionOptions` **没找到**（候选槽位 4 段错误、5/6 返回成功但不写 out）；改用 **`CreateSession(env, path, NULL, &out)`**（ORT 自造默认 options）——实测成功，后续推理正常。
- 模型元信息（自写 protobuf 解析）：输入 `x`、`[N,3,48,W]` float32；输出 `softmax_2.tmp_0`（char，`[N,T,97]`）/`softmax_5.tmp_0`（全字符集，`[N,T,6902]`）；`T = W/8`（W=320→T=40）。
- 预处理：等比缩放到高 48 → `x/127.5−1` 归一到 `[-1,1]` → 右侧补 0 到 W。
- 后处理：CTC，`argmax → 去重 → 去第 0 类（blank）→ chars[index−1]`；置信度 = 保留步 max 概率均值。
- 合成图实测：char 模型 **5/6**（唯一失败 `24.4万`→`24.4`，`万` 不在 96 字符集）；全字符集模型 3/6；**88 ms / 6 张**（CPU，≈14 ms/张）。
- 结论：**零新依赖**（内核 dylib + 内核模型 + 内置字典 + ctypes）。

### 2.3 W0-b：准确率**不达标**（决定性）

- 数据：4 张 M45 真机帧（1280×720）+ 1 张 fixture；标注集 = 库里 `count IS NOT NULL` 的 77 条 ∩ 帧上识别出的格子 = **15 个样本（14 个不同道具，`4006` 在两张帧上都出现）**。
- 几何/角标（实测）：数量在图标**右下角**（白字 + 深色描边）；模板坐标约 `x∈[tw−70,tw−8]`、`y∈[th−40,th−8]`；M45 的模板库照内核 `prepare_cached_templates` 已把模板右下 `80×50` 抹成 0，**恰好就是角标区**。
- 减法口径实测：`格子 − bank模板×0.41` 在角标区**等于不减**（模板该区为 0）；改用未抹角的原始模板 `×1.0` 肉眼最干净但二值化噪声更大、端到端更差。**没能在内核二进制里验证 `0.41` 这个常量**。
- **参数扫描（k × 二值化阈值 × 是否成行分组）最好成绩 = 8/15 = 53%**（不减、阈值 150–170），所有减法档位都不超过它。
- **错误成类**：掉末位（`4051`→`405`、`966`→`96`）、掉首位（`4722000`→`1.472.23`/`h22万`）、多读一位（`3092`→`30921`）、杂字混入（`55`→`[]5`）、`万` 读成杂字（`24.4万`→`K24.4.3`）、单位数读不出（`1/2/4`）。
- **自识别独有格人工抽查 10 格：对 4 错 6**，错法同上（`71`→`7`、`22`→`2`、`42`→`4`、`330`→`33`、`1.6万`→`.67`、`5`→空）。
- 正面证据：`7002` 的 `1302` 这次读对了（M45 W3b 用任务 JSON 的 OCR 曾读成 `302`）⇒ **rec 模型本身够用，卡在裁剪/分割**。

### 2.4 止损判定

准确率 53% ≪ 95%，且错误是系统性的掉位/多读 ⇒ 按 §1 的判定标准：**不做**。

## 3. 范围

### 3.1 本轮做（已完成）

- W0-a：符号普查 + `OrtApi` 槽位还原 + ctypes 推理链路 + 合成图验证。
- W0-b：真机帧上的减法/裁剪/识别 + 与标注集对照 + 抽查 + 止损判定。
- 本文档、计划、台账。

### 3.2 本轮不做（判定「不做」后关闭）

- 不写数量读取模块、不接进逐屏扫描、不改页面展示、不写库。
- **自识别独有的道具数量继续写 `count=NULL`、页面继续显示「—」**（M45 现状不变）。
- 不改 `maa_api/`、不改 `web/`、不 merge、不打 tag、不 push。

### 3.3 什么条件下可以重开

只有下面任一条有新证据时才值得重开（否则不要重做同一件已经证否的事）：

1. **先做 det**：本轮没实现文本检测（`PaddleCharOCR/det` + DB 后处理），而人工抽查里错的全是分割问题、`rec` 单独用能读对（`1302`/`2677`/`35`）。重开的第一步必须是「det 定位文本框 → rec」，而不是再调减法系数或阈值。
2. **拿到内核算法的确切口径**：从 `libMaaCore.dylib` 里把 `DepotImageAnalyzer` 的角标裁剪/二值化/减法常数反汇编出来（含 `0.41` 的真正作用对象），照着复刻。
3. **内核自己给出数量**：如果将来内核的 `DepotRecognitionTask` 白名单放开（例如把 `NORMAL` 也纳入），我们直接吃内核的数量即可，不必自研。

## 4. 设计（**本轮不实现**，记录下来供重开时用）

> 这一节是「如果 W0-b 达标会怎么写」，不是已交付的代码。所有阈值/口径都以台账 W0 的实测数字为准。

### 4.1 模型与字典来源

- 运行时：`resource/lib/maa/Darwin/libonnxruntime.1.19.2.dylib`（按平台映射到 `Linux/Win32` 同名文件），**只读、不拷贝进版本库**。
- 模型：内核资源目录 `resource/lib/maa/Darwin/resource/PaddleOCR/rec/inference.onnx` + `rec/keys.txt`（**必须两个一起取**，类别数 = 字典行数 + 1）。
- 资源根顺序**复用 M45 的 `depot_scan.resource_roots()`**（增量层 → 内核层 → repo 快照），保证「内核升级/资源更新」时读到的是同一份模型。
- **模型或字典缺失 ⇒ 直接降级**（见 §4.6），不抛异常打断整轮扫描。

### 4.2 减法口径

- 照内核：`resid = cell_patch − template × k`。
- **M45 的模板库不能直接用**：`load_item_bank` 把模板右下 `80×50` 抹成 0，而角标就在那里 ⇒ 该区等于不减。
- 重开时要么给 `ItemBank` 增加一份**未抹角**的模板（只为减法用），要么把 `k` 与「抹角」两件事解耦；`k` 的经验值必须以真实标注集为准，**不要抄 `0.41`**（本轮实测 `0.41` 与 `0` 在角标区等价、`1.0` 肉眼最好但端到端最差）。

### 4.3 裁剪与归一化

- 单元格 `122×122`（1066×599 空间）、匹配窗口 `162×132`；模板按 masked NCC 的 argmax 位置与格子对齐（不要用「居中」假设）。
- 角标 ROI：格子右下角，约 `92×48`（`x∈[tw−92,tw−2]`、`y∈[th−48,th−3]`）。
- 二值化 + 连通块：按「字高相近（≥ 0.62×最大字高）+ 横向 gap ≤ 14 px」聚成一行，取最大的一行；去掉小碎块（面积 < 8~10 px）。
- 行图归一化：只保留数字笔画（黑字白底）、等比缩放到高 **48**、右侧补 0；批量推理时同一批用同一个宽 W（PaddleOCR 口径：`T = W/8`）。
- **重开时必须先做 det**（§3.3-1），固定 ROI 这条已经被证否。

### 4.4 阈值与「读不出写 NULL」

- 只有**解析成功**才写数量：`^[0-9]+(\.[0-9])?万?$`（`万` ⇒ ×10000），且 OCR 置信度 ≥ 阈值；**其余一律 `count=NULL`（页面「—」）**。
- **绝不猜**：读出的数字与页面显示位数不符（例如屏幕是 `1.6万` 却只读出 2 个字符）、置信度低、ROI 里没找到文本行 —— 全部走 NULL。宁可「—」也不要错数（M45 写库已有「相对上一轮的下降护栏」，数量错一样会污染这条护栏）。
- `万` 必须走全字符集模型（char 模型没有 `万`）。

### 4.5 与 M45 扫描链的接入点

- 新模块放 `maa_api/services/account/depot_quantity.py`（纯函数、可注入模型路径，照 `depot_scan.py` 的风格：不碰设备/网络/数据库）。
- 接入点在 `depot_scan_service.py` 的逐屏扫描里：`scan_panel()` 得到 `PanelScan.cells` → 对 `accepted` 的格子补一次数量读取 → 结果挂到 `CellResult` 的扩展字段（或 `flatten_scan()` 的 `items` 映射里附带 `count`）。
- 写库仍在 `depot.py`：`count=None` 的行保持现在的行为（写 NULL、页面「—」）。
- **每次调用现读设置**（照 M45 W2 的做法），阈值可由设置项覆盖。

### 4.6 失败时的降级

- 模型/字典缺失、ORT 加载失败、槽位表与当前 `libonnxruntime` 版本不匹配 ⇒ **整个数量读取关闭**，`count` 全 NULL，只记一条 warning，不打断扫描、不影响识别结果。
- 单格失败 ⇒ 该格 NULL。

## 5. 兼容与风险

| 风险 | 说明 | 缓解 |
| --- | --- | --- |
| **ORT 版本升级**（内核换 `libonnxruntime*.dylib`） | 槽位表是**按 1.19.2 的 `OrtApi` 顺序**从机器码里还原的，版本一变槽位可能整体位移 | 启动时校验 `OrtGetApiBase()->GetVersionString()`（实测 `1.19.2`）；版本不符就**整体降级关闭**（§4.6）。绝不「猜着调」 |
| **内核换模型/换字符集** | `keys.txt` 行数 = 类别数 − 1，行数一变解码全错；`version.txt` 显示 char 模型是 `en_PP-OCRv3_rec_infer` | 启动时校验「输出类别数 == 字典行数 + 1」，不符就关闭并记 warning |
| **`万` 与小数** | 大数量在游戏里显示成 `472.2万`；char 字符集没有 `万` | 用全字符集模型，或对 `万` 单独检测；解析规则见 §4.4 |
| **性能** | 实测 rec ≈ 14 ms/张（CPU、320×48、单张）；一页 24 格 → 批量一次约 0.1–0.4 s（本轮 6 张 88 ms）；det 另算 | 批量推理（一次一格）；必要时只在「本页有新 id」时读；不影响 M45 已有的单页 0.8 s 扫描预算太多 |
| **准确率本身** | **本轮实测 53%**，达不到可用水平 | 这正是判定「不做」的理由；重开的前置条件见 §3.3 |
| **无头文件/无网络** | `OrtApi` 槽位靠反汇编还原，可维护性差 | 槽位表与证据一起写进代码注释（本轮台账已记录）；不改内核 dylib、不做二进制 patch |

## 6. 文档同步表

| 文档 | 需要改什么 | 本轮 |
| --- | --- | --- |
| `docs/13-决策记录.md` §8 | 追加一条 M46 的裁决：**不做**，数量继续 NULL，理由（53% + 系统性掉位）与重开条件 | 待主 agent 决定（本轮不改其他文档，避免与 M45 的 §8.65/§8.66 抢同一段） |
| `docs/12`（里程碑表） | M46 记为「W0 后关闭（不做）」 | 同上 |
| `docs/04` / `web` 页面说明 | **不改**（行为不变：数量仍显示「—」） | — |
| 本 spec / plan / ledger | 本轮交付 | ✓ |

## 7. 验收

本轮（W0 + 文档）的验收标准：

1. 三个文档存在且内容与实测一致（`docs/superpowers/{specs,plans,ledgers}/2026-10-13-m46-depot-quantity.md`）。
2. 分支 `m46-depot-quantity` 上**只提交 `docs/`**；`maa_api/`、`web/` 无改动；不 merge、不打 tag、不 push。
3. W0-a 有可复现结论：内核自带 ORT + PaddleOCR rec 能跑通、合成图 char 模型 5/6、关键参数（形状/归一化/解码/blank=0）写明。
4. W0-b 有可复现数字：标注集 15 格、最好 8/15 = 53%、错误成类、抽查 10 格对 4 错 6、止损判定为「不做」。
5. 未取证的部分（det 未实现、`0.41` 未在内核里验证、未在真机上重跑）**明确写成未取证**，不得当成结论。

若将来重开，验收标准回到 §1 的判定线：标注集 ≥ 95% 且无系统性掉位，才允许把数量写进 `account_depot_item`。

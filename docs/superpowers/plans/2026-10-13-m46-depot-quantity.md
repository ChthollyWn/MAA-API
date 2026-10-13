# M46 计划：仓库道具数量读取（W0 后判定「不做」）

- 里程碑：M46，分支 `m46-depot-quantity`（基点 `eec0f1d`）
- 规格：`docs/superpowers/specs/2026-10-13-m46-depot-quantity.md`
- 台账：`docs/superpowers/ledgers/2026-10-13-m46-depot-quantity.md`
- **总判定**：W0 的止损线未通过（标注集最好 8/15 = 53%，且有系统性掉位/多读）⇒ **W1/W2/W3 全部不执行**。下面的 W1–W3 是「若重开时怎么做」的备档，不是待办。

---

## W0（本轮）：实验与判定 —— **已完成，结论「不做」**

**为什么**
用户对 M45 遗留不满：「仓库里自己识别出来的道具没有数量」。方向是用内核自带的 OCR（onnxruntime + PaddleOCR 模型）在 Python 侧补上内核内部那一步「图像减法」，**不新增任何依赖**。W0 的唯一任务是：先证明链路能通，再用真机数据量出准确率，决定这件事值不值得做。

**范围**
- `nm`/`strings` 普查内核导出的 OCR 符号。
- ctypes 驱动内核自带的 `libonnxruntime.1.19.2.dylib` + `PaddleOCR/PaddleCharOCR` rec 模型：模型元信息、输入形状、归一化、输出形状、CTC 解码。
- 合成图端到端（W0-a）；真机帧上的减法 + 裁剪 + 识别 + 与库里准确数量对照 + 人工抽查（W0-b）。
- 三个文档；分支与提交（只提交 `docs/`）。
- **不做**：不改 `maa_api/`、不改 `web/`、不 merge/tag/push、不 pip install。

**做法（关键路径）**
1. 符号普查：内核 C API 无 OCR；C++ 侧 `asst::OcrPack/RegionOCRer/TemplDetOCRer` 因缺头文件与未知返回类型不可用 ⇒ 走 ORT C API。
2. `OrtApi` 槽位表：从 `libfastdeploy_ppocr.dylib` 的反汇编里按 `ldr xT,[api,#0xNN] / blr` 现场还原（`0xNN/8` = 槽位），并用「实参个数」扫描找 `Run`（唯一 8 实参者 = 槽位 9）。
3. ctypes 封装：`CreateEnv(3) → CreateSession(7, options=NULL) → CreateMemoryInfo(68) → CreateTensorWithDataAsOrtValue(49) → Run(9) → GetTensorTypeAndShape(65)/GetTensorElementType(60)/GetDimensionsCount(61)/GetDimensions(62)/GetTensorShapeElementCount(64)/GetTensorMutableData(51)`。
4. 合成图（PIL 画 `1302/475/24.4万/7002/0012/8`）验证链路与解码；用 argmax 序列反证 **blank 是第 0 类**。
5. 真机帧（4 张 M45 内核帧 + fixture）→ M45 识别器定格 → 对齐模板 → 残差 → 右下 ROI → 二值化/连通块 → 48 高行图 → rec → 解析数字 → 与库里 77 条准确数量求交（15 格）→ 参数扫描（k × 阈值 × 成行分组）→ 人工抽查 10 个自识别独有格。
6. 按止损线判定。

**验收（已完成）**
- W0-a：合成图 char 模型 5/6、88 ms/6 张；输入 `(N,3,48,W)`、归一化 `x/127.5−1`、输出 `(N,T,C)`、`T=W/8`、blank=0、`chars[index−1]` 全部写明。
- W0-b：标注集 15 个样本、最好 **8/15 = 53%**、错误成类（掉末位/掉首位/多读一位/杂字/`万`）、抽查 10 格 **对 4 错 6**。
- 止损判定：**不做**；`count` 继续 `NULL`，页面继续「—」。
- 三个文档；分支只提交 `docs/`；未取证项（det 未实现、`0.41` 未在内核里验证、未在真机重跑）写明。

**blocked_by**：无（已完成）。

---

## W1（**不执行**）：数量读取模块 + 单测

**为什么**
W0-a 已经证明「内核自带 ORT + rec 模型」在 Python 里可用，但 W0-b 证明「固定 ROI + 阈值 + 连通块」这条分割路径只有 53%。W1 的本意是把达标的读取逻辑落成纯函数模块；**因为没达标，它不执行**。重开时第一步必须是 **det 定位文本框再 rec**（见规格 §3.3），而不是继续调减法/阈值。

**范围（备档）**
- 新模块 `maa_api/services/account/depot_quantity.py`：纯函数、可注入模型/字典路径、不碰设备/网络/数据库。
- 单测：`tests/services/test_account_depot_quantity.py`（合成图 + 真机帧 fixture + 噪声图），覆盖「读不出 ⇒ NULL」。
- 模型/字典缺失 ⇒ 降级关闭，只记 warning。

**做法（备档）**
1. ORT 封装拆成独立小类（`OrtApiTable` + `KernelOcr`），槽位表与**还原证据**一起写进注释；启动时校验 `GetVersionString()` 与「输出类别数 == 字典行数 + 1」。
2. 预处理（高 48、`x/127.5−1`、右侧补 0）、CTC 解码（blank=0）、数字解析（`万` ⇒ ×10000、只接受 `^[0-9]+(\.[0-9])?万?$`）各自是纯函数。
3. 分割：**det（DB）→ 文本框 → rec**；只有在 det 不可用时才回退到固定 ROI，并且回退路径的置信度门槛要更高。
4. 并发/单例：一个进程一份 session（惰性初始化），避免每格重建 session。

**验收（备档）**
- 标注集（≥ 15 格，越接近 77 越好）准确率 **≥ 95%**，且无系统性掉位/多读。
- 「模型缺失 / 版本不符 / 类别数不符」三种情况下：数量全 NULL、只记 warning、单测覆盖。
- `.venv/bin/python -m pytest -q` 全绿。

**blocked_by**：**W0 的止损判定（准确率 53% ≪ 95% + 系统性掉位）**。重开的前置条件见规格 §3.3（先做 det / 拿到内核确切口径 / 内核自己给数量）。

---

## W2（**不执行**）：接进逐屏扫描 + 页面展示 + 真机验收

**为什么**
数量只有跟 M45 的逐屏扫描、写库护栏、页面展示接起来才对用户可见。**因为 W1 未做，它不执行。**

**范围（备档）**
- `depot_scan_service.py`：每屏 `scan_panel()` 之后对 `accepted` 格子补一次数量读取，结果随 `flatten_scan()` 一起回传。
- `depot.py`：有数量写数量，读不出仍写 NULL（不改现有「下限/下降护栏」语义）。
- `web/`：`—` 与数字共存的展示（数量列已存在，只需接上）；截图测试/快照按需更新。
- 设置项：数量读取开关与置信度阈值（照 M45 W2 的做法「每次调用现读」）。

**做法（备档）**
1. 先做真机取证：确认「本页新出现的 id」才读数量，避免白花时间。
2. 逐屏扫描里批量读（一屏 24 格一次批量），失败格子记 NULL 与 warning。
3. 页面：数量列 `—` / `1234` / `1.6万` 的展示与排序；「本页读取失败」的提示不改文案基线（按 M45 的文案基线流程走）。

**验收（备档）**
- 真机一轮：自识别独有的 24 条里有多少拿到数量、多少仍 NULL，逐条列出并与截图人工核对。
- 后端测试 + 前端 `pnpm typecheck/test/build` + `scripts/frontend_smoke.py` 全绿。
- OpenAPI 快照（若接口形状变了）：`scripts/dump_openapi.py --check` → 重新生成 → `pnpm gen:api`。

**blocked_by**：W1（未执行）。

---

## W3（**不执行**）：文档、全门禁、合并与 tag

**为什么**
收口流程与 M0–M45 一致。**因为 W1/W2 未做，它不执行。**

**范围（备档）**
- `docs/13` §8 追加一条裁决；`docs/12` 里程碑表记 M46；`docs/04` 与页面文案按需同步。
- 全门禁：后端 `pytest -q`、前端 `pnpm install --frozen-lockfile && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build`、`scripts/dump_openapi.py --check`、相关 `scripts/*_smoke.py`。
- `git merge --no-ff refactor/v2`（在 `m46-depot-quantity` 上完成后由主 agent 执行）与 tag `v2-m46`。

**做法（备档）**
1. 文档同步 → 全门禁 → `--no-ff` 合并 → tag（**默认不 push**）。
2. 台账 `progress.md` 归档到 `docs/superpowers/ledgers/`，「Rulings I made」一并写入。

**验收（备档）**
- 门禁全绿；`git log` 有 `--no-ff` 合并提交；tag `v2-m46` 指向它。

**blocked_by**：W2（未执行）。

---

## 本轮实际收口（W0 版）

| 项 | 结果 |
| --- | --- |
| 分支 | `m46-depot-quantity`（从 `refactor/v2` @ `eec0f1d`） |
| 提交内容 | **只提交 `docs/`**：spec + plan + ledger 三个文档 |
| 未改 | `maa_api/`、`web/`、`resource/`（只读）、`config.yaml`、其他 docs |
| 未做 | merge、tag、push |
| 实验脚本 | `/tmp/m46/`（**不提交**） |
| 结论 | **不做**：自识别独有的道具数量继续 `count=NULL`、页面「—」 |

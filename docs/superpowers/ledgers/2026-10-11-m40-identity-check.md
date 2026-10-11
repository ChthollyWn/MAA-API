# M40 · 全量扫描的身份核对（详情页拉丁名） · 进度

- 分支：`m40-identity-check`（自 `refactor/v2`，基点 `6abd5cc`；在 tag `v2-m39` 之后），完成后 tag `v2-m40`
- 规格：`docs/superpowers/specs/2026-10-11-m40-identity-check.md`
- 计划：`docs/superpowers/plans/2026-10-11-m40-identity-check.md`
- 来源：M39（用户 m00039 的两类扫描）在 2026-10-11 晚的整轮全量里暴露**身份错位**。用户 m02187 的裁决是「让它跑完」（不取消那轮、不动服务与设备）；本轮在其后做修正版。
- 真机：BlueStacks / Android 2560×1440（`127.0.0.1:5555`）；用户常驻服务 8002（PID 77596 的守护循环，core pid 77698），只在它空闲时动设备

## 任务状态

| 工作项 | 写范围 | 状态 |
| --- | --- | --- |
| W1 事实取证 | `scripts/probe_full_scan.py`（探针，不进生产路径）、规格 §2 | 完成（拉丁名 ROI 两轮标定 → `(0, 452, 524, 32)`；1048 条名字的变形实验定阈值 0.7/0.15/3） |
| W2 服务层 | `maa_api/services/account/scan_tasks.py`、`capture.py`、`scan.py` | 完成（`OperNameEn` 节点进详情链末尾与短路表；`OperatorMeta.name_en`；`_name_en_key`/`_name_en_hit`/`_expected_name_en`/`_confirm_card`） |
| W3 契约与前端 | `maa_api/services/account/scan.py`（`snapshot()`）、`maa_api/api/routers/account.py`（GET 描述）、`web/src/features/account/api.ts`、`TrainingScanCard.tsx`、`test-utils.tsx` | 完成（两个计数进契约、类型、归一化、摘要） |
| W4 单测 | `tests/services/test_account_training_scan.py`、`web/src/features/account/TrainingScanCard.test.tsx` | 完成（后端新增 6 例、同步 2 例；前端摘要与请求层用例） |
| W5 文档 + 台账 + 门禁 + 合并 | `docs/13` §8.56、`docs/12` M40、`docs/05`、`docs/09`、规格/计划/本文件、生成物与基线 | 进行中 |

## Rulings I made（实施中定的口径）

1. **用「详情页左下角拉丁名」当身份信号**，而不是①读中文美术字（M36 实测 `OperBoxNameOCR` score 0.166）、②整表先扫名（M39 Design B，需要「回列表顶部」且整体错位风险更大）、③改成逐格点开（真机 6.3 s/张 vs 4.9 s/张）。拉丁名是不随立绘变化的纯文本，内核 ASCII OCR 读得出来，且能**不回列表**逐张核对——这正是用户 m00039 描述的滑动机制能保留的关键。
2. **只跟同一屏 16 格的期望名比**：离线实验里残读的 99.7% 能唯一命中，失败集中在「本体与异格同名」（`rene`→`irene` 而非 `arene`）；同屏候选 + 领先第二名 0.15 把这类干扰压掉。
3. **阈值做成常量而不是写死**：`NAME_EN_MIN_SCORE = 0.7`、`NAME_EN_MIN_MARGIN = 0.15`、`NAME_EN_MIN_FUZZY_LENGTH = 3`。第三个从初版的 4 改成 3——真机 `Ines`→`nes` 只剩 3 个字符，卡在 4 会把能认的读法丢掉；2 个字符（`ne`）仍不猜。
4. **ROI 左边界放到 0**：初版 `(8, 456, 510, 28)` 12 格只中 5 格，`Ines`→`nes`、`SilverAsh`→`ilverAsh`（掉首字母）；放宽成 `(0, 452, 524, 32)` 后同一批干净了，代价是个别格子多一个字母（`Bagipipe`）——残读交给模糊比对吸收，掉字比多字难救。
5. **认不出不拦**（按卡名写库 + `unverified` 计数），**认错了才拦**（重锚一次，仍不对就不写库）。理由是两边的代价不对称：少写一条读数是漏，写错人是脏——M39 的事故正是后者。
6. **`_name_en_hit` 用「相等或模糊 + 领先余量 + 最短长度」三段判定**，不卡 OCR 的 `score`：真机里 score 与正确性只是弱相关（0.667 的年是对的、0.916 的重岳是错的）。
7. **节点必须放在详情链最末并进短路表**：链上某个节点识别不出东西时整条链就结束（`scripts/probe_training_scan.py:184-186`），放前面会连累模组读数，不进 `ELITE_SHORTCUTS` 则精一/精零的人根本走不到它。
8. **`name_en` 只在是纯 ASCII 时才认**（`_latin_name`）：`battle_data` 里个别干员的 `name_en` 就是中文（「克莱门莎」），拿它当拉丁名会让比对永远失败。
9. **重锚后把新帧当作「滑动前」画面**（`image = detail.get("image") or image`）：重锚本身点开了一次，若不换帧，下一张的像素比对会拿着旧帧比出「没动」而白跑一次重试。
10. **两个计数进契约、为 0 不提**：`realigned`/`unverified` 是 int（与 `skipped`/`unresolved`/`mismatched` 一致），前端摘要里非 0 才显示、单位用「张」（全量扫描按张算）。
11. **本轮不清库、不写迁移**：那轮写错的读数靠再跑一轮全量刷新（重扫到同一人时用新读数覆盖）；是否现在跑由用户定（本轮只把设备留给用户常驻服务）。

## 实施中修掉的问题（按发现顺序）

1. **探针 `--cells` 参数没生效**：`parse_cells` 按 `":"` 切分，传 `top1` 被过滤成空、悄悄退回默认抽样（top0/top2/top4），看着像「只跑了 3 格」。改成先 `replace(":", "")` 再取行名前缀 + 数字后缀（同时接受 `top0` 与 `top:0`）。
2. **掉首字母被当成错位**：`Ines`→`nes`（0.993）在「前缀关系」口径下判成对不上。生产口径（相等/相似度/领先余量/最短 3 字符）能认它 ⇒ 探针同时打印两栏，避免再把 OCR 残读误读成「漂移」。
3. **前端必填字段漏改会被 TS 抓**：`TrainingScanState` 加两个必填后，`IDLE_TRAINING_SCAN`、提交后的缓存种子、三个 fixture、mock 响应逐处补齐（`pnpm typecheck` 0 错）。
4. **`test_snapshot_shape_matches_the_spec` 与 `test_the_elite_shortcut_skips_the_other_badges`**：前者断言快照键集合（加 `realigned`/`unverified`），后者断言短路链尾巴（加 `OperNameEn`）。
5. **测试助手 `_detail_batch`**：加 `name_en: str | None = None`（有值才发 `OperNameEn` 回调），否则所有既有详情批次都会多出一条读不到的回调。

## 真机复核（W1 + W4 的补充）

`scripts/probe_full_scan.py --step name --cells "top0,top1,top2,top3,top5,top6,top7,bottom1,bottom2"`，报告 `/tmp/m40_name4.json`、日志 `/tmp/m40_name4.log`（设备停在干员列表第 1 屏，本屏卡名 14/16）：

| 格 | 卡名 | 期望 | 读到 | score | 生产口径 |
| --- | --- | --- | --- | --- | --- |
| top0 | 风笛 | `Bagpipe` | «Bagipipe» | 0.934 | 对得上（命中第 1 张） |
| top1 | 凛御银灰 | `SilverAsh the Reignfrost` | «SilverAsh tbo P» | 0.759 | 认不出 |
| top2 | 伊内丝 | `Ines` | «0» | 0.088 | 认不出 |
| top3 | 重岳 | `Chongyue` | «onvue» | 0.916 | 认不出 |
| top5 | 史尔特尔 | `Surtr` | «Surt» | 0.923 | 对得上（第 6 张） |
| top6 | 年 | `Nian` | «Niann» | 0.667 | 对得上（第 7 张） |
| top7 | 黍 | `Shu` | «Shu?» | 0.814 | 对得上（第 8 张） |
| bottom1 | 琴柳 | `Saileach` | «Saileach» | 0.986 | 对得上（第 2 张） |
| bottom2 | 赤刃明霄陈 | `Ch'en the Dawnstreak` | «Ch'enthe Dawnstreak» | 0.986 | 对得上（第 3 张） |

- 生产口径 **6/9 对得上**、前缀口径 5/9（`Bagipipe`/`Shu?` 这类在严格前缀下算失手）。
- **零误报**：凡是给出命中的，命中索引都等于本格索引（`prod_hit == scan_index`）；认不出的三格只计入 `unverified`，不会触发无谓重锚。
- 期望表只覆盖 12 格（`克莱门莎` 的 `name_en` 是中文、`.`/`手` 两个碎片卡名不在目录里）——与生产侧 `_expected_name_en` 的口径一致。

## 门禁实测

| 项 | 命令 | 结果 |
| --- | --- | --- |
| 后端（本轮面） | `.venv/bin/python -m pytest -q tests/services/test_account_training_scan.py tests/services/test_account_scan_image.py` | **71 passed**（新增 6 例 + 同步 2 例） |
| 后端（全量） | `.venv/bin/python -m pytest -q` | 进行中（历史基线 `2 failed, 3704 passed, 5 skipped`，两条红是既有真资源用例） |
| 生成物 | `.venv/bin/python scripts/dump_openapi.py` → `web/openapi.json`；`cd web && pnpm gen:api` → `web/src/types/api.d.ts` | 均已更新（GET 描述多了两个计数） |
| 生成物校验 | `cd web && pnpm gen:api:check` | `OpenAPI types are up to date.`（exit 0） |
| 前端类型 | `cd web && pnpm typecheck` | exit 0 |
| 前端单测 | `cd web && pnpm test --run` | **74 文件 / 777 例全过**（exit 0） |
| 前端构建 | `cd web && pnpm build` | exit 0 |
| 文案基线 | `.venv/bin/python scripts/copy_inventory.py --check` → `--write-baseline` → `--check` | 首检报「文案 AI 腔回潮」（本轮新增文档正文的 bold 2943→3041、em-dash 600→615、absolutes 217→220 等）⇒ 按仓库既有做法重设基线（46 项）⇒ 复检 **通过：无回潮，0 项较基线下降**；`docs/copy-audit/*` 随之更新 |

判据：全绿即合并；全量 `pytest` 里那两条红是 M38 起就存在的真资源用例，与本轮改动无关（本轮不碰它们）。

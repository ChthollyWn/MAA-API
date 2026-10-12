# M44 台账：仓库识别覆盖全部页签 + 道具图（`m44-depot-all-items`）

- 规格：`docs/superpowers/specs/2026-10-12-m44-depot-all-items.md`
- 计划：`docs/superpowers/plans/2026-10-12-m44-depot-all-items.md`
- 分支基点：`refactor/v2`（tag `v2-m43`）

## 波次进度

| 波次 | 内容 | 状态 |
| --- | --- | --- |
| W0 | 事实取证 | 完成（规格 §2） |
| W1 | 页签链真机实验 + 实现 | **候选 A 已验证通过**（见下） |
| W2 | 下限护栏 | 待做 |
| W3 | 道具图代理 `kind=item` + 仓库页展示 | 待做 |
| W4 | 文档、门禁、真机验收、合并打 tag | 待做 |

## W1 真机实验（2026-10-12 15:59–16:05）

**页签栏实测**（模拟器 2560×1440，游戏停在仓库页）：`全部 / 消耗物品 / 基础物品 / 养成材料 / 特别纪念`。
内核坐标（1280×720）里 `DepotAllTab` 的 roi `[450,0,300,138]` 正好覆盖「全部」（换算后 x 900–1500，页签中心 ≈1152 ✓），`DepotMaterialTab` 的 `[945,0,300,138]` 覆盖「养成材料」（≈1991 ✓）。

**做法（候选 A）**：在自定义资源层
`resource/maa-layers/custom/resource/tasks/MaaApi@DepotAllTabs.json` 里覆盖三个节点：

| 节点 | 覆盖内容 |
| --- | --- |
| `Depot` | `next` 从 `DepotMaterialTab` 改成 `DepotAllTab`（其余字段照抄内置定义） |
| `DepotAllTab` | `next` 改成 `DepotAllTabClicked`（内置是切回养成材料） |
| `DepotAllTabClicked` | 新增：等「全部」页签进入选中态 |

配套模板 `resource/maa-layers/custom/resource/template/Depot/DepotAllTabClicked.png` 从真机截图裁出（roi `[450,0,300,138]` ×2 倍 → 600×276）。内核资源在启动时加载，改层后需要重启服务。

**结果**：

| 指标 | 覆盖前 | 覆盖后 |
| --- | --- | --- |
| 一次 `Depot` 识别的条目数 | 26 | **77** |
| 跑完后游戏停在哪个页签 | 养成材料 | **全部**（截图确认） |

**仍待确认**：77 件是否等于该账号的全部道具类型。`7003 寻访凭证 / 7004 十连寻访凭证 / 3401 家具零件 / 3141 源石碎片` 仍不在库里——需要对照「全部」页签实际滚动到底的卡片数，判定是「用户确实没有」还是「内核只读了首屏」。这一步要在 W1 收尾时做（截图逐屏比对），不确定前不写进结论。

## 实测事故（W2 要修）

一次只读到 26 件的识别把 77 行整表替换成 26 行 —— `DepotService.replace_all` 没有下限护栏（M42 给森空岛做过 `SKLAND_MIN_CHARS`，仓库这条一直没有）。

# M7-01 MaaResource 增量加载实测

## 探针与环境

- 首次判别探针：.venv/bin/python scripts/probe_resource_merge.py（末行 PROBE FAILED；三项 native 行为不可由公开接口观测）
- M7 退路验收：.venv/bin/python scripts/probe_resource_merge.py --expect-fallback（需要访问官方 MaaResource main archive；本次以 `--archive /private/tmp/maa-resource-m7-main.zip` 运行，末行 PROBE FALLBACK REQUIRED，退出码 0）
- 可复跑指定归档：.venv/bin/python scripts/probe_resource_merge.py --archive /path/to/MaaResource-main.zip
- 实测时间：2026-09-23（Asia/Shanghai）
- 环境：macOS 15.4.1 arm64，Python 3.13.3，本机 resource/lib/maa/Darwin/libMaaCore.dylib，MaaCore v6.17.5
- 归档：GitHub MaaAssistantArknights/MaaResource main，压缩包 13,952,430 字节，version.json.last_updated=2026-09-20 12:47:33.000；ZIP CRC 检查通过，提取到 5,253 个文件
- 隔离：MaaCore AsstSetUserDir 指向每次运行新建的系统临时目录；空增量层、下载包及提取目录也都位于同一临时目录。未连接设备、未启动任务、未写入发行资源目录。临时目录在探针结束时自动删除。
- 下载注意：本机 Python HTTPS 证书链不可用时，脚本会回退到系统 curl，仍启用系统证书校验；不得关闭 TLS 校验。也可用 --archive 提供已下载归档。

## 实际观测

以下状态按探针输出原样记录。FAIL 表示该假设没有被本次公开接口证据证明，并不自动表示内核行为一定错误。

| 检查项 | 结果 | 证据与限制 |
|---|---|---|
| ZIP 完整性与资源目录提取 | PASS | zipfile.testzip() 未发现 CRC 错误，归档含 MaaResource-main/resource，只提取其资源子树。 |
| MaaCore 临时 user_dir | PASS | AsstSetUserDir(<temp>/user) 返回 true；版本接口返回 v6.17.5。 |
| 基础资源加载 | PASS | AsstLoadResource(<repo>/resource/lib/maa/Darwin) 返回 true。 |
| 增量目录缺项 | PASS（有限） | 先构造仅含空 resource/ 的增量目录，再调用 AsstLoadResource，返回 true。公开 API 只给 bool，无法区分“静默跳过全部文件”和“接受一个空层”，所以只证明空层不使调用失败。 |
| MaaResource 独有 Tile 数据可见 | FAIL / 不可观测 | 选择仅存在于归档 Tile overview 的关卡 DP-1（act1dp_01）。加载前 AsstGetMapLevelKey("DP-1") 四字段为空；加载 repo 层后返回 stage_id=act1dp_01、level_id=activities/act1dp/level_act1dp_01、name=入场吧绒绒！。重复加载两次后查询保持相同。这个间接证据证明关卡元数据经增量资源变得可查，但 AsstGetMapLevelKey 不读/不返回 Arknights-Tile-Pos 的坐标记录，不能把 Tile 坐标可见性记为 PASS。 |
| 重载不累积重复 Tile 记录 | FAIL / 不可观测 | 对 DP-1 重复 AsstLoadResource(repo) 两次，随后 map-key 查询内容不变。C API 只返回单条 map-key 记录，不提供 TilePack 条目数或路径枚举；查询稳定不足以排除内部列表多出重复项。 |
| 基础资源独有模板仍可用 | FAIL / 不可观测 | 基础目录有 10 个 template/Award/*.png，MaaResource 归档中没有 template/Award/；基础 task 的 AsstAppendTask("Award", "{}") 返回 task id 1。此返回只证明 task 被接受，不证明匹配器找到或使用了某张图。模板实际匹配需向 MaaCore 提供画面并执行相关任务，而本卡约束禁止连设备/跑游戏任务；C API 没有模板目录查询接口。 |
| 重载后模板匹配缓存失效 | FAIL / 不可观测 | MaaCore C API 未导出 template cache revision getter，也没有可在不连接设备、不跑游戏任务的情况下给定图像并查询 matcher 命中的接口。重复加载和 map-key 稳定性不能证明 matcher cache revision 递增。 |

探针默认运行最终输出 PROBE FAILED，因为 Tile 坐标读取、TilePack 重复行计数、真实模板匹配和 matcher cache 失效均未被公开接口实证；不能把不可观测假设包装成 PASS。M7 按 docs/07 §3.3 已选择覆盖式回退，因此 `.venv/bin/python scripts/probe_resource_merge.py --expect-fallback` 在重新实测后会以退出码 0 和末行 `PROBE FALLBACK REQUIRED` 结束，但只有归档/隔离目录/基础加载/间接关卡查询等前置检查通过且余项明确为不可观测时才接受；任何实际基础调用失败仍以非零退出。

## 设计影响与后续验证

现有实证确认基础资源和 MaaResource 增量层能够依次加载，且 repo-only 关卡元数据在增量加载后变为可查；本次不能据此批准独立目录方案所依赖的全部假设。特别是 AsstGetMapLevelKey 结果不是 Tile 坐标证据。

实施策略已确认：按 docs/07 §3.3 的退路，把 MaaResource 文件覆盖合并到 `<maa_path>/resource/`，资源更新时先复制整个资源树到同盘 staging、叠加 repo 文件，再以目录 rename 原子替换；上一版完整资源树保留在 `<layers>/repo-backup/` 供回滚。repo 原始包保持在 `<layers>/repo/`，所以 MaaCore 整体更新后可在 `supervisor.start()` 前重新合并。repo 更新后重启 MaaCore 子进程，确保不可观测的 TilePack/模板匹配缓存全部重新初始化。OTA 与 custom 仍在 MaaCore 目录外按 cache/custom 层加载。这样不依赖已无法公开验证的 repo 增量合并或运行期 matcher cache 失效。

要把剩余项变成 PASS，需要增加可判别观测：Tile 坐标查询/关卡地图运算的公开无设备入口；TilePack 可枚举数量或等价的重复记录判据；基于固定本地图像、且不会执行游戏操作的模板 matcher 探针；以及可观测 matcher cache revision 或明确的缓存命中失效结果。当前公开 FFI 不提供这些观测口。

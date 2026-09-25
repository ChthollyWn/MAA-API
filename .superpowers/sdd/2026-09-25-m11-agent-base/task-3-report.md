# Task 3 自审报告：设备与原子操作工具

## 实现

- 新增 `device.register_tools(registry)`：注册 `get_device_status`、`list_devices`、`reconnect_device`，通过应用注入的 `DeviceManager` 查询、扫描与重连。
- 新增 `raw.register_tools(registry)`：注册截图、点击、滑动、长按、输入、白名单按键和回主界面。截图仅在设备已连接时调用 `DeviceManager.screenshot(backend="core")`，返回 PNG base64、尺寸、后端与 UTC 采集时间。
- 五种需会话级授权的动作由现有 `PolicyEngine` 按工具名判定；截图、回主界面及设备工具保持 SAFE。所有 raw 动作在调用服务前持有 `PipelineRunner.operation_lock`，锁内用 `PipelineRepository.current()` 拒绝运行中的流水线并抛 `PIPELINE_ALREADY_RUNNING`。
- `key_event` 将允许的 BACK、HOME、ENTER、DEL、APP_SWITCH 写进 schema 枚举；工具参数拒绝额外字段，不提供自由 ADB 命令。回主界面通过新增的 `DeviceManager.back_to_home()` 委托 MaaCore app service。

## RED / GREEN 证据

- 首轮工具契约测试在实现前运行：10 项失败，均指向缺少 `maa_api.agent.tools.device` 模块。
- DeviceManager 回主界面委托测试在封装方法实现前运行：因 `DeviceManager` 没有 `back_to_home` 而失败。
- 白名单 schema 契约测试在 `key_event` schema 还是普通字符串时运行：因 schema 没有 `enum` 而失败。
- 重连失败映射契约测试在补全 REST 对应的错误详情前运行：缺少命令、输出和提示字段而失败。
- 最终定向验证：`.venv/bin/python -m pytest -q -o addopts= tests/agent/test_device_raw_tools.py tests/services/test_device_service.py tests/api/test_device_router.py tests/api/test_device_lifecycle.py`，结果 **69 passed, 1 warning**；警告为 Starlette 对 `httpx` TestClient 的弃用提醒。
- `.venv/bin/python -m compileall -q`（本任务两个工具模块、服务及两份测试）通过；`git diff --check` 通过。ruff 未运行：当前 `.venv` 没有安装 `ruff`。

## 自审

- 检查范围内未出现直接导入/调用 `CoreClient`、任意 shell 参数、`force` 绕过或越权通路。ADB 坐标动作经过 `DeviceManager` 固定方法，按键有白名单。
- 所有五种授权动作及 SAFE 工具都由 `PolicyEngine` 行为测试确认；流水线冲突覆盖五种危险动作及 SAFE `back_to_home`，冲突时不触碰设备。另验证原子服务调用期间共享 operation lock 保持锁定。
- 设备状态、扫描、重连、截图与原子动作结果均为 JSON 可编码值；截图字节转为 base64。
- 仅编辑并提交 Task 3 的工具模块、对应服务封装与行为测试；没有修改 Task 1/2 的 registry、policy、其他工具或并行测试文件。

## 提交

- Commit SHA 记录在本报告所在提交的 Git 元数据中。

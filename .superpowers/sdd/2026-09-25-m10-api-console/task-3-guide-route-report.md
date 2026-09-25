# Task 3：接入指南直达路由

## 变更

- 注册 `/more/api-console/guide`，仍由 API 调试台页面承载。
- 直达路由在调试台中显示完整接入指南，并提供返回 API 调试台的链接。
- 现有指南预览增加「打开完整指南」链接；预览与直达页面都渲染 `GuideMarkdown`，内容继续来自 `docs/14-开放API接入指南.md` 单一 Markdown 源。
- 在 API 调试台页面集成测试中覆盖直达路由、指南内容、返回链接和接口列表。

## TDD 与验证

- RED：先添加直达路由测试，运行 `cd web && pnpm test --run src/features/api-console/api-console.page.test.tsx`。新用例因旧路由匹配到 Not Found，找不到「API 调试台」标题而失败；同文件原有 9 项通过。
- GREEN：实现后再次运行上述聚焦测试，10/10 项通过。
- `cd web && pnpm typecheck`：通过。
- `cd web && pnpm test --run`：95/96 项通过；未通过项为未修改的 `src/routes/logs/logs.test.tsx` 中「keeps the scroll position when a new log arrives while the reader is away from the bottom」，期望 `scrollTop` 为 100，实际为 1000。
- `git diff --check`：通过。

主 agent 应按项目约定重新运行验证命令和后端测试后再审核完成状态。

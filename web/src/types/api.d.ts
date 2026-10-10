export type $Read<T> = {
    readonly $read: T;
};
export type $Write<T> = {
    readonly $write: T;
};
export type Readable<T> = T extends $Write<any> ? never : T extends $Read<infer U> ? Readable<U> : T extends (infer E)[] ? Readable<E>[] : T extends object ? {
    [K in keyof T as NonNullable<T[K]> extends $Write<any> ? never : K]: Readable<T[K]>;
} : T;
export type Writable<T> = T extends $Read<any> ? never : T extends $Write<infer U> ? Writable<U> : T extends (infer E)[] ? Writable<E>[] : T extends object ? {
    [K in keyof T as NonNullable<T[K]> extends $Read<any> ? never : K]: Writable<T[K]>;
} & {
    [K in keyof T as NonNullable<T[K]> extends $Read<any> ? K : never]?: never;
} : T;
export interface paths {
    "/api/account/depot": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 读取仓库
         * @description 仓库识别结果，按物品分类分组展示的只读列表。
         *
         *     - `category` 与 `query` 都是筛选条件，`total` 是筛选后的总数
         *     - 仓库识别只看养成材料页签，龙门币与作战记录不在其中
         *     - 识别时间是整表替换时写下的，`stale` 按 `agent.depot_stale_days` 判定
         */
        get: operations["account_list_account_depot"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/account/operator-diffs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 读取识别差异
         * @description 识别覆盖掉手动值的差异列表：干员、字段、手动值、识别值、时间。
         *
         *     - 默认只返回未处理的差异；`include_dismissed=true` 连已「知道了」的一起返回
         *     - `total` 是未处理差异的总数（不受 `limit` 影响）
         */
        get: operations["account_list_account_operator_diffs"];
        put?: never;
        post?: never;
        /**
         * 标记差异已知
         * @description 把识别差异标记为已知（页面上的「知道了」）。
         *
         *     - 请求体给 `ids` 逐条处理，省略表示全部
         *     - 幂等：重复标记已处理的差异不计入 `dismissed`
         *     - 找不到的 id 不报错，只在 `dismissed` 里少计数
         */
        delete: operations["account_dismiss_account_operator_diffs"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/account/operators": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 读取干员表
         * @description 账号识别到的干员列表，带筛选与分页。
         *
         *     - 默认只返回已拥有的干员（`owned_only=true`）；未拥有的行来自识别的 `all_opers`
         *     - `total` 是筛选后的总数，不是当前页条数
         *     - 每行的 `field_sources` 标出各字段来自识别、手动还是 Agent
         */
        get: operations["account_list_account_operators"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/account/operators/{char_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 读取单名干员
         * @description 按 `char_id` 读一条干员记录（编辑抽屉打开时用）。
         */
        get: operations["account_get_account_operator"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /**
         * 编辑干员数据
         * @description 手动编辑一名干员的字段，只写请求体里给出的字段。
         *
         *     - 精英化上限按稀有度：1–2★ 精零、3★ 精一、4–6★ 精二
         *     - 专精只在精二且技能等级 7 时可填；模组只在精二时可填，类型 X/Y/D/A、等级 1–3
         *     - 写入后这些字段的 `field_sources` 标 `manual`（或 `source=agent`）
         *     - 下次识别若覆盖了手动值且值不同，会记一条差异并在 `GET /api/account/operator-diffs` 里列出
         *     - 违反字段约束返回 422 `VALIDATION_ERROR`，`details.field` 指出字段
         */
        patch: operations["account_patch_account_operator"];
        trace?: never;
    };
    "/api/account/summary": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 读取账号数据概要
         * @description 干员与仓库的计数、识别时间与是否过期，以及未处理的识别差异条数。
         *
         *     - 过期阈值由 `agent.box_stale_days`（默认 14 天）与 `agent.depot_stale_days`（默认 3 天）决定
         *     - 从未识别时 `recognized_at` 为 `null` 且 `stale` 为 `true`
         */
        get: operations["account_get_account_summary"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/audits": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 分页读取 Agent 工具审计 */
        get: operations["agent_list_audits"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/audits/{audit_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 读取单条 Agent 工具审计 */
        get: operations["agent_get_audit"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/capabilities/check": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** 检查 Agent 模型能力 */
        post: operations["agent_check_capabilities"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/knowledge": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 读取 Agent 知识库条目 */
        get: operations["agent_list_knowledge"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/knowledge/{entry_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 读取 Agent 知识条目详情 */
        get: operations["agent_get_knowledge_entry"];
        /**
         * 写入 Agent 知识条目
         * @description 写入完整的生效内容：新建自定义条目，或修改、停用内置条目。
         *
         *     内置条目的 ``kind`` 不可修改，改了返回 400 ``KNOWLEDGE_BUILTIN_READONLY``。
         */
        put: operations["agent_put_knowledge_entry"];
        post?: never;
        /**
         * 删除 Agent 知识条目
         * @description 删除自定义条目，或撤销对内置条目的修改（恢复默认）。
         *
         *     内置条目未被修改过时返回 400 ``KNOWLEDGE_BUILTIN_READONLY``；要停用它请写入
         *     ``enabled=false``。
         */
        delete: operations["agent_delete_knowledge_entry"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/questions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 分页读取 Agent 提问 */
        get: operations["agent_list_questions"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/questions/{question_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 读取单条 Agent 提问 */
        get: operations["agent_get_question"];
        put?: never;
        /** 提交 Agent 提问的选择 */
        post: operations["agent_answer_question"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/questions/{question_id}/skip": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** 跳过 Agent 提问 */
        post: operations["agent_skip_question"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/sessions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 分页读取 Agent 会话 */
        get: operations["agent_list_sessions"];
        put?: never;
        /** 创建 Agent 会话 */
        post: operations["agent_create_session"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/sessions/{session_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 读取 Agent 会话详情 */
        get: operations["agent_get_session_detail"];
        put?: never;
        post?: never;
        /** 删除 Agent 会话 */
        delete: operations["agent_delete_session"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/sessions/{session_id}/atomic-grant": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /** 撤销 Agent 会话的原子操作授权 */
        delete: operations["agent_revoke_atomic_grant"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/sessions/{session_id}/copilot-grant": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /**
         * 撤销 Agent 会话的作业授权
         * @description 把该会话生效中的作业授权标 `revoked`（规格 §9.6），前端授权条的「撤销」入口。
         *
         *     幂等：没有生效中的授权同样返回 204。
         */
        delete: operations["agent_revoke_copilot_grant"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/sessions/{session_id}/interrupt": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * 中断该会话正在执行的一轮
         * @description 中断该会话正在执行的一轮。
         *
         *     幂等：没有活动轮次时返回 200 且 ``interrupted=false``。只中断这一轮，
         *     不影响流水线与队列。
         */
        post: operations["agent_interrupt_session_turn"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/sessions/{session_id}/messages": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 按序读取 Agent 会话消息 */
        get: operations["agent_list_session_messages"];
        put?: never;
        /** 发送 Agent 消息并接收 SSE 事件 */
        post: operations["agent_stream_session_message"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/tools": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 读取 Agent 工具清单 */
        get: operations["agent_list_tools"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/tools/{name}/invoke": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** 调用 Agent 工具 */
        post: operations["agent_invoke_tool"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/confirmations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 分页读取人工确认请求 */
        get: operations["confirmations_list_confirmations"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/confirmations/{confirmation_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 读取人工确认详情与执行结果 */
        get: operations["confirmations_get_confirmation"];
        put?: never;
        /**
         * 批准或拒绝人工确认
         * @description 批准或拒绝一条确认。
         *
         *     ``overrides`` 只有 ``grant_copilot_attempts`` 用得上（规格 §9.6）：用户在作业授权
         *     卡片上改的尝试次数 / 成功标准 / 是否允许助战，批准时写进授权记录。
         */
        post: operations["confirmations_resolve_confirmation"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/core/back_to_home": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** 返回游戏主界面 */
        post: operations["core_back_to_home"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/device/candidates": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 扫描设备候选项 */
        get: operations["device_list_device_candidates"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/device/click": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** 执行单点点击（坐标为设备像素，与截图一致） */
        post: operations["device_click_device"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/device/input_text": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** 输入设备文本 */
        post: operations["device_input_device_text"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/device/key_event": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** 发送设备按键事件 */
        post: operations["device_device_key_event"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/device/list": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 扫描已连接设备 */
        get: operations["device_list_devices"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/device/long_press": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** 执行长按操作（坐标为设备像素，与截图一致） */
        post: operations["device_long_press_device"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/device/reconnect": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** 手动重连设备 */
        post: operations["device_reconnect_device"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/device/screenshot": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 获取实时设备截图 */
        get: operations["device_device_screenshot"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/device/status": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 读取设备连接状态 */
        get: operations["device_device_status"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/device/swipe": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** 执行滑动操作（坐标为设备像素，与截图一致） */
        post: operations["device_swipe_device"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/images/{sha256}/full": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 读取截图原图
         * @description 按内容哈希读取 JPEG 原图，响应可长期缓存。
         */
        get: operations["screenshots_get_screenshot_full"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/images/{sha256}/thumb": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 读取截图缩略图
         * @description 按内容哈希读取长边 320px 的 JPEG 缩略图，响应可长期缓存。
         */
        get: operations["screenshots_get_screenshot_thumb"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/notifications/channels": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 读取通知通道列表 */
        get: operations["notifications_list_notification_channels"];
        put?: never;
        /** 创建通知通道 */
        post: operations["notifications_create_notification_channel"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/notifications/channels/{channel_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** 更新通知通道 */
        put: operations["notifications_update_notification_channel"];
        post?: never;
        /** 删除通知通道 */
        delete: operations["notifications_delete_notification_channel"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/notifications/channels/{channel_id}/test": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** 发送通知测试消息 */
        post: operations["notifications_test_notification_channel"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/notifications/push-subscriptions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 列出 Web Push 订阅
         * @description 为保护凭据，响应不含 `endpoint` 全文与 `p256dh` / `auth`，只返回设备来源、登记时间与失效状态。
         *
         *     - `include_disabled=true` 时同时返回已失效的订阅
         */
        get: operations["notifications_list_push_subscriptions"];
        put?: never;
        /**
         * 登记 Web Push 订阅
         * @description 登记浏览器推送订阅，接受 `PushSubscription.toJSON()` 的形状。
         *
         *     - 同一 `endpoint` 重复登记是幂等更新：返回 200 与既有资源，不新增行
         *     - `endpoint` 必须是 https 绝对 URL；`keys.p256dh` 必须是 65 octet 的未压缩 P-256 点；`keys.auth` 必须是 16 octet
         */
        post: operations["notifications_create_push_subscription"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/notifications/push-subscriptions/delete": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * 按 endpoint 注销当前浏览器的订阅
         * @description 按 `endpoint` 精确注销当前浏览器的订阅。
         *
         *     - `endpoint` 放在请求体，避免它进入访问日志与浏览器历史
         *     - 幂等：找不到对应订阅同样返回 204
         */
        post: operations["notifications_delete_push_subscription_by_endpoint"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/notifications/push-subscriptions/{subscription_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /** 删除 Web Push 订阅 */
        delete: operations["notifications_delete_push_subscription"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/notifications/vapid-public-key": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 读取 Web Push 的 VAPID 公钥
         * @description 返回 VAPID 公钥，用于浏览器端 `pushManager.subscribe` 的 `applicationServerKey`。
         *
         *     - 未配置私钥或私钥不可用时返回 503 `PUSH_NOT_CONFIGURED`
         */
        get: operations["notifications_get_vapid_public_key"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/pipelines": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 查询流水线历史 */
        get: operations["pipelines_list_pipelines"];
        put?: never;
        /** 提交流水线 */
        post: operations["pipelines_submit_pipeline"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/pipelines/current": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 获取当前流水线 */
        get: operations["pipelines_current_pipeline"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/pipelines/{pipeline_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 获取流水线详情 */
        get: operations["pipelines_get_pipeline"];
        put?: never;
        post?: never;
        /** 取消流水线 */
        delete: operations["pipelines_cancel_pipeline"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/pipelines/{pipeline_id}/logs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 查询流水线日志 */
        get: operations["pipelines_list_pipeline_logs"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/pipelines/{pipeline_id}/retry": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** 重放流水线 */
        post: operations["pipelines_retry_pipeline"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/pipelines/{pipeline_id}/screenshots": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 查询流水线截图 */
        get: operations["pipelines_list_pipeline_screenshots"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/pipelines/{pipeline_id}/tasks": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 获取流水线任务列表 */
        get: operations["pipelines_list_pipeline_tasks"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/queue": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 获取队列快照 */
        get: operations["queue_get_queue"];
        put?: never;
        post?: never;
        /** 取消所有待执行流水线 */
        delete: operations["queue_clear_queue"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/queue/pause": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** 暂停队列消费 */
        post: operations["queue_pause_queue"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/queue/resume": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** 恢复队列消费 */
        post: operations["queue_resume_queue"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/queue/{pipeline_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /** 调整待执行流水线优先级 */
        patch: operations["queue_reprioritize_pipeline"];
        trace?: never;
    };
    "/api/resources/copilots": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 列出已保存的 Copilot 作业
         * @description 返回已校验作业的元数据，不含作业 JSON 内容；供流水线与定时任务编辑器选择 `asset_id`。
         */
        get: operations["resources_list_copilot_assets"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/resources/copilots/{asset_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 读取单个 Copilot 作业（含作业 JSON）
         * @description 返回该作业的资源 wire 与 `content`（作业 JSON 原文），供「作业」页查看与下载。
         *
         *     - 小作业的 `content` 在库里，大作业从 `agent-assets/copilot/` 下的文件读
         *     - 不存在、不是 Copilot 作业返回 `RESOURCE_ASSET_NOT_FOUND`
         *     - 文件缺失或损坏返回 `RESOURCE_LOAD_FAILED`
         */
        get: operations["resources_get_copilot_asset"];
        put?: never;
        post?: never;
        /**
         * 删除一个 Copilot 作业
         * @description 删除作业资源记录；大作业落盘的文件一并清掉。不存在、不是 Copilot 作业返回 `RESOURCE_ASSET_NOT_FOUND`。
         */
        delete: operations["resources_delete_copilot_asset"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/resources/copilots/{asset_id}/retain": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * 设置 Copilot 作业是否保留
         * @description 写入 `meta.retain`（规格 §9.11）：被保留的作业不会被每日清理删掉，可以再取消。返回更新后的资源 wire（不含 `content`）。
         */
        post: operations["resources_retain_copilot_asset"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/resources/items": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 列出 Fight.drops 可用物品
         * @description 列出当前内核物品索引里的物品，按名称与物品 ID 稳定排序。
         *
         *     - `icon_url` 指向同源图片端点，对应图片不可用时为 `null`
         *     - 索引缺失或无效时返回 `RESOURCE_LOAD_FAILED`
         */
        get: operations["resources_list_items"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/resources/items/icon": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 读取索引中物品的图标
         * @description 返回物品索引中该物品的 PNG 图标；索引里没有该物品或图标文件缺失时返回 `RESOURCE_ASSET_NOT_FOUND`。
         */
        get: operations["resources_get_item_icon"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/schedules": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 读取定时任务 */
        get: operations["schedules_list_schedules"];
        put?: never;
        /** 新建定时任务 */
        post: operations["schedules_create_schedule"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/schedules/{schedule_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 读取定时任务与近期执行结果 */
        get: operations["schedules_get_schedule"];
        /** 全量替换定时任务 */
        put: operations["schedules_update_schedule"];
        post?: never;
        /** 删除定时任务 */
        delete: operations["schedules_delete_schedule"];
        options?: never;
        head?: never;
        /** 局部更新定时任务 */
        patch: operations["schedules_patch_schedule"];
        trace?: never;
    };
    "/api/schedules/{schedule_id}/run": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** 立即运行定时任务 */
        post: operations["schedules_run_schedule"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/screenshots": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 查询截图列表
         * @description 按流水线、触发原因和时间筛选截图，并以 page/size 分页。
         */
        get: operations["screenshots_list_screenshots"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/screenshots/{id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 读取截图内容
         * @description 返回截图图像字节；as=base64 时返回带元数据的 JSON。
         */
        get: operations["screenshots_get_screenshot"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/settings": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 读取生效配置 */
        get: operations["settings_get_settings"];
        /** 批量写入并热生效配置 */
        put: operations["settings_update_settings"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/settings/reset": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** 清除设置项数据库覆盖 */
        post: operations["settings_reset_settings"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/settings/schema": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 读取设置项元信息 */
        get: operations["settings_get_settings_schema"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/snippets": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 读取 API 调试台收藏 */
        get: operations["snippets_list_snippets"];
        put?: never;
        /** 创建 API 调试台收藏 */
        post: operations["snippets_create_snippet"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/snippets/{snippet_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 读取 API 调试台收藏详情 */
        get: operations["snippets_get_snippet"];
        /** 更新 API 调试台收藏 */
        put: operations["snippets_update_snippet"];
        post?: never;
        /** 删除 API 调试台收藏 */
        delete: operations["snippets_delete_snippet"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/sources/cache": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /**
         * 清空外部数据源缓存
         * @description 清空全部或单个来源的内存缓存；返回清掉的条数。
         */
        delete: operations["sources_clear_sources_cache"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/sources/status": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 查外部数据源状态
         * @description 各来源的开关、状态与缓存统计；供设置页显示与排查。
         */
        get: operations["sources_get_sources_status"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/system/auth/cookie": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * 换取认证 cookie
         * @description 用请求头里的 token 换取一枚 `HttpOnly; SameSite=Lax` 的 `maa_token` cookie，供浏览器 WebSocket 握手自动携带，响应无体（204）。
         *
         *     - 副作用：下发 `Set-Cookie`，值即 `access_token`、`Path=/`；`Secure` 由请求的 `X-Forwarded-Proto` 决定
         *     - 免鉴权模式（未配置 `access_token`）返回 204 但不下发 cookie
         *     - 仅头部 token 可调用：仅凭 cookie 返回 403，缺少 token 或不匹配返回 401
         */
        post: operations["system_exchange_cookie"];
        /**
         * 清除认证 cookie
         * @description 清除 `maa_token` cookie，供前端「登出」时调用，响应无体（204）。
         *
         *     - 副作用：下发 `Set-Cookie`（空值 + `Max-Age=0`），浏览器随后删除该 cookie
         *     - 仅头部 token 可调用：仅凭 cookie 返回 403，缺少 token 或不匹配返回 401
         *     - 幂等：没有 cookie 时调用同样返回 204
         */
        delete: operations["system_clear_cookie"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/system/health": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 获取健康状态
         * @description 返回服务自身状态与运行态概览：内核、设备、队列。免鉴权，无需携带凭据。
         *
         *     - `auth_enabled` 由是否配置 `access_token` 决定；为 `false` 时前端跳过登录页
         *     - `started_at` 是进程启动时间（ISO 8601），尚未写入时为 `null`
         *     - 运行时对象尚未就绪时 `core` / `device` / `queue` 为 `null`，仍返回 200
         */
        get: operations["system_health"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/system/logs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 查询历史日志
         * @description 按来源、级别、流水线、任务、logger、内容与时间过滤；默认使用 id 游标分页。
         */
        get: operations["system_list_logs"];
        put?: never;
        post?: never;
        /**
         * 清理历史日志
         * @description 按来源或时间界限删除日志，至少提供一个条件。
         */
        delete: operations["system_delete_logs"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/system/logs/export": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 导出日志
         * @description 以 text 或 JSON Lines 附件导出经过过滤的日志，最多导出 100000 条。
         */
        get: operations["system_export_logs"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/tasks/types": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 获取任务类型 schema
         * @description 返回全部任务类型的参数 schema，供前端动态生成表单。只读、无副作用。
         *
         *     - `schema` 为标准 JSON Schema；中文标签、分组、控件提示与风险标记挂在 `x-label` / `x-group` / `x-widget` / `x-enum-labels` / `x-risk` / `x-depends-on` 上，取值范围走 `minimum` / `maximum` / `enum` 等原生关键字
         *     - `client_type` / `server` 的 `default` 是当前生效的全局渠道默认值（默认 Bilibili / CN）；提交时不传这两个字段仍按同一默认值注入，显式传 `null` 表示留空
         *     - 返回当前支持的全部任务类型，不接受分页参数；`lang` 当前仅支持 `zh`，其它值返回 400 `INVALID_PARAMETER`
         */
        get: operations["tasks_list_types"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/tasks/types/{type_name}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 获取单个任务类型 schema
         * @description 按类型名返回单个任务类型的 schema，结构与 `GET /api/tasks/types` 的 `items` 元素一致（不套分页信封）。只读、无副作用。
         *
         *     - 渠道字段的 `default` 是当前生效的全局渠道默认值
         *     - `type_name` 不在支持列表内时返回 400 `UNKNOWN_TASK_TYPE`
         */
        get: operations["tasks_get_type"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/tasks/validate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * 校验任务参数
         * @description 校验并规范化任务数组，返回投递给内核的最终参数与原始提交。不落库、不入队、不碰内核。
         *
         *     - 未显式指定 `client_type` / `server` 的任务会注入当前生效的全局渠道默认值（默认 Bilibili / CN）；显式传 `null` 表示留空，不注入
         *     - `params` 是投递给内核的形态（已剔除 null、已注入默认值），`raw_params` 是原始提交快照（保留显式 null）
         *     - 任务数 1–32，越界返回 422 `VALIDATION_ERROR`
         */
        post: operations["tasks_validate"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/tasks/{task_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 获取单任务详情 */
        get: operations["tasks_get_task"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/updates": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 分页查询更新历史 */
        get: operations["updates_list_updates"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/updates/core": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** 受理内核更新 */
        post: operations["updates_update_core"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/updates/core/rollback": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** 受理内核回滚 */
        post: operations["updates_rollback_core"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/updates/game": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** 受理游戏更新 */
        post: operations["updates_update_game"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/updates/resource": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** 受理活动资源更新 */
        post: operations["updates_update_resource"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/updates/status": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 检查内核、资源与游戏更新 */
        get: operations["updates_update_status"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/updates/{update_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 读取更新进度与记录 */
        get: operations["updates_get_update"];
        put?: never;
        post?: never;
        /** 取消进行中的更新 */
        delete: operations["updates_cancel_update"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/updates/{update_id}/retry": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** 受理失败更新重试 */
        post: operations["updates_retry_update"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /**
         * AnswerRequest
         * @description ``POST /api/agent/questions/{id}`` 的入参。
         *
         *     ``selected`` 是必填的字符串数组；空数组或不合法的选项返回 400
         *     ``QUESTION_SELECTION_INVALID``。``multiple=false`` 时只能给一项。
         */
        AnswerRequest: {
            /** Selected */
            selected: string[];
        };
        /** ApiSnippetPage */
        ApiSnippetPage: {
            /** Items */
            items: components["schemas"]["ApiSnippetView"][];
            /** Total */
            total: number;
        };
        /** ApiSnippetView */
        ApiSnippetView: {
            /** Body */
            body: unknown | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Headers */
            headers: {
                [key: string]: string;
            };
            /** Id */
            id: string;
            /** Method */
            method: string;
            /** Name */
            name: string;
            /** Path */
            path: string;
            /** Path Params */
            path_params: {
                [key: string]: unknown;
            };
            /** Query */
            query: {
                [key: string]: unknown;
            };
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
        };
        /**
         * ApiSnippetWrite
         * @description Validated full snippet payload; credential fields are stripped by the service.
         */
        ApiSnippetWrite: {
            /** Body */
            body?: unknown | null;
            /** Headers */
            headers?: {
                [key: string]: string;
            };
            /**
             * Method
             * @enum {string}
             */
            method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE" | "HEAD" | "OPTIONS" | "TRACE";
            /** Name */
            name: string;
            /** Path */
            path: string;
            /** Path Params */
            path_params?: {
                [key: string]: unknown;
            };
            /** Query */
            query?: {
                [key: string]: unknown;
            };
        };
        /**
         * AuditStatus
         * @enum {string}
         */
        AuditStatus: "pending" | "success" | "failed" | "rejected" | "expired";
        /**
         * AwardInput
         * @description 领取各种奖励。
         * @example {
         *       "award": true,
         *       "mail": true,
         *       "name": "Award"
         *     }
         */
        AwardInput: {
            /**
             * Award
             * @description 领取每日与每周任务奖励（默认开启）
             * @default true
             */
            award: boolean | null;
            /**
             * Enable
             * @description 关掉后执行流水线时会跳过本任务（默认开启）
             * @default true
             */
            enable: boolean | null;
            /**
             * Mail
             * @description 领取所有邮件奖励（默认关闭）
             * @default false
             */
            mail: boolean | null;
            /**
             * Mining
             * @description 领取限时开采许可的合成玉（默认关闭）
             * @default false
             */
            mining: boolean | null;
            /**
             * @description 由任务类型选择器决定，不用填 (enum property replaced by openapi-typescript)
             * @enum {string}
             */
            name: "Award";
            /**
             * Orundum
             * @description 领取幸运墙的合成玉（默认关闭）
             * @default false
             */
            orundum: boolean | null;
            /**
             * Recruit
             * @description 领取限定池的每日免费单抽（默认关闭）
             * @default false
             */
            recruit: boolean | null;
            /**
             * Specialaccess
             * @description 领取五周年赠送的月卡奖励（默认关闭）
             * @default false
             */
            specialaccess: boolean | null;
        };
        /**
         * CacheClearResponse
         * @description 清空缓存的结果。
         */
        CacheClearResponse: {
            /**
             * Cleared
             * @description 清掉的条数
             */
            cleared: number;
            /**
             * Source
             * @description 清空的来源；null 表示全部
             */
            source: string | null;
        };
        /**
         * CallerType
         * @enum {string}
         */
        CallerType: "rest" | "internal";
        /** ClickRequest */
        ClickRequest: {
            /**
             * Force
             * @default false
             */
            force: boolean;
            /** X */
            x: number;
            /** Y */
            y: number;
        };
        /**
         * CloseDownInput
         * @description 关闭游戏：任务结束后关闭客户端。
         * @example {
         *       "client_type": "Bilibili",
         *       "name": "CloseDown"
         *     }
         */
        CloseDownInput: {
            /**
             * Client Type
             * @description 要关闭的客户端渠道；不填时用设置里选的默认渠道
             */
            client_type?: ("Official" | "Bilibili" | "txwy" | "YoStarEN" | "YoStarJP" | "YoStarKR") | null;
            /**
             * Enable
             * @description 关掉后执行流水线时会跳过本任务（默认开启）
             * @default true
             */
            enable: boolean | null;
            /**
             * @description 由任务类型选择器决定，不用填 (enum property replaced by openapi-typescript)
             * @enum {string}
             */
            name: "CloseDown";
        };
        /**
         * ConfirmationStatus
         * @enum {string}
         */
        ConfirmationStatus: "pending" | "approved" | "rejected" | "expired";
        /**
         * CopilotAssetOut
         * @description Saved, validated Copilot metadata for queue editors.
         */
        CopilotAssetOut: {
            /** Checksum */
            checksum: string;
            /** Created At */
            created_at: string | null;
            /** Description */
            description: string | null;
            /** Enabled */
            enabled: boolean;
            /** Id */
            id: string;
            /** Kind */
            kind: string;
            /** Meta */
            meta: {
                [key: string]: unknown;
            } | null;
            /** Name */
            name: string;
            /** Updated At */
            updated_at: string | null;
        };
        /** CopilotAssetPageOut */
        CopilotAssetPageOut: {
            /** Items */
            items: components["schemas"]["CopilotAssetOut"][];
            /** Page */
            page: number;
            /** Size */
            size: number;
            /** Total */
            total: number;
        };
        /**
         * CopilotInput
         * @description 执行已校验并上传的 Copilot 作业。
         *
         *     ``asset_id`` 是本服务的作业资源 id；内核要的 ``filename`` 由
         *     :class:`~maa_api.services.pipeline_runner.PipelineRunner` 在执行前解析（作业 JSON 的
         *     协议校验在 :mod:`maa_api.services.copilot.rules`，D2 修复后的口径）。
         *
         *     ``support_unit_usage`` 是 M29 的自动重试用到的参数（规格 §9.1 第 3 条）：0 不用助战，
         *     1 缺一名干员时借助战补位。取值域照上游放宽到 0–3，本里程碑只写 0 / 1。
         * @example {
         *       "asset_id": "asset-1",
         *       "formation": false,
         *       "name": "Copilot"
         *     }
         * @example {
         *       "asset_id": "asset-1",
         *       "formation": true,
         *       "name": "Copilot",
         *       "support_unit_usage": 1
         *     }
         */
        CopilotInput: {
            /**
             * Asset Id
             * @description 要执行的已上传作业（不支持运行中设置）
             */
            asset_id: string;
            /**
             * Formation
             * @description 执行作业前按作业重新编队（默认关闭）
             * @default false
             */
            formation: boolean;
            /**
             * @description 由任务类型选择器决定，不用填 (enum property replaced by openapi-typescript)
             * @enum {string}
             */
            name: "Copilot";
            /**
             * Support Unit Usage
             * @description 0 不用助战；1 缺一名时借助战补位（仅在重新编队时有效）
             * @default 0
             */
            support_unit_usage: number;
        };
        /**
         * CoreHealth
         * @description 内核状态：运行状态、进程 pid 与重启代际。
         */
        CoreHealth: {
            /** Generation */
            generation: number;
            /** Pid */
            pid: number | null;
            /** State */
            state: string;
        };
        /** CoreRollbackRequest */
        CoreRollbackRequest: {
            /**
             * Force Interrupt
             * @default false
             */
            force_interrupt: boolean;
        };
        /** CoreUpdateRequest */
        CoreUpdateRequest: {
            /**
             * Channel
             * @default stable
             */
            channel: string;
            /**
             * Force
             * @default false
             */
            force: boolean;
            /**
             * Force Interrupt
             * @default false
             */
            force_interrupt: boolean;
            /** Version */
            version?: string | null;
        };
        /**
         * CustomInput
         * @description 自定义任务（D7、规格 §9.1 第 3 条）：跑 ``tasks.json`` 里已定义的任务链。
         *
         *     形状照上游 v6.18.0 的 ``CustomTask::set_params``（``src/MaaCore/Task/Interface/
         *     CustomTask.cpp`` 与集成文档「任务参数 · ``Custom``」）：
         *
         *     - ``task_names``（必填，array<string>）：执行数组中**第一个匹配上**的任务及其
         *       ``next`` 等；想跑多条就 append 多个 Custom 任务；
         *     - ``params``（可选，object）：任务附加参数，目前只有像素画/MiniGame 这类任务读它
         *       （``params.pixel_paint`` 等），因此不在这里收窄结构；
         *     - ``enable``（可选，默认 true）：继承自 :class:`TaskInputBase`。
         *
         *     ``task_names`` 里的名字必须在内核的任务表里存在，否则 ``AsstAppendTask`` 返回 0、
         *     内核侧直接 ``set_params failed``（``CustomTask::set_params`` 对每个名字做
         *     ``Task.get``）；本服务的内置识别任务（``MaaApi@CopilotResult@…``，见
         *     :mod:`maa_api.services.copilot.result`）就是靠这条路径运行。
         * @example {
         *       "name": "Custom",
         *       "task_names": [
         *         "MaaApi@CopilotResult@Start"
         *       ]
         *     }
         * @example {
         *       "name": "Custom",
         *       "params": {
         *         "key": "value"
         *       },
         *       "task_names": [
         *         "Custom_MyTask"
         *       ]
         *     }
         */
        CustomInput: {
            /**
             * Enable
             * @description 关掉后执行流水线时会跳过本任务（默认开启）
             * @default true
             */
            enable: boolean | null;
            /**
             * @description 由任务类型选择器决定，不用填 (enum property replaced by openapi-typescript)
             * @enum {string}
             */
            name: "Custom";
            /**
             * Params
             * @description 任务附加参数（可选），目前只有 MiniGame 类任务会读
             */
            params?: {
                [key: string]: unknown;
            } | null;
            /**
             * Task Names
             * @description 内核 tasks.json 里的任务名列表，执行第一个匹配上的
             */
            task_names: string[];
        };
        /**
         * DepotInput
         * @description 仓库识别：读一遍账号仓库「养成材料」页签的数量（规格 §7.2 T1）。
         *
         *     没有参数，空参数即可入队。识别结果经 ``SubTaskExtraInfo`` 回调整表替换仓库
         *     数量；龙门币与作战记录不在该页签内。
         * @example {
         *       "name": "Depot"
         *     }
         */
        DepotInput: {
            /**
             * Enable
             * @description 关掉后执行流水线时会跳过本任务（默认开启）
             * @default true
             */
            enable: boolean | null;
            /**
             * @description 由任务类型选择器决定，不用填 (enum property replaced by openapi-typescript)
             * @enum {string}
             */
            name: "Depot";
        };
        /**
         * DeviceHealth
         * @description 设备连接状态与最近一次连接信息。
         */
        DeviceHealth: {
            /** Address */
            address?: string | null;
            /**
             * Core Id
             * @default default
             */
            core_id: string;
            /** Last Connected At */
            last_connected_at?: number | null;
            /** Last Error */
            last_error?: {
                [key: string]: unknown;
            } | null;
            /** Resolution */
            resolution?: {
                [key: string]: number;
            } | null;
            retry: components["schemas"]["DeviceRetryHealth"];
            /**
             * State
             * @default disconnected
             */
            state: string;
            /** Uuid */
            uuid?: string | null;
        };
        /**
         * DeviceRetryHealth
         * @description 自动重连状态：已尝试次数、上限与下次重连时间（epoch 秒）。
         */
        DeviceRetryHealth: {
            /** Attempt */
            attempt: number;
            /** Max */
            max: number;
            /** Next At */
            next_at: number | null;
        };
        /**
         * FightInput
         * @description 刷理智：刷指定关卡，支持理智药、碎石与连战。
         * @example {
         *       "DrGrandet": true,
         *       "medicine": 2,
         *       "name": "Fight",
         *       "series": 0,
         *       "stage": "1-7",
         *       "stone": 1
         *     }
         */
        FightInput: {
            /**
             * Drgrandet
             * @description 在碎石确认界面等到理智恢复 1 点再碎，省一颗源石（默认关闭）
             * @default false
             */
            DrGrandet: boolean | null;
            /**
             * Client Type
             * @description 客户端渠道；崩溃后按它重启游戏继续刷，明确留空则不启用
             */
            client_type?: ("Official" | "Bilibili" | "txwy" | "YoStarEN" | "YoStarJP" | "YoStarKR") | null;
            /**
             * Drops
             * @description 指定掉落目标与数量，任一达标就停止任务（默认不指定）
             * @default {}
             */
            drops: {
                [key: string]: number;
            } | null;
            /**
             * Enable
             * @description 关掉后执行流水线时会跳过本任务（默认开启）
             * @default true
             */
            enable: boolean | null;
            /**
             * Expiring Medicine
             * @description 最多吃几瓶 48 小时内过期的理智药；非 0 时内核会把这些药一起用掉
             * @default 0
             */
            expiring_medicine: number | null;
            /**
             * Medicine
             * @description 最多吃几瓶理智药（默认 0，即不吃）
             * @default 0
             */
            medicine: number | null;
            /**
             * @description 由任务类型选择器决定，不用填 (enum property replaced by openapi-typescript)
             * @enum {string}
             */
            name: "Fight";
            /**
             * Penguin Id
             * @description 企鹅物流的汇报 id，配合「汇报企鹅数据」使用
             */
            penguin_id?: string | null;
            /**
             * Report To Penguin
             * @description 把作战结果上传到企鹅物流（默认关闭）
             * @default false
             */
            report_to_penguin: boolean | null;
            /**
             * Series
             * @description 同一关卡连续作战的次数（默认 1）
             * @default 1
             */
            series: number | null;
            /**
             * Server
             * @description 游戏服务器，影响掉落识别与上传；不填时用设置里选的默认服务器
             */
            server?: ("CN" | "US" | "JP" | "KR") | null;
            /**
             * Stage
             * @description 剿灭作战填 Annihilation，留空识别当前或上次关卡（不支持运行中设置）
             * @example 1-7
             * @example S3-2
             * @example Annihilation
             * @example CE-6Hard
             */
            stage?: string | null;
            /**
             * Stone
             * @description 最多碎几颗源石换理智（默认 0，即不碎）
             * @default 0
             */
            stone: number | null;
            /**
             * Times
             * @description 刷多少次后停止；不填则不限制次数
             */
            times?: number | null;
        };
        /** ForceRequest */
        ForceRequest: {
            /**
             * Force
             * @default false
             */
            force: boolean;
        };
        /** GameUpdateRequest */
        GameUpdateRequest: {
            /** Channel */
            channel?: string | null;
            /**
             * Force
             * @default false
             */
            force: boolean;
            /**
             * Force Interrupt
             * @default false
             */
            force_interrupt: boolean;
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /**
         * HealthResponse
         * @description ``GET /api/system/health`` 的响应体。
         *
         *     ``core`` / ``device`` / ``queue`` 在运行时对象尚未就绪时为 ``null``，健康探测仍返回 200。
         *     ``started_at`` 是进程启动时间，尚未写入时为 ``null``（键始终存在）。
         */
        HealthResponse: {
            /** Auth Enabled */
            auth_enabled: boolean;
            core?: components["schemas"]["CoreHealth"] | null;
            device?: components["schemas"]["DeviceHealth"] | null;
            queue?: components["schemas"]["QueueHealth"] | null;
            /** Started At */
            started_at?: string | null;
            /**
             * Status
             * @default ok
             */
            status: string;
            /** Version */
            version: string;
        };
        /**
         * InfrastInput
         * @description 基建换班：默认换班、自定义换班与一键轮换。
         * @example {
         *       "drones": "Money",
         *       "facility": [
         *         "Mfg",
         *         "Trade",
         *         "Power",
         *         "Control",
         *         "Reception",
         *         "Office",
         *         "Dorm"
         *       ],
         *       "mode": 0,
         *       "name": "Infrast",
         *       "threshold": 0.3
         *     }
         * @example {
         *       "facility": [
         *         "Mfg",
         *         "Trade"
         *       ],
         *       "filename": "plans/custom.json",
         *       "mode": 10000,
         *       "name": "Infrast",
         *       "plan_index": 0
         *     }
         */
        InfrastInput: {
            /**
             * Dorm Notstationed Enabled
             * @description 宿舍填充时勾选「未进驻」筛选（默认关闭）
             * @default false
             */
            dorm_notstationed_enabled: boolean | null;
            /**
             * Dorm Trust Enabled
             * @description 宿舍剩余位置填信赖未满的干员（默认关闭）
             * @default false
             */
            dorm_trust_enabled: boolean | null;
            /**
             * Drones
             * @description 无人机用在哪里；自定义换班模式下不生效（默认不使用）
             * @default _NotUse
             */
            drones: ("_NotUse" | "Money" | "SyntheticJade" | "CombatRecord" | "PureGold" | "OriginStone" | "Chip") | null;
            /**
             * Enable
             * @description 关掉后执行流水线时会跳过本任务（默认开启）
             * @default true
             */
            enable: boolean | null;
            /**
             * Facility
             * @description 要换班的设施，顺序即换班顺序；默认不含加工站与训练室（不支持运行中设置）
             * @default [
             *       "Mfg",
             *       "Trade",
             *       "Power",
             *       "Control",
             *       "Reception",
             *       "Office",
             *       "Dorm"
             *     ]
             */
            facility: ("Mfg" | "Trade" | "Power" | "Control" | "Reception" | "Office" | "Dorm" | "Processing" | "Training")[] | null;
            /**
             * Filename
             * @description 自定义换班用的配置文件路径（不支持运行中设置）
             */
            filename?: string | null;
            /**
             * Mode
             * @description 换班工作模式；一键轮换会跳过部分设施（默认换班）
             * @default 0
             */
            mode: (0 | 10000 | 20000) | null;
            /**
             * @description 由任务类型选择器决定，不用填 (enum property replaced by openapi-typescript)
             * @enum {string}
             */
            name: "Infrast";
            /**
             * Plan Index
             * @description 用配置文件里的第几个方案，默认第一个（不支持运行中设置）
             * @default 0
             */
            plan_index: number | null;
            /**
             * Replenish
             * @description 贸易站「源石碎片」卖完后自动补货（默认关闭）
             * @default false
             */
            replenish: boolean | null;
            /**
             * Threshold
             * @description 干员心情低于这个比例就换班（默认 0.3）
             * @default 0.3
             */
            threshold: number | null;
        };
        /** InputTextRequest */
        InputTextRequest: {
            /**
             * Force
             * @default false
             */
            force: boolean;
            /** Text */
            text: string;
        };
        /** InvokeRequest */
        InvokeRequest: {
            /** Arguments */
            arguments: {
                [key: string]: unknown;
            };
            /**
             * Mode
             * @default sync
             */
            mode: string;
        };
        /**
         * ItemOut
         * @description An item that can be selected for ``Fight.drops``.
         */
        ItemOut: {
            /** Icon Url */
            icon_url: string | null;
            /** Item Id */
            item_id: string;
            /** Name */
            name: string;
        };
        /** KeyEventRequest */
        KeyEventRequest: {
            /**
             * Force
             * @default false
             */
            force: boolean;
            /** Key */
            key: string;
        };
        /**
         * KnowledgeBuiltinView
         * @description 内置条目的原文快照，供界面对比与恢复。
         */
        KnowledgeBuiltinView: {
            /** Body */
            body: string;
            /** Id */
            id: string;
            /** Kind */
            kind: string;
            /** Title */
            title: string;
            /** When To Use */
            when_to_use: string;
        };
        /**
         * KnowledgeDetail
         * @description 详情：内置条目另附原文快照（``builtin``），自定义条目为 ``None``。
         */
        KnowledgeDetail: {
            /** Body */
            body: string;
            builtin?: components["schemas"]["KnowledgeBuiltinView"] | null;
            /** Enabled */
            enabled: boolean;
            /** Id */
            id: string;
            /** Kind */
            kind: string;
            /** Modified */
            modified: boolean;
            /** Source */
            source: string;
            /** Title */
            title: string;
            /** Updated At */
            updated_at?: string | null;
            /** When To Use */
            when_to_use: string;
        };
        /**
         * KnowledgePage
         * @description 列表响应：列表项不含正文，只给字数。
         */
        KnowledgePage: {
            /** Items */
            items: components["schemas"]["KnowledgeSummary"][];
            /** Total */
            total: number;
        };
        /**
         * KnowledgeSummary
         * @description 列表项：不含正文，只给字数（规格 §4.1 的 REST 列表）。
         */
        KnowledgeSummary: {
            /** Body Chars */
            body_chars: number;
            /** Enabled */
            enabled: boolean;
            /** Id */
            id: string;
            /** Kind */
            kind: string;
            /** Modified */
            modified: boolean;
            /** Source */
            source: string;
            /** Title */
            title: string;
            /** Updated At */
            updated_at?: string | null;
            /** When To Use */
            when_to_use: string;
        };
        /**
         * KnowledgeWriteRequest
         * @description ``PUT`` 的入参：完整的生效内容。
         *
         *     继承服务层的 :class:`~maa_api.agent.knowledge.KnowledgeWrite`（字段名一致），
         *     在这里补上 REST 层的格式与长度约束；服务层的同名校验保留作兜底。
         */
        KnowledgeWriteRequest: {
            /** Body */
            body: string;
            /**
             * Enabled
             * @default true
             */
            enabled: boolean;
            /**
             * Kind
             * @enum {string}
             */
            kind: "playbook" | "screen" | "rule" | "glossary" | "profile";
            /** Title */
            title: string;
            /** When To Use */
            when_to_use: string;
        };
        /** LongPressRequest */
        LongPressRequest: {
            /** Duration Ms */
            duration_ms: number;
            /**
             * Force
             * @default false
             */
            force: boolean;
            /** X */
            x: number;
            /** Y */
            y: number;
        };
        /**
         * MallInput
         * @description 获取信用及商店购物。
         * @example {
         *       "blacklist": [
         *         "加急许可",
         *         "家具零件"
         *       ],
         *       "buy_first": [
         *         "招聘许可"
         *       ],
         *       "name": "Mall",
         *       "shopping": true
         *     }
         */
        MallInput: {
            /**
             * Blacklist
             * @description 不买的商品名（不支持运行中设置）
             * @default []
             */
            blacklist: string[] | null;
            /**
             * Buy First
             * @description 优先购买的商品名（不支持运行中设置）
             * @default []
             */
            buy_first: string[] | null;
            /**
             * Enable
             * @description 关掉后执行流水线时会跳过本任务（默认开启）
             * @default true
             */
            enable: boolean | null;
            /**
             * Force Shopping If Credit Full
             * @description 信用点溢出时也买黑名单里的商品（默认关闭）
             * @default false
             */
            force_shopping_if_credit_full: boolean | null;
            /**
             * @description 由任务类型选择器决定，不用填 (enum property replaced by openapi-typescript)
             * @enum {string}
             */
            name: "Mall";
            /**
             * Only Buy Discount
             * @description 只买打折商品，第二轮购买生效（默认关闭）
             * @default false
             */
            only_buy_discount: boolean | null;
            /**
             * Reserve Max Credit
             * @description 信用点低于 300 就停止购买，第二轮生效（默认关闭）
             * @default false
             */
            reserve_max_credit: boolean | null;
            /**
             * Shopping
             * @description 用信用点买东西，默认开启（不支持运行中设置）
             * @default true
             */
            shopping: boolean | null;
        };
        /** NotifyChannelRequest */
        NotifyChannelRequest: {
            /** Config */
            config: {
                [key: string]: unknown;
            };
            /**
             * Enabled
             * @default true
             */
            enabled: boolean;
            /** Events */
            events: components["schemas"]["NotifyEvent"][];
            /** Name */
            name: string;
            type: components["schemas"]["NotifyChannelType"];
        };
        /**
         * NotifyChannelType
         * @enum {string}
         */
        NotifyChannelType: "email" | "webhook" | "bark" | "dingtalk" | "wecom" | "feishu";
        /**
         * NotifyEvent
         * @enum {string}
         */
        NotifyEvent: "pipeline_completed" | "pipeline_failed" | "core_crashed" | "device_disconnected" | "update_available" | "update_finished" | "confirmation_required" | "upgrade_plan_finished" | "copilot_research_finished";
        /** NotifyTestRequest */
        NotifyTestRequest: {
            /** @default pipeline_completed */
            event: components["schemas"]["NotifyEvent"];
        };
        /**
         * OperBoxInput
         * @description 干员识别：读一遍账号里有哪些干员、练到什么程度（规格 §7.2 T1）。
         *
         *     没有参数：``AsstAppendTask("OperBox", {})`` 就能入队（docs/ENVIRONMENT.md
         *     「MaaCore 内核」节实测）。识别结果经 ``SubTaskExtraInfo`` 回调推给
         *     :mod:`maa_api.services.account.capture`，识别不了技能等级、专精与模组。
         * @example {
         *       "name": "OperBox"
         *     }
         */
        OperBoxInput: {
            /**
             * Enable
             * @description 关掉后执行流水线时会跳过本任务（默认开启）
             * @default true
             */
            enable: boolean | null;
            /**
             * @description 由任务类型选择器决定，不用填 (enum property replaced by openapi-typescript)
             * @enum {string}
             */
            name: "OperBox";
        };
        /**
         * OperatorDiffDismissRequest
         * @description 把差异标记为「知道了」：给 ``ids`` 逐条处理，省略表示全部。
         * @example {
         *       "ids": [
         *         1,
         *         2
         *       ]
         *     }
         * @example {}
         */
        OperatorDiffDismissRequest: {
            /**
             * Ids
             * @description 差异 id 列表；省略或 null 表示全部
             */
            ids?: number[] | null;
        };
        /**
         * OperatorPatchRequest
         * @description 手动编辑一名干员的请求体：只接受可编辑字段，未给的字段不动。
         *
         *     取值域在这里做第一层（类型与范围），跨字段约束（精英化与稀有度、专精与技能等级、
         *     模组与精英化）在服务层做——它们要看行里既有的值。
         * @example {
         *       "elite": 2,
         *       "level": 90,
         *       "own": true,
         *       "potential": 6
         *     }
         * @example {
         *       "elite": 2,
         *       "level": 80,
         *       "masteries": {
         *         "1": 3,
         *         "2": 3,
         *         "3": 0
         *       },
         *       "modules": {
         *         "X": 3
         *       },
         *       "skill_level": 7,
         *       "source": "manual"
         *     }
         */
        OperatorPatchRequest: {
            /**
             * Elite
             * @description 精英化等级，上限按稀有度
             */
            elite?: number | null;
            /**
             * Level
             * @description 等级
             */
            level?: number | null;
            /**
             * Masteries
             * @description 专精等级：技能序号 1–3 → 0–3；只在精二且技能 7 级时可填
             */
            masteries?: {
                [key: string]: number;
            } | null;
            /**
             * Modules
             * @description 模组等级：类型 X/Y/D/A → 1–3；只在精二时可填
             */
            modules?: {
                [key: string]: number;
            } | null;
            /**
             * Own
             * @description 是否拥有
             */
            own?: boolean | null;
            /**
             * Potential
             * @description 潜能
             */
            potential?: number | null;
            /**
             * Skill Level
             * @description 技能等级 1–7
             */
            skill_level?: number | null;
            /**
             * Source
             * @description 字段来源标记；Agent 工具写 agent，页面写 manual
             * @default manual
             * @enum {string}
             */
            source: "manual" | "agent";
        };
        /**
         * OverridesRequest
         * @description ``grant_copilot_attempts`` 卡片上可以改的三项（规格 §9.6）。
         *
         *     尝试次数、成功标准与是否允许助战都能在卡片上改；有效期只展示，**不在**这里。
         *     键名与取值域与 ``services/copilot/grant.py`` 的常量一一对应（1–10 /
         *     ``three_star`` / ``clear`` / ``fill_one`` / ``none``）。``extra="forbid"``：
         *     拼错的键是 422，不静默忽略 —— 用户以为改了次数而实际没改是最坏的结果。
         */
        OverridesRequest: {
            /** Attempts */
            attempts?: number | null;
            /** Success */
            success?: ("three_star" | "clear") | null;
            /** Support */
            support?: ("fill_one" | "none") | null;
        };
        /**
         * PipelineCreate
         * @description 流水线提交请求体（docs/05 §7.4）。``source`` 不在请求体里，由服务端按入口判定。
         * @example {
         *       "tasks": [
         *         {
         *           "name": "StartUp",
         *           "start_game_enabled": true
         *         },
         *         {
         *           "facility": [
         *             "Mfg",
         *             "Trade",
         *             "Control",
         *             "Power",
         *             "Reception",
         *             "Office",
         *             "Dorm"
         *           ],
         *           "mode": 0,
         *           "name": "Infrast"
         *         },
         *         {
         *           "name": "Fight",
         *           "series": 0,
         *           "stage": "1-7"
         *         },
         *         {
         *           "confirm": [
         *             3,
         *             4,
         *             5
         *           ],
         *           "name": "Recruit",
         *           "select": [
         *             4,
         *             5
         *           ],
         *           "times": 4
         *         },
         *         {
         *           "blacklist": [
         *             "加急许可",
         *             "家具零件"
         *           ],
         *           "name": "Mall",
         *           "shopping": true
         *         },
         *         {
         *           "award": true,
         *           "name": "Award"
         *         },
         *         {
         *           "name": "CloseDown"
         *         }
         *       ],
         *       "title": "日常"
         *     }
         */
        PipelineCreate: {
            /**
             * Notify On Finish
             * @default true
             */
            notify_on_finish: boolean;
            /** Priority */
            priority?: number | null;
            /** Tasks */
            tasks: (components["schemas"]["StartUpInput"] | components["schemas"]["CloseDownInput"] | components["schemas"]["FightInput"] | components["schemas"]["RecruitInput"] | components["schemas"]["InfrastInput"] | components["schemas"]["MallInput"] | components["schemas"]["AwardInput"] | components["schemas"]["RoguelikeInput"] | components["schemas"]["ReclamationInput"] | components["schemas"]["OperBoxInput"] | components["schemas"]["DepotInput"] | components["schemas"]["CustomInput"] | components["schemas"]["CopilotInput"])[];
            /** Title */
            title?: string | null;
        };
        /** PriorityRequest */
        PriorityRequest: {
            /** Priority */
            priority: number;
        };
        /**
         * PushSubscribeRequest
         * @description 浏览器 ``PushSubscription.toJSON()`` 的子集，``expirationTime`` 忽略。
         */
        PushSubscribeRequest: {
            /** Endpoint */
            endpoint: string;
            /** Keys */
            keys: {
                [key: string]: string;
            };
        };
        /**
         * PushUnsubscribeRequest
         * @description 按 ``endpoint`` 注销订阅。
         */
        PushUnsubscribeRequest: {
            /** Endpoint */
            endpoint: string;
        };
        /**
         * QuestionStatus
         * @description Agent 主动提问的状态机（M21，规格 §3.2）。
         *
         *     与 ``ConfirmationStatus`` 分开：确认的终态是「批准/拒绝/过期」，提问的终态是
         *     「已答/已跳过/过期」——「跳过」在确认链路里没有对应物，批准也没有。
         *     ``CANCELLED``（M22）是「用户中断了这一轮」：提问没有获得裁决就被撤回，与
         *     「跳过」（用户主动不答）和「过期」（没人管）都不同，不能合并进任何一个既有终态。
         * @enum {string}
         */
        QuestionStatus: "pending" | "answered" | "skipped" | "expired" | "cancelled";
        /**
         * QueueHealth
         * @description 队列计数与暂停状态。
         */
        QueueHealth: {
            /** Paused */
            paused: boolean;
            /** Pending */
            pending: number;
            /** Running */
            running: number;
        };
        /**
         * ReclamationInput
         * @description 生息演算：自动刷分、制造与建造。
         * @example {
         *       "increment_mode": 0,
         *       "mode": 1,
         *       "name": "Reclamation",
         *       "num_craft_batches": 16,
         *       "theme": "Tales",
         *       "tools_to_craft": [
         *         "荧光棒"
         *       ]
         *     }
         */
        ReclamationInput: {
            /**
             * Enable
             * @description 关掉后执行流水线时会跳过本任务（默认开启）
             * @default true
             */
            enable: boolean | null;
            /**
             * Increment Mode
             * @description 制造时点击屏幕的方式（默认连点）
             * @default 0
             */
            increment_mode: (0 | 1) | null;
            /**
             * Mode
             * @description 运行模式（默认刷分与建造点）
             * @default 0
             */
            mode: (0 | 1) | null;
            /**
             * @description 由任务类型选择器决定，不用填 (enum property replaced by openapi-typescript)
             * @enum {string}
             */
            name: "Reclamation";
            /**
             * Num Craft Batches
             * @description 单次最多制造几轮（默认 16）
             * @default 16
             */
            num_craft_batches: number | null;
            /**
             * Theme
             * @description 生息演算主题；沙中之火已下线（默认沙洲遗闻）
             * @default Tales
             */
            theme: ("Fire" | "Tales") | null;
            /**
             * Tools To Craft
             * @description 要自动制造的物品；不填则不制造
             * @default []
             */
            tools_to_craft: string[] | null;
        };
        /** ReconnectRequest */
        ReconnectRequest: {
            /** Adb Path */
            adb_path?: string | null;
            /** Address */
            address?: string | null;
        };
        /**
         * RecruitInput
         * @description 自动公招：按 Tag 等级自动选择与确认，支持加急与数据上报。
         * @example {
         *       "confirm": [
         *         3,
         *         4,
         *         5
         *       ],
         *       "expedite": true,
         *       "expedite_times": 1,
         *       "name": "Recruit",
         *       "select": [
         *         4,
         *         5
         *       ],
         *       "times": 4
         *     }
         */
        RecruitInput: {
            /**
             * Confirm
             * @description 要点击确认的 Tag 星级（默认 2、3、4、5 星）
             * @default [
             *       2,
             *       3,
             *       4,
             *       5
             *     ]
             */
            confirm: number[] | null;
            /**
             * Enable
             * @description 关掉后执行流水线时会跳过本任务（默认开启）
             * @default true
             */
            enable: boolean | null;
            /**
             * Expedite
             * @description 用加急许可立刻完成招募（默认关闭）
             * @default false
             */
            expedite: boolean | null;
            /**
             * Expedite Times
             * @description 当前内核不再读取此值，加急次数由「招募次数」决定
             */
            expedite_times?: number | null;
            /**
             * Extra Tags Mode
             * @description 一轮里选择更多 Tag 的策略（默认 0）
             * @default 0
             */
            extra_tags_mode: (0 | 1 | 2) | null;
            /**
             * First Tags
             * @description 无论其他设置如何都会优先进这些 Tag（仅 3 星）
             * @default []
             */
            first_tags: string[] | null;
            /**
             * @description 由任务类型选择器决定，不用填 (enum property replaced by openapi-typescript)
             * @enum {string}
             */
            name: "Recruit";
            /**
             * Penguin Id
             * @description 企鹅物流的汇报 id，配合「汇报企鹅数据」使用
             */
            penguin_id?: string | null;
            /**
             * Recruitment Time
             * @description 各 Tag 星级对应的希望招募时限（默认 9 小时）
             * @default {
             *       "3": 540,
             *       "4": 540,
             *       "5": 540,
             *       "6": 540
             *     }
             */
            recruitment_time: {
                [key: string]: number;
            } | null;
            /**
             * Refresh
             * @description 连三星标签也一起刷新（默认关闭）
             * @default false
             */
            refresh: boolean | null;
            /**
             * Report To Penguin
             * @description 把公招结果上传到企鹅物流（默认关闭）
             * @default false
             */
            report_to_penguin: boolean | null;
            /**
             * Report To Yituliu
             * @description 把公招结果上传到一图流（默认关闭）
             * @default false
             */
            report_to_yituliu: boolean | null;
            /**
             * Select
             * @description 要点击选中的 Tag 星级（默认 1、4、5 星）
             * @default [
             *       1,
             *       4,
             *       5
             *     ]
             */
            select: number[] | null;
            /**
             * Server
             * @description 游戏服务器，影响数据上传；不填时用设置里选的默认服务器
             */
            server?: ("CN" | "US" | "JP" | "KR") | null;
            /**
             * Set Time
             * @description 按「招募时限映射」把游戏内招募计时器点到位（默认开启）
             * @default true
             */
            set_time: boolean | null;
            /**
             * Skip Robot
             * @description 识别到小车词条时跳过（默认关闭）
             * @default false
             */
            skip_robot: boolean | null;
            /**
             * Times
             * @description 招募多少次；默认 0，只进公招页不招募
             * @default 0
             */
            times: number | null;
            /**
             * Yituliu Id
             * @description 一图流的汇报 id，配合「汇报一图流数据」使用
             */
            yituliu_id?: string | null;
        };
        /** ResolveRequest */
        ResolveRequest: {
            /** Approved */
            approved: boolean;
            overrides?: components["schemas"]["OverridesRequest"] | null;
            /** Reason */
            reason?: string | null;
        };
        /** ResourceUpdateRequest */
        ResourceUpdateRequest: {
            /**
             * Channel
             * @default all
             */
            channel: string;
            /**
             * Force
             * @default false
             */
            force: boolean;
            /**
             * Force Interrupt
             * @default false
             */
            force_interrupt: boolean;
            /**
             * Reload Mode
             * @default wait
             * @enum {string}
             */
            reload_mode: "wait" | "force" | "defer";
        };
        /**
         * RetainRequest
         * @description 「保留」开关（规格 §9.11）：被保留的作业不会被每日清理删掉。
         */
        RetainRequest: {
            /** Retain */
            retain: boolean;
        };
        /** RetryRequest */
        RetryRequest: {
            /** Priority */
            priority?: number | null;
        };
        /**
         * RiskLevel
         * @enum {string}
         */
        RiskLevel: "none" | "consume" | "destructive";
        /**
         * RoguelikeCollectibleStartList
         * @description ``collectible_mode_start_list``：凹开局时期望的开局奖励，默认全部关闭。
         */
        RoguelikeCollectibleStartList: {
            /**
             * Dice
             * @description 骰子奖励（仅水月主题）
             * @default false
             */
            dice: boolean;
            /**
             * Hope
             * @description 希望奖励（通用；界园主题没有）
             * @default false
             */
            hope: boolean;
            /**
             * Hot Water
             * @description 热水壶奖励（通用）
             * @default false
             */
            hot_water: boolean;
            /**
             * Ideas
             * @description 2 构想奖励（仅萨卡兹主题）
             * @default false
             */
            ideas: boolean;
            /**
             * Ingot
             * @description 源石锭奖励（通用）
             * @default false
             */
            ingot: boolean;
            /**
             * Key
             * @description 钥匙奖励（仅水月主题）
             * @default false
             */
            key: boolean;
            /**
             * Random
             * @description 用全部源石锭换随机收藏品（通用）
             * @default false
             */
            random: boolean;
            /**
             * Shield
             * @description 护盾奖励，约等于额外生命（通用）
             * @default false
             */
            shield: boolean;
            /**
             * Ticket
             * @description 票券奖励（仅界园主题）
             * @default false
             */
            ticket: boolean;
        };
        /**
         * RoguelikeCoreChar
         * @description ``core_char_list`` 的一项：开局第 N 次招募指定的一名干员。
         */
        RoguelikeCoreChar: {
            /**
             * Name
             * @description 干员中文名，无论区服
             */
            name: string;
            /**
             * Use Support
             * @description 这一顺位使用助战干员
             * @default false
             */
            use_support: boolean;
        };
        /**
         * RoguelikeInput
         * @description 无限刷肉鸽（集成战略）。
         *
         *     主题／模式相关的跨字段规则全部实现为 ``model_validator`` 并抛 ``AppError``
         *     （``TASK_PARAM_INVALID``；``mode=2`` 是已弃用值，抛 ``TASK_PARAM_DEPRECATED``）
         *     ——「设了但不生效」必须在提交时就告诉用户，而不是静默忽略。矩阵对齐上游
         *     ``RoguelikeConfig.cpp``：模式按主题收口，``start_with_elite_two`` 只许 ``mode=4``，
         *     ``difficulty`` 在 Phantom 下也允许（``-1`` 表示不指定）。
         * @example {
         *       "investment_enabled": true,
         *       "investments_count": 10,
         *       "mode": 1,
         *       "name": "Roguelike",
         *       "theme": "Sami"
         *     }
         * @example {
         *       "check_collapsal_paradigms": true,
         *       "expected_collapsal_paradigms": [
         *         "目空一些",
         *         "睁眼瞎"
         *       ],
         *       "mode": 5,
         *       "name": "Roguelike",
         *       "theme": "Sami"
         *     }
         * @example {
         *       "collectible_mode_start_list": {
         *         "hot_water": true,
         *         "shield": true
         *       },
         *       "core_char_list": [
         *         {
         *           "name": "维什戴尔",
         *           "use_support": true
         *         },
         *         {
         *           "name": "古米",
         *           "use_support": false
         *         }
         *       ],
         *       "mode": 4,
         *       "name": "Roguelike",
         *       "start_with_elite_two": true,
         *       "theme": "Sarkaz"
         *     }
         */
        RoguelikeInput: {
            /**
             * Blackflow Cultivation Target
             * @description 刷襁褓动物的目标（默认襁褓猫）
             */
            blackflow_cultivation_target?: ("swaddled_cat" | "swaddled_feathered_serpent" | "swaddled_dog" | "swaddled_cerberus") | null;
            /**
             * Blackflow Strategy
             * @description 黑流树海的策略；不填按模式与投资推断
             */
            blackflow_strategy?: ("baby_animal" | "investment" | "burn_with_investment" | "burn") | null;
            /**
             * Check Collapsal Paradigms
             * @description 检测拿到的坍缩范式；不填由内核按模式决定
             */
            check_collapsal_paradigms?: boolean | null;
            /**
             * Collectible Mode Shopping
             * @description 凹开局时也购物（默认关闭）
             * @default false
             */
            collectible_mode_shopping: boolean | null;
            /**
             * Collectible Mode Squad
             * @description 凹开局使用的分队；不填与开局分队相同
             */
            collectible_mode_squad?: string | null;
            /** @description 凹开局期望的奖励，默认全部关闭 */
            collectible_mode_start_list?: components["schemas"]["RoguelikeCollectibleStartList"] | null;
            /**
             * Core Char
             * @description 开局要借的干员中文名；不填由内核按练度选择
             */
            core_char?: string | null;
            /**
             * Core Char List
             * @description 按顺位指定开局干员，最多三个，职业不能相同
             */
            core_char_list?: components["schemas"]["RoguelikeCoreChar"][] | null;
            /**
             * Deep Exploration Auto Iterate
             * @description 深入调查自动切换（仅刷深入调查模式）
             */
            deep_exploration_auto_iterate?: boolean | null;
            /**
             * Difficulty
             * @description 指定难度等级；-1 表示不指定（默认不指定）
             */
            difficulty?: number | null;
            /**
             * Double Check Collapsal Paradigms
             * @description 多一道坍缩范式检测，避免漏检（不填由内核决定）
             */
            double_check_collapsal_paradigms?: boolean | null;
            /**
             * Enable
             * @description 关掉后执行流水线时会跳过本任务（默认开启）
             * @default true
             */
            enable: boolean | null;
            /**
             * Expected Collapsal Paradigms
             * @description 希望刷到的坍缩范式（仅刷坍缩范式模式）
             */
            expected_collapsal_paradigms?: string[] | null;
            /**
             * Find Playtime Target
             * @description 想刷的常乐节点（刷常乐节点模式必填）
             */
            find_playTime_target?: (1 | 2 | 3) | null;
            /**
             * First Floor Foldartal
             * @description 第一层远见想要的密文板；凹到就停止任务
             */
            first_floor_foldartal?: string | null;
            /**
             * Investment Enabled
             * @description 把源石锭投进商店（默认开启）
             * @default true
             */
            investment_enabled: boolean | null;
            /**
             * Investment With More Score
             * @description 投资后顺便购物（仅刷源石锭模式）
             * @default false
             */
            investment_with_more_score: boolean | null;
            /**
             * Investments Count
             * @description 投多少次后停止；不填则不限制
             */
            investments_count?: number | null;
            /**
             * Mode
             * @description 肉鸽的玩法模式（默认刷分/奖励点数）
             * @default 0
             */
            mode: (0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 10001 | 20001 | 30001) | null;
            /**
             * Monthly Squad Auto Iterate
             * @description 月度小队自动切换（仅刷月度小队模式）
             */
            monthly_squad_auto_iterate?: boolean | null;
            /**
             * Monthly Squad Check Comms
             * @description 把月度小队通信也作为切换依据（默认关闭）
             */
            monthly_squad_check_comms?: boolean | null;
            /**
             * @description 由任务类型选择器决定，不用填 (enum property replaced by openapi-typescript)
             * @enum {string}
             */
            name: "Roguelike";
            /**
             * Only Start With Elite Two
             * @description 只凹精二直升，忽略其他开局条件（默认关闭）
             * @default false
             */
            only_start_with_elite_two: boolean | null;
            /**
             * Refresh Trader With Dice
             * @description 用骰子刷新商店，用于刷指路鳞（默认关闭）
             * @default false
             */
            refresh_trader_with_dice: boolean | null;
            /**
             * Roles
             * @description 开局职业组；不填则由内核自动选择
             */
            roles?: string | null;
            /**
             * Squad
             * @description 开局用哪个分队；不填则由内核自动选择
             */
            squad?: string | null;
            /**
             * Start Foldartal List
             * @description 开局奖励想要的密文板；需搭配「生活至上分队」
             */
            start_foldartal_list?: string[] | null;
            /**
             * Start With Elite Two
             * @description 凹开局时顺便凹精二直升（仅凹开局模式）
             * @default false
             */
            start_with_elite_two: boolean | null;
            /**
             * Start With Seed
             * @description 用固定种子刷钱；留空则不启用
             */
            start_with_seed?: string | null;
            /**
             * Starts Count
             * @description 开始探索多少次后停止；不填则不限制
             */
            starts_count?: number | null;
            /**
             * Stop At Final Boss
             * @description 第 5 层险路恶敌前停止；不适用于 Phantom
             * @default false
             */
            stop_at_final_boss: boolean | null;
            /**
             * Stop At Max Level
             * @description 肉鸽等级刷满后停止任务（默认关闭）
             * @default false
             */
            stop_at_max_level: boolean | null;
            /**
             * Stop When Investment Full
             * @description 投资满后自动停止任务（默认关闭）
             * @default false
             */
            stop_when_investment_full: boolean | null;
            /**
             * Theme
             * @description 要刷的肉鸽主题（默认傀影与猩红孤钻）
             * @default Phantom
             */
            theme: ("Phantom" | "Mizuki" | "Sami" | "Sarkaz" | "JieGarden" | "BlackFlow") | null;
            /**
             * Use Foldartal
             * @description 是否使用密文板；不填由内核按模式决定
             */
            use_foldartal?: boolean | null;
            /**
             * Use Nonfriend Support
             * @description 允许借非好友的助战干员（默认关闭）
             * @default false
             */
            use_nonfriend_support: boolean | null;
            /**
             * Use Support
             * @description 开局干员换成助战干员（默认关闭）
             * @default false
             */
            use_support: boolean | null;
        };
        /** SchedulePage */
        SchedulePage: {
            /** Items */
            items: components["schemas"]["ScheduleView"][];
            /** Total */
            total: number;
        };
        /**
         * SchedulePatch
         * @description Partial schedule updates supported by PATCH.
         */
        SchedulePatch: {
            /** Cron */
            cron?: string | null;
            /** Enabled */
            enabled?: boolean | null;
            /** Priority */
            priority?: number | null;
        };
        /** ScheduleRunAccepted */
        ScheduleRunAccepted: {
            /** Pipeline Id */
            pipeline_id: string;
            /** Priority */
            priority: number;
            /** Schedule Id */
            schedule_id: string;
            /** Status */
            status: string;
        };
        /** ScheduleRunRequest */
        ScheduleRunRequest: {
            /** Priority */
            priority?: number | null;
        };
        /** ScheduleRunView */
        ScheduleRunView: {
            /** Error */
            error: {
                [key: string]: unknown;
            } | null;
            /** Finished At */
            finished_at: string | null;
            /** Pipeline Id */
            pipeline_id: string | null;
            /** Started At */
            started_at: string | null;
            /** Status */
            status: string;
        };
        /** ScheduleView */
        ScheduleView: {
            /** Cron */
            cron: string;
            /** Enabled */
            enabled: boolean;
            /** Id */
            id: string;
            /** Last Run At */
            last_run_at: string | null;
            last_run_result: components["schemas"]["ScheduleRunView"] | null;
            /** Name */
            name: string;
            /** Next Run At */
            next_run_at: string | null;
            /** Priority */
            priority: number;
            /** Recent Runs */
            recent_runs: components["schemas"]["ScheduleRunView"][];
            /** Template */
            template: {
                [key: string]: unknown;
            }[];
            /** Timezone */
            timezone: string;
        };
        /**
         * ScheduleWrite
         * @description Validated create/full-replacement payload for a schedule.
         */
        ScheduleWrite: {
            /**
             * Catch Up
             * @default false
             */
            catch_up: boolean;
            /** Cron */
            cron: string;
            /**
             * Enabled
             * @default true
             */
            enabled: boolean;
            /**
             * Misfire Grace Seconds
             * @default 300
             */
            misfire_grace_seconds: number;
            /** Name */
            name: string;
            /**
             * Priority
             * @default 2
             */
            priority: number;
            /**
             * Skip If Running
             * @default true
             */
            skip_if_running: boolean;
            /** Template */
            template: (components["schemas"]["StartUpInput"] | components["schemas"]["CloseDownInput"] | components["schemas"]["FightInput"] | components["schemas"]["RecruitInput"] | components["schemas"]["InfrastInput"] | components["schemas"]["MallInput"] | components["schemas"]["AwardInput"] | components["schemas"]["RoguelikeInput"] | components["schemas"]["ReclamationInput"] | components["schemas"]["OperBoxInput"] | components["schemas"]["DepotInput"] | components["schemas"]["CustomInput"] | components["schemas"]["CopilotInput"])[];
            /**
             * Timezone
             * @default Asia/Shanghai
             */
            timezone: string;
        };
        /** SessionCreateRequest */
        SessionCreateRequest: {
            /** Title */
            title?: string | null;
        };
        /** SessionMessageRequest */
        SessionMessageRequest: {
            /** Content */
            content: string;
        };
        /** SettingResetRequest */
        SettingResetRequest: {
            /** Keys */
            keys: string[];
        };
        /** SettingUpdateRequest */
        SettingUpdateRequest: {
            /** Items */
            items: {
                [key: string]: unknown;
            };
        };
        /**
         * SourceCacheStats
         * @description 一个来源的缓存统计。
         */
        SourceCacheStats: {
            /**
             * Bytes
             * @description 估算占用字节数
             */
            bytes: number;
            /**
             * Entries
             * @description 缓存条数
             */
            entries: number;
            /**
             * Evictions
             * @description 因超出内存上限被淘汰的条数
             */
            evictions: number;
            /**
             * Hit Rate
             * @description 命中率，没有读写时为 null
             */
            hit_rate?: number | null;
            /**
             * Hits
             * @description 命中次数
             */
            hits: number;
            /**
             * Misses
             * @description 未命中次数
             */
            misses: number;
        };
        /**
         * SourceStatus
         * @description 一个来源的运行时状态。
         */
        SourceStatus: {
            /**
             * Bytes Read
             * @default 0
             */
            bytes_read: number;
            cache: components["schemas"]["SourceCacheStats"];
            /**
             * Consecutive Failures
             * @default 0
             */
            consecutive_failures: number;
            /**
             * Cooldown Seconds
             * @description 冷却剩余秒数
             * @default 0
             */
            cooldown_seconds: number;
            /**
             * Cooldown Until
             * @description 冷却截止时间，未在冷却时为 null
             */
            cooldown_until?: string | null;
            /**
             * Enabled
             * @description 该来源当前是否启用（总开关关闭时全为 false）
             */
            enabled: boolean;
            /** Kind */
            kind: string;
            /** Label */
            label: string;
            /** Last Error At */
            last_error_at?: string | null;
            /** Last Error Code */
            last_error_code?: string | null;
            /** Last Error Message */
            last_error_message?: string | null;
            /** Last Success At */
            last_success_at?: string | null;
            /**
             * Rate Per Minute
             * @description 当前每分钟请求上限
             */
            rate_per_minute: number;
            /**
             * Requests
             * @default 0
             */
            requests: number;
            /**
             * Retries
             * @default 0
             */
            retries: number;
        };
        /**
         * SourcesStatusResponse
         * @description 各外部来源的状态快照。
         */
        SourcesStatusResponse: {
            /** Sources */
            sources: components["schemas"]["SourceStatus"][];
        };
        /**
         * StartUpInput
         * @description 开始唤醒：启动客户端并进入游戏。
         * @example {
         *       "client_type": "Official",
         *       "name": "StartUp",
         *       "start_game_enabled": true
         *     }
         */
        StartUpInput: {
            /**
             * Account Name
             * @description 切换到已登录的账号，输入登录名的一部分即可；不填则不切换
             * @example 123****4567
             * @example 4567
             * @example 张三
             */
            account_name?: string | null;
            /**
             * Client Type
             * @description 游戏客户端渠道；不填时用设置里选的默认渠道
             */
            client_type?: ("Official" | "Bilibili" | "txwy" | "YoStarEN" | "YoStarJP" | "YoStarKR") | null;
            /**
             * Enable
             * @description 关掉后执行流水线时会跳过本任务（默认开启）
             * @default true
             */
            enable: boolean | null;
            /**
             * @description 由任务类型选择器决定，不用填 (enum property replaced by openapi-typescript)
             * @enum {string}
             */
            name: "StartUp";
            /**
             * Start Game Enabled
             * @description 启动任务前自动打开游戏客户端（默认关闭）
             * @default false
             */
            start_game_enabled: boolean | null;
        };
        /** SwipeRequest */
        SwipeRequest: {
            /**
             * Duration Ms
             * @default 300
             */
            duration_ms: number;
            /**
             * Force
             * @default false
             */
            force: boolean;
            /** X1 */
            x1: number;
            /** X2 */
            x2: number;
            /** Y1 */
            y1: number;
            /** Y2 */
            y2: number;
        };
        /**
         * TaskValidateRequest
         * @description ``POST /api/tasks/validate`` 的请求体：只有任务数组。
         *
         *     不含 ``title`` / ``priority`` / ``notify_on_finish``：这个端点只校验参数，
         *     不建流水线。
         * @example {
         *       "tasks": [
         *         {
         *           "client_type": "Official",
         *           "name": "StartUp"
         *         },
         *         {
         *           "medicine": 2,
         *           "name": "Fight",
         *           "series": 0,
         *           "stage": "1-7"
         *         },
         *         {
         *           "confirm": [
         *             3,
         *             4,
         *             5
         *           ],
         *           "name": "Recruit",
         *           "select": [
         *             4,
         *             5
         *           ],
         *           "times": 4
         *         }
         *       ]
         *     }
         */
        TaskValidateRequest: {
            /** Tasks */
            tasks: (components["schemas"]["StartUpInput"] | components["schemas"]["CloseDownInput"] | components["schemas"]["FightInput"] | components["schemas"]["RecruitInput"] | components["schemas"]["InfrastInput"] | components["schemas"]["MallInput"] | components["schemas"]["AwardInput"] | components["schemas"]["RoguelikeInput"] | components["schemas"]["ReclamationInput"] | components["schemas"]["OperBoxInput"] | components["schemas"]["DepotInput"] | components["schemas"]["CustomInput"] | components["schemas"]["CopilotInput"])[];
        };
        /** ValidationError */
        ValidationError: {
            /** Context */
            ctx?: Record<string, never>;
            /** Input */
            input?: unknown;
            /** Location */
            loc: (string | number)[];
            /** Message */
            msg: string;
            /** Error Type */
            type: string;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    account_list_account_depot: {
        parameters: {
            query?: {
                /** @description 物品分类 */
                category?: string | null;
                /** @description 物品名片段 */
                query?: string | null;
                limit?: number;
                offset?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 参数校验失败：VALIDATION_ERROR */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "VALIDATION_ERROR",
                     *         "details": {},
                     *         "message": "参数校验失败"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    account_list_account_operator_diffs: {
        parameters: {
            query?: {
                include_dismissed?: boolean;
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 参数校验失败：VALIDATION_ERROR */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "VALIDATION_ERROR",
                     *         "details": {},
                     *         "message": "参数校验失败"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    account_dismiss_account_operator_diffs: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["OperatorDiffDismissRequest"] | null;
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 参数校验失败：VALIDATION_ERROR */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "VALIDATION_ERROR",
                     *         "details": {},
                     *         "message": "参数校验失败"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    account_list_account_operators: {
        parameters: {
            query?: {
                /** @description 干员名片段 */
                query?: string | null;
                /** @description 只返回已拥有的干员 */
                owned_only?: boolean;
                /** @description 最低稀有度 */
                rarity_min?: number | null;
                /** @description 职业，如 近卫 / 医疗 */
                profession?: string | null;
                /** @description 最低精英化等级 */
                elite_min?: number | null;
                limit?: number;
                offset?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 参数校验失败：VALIDATION_ERROR */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "VALIDATION_ERROR",
                     *         "details": {},
                     *         "message": "参数校验失败"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    account_get_account_operator: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 干员 id，如 char_002_amiya */
                char_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：ACCOUNT_DATA_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "ACCOUNT_DATA_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    account_patch_account_operator: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 干员 id，如 char_002_amiya */
                char_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["OperatorPatchRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：ACCOUNT_DATA_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "ACCOUNT_DATA_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 参数校验失败：VALIDATION_ERROR */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "VALIDATION_ERROR",
                     *         "details": {},
                     *         "message": "参数校验失败"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    account_get_account_summary: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    agent_list_audits: {
        parameters: {
            query?: {
                caller?: components["schemas"]["CallerType"] | null;
                tool_name?: string | null;
                status?: components["schemas"]["AuditStatus"] | null;
                risk_level?: components["schemas"]["RiskLevel"] | null;
                since?: string | null;
                page?: number;
                size?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description 请求不成立：INVALID_PAGINATION */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "INVALID_PAGINATION",
                     *         "details": {},
                     *         "message": "请求不成立"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    agent_get_audit: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                audit_id: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    agent_check_capabilities: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 服务暂时不可用：AGENT_DISABLED、LLM_NOT_CONFIGURED、LLM_ENCRYPTION_KEY_INVALID */
            503: {
                headers: {
                    /** @description 建议的重试等待秒数（整数） */
                    "Retry-After"?: number;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "AGENT_DISABLED",
                     *         "details": {},
                     *         "message": "服务暂时不可用"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    agent_list_knowledge: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KnowledgePage"];
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    agent_get_knowledge_entry: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 知识条目 id */
                entry_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KnowledgeDetail"];
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：KNOWLEDGE_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "KNOWLEDGE_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 参数校验失败：VALIDATION_ERROR */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "VALIDATION_ERROR",
                     *         "details": {},
                     *         "message": "参数校验失败"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    agent_put_knowledge_entry: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 知识条目 id */
                entry_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["KnowledgeWriteRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["KnowledgeDetail"];
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 与当前状态冲突：KNOWLEDGE_BUILTIN_READONLY */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "KNOWLEDGE_BUILTIN_READONLY",
                     *         "details": {},
                     *         "message": "与当前状态冲突"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 参数校验失败：VALIDATION_ERROR */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "VALIDATION_ERROR",
                     *         "details": {},
                     *         "message": "参数校验失败"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    agent_delete_knowledge_entry: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 知识条目 id */
                entry_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：KNOWLEDGE_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "KNOWLEDGE_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 与当前状态冲突：KNOWLEDGE_BUILTIN_READONLY */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "KNOWLEDGE_BUILTIN_READONLY",
                     *         "details": {},
                     *         "message": "与当前状态冲突"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 参数校验失败：VALIDATION_ERROR */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "VALIDATION_ERROR",
                     *         "details": {},
                     *         "message": "参数校验失败"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    agent_list_questions: {
        parameters: {
            query?: {
                session_id?: string | null;
                status?: components["schemas"]["QuestionStatus"] | null;
                page?: number;
                size?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description 请求不成立：INVALID_PAGINATION */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "INVALID_PAGINATION",
                     *         "details": {},
                     *         "message": "请求不成立"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    agent_get_question: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                question_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：QUESTION_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "QUESTION_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    agent_answer_question: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                question_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AnswerRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description 请求不成立：QUESTION_SELECTION_INVALID */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "QUESTION_SELECTION_INVALID",
                     *         "details": {},
                     *         "message": "请求不成立"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：QUESTION_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "QUESTION_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 与当前状态冲突：QUESTION_ALREADY_ANSWERED、QUESTION_EXPIRED */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "QUESTION_ALREADY_ANSWERED",
                     *         "details": {},
                     *         "message": "与当前状态冲突"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    agent_skip_question: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                question_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：QUESTION_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "QUESTION_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 与当前状态冲突：QUESTION_ALREADY_ANSWERED、QUESTION_EXPIRED */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "QUESTION_ALREADY_ANSWERED",
                     *         "details": {},
                     *         "message": "与当前状态冲突"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    agent_list_sessions: {
        parameters: {
            query?: {
                page?: number;
                size?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    agent_create_session: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SessionCreateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description 服务暂时不可用：LLM_NOT_CONFIGURED、LLM_ENCRYPTION_KEY_INVALID */
            503: {
                headers: {
                    /** @description 建议的重试等待秒数（整数） */
                    "Retry-After"?: number;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "LLM_NOT_CONFIGURED",
                     *         "details": {},
                     *         "message": "服务暂时不可用"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    agent_get_session_detail: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                session_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：AGENT_SESSION_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "AGENT_SESSION_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description 服务暂时不可用：SERVICE_UNAVAILABLE */
            503: {
                headers: {
                    /** @description 建议的重试等待秒数（整数） */
                    "Retry-After"?: number;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SERVICE_UNAVAILABLE",
                     *         "details": {},
                     *         "message": "服务暂时不可用"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    agent_delete_session: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                session_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：AGENT_SESSION_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "AGENT_SESSION_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    agent_revoke_atomic_grant: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                session_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：AGENT_SESSION_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "AGENT_SESSION_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    agent_revoke_copilot_grant: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                session_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：AGENT_SESSION_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "AGENT_SESSION_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description 服务暂时不可用：SERVICE_UNAVAILABLE */
            503: {
                headers: {
                    /** @description 建议的重试等待秒数（整数） */
                    "Retry-After"?: number;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SERVICE_UNAVAILABLE",
                     *         "details": {},
                     *         "message": "服务暂时不可用"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    agent_interrupt_session_turn: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                session_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：AGENT_SESSION_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "AGENT_SESSION_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description 服务暂时不可用：AGENT_DISABLED */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "AGENT_DISABLED",
                     *         "details": {},
                     *         "message": "服务暂时不可用"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    agent_list_session_messages: {
        parameters: {
            query?: {
                after_seq?: number | null;
                page?: number;
                size?: number;
            };
            header?: never;
            path: {
                session_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：AGENT_SESSION_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "AGENT_SESSION_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    agent_stream_session_message: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                session_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SessionMessageRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：AGENT_SESSION_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "AGENT_SESSION_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 与当前状态冲突：AGENT_SESSION_BUSY */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "AGENT_SESSION_BUSY",
                     *         "details": {},
                     *         "message": "与当前状态冲突"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description 服务暂时不可用：AGENT_DISABLED、LLM_NOT_CONFIGURED、LLM_ENCRYPTION_KEY_INVALID */
            503: {
                headers: {
                    /** @description 建议的重试等待秒数（整数） */
                    "Retry-After"?: number;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "AGENT_DISABLED",
                     *         "details": {},
                     *         "message": "服务暂时不可用"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    agent_list_tools: {
        parameters: {
            query?: {
                risk_level?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description 服务暂时不可用：AGENT_DISABLED */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "AGENT_DISABLED",
                     *         "details": {},
                     *         "message": "服务暂时不可用"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    agent_invoke_tool: {
        parameters: {
            query?: never;
            header?: {
                "Idempotency-Key"?: string | null;
            };
            path: {
                name: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["InvokeRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 身份有效但动作被拒绝：CONFIRMATION_REJECTED */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "CONFIRMATION_REJECTED",
                     *         "details": {},
                     *         "message": "身份有效但动作被拒绝"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：TOOL_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "TOOL_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 与当前状态冲突：CONFIRMATION_EXPIRED、IDEMPOTENCY_KEY_CONFLICT */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "CONFIRMATION_EXPIRED",
                     *         "details": {},
                     *         "message": "与当前状态冲突"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 参数校验失败：TOOL_ARGS_INVALID */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "TOOL_ARGS_INVALID",
                     *         "details": {},
                     *         "message": "参数校验失败"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    confirmations_list_confirmations: {
        parameters: {
            query?: {
                status?: components["schemas"]["ConfirmationStatus"] | null;
                page?: number;
                size?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description 请求不成立：INVALID_PAGINATION */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "INVALID_PAGINATION",
                     *         "details": {},
                     *         "message": "请求不成立"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    confirmations_get_confirmation: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                confirmation_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：CONFIRMATION_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "CONFIRMATION_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    confirmations_resolve_confirmation: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                confirmation_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ResolveRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：CONFIRMATION_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "CONFIRMATION_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 与当前状态冲突：CONFIRMATION_ALREADY_RESOLVED、CONFIRMATION_EXPIRED */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "CONFIRMATION_ALREADY_RESOLVED",
                     *         "details": {},
                     *         "message": "与当前状态冲突"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description 服务暂时不可用：SERVICE_UNAVAILABLE */
            503: {
                headers: {
                    /** @description 建议的重试等待秒数（整数） */
                    "Retry-After"?: number;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SERVICE_UNAVAILABLE",
                     *         "details": {},
                     *         "message": "服务暂时不可用"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    core_back_to_home: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["ForceRequest"] | null;
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 与当前状态冲突：PIPELINE_ALREADY_RUNNING */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "PIPELINE_ALREADY_RUNNING",
                     *         "details": {},
                     *         "message": "与当前状态冲突"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description 服务暂时不可用：CORE_NOT_READY */
            503: {
                headers: {
                    /** @description 建议的重试等待秒数（整数） */
                    "Retry-After"?: number;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "CORE_NOT_READY",
                     *         "details": {},
                     *         "message": "服务暂时不可用"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    device_list_device_candidates: {
        parameters: {
            query?: {
                include_common_ports?: boolean;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description 服务端内部错误：ADB_NOT_FOUND */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "ADB_NOT_FOUND",
                     *         "details": {},
                     *         "message": "服务端内部错误"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 下游依赖失败：DEVICE_SCAN_FAILED */
            502: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "DEVICE_SCAN_FAILED",
                     *         "details": {},
                     *         "message": "下游依赖失败"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    device_click_device: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ClickRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description 请求不成立：INVALID_PARAMETER */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "INVALID_PARAMETER",
                     *         "details": {},
                     *         "message": "请求不成立"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 与当前状态冲突：PIPELINE_ALREADY_RUNNING */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "PIPELINE_ALREADY_RUNNING",
                     *         "details": {},
                     *         "message": "与当前状态冲突"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description 服务暂时不可用：CORE_NOT_READY、DEVICE_NOT_CONNECTED、SERVICE_UNAVAILABLE */
            503: {
                headers: {
                    /** @description 建议的重试等待秒数（整数） */
                    "Retry-After"?: number;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "CORE_NOT_READY",
                     *         "details": {},
                     *         "message": "服务暂时不可用"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    device_input_device_text: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["InputTextRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 与当前状态冲突：PIPELINE_ALREADY_RUNNING */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "PIPELINE_ALREADY_RUNNING",
                     *         "details": {},
                     *         "message": "与当前状态冲突"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description 下游依赖失败：ADB_COMMAND_FAILED */
            502: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "ADB_COMMAND_FAILED",
                     *         "details": {},
                     *         "message": "下游依赖失败"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 服务暂时不可用：SERVICE_UNAVAILABLE */
            503: {
                headers: {
                    /** @description 建议的重试等待秒数（整数） */
                    "Retry-After"?: number;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SERVICE_UNAVAILABLE",
                     *         "details": {},
                     *         "message": "服务暂时不可用"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    device_device_key_event: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["KeyEventRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description 请求不成立：INVALID_PARAMETER */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "INVALID_PARAMETER",
                     *         "details": {},
                     *         "message": "请求不成立"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 与当前状态冲突：PIPELINE_ALREADY_RUNNING */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "PIPELINE_ALREADY_RUNNING",
                     *         "details": {},
                     *         "message": "与当前状态冲突"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description 下游依赖失败：ADB_COMMAND_FAILED */
            502: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "ADB_COMMAND_FAILED",
                     *         "details": {},
                     *         "message": "下游依赖失败"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 服务暂时不可用：SERVICE_UNAVAILABLE */
            503: {
                headers: {
                    /** @description 建议的重试等待秒数（整数） */
                    "Retry-After"?: number;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SERVICE_UNAVAILABLE",
                     *         "details": {},
                     *         "message": "服务暂时不可用"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    device_list_devices: {
        parameters: {
            query?: {
                include_common_ports?: boolean;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description 服务端内部错误：ADB_NOT_FOUND */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "ADB_NOT_FOUND",
                     *         "details": {},
                     *         "message": "服务端内部错误"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 下游依赖失败：DEVICE_SCAN_FAILED */
            502: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "DEVICE_SCAN_FAILED",
                     *         "details": {},
                     *         "message": "下游依赖失败"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    device_long_press_device: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["LongPressRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description 请求不成立：INVALID_PARAMETER */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "INVALID_PARAMETER",
                     *         "details": {},
                     *         "message": "请求不成立"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 与当前状态冲突：PIPELINE_ALREADY_RUNNING */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "PIPELINE_ALREADY_RUNNING",
                     *         "details": {},
                     *         "message": "与当前状态冲突"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description 下游依赖失败：ADB_COMMAND_FAILED */
            502: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "ADB_COMMAND_FAILED",
                     *         "details": {},
                     *         "message": "下游依赖失败"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 服务暂时不可用：SERVICE_UNAVAILABLE */
            503: {
                headers: {
                    /** @description 建议的重试等待秒数（整数） */
                    "Retry-After"?: number;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SERVICE_UNAVAILABLE",
                     *         "details": {},
                     *         "message": "服务暂时不可用"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    device_reconnect_device: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["ReconnectRequest"] | null;
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 与当前状态冲突：PIPELINE_ALREADY_RUNNING */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "PIPELINE_ALREADY_RUNNING",
                     *         "details": {},
                     *         "message": "与当前状态冲突"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description 服务端内部错误：ADB_NOT_FOUND */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "ADB_NOT_FOUND",
                     *         "details": {},
                     *         "message": "服务端内部错误"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 下游依赖失败：ADB_CONNECT_FAILED */
            502: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "ADB_CONNECT_FAILED",
                     *         "details": {},
                     *         "message": "下游依赖失败"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    device_device_screenshot: {
        parameters: {
            query?: {
                backend?: string;
                format?: string;
                size?: string;
                quality?: number | null;
                archive?: boolean;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description 请求不成立：INVALID_PARAMETER */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "INVALID_PARAMETER",
                     *         "details": {},
                     *         "message": "请求不成立"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description 下游依赖失败：SCREENSHOT_FAILED */
            502: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SCREENSHOT_FAILED",
                     *         "details": {},
                     *         "message": "下游依赖失败"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 服务暂时不可用：DEVICE_NOT_CONNECTED */
            503: {
                headers: {
                    /** @description 建议的重试等待秒数（整数） */
                    "Retry-After"?: number;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "DEVICE_NOT_CONNECTED",
                     *         "details": {},
                     *         "message": "服务暂时不可用"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    device_device_status: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 服务暂时不可用：SERVICE_UNAVAILABLE */
            503: {
                headers: {
                    /** @description 建议的重试等待秒数（整数） */
                    "Retry-After"?: number;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SERVICE_UNAVAILABLE",
                     *         "details": {},
                     *         "message": "服务暂时不可用"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    device_swipe_device: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SwipeRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description 请求不成立：INVALID_PARAMETER */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "INVALID_PARAMETER",
                     *         "details": {},
                     *         "message": "请求不成立"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 与当前状态冲突：PIPELINE_ALREADY_RUNNING */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "PIPELINE_ALREADY_RUNNING",
                     *         "details": {},
                     *         "message": "与当前状态冲突"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description 下游依赖失败：ADB_COMMAND_FAILED */
            502: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "ADB_COMMAND_FAILED",
                     *         "details": {},
                     *         "message": "下游依赖失败"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 服务暂时不可用：SERVICE_UNAVAILABLE */
            503: {
                headers: {
                    /** @description 建议的重试等待秒数（整数） */
                    "Retry-After"?: number;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SERVICE_UNAVAILABLE",
                     *         "details": {},
                     *         "message": "服务暂时不可用"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    screenshots_get_screenshot_full: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                sha256: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：SCREENSHOT_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SCREENSHOT_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    screenshots_get_screenshot_thumb: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                sha256: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：SCREENSHOT_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SCREENSHOT_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    notifications_list_notification_channels: {
        parameters: {
            query?: {
                type?: components["schemas"]["NotifyChannelType"] | null;
                enabled?: boolean | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    notifications_create_notification_channel: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["NotifyChannelRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description 与当前状态冲突：NOTIFY_CHANNEL_CONFLICT */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "NOTIFY_CHANNEL_CONFLICT",
                     *         "details": {},
                     *         "message": "与当前状态冲突"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 参数校验失败：NOTIFY_CONFIG_INVALID */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "NOTIFY_CONFIG_INVALID",
                     *         "details": {},
                     *         "message": "参数校验失败"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    notifications_update_notification_channel: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                channel_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["NotifyChannelRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description 资源不存在：NOTIFY_CHANNEL_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "NOTIFY_CHANNEL_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 与当前状态冲突：NOTIFY_CHANNEL_CONFLICT */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "NOTIFY_CHANNEL_CONFLICT",
                     *         "details": {},
                     *         "message": "与当前状态冲突"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 参数校验失败：NOTIFY_CONFIG_INVALID */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "NOTIFY_CONFIG_INVALID",
                     *         "details": {},
                     *         "message": "参数校验失败"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    notifications_delete_notification_channel: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                channel_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description 资源不存在：NOTIFY_CHANNEL_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "NOTIFY_CHANNEL_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    notifications_test_notification_channel: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                channel_id: string;
            };
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["NotifyTestRequest"] | null;
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description 资源不存在：NOTIFY_CHANNEL_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "NOTIFY_CHANNEL_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description 下游依赖失败：NOTIFY_SEND_FAILED */
            502: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "NOTIFY_SEND_FAILED",
                     *         "details": {},
                     *         "message": "下游依赖失败"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    notifications_list_push_subscriptions: {
        parameters: {
            query?: {
                include_disabled?: boolean;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    notifications_create_push_subscription: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PushSubscribeRequest"];
            };
        };
        responses: {
            /** @description 该 endpoint 已登记，本次为幂等更新 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description 参数校验失败：PUSH_SUBSCRIPTION_INVALID */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "PUSH_SUBSCRIPTION_INVALID",
                     *         "details": {},
                     *         "message": "参数校验失败"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    notifications_delete_push_subscription_by_endpoint: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PushUnsubscribeRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    notifications_delete_push_subscription: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                subscription_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description 资源不存在：PUSH_SUBSCRIPTION_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "PUSH_SUBSCRIPTION_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    notifications_get_vapid_public_key: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description 服务暂时不可用：PUSH_NOT_CONFIGURED */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "PUSH_NOT_CONFIGURED",
                     *         "details": {},
                     *         "message": "服务暂时不可用"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    pipelines_list_pipelines: {
        parameters: {
            query?: {
                status?: string | null;
                source?: string | null;
                schedule_id?: string | null;
                since?: string | null;
                page?: number;
                size?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description 请求不成立：INVALID_PARAMETER、INVALID_PAGINATION */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "INVALID_PARAMETER",
                     *         "details": {},
                     *         "message": "请求不成立"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    pipelines_submit_pipeline: {
        parameters: {
            query?: never;
            header?: {
                "Idempotency-Key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PipelineCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description 请求不成立：PIPELINE_EMPTY、PIPELINE_TOO_MANY_TASKS、UNKNOWN_TASK_TYPE */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "PIPELINE_EMPTY",
                     *         "details": {},
                     *         "message": "请求不成立"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 与当前状态冲突：QUEUE_PAUSED、IDEMPOTENCY_KEY_CONFLICT */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "QUEUE_PAUSED",
                     *         "details": {},
                     *         "message": "与当前状态冲突"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 参数校验失败：TASK_PARAM_INVALID */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "TASK_PARAM_INVALID",
                     *         "details": {},
                     *         "message": "参数校验失败"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 请求过于频繁：QUEUE_FULL */
            429: {
                headers: {
                    /** @description 建议的重试等待秒数（整数） */
                    "Retry-After"?: number;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "QUEUE_FULL",
                     *         "details": {},
                     *         "message": "请求过于频繁"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    pipelines_current_pipeline: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    pipelines_get_pipeline: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                pipeline_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：PIPELINE_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "PIPELINE_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    pipelines_cancel_pipeline: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                pipeline_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：PIPELINE_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "PIPELINE_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 与当前状态冲突：PIPELINE_NOT_CANCELLABLE */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "PIPELINE_NOT_CANCELLABLE",
                     *         "details": {},
                     *         "message": "与当前状态冲突"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    pipelines_list_pipeline_logs: {
        parameters: {
            query?: {
                "source[]"?: string[] | null;
                "level[]"?: string[] | null;
                after_id?: number | null;
                order?: string;
                page?: number | null;
                size?: number;
            };
            header?: never;
            path: {
                pipeline_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description 请求不成立：INVALID_PARAMETER、INVALID_PAGINATION */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "INVALID_PARAMETER",
                     *         "details": {},
                     *         "message": "请求不成立"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：PIPELINE_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "PIPELINE_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    pipelines_retry_pipeline: {
        parameters: {
            query?: never;
            header?: {
                "Idempotency-Key"?: string | null;
            };
            path: {
                pipeline_id: string;
            };
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["RetryRequest"] | null;
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description 请求不成立：INVALID_PARAMETER */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "INVALID_PARAMETER",
                     *         "details": {},
                     *         "message": "请求不成立"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：PIPELINE_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "PIPELINE_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 与当前状态冲突：QUEUE_PAUSED、IDEMPOTENCY_KEY_CONFLICT */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "QUEUE_PAUSED",
                     *         "details": {},
                     *         "message": "与当前状态冲突"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description 请求过于频繁：QUEUE_FULL */
            429: {
                headers: {
                    /** @description 建议的重试等待秒数（整数） */
                    "Retry-After"?: number;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "QUEUE_FULL",
                     *         "details": {},
                     *         "message": "请求过于频繁"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    pipelines_list_pipeline_screenshots: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                pipeline_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：PIPELINE_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "PIPELINE_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    pipelines_list_pipeline_tasks: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                pipeline_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：PIPELINE_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "PIPELINE_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    queue_get_queue: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    queue_clear_queue: {
        parameters: {
            query?: {
                source?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description 请求不成立：INVALID_PARAMETER */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "INVALID_PARAMETER",
                     *         "details": {},
                     *         "message": "请求不成立"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    queue_pause_queue: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    queue_resume_queue: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    queue_reprioritize_pipeline: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                pipeline_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PriorityRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：PIPELINE_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "PIPELINE_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 与当前状态冲突：QUEUE_ITEM_NOT_PENDING */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "QUEUE_ITEM_NOT_PENDING",
                     *         "details": {},
                     *         "message": "与当前状态冲突"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    resources_list_copilot_assets: {
        parameters: {
            query?: {
                page?: number;
                size?: number;
                q?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CopilotAssetPageOut"];
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description 服务暂时不可用：SERVICE_UNAVAILABLE */
            503: {
                headers: {
                    /** @description 建议的重试等待秒数（整数） */
                    "Retry-After"?: number;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SERVICE_UNAVAILABLE",
                     *         "details": {},
                     *         "message": "服务暂时不可用"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    resources_get_copilot_asset: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                asset_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：RESOURCE_ASSET_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "RESOURCE_ASSET_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description 服务端内部错误：RESOURCE_LOAD_FAILED */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "RESOURCE_LOAD_FAILED",
                     *         "details": {},
                     *         "message": "服务端内部错误"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 服务暂时不可用：SERVICE_UNAVAILABLE */
            503: {
                headers: {
                    /** @description 建议的重试等待秒数（整数） */
                    "Retry-After"?: number;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SERVICE_UNAVAILABLE",
                     *         "details": {},
                     *         "message": "服务暂时不可用"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    resources_delete_copilot_asset: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                asset_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：RESOURCE_ASSET_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "RESOURCE_ASSET_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description 服务暂时不可用：SERVICE_UNAVAILABLE */
            503: {
                headers: {
                    /** @description 建议的重试等待秒数（整数） */
                    "Retry-After"?: number;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SERVICE_UNAVAILABLE",
                     *         "details": {},
                     *         "message": "服务暂时不可用"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    resources_retain_copilot_asset: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                asset_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RetainRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：RESOURCE_ASSET_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "RESOURCE_ASSET_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description 服务暂时不可用：SERVICE_UNAVAILABLE */
            503: {
                headers: {
                    /** @description 建议的重试等待秒数（整数） */
                    "Retry-After"?: number;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SERVICE_UNAVAILABLE",
                     *         "details": {},
                     *         "message": "服务暂时不可用"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    resources_list_items: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ItemOut"][];
                };
            };
            /** @description 服务端内部错误：RESOURCE_LOAD_FAILED */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "RESOURCE_LOAD_FAILED",
                     *         "details": {},
                     *         "message": "服务端内部错误"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    resources_get_item_icon: {
        parameters: {
            query: {
                item_id: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "image/png": unknown;
                };
            };
            /** @description 资源不存在：RESOURCE_ASSET_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "RESOURCE_ASSET_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description 服务端内部错误：RESOURCE_LOAD_FAILED */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "RESOURCE_LOAD_FAILED",
                     *         "details": {},
                     *         "message": "服务端内部错误"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    schedules_list_schedules: {
        parameters: {
            query?: {
                enabled?: boolean | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SchedulePage"];
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    schedules_create_schedule: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ScheduleWrite"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ScheduleView"];
                };
            };
            /** @description 请求不成立：SCHEDULE_CRON_INVALID、UNKNOWN_TASK_TYPE、TASK_PARAM_DEPRECATED */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SCHEDULE_CRON_INVALID",
                     *         "details": {},
                     *         "message": "请求不成立"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 与当前状态冲突：SCHEDULE_NAME_CONFLICT */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SCHEDULE_NAME_CONFLICT",
                     *         "details": {},
                     *         "message": "与当前状态冲突"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 参数校验失败：TASK_PARAM_INVALID、VALIDATION_ERROR */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "TASK_PARAM_INVALID",
                     *         "details": {},
                     *         "message": "参数校验失败"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    schedules_get_schedule: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                schedule_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ScheduleView"];
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：SCHEDULE_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SCHEDULE_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    schedules_update_schedule: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                schedule_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ScheduleWrite"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ScheduleView"];
                };
            };
            /** @description 请求不成立：SCHEDULE_CRON_INVALID、UNKNOWN_TASK_TYPE、TASK_PARAM_DEPRECATED */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SCHEDULE_CRON_INVALID",
                     *         "details": {},
                     *         "message": "请求不成立"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：SCHEDULE_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SCHEDULE_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 与当前状态冲突：SCHEDULE_NAME_CONFLICT */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SCHEDULE_NAME_CONFLICT",
                     *         "details": {},
                     *         "message": "与当前状态冲突"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 参数校验失败：TASK_PARAM_INVALID、VALIDATION_ERROR */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "TASK_PARAM_INVALID",
                     *         "details": {},
                     *         "message": "参数校验失败"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    schedules_delete_schedule: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                schedule_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：SCHEDULE_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SCHEDULE_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    schedules_patch_schedule: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                schedule_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SchedulePatch"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ScheduleView"];
                };
            };
            /** @description 请求不成立：SCHEDULE_CRON_INVALID */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SCHEDULE_CRON_INVALID",
                     *         "details": {},
                     *         "message": "请求不成立"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：SCHEDULE_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SCHEDULE_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 参数校验失败：VALIDATION_ERROR */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "VALIDATION_ERROR",
                     *         "details": {},
                     *         "message": "参数校验失败"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    schedules_run_schedule: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                schedule_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ScheduleRunRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ScheduleRunAccepted"];
                };
            };
            /** @description 请求不成立：PIPELINE_EMPTY、UNKNOWN_TASK_TYPE */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "PIPELINE_EMPTY",
                     *         "details": {},
                     *         "message": "请求不成立"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：SCHEDULE_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SCHEDULE_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 与当前状态冲突：QUEUE_PAUSED、PIPELINE_ALREADY_RUNNING */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "QUEUE_PAUSED",
                     *         "details": {},
                     *         "message": "与当前状态冲突"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 参数校验失败：TASK_PARAM_INVALID */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "TASK_PARAM_INVALID",
                     *         "details": {},
                     *         "message": "参数校验失败"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 请求过于频繁：QUEUE_FULL */
            429: {
                headers: {
                    /** @description 建议的重试等待秒数（整数） */
                    "Retry-After"?: number;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "QUEUE_FULL",
                     *         "details": {},
                     *         "message": "请求过于频繁"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    screenshots_list_screenshots: {
        parameters: {
            query?: {
                pipeline_id?: string | null;
                trigger?: string | null;
                since?: number | null;
                page?: number;
                size?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description 请求不成立：INVALID_PAGINATION、INVALID_PARAMETER */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "INVALID_PAGINATION",
                     *         "details": {},
                     *         "message": "请求不成立"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    screenshots_get_screenshot: {
        parameters: {
            query?: {
                as?: string | null;
            };
            header?: never;
            path: {
                id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description 请求不成立：INVALID_PARAMETER */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "INVALID_PARAMETER",
                     *         "details": {},
                     *         "message": "请求不成立"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：SCREENSHOT_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SCREENSHOT_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源已永久移除：SCREENSHOT_EXPIRED */
            410: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SCREENSHOT_EXPIRED",
                     *         "details": {},
                     *         "message": "资源已永久移除"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    settings_get_settings: {
        parameters: {
            query?: {
                group?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    settings_update_settings: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SettingUpdateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description 请求不成立：SETTING_KEY_UNKNOWN */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SETTING_KEY_UNKNOWN",
                     *         "details": {},
                     *         "message": "请求不成立"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 身份有效但动作被拒绝：SETTING_READONLY */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SETTING_READONLY",
                     *         "details": {},
                     *         "message": "身份有效但动作被拒绝"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 参数校验失败：SETTING_VALUE_INVALID */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SETTING_VALUE_INVALID",
                     *         "details": {},
                     *         "message": "参数校验失败"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 服务端内部错误：SETTING_APPLY_FAILED */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SETTING_APPLY_FAILED",
                     *         "details": {},
                     *         "message": "服务端内部错误"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 服务暂时不可用：LLM_ENCRYPTION_KEY_INVALID */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "LLM_ENCRYPTION_KEY_INVALID",
                     *         "details": {},
                     *         "message": "服务暂时不可用"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    settings_reset_settings: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SettingResetRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description 请求不成立：SETTING_KEY_UNKNOWN */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SETTING_KEY_UNKNOWN",
                     *         "details": {},
                     *         "message": "请求不成立"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 身份有效但动作被拒绝：SETTING_READONLY */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SETTING_READONLY",
                     *         "details": {},
                     *         "message": "身份有效但动作被拒绝"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    settings_get_settings_schema: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
        };
    };
    snippets_list_snippets: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiSnippetPage"];
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    snippets_create_snippet: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ApiSnippetWrite"];
            };
        };
        responses: {
            /** @description Created */
            201: {
                headers: {
                    /** @description 新建收藏的 URI */
                    Location?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiSnippetView"];
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 与当前状态冲突：API_SNIPPET_NAME_CONFLICT */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "API_SNIPPET_NAME_CONFLICT",
                     *         "details": {},
                     *         "message": "与当前状态冲突"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 参数校验失败：VALIDATION_ERROR */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "VALIDATION_ERROR",
                     *         "details": {},
                     *         "message": "参数校验失败"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    snippets_get_snippet: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                snippet_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiSnippetView"];
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：API_SNIPPET_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "API_SNIPPET_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    snippets_update_snippet: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                snippet_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ApiSnippetWrite"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiSnippetView"];
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：API_SNIPPET_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "API_SNIPPET_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 与当前状态冲突：API_SNIPPET_NAME_CONFLICT */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "API_SNIPPET_NAME_CONFLICT",
                     *         "details": {},
                     *         "message": "与当前状态冲突"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 参数校验失败：VALIDATION_ERROR */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "VALIDATION_ERROR",
                     *         "details": {},
                     *         "message": "参数校验失败"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    snippets_delete_snippet: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                snippet_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：API_SNIPPET_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "API_SNIPPET_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    sources_clear_sources_cache: {
        parameters: {
            query?: {
                /** @description 只清这个来源；不传则清空全部（如 prts） */
                source?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CacheClearResponse"];
                };
            };
            /** @description 请求不成立：INVALID_PARAMETER */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "INVALID_PARAMETER",
                     *         "details": {},
                     *         "message": "请求不成立"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    sources_get_sources_status: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SourcesStatusResponse"];
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    system_exchange_cookie: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 身份有效但动作被拒绝：FORBIDDEN */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "FORBIDDEN",
                     *         "details": {},
                     *         "message": "身份有效但动作被拒绝"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    system_clear_cookie: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 身份有效但动作被拒绝：FORBIDDEN */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "FORBIDDEN",
                     *         "details": {},
                     *         "message": "身份有效但动作被拒绝"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    system_health: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HealthResponse"];
                };
            };
        };
    };
    system_list_logs: {
        parameters: {
            query?: {
                source?: string[] | null;
                level?: string[] | null;
                pipeline_id?: string | null;
                task_id?: string | null;
                logger?: string | null;
                q?: string | null;
                since?: number | null;
                until?: number | null;
                after_id?: number | null;
                before_id?: number | null;
                order?: string;
                page?: number | null;
                size?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description 请求不成立：INVALID_PARAMETER、INVALID_PAGINATION */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "INVALID_PARAMETER",
                     *         "details": {},
                     *         "message": "请求不成立"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    system_delete_logs: {
        parameters: {
            query?: {
                source?: string[] | null;
                before?: number | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description 请求不成立：INVALID_PARAMETER */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "INVALID_PARAMETER",
                     *         "details": {},
                     *         "message": "请求不成立"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    system_export_logs: {
        parameters: {
            query?: {
                format?: string;
                source?: string[] | null;
                level?: string[] | null;
                pipeline_id?: string | null;
                task_id?: string | null;
                logger?: string | null;
                q?: string | null;
                since?: number | null;
                until?: number | null;
                after_id?: number | null;
                before_id?: number | null;
                order?: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description 请求不成立：INVALID_PARAMETER、INVALID_PAGINATION */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "INVALID_PARAMETER",
                     *         "details": {},
                     *         "message": "请求不成立"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    tasks_list_types: {
        parameters: {
            query?: {
                /** @description 中文标签语言；当前仅支持 zh */
                lang?: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description 请求不成立：INVALID_PARAMETER */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "INVALID_PARAMETER",
                     *         "details": {},
                     *         "message": "请求不成立"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    tasks_get_type: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                type_name: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description 请求不成立：UNKNOWN_TASK_TYPE */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNKNOWN_TASK_TYPE",
                     *         "details": {},
                     *         "message": "请求不成立"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    tasks_validate: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TaskValidateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description 请求不成立：UNKNOWN_TASK_TYPE、TASK_PARAM_DEPRECATED */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNKNOWN_TASK_TYPE",
                     *         "details": {},
                     *         "message": "请求不成立"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 参数校验失败：TASK_PARAM_INVALID、VALIDATION_ERROR */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "TASK_PARAM_INVALID",
                     *         "details": {},
                     *         "message": "参数校验失败"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    tasks_get_task: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                task_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：TASK_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "TASK_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    updates_list_updates: {
        parameters: {
            query?: {
                target?: string | null;
                status?: string | null;
                page?: number;
                size?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description 请求不成立：INVALID_PARAMETER、INVALID_PAGINATION */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "INVALID_PARAMETER",
                     *         "details": {},
                     *         "message": "请求不成立"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    updates_update_core: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CoreUpdateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description 请求不成立：INVALID_PARAMETER */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "INVALID_PARAMETER",
                     *         "details": {},
                     *         "message": "请求不成立"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 身份有效但动作被拒绝：FORBIDDEN */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "FORBIDDEN",
                     *         "details": {},
                     *         "message": "身份有效但动作被拒绝"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 与当前状态冲突：UPDATE_ALREADY_RUNNING、ALREADY_LATEST_VERSION、UPDATE_BLOCKED_BY_PIPELINE、UPDATE_QUEUE_BUSY_TIMEOUT */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UPDATE_ALREADY_RUNNING",
                     *         "details": {},
                     *         "message": "与当前状态冲突"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description 服务暂时不可用：SERVICE_UNAVAILABLE */
            503: {
                headers: {
                    /** @description 建议的重试等待秒数（整数） */
                    "Retry-After"?: number;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SERVICE_UNAVAILABLE",
                     *         "details": {},
                     *         "message": "服务暂时不可用"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    updates_rollback_core: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["CoreRollbackRequest"] | null;
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 身份有效但动作被拒绝：FORBIDDEN */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "FORBIDDEN",
                     *         "details": {},
                     *         "message": "身份有效但动作被拒绝"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 与当前状态冲突：UPDATE_ALREADY_RUNNING、UPDATE_BLOCKED_BY_PIPELINE、UPDATE_QUEUE_BUSY_TIMEOUT */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UPDATE_ALREADY_RUNNING",
                     *         "details": {},
                     *         "message": "与当前状态冲突"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description 服务端内部错误：UPDATE_ROLLBACK_FAILED */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UPDATE_ROLLBACK_FAILED",
                     *         "details": {},
                     *         "message": "服务端内部错误"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 服务暂时不可用：SERVICE_UNAVAILABLE */
            503: {
                headers: {
                    /** @description 建议的重试等待秒数（整数） */
                    "Retry-After"?: number;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SERVICE_UNAVAILABLE",
                     *         "details": {},
                     *         "message": "服务暂时不可用"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    updates_update_game: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["GameUpdateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description 请求不成立：INVALID_PARAMETER */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "INVALID_PARAMETER",
                     *         "details": {},
                     *         "message": "请求不成立"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 身份有效但动作被拒绝：FORBIDDEN */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "FORBIDDEN",
                     *         "details": {},
                     *         "message": "身份有效但动作被拒绝"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 与当前状态冲突：UPDATE_ALREADY_RUNNING、UPDATE_BLOCKED_BY_PIPELINE、UPDATE_QUEUE_BUSY_TIMEOUT */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UPDATE_ALREADY_RUNNING",
                     *         "details": {},
                     *         "message": "与当前状态冲突"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description 下游依赖失败：GAME_INSTALL_FAILED */
            502: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "GAME_INSTALL_FAILED",
                     *         "details": {},
                     *         "message": "下游依赖失败"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 服务暂时不可用：DEVICE_NOT_CONNECTED、SERVICE_UNAVAILABLE */
            503: {
                headers: {
                    /** @description 建议的重试等待秒数（整数） */
                    "Retry-After"?: number;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "DEVICE_NOT_CONNECTED",
                     *         "details": {},
                     *         "message": "服务暂时不可用"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    updates_update_resource: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ResourceUpdateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description 请求不成立：INVALID_PARAMETER */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "INVALID_PARAMETER",
                     *         "details": {},
                     *         "message": "请求不成立"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 身份有效但动作被拒绝：FORBIDDEN */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "FORBIDDEN",
                     *         "details": {},
                     *         "message": "身份有效但动作被拒绝"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 与当前状态冲突：UPDATE_ALREADY_RUNNING、UPDATE_BLOCKED_BY_PIPELINE、UPDATE_QUEUE_BUSY_TIMEOUT */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UPDATE_ALREADY_RUNNING",
                     *         "details": {},
                     *         "message": "与当前状态冲突"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description 服务端内部错误：UPDATE_DISK_INSUFFICIENT */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UPDATE_DISK_INSUFFICIENT",
                     *         "details": {},
                     *         "message": "服务端内部错误"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 下游依赖失败：UPDATE_MANIFEST_UNAVAILABLE */
            502: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UPDATE_MANIFEST_UNAVAILABLE",
                     *         "details": {},
                     *         "message": "下游依赖失败"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 服务暂时不可用：SERVICE_UNAVAILABLE */
            503: {
                headers: {
                    /** @description 建议的重试等待秒数（整数） */
                    "Retry-After"?: number;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SERVICE_UNAVAILABLE",
                     *         "details": {},
                     *         "message": "服务暂时不可用"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    updates_update_status: {
        parameters: {
            query?: {
                refresh?: boolean;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description 下游依赖失败：UPDATE_MANIFEST_UNAVAILABLE、GAME_VERSION_UNKNOWN */
            502: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UPDATE_MANIFEST_UNAVAILABLE",
                     *         "details": {},
                     *         "message": "下游依赖失败"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 服务暂时不可用：SERVICE_UNAVAILABLE */
            503: {
                headers: {
                    /** @description 建议的重试等待秒数（整数） */
                    "Retry-After"?: number;
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SERVICE_UNAVAILABLE",
                     *         "details": {},
                     *         "message": "服务暂时不可用"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    updates_get_update: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                update_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：UPDATE_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UPDATE_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    updates_cancel_update: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                update_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：UPDATE_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UPDATE_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 与当前状态冲突：UPDATE_NOT_CANCELLABLE */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UPDATE_NOT_CANCELLABLE",
                     *         "details": {},
                     *         "message": "与当前状态冲突"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    updates_retry_update: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                update_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description token 缺失或不匹配：UNAUTHORIZED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UNAUTHORIZED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 资源不存在：UPDATE_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UPDATE_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 与当前状态冲突：UPDATE_NOT_CANCELLABLE、UPDATE_ALREADY_RUNNING */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UPDATE_NOT_CANCELLABLE",
                     *         "details": {},
                     *         "message": "与当前状态冲突"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
}

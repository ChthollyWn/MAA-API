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
        /** 执行单点点击 */
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
        /** 执行长按操作 */
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
        /** 执行滑动操作 */
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
    "/api/resources/items": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 列出 Fight.drops 可用物品
         * @description 读取当前 MaaCore `resource/item_index.json` 并按名称、物品 ID 稳定排序。`icon_url` 指向同源图片端点；对应图片不可用时为 `null`。索引缺失或无效时返回统一 API 错误。
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
         * @description 只返回 item_index.json 中登记且位于 `resource/template/items` 的 PNG 图标。
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
         * @description 返回截图图像字节；as=base64 时返回面向 agent 的 JSON 包装。
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
         * @description 用请求头里的 token 换取一枚 `HttpOnly; SameSite=Lax` 的 `maa_token` cookie。
         *
         *     - 用途：浏览器 WebSocket 构造器不能自定义请求头（docs/05 §5.4），换取后握手自动携带 cookie，token 不进 URL 与访问日志
         *     - 副作用：下发 `Set-Cookie`，值即配置的 `access_token`、`Path=/`
         *     - 未带 `Secure` 属性：HTTPS 判定（`X-Forwarded-Proto`）归 M15
         *     - 免鉴权模式（未配置 `access_token`）下返回 `204` 但**不下发** cookie，此时没有可下发的秘密，WebSocket 握手也不校验
         *     - 写方法：仅凭 cookie 调用本端点同样 403（cookie 渠道收窄，docs/05 §5.1）
         *     - `401 UNAUTHORIZED`：缺少 token 或 token 不匹配；`403 FORBIDDEN`：token 仅来自 cookie
         *     - 响应无体（`204`），成功与否只看状态码
         */
        post: operations["system_exchange_cookie"];
        /**
         * 清除认证 cookie
         * @description 清除 `maa_token` cookie，供前端「登出」时调用。
         *
         *     - 为什么需要服务端端点：cookie 是 `HttpOnly`，前端 JavaScript 删不掉（docs/13 §4），不清理则登出后 WebSocket 仍能连上
         *     - 副作用：下发 `Set-Cookie`（空值 + `Max-Age=0`），浏览器随后删除该 cookie
         *     - 写方法，必须经头部 token 鉴权；仅凭 cookie 调用会 403
         *     - 免鉴权模式下无需凭据（本就没有 cookie 可清时调用也无害）
         *     - `401 UNAUTHORIZED`：缺少 token 或 token 不匹配；`403 FORBIDDEN`：token 仅来自 cookie
         *     - 响应无体（`204`）
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
         * @description 返回服务自身的健康状态，供外部监控与容器健康检查探测。
         *
         *     - **免鉴权**：即使配置了 `access_token` 也不校验（docs/05 §5.3），无需携带凭据
         *     - 只返回状态，不返回业务数据；`auth_enabled` 为 `false` 时前端直接跳过登录页
         *     - `auth_enabled` 由配置的 `access_token` 是否为空决定（docs/05 §5.2）
         *     - `version` 取已安装分发包元数据，读不到时回退 `0.1.0`
         *     - `started_at` 是进程启动时间（ISO 8601 字符串），尚未写入时为 `null`（docs/05 §3.3）
         *     - `core` 含内核状态、pid 和代际；`device` 复用设备快照；`queue` 只含待处理/运行数和暂停状态
         *     - lifespan 尚未装配运行态对象时，`core` / `device` / `queue` 为 `null`，仍返回 200
         *     - 不校验 token；队列概览只执行只读查询，不返回队列条目
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
         * @description 按来源和/或时间界限删除日志；至少提供一个条件。
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
         * @description 返回全部 9 种任务类型的完整参数 schema，供前端动态生成表单、agent 了解可用参数。
         *
         *     - 只读端点，**无副作用**
         *     - `schema` 是 pydantic 直出的标准 JSON Schema；中文标签、分组、控件提示与风险标记挂在 `x-label` / `x-group` / `x-widget` / `x-enum-labels` / `x-risk` / `x-depends-on` 上，取值范围走 `minimum` / `maximum` / `enum` 等原生关键字
         *     - 清单固定 9 条，响应固定 `total=9, page=1, size=9`（不接受分页参数）
         *     - `lang` 当前仅支持 `zh`，其它值返回 400 `INVALID_PARAMETER`
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
         * @description 按类型名返回单个任务类型的 schema，结构与 `GET /api/tasks/types` 的 `items` 元素一致（不套分页信封）。
         *
         *     - 只读端点，**无副作用**
         *     - `type_name` 不在 9 种任务类型内时返回 400 `UNKNOWN_TASK_TYPE`（错误码表里它就是 400，不是 404）
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
         * @description 只校验不提交：对任务数组跑完整的 pydantic 判别联合校验，并按全局渠道默认值规范化，返回投递给内核的最终参数与用户原始提交。
         *
         *     - **无副作用**：不落库、不入队、不碰内核；只读 setting 表
         *     - 未显式指定 `client_type` / `server` 的任务会注入 setting 表里的全局默认值（默认 Bilibili / CN）；显式传 `null` 视为刻意留空，不注入
         *     - `params` 是投递给内核的形态（已剔除 null、已注入默认值），`raw_params` 是原始提交快照（保留显式 null），可用于审计与回显
         *     - 任务数 1..32，越界返回 422 `VALIDATION_ERROR`
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
        /** 受理 MaaCore 更新 */
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
        /** 受理 MaaCore 回滚 */
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
         * AwardInput
         * @description 领取各种奖励。
         *
         *     按 docs/05 §7.6：``mail``/``recruit``/``orundum``/``mining``/``specialaccess``
         *     一律 ``default=None``，不预设 True/False —— 旧 ``TaskRequest`` 把它们写成 True
         *     与 docstring 冲突，且服务端预设默认值会在内核调整默认行为时产生偏差。
         * @example {
         *       "award": true,
         *       "mail": true,
         *       "name": "Award"
         *     }
         */
        AwardInput: {
            /**
             * Award
             * @description 领取每日/每周任务奖励；不传则由内核使用默认值（True）
             */
            award?: boolean | null;
            /**
             * Enable
             * @description 是否启用本任务；不传则由内核使用默认值（True）
             */
            enable?: boolean | null;
            /**
             * Mail
             * @description 领取所有邮件奖励；不传则由内核使用默认值（False）
             */
            mail?: boolean | null;
            /**
             * Mining
             * @description 领取限时开采许可的合成玉奖励；不传则由内核使用默认值（False）
             */
            mining?: boolean | null;
            /**
             * @description 判别联合 discriminator，固定为 Award；由任务类型选择器决定，用户不需要填写 (enum property replaced by openapi-typescript)
             * @enum {string}
             */
            name: "Award";
            /**
             * Orundum
             * @description 领取幸运墙的合成玉奖励；不传则由内核使用默认值（False）
             */
            orundum?: boolean | null;
            /**
             * Recruit
             * @description 领取限定池子赠送的每日免费单抽；不传则由内核使用默认值（False）
             */
            recruit?: boolean | null;
            /**
             * Specialaccess
             * @description 领取五周年赠送的月卡奖励；不传则由内核使用默认值（False）
             */
            specialaccess?: boolean | null;
        };
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
             * @description 客户端版本；docstring 标注「必选，填空则不执行」，实际上由全局渠道默认值注入保证非空（未指定时套用默认 Bilibili）。可选值：Official / Bilibili / txwy / YoStarEN / YoStarJP / YoStarKR
             */
            client_type?: ("Official" | "Bilibili" | "txwy" | "YoStarEN" | "YoStarJP" | "YoStarKR") | null;
            /**
             * Enable
             * @description 是否启用本任务；不传则由内核使用默认值（True）
             */
            enable?: boolean | null;
            /**
             * @description 判别联合 discriminator，固定为 CloseDown；由任务类型选择器决定，用户不需要填写 (enum property replaced by openapi-typescript)
             * @enum {string}
             */
            name: "CloseDown";
        };
        /**
         * CoreHealth
         * @description 内核监督器的最小首页状态。
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
         * DeviceHealth
         * @description 复用 ``DeviceManager.snapshot()`` 的设备状态。
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
         * @description 复用 ``DeviceManager.snapshot()`` 的重试状态。
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
             * @description 节省理智碎石模式；仅在可能产生碎石效果时生效：在碎石确认界面等待，直到当前的 1 点理智恢复完成后再立刻碎石。不传则由内核使用默认值（False）
             */
            DrGrandet?: boolean | null;
            /**
             * Client Type
             * @description 客户端版本；用于游戏崩溃时重启并连回去继续刷，若为空则不启用该功能。未指定时套用全局渠道默认值（默认 Bilibili）
             */
            client_type?: ("Official" | "Bilibili" | "txwy" | "YoStarEN" | "YoStarJP" | "YoStarKR") | null;
            /**
             * Drops
             * @description 指定掉落数量；key 为 item_id（见 resource/item_index.json），value 为数量。是或的关系，即任一达到即停止任务
             */
            drops?: {
                [key: string]: number;
            } | null;
            /**
             * Enable
             * @description 是否启用本任务；不传则由内核使用默认值（True）
             */
            enable?: boolean | null;
            /**
             * Expiring Medicine
             * @description 最大使用 48 小时内过期理智药数量；不传则由内核使用默认值（0）
             */
            expiring_medicine?: number | null;
            /**
             * Medicine
             * @description 最大使用理智药数量；不传则由内核使用默认值（0，即不吃药）
             */
            medicine?: number | null;
            /**
             * @description 判别联合 discriminator，固定为 Fight；由任务类型选择器决定，用户不需要填写 (enum property replaced by openapi-typescript)
             * @enum {string}
             */
            name: "Fight";
            /**
             * Penguin Id
             * @description 企鹅数据汇报 id；仅在 report_to_penguin 为 True 时有效
             */
            penguin_id?: string | null;
            /**
             * Report To Penguin
             * @description 是否汇报企鹅数据；不传则由内核使用默认值（False）
             */
            report_to_penguin?: boolean | null;
            /**
             * Series
             * @description 连战次数；-1 为禁用切换，0 为自动切换为当前可用的最大次数（如当前理智不够 6 次，则选择最低可用次数），1~6 为指定连战次数
             */
            series?: number | null;
            /**
             * Server
             * @description 服务器，会影响掉落识别及上传；未指定时套用全局默认值（CN）。可选值：CN / US / JP / KR
             */
            server?: ("CN" | "US" | "JP" | "KR") | null;
            /**
             * Stage
             * @description 关卡名；留空则识别当前/上次的关卡。支持全部主线关卡（如 1-7、S3-2），可在关卡结尾输入 Normal/Hard 表示需要切换标准与磨难难度；剿灭作战必须输入 Annihilation；当期 SS 活动后三关必须输入完整关卡编号。不支持运行中设置
             * @example 1-7
             * @example S3-2
             * @example Annihilation
             * @example CE-6Hard
             */
            stage?: string | null;
            /**
             * Stone
             * @description 最大吃石头（源石）数量；不传则由内核使用默认值（0，即不碎石）
             */
            stone?: number | null;
            /**
             * Times
             * @description 指定战斗次数；不传则由内核使用默认值（无穷大）
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
         * @description ``GET /api/system/health`` 的响应体（docs/05 §6.1、§3.3）。
         *
         *     内核（``core``）、设备（``device``）与队列（``queue``）只读取 lifespan 装配到
         *     ``app.state`` 的运行时对象。lifespan 尚未完成或没有装配对象时，这些字段为
         *     ``null``，健康探测仍返回 200。队列只包含计数与暂停状态，不暴露队列详情。
         *
         *     ``started_at`` 按 docs/05 §3.3「尚未发生的字段返回 ``null`` 而不是省略键」，
         *     类型是 ``str | None``、默认 ``None``，响应里始终出现该键；写入方是 M3-09 的
         *     lifespan（``app.state.started_at``），本模块只负责读取。
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
             * @description 是否启用宿舍「未进驻」选项；不传则由内核使用默认值（False）
             */
            dorm_notstationed_enabled?: boolean | null;
            /**
             * Dorm Trust Enabled
             * @description 是否将宿舍剩余位置填入信赖未满干员；不传则由内核使用默认值（False）
             */
            dorm_trust_enabled?: boolean | null;
            /**
             * Drones
             * @description 无人机用途；mode == 10000 时该字段无效（会被忽略）。可选值包括 "_NotUse", "Money", "SyntheticJade", "CombatRecord", "PureGold", "OriginStone", "Chip"；不传则由内核使用默认值（_NotUse）
             */
            drones?: ("_NotUse" | "Money" | "SyntheticJade" | "CombatRecord" | "PureGold" | "OriginStone" | "Chip") | null;
            /**
             * Enable
             * @description 是否启用本任务；不传则由内核使用默认值（True）
             */
            enable?: boolean | null;
            /**
             * Facility
             * @description 要换班的设施（有序，顺序即换班顺序），必选；设施名选项包括 "Mfg", "Trade", "Power", "Control", "Reception", "Office", "Dorm"。不支持运行中设置
             */
            facility?: ("Mfg" | "Trade" | "Power" | "Control" | "Reception" | "Office" | "Dorm")[] | null;
            /**
             * Filename
             * @description 自定义配置路径，仅 mode == 10000 时生效且必填，否则会被忽略。不支持运行中设置
             */
            filename?: string | null;
            /**
             * Mode
             * @description 换班工作模式；0 为默认换班模式（单设施最优解），10000 为自定义换班模式（读取用户配置），20000 为一键轮换模式（会跳过控制中枢、发电站、宿舍以及办公室，其余设施不进行换班但保留基本操作，如使用无人机、会客室逻辑）。不传则由内核使用默认值（0）
             */
            mode?: (0 | 10000 | 20000) | null;
            /**
             * @description 判别联合 discriminator，固定为 Infrast；由任务类型选择器决定，用户不需要填写 (enum property replaced by openapi-typescript)
             * @enum {string}
             */
            name: "Infrast";
            /**
             * Plan Index
             * @description 使用配置中的方案序号，仅 mode == 10000 时生效且必填，否则会被忽略。不支持运行中设置
             */
            plan_index?: number | null;
            /**
             * Replenish
             * @description 贸易站「源石碎片」是否自动补货；不传则由内核使用默认值（False）
             */
            replenish?: boolean | null;
            /**
             * Threshold
             * @description 工作心情阈值，取值范围 [0, 1.0]；内核当前默认值为 0.3（服务端不预设，不传则由内核决定）。mode == 10000 时该字段仅针对 autofill 有效
             */
            threshold?: number | null;
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
             * @description 黑名单列表（商品名，如「加急许可」「家具零件」等）；不支持运行中设置，可选
             */
            blacklist?: string[] | null;
            /**
             * Buy First
             * @description 优先购买列表（商品名，如「招聘许可」「龙门币」等）；不支持运行中设置，可选
             */
            buy_first?: string[] | null;
            /**
             * Enable
             * @description 是否启用本任务；不传则由内核使用默认值（True）
             */
            enable?: boolean | null;
            /**
             * Force Shopping If Credit Full
             * @description 是否在信用溢出时无视黑名单；不传则由内核使用默认值（True）
             */
            force_shopping_if_credit_full?: boolean | null;
            /**
             * @description 判别联合 discriminator，固定为 Mall；由任务类型选择器决定，用户不需要填写 (enum property replaced by openapi-typescript)
             * @enum {string}
             */
            name: "Mall";
            /**
             * Only Buy Discount
             * @description 是否只购买折扣物品，只作用于第二轮购买；不传则由内核使用默认值（False）
             */
            only_buy_discount?: boolean | null;
            /**
             * Reserve Max Credit
             * @description 是否在信用点低于 300 时停止购买，只作用于第二轮购买；不传则由内核使用默认值（False）
             */
            reserve_max_credit?: boolean | null;
            /**
             * Shopping
             * @description 是否购物；不支持运行中设置。不传则由内核使用默认值（False）
             */
            shopping?: boolean | null;
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
        NotifyChannelType: "email" | "webhook" | "bark" | "dingtalk" | "wecom";
        /**
         * NotifyEvent
         * @enum {string}
         */
        NotifyEvent: "pipeline_completed" | "pipeline_failed" | "core_crashed" | "device_disconnected" | "update_available" | "update_finished" | "confirmation_required";
        /** NotifyTestRequest */
        NotifyTestRequest: {
            /** @default pipeline_completed */
            event: components["schemas"]["NotifyEvent"];
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
            tasks: (components["schemas"]["StartUpInput"] | components["schemas"]["CloseDownInput"] | components["schemas"]["FightInput"] | components["schemas"]["RecruitInput"] | components["schemas"]["InfrastInput"] | components["schemas"]["MallInput"] | components["schemas"]["AwardInput"] | components["schemas"]["RoguelikeInput"] | components["schemas"]["ReclamationInput"])[];
            /** Title */
            title?: string | null;
        };
        /** PriorityRequest */
        PriorityRequest: {
            /** Priority */
            priority: number;
        };
        /**
         * QueueHealth
         * @description 队列概览，不暴露流水线或任务条目。
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
             * @description 是否启用本任务；不传则由内核使用默认值（True）
             */
            enable?: boolean | null;
            /**
             * Increment Mode
             * @description 点击类型；0 为连点，1 为长按。不传则由内核使用默认值（0）
             */
            increment_mode?: (0 | 1) | null;
            /**
             * Mode
             * @description 模式；0 为刷分与建造点（进入战斗直接退出），1 为沙中之火刷赤金、联络员买水后基地锻造／沙洲遗闻自动制造物品并读档刷货币。不传则由内核使用默认值（0）
             */
            mode?: (0 | 1) | null;
            /**
             * @description 判别联合 discriminator，固定为 Reclamation；由任务类型选择器决定，用户不需要填写 (enum property replaced by openapi-typescript)
             * @enum {string}
             */
            name: "Reclamation";
            /**
             * Num Craft Batches
             * @description 单次最大制造轮数；不传则由内核使用默认值（16）
             */
            num_craft_batches?: number | null;
            /**
             * Theme
             * @description 主题；Fire 为「沙中之火」，Tales 为「沙洲遗闻」
             */
            theme?: ("Fire" | "Tales") | null;
            /**
             * Tools To Craft
             * @description 自动制造的物品；不传则由内核使用默认值（荧光棒）
             */
            tools_to_craft?: string[] | null;
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
             * @description 会去点击确认的 Tag 等级，必选；元素取值范围 1~6。若仅公招计算，可设置为空数组
             */
            confirm?: number[] | null;
            /**
             * Enable
             * @description 是否启用本任务；不传则由内核使用默认值（True）
             */
            enable?: boolean | null;
            /**
             * Expedite
             * @description 是否使用加急许可；不传则由内核使用默认值（False）
             */
            expedite?: boolean | null;
            /**
             * Expedite Times
             * @description 加急次数；仅在 expedite 为 True 时有效。可选，不传则由内核使用默认值（无限使用，直到 times 达到上限）
             */
            expedite_times?: number | null;
            /**
             * Extra Tags Mode
             * @description 选择更多的 Tags；0 为默认行为，1 为选 3 个 Tags（即使可能冲突），2 为如果可能则同时选择更多的高星 Tag 组合（即使可能冲突）。不传则由内核使用默认值（0）
             */
            extra_tags_mode?: (0 | 1 | 2) | null;
            /**
             * First Tags
             * @description 首选 Tags，仅在 Tag 等级为 3 时有效；会尽可能多地选择这里的 Tags（如果有）。属于强制选择，会忽略所有「让 3 星 Tag 不被选择」的设置
             */
            first_tags?: string[] | null;
            /**
             * @description 判别联合 discriminator，固定为 Recruit；由任务类型选择器决定，用户不需要填写 (enum property replaced by openapi-typescript)
             * @enum {string}
             */
            name: "Recruit";
            /**
             * Penguin Id
             * @description 企鹅数据汇报 id；仅在 report_to_penguin 为 True 时有效
             */
            penguin_id?: string | null;
            /**
             * Recruitment Time
             * @description Tag 等级（大于等于 3，JSON 键为字符串）和对应的希望招募时限，单位为分钟；内核默认值都为 540（即 09:00:00）
             */
            recruitment_time?: {
                [key: string]: number;
            } | null;
            /**
             * Refresh
             * @description 是否刷新三星 Tags；不传则由内核使用默认值（False）
             */
            refresh?: boolean | null;
            /**
             * Report To Penguin
             * @description 是否汇报企鹅数据；不传则由内核使用默认值（False）
             */
            report_to_penguin?: boolean | null;
            /**
             * Report To Yituliu
             * @description 是否汇报一图流数据；不传则由内核使用默认值（False）
             */
            report_to_yituliu?: boolean | null;
            /**
             * Select
             * @description 会去点击标签的 Tag 等级，必选；元素取值范围 1~6
             */
            select?: number[] | null;
            /**
             * Server
             * @description 服务器，会影响上传；未指定时套用全局默认值（CN）。可选值：CN / US / JP / KR
             */
            server?: ("CN" | "US" | "JP" | "KR") | null;
            /**
             * Set Time
             * @description 是否设置招募时限；仅在 times 为 0 时生效。不传则由内核使用默认值（True）
             */
            set_time?: boolean | null;
            /**
             * Skip Robot
             * @description 是否在识别到小车词条时跳过；不传则由内核使用默认值（True，即跳过）
             */
            skip_robot?: boolean | null;
            /**
             * Times
             * @description 招募多少次；不传则由内核使用默认值（0）。若仅公招计算，可设置为 0
             */
            times?: number | null;
            /**
             * Yituliu Id
             * @description 一图流汇报 id；仅在 report_to_yituliu 为 True 时有效
             */
            yituliu_id?: string | null;
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
        /** RetryRequest */
        RetryRequest: {
            /** Priority */
            priority?: number | null;
        };
        /**
         * RoguelikeInput
         * @description 无限刷肉鸽（集成战略）。
         *
         *     主题／模式相关的跨字段规则全部实现为 ``model_validator`` 并抛 ``AppError``
         *     （``TASK_PARAM_INVALID``；``mode=2`` 是已弃用值，抛 ``TASK_PARAM_DEPRECATED``）
         *     ——「设了但不生效」必须在提交时就告诉用户，而不是静默忽略。
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
         */
        RoguelikeInput: {
            /**
             * Check Collapsal Paradigms
             * @description 是否检测获取的坍缩范式；仅适用于 Sami 主题。模式 5 下内核默认值为 True，其他模式下为 False；不传则由内核决定
             */
            check_collapsal_paradigms?: boolean | null;
            /**
             * Core Char
             * @description 开局干员名；仅支持单个干员中文名（无论区服）；若留空或设置为空字符串 "" 则根据练度自动选择
             */
            core_char?: string | null;
            /**
             * Difficulty
             * @description 指定难度等级；仅适用于除 Phantom 以外的主题，若未解锁难度则会选择当前已解锁的最高难度。不传则由内核使用默认值（0）
             */
            difficulty?: number | null;
            /**
             * Double Check Collapsal Paradigms
             * @description 是否执行坍缩范式检测防漏措施；仅在主题为 Sami 且 check_collapsal_paradigms 为 True 时有效。模式 5 下内核默认值为 True，其他模式下为 False；不传则由内核决定
             */
            double_check_collapsal_paradigms?: boolean | null;
            /**
             * Enable
             * @description 是否启用本任务；不传则由内核使用默认值（True）
             */
            enable?: boolean | null;
            /**
             * Expected Collapsal Paradigms
             * @description 希望触发的坍缩范式；仅在主题为 Sami 且模式为 5 时有效。内核默认值为 ["目空一些", "睁眼瞎", "图像损坏", "一抹黑"]
             */
            expected_collapsal_paradigms?: string[] | null;
            /**
             * First Floor Foldartal
             * @description 希望在第一层远见阶段得到的密文板；仅适用于 Sami 主题，不限模式；若成功凹到则停止任务
             */
            first_floor_foldartal?: string | null;
            /**
             * Investment Enabled
             * @description 是否投资源石锭；不传则由内核使用默认值（True）
             */
            investment_enabled?: boolean | null;
            /**
             * Investments Count
             * @description 投资源石锭的次数；达到后自动停止任务。不传则由内核使用默认值（INT_MAX，即不限制）
             */
            investments_count?: number | null;
            /**
             * Mode
             * @description 模式；0 为刷分/奖励点数（尽可能稳定地打更多层数），1 为刷源石锭（第一层投资完就退出），2 已弃用（兼顾模式 0 与 1，投资过后再退出，没有投资就继续往后打），3 开发中，4 为凹开局（先在 0 难度下到达第三层后重开，再到指定难度下凹开局奖励；Phantom 主题下不切换难度），5 为刷坍缩范式（仅适用于 Sami 主题）。不传则由内核使用默认值（0）
             */
            mode?: (0 | 1 | 2 | 3 | 4 | 5) | null;
            /**
             * @description 判别联合 discriminator，固定为 Roguelike；由任务类型选择器决定，用户不需要填写 (enum property replaced by openapi-typescript)
             * @enum {string}
             */
            name: "Roguelike";
            /**
             * Only Start With Elite Two
             * @description 是否只凹开局干员精二直升而忽视其他开局条件；仅在模式为 4 且 start_with_elite_two 为 True 时有效。不传则由内核使用默认值（False）
             */
            only_start_with_elite_two?: boolean | null;
            /**
             * Refresh Trader With Dice
             * @description 是否用骰子刷新商店购买特殊商品；仅适用于 Mizuki 主题，用于刷指路鳞。不传则由内核使用默认值（False）
             */
            refresh_trader_with_dice?: boolean | null;
            /**
             * Roles
             * @description 开局职业组；不传则由内核使用默认值（"取长补短"）
             */
            roles?: string | null;
            /**
             * Squad
             * @description 开局分队名；不传则由内核使用默认值（"指挥分队"）
             */
            squad?: string | null;
            /**
             * Start Foldartal List
             * @description 凹开局时希望在开局奖励阶段得到的密文板；仅主题为 Sami 且模式为 4 时有效。仅当开局拥有列表中所有的密文板时才算凹开局成功；此参数须与「生活至上分队」同时使用，其他分队在开局奖励阶段不会获得密文板
             */
            start_foldartal_list?: string[] | null;
            /**
             * Start With Elite Two
             * @description 是否在凹开局的同时凹干员精二直升；仅适用于模式 4。不传则由内核使用默认值（False）
             */
            start_with_elite_two?: boolean | null;
            /**
             * Starts Count
             * @description 开始探索的次数；达到后自动停止任务。不传则由内核使用默认值（INT_MAX，即不限制）
             */
            starts_count?: number | null;
            /**
             * Stop At Final Boss
             * @description 是否在第 5 层险路恶敌节点前停止任务；仅适用于除 Phantom 以外的主题。不传则由内核使用默认值（False）
             */
            stop_at_final_boss?: boolean | null;
            /**
             * Stop When Investment Full
             * @description 是否在投资到达上限后自动停止任务；不传则由内核使用默认值（False）
             */
            stop_when_investment_full?: boolean | null;
            /**
             * Theme
             * @description 主题；可选值包括 "Phantom", "Mizuki", "Sami", "Sarkaz"。不传则由内核使用默认值（Phantom）
             */
            theme?: ("Phantom" | "Mizuki" | "Sami" | "Sarkaz") | null;
            /**
             * Use Foldartal
             * @description 是否使用密文板；仅适用于 Sami 主题。模式 5 下内核默认值为 False，其他模式下为 True；不传则由内核决定
             */
            use_foldartal?: boolean | null;
            /**
             * Use Nonfriend Support
             * @description 是否可以是非好友助战干员；仅在 use_support 为 True 时有效。不传则由内核使用默认值（False）
             */
            use_nonfriend_support?: boolean | null;
            /**
             * Use Support
             * @description 开局干员是否为助战干员；不传则由内核使用默认值（False）
             */
            use_support?: boolean | null;
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
            template: (components["schemas"]["StartUpInput"] | components["schemas"]["CloseDownInput"] | components["schemas"]["FightInput"] | components["schemas"]["RecruitInput"] | components["schemas"]["InfrastInput"] | components["schemas"]["MallInput"] | components["schemas"]["AwardInput"] | components["schemas"]["RoguelikeInput"] | components["schemas"]["ReclamationInput"])[];
            /**
             * Timezone
             * @default Asia/Shanghai
             */
            timezone: string;
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
             * @description 切换账号；仅支持切换至已登录的账号，使用登录名进行查找，保证输入内容在所有已登录账号中唯一即可。官服示例 123****4567（可输入 123****4567、4567、123、3****4567）；B 服示例 张三（可输入 张三、张、三）
             * @example 123****4567
             * @example 4567
             * @example 张三
             */
            account_name?: string | null;
            /**
             * Client Type
             * @description 客户端版本；未指定时套用全局渠道默认值（默认 Bilibili）。可选值：Official / Bilibili / txwy / YoStarEN / YoStarJP / YoStarKR
             */
            client_type?: ("Official" | "Bilibili" | "txwy" | "YoStarEN" | "YoStarJP" | "YoStarKR") | null;
            /**
             * Enable
             * @description 是否启用本任务；不传则由内核使用默认值（True）
             */
            enable?: boolean | null;
            /**
             * @description 判别联合 discriminator，固定为 StartUp；由任务类型选择器决定，用户不需要填写 (enum property replaced by openapi-typescript)
             * @enum {string}
             */
            name: "StartUp";
            /**
             * Start Game Enabled
             * @description 是否自动启动客户端；不传则由内核使用默认值（True，即自动启动）
             */
            start_game_enabled?: boolean | null;
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
         *     有意不复用 :class:`~maa_api.domain.task.PipelineCreate`：校验端点不建流水线，
         *     ``title`` / ``priority`` / ``notify_on_finish`` 在这里没有意义，接受它们只会
         *     让调用方误以为「校验通过」等于「提交参数合法」。
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
            tasks: (components["schemas"]["StartUpInput"] | components["schemas"]["CloseDownInput"] | components["schemas"]["FightInput"] | components["schemas"]["RecruitInput"] | components["schemas"]["InfrastInput"] | components["schemas"]["MallInput"] | components["schemas"]["AwardInput"] | components["schemas"]["RoguelikeInput"] | components["schemas"]["ReclamationInput"])[];
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
            /** @description 与当前状态冲突：QUEUE_PAUSED */
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

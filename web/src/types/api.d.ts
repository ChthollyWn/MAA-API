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
    "/api/account/assets/{kind}/{asset_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 读取游戏资源图（干员头像 / 技能图标 / 道具图标）
         * @description 后端代理官方 CDN 的游戏美术资源，并按内容落盘缓存。
         *
         *     - `kind`：`avatar`（干员头像）、`skill`（技能图标）或 `item`（道具图标）
         *     - `asset_id`：`avatar` 是干员 id（`char_002_amiya`）、`skill` 是技能 id（`skchr_amiya_3`）、`item` 是**道具 id**（`3003`、`mod_unlock_token`）——道具的图标文件名由服务端从本机 `item_index.json` 解析，前端不必知道
         *     - 走服务端取图的原因：官方 CDN 有防盗链，浏览器带 `Referer` 会被 403
         *     - 响应可长期缓存（`Cache-Control: public, max-age=604800`），命中缓存时不打上游
         */
        get: operations["account_get_game_asset"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
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
         *     - `categories` 是这份快照里出现过的全部分类（按名字排序），不受两个筛选条件影响；页面用它渲染分类下拉
         *     - 只返回**有明确数量**的物品（内核 `Depot` 那一趟的结果，约 77 件）：M45 的自识别已于 2026-10-13 关停、2026-10-14 删除实现，它写下的 `count = NULL` 行读侧一律排除（`total` 与 `categories` 同口径）
         *     - `count` 仍是可空列：真出现 `null` 时页面显示「—」，不许当成 0
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
    "/api/account/operators": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 读取干员表
         * @description 账号干员列表，带筛选与分页。
         *
         *     - 数据来自森空岛练度同步（M47 起唯一来源），只读
         *     - 默认只返回已拥有的干员（`owned_only=true`）；未拥有的行也留在表里（那一轮同步的 `chars` 里没有它）
         *     - `total` 是筛选后的总数，不是当前页条数
         *     - 每行的 `field_sources` 标出各字段的来源（M47 之后基本只有 `skland`）
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
         * @description 按 `char_id` 读一条干员记录（外部 API 读取方与卡片详情用）。
         */
        get: operations["account_get_account_operator"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
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
         * @description 干员与仓库的计数、上次写入时间与是否过期。
         *
         *     - 干员侧的唯一来源是森空岛练度同步（M47 起），所以 `box.recognized_at` 是上次同步时间，阈值看 `agent.box_stale_days`（默认 14 天）
         *     - 仓库侧看 `agent.depot_stale_days`（默认 3 天）
         *     - 从未同步 / 从未识别时 `recognized_at` 为 `null` 且 `stale` 为 `true`
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
    "/api/account/upgrade-plans": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 列出养成计划
         * @description 养成计划列表，`items` 的形状与 Agent 工具 `list_upgrade_plans` 一致。
         *
         *     - 顺带重算进行中计划的进度：`active` 且材料已齐的计划会在这里变成 `completed`
         *     - `status` 只接受 `active` / `completed` / `cancelled`，缺省返回全部
         *     - `total` 是筛选后的条数
         */
        get: operations["account_list_account_upgrade_plans"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/account/upgrade-plans/{plan_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 读取养成计划
         * @description 按 `plan_id` 读一条养成计划，返回与列表 `items` 相同的形状。
         *
         *     - 计划不存在时 404 `UPGRADE_PLAN_NOT_FOUND`（与服务层同一个码）
         */
        get: operations["account_get_account_upgrade_plan"];
        put?: never;
        post?: never;
        /**
         * 取消养成计划
         * @description 取消一条养成计划，等价于 Agent 工具 `cancel_upgrade_plan`。
         *
         *     - 删除计划名下的每日定时任务；定时任务已经不在了也不报错
         *     - 计划状态置为 `cancelled`，返回取消后的计划
         *     - 计划不存在时 404 `UPGRADE_PLAN_NOT_FOUND`
         */
        delete: operations["account_cancel_account_upgrade_plan"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/artifacts": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 列出产物元数据
         * @description 按创建时间倒序分页；列表只返回元数据，不带正文。
         */
        get: operations["agent_list_artifacts"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/artifacts/{artifact_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 读取产物元数据与正文
         * @description 正文最多 64 KiB；超出时只返回开头并置 ``truncated=true``。
         */
        get: operations["agent_get_artifact"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/artifacts/{artifact_id}/content": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 下载产物原始字节
         * @description 原样返回字节；``Content-Type`` 取产物的 ``mime``。
         */
        get: operations["agent_download_artifact"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/artifacts/{artifact_id}/pin": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * 置顶或取消置顶产物
         * @description 置顶的 ``spill`` 不会被每天 04:00 的保留任务删除。
         */
        put: operations["agent_pin_artifact"];
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
    "/api/agent/brief/run": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * 立即生成每日简报
         * @description 建一份简报产物与 analyst 工单并返回两个 id（202，不受一天一张限制）。
         */
        post: operations["agent_run_brief"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/channels": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 读取 IM 通道状态
         * @description 四个平台的启用、配置与连接状态；不含密钥与绑定。
         */
        get: operations["agent_list_channels"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/channels/bindings": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 读取通道绑定
         * @description active 绑定列表；不含密钥。
         */
        get: operations["agent_list_channel_bindings"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/channels/bindings/{binding_id}": {
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
         * 解除通道绑定
         * @description 置为 revoked；不存在或已撤销回 404 ``CHANNEL_BINDING_NOT_FOUND``。
         */
        delete: operations["agent_revoke_channel_binding"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/channels/{platform}/bind-codes": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * 生成一次性绑定码
         * @description 写一条 10 分钟有效的绑定码；非法平台回 400 ``BIND_CODE_INVALID``。
         */
        post: operations["agent_create_bind_code"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/channels/{platform}/test": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * 发送通道测试消息
         * @description 同步发给该平台全部 active 绑定；未配置 503、发送失败 502。
         */
        post: operations["agent_test_channel"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/costs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 汇总模型调用成本
         * @description ``since <= created_at < until``；默认按服务器本地日期分组。
         */
        get: operations["agent_summarize_costs"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/device-lease": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 读取设备租约
         * @description 当前租约状态；没有租约时 ``lease`` 为 ``null``。
         */
        get: operations["agent_get_device_lease"];
        put?: never;
        post?: never;
        /**
         * 人工释放设备租约
         * @description 用户接管设备：删掉当前租约并给持有者留一次性抢占记号，没有租约时同样返回 204。
         */
        delete: operations["agent_release_device_lease"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/goals": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 分页读取目标
         * @description 按状态过滤分页，``updated_at`` 倒序；不传 ``status`` 时返回全部状态。
         */
        get: operations["agent_list_goals"];
        put?: never;
        /**
         * 新建目标
         * @description 建目标、规划工单与复盘 job；成功时带 ``Location`` 指向新目标。
         */
        post: operations["agent_create_goal"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/goals/{goal_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 读取目标详情
         * @description 目标本体、里程碑与最近 20 张相关工单；不存在报 404 ``GOAL_NOT_FOUND``。
         */
        get: operations["agent_get_goal"];
        /**
         * 修改目标
         * @description 改标题、截止或状态（暂停 / 恢复 / 取消）；已结束的目标报 409 ``GOAL_ENDED``。
         *
         *     恢复不做确认：REST 是用户本人在操作（规格 §4.3）。
         */
        put: operations["agent_update_goal"];
        post?: never;
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
    "/api/agent/memories": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 列出长期记忆 */
        get: operations["agent_list_memories"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/memories/{memory_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * 修改一条生效中的记忆
         * @description 改一条 ``active`` 记忆的 ``content``、``subject`` 或 ``expires_at``。
         *
         *     不存在的记忆返回 404 ``MEMORY_NOT_FOUND``；不是生效中或一个字段都没传时由
         *     服务层报 400 ``INVALID_PARAMETER``。``expires_at`` 传 ``null`` 表示清空。
         */
        put: operations["agent_update_memory"];
        post?: never;
        /**
         * 删除一条记忆
         * @description 物理删除一条记忆（03 §14 不自动删，用户在这里删）；不存在报 404。
         */
        delete: operations["agent_delete_memory"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/memories/{memory_id}/review": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * 审核一条待审记忆
         * @description 批准或拒绝一条 ``proposed`` 记忆。
         *
         *     ``approved=true`` 改为 ``active``，带 ``content`` 时顺手改写正文；``false``
         *     改为 ``rejected``。已经不在 ``proposed`` 返回 409 ``MEMORY_NOT_PROPOSED``。
         *     取代关系不由请求体传入：批准时若 ``supersedes_id`` 指向一条生效中的记忆，
         *     服务层把那条改为 ``superseded``。
         */
        post: operations["agent_review_memory"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/model-profiles": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 读取模型档案列表
         * @description ``default`` 在最前，其余按 ``name`` 升序。
         */
        get: operations["agent_list_model_profiles"];
        put?: never;
        /**
         * 新建模型档案
         * @description 成功时带 ``Location`` 指向新档案。
         */
        post: operations["agent_create_model_profile"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/model-profiles/{name}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 读取模型档案 */
        get: operations["agent_get_model_profile"];
        /**
         * 修改模型档案
         * @description 字段全部可选；``api_key="***"`` 表示不改，``""`` 表示清除。
         */
        put: operations["agent_update_model_profile"];
        post?: never;
        /**
         * 删除模型档案
         * @description 被角色引用时返回 409 ``MODEL_PROFILE_IN_USE``，``default`` 不可删除。
         */
        delete: operations["agent_delete_model_profile"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/model-profiles/{name}/probe": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * 探查模型档案的能力
         * @description 依次探查工具调用、视觉、JSON Schema 与尾部缓存位置，并写回档案。
         */
        post: operations["agent_probe_model_profile"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/model-roles": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 读取角色到模型档案的绑定
         * @description 九个角色各一行；``embedding`` 未设置时 ``profile`` 与 ``model`` 都是 null。
         */
        get: operations["agent_list_model_roles"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/nav/edges/{edge_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * 新建或修改导航跳转
         * @description 不存在时按 from_node/to_node/action 新建（id 取路径里的 UUID）；存在时只能改 enabled、action 与 cost_ms，改端点要删旧建新。
         */
        put: operations["agent_update_nav_edge"];
        post?: never;
        /**
         * 删除导航跳转
         * @description 物理删除一条跳转；种子跳转删掉后不会在重启时自动写回。
         */
        delete: operations["agent_delete_nav_edge"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/nav/graph": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 读取界面导航图
         * @description 返回全部界面与跳转、导航开关以及当前是否有可用的视觉模型。
         */
        get: operations["agent_get_nav_graph"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/nav/nodes/{node_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * 新建或修改导航界面
         * @description 新建界面时 origin 记为 user，修改已有界面不改 origin；可以同时替换特征 rois、标记当前画面为样本或按截图 id 加样本。
         */
        put: operations["agent_put_nav_node"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/observations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 分页读取观察记录
         * @description 按规则 / 是否已审阅过滤，``created_at`` 倒序分页（§4.3）。
         */
        get: operations["agent_list_observations"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/observations/{observation_id}/verdict": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * 审阅一条观察记录
         * @description 标记「判断对了 / 不该处理」；再次提交即改判（§4.3）。
         */
        post: operations["agent_review_observation"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/orchestrator": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 读取总控状态
         * @description 当前总控会话的状态；没有活动会话时先建一个（规格 §4.3.1）。
         */
        get: operations["agent_get_orchestrator"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/orchestrator/interrupt": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * 中断总控正在执行的一轮
         * @description 幂等：没有活动轮次时返回 200 且 ``interrupted=false``。
         */
        post: operations["agent_interrupt_orchestrator"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/orchestrator/messages": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 分页读取当前总控会话的消息 */
        get: operations["agent_list_orchestrator_messages"];
        put?: never;
        /**
         * 向总控发送消息并以 SSE 接收事件
         * @description 校验请求 → 订阅事件 → 写用户消息 → 返回这条消息的 SSE 流。
         */
        post: operations["agent_send_orchestrator_message"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/orchestrator/reset": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * 重新开始总控会话
         * @description 换一个新会话；运行中或还有未处理消息时 409。
         */
        post: operations["agent_reset_orchestrator"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/outbox": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 分页读取发件箱
         * @description ``created_at`` 倒序分页；``status`` / ``channel`` 都可选。
         */
        get: operations["agent_list_outbox"];
        put?: never;
        post?: never;
        delete?: never;
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
    "/api/agent/retrieval/search": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 检索知识与已建索引的资料
         * @description 按内容检索；向量服务失败只体现在 ``vector`` 上，不影响全文结果。
         */
        get: operations["agent_search_retrieval"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/retrieval/sources": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 读取检索来源与向量状态
         * @description 列出已注册来源的开关、状态与计数，以及全库补向量进度。
         */
        get: operations["agent_list_retrieval_sources"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/retrieval/sources/{source_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * 更新检索来源的开关与配置
         * @description 改 ``enabled`` 或整份 ``config``；``config`` 先交来源自己的校验。
         */
        put: operations["agent_update_retrieval_source"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/retrieval/sources/{source_id}/reindex": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * 重建检索来源的索引
         * @description 后台重建：先校验全文索引，再排一次强制同步；同步中返回 409。
         */
        post: operations["agent_reindex_retrieval_source"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/runs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 列出代理运行 */
        get: operations["agent_list_runs"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/runs/{run_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 读取运行与子运行树 */
        get: operations["agent_get_run"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/runs/{run_id}/spans": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 读取运行所在的整条追踪 */
        get: operations["agent_list_run_spans"];
        put?: never;
        post?: never;
        delete?: never;
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
    "/api/agent/sessions/{session_id}/checkpoints": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 列出会话的压缩检查点
         * @description 按 ``created_at`` 降序返回会话的全部检查点（含已被取代的）。
         */
        get: operations["agent_list_checkpoints"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/sessions/{session_id}/compact": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * 手动压缩会话的较早消息
         * @description 压缩一次会话；摘要不可用时 502 ``COMPACTION_FAILED``，不写检查点。
         */
        post: operations["agent_compact_session"];
        delete?: never;
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
    "/api/agent/sessions/{session_id}/plan": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 读 Worker 会话当前未结束的计划
         * @description 当前计划；没有未结束计划时返回 ``null``。
         */
        get: operations["agent_get_plan"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/sessions/{session_id}/plan/approval": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * 批准或退回待批准的计划
         * @description ``approved=true`` 转 ``active``，``false`` 退回 ``draft``；都写收件箱并唤醒。
         */
        post: operations["agent_approve_plan"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/sessions/{session_id}/rewind": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * 回到指定消息
         * @description 取代目标之后的消息、作废之后的检查点与计划；副作用逐条列出。
         */
        post: operations["agent_rewind_session"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/skills": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 读取技能列表 */
        get: operations["agent_list_skills"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/skills/{skill_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 读取技能详情 */
        get: operations["agent_get_skill"];
        /**
         * 写入技能
         * @description 写入完整的生效内容：新建自定义技能，或修改 / 停用内置技能。
         *
         *     内容不合法（含 id 不符合模式、未知工具或代理）返回 400 ``SKILL_INVALID``，
         *     ``details`` 带 ``field`` 与 ``reason``，未知工具与代理另带 ``unknown``。
         */
        put: operations["agent_put_skill"];
        post?: never;
        /**
         * 删除技能
         * @description 删除自定义技能，或撤销对内置技能的修改（恢复默认）。
         *
         *     内置技能没有覆盖可删时返回 409 ``SKILL_BUILTIN_READONLY``；要停用它请写入
         *     ``enabled=false``。
         */
        delete: operations["agent_delete_skill"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/specs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 读取代理定义列表 */
        get: operations["agent_list_specs"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/specs/{name}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 读取代理定义详情 */
        get: operations["agent_get_spec"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/tickets": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 分页读取工单 */
        get: operations["agent_list_tickets"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/tickets/{ticket_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 读取工单详情 */
        get: operations["agent_get_ticket"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/tickets/{ticket_id}/cancel": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * 取消工单
         * @description 停掉 Worker 与子代理（已提交的流水线不停），返回取消后的工单详情。
         */
        post: operations["agent_cancel_ticket"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/tickets/{ticket_id}/fork": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * 从这条消息分叉工单
         * @description 复制到 ``at_seq`` 为止的历史与同一份委托，新工单从 ``open`` 开始。
         */
        post: operations["agent_fork_ticket"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/tickets/{ticket_id}/interrupt": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * 中断工单正在执行的一轮
         * @description 幂等：没有活动轮次时返回 200 且 ``interrupted=false``。
         */
        post: operations["agent_interrupt_ticket"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/tickets/{ticket_id}/messages": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 分页读取工单的消息
         * @description Worker 会话的消息；``include_subagents`` 时加上子代理会话，按 id 升序。
         */
        get: operations["agent_list_ticket_messages"];
        put?: never;
        /**
         * 向工单发送消息并以 SSE 接收事件
         * @description 校验工单与 LLM 配置 → 订阅事件 → 写用户消息 → 返回这条消息的 SSE 流。
         */
        post: operations["agent_send_ticket_message"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/tickets/{ticket_id}/reports": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** 分页读取工单汇报 */
        get: operations["agent_list_ticket_reports"];
        put?: never;
        post?: never;
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
    "/api/agent/trigger-rules": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 读取事件规则列表
         * @description 全部规则（内置 + 覆盖 + 用户），附 30 天统计与升降档建议（§4.5.6）。
         */
        get: operations["agent_list_trigger_rules"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/trigger-rules/{rule_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * 写入事件规则
         * @description 写入完整的生效内容：覆盖内置规则，或新建 / 修改用户规则（§4.3）。
         */
        put: operations["agent_put_trigger_rule"];
        post?: never;
        /**
         * 删除事件规则
         * @description 删除用户规则；内置规则只删覆盖行（幂等），其余 404（§4.3）。
         */
        delete: operations["agent_delete_trigger_rule"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/triggers": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 分页读取事件记录
         * @description 按来源 / 种类 / 规则过滤，``id`` 倒序分页（§4.3）。
         */
        get: operations["agent_list_triggers"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/watchdog": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 读取值守概况
         * @description 模式取设置、上次巡检取服务属性、未解除数现查（§4.3）。
         */
        get: operations["agent_get_watchdog"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/watchdog/alerts": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 分页读取值守告警
         * @description 按解除状态与级别过滤，``created_at`` 倒序分页（§4.3）。
         */
        get: operations["agent_list_watchdog_alerts"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agent/watchdog/alerts/{alert_id}/resolve": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * 解除一条值守告警
         * @description 写入 ``resolved_at``；重复解除保留首次时间，不存在的 id 报 404（§4.3）。
         */
        post: operations["agent_resolve_watchdog_alert"];
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
    "/api/skland/binding": {
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
         * 解绑森空岛账号
         * @description 删凭据与角色行；**干员练度保留**（它是账号数据，不随绑定关系消失）。
         */
        delete: operations["skland_unbind_skland"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/skland/login/manual": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * 手动粘贴 cred 绑定（扫码的兜底）
         * @description 先用 ``binding`` 验一次凭据，再落库；返回与 ``GET /status`` 同形状。
         */
        post: operations["skland_bind_skland_manual"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/skland/login/qrcode": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * 生成森空岛扫码登录二维码
         * @description 生成一张二维码；用户用森空岛 App 扫它并在 App 里确认。
         */
        post: operations["skland_start_skland_qr_login"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/skland/login/qrcode/{session_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 轮询扫码结果
         * @description 轮询一次；``succeeded`` 时凭据已经落库，前端刷新 ``GET /api/skland/status`` 即可。
         */
        get: operations["skland_poll_skland_qr_login"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/skland/status": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * 森空岛绑定与同步状态
         * @description 没绑定也返回 200：``bound=false`` 是状态而不是错误。
         */
        get: operations["skland_get_skland_status"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/skland/sync": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * 同步森空岛练度（同步执行）
         * @description 跑一轮同步；返回摘要。冷却期内的重复提交回 429，已有同步在跑回 409。
         */
        post: operations["skland_sync_skland_training"];
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
         * AccountDepotList
         * @description 仓库响应（docs/05 §6.20：列表用 ``{items, total, limit, offset}``）。
         *
         *     ``items`` 只含**有明确数量**的行（``count IS NOT NULL``）：用户 2026-10-13 的口径是
         *     「仓库页只显示内核识别到的、有明确数量的物品」，历史上自识别写下的 ``NULL`` 行读侧
         *     排除（见 `docs/13` §8.68）。``categories`` 是这份快照里出现过的全部分类（按名字排序），
         *     与 ``category`` / ``query`` 无关，页面用它渲染分类下拉（M34），口径与 ``items`` 一致。
         */
        AccountDepotList: {
            /** Categories */
            categories?: string[];
            /** Items */
            items: components["schemas"]["AccountDepotRow"][];
            /** Limit */
            limit: number;
            /** Offset */
            offset: number;
            /** Total */
            total: number;
        };
        /**
         * AccountDepotRow
         * @description 仓库行：物品 id、名字、数量、分类与识别时间（规格 §7.5 的只读列表）。
         *
         *     ``count`` 允许 ``null`` 是**防御性**的：迁移 `0021` 起这一列可空，而写这一列的那条
         *     自识别链路已于 2026-10-13 关停、2026-10-14 连实现一起删除（见 `docs/13` §8.68），
         *     正常路径上不会有这种行 —— 列表读侧也已排除它们。真出现时页面显示「—」，
         *     **不许当成 0**（「仓库里没有这件道具」与「不知道有几件」是两件事）。
         */
        AccountDepotRow: {
            /** Category */
            category?: string | null;
            /** Count */
            count?: number | null;
            /** Item Id */
            item_id: string;
            /** Name */
            name?: string | null;
            /** Recognized At */
            recognized_at?: string | null;
        };
        /**
         * AccountModuleDetail
         * @description 模组明细（M43 §4.3）：卡片显示 ``CCR-X`` 与等级的依据。
         *
         *     ``type`` 是类型字母（`X` / `Y` / `D` / `A`），``branch`` 是分支码（`ccr` / `exe` …），
         *     ``label`` 是页面直接显示的标签（`CCR-X`），``type_icon`` 是上游 `typeIcon` 原值
         *     （`ccr-y`，以后要用第三方图标时按它拼 URL）。``equip_id`` 是模组 id。
         */
        AccountModuleDetail: {
            /** Branch */
            branch?: string | null;
            /** Equip Id */
            equip_id?: string | null;
            /** Label */
            label?: string | null;
            /** Level */
            level: number;
            /** Type */
            type: string;
            /** Type Icon */
            type_icon?: string | null;
        };
        /**
         * AccountOperatorList
         * @description 干员表响应（docs/05 §6.20：列表用 ``{items, total, limit, offset}``）。
         */
        AccountOperatorList: {
            /** Items */
            items: components["schemas"]["AccountOperatorRow"][];
            /** Limit */
            limit: number;
            /** Offset */
            offset: number;
            /** Total */
            total: number;
        };
        /**
         * AccountOperatorRow
         * @description 干员行：docs/05 §6.20 的字段清单，与单条读取同形。
         *
         *     M47 起这张表**只由森空岛练度同步写**（``field_sources`` 因此基本只有 ``skland``；
         *     ``recognition`` / ``manual`` / ``agent`` / ``training`` 只可能出现在 M47 之前的历史行上，
         *     而迁移 `0022` 已经把老行清掉了）。练度六列（``elite`` / ``level`` / ``potential`` /
         *     ``skill_level`` / ``masteries`` / ``modules``）可以是 ``null`` ＝ **未识别**：那次同步
         *     没给这一项，或上游给了越界值。别把 ``null`` 读成 0 级 / 技能 1 级 / 无专精。
         *     ``name``、``rarity``、``profession`` 在映射不到的干员上也可以是 ``null``。
         */
        AccountOperatorRow: {
            /** Char Id */
            char_id: string;
            /** Elite */
            elite?: number | null;
            /** Favor Percent */
            favor_percent?: number | null;
            /** Field Sources */
            field_sources?: {
                [key: string]: string;
            };
            /** Level */
            level?: number | null;
            /** Masteries */
            masteries?: {
                [key: string]: number;
            } | null;
            /** Module Details */
            module_details?: components["schemas"]["AccountModuleDetail"][] | null;
            /** Modules */
            modules?: {
                [key: string]: number;
            } | null;
            /** Name */
            name?: string | null;
            /** Own */
            own: boolean;
            /** Potential */
            potential?: number | null;
            /** Profession */
            profession?: string | null;
            /** Rarity */
            rarity?: number | null;
            /** Recognized At */
            recognized_at?: string | null;
            /** Skill Ids */
            skill_ids?: string[] | null;
            /** Skill Level */
            skill_level?: number | null;
            /** Updated At */
            updated_at?: string | null;
        };
        /**
         * AgentMessageCreate
         * @description 两条 SSE 端点共用的消息请求体。
         */
        AgentMessageCreate: {
            /** Content */
            content: string;
            /** Mentions */
            mentions?: components["schemas"]["Mention"][] | null;
        };
        /**
         * AgentMessageItem
         * @description v2 ``message_wire`` 的全部字段 + 运行 / 收件箱归属（M70、M72）。
         */
        AgentMessageItem: {
            /** Audit Id */
            audit_id: number | null;
            /** Completion Tokens */
            completion_tokens: number | null;
            /** Content */
            content: string | null;
            /** Created At */
            created_at: string;
            /** Finish Reason */
            finish_reason: string | null;
            /** Id */
            id: number;
            /** Inbox Id */
            inbox_id: number | null;
            inbox_kind?: components["schemas"]["InboxKind"] | null;
            /** Inbox Thread Key */
            inbox_thread_key?: string | null;
            /** Latency Ms */
            latency_ms: number | null;
            /** Prompt Tokens */
            prompt_tokens: number | null;
            /** Reasoning Content */
            reasoning_content: string | null;
            /** Role */
            role: string;
            /** Run Id */
            run_id: string | null;
            /** Seq */
            seq: number;
            /** Session Id */
            session_id: string;
            /** Superseded At */
            superseded_at?: string | null;
            /** Tool Call Id */
            tool_call_id: string | null;
            /** Tool Calls */
            tool_calls: {
                [key: string]: unknown;
            }[] | null;
            /** Tool Name */
            tool_name: string | null;
        };
        /**
         * AgentMessagePage
         * @description 消息分页信封；按 ``seq``（总控）或 ``id``（工单含子代理）升序。
         */
        AgentMessagePage: {
            /** Items */
            items: components["schemas"]["AgentMessageItem"][];
            /** Page */
            page: number;
            /** Size */
            size: number;
            /** Total */
            total: number;
        };
        /**
         * AgentRunKind
         * @enum {string}
         */
        AgentRunKind: "orchestrator_turn" | "worker_turn" | "subagent" | "trigger_eval" | "compaction" | "memory_extract" | "title" | "watchdog_check" | "eval_case" | "legacy_turn";
        /**
         * AgentRunStatus
         * @enum {string}
         */
        AgentRunStatus: "running" | "waiting_human" | "succeeded" | "failed" | "interrupted" | "cancelled";
        /**
         * AlertActionStatus
         * @description 告警动作的执行结果：只记录、已执行、失败、条件不满足跳过。
         * @enum {string}
         */
        AlertActionStatus: "recorded" | "done" | "failed" | "skipped";
        /**
         * AlertOut
         * @description 一条值守告警（§4.3）；``evidence`` 保留检查写入的判定证据。
         */
        AlertOut: {
            /** Action */
            action: string | null;
            action_status: components["schemas"]["AlertActionStatus"] | null;
            /** Check */
            check: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Evidence */
            evidence: {
                [key: string]: unknown;
            };
            /** Id */
            id: number;
            /** Message */
            message: string;
            /** Resolved At */
            resolved_at: string | null;
            severity: components["schemas"]["AlertSeverity"];
            /** Target Id */
            target_id: string | null;
            target_kind: components["schemas"]["AlertTargetKind"];
        };
        /**
         * AlertPage
         * @description 告警的分页信封（§4.3 / 04 §1）。
         */
        AlertPage: {
            /** Items */
            items: components["schemas"]["AlertOut"][];
            /** Page */
            page: number;
            /** Size */
            size: number;
            /** Total */
            total: number;
        };
        /**
         * AlertSeverity
         * @description 值守告警的级别：信息、警告、严重。
         * @enum {string}
         */
        AlertSeverity: "info" | "warn" | "critical";
        /**
         * AlertTargetKind
         * @description 告警目标种类：运行、工单、设备、内核、队列。
         * @enum {string}
         */
        AlertTargetKind: "run" | "ticket" | "device" | "core" | "queue";
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
         * ArtifactDetailOut
         * @description 产物详情：正文最多 64 KiB，超出时 ``truncated=true``。
         */
        ArtifactDetailOut: {
            /** Content */
            content: string;
            /** Created At */
            created_at: string;
            /** Id */
            id: string;
            kind: components["schemas"]["ArtifactKind"];
            /** Mime */
            mime: string;
            /** Pinned */
            pinned: boolean;
            /** Session Id */
            session_id: string | null;
            /** Size Bytes */
            size_bytes: number;
            storage: components["schemas"]["ArtifactStorage"];
            /** Ticket Id */
            ticket_id: string | null;
            /** Title */
            title: string;
            /** Truncated */
            truncated: boolean;
        };
        /**
         * ArtifactKind
         * @description 产物的种类；本里程碑只写 note / spill / report / file。
         * @enum {string}
         */
        ArtifactKind: "note" | "spill" | "report" | "chart" | "table" | "file" | "form" | "dashboard";
        /**
         * ArtifactOut
         * @description 产物元数据；列表不返回正文。
         */
        ArtifactOut: {
            /** Created At */
            created_at: string;
            /** Id */
            id: string;
            kind: components["schemas"]["ArtifactKind"];
            /** Mime */
            mime: string;
            /** Pinned */
            pinned: boolean;
            /** Session Id */
            session_id: string | null;
            /** Size Bytes */
            size_bytes: number;
            storage: components["schemas"]["ArtifactStorage"];
            /** Ticket Id */
            ticket_id: string | null;
            /** Title */
            title: string;
        };
        /**
         * ArtifactPage
         * @description 产物分页信封，与 04 §1 的统一分页形状一致。
         */
        ArtifactPage: {
            /** Items */
            items: components["schemas"]["ArtifactOut"][];
            /** Page */
            page: number;
            /** Size */
            size: number;
            /** Total */
            total: number;
        };
        /**
         * ArtifactPinRequest
         * @description 置顶或取消置顶（规格 §4.3）。
         */
        ArtifactPinRequest: {
            /** Pinned */
            pinned: boolean;
        };
        /**
         * ArtifactStorage
         * @description 产物的存储方式：小正文进库，大正文落 resource/artifacts/。
         * @enum {string}
         */
        ArtifactStorage: "db" | "file";
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
         * BindCodeOut
         * @description 一次性绑定码；``expires_at`` 是 UTC 时间。
         */
        BindCodeOut: {
            /** Code */
            code: string;
            /**
             * Expires At
             * Format: date-time
             */
            expires_at: string;
        };
        /**
         * BindingListOut
         * @description 绑定列表（不分页；数量由真实绑定数决定）。
         */
        BindingListOut: {
            /** Items */
            items: components["schemas"]["BindingOut"][];
        };
        /**
         * BindingOut
         * @description 一条通道绑定；外部身份与会话 id 是数据，不是凭据。
         */
        BindingOut: {
            /**
             * Bound At
             * Format: date-time
             */
            bound_at: string;
            /** Display Name */
            display_name?: string | null;
            /** External Chat Id */
            external_chat_id: string;
            /** External User Id */
            external_user_id: string;
            /** Id */
            id: string;
            /** Last Seen At */
            last_seen_at?: string | null;
            platform: components["schemas"]["ChannelPlatform"];
            /** Revoked At */
            revoked_at?: string | null;
            /** Status */
            status: string;
        };
        /**
         * BriefRunResult
         * @description ``POST /api/agent/brief/run`` 的响应体（§4.3、§4.5.9）。
         */
        BriefRunResult: {
            /** Artifact Id */
            artifact_id: string;
            /** Ticket Id */
            ticket_id: string;
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
        /**
         * ChannelOut
         * @description 一个平台的开关、配置与连接状态；不含密钥、不含绑定列表。
         */
        ChannelOut: {
            /** Configured */
            configured: boolean;
            /** Connected */
            connected?: boolean | null;
            /** Enabled */
            enabled: boolean;
            /** Last Error */
            last_error?: string | null;
            platform: components["schemas"]["ChannelPlatform"];
        };
        /**
         * ChannelPlatform
         * @description IM 平台；``channel_binding.platform`` 与绑定码上的平台用它。
         * @enum {string}
         */
        ChannelPlatform: "telegram" | "feishu" | "dingtalk" | "qq";
        /**
         * CheckpointList
         * @description 检查点列表；按 ``created_at`` 降序，最新的在最前。
         */
        CheckpointList: {
            /** Items */
            items: components["schemas"]["CheckpointView"][];
        };
        /**
         * CheckpointStatus
         * @description 检查点的状态：生效、被更新的检查点取代，或因会话重写而失效。
         * @enum {string}
         */
        CheckpointStatus: "active" | "superseded" | "invalidated";
        /**
         * CheckpointView
         * @description 一个压缩检查点的对外形状。
         *
         *     ``summary`` 是读历史时原样塞回第一条 user 消息的正文；``degraded`` 为真表示
         *     摘要模型不可用、内容由规则生成；``model`` 为空同样表示机械摘要。
         */
        CheckpointView: {
            /** Created At */
            created_at: string;
            /** Degraded */
            degraded: boolean;
            /** Id */
            id: string;
            /** Model */
            model: string | null;
            /** Run Id */
            run_id: string | null;
            /** Session Id */
            session_id: string;
            status: components["schemas"]["CheckpointStatus"];
            /** Summary */
            summary: string;
            /** Tokens After */
            tokens_after: number;
            /** Tokens Before */
            tokens_before: number;
            trigger: components["schemas"]["CompactionTrigger"];
            /** Upto Seq */
            upto_seq: number;
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
         * CompactResultView
         * @description 手动压缩的结果；``skipped`` 为真时没有新检查点，前后估算相同。
         */
        CompactResultView: {
            /** Checkpoint Id */
            checkpoint_id: string | null;
            /** Skipped */
            skipped: boolean;
            /** Tokens After */
            tokens_after: number;
            /** Tokens Before */
            tokens_before: number;
        };
        /**
         * CompactionTrigger
         * @description 检查点的触发方式：运行内自动压缩或用户手动压缩。
         * @enum {string}
         */
        CompactionTrigger: "auto" | "manual";
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
         * CostGroupView
         * @description 一种分组口径下的一行：key 是日期 / 代理名 / 模型名 / 角色名。
         */
        CostGroupView: {
            /**
             * Cached Tokens
             * @default 0
             */
            cached_tokens: number;
            /**
             * Calls
             * @default 0
             */
            calls: number;
            /**
             * Completion Tokens
             * @default 0
             */
            completion_tokens: number;
            /**
             * Cost Micros
             * @default 0
             */
            cost_micros: number;
            /** Key */
            key: string;
            /**
             * Prompt Tokens
             * @default 0
             */
            prompt_tokens: number;
        };
        /**
         * CostSummaryView
         * @description 成本汇总（``GET /api/agent/costs`` 的响应体）。
         */
        CostSummaryView: {
            /** Currency */
            currency: string;
            /** Groups */
            groups: components["schemas"]["CostGroupView"][];
            /** Total Micros */
            total_micros: number;
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
         * DeviceLeaseOut
         * @description 当前设备租约的对外形状；与 ``DeviceLeaseService.wire`` 一一对应。
         */
        DeviceLeaseOut: {
            /** Acquired At */
            acquired_at: string;
            /** Device Id */
            device_id: string;
            /** Expires At */
            expires_at: string;
            /** Explicit */
            explicit: boolean;
            /** Heartbeat At */
            heartbeat_at: string;
            /** Holder Agent */
            holder_agent: string;
            /** Lease Id */
            lease_id: string;
            /** Purpose */
            purpose: string;
            /** Run Id */
            run_id: string | null;
            /** Ticket Id */
            ticket_id: string | null;
            /** Ticket Title */
            ticket_title: string | null;
        };
        /**
         * DeviceLeaseState
         * @description ``GET`` 的响应：是否持有、租约详情与排队人数。
         */
        DeviceLeaseState: {
            /** Held */
            held: boolean;
            lease: components["schemas"]["DeviceLeaseOut"] | null;
            /** Waiters */
            waiters: number;
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
         * EmbeddingStatusView
         * @description 向量服务的状态与全库补向量进度。
         */
        EmbeddingStatusView: {
            /** Embedded Chunks */
            embedded_chunks: number;
            /** Error */
            error: string | null;
            /** Model */
            model: string | null;
            /**
             * Provider
             * @enum {string}
             */
            provider: "none" | "remote" | "local";
            /**
             * Status
             * @enum {string}
             */
            status: "disabled" | "ready" | "indexing" | "unavailable";
            /** Total Chunks */
            total_chunks: number;
        };
        /**
         * EventSource
         * @description 事件来源渠道。
         * @enum {string}
         */
        EventSource: "web" | "im_telegram" | "im_feishu" | "im_dingtalk" | "im_qq" | "pipeline" | "core" | "device" | "schedule" | "system" | "watchdog" | "goal";
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
        /**
         * ForkRequest
         * @description 分叉请求：在 ``at_seq`` 这条消息（含）处分叉出新工单。
         */
        ForkRequest: {
            /**
             * At Seq
             * @description 分叉处的消息 seq（含）
             */
            at_seq: number;
        };
        /**
         * ForkedFrom
         * @description 分叉来源：源工单与分叉处的消息 seq（由 Worker 会话的两列反查）。
         */
        ForkedFrom: {
            /** At Seq */
            at_seq: number;
            /** Ticket Id */
            ticket_id: string;
            /** Title */
            title: string;
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
        /**
         * GoalCreate
         * @description 新建目标的请求体（REST 与 ``create_goal`` 工具共用）。
         */
        GoalCreate: {
            /** Deadline */
            deadline?: string | null;
            /** Description */
            description?: string | null;
            /** Review Cron */
            review_cron?: string | null;
            target: components["schemas"]["GoalTarget"];
            /** Title */
            title: string;
        };
        /**
         * GoalDetail
         * @description 目标详情：本体 + 里程碑 + 最近 20 张目标工单。
         */
        GoalDetail: {
            /** Created At */
            created_at: string | null;
            /** Deadline */
            deadline?: string | null;
            /** Description */
            description?: string | null;
            /** Ended At */
            ended_at?: string | null;
            /** Id */
            id: string;
            /** Last Review At */
            last_review_at?: string | null;
            /** Milestones */
            milestones?: components["schemas"]["MilestoneOut"][];
            /** Next Review At */
            next_review_at?: string | null;
            /** Planning Ticket Id */
            planning_ticket_id?: string | null;
            /** Progress */
            progress?: {
                [key: string]: unknown;
            };
            /** Review Cron */
            review_cron: string;
            /** Schedule Ids */
            schedule_ids?: string[];
            /** Status */
            status: string;
            target: components["schemas"]["GoalTarget"];
            /** Tickets */
            tickets?: components["schemas"]["GoalTicketOut"][];
            /** Title */
            title: string;
            /** Updated At */
            updated_at: string | null;
            /** Upgrade Plan Id */
            upgrade_plan_id?: string | null;
        };
        /**
         * GoalPage
         * @description 目标分页响应。
         */
        GoalPage: {
            /** Items */
            items: components["schemas"]["GoalSummary"][];
            /** Page */
            page: number;
            /** Size */
            size: number;
            /** Total */
            total: number;
        };
        /**
         * GoalStatus
         * @description 长程目标的状态机：进行中、暂停、完成、失败、取消。
         * @enum {string}
         */
        GoalStatus: "active" | "paused" | "done" | "failed" | "cancelled";
        /**
         * GoalSummary
         * @description 目标列表的一行。
         */
        GoalSummary: {
            /** Deadline */
            deadline?: string | null;
            /** Id */
            id: string;
            /**
             * Milestones Done
             * @default 0
             */
            milestones_done: number;
            /**
             * Milestones Total
             * @default 0
             */
            milestones_total: number;
            /** Next Review At */
            next_review_at?: string | null;
            /** Status */
            status: string;
            /** Title */
            title: string;
            /** Updated At */
            updated_at: string | null;
        };
        /**
         * GoalTarget
         * @description 结构化目标：用户原话、可选练度目标与养成计划的运行时刻。
         */
        GoalTarget: {
            /** Operators */
            operators?: components["schemas"]["UpgradeTarget"][];
            /** Run Times */
            run_times?: string[];
            /** Text */
            text: string;
        };
        /**
         * GoalTicketOut
         * @description 目标详情里的一张相关工单（至多 20 张）。
         */
        GoalTicketOut: {
            /** Id */
            id: string;
            /** Kind */
            kind: string;
            /** Last Report Status */
            last_report_status?: string | null;
            /** Status */
            status: string;
            /** Title */
            title: string;
            /** Updated At */
            updated_at: string | null;
        };
        /**
         * GoalUpdate
         * @description 改目标：用 ``model_fields_set`` 区分「没传」与「传 null」。
         */
        GoalUpdate: {
            /** Deadline */
            deadline?: string | null;
            /** Status */
            status?: ("active" | "paused" | "cancelled") | null;
            /** Title */
            title?: string | null;
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
         * HumanActionOut
         * @description 待人工处理的动作；``type`` 决定前端弹哪一种卡片。
         */
        HumanActionOut: {
            /** Prompt */
            prompt: string;
            /** Ref Id */
            ref_id?: string | null;
            /**
             * Type
             * @enum {string}
             */
            type: "confirmation" | "question" | "decision";
        };
        /**
         * InboxKind
         * @description 总控收件箱条目的种类。
         * @enum {string}
         */
        InboxKind: "user_message" | "report" | "thread_event" | "system" | "trigger";
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
        /**
         * MandateOut
         * @description 工单的委托（建单时写入，此后不可变）。
         */
        MandateOut: {
            /** Created At */
            created_at: string;
            /** Id */
            id: string;
            /** Policy Version */
            policy_version: string | null;
            risk_ceiling: components["schemas"]["RiskLevel"];
            /** Rule Id */
            rule_id: string | null;
            /** Scope */
            scope: string;
            /** Sender */
            sender: string | null;
            source: components["schemas"]["EventSource"];
            strength: components["schemas"]["MandateStrength"];
        };
        /**
         * MandateStrength
         * @description 委托强度：显式授权或推断授权。
         * @enum {string}
         */
        MandateStrength: "explicit" | "inferred";
        /**
         * MemoryKind
         * @description 记忆的种类（07 §5.1）：稳定事实、一次经历、验证过的做法。
         * @enum {string}
         */
        MemoryKind: "semantic" | "episodic" | "procedural";
        /**
         * MemoryOut
         * @description 一条长期记忆的全部列（03 §10.2）；时间统一渲染成 ``...Z``。
         *
         *     没有 ``pinned`` 列，不要加（规格 §4.3）。
         */
        MemoryOut: {
            /** Confidence */
            confidence: number | null;
            /** Content */
            content: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string | null;
            /**
             * Expires At
             * Format: date-time
             */
            expires_at: string | null;
            /** Id */
            id: string;
            kind: components["schemas"]["MemoryKind"];
            /**
             * Last Used At
             * Format: date-time
             */
            last_used_at: string | null;
            /**
             * Reviewed At
             * Format: date-time
             */
            reviewed_at: string | null;
            /** Reviewed By */
            reviewed_by: string | null;
            /** Source Run Id */
            source_run_id: string | null;
            /** Source Session Id */
            source_session_id: string | null;
            /** Source Ticket Id */
            source_ticket_id: string | null;
            status: components["schemas"]["MemoryStatus"];
            /** Subject */
            subject: string | null;
            /** Supersedes Id */
            supersedes_id: string | null;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string | null;
            /** Use Count */
            use_count: number;
        };
        /**
         * MemoryPage
         * @description 记忆分页信封，与 04 §1 的统一分页形状一致。
         */
        MemoryPage: {
            /** Items */
            items: components["schemas"]["MemoryOut"][];
            /** Page */
            page: number;
            /** Size */
            size: number;
            /** Total */
            total: number;
        };
        /**
         * MemoryReviewRequest
         * @description ``POST .../review`` 的入参：批准可顺手改写正文，拒绝只改状态。
         */
        MemoryReviewRequest: {
            /** Approved */
            approved: boolean;
            /** Content */
            content?: string | null;
        };
        /**
         * MemoryStatus
         * @description 记忆的状态：待审核、生效、拒绝、被取代、过期。
         * @enum {string}
         */
        MemoryStatus: "proposed" | "active" | "rejected" | "superseded" | "expired";
        /**
         * MemoryUpdateRequest
         * @description ``PUT`` 的入参：三项都可选，但至少要改一项（服务层报参数错误）。
         *
         *     ``subject`` 与 ``expires_at`` 显式传 ``null`` 表示清空；路由用
         *     ``model_fields_set`` 区分「没传」与「传了 null」。
         */
        MemoryUpdateRequest: {
            /** Content */
            content?: string | null;
            /** Expires At */
            expires_at?: string | null;
            /** Subject */
            subject?: string | null;
        };
        /**
         * Mention
         * @description 一条 @ 引用；``type`` 决定前端去哪类对象里跳转。
         */
        Mention: {
            /** Id */
            id: string;
            /** Label */
            label: string;
            /**
             * Type
             * @enum {string}
             */
            type: "knowledge" | "operator" | "stage" | "pipeline" | "ticket";
        };
        /**
         * MilestoneOut
         * @description 一个里程碑的对外形状。
         */
        MilestoneOut: {
            /** Due At */
            due_at?: string | null;
            /** Id */
            id: string;
            /** Metrics */
            metrics?: {
                [key: string]: unknown;
            };
            /** Ord */
            ord: number;
            /** Status */
            status: string;
            /** Title */
            title: string;
        };
        /**
         * ModelProfileCreate
         * @description 新建档案；跨字段约束见 :meth:`_check_connection`。
         */
        ModelProfileCreate: {
            /** Api Key */
            api_key?: string | null;
            /** Base Url */
            base_url?: string | null;
            /** Context Window */
            context_window?: number | null;
            /**
             * Currency
             * @default CNY
             */
            currency: string;
            /**
             * Dynamic Context Position
             * @default tail_system
             * @enum {string}
             */
            dynamic_context_position: "tail_system" | "tail_user";
            /** Embedding Dim */
            embedding_dim?: number | null;
            /**
             * Enabled
             * @default true
             */
            enabled: boolean;
            /**
             * Kind
             * @default chat
             * @enum {string}
             */
            kind: "chat" | "embedding";
            /** Local Model Path */
            local_model_path?: string | null;
            /** Max Output Tokens */
            max_output_tokens?: number | null;
            /** Model */
            model: string;
            /** Name */
            name: string;
            /** Price Cached Input */
            price_cached_input?: number | null;
            /** Price Input */
            price_input?: number | null;
            /** Price Output */
            price_output?: number | null;
            /**
             * Provider
             * @default openai_compatible
             * @enum {string}
             */
            provider: "openai_compatible" | "local_onnx";
            /**
             * Supports Tools
             * @default true
             */
            supports_tools: boolean;
        };
        /**
         * ModelProfileList
         * @description ``GET /api/agent/model-profiles`` 的响应体。
         */
        ModelProfileList: {
            /** Items */
            items: components["schemas"]["ModelProfileView"][];
        };
        /**
         * ModelProfileUpdate
         * @description 修改档案；字段全部可选，实际改哪些字段看 ``model_fields_set``。
         */
        ModelProfileUpdate: {
            /** Api Key */
            api_key?: string | null;
            /** Base Url */
            base_url?: string | null;
            /** Context Window */
            context_window?: number | null;
            /** Currency */
            currency?: string | null;
            /** Dynamic Context Position */
            dynamic_context_position?: ("tail_system" | "tail_user") | null;
            /** Embedding Dim */
            embedding_dim?: number | null;
            /** Enabled */
            enabled?: boolean | null;
            /** Kind */
            kind?: ("chat" | "embedding") | null;
            /** Local Model Path */
            local_model_path?: string | null;
            /** Max Output Tokens */
            max_output_tokens?: number | null;
            /** Model */
            model?: string | null;
            /** Price Cached Input */
            price_cached_input?: number | null;
            /** Price Input */
            price_input?: number | null;
            /** Price Output */
            price_output?: number | null;
            /** Provider */
            provider?: ("openai_compatible" | "local_onnx") | null;
            /** Supports Tools */
            supports_tools?: boolean | null;
        };
        /**
         * ModelProfileView
         * @description 档案的对外视图：密钥只给 ``"***"``，``default`` 的 id 与时间戳为 null。
         */
        ModelProfileView: {
            /** Api Key */
            api_key: "***" | null;
            /** Base Url */
            base_url: string | null;
            /** Context Window */
            context_window: number | null;
            /** Created At */
            created_at: string | null;
            /** Currency */
            currency: string;
            /**
             * Dynamic Context Position
             * @enum {string}
             */
            dynamic_context_position: "tail_system" | "tail_user";
            /** Embedding Dim */
            embedding_dim: number | null;
            /** Enabled */
            enabled: boolean;
            /** Id */
            id: string | null;
            /**
             * Kind
             * @enum {string}
             */
            kind: "chat" | "embedding";
            /** Local Model Path */
            local_model_path: string | null;
            /** Max Output Tokens */
            max_output_tokens: number | null;
            /** Model */
            model: string;
            /** Name */
            name: string;
            /** Price Cached Input */
            price_cached_input: number | null;
            /** Price Input */
            price_input: number | null;
            /** Price Output */
            price_output: number | null;
            /**
             * Provider
             * @enum {string}
             */
            provider: "openai_compatible" | "local_onnx";
            /** Readonly */
            readonly: boolean;
            /** Supports Json Schema */
            supports_json_schema: boolean | null;
            /** Supports Tools */
            supports_tools: boolean;
            /** Supports Vision */
            supports_vision: boolean | null;
            /** Updated At */
            updated_at: string | null;
        };
        /**
         * ModelRoleItem
         * @description 一个角色当前生效的设置值、解析出的模型与来源。
         */
        ModelRoleItem: {
            /** Model */
            model: string | null;
            /** Profile */
            profile: string | null;
            /** Role */
            role: string;
            /**
             * Source
             * @enum {string}
             */
            source: "setting" | "default";
        };
        /**
         * ModelRolesView
         * @description ``GET /api/agent/model-roles`` 的响应体；``items`` 按 ``ModelRole`` 顺序。
         */
        ModelRolesView: {
            /** Items */
            items: components["schemas"]["ModelRoleItem"][];
        };
        /**
         * NavEdgeOut
         * @description 一条跳转；``action`` 是原始动作 JSON，``action_hash`` 是规范哈希。
         */
        NavEdgeOut: {
            /** Action */
            action: {
                [key: string]: unknown;
            };
            /** Action Hash */
            action_hash: string;
            /** Cost Ms */
            cost_ms: number | null;
            /** Created At */
            created_at: string;
            /** Enabled */
            enabled: boolean;
            /** Fail Count */
            fail_count: number;
            /** From Node */
            from_node: string;
            /** Id */
            id: string;
            /** Last Used At */
            last_used_at: string | null;
            /** Origin */
            origin: string;
            /** Success Count */
            success_count: number;
            /** To Node */
            to_node: string;
            /** Updated At */
            updated_at: string;
        };
        /**
         * NavEdgePut
         * @description ``PUT /api/agent/nav/edges/{edge_id}`` 的入参（§4.3.2）。
         *
         *     不存在时 ``from_node``/``to_node``/``action`` 必须给全；存在时只有
         *     ``enabled``/``action``/``cost_ms`` 生效，端点与现值不同是 400。
         */
        NavEdgePut: {
            /** Action */
            action?: {
                [key: string]: unknown;
            } | null;
            /** Cost Ms */
            cost_ms?: number | null;
            /** Enabled */
            enabled?: boolean | null;
            /** From Node */
            from_node?: string | null;
            /** To Node */
            to_node?: string | null;
        };
        /**
         * NavExemplarOut
         * @description 一个识别样本；``thumb_url`` 在截图已被清理或查不到时为 ``None``。
         */
        NavExemplarOut: {
            /** Added At */
            added_at: string | null;
            /** Phash */
            phash: string;
            /** Screenshot Id */
            screenshot_id: string | null;
            /** Source */
            source: string;
            /** Thumb Url */
            thumb_url: string | null;
        };
        /**
         * NavGraphOut
         * @description 整张导航图与三个运行时开关（``vision_available`` 现解析视觉档案）。
         */
        NavGraphOut: {
            /** Edges */
            edges: components["schemas"]["NavEdgeOut"][];
            /** Enabled */
            enabled: boolean;
            /** Learn */
            learn: boolean;
            /** Nodes */
            nodes: components["schemas"]["NavNodeOut"][];
            /** Vision Available */
            vision_available: boolean;
        };
        /**
         * NavNodeOut
         * @description 一个界面节点；时间统一渲染成 ``...Z``。
         */
        NavNodeOut: {
            /** Created At */
            created_at: string;
            /** Exemplars */
            exemplars: components["schemas"]["NavExemplarOut"][];
            /** Id */
            id: string;
            /** Kind */
            kind: string;
            /** Origin */
            origin: string;
            recognizer: components["schemas"]["NavRecognizerModel"];
            /** Title */
            title: string;
            /** Updated At */
            updated_at: string;
        };
        /**
         * NavNodePut
         * @description ``PUT /api/agent/nav/nodes/{node_id}`` 的入参（§4.3.2）。
         */
        NavNodePut: {
            /** Add Exemplar */
            add_exemplar?: "current" | null;
            /** Add Exemplar Screenshot Id */
            add_exemplar_screenshot_id?: string | null;
            /**
             * Kind
             * @enum {string}
             */
            kind: "screen" | "stage" | "menu" | "dialog";
            recognizer?: components["schemas"]["NavRecognizerModel"] | null;
            /** Remove Exemplars */
            remove_exemplars?: string[];
            /** Title */
            title: string;
        };
        /**
         * NavRecognizerModel
         * @description 节点的识别配置（§4.2）；请求里 ``extra="forbid"``。
         */
        NavRecognizerModel: {
            /**
             * Description
             * @default
             */
            description: string;
            /**
             * Generic
             * @default false
             */
            generic: boolean;
            /**
             * Max Distance
             * @default 10
             */
            max_distance: number;
            /** Rois */
            rois?: number[][];
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
         * ObservationOut
         * @description 观察记录的一条（§4.3）；``trigger_*`` 来自关联事件。
         */
        ObservationOut: {
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Decision */
            decision: {
                [key: string]: unknown;
            };
            /** Id */
            id: number;
            /** Reviewed At */
            reviewed_at: string | null;
            /** Rule Id */
            rule_id: string;
            /** Thread Key */
            thread_key: string;
            /** Trigger Id */
            trigger_id: number;
            /** Trigger Kind */
            trigger_kind: string;
            /** Trigger Summary */
            trigger_summary: string;
            /** Verdict */
            verdict: ("good" | "bad") | null;
        };
        /**
         * ObservationPage
         * @description 观察记录的分页信封（§4.3）。
         */
        ObservationPage: {
            /** Items */
            items: components["schemas"]["ObservationOut"][];
            /** Page */
            page: number;
            /** Size */
            size: number;
            /** Total */
            total: number;
        };
        /**
         * ObservationVerdictIn
         * @description ``POST .../verdict`` 的入参：只能标 good / bad，可改判。
         */
        ObservationVerdictIn: {
            /**
             * Verdict
             * @enum {string}
             */
            verdict: "good" | "bad";
        };
        /**
         * OrchestratorInterruptResult
         * @description 中断总控当前一轮的结果；没有活动轮次时 ``interrupted=false``。
         */
        OrchestratorInterruptResult: {
            /** Interrupted */
            interrupted: boolean;
            /** Session Id */
            session_id: string;
            /** Turn Running */
            turn_running: boolean;
        };
        /**
         * OrchestratorStateModel
         * @description 总控会话的对外状态（``GET /api/agent/orchestrator``）。
         */
        OrchestratorStateModel: {
            /** Archived Count */
            archived_count: number;
            /** Pending Inbox */
            pending_inbox: number;
            /** Session Id */
            session_id: string;
            /** Status */
            status: string;
            /** Turn Running */
            turn_running: boolean;
        };
        /**
         * OutboxChannel
         * @description 发件箱的出口：四个 IM 平台与 Web Push（docs/03 §11.7）。
         * @enum {string}
         */
        OutboxChannel: "im_telegram" | "im_feishu" | "im_dingtalk" | "im_qq" | "webpush";
        /**
         * OutboxKind
         * @description 发件箱消息的种类；决定去重键的前缀（spec §4.5 出站）。
         * @enum {string}
         */
        OutboxKind: "reply" | "report" | "confirmation" | "brief" | "alert";
        /**
         * OutboxOut
         * @description 发件箱的一行；``payload`` 原样返回，``receipt`` 为平台回执。
         */
        OutboxOut: {
            /** Attempts */
            attempts: number;
            channel: components["schemas"]["OutboxChannel"];
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Dedupe Key */
            dedupe_key: string;
            /** Id */
            id: number;
            kind: components["schemas"]["OutboxKind"];
            /** Last Error */
            last_error?: string | null;
            /**
             * Next Attempt At
             * Format: date-time
             */
            next_attempt_at: string;
            /** Payload */
            payload: {
                [key: string]: unknown;
            };
            /** Receipt */
            receipt?: {
                [key: string]: unknown;
            } | null;
            /** Ref Run Id */
            ref_run_id?: string | null;
            /** Ref Ticket Id */
            ref_ticket_id?: string | null;
            /** Sent At */
            sent_at?: string | null;
            status: components["schemas"]["OutboxStatus"];
            /** Target */
            target: string;
        };
        /**
         * OutboxPage
         * @description 发件箱分页信封（§4.3 / 04 §1）。
         */
        OutboxPage: {
            /** Items */
            items: components["schemas"]["OutboxOut"][];
            /** Page */
            page: number;
            /** Size */
            size: number;
            /** Total */
            total: number;
        };
        /**
         * OutboxStatus
         * @description 发件箱状态机：待发、发送中、已发、失败待重试、死信。
         * @enum {string}
         */
        OutboxStatus: "pending" | "sending" | "sent" | "failed" | "dead";
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
            tasks: (components["schemas"]["StartUpInput"] | components["schemas"]["CloseDownInput"] | components["schemas"]["FightInput"] | components["schemas"]["RecruitInput"] | components["schemas"]["InfrastInput"] | components["schemas"]["MallInput"] | components["schemas"]["AwardInput"] | components["schemas"]["RoguelikeInput"] | components["schemas"]["ReclamationInput"] | components["schemas"]["DepotInput"] | components["schemas"]["CustomInput"] | components["schemas"]["CopilotInput"])[];
            /** Title */
            title?: string | null;
        };
        /**
         * PlanApprovalRequest
         * @description 批准或退回一份待批准计划；退回可带原因。
         */
        PlanApprovalRequest: {
            /** Approved */
            approved: boolean;
            /** Reason */
            reason?: string | null;
        };
        /**
         * PlanItemOut
         * @description 计划里的一步；``id`` 是 8 位十六进制、同计划唯一。
         */
        PlanItemOut: {
            /** Id */
            id: string;
            /** Note */
            note?: string | null;
            status: components["schemas"]["PlanItemStatus"];
            /** Title */
            title: string;
        };
        /**
         * PlanItemStatus
         * @description 计划步骤的状态。
         * @enum {string}
         */
        PlanItemStatus: "pending" | "in_progress" | "done" | "cancelled";
        /**
         * PlanOut
         * @description ``GET .../plan`` 与批准端点的响应（spec §4.3）。
         */
        PlanOut: {
            /** Approved At */
            approved_at: string | null;
            /** Approved By */
            approved_by: string | null;
            /** Id */
            id: string;
            /** Items */
            items: components["schemas"]["PlanItemOut"][];
            /** Session Id */
            session_id: string;
            status: components["schemas"]["PlanStatus"];
            /** Ticket Id */
            ticket_id: string | null;
            /** Title */
            title: string;
        };
        /**
         * PlanStatus
         * @description 计划的状态机（v3 spec §4.5）。
         * @enum {string}
         */
        PlanStatus: "draft" | "awaiting_approval" | "active" | "done" | "abandoned";
        /** PriorityRequest */
        PriorityRequest: {
            /** Priority */
            priority: number;
        };
        /**
         * ProbeResultView
         * @description ``POST /api/agent/model-profiles/{name}/probe`` 的响应体。
         */
        ProbeResultView: {
            /** Latency Ms */
            latency_ms: number;
            /** Supports Json Schema */
            supports_json_schema: boolean | null;
            /** Supports Tools */
            supports_tools: boolean | null;
            /** Supports Vision */
            supports_vision: boolean | null;
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
        /**
         * ReindexAccepted
         * @description ``POST reindex`` 已排队；真正的重建在后台进行。
         */
        ReindexAccepted: {
            /** Source Id */
            source_id: string;
            /**
             * Status
             * @constant
             */
            status: "indexing";
        };
        /**
         * ReportStatus
         * @description Worker 汇报的状态。
         * @enum {string}
         */
        ReportStatus: "progress" | "waiting" | "ready" | "blocked" | "failed" | "dropped";
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
        /**
         * RetrievalHit
         * @description 一条检索结果：分块内容 + 出处 + 两路名次与融合分数。
         */
        RetrievalHit: {
            /** Chunk Id */
            chunk_id: number;
            /** Document Id */
            document_id: string;
            /** Fetched At */
            fetched_at: string | null;
            /** Fts Rank */
            fts_rank: number | null;
            /** Heading */
            heading: string | null;
            /** License */
            license: string | null;
            /** Score */
            score: number;
            /** Snippet */
            snippet: string;
            /** Source Id */
            source_id: string;
            /** Source Title */
            source_title: string;
            /** Text */
            text: string;
            /** Title */
            title: string;
            /** Trusted */
            trusted: boolean;
            /** Url */
            url: string | null;
            /** Vector Rank */
            vector_rank: number | null;
            /** Version */
            version: string | null;
        };
        /**
         * RetrievalResult
         * @description 一次检索的结果；``vector`` 说明向量路走到了哪一步。
         */
        RetrievalResult: {
            /** Enabled */
            enabled: boolean;
            /** Hits */
            hits: components["schemas"]["RetrievalHit"][];
            /** Query */
            query: string;
            /** Skipped Sources */
            skipped_sources: string[];
            /** Sources */
            sources: string[];
            /** Took Ms */
            took_ms: number;
            /** Top K */
            top_k: number;
            /**
             * Vector
             * @enum {string}
             */
            vector: "used" | "disabled" | "unavailable" | "empty";
            /** Vector Model */
            vector_model: string | null;
        };
        /**
         * RetrievalSourceList
         * @description 来源列表与全局向量状态。
         */
        RetrievalSourceList: {
            embedding: components["schemas"]["EmbeddingStatusView"];
            /** Items */
            items: components["schemas"]["RetrievalSourceView"][];
        };
        /**
         * RetrievalSourceUpdate
         * @description ``PUT`` 的入参：两个字段都可选，但至少要改一项。
         */
        RetrievalSourceUpdate: {
            /** Config */
            config?: {
                [key: string]: unknown;
            } | null;
            /** Enabled */
            enabled?: boolean | null;
        };
        /**
         * RetrievalSourceView
         * @description 一个检索来源的完整对外形状：元数据、开关、状态与三类计数。
         */
        RetrievalSourceView: {
            /** Chunk Count */
            chunk_count: number;
            /** Config */
            config: {
                [key: string]: unknown;
            };
            /** Doc Count */
            doc_count: number;
            /** Enabled */
            enabled: boolean;
            /** Error */
            error: string | null;
            /** Id */
            id: string;
            /** Kind */
            kind: string;
            /**
             * Last Indexed At
             * Format: date-time
             */
            last_indexed_at: string | null;
            /** License */
            license: string | null;
            /** Searchable By Default */
            searchable_by_default: boolean;
            /** Setting Enabled */
            setting_enabled: boolean | null;
            /**
             * Status
             * @enum {string}
             */
            status: "idle" | "indexing" | "error";
            /** Title */
            title: string;
            /** Trusted */
            trusted: boolean;
            /** Vector Count */
            vector_count: number;
        };
        /** RetryRequest */
        RetryRequest: {
            /** Priority */
            priority?: number | null;
        };
        /**
         * RewindRequest
         * @description 回溯请求：保留到 ``to_seq``（含）。
         */
        RewindRequest: {
            /**
             * Dry Run
             * @description 只计算影响，不写库
             * @default false
             */
            dry_run: boolean;
            /**
             * To Seq
             * @description 保留到这条消息（含）；-1 只用于总控会话
             */
            to_seq: number;
        };
        /**
         * RewindResultOut
         * @description 一次回溯的结果（``dry_run`` 时只报告不写库）。
         */
        RewindResultOut: {
            /** Checkpoint Reactivated */
            checkpoint_reactivated: string | null;
            /** Checkpoints Invalidated */
            checkpoints_invalidated: number;
            /** Dry Run */
            dry_run: boolean;
            /** Notice Message Id */
            notice_message_id: number | null;
            /** Plan Abandoned */
            plan_abandoned: boolean;
            /** Session Id */
            session_id: string;
            /** Side Effects */
            side_effects: components["schemas"]["RewindSideEffectOut"][];
            /** Superseded */
            superseded: number;
            /** Superseded Events */
            superseded_events: number;
            /** To Seq */
            to_seq: number;
        };
        /**
         * RewindSideEffectOut
         * @description 回溯范围内已经发生、不会被撤销的一次工具调用。
         */
        RewindSideEffectOut: {
            /** Audit Id */
            audit_id: number | null;
            /** Detail */
            detail: string | null;
            /** Seq */
            seq: number;
            /** Tool Name */
            tool_name: string;
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
        /**
         * RuleStats
         * @description 规则列表附带的 30 天统计与升降档建议（§4.5.6）。
         */
        RuleStats: {
            /**
             * Bad 30D
             * @default 0
             */
            bad_30d: number;
            /**
             * Good 30D
             * @default 0
             */
            good_30d: number;
            /**
             * Observations 30D
             * @default 0
             */
            observations_30d: number;
            /**
             * Reviewed 30D
             * @default 0
             */
            reviewed_30d: number;
            /**
             * Suggest Auto
             * @default false
             */
            suggest_auto: boolean;
            /**
             * Suggest Observe
             * @default false
             */
            suggest_observe: boolean;
            /**
             * Tickets 30D
             * @default 0
             */
            tickets_30d: number;
            /**
             * Tickets Bad 30D
             * @default 0
             */
            tickets_bad_30d: number;
        };
        /**
         * RunDetail
         * @description 运行详情与子运行树（深度不超过 3）。
         */
        RunDetail: {
            /** Agent Name */
            agent_name: string;
            /** Boot Id */
            boot_id: string;
            /** Cached Tokens */
            cached_tokens: number;
            /** Children */
            children?: components["schemas"]["RunDetail"][];
            /** Completion Tokens */
            completion_tokens: number;
            /** Cost Micros */
            cost_micros: number;
            /** Currency */
            currency: string | null;
            /** Error Code */
            error_code: string | null;
            /** Error Message */
            error_message: string | null;
            /** Finish Reason */
            finish_reason: string | null;
            /**
             * Finished At
             * Format: date-time
             */
            finished_at: string | null;
            /**
             * Heartbeat At
             * Format: date-time
             */
            heartbeat_at: string | null;
            kind: components["schemas"]["AgentRunKind"];
            /** Meta */
            meta: {
                [key: string]: unknown;
            } | null;
            /** Model */
            model: string | null;
            /** Model Profile */
            model_profile: string | null;
            /** Parent Run Id */
            parent_run_id: string | null;
            /** Prompt Tokens */
            prompt_tokens: number;
            /** Reasoning Tokens */
            reasoning_tokens: number;
            /** Root Run Id */
            root_run_id: string;
            /** Run Id */
            run_id: string;
            /** Session Id */
            session_id: string | null;
            /** Spec Version */
            spec_version: string | null;
            /**
             * Started At
             * Format: date-time
             */
            started_at: string | null;
            status: components["schemas"]["AgentRunStatus"];
            /** Steps */
            steps: number;
            /**
             * Ticket Id
             * @description 所属工单；工单功能上线前恒为空
             */
            ticket_id?: string | null;
            /** Trigger Seq */
            trigger_seq: number | null;
        };
        /** RunPage */
        RunPage: {
            /** Items */
            items: components["schemas"]["RunSummary"][];
            /** Page */
            page: number;
            /** Size */
            size: number;
            /** Total */
            total: number;
        };
        /**
         * RunSpansResponse
         * @description 运行所在的整条 trace：按 ``(started_at, id)`` 升序，最多 :data:`MAX_TRACE_SPANS`。
         */
        RunSpansResponse: {
            /** Items */
            items: components["schemas"]["SpanOut"][];
            /** Trace Id */
            trace_id: string;
            /** Truncated */
            truncated: boolean;
        };
        /**
         * RunSummary
         * @description 一次代理调用的摘要。
         */
        RunSummary: {
            /** Agent Name */
            agent_name: string;
            /** Cached Tokens */
            cached_tokens: number;
            /** Completion Tokens */
            completion_tokens: number;
            /** Cost Micros */
            cost_micros: number;
            /** Currency */
            currency: string | null;
            /** Error Code */
            error_code: string | null;
            /** Finish Reason */
            finish_reason: string | null;
            /**
             * Finished At
             * Format: date-time
             */
            finished_at: string | null;
            /**
             * Heartbeat At
             * Format: date-time
             */
            heartbeat_at: string | null;
            kind: components["schemas"]["AgentRunKind"];
            /** Model */
            model: string | null;
            /** Model Profile */
            model_profile: string | null;
            /** Parent Run Id */
            parent_run_id: string | null;
            /** Prompt Tokens */
            prompt_tokens: number;
            /** Reasoning Tokens */
            reasoning_tokens: number;
            /** Root Run Id */
            root_run_id: string;
            /** Run Id */
            run_id: string;
            /** Session Id */
            session_id: string | null;
            /** Spec Version */
            spec_version: string | null;
            /**
             * Started At
             * Format: date-time
             */
            started_at: string | null;
            status: components["schemas"]["AgentRunStatus"];
            /** Steps */
            steps: number;
            /**
             * Ticket Id
             * @description 所属工单；工单功能上线前恒为空
             */
            ticket_id?: string | null;
            /** Trigger Seq */
            trigger_seq: number | null;
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
            template: (components["schemas"]["StartUpInput"] | components["schemas"]["CloseDownInput"] | components["schemas"]["FightInput"] | components["schemas"]["RecruitInput"] | components["schemas"]["InfrastInput"] | components["schemas"]["MallInput"] | components["schemas"]["AwardInput"] | components["schemas"]["RoguelikeInput"] | components["schemas"]["ReclamationInput"] | components["schemas"]["DepotInput"] | components["schemas"]["CustomInput"] | components["schemas"]["CopilotInput"])[];
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
         * SkillBuiltinView
         * @description 内置技能的原文快照，供界面对比与恢复。
         */
        SkillBuiltinView: {
            /** Agents */
            agents: string[];
            /** Body */
            body: string;
            /** Description */
            description: string;
            /** Id */
            id: string;
            /** Title */
            title: string;
            /** Tools */
            tools: string[];
            /** Version */
            version: number;
        };
        /**
         * SkillDetail
         * @description 详情：生效正文 + 内置原文快照（自定义技能为 ``None``）。
         */
        SkillDetail: {
            /** Agents */
            agents: string[];
            /** Body */
            body: string;
            /** Body Chars */
            body_chars: number;
            builtin: components["schemas"]["SkillBuiltinView"] | null;
            /** Description */
            description: string;
            /** Enabled */
            enabled: boolean;
            /** Files */
            files: string[];
            /** Id */
            id: string;
            /** Modified */
            modified: boolean;
            /**
             * Source
             * @enum {string}
             */
            source: "builtin" | "user" | "generated";
            /**
             * Status
             * @enum {string}
             */
            status: "draft" | "active";
            /** Title */
            title: string;
            /** Tools */
            tools: string[];
            /** Updated At */
            updated_at: string | null;
            /** Version */
            version: number | null;
        };
        /**
         * SkillList
         * @description 列表响应：列表项不含正文，只给字数与来源。
         */
        SkillList: {
            /** Items */
            items: components["schemas"]["SkillSummary"][];
        };
        /**
         * SkillSummary
         * @description 列表项：不含正文，只给字数与来源（规格 §4.2 的 REST 列表）。
         */
        SkillSummary: {
            /** Agents */
            agents: string[];
            /** Body Chars */
            body_chars: number;
            /** Description */
            description: string;
            /** Enabled */
            enabled: boolean;
            /** Files */
            files: string[];
            /** Id */
            id: string;
            /** Modified */
            modified: boolean;
            /**
             * Source
             * @enum {string}
             */
            source: "builtin" | "user" | "generated";
            /**
             * Status
             * @enum {string}
             */
            status: "draft" | "active";
            /** Title */
            title: string;
            /** Tools */
            tools: string[];
            /** Updated At */
            updated_at: string | null;
            /** Version */
            version: number | null;
        };
        /**
         * SkillWriteRequest
         * @description ``PUT`` 的入参：完整的生效内容。
         *
         *     继承服务层的 :class:`~maa_api.agent.skills.registry.SkillWrite`（字段名一致），
         *     这里只补 REST 层的 ``extra="forbid"``；长度与工具 / 代理的校验在服务层，
         *     失败统一报 400 ``SKILL_INVALID``。
         */
        SkillWriteRequest: {
            /**
             * Agents
             * @default []
             */
            agents: string[];
            /** Body */
            body: string;
            /** Description */
            description: string;
            /**
             * Enabled
             * @default true
             */
            enabled: boolean;
            /** Title */
            title: string;
            /**
             * Tools
             * @default []
             */
            tools: string[];
        };
        /**
         * SklandBindResult
         * @description 解绑结果。
         */
        SklandBindResult: {
            /**
             * Bound
             * @default false
             */
            bound: boolean;
        };
        /**
         * SklandManualBindRequest
         * @description 手动兜底绑定：扫码走不通时粘贴鹰角通行证算出来的 ``cred`` 与签名密钥。
         */
        SklandManualBindRequest: {
            /**
             * Cred
             * @description 森空岛 cred
             */
            cred: string;
            /**
             * Token
             * @description 签名密钥
             */
            token: string;
        };
        /**
         * SklandQrPollResult
         * @description 轮询结果；``state`` 只有四态，上游没有「已扫码待确认」的中间态。
         */
        SklandQrPollResult: {
            /** @description 成功时的默认角色 */
            account?: components["schemas"]["SklandRoleRow"] | null;
            /**
             * Message
             * @description 失败或过期时的说明
             */
            message?: string | null;
            /**
             * Roles
             * @description 成功时的全部角色
             */
            roles?: components["schemas"]["SklandRoleRow"][] | null;
            /**
             * State
             * @description waiting 等待扫码 / succeeded / expired / failed
             */
            state: string;
        };
        /**
         * SklandQrStartResult
         * @description 扫码会话：把 ``qr_svg`` 直接塞进 ``<img src="data:image/svg+xml;…">`` 即可。
         */
        SklandQrStartResult: {
            /**
             * Expires At
             * @description 会话失效时间（UTC，ISO8601）
             */
            expires_at: string;
            /**
             * Payload
             * @description 二维码里的登录串 hypergryph://scan_login?scanId=…
             */
            payload: string;
            /**
             * Poll Interval Ms
             * @description 建议轮询间隔（毫秒）
             */
            poll_interval_ms: number;
            /**
             * Qr Svg
             * @description 完整 SVG 文档（后端零依赖生成）
             */
            qr_svg: string;
            /** Session Id */
            session_id: string;
        };
        /**
         * SklandRoleRow
         * @description 一个游戏角色（``bindingList`` 的一条）。
         */
        SklandRoleRow: {
            /**
             * App Code
             * @description 归属应用，目前只有 `arknights`
             */
            app_code: string;
            /**
             * Channel Master Id
             * @description 渠道主 id，上游不给就是 null
             */
            channel_master_id?: string | null;
            /**
             * Is Default
             * @description 上游的 isDefault：多角色时同步取它
             */
            is_default: boolean;
            /**
             * Level
             * @description 角色等级
             */
            level?: number | null;
            /**
             * Nickname
             * @description 角色昵称
             */
            nickname?: string | null;
            /**
             * Synced At
             * @description 最近一次成功同步这个角色的时间
             */
            synced_at?: string | null;
            /**
             * Uid
             * @description 游戏角色 uid，`player/info` 用它取数
             */
            uid: string;
        };
        /**
         * SklandStatusResult
         * @description 绑定与同步状态。
         */
        SklandStatusResult: {
            account?: components["schemas"]["SklandRoleRow"] | null;
            /** Auto Sync Enabled */
            auto_sync_enabled: boolean;
            /** Auto Sync Interval Minutes */
            auto_sync_interval_minutes: number;
            /**
             * Bound
             * @description 库里有没有凭据
             */
            bound: boolean;
            /**
             * Key Configured
             * @description skland.credential_key 是否已配置且合法
             */
            key_configured: boolean;
            /** Last Error */
            last_error?: string | null;
            last_sync?: components["schemas"]["SklandSyncResult"] | null;
            /** Last Sync At */
            last_sync_at?: string | null;
            /** Min Chars */
            min_chars: number;
            /** Min Sync Interval Seconds */
            min_sync_interval_seconds: number;
            /** Roles */
            roles?: components["schemas"]["SklandRoleRow"][];
        };
        /**
         * SklandSyncResult
         * @description 一轮同步的摘要；``status`` 目前恒为 ``succeeded``（失败走错误码）。
         */
        SklandSyncResult: {
            /**
             * Created
             * @description 新建的行数
             */
            created: number;
            /**
             * Dropped Invalid
             * @description 越界或映射不出、被丢掉的字段项数
             */
            dropped_invalid: number;
            /** Duration Ms */
            duration_ms: number;
            /**
             * Ownership Cleared
             * @description 被收缩成未拥有的行数
             */
            ownership_cleared: number;
            role: components["schemas"]["SklandRoleRow"];
            /** Status */
            status: string;
            /** Synced At */
            synced_at: string;
            /**
             * Total
             * @description 这一轮拿到的干员数
             */
            total: number;
            /**
             * Unchanged
             * @description 值没变、只刷新同步时间的行数
             */
            unchanged: number;
            /**
             * Updated
             * @description 有字段变化的行数
             */
            updated: number;
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
         * SpanOut
         * @description 一个 span 的对外形状（M68 规格 §4.3）；时间与 ``RunSummary`` 一样带 ``Z``。
         */
        SpanOut: {
            /** Attributes */
            attributes: {
                [key: string]: unknown;
            };
            /** Duration Ms */
            duration_ms: number | null;
            /** Ended At */
            ended_at: string | null;
            /** Kind */
            kind: string;
            /** Name */
            name: string;
            /** Parent Span Id */
            parent_span_id: string | null;
            /** Run Id */
            run_id: string | null;
            /** Span Id */
            span_id: string;
            /** Started At */
            started_at: string;
            /** Status */
            status: string;
        };
        /**
         * SpecBudget
         * @description 单次运行的预算上限。
         */
        SpecBudget: {
            /** Max Cost Per Run */
            max_cost_per_run: number;
        };
        /**
         * SpecDetail
         * @description 详情：列表字段之外再加定义文件里其余 8 个字段与正文提示词。
         */
        SpecDetail: {
            /** Body */
            body: string;
            budget: components["schemas"]["SpecBudget"];
            /** Deferred Groups */
            deferred_groups: string[];
            /** Delegates */
            delegates: string[];
            /** Description */
            description: string;
            /**
             * Device
             * @enum {string}
             */
            device: "none" | "read" | "exclusive";
            /**
             * Kind
             * @enum {string}
             */
            kind: "orchestrator" | "worker" | "subagent" | "background";
            /** Max Steps */
            max_steps: number;
            /**
             * Model Role
             * @enum {string}
             */
            model_role: "orchestrator" | "worker" | "subagent" | "summarizer" | "title" | "trigger_eval" | "memory" | "vision" | "embedding";
            /** Name */
            name: string;
            /** Output Schema */
            output_schema: string | null;
            /**
             * Risk Ceiling
             * @enum {string}
             */
            risk_ceiling: "none" | "consume" | "destructive";
            /** Skills */
            skills: string[];
            /** Spec Version */
            spec_version: string;
            /** Title */
            title: string;
            /** Tools */
            tools: string[];
            /** Version */
            version: number;
        };
        /**
         * SpecList
         * @description 列表响应：``items`` 按 ``name`` 排序。
         */
        SpecList: {
            /** Items */
            items: components["schemas"]["SpecSummary"][];
        };
        /**
         * SpecSummary
         * @description 列表项：一个代理定义的身份、种类、工具与版本。
         */
        SpecSummary: {
            /** Delegates */
            delegates: string[];
            /** Description */
            description: string;
            /**
             * Kind
             * @enum {string}
             */
            kind: "orchestrator" | "worker" | "subagent" | "background";
            /**
             * Model Role
             * @enum {string}
             */
            model_role: "orchestrator" | "worker" | "subagent" | "summarizer" | "title" | "trigger_eval" | "memory" | "vision" | "embedding";
            /** Name */
            name: string;
            /** Spec Version */
            spec_version: string;
            /** Title */
            title: string;
            /** Tools */
            tools: string[];
            /** Version */
            version: number;
        };
        /**
         * StartUpInput
         * @description 开始唤醒：启动客户端并进入游戏。
         * @example {
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
        /**
         * SubscriptionOut
         * @description 工单对某个话题键的订阅；停用后保留行。
         */
        SubscriptionOut: {
            /** Active */
            active: boolean;
            /** Created At */
            created_at: string;
            /** Ended At */
            ended_at: string | null;
            /** Thread Key */
            thread_key: string;
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
            tasks: (components["schemas"]["StartUpInput"] | components["schemas"]["CloseDownInput"] | components["schemas"]["FightInput"] | components["schemas"]["RecruitInput"] | components["schemas"]["InfrastInput"] | components["schemas"]["MallInput"] | components["schemas"]["AwardInput"] | components["schemas"]["RoguelikeInput"] | components["schemas"]["ReclamationInput"] | components["schemas"]["DepotInput"] | components["schemas"]["CustomInput"] | components["schemas"]["CopilotInput"])[];
        };
        /**
         * TestChannelOut
         * @description 测试消息同步发送的结果：发给了几条 active 绑定。
         */
        TestChannelOut: {
            /** Sent */
            sent: number;
        };
        /**
         * TicketCancelRequest
         * @description 取消工单的请求体；不写原因时服务层用「用户取消」。
         */
        TicketCancelRequest: {
            /** Reason */
            reason?: string | null;
        };
        /**
         * TicketDetail
         * @description 工单详情：摘要、结果、委托、订阅、最近 5 条汇报与 Worker 是否在跑。
         */
        TicketDetail: {
            forked_from?: components["schemas"]["ForkedFrom"] | null;
            lease: components["schemas"]["DeviceLeaseOut"] | null;
            mandate: components["schemas"]["MandateOut"];
            /** Recent Reports */
            recent_reports: components["schemas"]["TicketReportOut"][];
            /** Result */
            result: {
                [key: string]: unknown;
            } | null;
            /** Subscriptions */
            subscriptions: components["schemas"]["SubscriptionOut"][];
            ticket: components["schemas"]["TicketSummary"];
            /** Turn Running */
            turn_running: boolean;
            /** Worker Session Id */
            worker_session_id: string | null;
        };
        /**
         * TicketInterruptResult
         * @description 中断工单当前一轮的结果；幂等。
         */
        TicketInterruptResult: {
            /** Interrupted */
            interrupted: boolean;
            /** Ticket Id */
            ticket_id: string;
            /** Turn Running */
            turn_running: boolean;
        };
        /**
         * TicketOrigin
         * @description 工单的来源：用户、触发器、目标、看门狗或系统。
         * @enum {string}
         */
        TicketOrigin: "user" | "trigger" | "goal" | "watchdog" | "system";
        /**
         * TicketPage
         * @description 工单列表的分页信封。
         */
        TicketPage: {
            /** Items */
            items: components["schemas"]["TicketSummary"][];
            /** Page */
            page: number;
            /** Size */
            size: number;
            /** Total */
            total: number;
        };
        /**
         * TicketReportOut
         * @description 一次 Worker 汇报的对外形状。
         */
        TicketReportOut: {
            /** Created At */
            created_at: string;
            /** Details */
            details: {
                [key: string]: unknown;
            } | null;
            /** Forwarded */
            forwarded: boolean;
            human_action: components["schemas"]["HumanActionOut"] | null;
            /** Id */
            id: number;
            /** Run Id */
            run_id: string | null;
            status: components["schemas"]["ReportStatus"];
            /** Summary */
            summary: string;
            /** Ticket Id */
            ticket_id: string;
        };
        /**
         * TicketReportPage
         * @description 工单汇报的分页信封。
         */
        TicketReportPage: {
            /** Items */
            items: components["schemas"]["TicketReportOut"][];
            /** Page */
            page: number;
            /** Size */
            size: number;
            /** Total */
            total: number;
        };
        /**
         * TicketStatus
         * @description 工单状态机（M69 规格 §4.5）。
         * @enum {string}
         */
        TicketStatus: "open" | "waiting" | "blocked" | "done" | "failed" | "cancelled";
        /**
         * TicketSummary
         * @description 工单列表项与详情里的工单摘要。
         */
        TicketSummary: {
            /** Created At */
            created_at: string;
            /** Ended At */
            ended_at: string | null;
            human_action: components["schemas"]["HumanActionOut"] | null;
            /** Id */
            id: string;
            /** Kind */
            kind: string;
            last_report_status: components["schemas"]["ReportStatus"] | null;
            origin: components["schemas"]["TicketOrigin"];
            /** Priority */
            priority: number;
            status: components["schemas"]["TicketStatus"];
            /** Summary */
            summary: string | null;
            /** Title */
            title: string;
            /** Updated At */
            updated_at: string;
        };
        /**
         * TriggerAdmission
         * @description 事件进入系统后的准入结论：接受、忽略、不合法。
         * @enum {string}
         */
        TriggerAdmission: "accepted" | "ignored" | "rejected";
        /**
         * TriggerEvalStatus
         * @description 事件研判的状态；pending 表示还没研判完，其余是终值。
         * @enum {string}
         */
        TriggerEvalStatus: "pending" | "accept" | "stop" | "ignore" | "unavailable" | "skipped";
        /**
         * TriggerMode
         * @description 事件规则的档位：关闭、只记录本来会做什么、直接建工单。
         * @enum {string}
         */
        TriggerMode: "off" | "observe" | "auto";
        /**
         * TriggerOut
         * @description 事件记录的一条（§4.3）。
         */
        TriggerOut: {
            admission: components["schemas"]["TriggerAdmission"];
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Dedupe Key */
            dedupe_key: string | null;
            /** Eval Reason */
            eval_reason: string | null;
            /** Eval Run Id */
            eval_run_id: string | null;
            eval_status: components["schemas"]["TriggerEvalStatus"];
            /** Id */
            id: number;
            /** Kind */
            kind: string;
            /** Payload */
            payload: {
                [key: string]: unknown;
            };
            /** Processed At */
            processed_at: string | null;
            routed_to: components["schemas"]["TriggerRoute"] | null;
            /** Rule Id */
            rule_id: string | null;
            /** Sender */
            sender: string | null;
            /** Source */
            source: string;
            /** Summary */
            summary: string;
            /** Thread Key */
            thread_key: string;
            /** Ticket Id */
            ticket_id: string | null;
        };
        /**
         * TriggerPage
         * @description 事件记录的分页信封（§4.3）。
         */
        TriggerPage: {
            /** Items */
            items: components["schemas"]["TriggerOut"][];
            /** Page */
            page: number;
            /** Size */
            size: number;
            /** Total */
            total: number;
        };
        /**
         * TriggerRoute
         * @description 事件处理后的去向：交总控、建工单、写观察记录、只记录。
         * @enum {string}
         */
        TriggerRoute: "orchestrator" | "ticket" | "observation" | "none";
        /**
         * TriggerRuleList
         * @description 规则列表：全部生效规则 + 规则集版本哈希（§4.3）。
         */
        TriggerRuleList: {
            /** Items */
            items: components["schemas"]["TriggerRuleOut"][];
            /** Version */
            version: string;
        };
        /**
         * TriggerRuleOut
         * @description 规则列表 / 写入返回的一条生效规则（§4.3）。
         */
        TriggerRuleOut: {
            /** Conditions */
            conditions: {
                [key: string]: unknown;
            };
            /** Cooldown Seconds */
            cooldown_seconds: number;
            /** Cron */
            cron: string | null;
            effective_mode: components["schemas"]["TriggerMode"];
            /** Enabled */
            enabled: boolean;
            /** Gated By */
            gated_by: string | null;
            /** Id */
            id: string;
            /** Kind Pattern */
            kind_pattern: string;
            mode: components["schemas"]["TriggerMode"];
            /** Modified */
            modified: boolean;
            /** Next Fire At */
            next_fire_at: string | null;
            /**
             * Origin
             * @enum {string}
             */
            origin: "builtin" | "override" | "user";
            /** Policy */
            policy: string;
            /** Source */
            source: string | null;
            stats: components["schemas"]["RuleStats"];
            /** Title */
            title: string;
            /** Worker */
            worker: string | null;
        };
        /**
         * TriggerRuleWrite
         * @description ``PUT /api/agent/trigger-rules/{rule_id}`` 的入参：写完整的生效内容。
         */
        TriggerRuleWrite: {
            /** Conditions */
            conditions?: {
                [key: string]: unknown;
            };
            /**
             * Cooldown Seconds
             * @default 0
             */
            cooldown_seconds: number;
            /** Cron */
            cron?: string | null;
            /**
             * Enabled
             * @default true
             */
            enabled: boolean;
            /** Kind Pattern */
            kind_pattern: string;
            mode?: components["schemas"]["TriggerMode"] | null;
            /**
             * Policy
             * @default
             */
            policy: string;
            source?: components["schemas"]["EventSource"] | null;
            /** Title */
            title: string;
            /** Worker */
            worker?: string | null;
        };
        /** UpgradeTarget */
        UpgradeTarget: {
            /** Elite */
            elite?: number | null;
            /** Masteries */
            masteries?: {
                [key: string]: number;
            } | null;
            /**
             * Modules
             * @description 模组目标：[{"type": "X", "level": 2}]。
             */
            modules?: {
                [key: string]: unknown;
            }[] | null;
            /** Operator */
            operator: string;
            /** Skill Level */
            skill_level?: number | null;
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
        /**
         * WatchdogStatusOut
         * @description 值守概况（§4.3）：模式、进程内上次巡检时间与未解除告警数。
         */
        WatchdogStatusOut: {
            /** Last Run At */
            last_run_at: string | null;
            /**
             * Mode
             * @enum {string}
             */
            mode: "record" | "auto";
            /** Open Count */
            open_count: number;
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
    account_get_game_asset: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 资源类型：avatar、skill 或 item */
                kind: string;
                /** @description 干员 id（如 char_002_amiya）、技能 id（如 skchr_amiya_3）或道具 id（如 3003） */
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
            /** @description 下游依赖失败：SKLAND_UPSTREAM_FAILED */
            502: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SKLAND_UPSTREAM_FAILED",
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
                    "application/json": components["schemas"]["AccountDepotList"];
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
                    "application/json": components["schemas"]["AccountOperatorList"];
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
    account_list_account_upgrade_plans: {
        parameters: {
            query?: {
                /** @description 按状态筛选：active / completed / cancelled */
                status?: ("active" | "completed" | "cancelled") | null;
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
    account_get_account_upgrade_plan: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 计划 id */
                plan_id: string;
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
            /** @description 资源不存在：UPGRADE_PLAN_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UPGRADE_PLAN_NOT_FOUND",
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
    account_cancel_account_upgrade_plan: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 计划 id */
                plan_id: string;
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
            /** @description 资源不存在：UPGRADE_PLAN_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "UPGRADE_PLAN_NOT_FOUND",
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
    agent_list_artifacts: {
        parameters: {
            query?: {
                /** @description 只看这个工单的产物 */
                ticket_id?: string | null;
                /** @description 只看这个会话的产物 */
                session_id?: string | null;
                /** @description 只看这种产物 */
                kind?: components["schemas"]["ArtifactKind"] | null;
                /** @description 页码，从 1 开始 */
                page?: number;
                /** @description 每页条数，1–200 */
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
                    "application/json": components["schemas"]["ArtifactPage"];
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
    agent_get_artifact: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 产物 id */
                artifact_id: string;
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
                    "application/json": components["schemas"]["ArtifactDetailOut"];
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
            /** @description 资源不存在：ARTIFACT_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "ARTIFACT_NOT_FOUND",
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
    agent_download_artifact: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 产物 id */
                artifact_id: string;
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
            /** @description 资源不存在：ARTIFACT_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "ARTIFACT_NOT_FOUND",
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
    agent_pin_artifact: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 产物 id */
                artifact_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ArtifactPinRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ArtifactOut"];
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
            /** @description 资源不存在：ARTIFACT_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "ARTIFACT_NOT_FOUND",
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
    agent_list_audits: {
        parameters: {
            query?: {
                caller?: components["schemas"]["CallerType"] | null;
                tool_name?: string | null;
                status?: components["schemas"]["AuditStatus"] | null;
                risk_level?: components["schemas"]["RiskLevel"] | null;
                since?: string | null;
                ticket_id?: string | null;
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
    agent_run_brief: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
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
                    "application/json": components["schemas"]["BriefRunResult"];
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
    agent_list_channels: {
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
                    "application/json": components["schemas"]["ChannelOut"][];
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
    agent_list_channel_bindings: {
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
                    "application/json": components["schemas"]["BindingListOut"];
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
    agent_revoke_channel_binding: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 绑定 id */
                binding_id: string;
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
            /** @description 资源不存在：CHANNEL_BINDING_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "CHANNEL_BINDING_NOT_FOUND",
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
    agent_create_bind_code: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description telegram / feishu / dingtalk / qq */
                platform: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["BindCodeOut"];
                };
            };
            /** @description 请求不成立：BIND_CODE_INVALID */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "BIND_CODE_INVALID",
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
    agent_test_channel: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description telegram / feishu / dingtalk / qq */
                platform: string;
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
                    "application/json": components["schemas"]["TestChannelOut"];
                };
            };
            /** @description 请求不成立：BIND_CODE_INVALID */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "BIND_CODE_INVALID",
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
            /** @description 下游依赖失败：CHANNEL_SEND_FAILED */
            502: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "CHANNEL_SEND_FAILED",
                     *         "details": {},
                     *         "message": "下游依赖失败"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 服务暂时不可用：CHANNEL_NOT_CONFIGURED、SERVICE_UNAVAILABLE */
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
                     *         "code": "CHANNEL_NOT_CONFIGURED",
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
    agent_summarize_costs: {
        parameters: {
            query?: {
                /** @description 起始时间（含），ISO 8601；缺省为 7 天前 */
                since?: string | null;
                /** @description 结束时间（不含），ISO 8601；缺省为现在 */
                until?: string | null;
                /** @description 分组口径：day / agent / model / role */
                group_by?: string;
                /** @description 三位大写币种；缺省取区间内账本行最多的币种 */
                currency?: string | null;
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
                    "application/json": components["schemas"]["CostSummaryView"];
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
    agent_get_device_lease: {
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
                    "application/json": components["schemas"]["DeviceLeaseState"];
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
    agent_release_device_lease: {
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
    agent_list_goals: {
        parameters: {
            query?: {
                /** @description 只看这些状态，可重复表示并集 */
                status?: components["schemas"]["GoalStatus"][] | null;
                /** @description 页码，从 1 开始 */
                page?: number;
                /** @description 每页条数，1–200 */
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
                    "application/json": components["schemas"]["GoalPage"];
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
    agent_create_goal: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["GoalCreate"];
            };
        };
        responses: {
            /** @description Created */
            201: {
                headers: {
                    /** @description 新目标的 URI */
                    Location?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["GoalDetail"];
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
            /** @description 服务暂时不可用：AGENT_DISABLED、SERVICE_UNAVAILABLE */
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
    agent_get_goal: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 目标 id */
                goal_id: string;
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
                    "application/json": components["schemas"]["GoalDetail"];
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
            /** @description 资源不存在：GOAL_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "GOAL_NOT_FOUND",
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
    agent_update_goal: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 目标 id */
                goal_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["GoalUpdate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["GoalDetail"];
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
            /** @description 资源不存在：GOAL_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "GOAL_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 与当前状态冲突：GOAL_ENDED */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "GOAL_ENDED",
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
    agent_list_memories: {
        parameters: {
            query?: {
                /** @description 按状态过滤（proposed/active/rejected/superseded/expired）；不传返回全部状态 */
                status?: string | null;
                /** @description 按种类过滤（semantic/episodic/procedural） */
                kind?: string | null;
                /** @description 在正文与分类键里做子串匹配 */
                q?: string | null;
                /** @description 页码，从 1 开始 */
                page?: number;
                /** @description 每页条数，1–200 */
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
                    "application/json": components["schemas"]["MemoryPage"];
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
    agent_update_memory: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 记忆 id */
                memory_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MemoryUpdateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MemoryOut"];
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
            /** @description 资源不存在：MEMORY_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "MEMORY_NOT_FOUND",
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
    agent_delete_memory: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 记忆 id */
                memory_id: string;
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
            /** @description 资源不存在：MEMORY_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "MEMORY_NOT_FOUND",
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
    agent_review_memory: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 记忆 id */
                memory_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MemoryReviewRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MemoryOut"];
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
            /** @description 资源不存在：MEMORY_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "MEMORY_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 与当前状态冲突：MEMORY_NOT_PROPOSED */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "MEMORY_NOT_PROPOSED",
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
    agent_list_model_profiles: {
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
                    "application/json": components["schemas"]["ModelProfileList"];
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
    agent_create_model_profile: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ModelProfileCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ModelProfileView"];
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
            /** @description 与当前状态冲突：MODEL_PROFILE_EXISTS */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "MODEL_PROFILE_EXISTS",
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
            /** @description 服务暂时不可用：LLM_ENCRYPTION_KEY_INVALID、SERVICE_UNAVAILABLE */
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
    agent_get_model_profile: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 模型档案名字 */
                name: string;
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
                    "application/json": components["schemas"]["ModelProfileView"];
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
            /** @description 资源不存在：MODEL_PROFILE_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "MODEL_PROFILE_NOT_FOUND",
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
    agent_update_model_profile: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 模型档案名字 */
                name: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ModelProfileUpdate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ModelProfileView"];
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
            /** @description 资源不存在：MODEL_PROFILE_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "MODEL_PROFILE_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 与当前状态冲突：MODEL_PROFILE_READONLY、MODEL_PROFILE_IN_USE */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "MODEL_PROFILE_READONLY",
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
            /** @description 服务暂时不可用：LLM_ENCRYPTION_KEY_INVALID、SERVICE_UNAVAILABLE */
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
    agent_delete_model_profile: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 模型档案名字 */
                name: string;
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
            /** @description 资源不存在：MODEL_PROFILE_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "MODEL_PROFILE_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 与当前状态冲突：MODEL_PROFILE_READONLY、MODEL_PROFILE_IN_USE */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "MODEL_PROFILE_READONLY",
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
    agent_probe_model_profile: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 模型档案名字 */
                name: string;
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
                    "application/json": components["schemas"]["ProbeResultView"];
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
            /** @description 资源不存在：MODEL_PROFILE_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "MODEL_PROFILE_NOT_FOUND",
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
            /** @description 请求过于频繁：LLM_RATE_LIMITED */
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
                     *         "code": "LLM_RATE_LIMITED",
                     *         "details": {},
                     *         "message": "请求过于频繁"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 下游依赖失败：LLM_REQUEST_FAILED */
            502: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "LLM_REQUEST_FAILED",
                     *         "details": {},
                     *         "message": "下游依赖失败"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 服务暂时不可用：LLM_NOT_CONFIGURED、LLM_ENCRYPTION_KEY_INVALID、SERVICE_UNAVAILABLE */
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
            /** @description 下游依赖超时：LLM_TIMEOUT */
            504: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "LLM_TIMEOUT",
                     *         "details": {},
                     *         "message": "下游依赖超时"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
        };
    };
    agent_list_model_roles: {
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
                    "application/json": components["schemas"]["ModelRolesView"];
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
    agent_update_nav_edge: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 跳转 id（UUID） */
                edge_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["NavEdgePut"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["NavEdgeOut"];
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
            /** @description 资源不存在：NOT_FOUND、NAV_NODE_NOT_FOUND */
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
    agent_delete_nav_edge: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 跳转 id */
                edge_id: string;
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
    agent_get_nav_graph: {
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
                    "application/json": components["schemas"]["NavGraphOut"];
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
    agent_put_nav_node: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 界面 id（短 slug 或 stage:<关卡代号>） */
                node_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["NavNodePut"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["NavNodeOut"];
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
    agent_list_observations: {
        parameters: {
            query?: {
                /** @description 按规则 id 过滤 */
                rule_id?: string | null;
                /** @description 是否已审阅 */
                reviewed?: boolean | null;
                /** @description 页码，从 1 开始 */
                page?: number;
                /** @description 每页条数，1–200 */
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
                    "application/json": components["schemas"]["ObservationPage"];
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
    agent_review_observation: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 观察记录 id */
                observation_id: number;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ObservationVerdictIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ObservationOut"];
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
            /** @description 资源不存在：OBSERVATION_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "OBSERVATION_NOT_FOUND",
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
    agent_get_orchestrator: {
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
                    "application/json": components["schemas"]["OrchestratorStateModel"];
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
    agent_interrupt_orchestrator: {
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
                    "application/json": components["schemas"]["OrchestratorInterruptResult"];
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
    agent_list_orchestrator_messages: {
        parameters: {
            query?: {
                /** @description 只取 seq 更大的消息 */
                after_seq?: number | null;
                /** @description 页码，从 1 开始 */
                page?: number;
                /** @description 每页条数，1–200 */
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
                    "application/json": components["schemas"]["AgentMessagePage"];
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
    agent_send_orchestrator_message: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AgentMessageCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "text/event-stream": unknown;
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
            /** @description 服务暂时不可用：LLM_NOT_CONFIGURED、LLM_ENCRYPTION_KEY_INVALID、AGENT_DISABLED */
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
    agent_reset_orchestrator: {
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
                    "application/json": components["schemas"]["OrchestratorStateModel"];
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
            /** @description 与当前状态冲突：RUN_ALREADY_ACTIVE */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "RUN_ALREADY_ACTIVE",
                     *         "details": {},
                     *         "message": "与当前状态冲突"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
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
    agent_list_outbox: {
        parameters: {
            query?: {
                /** @description 按发送状态过滤 */
                status?: components["schemas"]["OutboxStatus"] | null;
                /** @description 按出口过滤（im_* 或 webpush） */
                channel?: components["schemas"]["OutboxChannel"] | null;
                /** @description 页码，从 1 开始 */
                page?: number;
                /** @description 每页条数，1–200 */
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
                    "application/json": components["schemas"]["OutboxPage"];
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
    agent_search_retrieval: {
        parameters: {
            query: {
                /** @description 检索词，去掉首尾空白后 1–500 字 */
                q: string;
                /** @description 逗号分隔的来源 id；省略时检索默认可检索来源 */
                sources?: string | null;
                /** @description 返回条数，1–30；省略时用设置值 */
                top_k?: number | null;
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
                    "application/json": components["schemas"]["RetrievalResult"];
                };
            };
            /** @description 请求不成立：RETRIEVAL_QUERY_INVALID */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "RETRIEVAL_QUERY_INVALID",
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
    agent_list_retrieval_sources: {
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
                    "application/json": components["schemas"]["RetrievalSourceList"];
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
    agent_update_retrieval_source: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 检索来源 id */
                source_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RetrievalSourceUpdate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RetrievalSourceView"];
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
            /** @description 资源不存在：KNOWLEDGE_SOURCE_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "KNOWLEDGE_SOURCE_NOT_FOUND",
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
    agent_reindex_retrieval_source: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 检索来源 id */
                source_id: string;
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
                    "application/json": components["schemas"]["ReindexAccepted"];
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
            /** @description 资源不存在：KNOWLEDGE_SOURCE_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "KNOWLEDGE_SOURCE_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 与当前状态冲突：KNOWLEDGE_SOURCE_BUSY */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "KNOWLEDGE_SOURCE_BUSY",
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
    agent_list_runs: {
        parameters: {
            query?: {
                /** @description 只看这个会话的运行 */
                session_id?: string | null;
                /** @description 只看这个工单的运行；工单功能上线前返回空列表 */
                ticket_id?: string | null;
                /** @description 只列根运行（不含子运行） */
                root_only?: boolean;
                /** @description 页码，从 1 开始 */
                page?: number;
                /** @description 每页条数，1–200 */
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
                    "application/json": components["schemas"]["RunPage"];
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
    agent_get_run: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 运行 id */
                run_id: string;
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
                    "application/json": components["schemas"]["RunDetail"];
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
            /** @description 资源不存在：RUN_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "RUN_NOT_FOUND",
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
    agent_list_run_spans: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 运行 id */
                run_id: string;
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
                    "application/json": components["schemas"]["RunSpansResponse"];
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
            /** @description 资源不存在：RUN_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "RUN_NOT_FOUND",
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
    agent_list_checkpoints: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 会话 id */
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
                    "application/json": components["schemas"]["CheckpointList"];
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
    agent_compact_session: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 会话 id */
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
                    "application/json": components["schemas"]["CompactResultView"];
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
            /** @description 与当前状态冲突：RUN_ALREADY_ACTIVE */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "RUN_ALREADY_ACTIVE",
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
            /** @description 请求过于频繁：BUDGET_EXCEEDED */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "BUDGET_EXCEEDED",
                     *         "details": {},
                     *         "message": "请求过于频繁"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 下游依赖失败：COMPACTION_FAILED */
            502: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "COMPACTION_FAILED",
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
    agent_get_plan: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Worker 会话 id */
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
                    "application/json": components["schemas"]["PlanOut"] | null;
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
    agent_approve_plan: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Worker 会话 id */
                session_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PlanApprovalRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PlanOut"];
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
            /** @description 资源不存在：AGENT_SESSION_NOT_FOUND、PLAN_NOT_FOUND */
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
            /** @description 与当前状态冲突：PLAN_NOT_AWAITING_APPROVAL */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "PLAN_NOT_AWAITING_APPROVAL",
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
    agent_rewind_session: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 会话 id */
                session_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RewindRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RewindResultOut"];
                };
            };
            /** @description 请求不成立：REWIND_TARGET_INVALID */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "REWIND_TARGET_INVALID",
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
            /** @description 与当前状态冲突：TICKET_ENDED、RUN_ALREADY_ACTIVE */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "TICKET_ENDED",
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
    agent_list_skills: {
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
                    "application/json": components["schemas"]["SkillList"];
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
    agent_get_skill: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 技能 id */
                skill_id: string;
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
                    "application/json": components["schemas"]["SkillDetail"];
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
            /** @description 资源不存在：SKILL_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SKILL_NOT_FOUND",
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
    agent_put_skill: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 技能 id */
                skill_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SkillWriteRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SkillDetail"];
                };
            };
            /** @description 请求不成立：SKILL_INVALID */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SKILL_INVALID",
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
    agent_delete_skill: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 技能 id */
                skill_id: string;
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
            /** @description 资源不存在：SKILL_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SKILL_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 与当前状态冲突：SKILL_BUILTIN_READONLY */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SKILL_BUILTIN_READONLY",
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
    agent_list_specs: {
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
                    "application/json": components["schemas"]["SpecList"];
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
    agent_get_spec: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 代理定义名字，可用的名字见列表端点 */
                name: string;
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
                    "application/json": components["schemas"]["SpecDetail"];
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
            /** @description 资源不存在：AGENT_SPEC_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "AGENT_SPEC_NOT_FOUND",
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
    agent_list_tickets: {
        parameters: {
            query?: {
                /** @description 只看这些状态，可重复表示并集 */
                status?: components["schemas"]["TicketStatus"][] | null;
                /** @description 只看这个代理定义的工单 */
                kind?: string | null;
                /** @description 只看这个来源的工单 */
                origin?: components["schemas"]["TicketOrigin"] | null;
                /** @description 页码，从 1 开始 */
                page?: number;
                /** @description 每页条数，1–200 */
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
                    "application/json": components["schemas"]["TicketPage"];
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
    agent_get_ticket: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 工单 id */
                ticket_id: string;
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
                    "application/json": components["schemas"]["TicketDetail"];
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
            /** @description 资源不存在：TICKET_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "TICKET_NOT_FOUND",
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
    agent_cancel_ticket: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 工单 id */
                ticket_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TicketCancelRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TicketDetail"];
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
            /** @description 资源不存在：TICKET_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "TICKET_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 与当前状态冲突：TICKET_ENDED */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "TICKET_ENDED",
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
    agent_fork_ticket: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 源工单 id */
                ticket_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ForkRequest"];
            };
        };
        responses: {
            /** @description Created */
            201: {
                headers: {
                    /** @description 新工单的 URI */
                    Location?: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TicketDetail"];
                };
            };
            /** @description 请求不成立：REWIND_TARGET_INVALID */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "REWIND_TARGET_INVALID",
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
            /** @description 资源不存在：TICKET_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "TICKET_NOT_FOUND",
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
    agent_interrupt_ticket: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 工单 id */
                ticket_id: string;
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
                    "application/json": components["schemas"]["TicketInterruptResult"];
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
            /** @description 资源不存在：TICKET_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "TICKET_NOT_FOUND",
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
    agent_list_ticket_messages: {
        parameters: {
            query?: {
                /** @description 只取 Worker 会话里 seq 更大的消息 */
                after_seq?: number | null;
                /** @description 页码，从 1 开始 */
                page?: number;
                /** @description 每页条数，1–200 */
                size?: number;
                /** @description 是否带上该 Worker 会话的子代理消息 */
                include_subagents?: boolean;
            };
            header?: never;
            path: {
                /** @description 工单 id */
                ticket_id: string;
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
                    "application/json": components["schemas"]["AgentMessagePage"];
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
            /** @description 资源不存在：TICKET_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "TICKET_NOT_FOUND",
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
    agent_send_ticket_message: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 工单 id */
                ticket_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AgentMessageCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "text/event-stream": unknown;
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
            /** @description 资源不存在：TICKET_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "TICKET_NOT_FOUND",
                     *         "details": {},
                     *         "message": "资源不存在"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 与当前状态冲突：TICKET_ENDED */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "TICKET_ENDED",
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
            /** @description 服务暂时不可用：LLM_NOT_CONFIGURED、AGENT_DISABLED */
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
    agent_list_ticket_reports: {
        parameters: {
            query?: {
                /** @description 页码，从 1 开始 */
                page?: number;
                /** @description 每页条数，1–200 */
                size?: number;
            };
            header?: never;
            path: {
                /** @description 工单 id */
                ticket_id: string;
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
                    "application/json": components["schemas"]["TicketReportPage"];
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
            /** @description 资源不存在：TICKET_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "TICKET_NOT_FOUND",
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
    agent_list_trigger_rules: {
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
                    "application/json": components["schemas"]["TriggerRuleList"];
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
    agent_put_trigger_rule: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 规则 id（2–48 位小写字母、数字与连字符） */
                rule_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TriggerRuleWrite"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TriggerRuleOut"];
                };
            };
            /** @description 请求不成立：TRIGGER_RULE_INVALID */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "TRIGGER_RULE_INVALID",
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
    agent_delete_trigger_rule: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 规则 id（2–48 位小写字母、数字与连字符） */
                rule_id: string;
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
            /** @description 资源不存在：TRIGGER_RULE_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "TRIGGER_RULE_NOT_FOUND",
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
    agent_list_triggers: {
        parameters: {
            query?: {
                /** @description 按来源过滤 */
                source?: string | null;
                /** @description 按事件种类过滤 */
                kind?: string | null;
                /** @description 按命中的规则 id 过滤 */
                rule_id?: string | null;
                /** @description 页码，从 1 开始 */
                page?: number;
                /** @description 每页条数，1–200 */
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
                    "application/json": components["schemas"]["TriggerPage"];
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
    agent_get_watchdog: {
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
                    "application/json": components["schemas"]["WatchdogStatusOut"];
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
    agent_list_watchdog_alerts: {
        parameters: {
            query?: {
                /** @description false（默认）只看未解除；true 只看已解除 */
                resolved?: boolean;
                /** @description 按级别过滤：info / warn / critical */
                severity?: components["schemas"]["AlertSeverity"] | null;
                /** @description 页码，从 1 开始 */
                page?: number;
                /** @description 每页条数，1–200 */
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
                    "application/json": components["schemas"]["AlertPage"];
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
    agent_resolve_watchdog_alert: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 告警 id */
                alert_id: number;
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
                    "application/json": components["schemas"]["AlertOut"];
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
            /** @description 资源不存在：WATCHDOG_ALERT_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "WATCHDOG_ALERT_NOT_FOUND",
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
    skland_unbind_skland: {
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
                    "application/json": components["schemas"]["SklandBindResult"];
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
    skland_bind_skland_manual: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SklandManualBindRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SklandStatusResult"];
                };
            };
            /** @description token 缺失或不匹配：SKLAND_LOGIN_FAILED */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SKLAND_LOGIN_FAILED",
                     *         "details": {},
                     *         "message": "token 缺失或不匹配"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 与当前状态冲突：SKLAND_ENCRYPTION_KEY_INVALID */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SKLAND_ENCRYPTION_KEY_INVALID",
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
            /** @description 下游依赖失败：SKLAND_UPSTREAM_FAILED */
            502: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SKLAND_UPSTREAM_FAILED",
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
    skland_start_skland_qr_login: {
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
                    "application/json": components["schemas"]["SklandQrStartResult"];
                };
            };
            /** @description 与当前状态冲突：SKLAND_ENCRYPTION_KEY_INVALID */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SKLAND_ENCRYPTION_KEY_INVALID",
                     *         "details": {},
                     *         "message": "与当前状态冲突"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 下游依赖失败：SKLAND_UPSTREAM_FAILED */
            502: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SKLAND_UPSTREAM_FAILED",
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
    skland_poll_skland_qr_login: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description 扫码会话 id */
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
                    "application/json": components["schemas"]["SklandQrPollResult"];
                };
            };
            /** @description 资源不存在：SKLAND_QR_SESSION_NOT_FOUND */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SKLAND_QR_SESSION_NOT_FOUND",
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
            /** @description 下游依赖失败：SKLAND_UPSTREAM_FAILED */
            502: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SKLAND_UPSTREAM_FAILED",
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
    skland_get_skland_status: {
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
                    "application/json": components["schemas"]["SklandStatusResult"];
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
    skland_sync_skland_training: {
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
                    "application/json": components["schemas"]["SklandSyncResult"];
                };
            };
            /** @description 与当前状态冲突：SKLAND_NOT_BOUND、SKLAND_SYNC_BUSY、SKLAND_ENCRYPTION_KEY_INVALID */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SKLAND_NOT_BOUND",
                     *         "details": {},
                     *         "message": "与当前状态冲突"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 请求过于频繁：RATE_LIMITED */
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
                     *         "code": "RATE_LIMITED",
                     *         "details": {},
                     *         "message": "请求过于频繁"
                     *       }
                     *     }
                     */
                    "application/json": unknown;
                };
            };
            /** @description 下游依赖失败：SKLAND_UPSTREAM_FAILED */
            502: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example {
                     *       "error": {
                     *         "code": "SKLAND_UPSTREAM_FAILED",
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

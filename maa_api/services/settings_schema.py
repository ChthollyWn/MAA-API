"""Code-owned metadata for the visual settings API (docs/04 §5.6)."""

from __future__ import annotations

import json
import platform
from typing import Any
from pathlib import Path

from maa_api.settings import CONFIG_ONLY_SETTING_KEYS, REPO_ROOT, SETTING_KEYS

__all__ = ["SETTINGS_SCHEMA", "SETTING_READONLY_KEYS"]

SETTING_READONLY_KEYS = frozenset(
    {"app.access_token", "app.maa_core_path", "adb.path"}
)


def _connection_config_names() -> list[str]:
    """Load MaaCore's supported ADB connection names in resource order."""
    platform_name = "Darwin" if platform.system() == "Darwin" else "Linux"
    platforms = ["Darwin", platform_name, "Linux"]
    paths = [
        REPO_ROOT / "resource" / "lib" / "maa" / name / "resource" / "config.json"
        for name in dict.fromkeys(platforms)
    ]
    for path in paths:
        try:
            document = json.loads(path.read_text(encoding="utf8"))
            entries = document["connection"]
            if not isinstance(entries, list):
                continue
            names = [
                entry["configName"]
                for entry in entries
                if isinstance(entry, dict)
                and isinstance(entry.get("configName"), str)
                and entry["configName"].strip()
            ]
            if names:
                return list(dict.fromkeys(names))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return ["General"]

_FIELDS: dict[str, dict[str, Any]] = {
    "app.access_token": dict(label="访问密钥", group="应用", type="string", sensitive=True, hot_action="restart_required"),
    "app.maa_core_path": dict(label="内核路径", group="应用", type="string", hot_action="restart_required"),
    "app.proxy": dict(label="系统代理", group="应用", type="string", hot_action="none"),
    "updates.download_prefix": dict(label="更新下载前缀", group="更新", type="string", hot_action="none"),
    "updates.check_hour": dict(label="每日更新检查时间（小时）", group="更新", type="integer", minimum=0, maximum=23, hot_action="restart_required"),
    "adb.path": dict(label="ADB 路径", group="设备", type="string", hot_action="restart_required"),
    "adb.address": dict(label="设备地址", group="设备", type="string", hot_action="reconnect"),
    "adb.screenshot_quality": dict(label="截图质量", group="设备", type="integer", minimum=1, maximum=95, hot_action="none"),
    "adb.connection_config": dict(label="ADB 连接配置", group="设备", type="string", enum=_connection_config_names(), hot_action="reconnect"),
    "adb.common_ports": dict(label="模拟器常见端口", group="设备", type="integer[]", minimum=1, maximum=65535, hot_action="none"),
    # M38 内核识别间隔：开机时写进 resource/maa-layers/custom/resource/config.json，
    # 所以要重启服务才生效；内核自带的默认值是 500。
    "core.task_delay": dict(label="内核识别间隔（毫秒）", group="设备", type="integer", minimum=0, maximum=2000, hot_action="restart_required"),
    "log.ring_size": dict(label="日志缓冲条数", group="日志", type="integer", minimum=1, maximum=100000, hot_action="none"),
    "log.batch_size": dict(label="日志刷盘批量", group="日志", type="integer", minimum=1, maximum=10000, hot_action="none"),
    "log.flush_interval": dict(label="日志刷盘间隔", group="日志", type="number", minimum=0.05, maximum=60, hot_action="none"),
    "log.core_min_level": dict(label="内核日志最低级别", group="日志", type="string", enum=["OFF", "FAT", "ERR", "WRN", "INF", "DBG", "TRC"], hot_action="none"),
    "log.persist_maacore_debug_level": dict(label="内核调试日志落盘级别", group="日志", type="string", enum=["OFF", "FATAL", "ERROR", "WARNING", "INFO", "DEBUG"], hot_action="none"),
    "channel.client_type": dict(label="默认客户端渠道", group="渠道", type="string", enum=["Official", "Bilibili"], hot_action="none"),
    # M25 的「外部数据源」分区。全部 hot_action="none"：访问层每次请求现读设置。
    "sources.enabled": dict(label="启用外部数据源", group="外部数据源", type="boolean", hot_action="none"),
    "sources.use_proxy": dict(label="外部请求走系统代理", group="外部数据源", type="boolean", hot_action="none"),
    "sources.cache_live_seconds": dict(label="当期数据缓存时长（秒）", group="外部数据源", type="integer", minimum=60, maximum=3600, hot_action="none"),
    "sources.cache_static_seconds": dict(label="稳定数据缓存时长（秒）", group="外部数据源", type="integer", minimum=600, maximum=86400, hot_action="none"),
    "sources.cache_max_mb": dict(label="缓存内存上限（MB）", group="外部数据源", type="integer", minimum=8, maximum=512, hot_action="none"),
    "sources.cache_level_seconds": dict(
        label="拆包数据缓存时长（秒）",
        group="外部数据源",
        type="integer",
        minimum=3600,
        maximum=604800,
        hot_action="none",
    ),
    "sources.gamedata_enabled": dict(
        label="启用拆包数据",
        group="外部数据源",
        type="boolean",
        hot_action="none",
    ),
    "sources.gamedata_rate_per_minute": dict(
        label="拆包数据每分钟请求上限",
        group="外部数据源",
        type="integer",
        minimum=1,
        maximum=60,
        hot_action="none",
    ),
    "sources.gamedata_mirrors": dict(
        label="拆包数据镜像",
        group="外部数据源",
        type="string_list",
        description="按顺序尝试；都失败且开启 GitHub 回退时再走 API",
        hot_action="none",
    ),
    "sources.gamedata_github_api_fallback": dict(
        label="拆包数据 GitHub 回退",
        group="外部数据源",
        type="boolean",
        hot_action="none",
    ),
    "sources.prts_enabled": dict(label="启用 PRTS", group="外部数据源", type="boolean", hot_action="none"),
    "sources.prts_rate_per_minute": dict(label="PRTS 每分钟请求上限", group="外部数据源", type="integer", minimum=1, maximum=120, hot_action="none"),
    # M26 的企鹅物流（掉率）。主机两个内容一致，失败时自动改试另一个。
    "sources.penguin_enabled": dict(label="启用企鹅物流", group="外部数据源", type="boolean", hot_action="none"),
    "sources.penguin_host": dict(label="企鹅物流主机", group="外部数据源", type="string", enum=["penguin-stats.io", "penguin-stats.cn"], hot_action="none"),
    "sources.penguin_rate_per_minute": dict(label="企鹅物流每分钟请求上限", group="外部数据源", type="integer", minimum=1, maximum=60, hot_action="none"),
    # M29 作业站（prts.maa.plus）。关掉后作业搜索与导入直接报来源已关闭（附录 B）。
    "sources.copilot_site_enabled": dict(label="启用作业站", group="外部数据源", type="boolean", hot_action="none"),
    "sources.copilot_site_rate_per_minute": dict(label="作业站每分钟请求上限", group="外部数据源", type="integer", minimum=1, maximum=60, hot_action="none"),
    "channel.server": dict(label="默认数据服务器", group="渠道", type="string", enum=["CN", "US", "JP", "KR"], hot_action="none"),
    "llm.base_url": dict(label="LLM API 地址", group="LLM", type="string", hot_action="none"),
    "llm.api_key": dict(label="LLM API 密钥", group="LLM", type="string", sensitive=True, hot_action="none"),
    "llm.model": dict(label="LLM 模型", group="LLM", type="string", hot_action="none"),
    # M67 模型层：九个角色各选一个模型档案；空串表示 default 档案（由 llm.* 合成）。
    "llm.role_orchestrator": dict(label="总控使用的模型档案", group="LLM", type="string", description="空表示使用默认档案", hot_action="none"),
    "llm.role_worker": dict(label="Worker 使用的模型档案", group="LLM", type="string", description="空表示使用默认档案", hot_action="none"),
    "llm.role_subagent": dict(label="子代理使用的模型档案", group="LLM", type="string", description="空表示使用默认档案", hot_action="none"),
    "llm.role_summarizer": dict(label="摘要使用的模型档案", group="LLM", type="string", description="空表示使用默认档案", hot_action="none"),
    "llm.role_title": dict(label="标题使用的模型档案", group="LLM", type="string", description="空表示使用默认档案", hot_action="none"),
    "llm.role_trigger_eval": dict(label="事件研判使用的模型档案", group="LLM", type="string", description="空表示使用默认档案", hot_action="none"),
    "llm.role_memory": dict(label="记忆整理使用的模型档案", group="LLM", type="string", description="空表示使用默认档案", hot_action="none"),
    "llm.role_vision": dict(label="视觉使用的模型档案", group="LLM", type="string", description="空表示使用默认档案", hot_action="none"),
    "llm.role_embedding": dict(label="向量使用的模型档案", group="LLM", type="string", description="空表示不使用远端向量", hot_action="none"),
    # M67 成本预算（元，0 为不限）与上下文窗口兜底值。
    "budget.daily_cost_limit": dict(label="每日模型成本上限（元）", group="LLM", type="number", minimum=0, maximum=100000, description="0 为不限", hot_action="none"),
    "budget.per_run_cost_limit": dict(label="单次运行成本上限（元）", group="LLM", type="number", minimum=0, maximum=10000, description="0 为不限", hot_action="none"),
    "budget.warn_ratio": dict(label="成本告警比例", group="LLM", type="number", minimum=0.1, maximum=1, hot_action="none"),
    "context.fallback_context_tokens": dict(label="默认上下文窗口（token）", group="Agent", type="integer", minimum=8000, maximum=2000000, description="模型档案没写窗口时使用", hot_action="none"),
    # M74 压缩：自动压缩阈值、保留的最近轮次与工具结果清理边界，压缩/清理时现读。
    "context.compaction_ratio": dict(label="自动压缩的窗口比例", group="Agent", type="number", minimum=0.3, maximum=0.95, hot_action="none"),
    "context.keep_recent_turns": dict(label="压缩时保留的最近轮次", group="Agent", type="integer", minimum=1, maximum=10, hot_action="none"),
    "context.clear_tool_results_after": dict(label="保留原文的最近工具往返数", group="Agent", type="integer", minimum=1, maximum=20, description="更早的可重新获取的结果会成批换成占位", hot_action="none"),
    # M75 工作记忆：外置阈值与计划批准策略，外置/计划每次现读。
    "context.spill_threshold_chars": dict(label="工具输出外置阈值（字符）", group="Agent", type="integer", minimum=1000, maximum=100000, description="工具结果超过这个长度就存成产物", hot_action="none"),
    "plan.approval_mode": dict(label="计划批准模式", group="Agent", type="string", enum=["always", "complex", "never"], description="always 都要批准、complex 只对复杂计划、never 都不批准", hot_action="none"),
    "plan.complex_min_steps": dict(label="复杂计划的最小步骤数", group="Agent", type="integer", minimum=2, maximum=20, description="步骤数达到这个值算复杂计划", hot_action="none"),
    # M76 检索层：开关、向量服务与分块参数，检索/同步时现读，改完立即生效。
    "rag.enabled": dict(label="启用知识检索", group="Agent", type="boolean", hot_action="none"),
    "rag.embedding_provider": dict(label="向量服务", group="Agent", type="string", enum=["none", "remote", "local"], hot_action="none",
        description="none 只用全文检索；remote 用 LLM 设置里 embedding 角色的档案；local 用本地 ONNX 档案"),
    "rag.top_k": dict(label="检索返回条数", group="Agent", type="integer", minimum=1, maximum=30, hot_action="none"),
    "rag.chunk_chars": dict(label="分块长度（字）", group="Agent", type="integer", minimum=200, maximum=4000, hot_action="none"),
    "rag.chunk_overlap": dict(label="分块重叠（字）", group="Agent", type="integer", minimum=0, maximum=1000, hot_action="none"),
    # M77 外部语料：四个开关检索/刷新时现读；刷新 cron 在启动时注册任务，改完要重启。
    "rag.maa_docs_enabled": dict(label="检索 MAA 文档", group="Agent", type="boolean", hot_action="none"),
    "rag.prts_enabled": dict(label="检索 PRTS 页面", group="Agent", type="boolean", hot_action="none"),
    "rag.gamedata_enabled": dict(label="检索拆包实体卡", group="Agent", type="boolean", hot_action="none"),
    "rag.copilot_site_enabled": dict(label="检索作业站作业", group="Agent", type="boolean", hot_action="none"),
    "rag.refresh_cron": dict(label="语料刷新时间（cron）", group="Agent", type="string", hot_action="restart_required",
        description="五段 cron，按本机时区；改完重启服务生效"),
    # M78 技能：总开关，中间件与 load_skill 每次现读。
    "skills.enabled": dict(label="启用技能", group="Agent", type="boolean", hot_action="none"),
    # M79 长期记忆：四项都由抽取、注入与 remember 每次现读，改完立即生效。
    "memory.enabled": dict(label="启用长期记忆", group="Agent", type="boolean", hot_action="none"),
    "memory.auto_extract": dict(label="自动抽取记忆", group="Agent", type="boolean", hot_action="none"),
    "memory.require_review": dict(label="记忆须经审核", group="Agent", type="boolean", hot_action="none"),
    "memory.max_injection_tokens": dict(label="记忆注入上限（token）", group="Agent", type="integer", minimum=0, maximum=8000, hot_action="none"),
    # M87 作业研究闭环：策略弹授权卡时现读，改完立即生效。
    "copilot_loop.max_iterations": dict(label="作业研究每轮最多尝试次数", group="Agent", type="integer", minimum=1, maximum=10, hot_action="none"),
    # M80 提示注入防护：两项都由扫描与降权每次现读，改完立即生效。
    "guard.injection_detection": dict(label="提示注入检测", group="Agent", type="string", enum=["off", "rules", "rules_llm"], hot_action="none", description="off 只打标不检测；rules 用规则检测；rules_llm 在规则命中或内容出自他人时再请便宜模型复核"),
    "guard.block_on_detect": dict(label="检测到可疑指令时整段拦截", group="Agent", type="boolean", hot_action="none", description="关闭时只打标，之后的写操作逐次确认"),
    # M81 主动工作：三项都由事件处理与规则服务现读，改完立即生效。
    "triggers.enabled": dict(label="启用主动工作", group="Agent", type="boolean", hot_action="none"),
    "triggers.default_mode": dict(label="新事件规则的默认档位", group="Agent", type="string", enum=["off", "observe", "auto"], hot_action="none"),
    "triggers.eval_daily_limit": dict(label="每日事件研判次数上限", group="Agent", type="integer", minimum=0, maximum=5000, hot_action="none"),
    # M82 目标模式：两项都由 GoalService 每次现读，改完立即生效。
    "goals.enabled": dict(label="启用目标模式", group="Agent", type="boolean", hot_action="none"),
    "goals.default_review_cron": dict(label="目标默认复盘时间", group="Agent", type="string", description="五段 cron，按北京时间", hot_action="none"),
    # M83 值守：六项都由 WatchdogService 每轮现读，改完立即生效。
    "watchdog.enabled": dict(label="启用值守", group="Agent", type="boolean", hot_action="none"),
    "watchdog.interval_seconds": dict(label="值守巡检间隔（秒）", group="Agent", type="integer", minimum=60, maximum=3600, description="两次真正执行的最小间隔；定时任务每 60 秒触发，间隔未到就跳过", hot_action="none"),
    "watchdog.mode": dict(label="值守模式", group="Agent", type="string", enum=["record", "auto"], description="record 只记录告警；auto 才执行有限的自愈动作", hot_action="none"),
    "watchdog.allow_core_restart": dict(label="允许值守重启内核", group="Agent", type="boolean", description="auto 模式下是否允许重启内核", hot_action="none"),
    "watchdog.ticket_stale_minutes": dict(label="工单停滞判定（分钟）", group="Agent", type="integer", minimum=5, maximum=1440, description="工单超过这个时长没有活动即视为停滞", hot_action="none"),
    "watchdog.device_frozen_minutes": dict(label="流水线无回调判定（分钟）", group="Agent", type="integer", minimum=2, maximum=120, description="正在执行的流水线超过这个时长没有内核回调即视为冻结", hot_action="none"),
    # M84 每日简报：四项都由简报服务现读；brief.cron 启动时注册任务。
    "brief.enabled": dict(label="每日简报", group="Agent", type="boolean", hot_action="none"),
    "brief.cron": dict(label="简报生成时间", group="Agent", type="string", description="五段 cron，按北京时间", hot_action="restart_required"),
    "brief.push_web": dict(label="简报推送到浏览器", group="Agent", type="boolean", hot_action="none"),
    "brief.push_im": dict(label="简报推送到 IM", group="Agent", type="boolean", hot_action="none"),
    # M85 IM 通道：四个平台默认关闭；启用、凭证与地址在装配适配器时读取，改完要重启；
    # 回复长度与确认策略由 ChannelService 每次现读。四个密钥是敏感项（09 §8.2）。
    "channels.max_reply_chars": dict(label="单条 IM 回复最大字数", group="IM 通道", type="integer", minimum=80, maximum=4000, description="超出时先给结论再附网页链接", hot_action="none"),
    "channels.allow_destructive_approval": dict(label="允许在 IM 里批准破坏类确认", group="IM 通道", type="boolean", description="默认只能在网页上批准", hot_action="none"),
    "channels.telegram_enabled": dict(label="启用 Telegram 通道", group="IM 通道", type="boolean", hot_action="restart_required"),
    "channels.feishu_enabled": dict(label="启用飞书通道", group="IM 通道", type="boolean", hot_action="restart_required"),
    "channels.dingtalk_enabled": dict(label="启用钉钉通道", group="IM 通道", type="boolean", hot_action="restart_required"),
    "channels.qq_enabled": dict(label="启用 QQ 官方机器人通道", group="IM 通道", type="boolean", hot_action="restart_required"),
    "channels.telegram_bot_token": dict(label="Telegram Bot Token", group="IM 通道", type="string", sensitive=True, hot_action="restart_required"),
    "channels.feishu_app_secret": dict(label="飞书应用密钥", group="IM 通道", type="string", sensitive=True, hot_action="restart_required"),
    "channels.dingtalk_client_secret": dict(label="钉钉客户端密钥", group="IM 通道", type="string", sensitive=True, hot_action="restart_required"),
    "channels.qq_app_secret": dict(label="QQ 机器人密钥", group="IM 通道", type="string", sensitive=True, hot_action="restart_required"),
    "channels.feishu_app_id": dict(label="飞书 App ID", group="IM 通道", type="string", hot_action="restart_required"),
    "channels.dingtalk_client_id": dict(label="钉钉 Client ID", group="IM 通道", type="string", hot_action="restart_required"),
    "channels.qq_app_id": dict(label="QQ App ID", group="IM 通道", type="string", hot_action="restart_required"),
    "channels.feishu_domain": dict(label="飞书开放平台地址", group="IM 通道", type="string", hot_action="restart_required"),
    "channels.telegram_proxy": dict(label="Telegram 专用代理", group="IM 通道", type="string", description="空表示使用 app.proxy", hot_action="restart_required"),
    "channels.qq_sandbox": dict(label="QQ 沙箱环境", group="IM 通道", type="boolean", hot_action="restart_required"),
    # M68 运行追踪：开关与保留天数在设置页可改；导出两项是 config-only，不在这里。
    "trace.enabled": dict(label="记录运行追踪", group="Agent", type="boolean", hot_action="none"),
    "trace.retention_days": dict(label="运行追踪保留天数", group="Agent", type="integer", minimum=1, maximum=90, hot_action="none"),
    "agent.confirmation_timeout_seconds": dict(label="Agent 确认等待时长（秒）", group="Agent", type="integer", minimum=1, hot_action="none"),
    "agent.grant_confirmation_timeout_seconds": dict(label="屏幕操作授权确认等待时长（秒）", group="Agent", type="integer", minimum=1, hot_action="none"),
    "agent.atomic_grant_minutes": dict(label="屏幕操作授权窗口（分钟）", group="Agent", type="integer", minimum=1, maximum=60, hot_action="none"),
    "agent.auto_diagnose_failures": dict(label="自动诊断流水线与内核故障（两条诊断规则的总开关）", group="Agent", type="boolean", hot_action="none"),
    # M27 账号数据的过期阈值：概要接口与注入段现读设置，改完立即生效。
    "agent.box_stale_days": dict(label="干员数据过期天数（距上次森空岛同步）", group="Agent", type="integer", minimum=1, maximum=365, hot_action="none"),
    "agent.depot_stale_days": dict(label="仓库识别过期天数", group="Agent", type="integer", minimum=1, maximum=60, hot_action="none"),
    # M45 W4 仓库写库闸门：上一轮条目数够多时，本轮相对上一轮跌得太狠就整轮不写
    # （与 agent.depot_min_items 同一族，服务写库时现读，改完立即生效）。
    "agent.depot_drop_min_prev": dict(label="仓库条目下降护栏生效的上一轮条数", group="Agent", type="integer", minimum=0, maximum=5000, hot_action="none"),
    "agent.depot_drop_ratio": dict(label="仓库条目相对上一轮的最低保留比例", group="Agent", type="number", minimum=0, maximum=1, hot_action="none"),
    # M29 作业研究：每天 04:00 的清理任务与授权卡片现读这三项，改完立即生效。
    "agent.copilot_retention_days": dict(label="作业草稿保留天数", group="Agent", type="integer", minimum=1, maximum=365, hot_action="none"),
    "agent.copilot_grant_minutes": dict(label="作业授权窗口（分钟）", group="Agent", type="integer", minimum=1, maximum=1440, hot_action="none"),
    "agent.copilot_attempt_timeout_minutes": dict(label="单次作业尝试超时（分钟）", group="Agent", type="integer", minimum=1, maximum=60, hot_action="none"),
    # M70 总控与 Worker：合并窗口、并发闸与并行委派上限，服务层每次现读，改完立即生效。
    "orchestrator.batch_window_ms": dict(label="总控合并窗口（毫秒）", group="Agent", type="integer", minimum=0, maximum=5000, hot_action="none", description="新消息到达后等这么久再处理，窗口内的消息合并成一轮"),
    "worker.max_concurrent": dict(label="同时运行的 Worker 上限", group="Agent", type="integer", minimum=1, maximum=16, hot_action="none"),
    "delegation.max_parallel": dict(label="并行委派上限", group="Agent", type="integer", minimum=1, maximum=8, hot_action="none", description="一批调用里同时执行的只读子代理个数"),
    # M71 设备租约：有效期与排队等待时长，服务每次取租约 / 续期时现读，改完立即生效。
    "lease.ttl_seconds": dict(label="设备租约有效期（秒）", group="Agent", type="integer", minimum=60, maximum=3600, hot_action="none"),
    "lease.wait_seconds": dict(label="设备租约排队等待（秒）", group="Agent", type="integer", minimum=0, maximum=600, hot_action="none"),
    # 下面两项只从 config.yaml 提供，不出现在设置 API 的响应里（见 CONFIG_ONLY_SETTING_KEYS）。
    # 这里保留元数据是为了让这张表覆盖全部 key，加 key 时不会漏掉归属与说明。
    "notify.vapid_private_key": dict(label="Web Push VAPID 私钥", group="通知", type="string", sensitive=True, hot_action="restart_required"),
    "notify.vapid_subject": dict(label="Web Push 联络邮箱", group="通知", type="string", hot_action="restart_required"),
    # 只在有反向代理（tailscale serve / Caddy）时才有意义；改完必须重启 uvicorn。
    "deployment.forwarded_allow_ips": dict(label="信任的代理来源", group="部署", type="string", hot_action="restart_required"),
    "deployment.cors_origins": dict(label="附加浏览器来源白名单", group="部署", type="string[]", hot_action="restart_required"),
    "deployment.cors_origin_regex": dict(label="附加来源正则", group="部署", type="string", hot_action="restart_required"),
    # M86 界面导航图：导航工具与执行器每次现读，改完立即生效。
    "nav.enabled": dict(label="界面导航图", group="Agent", type="boolean", hot_action="none"),
    "nav.learn": dict(label="导航时学习新跳转", group="Agent", type="boolean", hot_action="none"),
    "nav.max_steps": dict(label="一次导航最多步数", group="Agent", type="integer", minimum=1, maximum=50, hot_action="none"),
    # M89 示教录制：四端点与设备端点采集每次现读，关闭后返回 503。
    "demonstration.enabled": dict(label="示教录制", group="Agent", type="boolean", description="关闭后示教页与四个端点都不可用", hot_action="none"),
}

#: 只从 ``config.yaml`` / 环境变量提供的 key 不出现在设置 API 里（见 settings.py 的说明）。
_SCHEMA_KEYS = tuple(key for key in SETTING_KEYS if key not in CONFIG_ONLY_SETTING_KEYS)

SETTINGS_SCHEMA: tuple[dict[str, Any], ...] = tuple(
    {
        "key": key,
        **_FIELDS[key],
        "readonly": key in SETTING_READONLY_KEYS,
    }
    for key in _SCHEMA_KEYS
)


def schema_for(key: str) -> dict[str, Any]:
    """Return the metadata record for a known key."""
    for item in SETTINGS_SCHEMA:
        if item["key"] == key:
            return item
    raise KeyError(key)

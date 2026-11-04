"""M73 T1：六份专职 Worker 定义与加载测试（规格 §4.7、§6）。

工具名单逐字照 05 §3.2 的六行（顺序也照抄）；``diagnose`` 的 12 个安全查询与
恢复工具与 ``legacy_incident`` / ``runtime.INCIDENT_ALLOWED_TOOLS`` 是同一份。
"""

from __future__ import annotations

from maa_api.agent import runtime
from maa_api.agent.specs.registry import AgentSpecRegistry
from maa_api.agent.tools import build_registry

#: 05 §3.2 的六行：title、device、风险上限、委派与工具顺序。
WORKERS = {
    "daily_ops": {
        "title": "执行官",
        "device": "exclusive",
        "risk_ceiling": "consume",
        "delegates": ("navigator", "verifier"),
        "tools": (
            "get_system_status", "get_versions", "get_logs", "get_screenshot", "get_device_status",
            "reconnect_device", "list_devices", "list_task_types", "resolve_stage", "get_pipeline",
            "wait_pipeline", "list_pipelines", "get_queue", "submit_pipeline", "stop_pipeline",
            "set_task_params", "cancel_queued", "get_drop_stats", "back_to_home", "list_schedules",
            "list_infrast_plans", "read_knowledge", "get_activity_stages", "get_stage",
            "get_drop_rates", "send_screenshot", "send_chart", "check_confirmation", "ask_user",
            "update_plan", "write_note", "read_artifact", "report", "delegate",
            "hold_device", "release_device", "load_skill", "read_skill_file",
        ),
    },
    "copilot": {
        "title": "作战参谋",
        "device": "exclusive",
        "risk_ceiling": "consume",
        "delegates": ("navigator", "verifier", "researcher"),
        "tools": (
            "get_stage", "get_stage_level_data", "search_copilots", "save_copilot_draft",
            "run_copilot_attempt", "record_copilot_result", "list_copilots", "upload_copilot",
            "get_operator", "get_operator_box", "search_prts", "get_prts_page", "get_screenshot",
            "annotate_screenshot", "send_screenshot", "send_stage_map", "submit_pipeline",
            "get_pipeline", "wait_pipeline", "stop_pipeline", "back_to_home", "update_resource",
            "wait_update", "read_knowledge", "ask_user", "update_plan", "write_note",
            "read_artifact", "report", "delegate", "hold_device",
            "release_device", "load_skill", "read_skill_file",
        ),
    },
    "planner": {
        "title": "规划师",
        "device": "none",
        "risk_ceiling": "consume",
        "delegates": ("researcher",),
        "tools": (
            "get_operator_box", "get_depot", "plan_upgrade", "start_upgrade_plan",
            "list_upgrade_plans", "cancel_upgrade_plan", "get_skland_status",
            "sync_skland_training", "get_item", "get_drop_rates", "get_stage",
            "get_activity_stages", "get_gacha_pools", "get_building_skills",
            "get_infrast_candidates", "list_infrast_plans", "set_infrast_plan", "list_schedules",
            "create_schedule", "update_schedule", "delete_schedule", "update_user_profile",
            "get_operator", "read_knowledge", "send_chart", "send_item_icons", "ask_user",
            "update_plan", "write_note", "read_artifact", "report", "delegate",
            "load_skill", "read_skill_file",
        ),
    },
    "diagnose": {
        "title": "诊断员",
        "device": "exclusive",
        "risk_ceiling": "none",
        "delegates": ("recon",),
        "tools": (
            "get_system_status", "get_device_status", "get_logs", "get_screenshot", "get_pipeline",
            "get_queue", "list_pipelines", "get_drop_stats", "read_knowledge", "stop_pipeline",
            "reconnect_device", "back_to_home", "get_versions", "send_screenshot",
            "annotate_screenshot", "update_plan", "write_note", "read_artifact", "report", "delegate",
            "load_skill", "read_skill_file",
        ),
    },
    "analyst": {
        "title": "分析师",
        "device": "none",
        "risk_ceiling": "none",
        "delegates": ("researcher",),
        "tools": (
            "list_pipelines", "get_pipeline", "get_drop_stats", "get_logs", "get_drop_rates",
            "get_item", "get_stage", "get_operator_box", "get_depot", "list_upgrade_plans",
            "send_chart", "send_item_icons", "read_knowledge", "update_plan", "write_note",
            "read_artifact", "report", "delegate", "load_skill", "read_skill_file",
        ),
    },
    "ops": {
        "title": "运维",
        "device": "exclusive",
        "risk_ceiling": "destructive",
        "delegates": (),
        "tools": (
            "check_updates", "scan_proxy_channels", "update_core", "update_resource",
            "update_game", "wait_update", "restart_core", "get_versions", "get_system_status",
            "get_device_status", "reconnect_device", "list_devices", "get_logs", "read_knowledge",
            "ask_user", "update_plan", "write_note", "read_artifact", "report",
            "load_skill", "read_skill_file",
        ),
    },
}

#: ``diagnose`` 在 v2 诊断工具之外追加的八个（§4.7.4、05 §3.2）。
DIAGNOSE_EXTRA_TOOLS = (
    "get_versions",
    "send_screenshot",
    "annotate_screenshot",
    "update_plan",
    "write_note",
    "read_artifact",
    "report",
    "delegate",
)


def _load_specs():
    registry = AgentSpecRegistry(build_registry())
    registry.load()
    return {spec.name: spec for spec in registry.list()}


def test_six_workers_match_contract():
    specs = _load_specs()

    assert set(WORKERS) <= set(specs)
    for name, expected in WORKERS.items():
        spec = specs[name]
        assert spec.kind == "worker"
        assert spec.model_role == "worker"
        assert spec.title == expected["title"]
        assert spec.device == expected["device"]
        assert spec.risk_ceiling == expected["risk_ceiling"]
        assert spec.delegates == expected["delegates"]
        assert spec.tools == expected["tools"]
        # M78 §4.7：八个总控与 Worker 定义各加两个技能工具，版本 +1。
        assert spec.version == 3
        assert spec.max_steps == 0
        assert spec.output_schema is None
        assert spec.budget.max_cost_per_run == 0

    # diagnose 的前 12 个与 v2 诊断会话是同一份工具名单；M78 的技能工具在最后。
    diagnose = specs["diagnose"]
    assert set(diagnose.tools) == set(runtime.INCIDENT_ALLOWED_TOOLS) | set(
        DIAGNOSE_EXTRA_TOOLS
    ) | {"load_skill", "read_skill_file"}
    assert (
        diagnose.tools[
            len(runtime.INCIDENT_ALLOWED_TOOLS) : len(runtime.INCIDENT_ALLOWED_TOOLS)
            + len(DIAGNOSE_EXTRA_TOOLS)
        ]
        == DIAGNOSE_EXTRA_TOOLS
    )
    assert diagnose.tools[-2:] == ("load_skill", "read_skill_file")


def test_worker_tools_exist_in_registry():
    tool_names = {definition.name for definition in build_registry().list()}

    for name, expected in WORKERS.items():
        unknown = set(expected["tools"]) - tool_names
        assert not unknown, f"{name} 引用了未注册的工具：{sorted(unknown)}"

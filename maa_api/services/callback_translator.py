"""Translate raw MaaCore callback payloads into normalized task log records."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from maa_api.core.enums import Message
from maa_api.services.log_hub import LogRecord

__all__ = [
    "CONNECTION_MAP",
    "EXTRA_INFO_MAP",
    "SUB_TASK_START_MAP",
    "CallbackTranslator",
]


_CONNECT_FAILED = "模拟器连接失败 {details}"
CONNECTION_MAP: dict[str, str] = {
    "ConnectFailed": _CONNECT_FAILED,
    "ConnectFaild": _CONNECT_FAILED,  # compatible with older MaaCore releases
    "Connected": "模拟器连接成功",
    "UuidGot": "已获取到设备唯一码 {uuid}",
    "UnsupportedResolution": "模拟器分辨率不被支持 {details}",
    "ResolutionError": "分辨率获取错误 {details}",
    "ResolutionGot": "已获取到模拟器分辨率 {height}*{width}",
    "ResolutionInfo": "分辨率信息 {height}*{width}（{why}）",
    "Reconnecting": "模拟器连接断开(adb/模拟器异常) 正在重连 {details}",
    "Reconnected": "模拟器连接断开(adb/模拟器异常) 重连成功 {details}",
    "Disconnect": "模拟器连接断开(adb/模拟器异常) 重连失败 {details}",
    "ScreencapFailed": "截图失败(adb/模拟器异常) {details}",
    "FastestWayToScreencap": "最快截图耗时 {cost}ms",
    "TouchModeNotAvailable": "不支持的触控模式 {details}",
}

SUB_TASK_START_MAP: dict[str, str] = {
    "StartButton2": "已开始战斗 {exec_times} 次",
    "MedicineConfirm": "使用理智药",
    "ExpiringMedicineConfirm": "使用 48 小时内过期的理智药",
    "StoneConfirm": "碎石",
    "RecruitRefreshConfirm": "刷新标签",
    "RecruitConfirm": "确认招募",
    "RecruitNowConfirm": "使用加急许可",
    "ReportToPenguinStats": "汇报到企鹅数据统计",
    "ReportToYituliu": "汇报到一图流大数据",
    "InfrastDormDoubleConfirmButton": "请进行基建宿舍的二次确认",
    "StartExplore": "已开始探索 {exec_times} 次",
    "StageTraderInvestConfirm": "已投资源石锭",
    "StageTraderInvestSystemFull": "投资达到了游戏上限",
    "ExitThenAbandon": "已放弃本次探索",
    "MissionCompletedFlag": "战斗完成",
    "MissionFailedFlag": "战斗失败",
    "MissionFailedFlag2": "战斗失败",
    "StageTraderEnter": "节点：诡异行商",
    "StageSafeHouseEnter": "节点：安全的角落",
    "StageCombatDpsEnter": "关卡：普通作战",
    "StageEmergencyDps": "关卡：紧急作战",
    "StageDreadfulFoe": "关卡：险路恶敌",
}

EXTRA_INFO_MAP: dict[str, str] = {
    "RecruitTagsDetected": "公招识别结果：{tags}",
    "ReCruitSpecialTag": "识别到特殊Tag：{tag}",
    "RecruitResult": "{level} ⭐ Tags",
    "RecruitTagsRefreshed": "已刷新Tags",
    "EnterFacility": "当前设施：{facility} {index}",
    "StageInfo": "开始战斗：{name}",
    "StageInfoError": "关卡识别错误",
    "RoguelikeEvent": "事件：{name}",
    "SanityBeforeStage": "当前理智：{current_sanity}/{max_sanity}",
    "StageDrops": "{stars}⭐通关{stage_code} \n掉落统计: \n{drop_statistics}",
}

_CONNECTION_WARNING = {
    "ConnectFailed",
    "ConnectFaild",
    "UnsupportedResolution",
    "ResolutionError",
    "Disconnect",
    "ScreencapFailed",
    "TouchModeNotAvailable",
}


class CallbackTranslator:
    """Pure callback translator; it has no state or persistence responsibilities."""

    def translate(self, msg: Message, details: dict[str, Any]) -> list[LogRecord]:
        """Translate a callback payload, retaining its complete original contents."""

        try:
            message = Message(msg)
        except (TypeError, ValueError):
            return [
                self._record(
                    msg,
                    details,
                    f"未映射回调 Message.{msg}",
                    "DEBUG",
                )
            ]

        if message == Message.ConnectionInfo:
            return self._connection_info(message, details)
        if message == Message.SubTaskStart:
            return self._subtask_start(message, details)
        if message == Message.SubTaskExtraInfo:
            return self._subtask_extra_info(message, details)
        if message in (
            Message.TaskChainStart,
            Message.TaskChainCompleted,
            Message.TaskChainError,
            Message.TaskChainStopped,
        ):
            task_name = _first(details, "taskchain", "task", "name")
            content, level = {
                Message.TaskChainStart: (f"开始任务 [{task_name}]", "INFO"),
                Message.TaskChainCompleted: (f"完成任务 [{task_name}]", "INFO"),
                Message.TaskChainError: (f"任务失败 [{task_name}]", "ERROR"),
                Message.TaskChainStopped: (f"任务已中止 [{task_name}]", "WARNING"),
            }[message]
            return [self._record(message, details, content, level)]

        if message == Message.AllTasksCompleted:
            return [self._record(message, details, "全部任务已完成", "INFO")]
        if message in (Message.InternalError, Message.InitFailed):
            label = "MaaCore 内部错误" if message == Message.InternalError else "MaaCore 初始化失败"
            reason = _first(details, "message", "error", "what")
            suffix = f"：{reason}" if reason else ""
            return [self._record(message, details, label + suffix, "ERROR")]
        if message == Message.Destroyed:
            return [self._record(message, details, "MaaCore 实例已销毁", "DEBUG")]
        if message == Message.AsyncCallInfo:
            what = str(details.get("what", ""))
            content = f"异步调用完成 {what}".rstrip()
            return [self._record(message, details, content, "DEBUG")]
        if message == Message.SubTaskError:
            sub_details = _mapping(details.get("details"))
            task = str(sub_details.get("task", ""))
            return [self._record(message, details, f"子任务出错 [{task}]", "WARNING")]
        if message in (
            Message.TaskChainExtraInfo,
            Message.SubTaskCompleted,
            Message.SubTaskStopped,
        ):
            return [
                self._record(
                    message,
                    details,
                    f"{message.name} {details.get('what', '')}".rstrip(),
                    "DEBUG",
                )
            ]
        return [
            self._record(
                message,
                details,
                f"未映射回调 {message.name}.{details.get('what', '')}".rstrip("."),
                "DEBUG",
            )
        ]

    def _connection_info(
        self, message: Message, details: dict[str, Any]
    ) -> list[LogRecord]:
        what = str(details.get("what", ""))
        template = CONNECTION_MAP.get(what)
        if template is None:
            return [
                self._record(
                    message,
                    details,
                    f"未映射回调 {message.name}.{what}",
                    "DEBUG",
                )
            ]
        nested = _mapping(details.get("details"))
        values = {
            "details": str(details.get("details", {})),
            "uuid": details.get("uuid", ""),
            "height": nested.get("height", ""),
            "width": nested.get("width", ""),
            "cost": nested.get("cost", ""),
            "why": details.get("why", ""),
        }
        level = "DEBUG" if what == "ResolutionInfo" else (
            "WARNING" if what in _CONNECTION_WARNING else "INFO"
        )
        return [self._record(message, details, template.format_map(_SafeValues(values)), level)]

    def _subtask_start(
        self, message: Message, details: dict[str, Any]
    ) -> list[LogRecord]:
        nested = _mapping(details.get("details"))
        task = str(nested.get("task", ""))
        template = SUB_TASK_START_MAP.get(task)
        if template is None:
            return [
                self._record(
                    message,
                    details,
                    f"未映射回调 {message.name}.{task}",
                    "DEBUG",
                )
            ]
        return [
            self._record(
                message,
                details,
                template.format_map(_SafeValues(nested)),
                "INFO",
            )
        ]

    def _subtask_extra_info(
        self, message: Message, details: dict[str, Any]
    ) -> list[LogRecord]:
        what = str(details.get("what", ""))
        template = EXTRA_INFO_MAP.get(what)
        if template is None:
            return [
                self._record(
                    message,
                    details,
                    f"未映射回调 {message.name}.{what}",
                    "DEBUG",
                )
            ]
        nested = _mapping(details.get("details"))
        values: dict[str, Any] = dict(nested)
        values["stage_code"] = _mapping(nested.get("stage")).get("stageCode", "")
        values["drop_statistics"] = "\n".join(
            f"{item.get('itemName', '')}: {item.get('quantity', '')}(+{item.get('addQuantity', '')})"
            for item in _mapping_items(nested.get("stats"))
        )
        return [
            self._record(
                message,
                details,
                template.format_map(_SafeValues(values)),
                "INFO",
            )
        ]

    @staticmethod
    def _record(
        msg: Message | int,
        details: dict[str, Any],
        content: str,
        level: str,
    ) -> LogRecord:
        raw: dict[str, Any] = {"msg": int(msg), "details": details}
        # Keep the full details object and duplicate its top-level fields for
        # convenient inspection in log history (for example ``raw.what``).
        for key, value in details.items():
            if key != "details":
                raw.setdefault(key, value)
        return LogRecord(
            ts=time.time(),
            source="task",
            level=level,
            content=content,
            raw=raw,
        )


class _SafeValues(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return ""


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _mapping_items(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _first(details: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = details.get(key)
        if value not in (None, ""):
            return str(value)
    return ""

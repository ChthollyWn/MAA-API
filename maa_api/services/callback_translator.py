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
    "Reconnecting": "模拟器连接断开（ADB 或模拟器异常），正在重连 {details}",
    "Reconnected": "模拟器连接断开（ADB 或模拟器异常），重连成功 {details}",
    "Disconnect": "模拟器连接断开（ADB 或模拟器异常），重连失败 {details}",
    "ScreencapFailed": "截图失败（ADB 或模拟器异常）{details}",
    "FastestWayToScreencap": "最快截图耗时 {cost}ms",
    "ScreencapCost": "截图耗时 {average}ms（{minimum}/{maximum}）",
    "EmulatorFPS": "模拟器帧率 {fps}",
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
    "InfrastDormConfirmButton": "基建宿舍确认",
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
    "RecruitTagsSelected": "已选择Tags：{tags}",
    "RecruitNoPermit": "公招无招募许可",
    "NotEnoughStaff": "干员不足",
    "EnterFacility": "当前设施：{facility} {index}",
    "InfrastConfirmButton": "基建确认按钮：{facility} {index}",
    "ProductOfFacility": "基建产物：{product}（{facility} {index}）",
    "StageInfo": "开始战斗：{name}",
    "StageInfoError": "关卡识别错误",
    "RoguelikeEvent": "事件：{name}",
    "SanityBeforeStage": "当前理智：{current_sanity}/{max_sanity}",
    "FightTimes": "已战斗 {times_finished}/{series} 次，消耗理智 {sanity_cost}",
    "ExceededLimit": "已达到次数上限：{task} {exec_times}/{max_times}",
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

#: ``details`` 里带这个键，说明回调来自 ProcessTask 的**动作级步骤**（``ClickSelf`` /
#: ``DoNothing`` / ``Swipe`` …），是内核内部的每一次点击与匹配。一条日常流水线里
#: 这类回调数以千计，且对使用者没有语义（docs/04 §10.1 的预算是一条流水线
#: 1000–3000 条 ``maa_task`` 记录）。只保留具名子任务与插件回调，步骤级事件不产记录。
_STEP_MARKER = "action"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first_string(value: Any) -> str:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item:
                return item
        return ""
    return str(value) if isinstance(value, str) else ""


def _flatten(envelope: Mapping[str, Any]) -> dict[str, Any]:
    """把信封（``details`` / ``subtask`` / ``class`` 那一层）压成可读映射。

    内核把数据放在两层中的任意一层：内层 ``details.details.task`` 是任务名，
    外层 ``details.task`` 是 ProcessTask 的类型名，插件回调两层都没有 ``task``、
    只有信封上的 ``subtask``。内层同键优先：它是本次事件自己的数据。
    """

    outer = _mapping(envelope.get("details"))
    inner = _mapping(outer.get("details"))
    merged = {**envelope, **outer, **inner}
    # 两层都有 task 时取内层（任务名），否则外层的 ProcessTask 会顶掉它。
    name = inner.get("task") or outer.get("task")
    if name:
        merged["task"] = name
    return merged


def _subtask_name(payload: Mapping[str, Any]) -> str:
    """子任务名：``task`` 优先，插件回调回退顶层 ``subtask``。"""

    for key in ("task", "subtask"):
        name = str(payload.get(key, "") or "")
        if name:
            return name
    return ""


def _is_process_step(details: Mapping[str, Any]) -> bool:
    """ProcessTask 的动作级步骤：有 ``action`` 就说明这不是一条具名业务回调。"""

    return bool(details.get(_STEP_MARKER))


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
            return self._subtask_error(message, details)
        if message in (
            Message.TaskChainExtraInfo,
            Message.SubTaskCompleted,
            Message.SubTaskStopped,
        ):
            if message is Message.TaskChainExtraInfo:
                what = str(details.get("what", ""))
                return [
                    self._record(
                        message,
                        details,
                        f"TaskChainExtraInfo {what}".rstrip(),
                        "DEBUG",
                    )
                ]
            payload = _flatten(details)
            name = _subtask_name(payload) or str(details.get("what", ""))
            if not name:
                return [self._record(message, details, f"{message.name} 完成", "DEBUG")]
            # 动作级步骤（ProcessTask 的每一次点击）不产记录，见 _STEP_MARKER。
            if _is_process_step(payload):
                return []
            return [self._record(message, details, f"子任务完成：{name}", "DEBUG")]
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
            "fps": nested.get("fps", ""),
            "average": nested.get("avg", ""),
            "minimum": nested.get("min", ""),
            "maximum": nested.get("max", ""),
        }
        level = "DEBUG" if what == "ResolutionInfo" else (
            "WARNING" if what in _CONNECTION_WARNING else "INFO"
        )
        return [self._record(message, details, template.format_map(_SafeValues(values)), level)]

    def _subtask_start(
        self, message: Message, details: dict[str, Any]
    ) -> list[LogRecord]:
        payload = _flatten(details)
        # 具名确认动作（``InfrastDormConfirmButton`` …）在读到的任务名里；动作级步骤
        # 另有 ``details.action``（见 _STEP_MARKER）。模板命中优先于静音判定。
        name = _subtask_name(payload)
        template = SUB_TASK_START_MAP.get(name)
        if template is not None:
            return [
                self._record(
                    message,
                    details,
                    template.format_map(_SafeValues(payload)),
                    "INFO",
                )
            ]
        if _is_process_step(payload):
            return []
        content = f"子任务开始：{name}" if name else "子任务开始"
        return [self._record(message, details, content, "DEBUG")]

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
        values = _flatten(details)
        values["tags"] = _join(nested.get("tags") or details.get("tags"))
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

    def _subtask_error(
        self, message: Message, details: dict[str, Any]
    ) -> list[LogRecord]:
        """子任务出错：任务名可能在任何一层，逐层回退直到取到非空值。"""

        payload = _flatten(details)
        name = (
            str(payload.get("task", ""))
            or _first_string(details.get("first"))
            or str(details.get("subtask", ""))
        )
        content = f"子任务出错 [{name}]" if name else "子任务出错"
        return [self._record(message, details, content, "WARNING")]

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


def _mapping_items(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _join(value: Any) -> str:
    """公招标签：内核发数组，旧载荷发字符串，两种都要能渲染进模板。"""

    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value) if value is not None else ""


def _first(details: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = details.get(key)
        if value not in (None, ""):
            return str(value)
    return ""

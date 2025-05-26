import ctypes
import json
from datetime import datetime
from typing import Any, Dict

from maa_api.exception.response_exception import ResponseException
from maa_api.log import logger
from maa_api.model.core.asst import Asst
from maa_api.model.core.pipeline import TaskPipeline
from maa_api.model.core.task import TaskStatus
from maa_api.model.util.utils import Message


def _current_datetime():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def _current_time():
    return datetime.now().strftime('%H:%M:%S')

class CallbackHandler:
    def __init__(self, pipeline: TaskPipeline):
        self.task_map = {}
        self.pipeline = pipeline

    def register_task(self, asst_task_id, task_id):
        """注册任务映射"""
        self.task_map[asst_task_id] = task_id

    @staticmethod
    @Asst.CallBackType
    def handle_message(msg, details, arg):
        """回调主处理函数"""
        try:
            handler = ctypes.cast(arg, ctypes.py_object).value

            d = json.loads(details.decode('utf-8'))
            message_type = Message(msg)

            if message_type == Message.ConnectionInfo:
                handler.handle_connection_info(d)
            elif message_type in [Message.TaskChainStart, Message.TaskChainCompleted, Message.TaskChainError]:
                handler.handle_task_status(message_type, d)
            elif message_type == Message.SubTaskStart:
                handler.handle_subtask_start(d)
            elif message_type == Message.SubTaskExtraInfo:
                handler.handle_subtask_extra_info(d)

        except Exception as e:
            logger.error(f"回调处理异常: {e}", exc_info=True)

    def handle_connection_info(self, details: Dict[str, str]):
        """处理连接状态变化"""
        con_what = details.get('what', '')
        con_details = details.get('details', {})

        connection_map = {
            'ConnectFaild': f"模拟器连接失败 {con_details}",
            'Connected': f"模拟器连接成功",
            'UuidGot': f"已获取到设备唯一码 {details.get('uuid', '')}",
            'UnsupportedResolution': f"模拟器分辨率不被支持 {con_details}",
            'ResolutionError': f"分辨率获取错误 {con_details}",
            'ResolutionGot': f"已获取到模拟器分辨率 {con_details.get('height', '')}*{con_details.get('width', '')}",
            'Reconnecting': f"模拟器连接断开(adb/模拟器异常) 正在重连 {con_details}",
            'Reconnected': f"模拟器连接断开(adb/模拟器异常) 重连成功 {con_details}",
            'Disconnect': f"模拟器连接断开(adb/模拟器异常) 重连失败 {con_details}",
            'ScreencapFailed': f"截图失败(adb/模拟器异常) {con_details}",
            'FastestWayToScreencap': f"最快截图耗时 {con_details.get('cost', '')}ms",
            'TouchModeNotAvailable': f"不支持的触控模式 {con_details}"
        }

        log = connection_map.get(con_what)
        if log:
            self.pipeline.append_log(f'{_current_time()} {log}')

    def handle_task_status(self, msg: Message, details: Dict[str, str]):
        """处理任务状态变化"""
        task_id = details.get('taskid')
        taskchain = details.get('taskchain')
        task = self.pipeline.get_task(self.task_map.get(task_id))

        if not task:
            logger.warning(f"回调: 未知任务 ID - {task_id}")
            return

        if task.type_name != taskchain:
            raise ResponseException(f"任务链子任务不匹配 task_type={task.type_name} taskchain={taskchain}")

        # 更新任务状态
        if msg == Message.TaskChainStart:
            self.pipeline.update_task_status(self.task_map.get(task_id), TaskStatus.RUNNING)
            log = f'开始任务 [{task.task_name}]'
        elif msg == Message.TaskChainCompleted:
            self.pipeline.update_task_status(self.task_map.get(task_id), TaskStatus.COMPLETED)
            log = f'完成任务 [{task.task_name}]'
        elif msg == Message.TaskChainError:
            self.pipeline.update_task_status(self.task_map.get(task_id), TaskStatus.FAILED)
            log = f'任务失败 [{task.task_name}]'
        else:
            return

        self.pipeline.append_log(f'{_current_time()} {log}')

    def handle_subtask_start(self, d: Dict[str, Any]):
        """处理子任务开始"""
        sub_details = d.get('details', {})
        sub_task = sub_details.get('task', '')
        sub_task_info = {
            'StartButton2': f"已开始战斗 {sub_details.get('exec_times', '')} 次",
            'MedicineConfirm': '使用理智药',
            'ExpiringMedicineConfirm': '使用 48 小时内过期的理智药',
            'StoneConfirm': '碎石',
            'RecruitRefreshConfirm': '刷新标签',
            'RecruitConfirm': '确认招募',
            'RecruitNowConfirm': '使用加急许可',
            'ReportToPenguinStats': '汇报到企鹅数据统计',
            'ReportToYituliu': '汇报到一图流大数据',
            'InfrastDormDoubleConfirmButton': '请进行基建宿舍的二次确认',
            'StartExplore': f"已开始探索 {sub_details.get('exec_times', '')} 次",
            'StageTraderInvestConfirm': '已投资源石锭',
            'StageTraderInvestSystemFull': '投资达到了游戏上限',
            'ExitThenAbandon': '已放弃本次探索',
            'MissionCompletedFlag': '战斗完成',
            'MissionFailedFlag': '战斗失败',
            'MissionFailedFlag2': '战斗失败',
            'StageTraderEnter': '节点：诡异行商',
            'StageSafeHouseEnter': '节点：安全的角落',
            'StageCombatDpsEnter': '关卡：普通作战',
            'StageEmergencyDps': '关卡：紧急作战',
            'StageDreadfulFoe': '关卡：险路恶敌'
        }

        if sub_task in sub_task_info:
            log = sub_task_info[sub_task]
            self.pipeline.append_log(f'{_current_time()} {log}')

    def handle_subtask_extra_info(self, d: Dict[str, Any]):
        """处理子任务额外信息"""
        sub_what = d.get('what', '')
        sub_details = d.get('details', {})

        drop_statistics = '\n'.join(
            [f"{item.get('itemName', '')}: {item.get('quantity', '')}(+{item.get('addQuantity', '')})"
             for item in sub_details.get('stats', [])]
        )

        extra_info_map = {
            'RecruitTagsDetected': f"公招识别结果：{sub_details.get('tags', '')}",
            'ReCruitSpecialTag': f"识别到特殊Tag：{sub_details.get('tag', '')}",
            'RecruitResult': f"{sub_details.get('level', '')} ⭐ Tags",
            'RecruitTagsRefreshed': "已刷新Tags",
            'EnterFacility': f"当前设施：{sub_details.get('facility', '')} {sub_details.get('index', '')}",
            'StageInfo': f"开始战斗：{sub_details.get('name', '')}",
            'StageInfoError': "关卡识别错误",
            'RoguelikeEvent': f"事件：{sub_details.get('name', '')}",
            'SanityBeforeStage': f"当前理智：{sub_details.get('current_sanity', '')}/{sub_details.get('max_sanity', '')}",
            'StageDrops': f"{sub_details.get('stars', '')}⭐通关{sub_details.get('stage', {}).get('stageCode', '')} \n掉落统计: \n{drop_statistics}"
        }

        if sub_what in extra_info_map:
            log = extra_info_map[sub_what]
            self.pipeline.append_log(f'{_current_time()} {log}')
import os
import json
import pathlib
import urllib.request

from pydantic import BaseModel, PrivateAttr
from enum import Enum
from typing import Optional, Any
from datetime import datetime

from .utils import HttpUtils

from maa_api.model.asst import Asst
from maa_api.model.utils import Message, InstanceOptionType, Version
from maa_api.model.updater import Updater
from maa_api.config.config import Config
from maa_api.config.path_config import LOG_PATH, LIB_PATH
from maa_api.exception.response_exception import ResponseException
from maa_api.log import logger

TASK_PIPELINE_LOG_DIR = LOG_PATH / "task_pipeline"
TASK_PIPELINE_LOG_DIR.mkdir(parents=True, exist_ok=True)
MAA_LIB_DIR = LIB_PATH / 'maa'
MAA_LIB_DIR.mkdir(parents=True, exist_ok=True)
class TaskStatus(Enum):
    # 等待执行
    PENDING = "pending"
    # 正在执行
    RUNNING = "running"
    # 执行成功
    COMPLETED = "completed"
    # 执行失败
    FAILED = "failed"
    # 任务中断
    CANCELLED = "cancelled"

class Task(BaseModel):
    task_name:str
    type_name: str
    params: dict[str, Any]
    is_now: bool = True
    status: TaskStatus = TaskStatus.PENDING
    create_time: str

    def __init__(self, **data):
        data.setdefault('create_time', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        super().__init__(**data)
        if not self.task_name or not self.type_name:
            raise ResponseException("任务名称或任务类型不能为空")
        self.params = {k: v for k, v in self.params.items() if v is not None}

    def dict(self, *args, **kwargs):
        result = super().dict(*args, **kwargs)
        result['status'] = result['status'].value
        return result
        
class TaskPipelineStatus(str, Enum):
    # 命令尚未开始执行
    IDLE = "idle"
    # 命令正在执行
    RUNNING = "running"
    # 命令执行成功
    COMPLETED = "completed"
    # 命令执行失败
    FAILED = "failed"
    # 命令被取消
    CANCELLED = "cancelled"

class TaskPipeline(BaseModel):
    status: TaskPipelineStatus = TaskPipelineStatus.IDLE
    tasks: list[Task] = []
    logs: list[str] = []
    _task_dict: dict[str, Task] = {}
    _asst: Optional[Asst] = PrivateAttr(None)

    def running(self) -> bool:
        return self._asst.running() if self._asst else False
    
    def _check_runing(self) -> None:
        if self.running():
            raise ResponseException("流水线任务正在运行中，不允许多实例访问")
        
    def _to_serializable_dict(self):
        pipeline_dict = {
            'status': self.status.value,
            '_task_dict': {k: v.dict() for k, v in self._task_dict.items()},
            'tasks': [task.dict() for task in self.tasks],
            'logs': self.logs
        }
        return pipeline_dict
    
    def _save_pipeline(self):
        file_path = TASK_PIPELINE_LOG_DIR / "pipeline.json"
        task_pipeline_dict = self._to_serializable_dict()

        with file_path.open('w', encoding='utf-8') as f:
            json.dump(task_pipeline_dict, f, ensure_ascii=False, indent=4)

    def append_task(self, task: Task) -> None:
        self._check_runing()

        task_id = self._asst.append_task(task.type_name, task.params)
        if not task_id:
            raise ResponseException("添加任务失败")
        self._task_dict[task_id] = task
        self._save_pipeline()
        
    def start(self) -> bool:
        self._check_runing()

        # 任务执行前，将当前批次所有非pending任务标记为旧批次任务
        if self._task_dict:
            for task in self._task_dict.values():
                if task.is_now and task.status != TaskStatus.PENDING:
                    task.is_now = False
        # 清除旧任务缓存日志
        self.logs = []
        
        if not self._asst.start():
            raise ResponseException("执行任务失败")
        self.status = TaskPipelineStatus.RUNNING
        self._save_pipeline()
        return True
    
    def stop(self) -> bool:
        # 任务停止后，将当前批次所有非completed任务标记为cancelled
        if self._task_dict:
            for task in self._task_dict.values():
                if task.is_now and task.status != TaskStatus.COMPLETED:
                    task.status = TaskStatus.CANCELLED

        if not self._asst.stop():
            raise ResponseException("停止任务失败")
        self.status = TaskPipelineStatus.CANCELLED
        self._save_pipeline()
        return True

    def active_tasks(self):
        self.tasks = [task for task in self._task_dict.values() if task.is_now]
        self._save_pipeline()
        return self
    
task_pipeline = TaskPipeline()

def _task_log(msg: str):
    current_date = datetime.now().strftime('%Y-%m-%d')

    file_path = TASK_PIPELINE_LOG_DIR / f'task_pipeline_{current_date}.log'

    with file_path.open('a', encoding='utf-8') as f:
        f.write(msg)

def _current_time() -> str:
    return datetime.now().strftime('%H:%M:%S')

def _current_datetime() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

@Asst.CallBackType
def _callback(msg, details, arg):
    m = Message(msg)
    d = json.loads(details.decode('utf-8'))
    task_dict = task_pipeline._task_dict

    log = None

    # 连接信息
    if m == Message.ConnectionInfo:
        try:
            con_what = d.get('what', '')
            con_details = d.get('details', '')

            con_what_infos = {
                'ConnectFaild': f"模拟器连接失败 {con_details}",
                'Connected': f"模拟器连接成功",
                'UnsupportedResolution': f"模拟器分辨率不被支持 {con_details}",
                'ResolutionError': f"分辨率获取错误 {con_details}",
                'Reconnecting': f"模拟器连接断开(adb/模拟器异常) 正在重连 {con_details}",
                'Reconnected': f"模拟器连接断开(adb/模拟器异常) 重连成功 {con_details}",
                'Disconnect': f"模拟器连接断开(adb/模拟器异常) 重连失败 {con_details}",
                'ScreencapFailed': f"截图失败(adb/模拟器异常) {con_details}",
                'TouchModeNotAvailable': f"不支持的触控模式 {con_details}"
            }

            if con_what in con_what_infos:
                log = con_what_infos[con_what]
        except:
            log = f"Error: {m} {d}"

    # 开始任务
    if m == Message.TaskChainStart:
        task = task_dict[d['taskid']]
        if task.type_name != d['taskchain']:
            raise ResponseException(f"任务链子任务不匹配 task_type={task.type_name} taskchain={d['taskchain']}")
        task_pipeline._task_dict[d['taskid']].status = TaskStatus.RUNNING
        log = f'开始任务 [{task.task_name}]'

    # 完成任务
    if m == Message.TaskChainCompleted:
        task = task_dict[d['taskid']]
        if task.type_name != d['taskchain']:
            raise ResponseException(f"任务链子任务不匹配 task_type={task.type_name} taskchain={d['taskchain']}")
        task_pipeline._task_dict[d['taskid']].status = TaskStatus.COMPLETED
        log = f'完成任务 [{task.task_name}]'

    # 停止任务
    if m == Message.TaskChainStopped:
        task = task_dict[d['taskid']]
        log = f'停止任务 [{task.task_name}]'

    # 异常任务
    if m == Message.TaskChainError:
        task = task_dict[d['taskid']]
        if task.type_name != d['taskchain']:
            raise ResponseException(f"任务链子任务不匹配 task_type={task.type_name} taskchain={d['taskchain']}")
        task_pipeline._task_dict[d['taskid']].status = TaskStatus.FAILED
        log = f'任务失败 [{task.task_name}]'

    # 完成全部任务
    if m == Message.AllTasksCompleted:
        task_pipeline.status = TaskPipelineStatus.COMPLETED
        log = '已完成全部任务'

    # 开始原子任务
    if m == Message.SubTaskStart:
        try:
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
                # 'StageEncounterEnter': '节点：不期而遇',
                'StageCombatDpsEnter': '关卡：普通作战',
                'StageEmergencyDps': '关卡：紧急作战',
                'StageDreadfulFoe': '关卡：险路恶敌'
            }

            if sub_task in sub_task_info:
                log = sub_task_info[sub_task]
        except:
            log = f"Error: {m} {d}"

    # 原子任务额外信息
    if m == Message.SubTaskExtraInfo:
        try:
            sub_what = d.get('what', '')
            sub_details = d.get('details', {})

            drop_statistics = '\n'.join(
                [f"{item.get('itemName', '')}: {item.get('quantity', '')}(+{item.get('addQuantity', '')})" for item in sub_details.get('stats', [])]    
            )

            sub_task_extra_info = {
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

            if sub_what in sub_task_extra_info:
                log = sub_task_extra_info[sub_what]
        except :
            log = f"Error: {m} {d}"

    if log:
        task_pipeline.logs.append(f'{_current_time()} {log}')
        _task_log(f'{_current_datetime()} {log} \n')
    _task_log(f'{m} {d} {arg} \n')

def _init_asst():
    maa_core_path = Config.get_config('app', 'maa_core_path')
    if not maa_core_path:
        maa_core_path = MAA_LIB_DIR
    maa_core_path = os.path.expanduser(maa_core_path)
    path = pathlib.Path(maa_core_path).resolve()

    # 更新maa版本
    logger.info("开始校验 MAA 版本")
    Updater(path, Version.Stable).update()

    # 加载核心资源
    logger.info("开始加载 MAA 核心资源")
    Asst.load(path=path)
    logger.info("MAA 核心资源加载成功")

    # 加载活动资源
    logger.info("开始加载版本活动资源")
    ota_tasks_url = 'https://ota.maa.plus/MaaAssistantArknights/api/resource/tasks.json'
    ota_tasks_path = path / 'cache' / 'resource' / 'tasks.json'
    ota_tasks_path.parent.mkdir(parents=True, exist_ok=True)
    resp = HttpUtils.get(ota_tasks_url)
    with open(ota_tasks_path, 'w', encoding='utf-8') as f:
        f.write(resp.text)
    Asst.load(path=path, incremental_path=path / 'cache')
    logger.info("版本活动资源加载成功")

    # 配置回调函数
    asst = Asst(callback=_callback)
    # 触控方案配置
    asst.set_instance_option(InstanceOptionType.touch_type, 'maatouch')
    # 暂停下干员
    asst.set_instance_option(InstanceOptionType.deployment_with_pause, '1')

    adb_path = Config.get_config('adb', 'path')
    if not adb_path:
        # 如果adb路径未设置，使用Path环境变量
        adb_path = 'adb'
    adb_address = Config.get_config('adb', 'address')
    if not asst.connect(adb_path, adb_address):
        raise RuntimeError(f"MAA ADB 连接失败 path={adb_path} address={adb_address}")
        
    return asst

task_pipeline._asst = _init_asst()

class StartUpTask(Task):
    def __init__(self,
                enable: bool = None,
                client_type: str = None,
                start_game_enabled: bool = None,
                account_name: str = None):
        """
        初始化开始唤醒任务。

        参数:
        enable (bool): 是否启用本任务，可选，默认为 True。
        client_type (str): 客户端版本，可选，默认为空。
            - 可选值包括: "Official", "Bilibili", "txwy", "YoStarEN", "YoStarJP", "YoStarKR"。

        start_game_enabled (bool): 是否自动启动客户端，可选，默认启动 (True)。

        account_name (str): 切换账号，可选，默认不切换。
            - 仅支持切换至已登录的账号，使用登录名进行查找，保证输入内容在所有已登录账号中唯一即可。
            - 官服示例: 123****4567，可输入 123****4567、4567、123、3****4567。
            - B服示例: 张三，可输入 张三、张、三。
        """
        
        params = {
            "enable": enable,
            "client_type": client_type,
            "start_game_enabled": start_game_enabled,
            "account_name": account_name
        }

        super().__init__(task_name="开始唤醒", type_name = "StartUp", params=params)

class CloseDownTask(Task):
    def __init__(self,
                enable: bool = None,
                client_type: str | None = None):
        
        """
        初始化关闭游戏任务。

        参数:
        enable (bool): 是否启用本任务，可选，预设为 True。

        client_type (str): 客户端版本，必选，填空则不执行。
            - 可选值包括: "Official", "Bilibili", "txwy", "YoStarEN", "YoStarJP", "YoStarKR"。
        """
        
        params = {
            "enable": enable,
            "client_type": client_type
        }

        super().__init__(task_name="关闭游戏", type_name = "CloseDown", params=params)

class FightTask(Task):
    def __init__(self,
                 enable: bool = None,
                 stage: str = None,
                 medicine: int = None,
                 expiring_medicine: int = None,
                 stone: int = None,
                 times: int = None,
                 series: int = None,
                 drops: dict = None,
                 report_to_penguin: bool = None,
                 penguin_id: str = None,
                 server: str = None,
                 client_type: str = None,
                 DrGrandet: bool = None):
        """
        初始化刷理智任务。

        参数:
        enable (bool): 是否启用本任务，可选，默认为 True。

        stage (str): 关卡名，可选，默认为空，识别当前/上次的关卡。不支持运行中设置。
            - 支持全部主线关卡，如 "1-7"、"S3-2"等。
            - 可在关卡结尾输入 Normal/Hard 表示需要切换标准与磨难难度。
            - 剿灭作战，必须输入 "Annihilation"。
            - 当期 SS 活动 后三关，必须输入完整关卡编号。

        medicine (int): 最大使用理智药数量，可选，默认 0。
        expiring_medicine (int): 最大使用 48 小时内过期理智药数量，可选，默认 0。
        stone (int): 最大吃石头数量，可选，默认 0。
        times (int): 指定次数，可选，默认无穷大。
        series (int): 连战次数，可选，1~6。
        drops (dict): 指定掉落数量，可选，默认不指定。
            - key 为 item_id，value 为数量。key 可参考 resource/item_index.json 文件。
            - 是或的关系，即任一达到即停止任务。

        report_to_penguin (bool): 是否汇报企鹅数据，可选，默认 False。
        penguin_id (str): 企鹅数据汇报 id, 可选，默认为空。仅在 report_to_penguin 为 True 时有效。
        server (str): 服务器，可选，默认 "CN"，会影响掉落识别及上传。
            - 可选值包括: "CN", "US", "JP", "KR"。

        client_type (str): 客户端版本，可选，默认为空。用于游戏崩溃时重启并连回去继续刷，若为空则不启用该功能。
            - 可选值包括: "Official", "Bilibili", "txwy", "YoStarEN", "YoStarJP", "YoStarKR"。

        DrGrandet (bool): 节省理智碎石模式，可选，默认 False，仅在可能产生碎石效果时生效。
            - 在碎石确认界面等待，直到当前的 1 点理智恢复完成后再立刻碎石。
        """
        
        params = {
            "enable": enable,
            "stage": stage,
            "medicine": medicine,
            "expiring_medicine": expiring_medicine,
            "stone": stone,
            "times": times,
            "series": series,
            "drops": drops,
            "report_to_penguin": report_to_penguin,
            "penguin_id": penguin_id,
            "server": server,
            "client_type": client_type,
            "DrGrandet": DrGrandet
        }

        super().__init__(task_name="刷理智", type_name="Fight", params=params)


class RecruitTask(Task):
    def __init__(self,
                 enable: bool = None,
                 refresh: bool = None,
                 select: list[int] = None,
                 confirm: list[int] = None,
                 first_tags: list[str] = None,
                 extra_tags_mode: int = None,
                 times: int = None,
                 set_time: bool = None,
                 expedite: bool = None,
                 expedite_times: int = None,
                 skip_robot: bool = None,
                 recruitment_time: dict = None,
                 report_to_penguin: bool = None,
                 penguin_id: str = None,
                 report_to_yituliu: bool = None,
                 yituliu_id: str = None,
                 server: str = None):
        """
        初始化公开招募任务。

        参数:
        enable (bool): 是否启用本任务，可选，默认为 True。

        refresh (bool): 是否刷新三星 Tags，可选，默认 False。

        select (list[int]): 会去点击标签的 Tag 等级，必选。

        confirm (list[int]): 会去点击确认的 Tag 等级，必选。若仅公招计算，可设置为空数组。

        first_tags (list[str]): 首选 Tags，仅在 Tag 等级为 3 时有效。可选，默认为空。
            - 当 Tag 等级为 3 时，会尽可能多地选择这里的 Tags（如果有）。
            - 是强制选择，会忽略所有“让 3 星 Tag 不被选择”的设置。

        extra_tags_mode (int): 选择更多的 Tags，可选，默认为 0。
            - 0: 默认行为。
            - 1: 选 3 个 Tags，即使可能冲突。
            - 2: 如果可能，同时选择更多的高星 Tag 组合，即使可能冲突。

        times (int): 招募多少次，可选，默认 0。若仅公招计算，可设置为 0。

        set_time (bool): 是否设置招募时限。仅在 times 为 0 时生效，可选，默认 True。

        expedite (bool): 是否使用加急许可，可选，默认 False。

        expedite_times (int): 加急次数，仅在 expedite 为 True 时有效。
            - 可选，默认无限使用（直到 times 达到上限）。

        skip_robot (bool): 是否在识别到小车词条时跳过，可选，默认跳过 (True)。

        recruitment_time (dict): Tag 等级（大于等于 3）和对应的希望招募时限，单位为分钟，默认值都为 540（即 09:00:00）。

        report_to_penguin (bool): 是否汇报企鹅数据，可选，默认 False。
        penguin_id (str): 企鹅数据汇报 id, 可选，默认为空。仅在 report_to_penguin 为 True 时有效。

        report_to_yituliu (bool): 是否汇报一图流数据，可选，默认 False。
        yituliu_id (str): 一图流汇报 id, 可选，默认为空。仅在 report_to_yituliu 为 True 时有效。

        server (str): 服务器，可选，默认 "CN"，会影响上传。
            - 可选值包括: "CN", "US", "JP", "KR"。
        """
        
        params = {
            "enable": enable,
            "refresh": refresh,
            "select": select,
            "confirm": confirm,
            "first_tags": first_tags,
            "extra_tags_mode": extra_tags_mode,
            "times": times,
            "set_time": set_time,
            "expedite": expedite,
            "expedite_times": expedite_times,
            "skip_robot": skip_robot,
            "recruitment_time": recruitment_time,
            "report_to_penguin": report_to_penguin,
            "penguin_id": penguin_id,
            "report_to_yituliu": report_to_yituliu,
            "yituliu_id": yituliu_id,
            "server": server
        }

        super().__init__(task_name="自动公招", type_name="Recruit", params=params)

class InfrastTask(Task):
    def __init__(self,
                 enable: bool = None,
                 mode: int = None,
                 facility: list[str] = None,
                 drones: str = None,
                 threshold: float = None,
                 replenish: bool = False,
                 dorm_notstationed_enabled: bool = None,
                 dorm_trust_enabled: bool = None,
                 failename: str = None,
                 plan_index: int = None):
        """
        初始化基建换班任务

        参数:
        enable (bool): 是否启用本任务，可选，默认为 True。
        mode (int): 换班工作模式，可选，默认 0。
            - 0: 默认换班模式，单设施最优解。
            - 10000: 自定义换班模式，读取用户配置。

        facility (list[str]): 要换班的设施（有序），必选。不支持运行中设置。
            设施名选项包括: "Mfg", "Trade", "Power", "Control", "Reception", "Office", "Dorm"。

        drones (str): 无人机用途，可选项，默认 "_NotUse"。
            - mode == 10000 时该字段无效（会被忽略）。
            - 可选值包括: "_NotUse", "Money", "SyntheticJade", "CombatRecord", "PureGold", "OriginStone", "Chip"。

        threshold (float): 工作心情阈值，可选，取值范围 [0, 1.0]，默认 0.3。
            - mode == 10000 时该字段仅针对 "autofill" 有效。

        replenish (bool): 贸易站“源石碎片”是否自动补货，可选，默认 False。

        dorm_notstationed_enabled (bool): 是否启用宿舍“未进驻”选项，可选，默认 False。
        dorm_trust_enabled (bool): 是否将宿舍剩余位置填入信赖未满干员，可选，默认 False。

        以下参数仅在 mode == 10000 时生效，否则会被忽略:
        filename (str): 自定义配置路径，必选。不支持运行中设置。
        plan_index (int): 使用配置中的方案序号，必选。不支持运行中设置。
        """
        
        params = {
            "enable": enable,
            "mode": mode,
            "facility": facility,
            "drones": drones,
            "threshold": threshold,
            "replenish": replenish,
            "dorm_notstationed_enabled": dorm_notstationed_enabled,
            "dorm_trust_enabled": dorm_trust_enabled,
            "failename": failename,
            "plan_index": plan_index
        }

        super().__init__(task_name="基建换班", type_name="Infrast", params=params)

class MallTask(Task):
    def __init__(self,
                 enable: bool = None,
                 shopping: bool = None,
                 buy_first: list[str] = None,
                 blacklist: list[str] = None,
                 force_shopping_if_credit_full: bool = None,
                 only_buy_discount: bool = None,
                 reserve_max_credit: bool = None):
        """
        初始化获取信用及商店购物任务。

        参数:
        enable (bool): 是否启用本任务，可选，默认为 True。

        shopping (bool): 是否购物，可选，默认 False。不支持运行中设置。

        buy_first (list[str]): 优先购买列表，可选。不支持运行中设置。
            - 商品名，如 "招聘许可"、"龙门币" 等。

        blacklist (list[str]): 黑名单列表，可选。不支持运行中设置。
            - 商品名，如 "加急许可"、"家具零件" 等。

        force_shopping_if_credit_full (bool): 是否在信用溢出时无视黑名单，默认为 True。

        only_buy_discount (bool): 是否只购买折扣物品，只作用于第二轮购买，默认为 False。

        reserve_max_credit (bool): 是否在信用点低于300时停止购买，只作用于第二轮购买，默认为 False。
        """
        
        params = {
            "enable": enable,
            "shopping": shopping,
            "buy_first": buy_first,
            "blacklist": blacklist,
            "force_shopping_if_credit_full": force_shopping_if_credit_full,
            "only_buy_discount": only_buy_discount,
            "reserve_max_credit": reserve_max_credit
        }

        super().__init__(task_name="获取信用及购物", type_name="Mall", params=params)


class AwardTask(Task):
    def __init__(self,
                 enable: bool = None,
                 award: bool = None,
                 mail: bool = None,
                 recruit: bool = None,
                 orundum: bool = None,
                 mining: bool = None,
                 specialaccess: bool = None):
        """
        初始化领取各种奖励任务。

        参数:
        enable (bool): 是否启用本任务，可选，默认为 True。

        award (bool): 领取每日/每周任务奖励，默认为 True。

        mail (bool): 领取所有邮件奖励，默认为 False。

        recruit (bool): 领取限定池子赠送的每日免费单抽，默认为 False。

        orundum (bool): 领取幸运墙的合成玉奖励，默认为 False。

        mining (bool): 领取限时开采许可的合成玉奖励，默认为 False。

        specialaccess (bool): 领取五周年赠送的月卡奖励，默认为 False。
        """
        
        params = {
            "enable": enable,
            "award": award,
            "mail": mail,
            "recruit": recruit,
            "orundum": orundum,
            "mining": mining,
            "specialaccess": specialaccess
        }

        super().__init__(task_name="领取奖励", type_name="Award", params=params)


class RoguelikeTask(Task):
    def __init__(self,
                 enable: bool = None,
                 theme: str = None,
                 mode: int = None,
                 squad: str = None,
                 roles: str = None,
                 core_char: str = None,
                 use_support: bool = None,
                 use_nonfriend_support: bool = None,
                 starts_count: int = None,
                 difficulty: int = None,
                 stop_at_final_boss: bool = None,
                 investment_enabled: bool = None,
                 investments_count: int = None,
                 stop_when_investment_full: bool = None,
                 start_with_elite_two: bool = None,
                 only_start_with_elite_two: bool = None,
                 refresh_trader_with_dice: bool = None,
                 first_floor_foldartal: str = None,
                 start_foldartal_list: list[str] = None,
                 use_foldartal: bool = None,
                 check_collapsal_paradigms: bool = None,
                 double_check_collapsal_paradigms: bool = None,
                 expected_collapsal_paradigms: list[str] = None):
        """
        初始化无限刷肉鸽任务。

        参数:
        enable (bool): 是否启用本任务，可选，默认值 True。

        theme (str): 主题，可选，默认值 "Phantom"。
            - 可选值包括: "Phantom", "Mizuki", "Sami", "Sarkaz"。

        mode (int): 模式，可选，默认值 0。
            - 0: 刷分/奖励点数，尽可能稳定地打更多层数。
            - 1: 刷源石锭，第一层投资完就退出。
            - 2: 【已弃用】兼顾模式 0 与 1，投资过后再退出，没有投资就继续往后打。
            - 3: 开发中...
            - 4: 凹开局，先在 0 难度下到达第三层后重开，再到指定难度下凹开局奖励，若不为热水壶或希望则回到 0 难度下重新来过；
                 若在 Phantom 主题下则不切换难度，仅在当前难度下尝试到达第三层、重开、凹开局。
            - 5: 刷坍缩范式；仅适用于 Sami 主题；通过战斗漏怪等方式加快坍缩值积累，
                 若遇到的第一个的坍缩范式在 expected_collapsal_paradigms 列表中则停止任务，否则重开。

        squad (str): 开局分队名，可选，默认值 "指挥分队"。

        roles (str): 开局职业组，可选，默认值 "取长补短"。

        core_char (str): 开局干员名，可选；仅支持单个干员**中文名**，无论区服；若留空或设置为空字符串 "" 则根据练度自动选择。

        use_support (bool): 开局干员是否为助战干员，可选，默认值 False。

        use_nonfriend_support (bool): 是否可以是非好友助战干员，可选，默认值 False；仅在 use_support 为 True 时有效。

        starts_count (int): 开始探索的次数，可选，默认值 INT_MAX；达到后自动停止任务。

        difficulty (int): 指定难度等级，可选，默认值 0；仅适用于**除 Phantom 以外**的主题；
            - 若未解锁难度，则会选择当前已解锁的最高难度。

        stop_at_final_boss (bool): 是否在第 5 层险路恶敌节点前停止任务，可选，默认值 False；仅适用于**除 Phantom 以外**的主题。

        investment_enabled (bool): 是否投资源石锭，可选，默认值 True。

        investments_count (int): 投资源石锭的次数，可选，默认值 INT_MAX；达到后自动停止任务。

        stop_when_investment_full (bool): 是否在投资到达上限后自动停止任务，可选，默认值 False。

        start_with_elite_two (bool): 是否在凹开局的同时凹干员精二直升，可选，默认值 False；仅适用于模式 4。

        only_start_with_elite_two (bool): 是否只凹开局干员精二直升而忽视其他开局条件，可选，默认值 False；
            - 仅在模式为 4 且 start_with_elite_two 为 True 时有效。

        refresh_trader_with_dice (bool): 是否用骰子刷新商店购买特殊商品，可选，默认值 False；仅适用于 Mizuki 主题，用于刷指路鳞。

        first_floor_foldartal (str): 希望在第一层远见阶段得到的密文版，可选；仅适用于 Sami 主题，不限模式；若成功凹到则停止任务。

        start_foldartal_list (list[str]): 凹开局时希望在开局奖励阶段得到的密文板，可选，默认值 []；仅主题为 Sami 且模式为 4 时有效；
            - 仅当开局拥有列表中所有的密文板时才算凹开局成功；
            - 注意，此参数须与 “生活至上分队” 同时使用，其他分队在开局奖励阶段不会获得密文板。

        use_foldartal (bool): 是否使用密文板，模式 5 下默认值 False，其他模式下默认值 True；仅适用于 Sami 主题。

        check_collapsal_paradigms (bool): 是否检测获取的坍缩范式，模式 5 下默认值 True，其他模式下默认值 False。

        double_check_collapsal_paradigms (bool): 是否执行坍缩范式检测防漏措施，模式 5 下默认值 True，其他模式下默认值 False；
            - 仅在主题为 Sami 且 check_collapsal_paradigms 为 True 时有效。

        expected_collapsal_paradigms (list[str]): 希望触发的坍缩范式，默认值 ["目空一些", "睁眼瞎", "图像损坏", "一抹黑"]；
            - 仅在主题为 Sami 且模式为 5 时有效。
        """
        
        params = {
            "enable": enable,
            "theme": theme,
            "mode": mode,
            "squad": squad,
            "roles": roles,
            "core_char": core_char,
            "use_support": use_support,
            "use_nonfriend_support": use_nonfriend_support,
            "starts_count": starts_count,
            "difficulty": difficulty,
            "stop_at_final_boss": stop_at_final_boss,
            "investment_enabled": investment_enabled,
            "investments_count": investments_count,
            "stop_when_investment_full": stop_when_investment_full,
            "start_with_elite_two": start_with_elite_two,
            "only_start_with_elite_two": only_start_with_elite_two,
            "refresh_trader_with_dice": refresh_trader_with_dice,
            "first_floor_foldartal": first_floor_foldartal,
            "start_foldartal_list": start_foldartal_list,
            "use_foldartal": use_foldartal,
            "check_collapsal_paradigms": check_collapsal_paradigms,
            "double_check_collapsal_paradigms": double_check_collapsal_paradigms,
            "expected_collapsal_paradigms": expected_collapsal_paradigms
        }

        super().__init__(task_name="自动肉鸽", type_name="Roguelike", params=params)

class ReclamationTask(Task):
    def __init__(self,
                 enable: bool = None,
                 theme: str = None,
                 mode: int = None,
                 tools_to_craft: list[str] = None,
                 increment_mode: int = None,
                 num_craft_batches: int = None):
        """
        初始化生息演算任务

        参数:
        enable (bool):

        theme (str): 主题，可选项。默认为 1
            - Fire  - *沙中之火*
            - Tales - *沙洲遗闻*

        mode (int): 模式，可选项。默认为 0
            - 0: 刷分与建造点，进入战斗直接退出
            - 1: 沙中之火：刷赤金，联络员买水后基地锻造；
                沙洲遗闻：自动制造物品并读档刷货币

        tools_to_craft (list[str]): 自动制造的物品，可选项，默认为荧光棒

        increment_mode (int): 点击类型，可选项。默认为0
            - 0: 连点
            - 1: 长按
        
        num_craft_batches (int): 单次最大制造轮数，可选。默认为 16
        """

        params = {
            enable: enable,
            theme: theme,
            mode: mode,
            tools_to_craft: tools_to_craft,
            increment_mode: increment_mode,
            num_craft_batches: num_craft_batches
        }

        super().__init__(task_name="生息演算", type_name="Reclamation", params=params)

import json
import subprocess
import asyncio
import pathlib

from asyncio.subprocess import Process
from pydantic import BaseModel, PrivateAttr
from enum import Enum
from typing import Optional, Any

from maa_core.asst import Asst
from maa_core.utils import Message, InstanceOptionType
from maa_api.config.config import Config
from maa_api.config.path_config import TEMP_PATH

TASK_PIPELINE_FILE = TEMP_PATH / "task_pipeline.log"
TASK_PIPELINE_FILE.touch(exist_ok=True)

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
    status: TaskStatus = TaskStatus.PENDING

    def __init__(self, **data):
        super().__init__(**data)
        if not self.task_name or not self.type_name:
            raise ValueError("Task must have a name and a type")
        
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
        

@Asst.CallBackType
def _callback(msg, details, arg):
    m = Message(msg)
    d = json.loads(details.decode('utf-8'))
    # tasks = task_pipeline.tasks

    # # 任务开始
    # if m == Message.TaskChainStart:
    #     task = tasks[d['taskid']]
    #     if task.type_name != d['taskchain']:
    #         raise RuntimeError(f"任务链子任务不匹配 task_type={task.type_name} taskchain={d['taskchain']}")
    #     task_pipeline.tasks[d['taskid']].status = TaskStatus.RUNNING
    #     print(f'开始任务 [{task.task_name}]')

    # # 任务结束
    # if m == Message.TaskChainCompleted:
    #     task = tasks[d['taskid']]
    #     if task.type_name != d['taskchain']:
    #         raise RuntimeError(f"任务链子任务不匹配 task_type={task.type_name} taskchain={d['taskchain']}")
    #     task_pipeline.tasks[d['taskid']].status = TaskStatus.COMPLETED
    #     print(f'完成任务 [{task.task_name}]')

    # if m == Message.AllTasksCompleted:
    #     task_pipeline.status = TaskPipelineStatus.COMPLETED
    #     print('已完成全部任务')

    print(m, d, arg)

def _init_asst():
    # 加载核心资源
    maa_core_path = Config.get_config('app', 'maa_core_path')
    path = pathlib.Path(maa_core_path).resolve()
    Asst.load(path=path)

    # 配置回调函数
    asst = Asst(callback=_callback)
    # 触控方案配置
    asst.set_instance_option(InstanceOptionType.touch_type, 'maatouch')
    # 暂停下干员
    asst.set_instance_option(InstanceOptionType.deployment_with_pause, '1')

    adb_address = Config.get_config('adb', 'address')
    if not asst.connect('adb.exe', adb_address):
        raise RuntimeError("MAA ADB 连接失败")
        
    return asst

class TaskPipeline(BaseModel):
    status: TaskPipelineStatus = TaskPipelineStatus.IDLE
    tasks: list[Task] = []
    _asst: Optional[Asst] = PrivateAttr(None)

    def __init__(self):
        super().__init__()

        # 加载核心资源
        maa_core_path = Config.get_config('app', 'maa_core_path')
        path = pathlib.Path(maa_core_path).resolve()
        Asst.load(path=path)

        # 配置回调函数
        asst = Asst(callback=self._callback)
        # 触控方案配置
        asst.set_instance_option(InstanceOptionType.touch_type, 'maatouch')
        # 暂停下干员
        asst.set_instance_option(InstanceOptionType.deployment_with_pause, '1')

        adb_address = Config.get_config('adb', 'address')
        if not asst.connect('adb.exe', adb_address):
            raise RuntimeError("MAA ADB 连接失败")
        
        self._asst = asst

    @Asst.CallBackType
    def _callback(self, msg, details, arg):
        m = Message(msg)
        d = json.loads(details.decode('utf-8'))

        tasks = self.tasks

        # 任务开始
        if m == Message.TaskChainStart:
            task = tasks[d['taskid']]
            if task.type_name != d['taskchain']:
                raise RuntimeError(f"任务链子任务不匹配 task_type={task.type_name} taskchain={d['taskchain']}")
            self.tasks[d['taskid']].status = TaskStatus.RUNNING
            print(f'开始任务 [{task.task_name}]')

        # 任务结束
        if m == Message.TaskChainCompleted:
            task = tasks[d['taskid']]
            if task.type_name != d['taskchain']:
                raise RuntimeError(f"任务链子任务不匹配 task_type={task.type_name} taskchain={d['taskchain']}")
            self.tasks[d['taskid']].status = TaskStatus.COMPLETED
            print(f'完成任务 [{task.task_name}]')

        if m == Message.AllTasksCompleted:
            self.status = TaskPipelineStatus.COMPLETED
            print('已完成全部任务')

    def running(self) -> bool:
        return self._asst.running() if self._asst else False

    def append_task(self, task: Task) -> bool:
        self.tasks.append(task)
        return self._asst.append_task(task.type_name, task.params)
        
    def start(self) -> bool:
        self.status = TaskPipelineStatus.RUNNING
        return self._asst.start()
    
task_pipeline = TaskPipeline()

"""启动游戏客户端

client_type:
    客户端版本，可选，默认为空
    选项："Official" | "Bilibili" | "txwy" | "YoStarEN" | "YoStarJP" | "YoStarKR"

account_name:
    切换账号，可选，默认不切换
    仅支持切换至已登录的账号，使用登录名进行查找，保证输入内容在所有已登录账号唯一即可
    官服：123****4567，可输入 123****4567、4567、123、3****4567
    B服：张三，可输入 张三、张、三
"""
class StartUpTask(Task):
    def __init__(self,
                enable: bool = True,
                client_type: str | None = None,
                start_game_enabled: bool = True,
                account_name: str | None = None):
        
        params = {
            "enable": enable,
            "client_type": client_type,
            "start_game_enabled": start_game_enabled,
            "account_name": account_name
        }

        super().__init__(task_name="开始唤醒", type_name = "StartUp", params=params)

"""关闭游戏客户端

client_type:
    客户端版本，可选，默认为空
    选项："Official" | "Bilibili" | "txwy" | "YoStarEN" | "YoStarJP" | "YoStarKR"
"""
class CloseDownTask(Task):
    def __init__(self,
                enable: bool = True,
                client_type: str | None = None):
        
        params = {
            "enable": enable,
            "client_type": client_type
        }

        super().__init__(task_name="关闭游戏", type_name = "CloseDown", params=params)
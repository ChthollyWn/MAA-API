import subprocess
import asyncio

from asyncio.subprocess import Process
from pydantic import BaseModel, PrivateAttr
from enum import Enum
from typing import Optional

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
    name:str
    command:str
    status: TaskStatus = TaskStatus.PENDING

    def __init__(self, **data):
        super().__init__(**data)
        if not self.name or not self.command:
            raise ValueError("Task must have a name and a command")
        
    async def run(self):
        if not self.command:
            self.status = TaskStatus.FAILED
            raise ValueError("Command is not set")
        
        self.status = TaskStatus.RUNNING
        try:
            process = await asyncio.create_subprocess_shell(
                self.command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            return process
        except Exception as e:
            self.status = TaskStatus.FAILED
            raise RuntimeError(f"Task {self.name} failed: {e}")
        
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
    _process: Optional[Process] = PrivateAttr(None)

    async def _stream_output(self, task_name: str):
        if not self._process:
            return
    
        async def stream(pipe, prefix, file):
            if pipe is None:
                return

            async for line in pipe:
                file.write(f"[{prefix}] {line.decode('utf-8', errors='ignore')}")
                file.flush()

        with TASK_PIPELINE_FILE.open('a', encoding='utf-8') as f:
            await asyncio.gather(
                stream(self._process.stdout, task_name, f),
                stream(self._process.stderr, task_name, f)
            )

    async def execute(self):
        """执行任务列表"""

        TASK_PIPELINE_FILE.write_text('')

        self.status = TaskPipelineStatus.RUNNING
        try:
            for task in self.tasks:
                if self.status == TaskPipelineStatus.CANCELLED:
                    break
                try:
                    self._process = await task.run()
                    await self._stream_output(task.name)

                    if not self._process and self.status == TaskPipelineStatus.CANCELLED:
                        task.status = TaskStatus.CANCELLED
                        break

                    await self._process.wait()
                    task.status = TaskStatus.COMPLETED if self._process.returncode == 0 else TaskStatus.FAILED
                except Exception as e:
                    self.status = TaskStatus.FAILED
                    raise RuntimeError(f"Task {task.name} failed: {e}")
        except Exception as e:
            self.status = TaskPipelineStatus.FAILED
            raise RuntimeError(f"Pipeline execution failed: {e}")
        if self.status != TaskPipelineStatus.CANCELLED:
            self.status = TaskPipelineStatus.COMPLETED

    def is_running(self) -> bool:
        """判断是否有任务正在执行中"""
        return self.status == TaskPipelineStatus.RUNNING
    
    def init_tasks(self, tasks: list[Task]):
        if self.is_running():
            raise RuntimeError("Pipeline is running")
        self.tasks = tasks
        self.status = TaskPipelineStatus.IDLE
    
    async def cancel(self):
        if self._process and self._process.returncode is None:
            try:
                self.status = TaskPipelineStatus.CANCELLED
                self._process.kill()
                await self._process.wait()
            except Exception as e:
                raise RuntimeError(f"Error while kill process:: {e}")
            finally:
                self._process = None

    def get_logs(self) -> str:
        """获取当前日志文件的内容"""
        return TASK_PIPELINE_FILE.read_text(encoding='utf-8', errors='ignore')
        
TaskPipelineManager = TaskPipeline()

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
    def __init__(self, client_type: str | None = None, account_name: str | None = None):
        command = "maa startup"

        if client_type:
            command += f" {client_type}"

        if account_name:
            command += f" --account-name {account_name}"

        command += " -v"

        super().__init__(name="开始唤醒", command=command)


"""关闭游戏客户端

client_type:
    客户端版本，可选，默认为空
    选项："Official" | "Bilibili" | "txwy" | "YoStarEN" | "YoStarJP" | "YoStarKR"
"""
class CloseDownTask(Task):
    def __init__(self, client_type: str | None = None):
        command = "maa closedown"

        if client_type:
            command += f" {client_type}"

        command += " -v"

        super().__init__(name="关闭游戏", command=command)
from enum import Enum

from pydantic import BaseModel

from maa_api.model.core.task import Task

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
    tasks: list[Task] = []
    status: TaskPipelineStatus = TaskPipelineStatus.IDLE

    def get_task_list(self):
        return self.tasks

    def add_task(self, task: Task):
        self.tasks.append(task)

    def add_task_list(self, task_list: list[Task]):
        self.tasks.extend(task_list)

    def update_tak(self, task: Task):
        self.tasks[self.tasks.index(task)] = task

    def update_task_list(self, task_list: list[Task]):
        self.tasks = task_list

    def clear_task_list(self):
        self.tasks = []
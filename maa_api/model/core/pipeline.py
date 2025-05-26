from enum import Enum

from pydantic import BaseModel

from maa_api.model.core.task import Task, TaskStatus

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
    logs: list[str] = []

    def get_task_list(self):
        return self.tasks

    def get_task_dict(self):
        return {task.id: task for task in self.tasks}

    def get_task(self, task_id: str):
       return self.get_task_dict().get(task_id)

    def add_task(self, task: Task):
        self.tasks.append(task)

    def update_task(self, task: Task):
        self.tasks[self.tasks.index(task)] = task

    def update_task_list(self, task_list: list[Task]):
        self.tasks = task_list

    def update_task_status(self, task_id: str, task_status: TaskStatus):
        for task in self.tasks:
            if task.id == task_id:
                task.status = task_status
                break

    def stop(self):
        self.status = TaskPipelineStatus.CANCELLED
        for task in self.tasks:
            if task.status == TaskStatus.RUNNING or task.status == TaskStatus.PENDING:
                task.status = TaskStatus.CANCELLED

    def clear_task_list(self):
        self.tasks = []
        self.logs = []

    def append_log(self, log: str):
        self.logs.append(log)
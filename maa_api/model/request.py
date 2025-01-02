from pydantic import BaseModel

from maa_api.model.task import Task, StartUpTask, CloseDownTask
from maa_api.exception.response_exception import ResponseException

class TaskRequest(BaseModel):
    name: str

    """启动关闭游戏客户端"""
    client_type: str | None
    account_name: str | None

    def to_task(self) -> Task:
        if not self.name:
            raise ResponseException(message="任务名不能为空")
    
        if self.name == "Ping":
            return Task(task_name="ping", command="ping localhost")
        
        if self.name == "StartUp":
            return StartUpTask(
                client_type=self.client_type, account_name=self.account_name
            )
        
        if self.name == "CloseDown":
            return CloseDownTask(
                client_type=self.client_type
            )

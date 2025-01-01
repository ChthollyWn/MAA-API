from maa_api.model.task import Task, StartUpTask, CloseDownTask
from maa_api.model.request import TaskRequest
from maa_api.exception.response_exception import ResponseException

def switch_task(request: TaskRequest) -> Task:
    if not request.name:
        raise ResponseException(message="任务名不能为空")
    
    if request.name == "Ping":
        return Task(name="ping", command="ping localhost")
    
    if request.name == "StartUp":
        return StartUpTask(
            client_type=request.client_type, account_name=request.account_name
        )
    
    if request.name == "CloseDown":
        return CloseDownTask(
            client_type=request.client_type
        )
    
def switch_tasks(requests: list[TaskRequest]) -> list[Task]:
    tasks = []
    for request in requests:
        task = switch_task(request)
        tasks.append(task)
    return tasks
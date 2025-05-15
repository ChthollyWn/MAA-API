import threading

from maa_api.exception.response_exception import ResponseException
from maa_api.model.core.task import Task
from maa_api.model.core.pipeline import TaskPipeline
from maa_api.model.core.callback_handler import callback
from maa_api.model.core.asst_manager import AssistManager
from maa_api.model.core.executor import TaskExecutor

class TaskScheduler:
    def __init__(self):
        self.task_pipeline = TaskPipeline()
        self.asst = AssistManager(callback).load_asst()

    def append(self, task_list: list[Task]):
        self._check_asst_running()
        self.task_pipeline.add_task_list(task_list)

    def clear(self):
        self._check_asst_running()
        self.task_pipeline.clear_task_list()

    def start(self):
        self._check_asst_running()
        task_list = self.task_pipeline.get_task_list()

        if task_list is None or len(task_list) == 0:
            raise ResponseException("任务列表为空")

        for task in task_list:
            task.id = TaskExecutor.execute(self.asst, task)

    # def stop(self):

    def _is_asst_running(self) -> bool:
        return self.asst.running()

    def _check_asst_running(self):
        if self.asst.running():
            raise ResponseException("MAA任务队列正在运行中，不允许多实例访问")


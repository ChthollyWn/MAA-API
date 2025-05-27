import threading

from maa_api.exception.response_exception import ResponseException
from maa_api.model.core.task import Task
from maa_api.model.core.pipeline import TaskPipeline
from maa_api.model.core.callback_handler import CallbackHandler
from maa_api.model.core.asst_manager import AssistManager
from maa_api.model.core.executor import PipelineExecutor

class TaskScheduler:
    def __init__(self):
        self.task_pipeline = TaskPipeline()
        self.callback_handler = CallbackHandler(self.task_pipeline)
        self.asst = AssistManager(self.callback_handler).load_asst()
        self.pipeline_executor = PipelineExecutor(self.task_pipeline, self.asst, self.callback_handler, is_send_email=True)

    def append(self, task: Task):
        self._check_asst_running()
        self.task_pipeline.add_task(task)

    def clear(self):
        self._check_asst_running()
        self.task_pipeline.clear_task_list()

    def start(self):
        self._check_asst_running()
        self.pipeline_executor.start()

    def stop(self):
        self.pipeline_executor.stop()

    def is_running(self) -> bool:
        return self.pipeline_executor.is_running()

    def _check_asst_running(self):
        if self.asst.running():
            raise ResponseException("MAA任务队列正在运行中，不允许多实例访问")

task_scheduler = TaskScheduler()
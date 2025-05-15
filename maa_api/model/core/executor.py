import threading

from maa_api.exception.response_exception import ResponseException
from maa_api.log import logger
from maa_api.model.core.asst import Asst
from maa_api.model.core.task import TaskStatus
from maa_api.model.core.callback_handler import CallbackHandler
from maa_api.model.core.pipeline import TaskPipeline, TaskPipelineStatus


class PipelineExecutor:
    def __init__(self, task_pipeline: TaskPipeline, asst: Asst):
        self.task_pipeline = task_pipeline
        self.asst = asst
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._current_task_index = 0
        self._thread = None
        self._running = False

    def start(self):
        """启动任务队列执行（在独立线程中运行）"""
        if self._running:
            raise ResponseException("任务队列已在运行")
        self._running = True
        self.task_pipeline.status = TaskPipelineStatus.RUNNING
        self._thread = threading.Thread(target=self._execute_pipeline)
        self._thread.start()

    def stop(self):
        """停止任务队列执行"""
        with self._condition:
            self.asst.stop()
            self._running = False
            self.task_pipeline.status = TaskPipelineStatus.CANCELLED
            self._condition.notify_all()

    def is_running(self):
        """检查任务队列是否在运行"""
        return self._running

    def _execute_pipeline(self):
        """独立线程执行任务队列"""
        try:
            task_list = self.task_pipeline.get_task_list()
            for task in task_list:
                if not self._running:
                    logger.info("任务队列已手动停止")
                    break

                # 1. 提交任务
                task_id = self.asst.append_task(task.type_name, task.params)
                if not task_id:
                    logger.error(f"任务 [{task.task_name}] 提交失败")
                    continue

                # 2. 绑定回调
                CallbackHandler.instance.register_task(task_id, task.id)

                # 3. 启动任务
                logger.info(f"开始执行任务 [{task.task_name}]")
                if not self.asst.start():
                    logger.error(f"任务 [{task.task_name}] 启动失败")
                    continue

                # 4. 等待当前任务完成
                with self._condition:
                    while self._running and not self.asst.running() and task.status == TaskStatus.FAILED:
                        self._condition.wait(timeout=task.error_timeout)

            # 5. 所有任务完成
            self.task_pipeline.status = TaskPipelineStatus.COMPLETED
            logger.info("任务队列已完成")
        except Exception as e:
            self.task_pipeline.status = TaskPipelineStatus.FAILED
            logger.error(f"任务队列执行异常: {e}")
        finally:
            self._running = False

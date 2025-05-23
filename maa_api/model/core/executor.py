import threading
import time

from maa_api.exception.response_exception import ResponseException
from maa_api.log import logger
from maa_api.model.core.asst import Asst
from maa_api.model.core.task import TaskStatus
from maa_api.model.core.callback_handler import CallbackHandler
from maa_api.model.core.pipeline import TaskPipeline, TaskPipelineStatus


class PipelineExecutor:
    def __init__(self, task_pipeline: TaskPipeline, asst: Asst, callback_handler: CallbackHandler):
        self.task_pipeline = task_pipeline
        self.asst = asst
        self.callback_handler = callback_handler
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
        """独立线程执行任务队列（含重试机制）"""
        try:
            task_list = self.task_pipeline.get_task_list()
            for task in task_list:
                if not self._running:
                    logger.info("任务队列已手动停止")
                    break

                retry_count = 0
                task_succeeded = False

                while retry_count <= task.max_retries and not task_succeeded and self._running:
                    # 1. 提交任务
                    task_id = self.asst.append_task(task.type_name, task.params)
                    if not task_id:
                        retry_count += 1
                        logger.warning(f"任务 [{task.task_name}] 提交失败（第 {retry_count}/{task.max_retries} 次重试）")
                        if retry_count <= task.max_retries:
                            time.sleep(task.retry_delay)
                        continue

                    # 2. 绑定回调
                    self.callback_handler.register_task(task_id, task.id)

                    # 3. 启动任务
                    logger.info(f"开始执行任务 [{task.task_name}]")
                    if not self.asst.start():
                        retry_count += 1
                        logger.warning(f"任务 [{task.task_name}] 启动失败（第 {retry_count}/{task.max_retries} 次重试）")
                        if retry_count <= task.max_retries:
                            time.sleep(task.retry_delay)
                        continue

                    # 4. 等待当前任务完成
                    with self._condition:
                        while self._running and self.asst.running():
                            self._condition.wait(timeout=5)

                    # 5. 检查任务状态
                    if task.status == TaskStatus.COMPLETED:
                        logger.info(f"任务 [{task.task_name}] 成功完成")
                        task_succeeded = True
                    elif task.status == TaskStatus.FAILED:
                        retry_count += 1
                        logger.warning(f"任务 [{task.task_name}] 执行失败（第 {retry_count}/{task.max_retries} 次重试）")
                        if retry_count <= task.max_retries:
                            time.sleep(task.retry_delay)
                    else:
                        task_succeeded = True

                if not task_succeeded:
                    logger.error(f"任务 [{task.task_name}] 达到最大重试次数，跳过该任务")

            # 6. 所有任务完成
            self.task_pipeline.status = TaskPipelineStatus.COMPLETED
            self.task_pipeline.append_log("已完成全部任务")
            logger.info("任务队列已完成")
        except Exception as e:
            self.task_pipeline.status = TaskPipelineStatus.FAILED
            logger.error(f"任务队列执行异常: {e}")
        finally:
            self._running = False

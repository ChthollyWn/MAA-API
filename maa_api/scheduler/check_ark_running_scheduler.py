import threading
import time
import copy
from apscheduler.schedulers.background import BackgroundScheduler

from maa_api.log import logger
from maa_api.service import adb_service
from maa_api.config.config import Config
from maa_api.model.core.task import TaskStatus, Task, StartUpTask, CloseDownTask

task_lock = threading.Lock()

def check_ark_running_scheduler():
    ark_package_name = "com.hypergryph.arknights.bilibili"
    adb_address = Config.get_config("adb", "address")

    # 使用锁确保同时只有一个任务在运行
    with task_lock:
        if task_pipeline.running() and not adb_service.adb_check_running(adb_address, ark_package_name):
            logger.info("检测到任务流水线运行时客户端闪退，开始重启！")

            # 复制未完成的任务
            old_tasks: list[Task] = []
            for task in task_pipeline.tasks:
                if task.status != TaskStatus.COMPLETED and task.is_now:
                    copy_task = copy.deepcopy(task)
                    copy_task.status = TaskStatus.PENDING
                    copy_task.is_now = True
                    old_tasks.append(copy_task)

            logger.info(f"当前未完成的任务 {old_tasks}")

            # 中断任务
            logger.info("开始执行任务中断操作")
            if task_pipeline.stop():
                logger.info("任务中断执行成功")

            # 等待任务流水线停止
            wait_for_asst_stop("任务已中断！")

            # 执行客户端重启任务
            logger.info("开始执行客户端重启任务")
            restart_pipeline()

            # 重新添加未完成的任务
            for task in old_tasks:
                task_pipeline.append_task(task)
            task_pipeline.start()
            logger.info("开始执行未完成的任务")

def restart_pipeline():
    task_pipeline.append_task(CloseDownTask(client_type="Bilibili"))
    task_pipeline.append_task(StartUpTask(client_type="Bilibili", start_game_enabled=True))
    task_pipeline.start()

    wait_for_asst_stop("客户端重启成功")
    
def wait_for_asst_stop(success_message):
    while task_pipeline.running():
        time.sleep(0.1)
    logger.info(success_message)

def start():
    scheduler = BackgroundScheduler()
    scheduler.add_job(check_ark_running_scheduler, 'interval', seconds=300)
    scheduler.start()

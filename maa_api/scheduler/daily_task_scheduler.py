import datetime
import json

from apscheduler.schedulers.background import BackgroundScheduler

from maa_api.config.config import DAILY_TASK_FILE_PATH
from maa_api.model.core.scheduler import task_scheduler
from maa_api.model.request.request import TaskRequest


def daily_art_task():
    if not DAILY_TASK_FILE_PATH.exists():
        return

    with DAILY_TASK_FILE_PATH.open('r', encoding='utf-8') as file:
        daily_task_data = json.load(file)

    enable = daily_task_data.get('enable', True)
    weekday_task = daily_task_data.get('weekday_task', {})
    today = datetime.date.today()
    weekday = today.weekday()
    today_tasks_key = weekday_task.get(str(weekday), '')
    task_dict = daily_task_data.get('task_dict', {})
    tasks = task_dict.get(today_tasks_key, [])
    task_requests = [TaskRequest(**task) for task in tasks]

    if not enable:
        return

    if task_scheduler.is_running():
        return

    task_scheduler.clear()

    for req in task_requests:
        task_scheduler.append(req.to_task())

    task_scheduler.start()

def start():
    scheduler = BackgroundScheduler()
    scheduler.add_job(daily_art_task, 'cron', hour='7,19', minute='0')
    scheduler.start()
import json
import time
import datetime

from jinja2 import Environment, FileSystemLoader
from apscheduler.schedulers.background import BackgroundScheduler

from maa_api.config.path_config import DAILY_TASK_FILE_PATH, STATIC_PATH
from maa_api.model.request import TaskRequest
from maa_api.model.task import task_pipeline
from maa_api.service import smtp_service

def daily_art_task():
    if not DAILY_TASK_FILE_PATH.exists():
        return
    
    with DAILY_TASK_FILE_PATH.open('r', encoding='utf-8') as file:
        daily_task_data = json.load(file)

    email = daily_task_data.get('email', '')
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
    
    if task_pipeline.running():
        return
    
    for req in task_requests:
        task_pipeline.append_task(req.to_task())

    task_pipeline.start()

    while task_pipeline.running():
            time.sleep(1)

    env = Environment(loader=FileSystemLoader(str(STATIC_PATH)))
    template = env.get_template('email_template.html')

    email_content = template.render(
         status = task_pipeline.status,
         tasks=task_pipeline.active_tasks().tasks,
         logs=task_pipeline.logs
    )
        
    smtp_service.send_email("MAA-API 日志通知", email_content, email)

def start():
    scheduler = BackgroundScheduler()
    scheduler.add_job(daily_art_task, 'cron', hour='7,19', minute='0')
    scheduler.start()
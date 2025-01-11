import json
import time

from maa_api.config.path_config import ACCOUNT_PATH
from maa_api.model.request import TaskRequest
from maa_api.model.task import task_pipeline
from maa_api.service import smtp_service

def daily_art_task():
    if not ACCOUNT_PATH.exists():
        return
    
    with ACCOUNT_PATH.open('r', encoding='utf-8') as file:
        account_data = json.load(file)

    for account in account_data:
        email = account.get('email', '')
        tasks = account.get('tasks', [])
        task_requests = [TaskRequest(**task) for task in tasks]

        if not task_pipeline.running():
            return

        for req in task_requests:
            task_pipeline.append_task(req.to_task())

        task_pipeline.start()

        while task_pipeline.running():
            time.sleep(10)
        
        smtp_service.send_email("maa-api log", str(task_pipeline.logs), email)
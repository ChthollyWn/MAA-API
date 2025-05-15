import json

from fastapi import APIRouter, Depends, BackgroundTasks

from maa_api.model.request.response import Response
from maa_api.model.request.request import TaskRequest
from maa_api.model.core.pipeline import task_pipeline
from maa_api.config.config import DAILY_TASK_FILE_PATH
from maa_api.dependence.auth import token_auth
from maa_api.scheduler import daily_art_task_scheduler

router = APIRouter()

@router.post("/api/maa/pipeline", dependencies=[Depends(token_auth)])
async def post_tasks(request: list[TaskRequest]):
    if task_pipeline.running():
        return Response.bad_request(message='流水线任务正在运行中，不允许多实例访问')

    for req in request:
        task_pipeline.append_task(req.to_task())

    task_pipeline.start()

    return Response.success()

@router.get("/api/maa/pipeline", dependencies=[Depends(token_auth)])
async def get_tasks():
    return Response.success(data=task_pipeline.active_tasks())

@router.delete("/api/maa/pipeline", dependencies=[Depends(token_auth)])
async def delete_tasks():
    task_pipeline.stop()
    return Response.success()

@router.get("/api/maa/daily", dependencies=[Depends(token_auth)])
async def get_daily_tasks():
    if not DAILY_TASK_FILE_PATH.exists():
        return
    
    with DAILY_TASK_FILE_PATH.open('r', encoding='utf-8') as file:
        return Response.success(data=json.load(file))
    
@router.put("/api/maa/daily", dependencies=[Depends(token_auth)])
async def update_daily_tasks(request: dict):
    try:
        with DAILY_TASK_FILE_PATH.open('w', encoding='utf-8') as file:
            json.dump(request, file, ensure_ascii=False, indent=4)
        return Response.success()
    except Exception as e:
        raise RuntimeError(e)
    
@router.post("/api/maa/daily/execute")
async def test(background_tasks: BackgroundTasks):
    background_tasks.add_task(daily_art_task_scheduler.daily_art_task)
    return Response.success()
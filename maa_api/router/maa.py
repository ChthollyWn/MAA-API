import json

from fastapi import APIRouter, Depends, BackgroundTasks

from maa_api.model.response import Response
from maa_api.model.request import TaskRequest
from maa_api.model.task import task_pipeline
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
    
@router.get("/api/maa/test")
async def test(background_tasks: BackgroundTasks):
    background_tasks.add_task(daily_art_task_scheduler.daily_art_task)
    return Response.success()
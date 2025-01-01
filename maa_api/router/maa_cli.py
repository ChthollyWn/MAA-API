import asyncio

from fastapi import APIRouter, Depends

from maa_api.model.response import Response
from maa_api.model.request import TaskRequest
from maa_api.service.maa_cli_service import switch_tasks
from maa_api.model.task import TaskPipelineManager
from maa_api.dependence.auth import token_auth

router = APIRouter()

@router.post("/api/maa/pipeline", dependencies=[Depends(token_auth)])
async def post_tasks(requests: list[TaskRequest]):
    tasks = switch_tasks(requests)

    if not TaskPipelineManager.is_running():
        TaskPipelineManager.init_tasks(tasks)
        asyncio.create_task(TaskPipelineManager.execute())

    return Response.success(data=TaskPipelineManager)

@router.get("/api/maa/pipeline", dependencies=[Depends(token_auth)])
def get_tasks():
    return Response.success(data=TaskPipelineManager)

@router.delete("/api/maa/pipeline", dependencies=[Depends(token_auth)])
async def delete_tasks():
    await TaskPipelineManager.cancel()
    return Response.success(data=TaskPipelineManager)

@router.get("/api/maa/pipeline/log", dependencies=[Depends(token_auth)])
def get_tasks_log():
    logs = TaskPipelineManager.get_logs()
    return Response.success(data=logs)
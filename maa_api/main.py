from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from maa_api.router import adb, maa, template
from maa_api.exception import response_exception, excetpion_handler
from maa_api.config.config import STATIC_PATH
from maa_api.scheduler import check_ark_running_scheduler, daily_art_task_scheduler

app = FastAPI()

# 注册路由
app.include_router(adb.router)
app.include_router(maa.router)
app.include_router(template.router)

# 异常处理
app.add_exception_handler(Exception, excetpion_handler.exception_handler)
app.add_exception_handler(response_exception.ResponseException, excetpion_handler.response_exception_handler)

# 挂载静态文件
app.mount("/static", StaticFiles(directory=STATIC_PATH), name="static")

# 跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许所有来源，如果需要限制来源，可以用列表指定特定的域名
    allow_credentials=True,
    allow_methods=["*"],  # 允许所有 HTTP 方法
    allow_headers=["*"],  # 允许所有请求头
)

# @app.on_event("startup")
# async def scheduler():
    # daily_art_task_scheduler.start()
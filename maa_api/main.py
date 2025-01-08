from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from maa_api.router import adb, maa, template
from maa_api.exception import response_exception, excetpion_handler
from maa_api.config.path_config import STATIC_PATH
from maa_api.scheduler import check_ark_running_scheduler

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

@app.on_event("startup")
async def scheduler():
    check_ark_running_scheduler.start()
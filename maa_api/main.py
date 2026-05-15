# 拦截初始化：确保 config.yaml 文件存在后再往后导入核心服务
import os
import sys

from maa_api.config.config import CONFIG_PATH

if not CONFIG_PATH.exists():
    print("=========================================================================")
    print("⚠️ 警告: 缺少配置文件！")
    print("已经根据模板文件 config.template.yaml 为你自动生成了空缺的 config.yaml。")
    print("请先去根目录填写 `config.yaml`（如果是首次启动，你可能需要配置 adb 路径、token 等）。")
    print("配置完成后重新运行启动命令即可进入系统。")
    print("=========================================================================")
    sys.exit(1)

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from maa_api.router import adb, maa, template, update, system
from maa_api.exception import response_exception, exception_handler
from maa_api.config.config import STATIC_PATH
from maa_api.scheduler import daily_task_scheduler

app = FastAPI()

# 注册路由
app.include_router(adb.router)
app.include_router(maa.router)
app.include_router(template.router)
app.include_router(update.router)
app.include_router(system.router)

# 异常处理
app.add_exception_handler(Exception, exception_handler.exception_handler)
app.add_exception_handler(response_exception.ResponseException, exception_handler.response_exception_handler)

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

@app.on_event("startup")
async def scheduler():
    daily_task_scheduler.start()
from fastapi import FastAPI

from maa_api.router import adb, maa, template
from maa_api.exception import response_exception, excetpion_handler

app = FastAPI()

# 注册路由
app.include_router(adb.router)
app.include_router(maa.router)
app.include_router(template.router)

# 异常处理
app.add_exception_handler(Exception, excetpion_handler.exception_handler)
app.add_exception_handler(response_exception.ResponseException, excetpion_handler.response_exception_handler)
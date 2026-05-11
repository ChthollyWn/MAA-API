from fastapi import Request
from maa_api.log import logger
from fastapi.responses import JSONResponse
from maa_api.model.request.response import Response
from maa_api.exception.response_exception import ResponseException

"""处理全局异常"""
async def exception_handler(request: Request, exc: Exception):
    logger.error(f"{request.url}", exc_info=exc)
    return JSONResponse(Response.server_error().dict())

"""处理响应异常"""
async def response_exception_handler(request: Request, exc: ResponseException):
    logger.error(f"{request.url}", exc_info=exc)
    return JSONResponse(Response.build(code=exc.code, message=exc.message).dict())
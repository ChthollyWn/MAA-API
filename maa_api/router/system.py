from fastapi import APIRouter, Depends
from datetime import datetime
from maa_api.dependence.auth import token_auth
from maa_api.config.config import LOG_PATH
from maa_api.model.request.response import Response
from maa_api.log import logger

router = APIRouter()

@router.get("/api/system/logs", dependencies=[Depends(token_auth)])
async def get_system_logs(lines: int = 200):
    """获取最近的服务端运行日志"""
    if not LOG_PATH.exists():
        return Response.success(data=["[System] 日志目录不存在..."])
    
    # 获取目录下所有普通的 .log 文件（排除 .error.log）
    log_files = [f for f in LOG_PATH.glob("*.log") if not f.name.endswith(".error.log")]
    
    if not log_files:
        return Response.success(data=["[System] 当前系统暂无当日日志..."])
    
    # 按文件最后修改时间排序，选取最近更新的那一个文件，避免跨天引发的日期不对齐问题
    latest_log_file = max(log_files, key=lambda f: f.stat().st_mtime)
    
    try:
        with open(latest_log_file, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
            return Response.success(data=[line.strip("\n") for line in all_lines[-lines:]])
    except Exception as e:
        logger.error(f"日志读取异常: {e}")
        return Response.fail(message=f"读取日志失败: {str(e)}")
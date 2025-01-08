from fastapi import APIRouter
from fastapi.responses import FileResponse

from maa_api.config.path_config import STATIC_PATH

router = APIRouter()

# 首页
@router.get("/")
async def index():
    return FileResponse(STATIC_PATH  / "index.html")
from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from maa_api.config.path_config import STATIC_PATH
from maa_api.dependence.auth import token_auth

router = APIRouter()

# 首页
@router.get("/", dependencies=[Depends(token_auth)])
async def index():
    return FileResponse(STATIC_PATH  / "index.html")
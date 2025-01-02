import io

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, StreamingResponse

from maa_api.service import adb_service
from maa_api.config.config import Config
from maa_api.dependence.auth import token_auth


router = APIRouter()

# @router.get("/api/maa/screenshot")
# def get_screenshot():
#     img = Asst.get_image(1280*720*3)
#     return StreamingResponse(content=io.BytesIO(img))

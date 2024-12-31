from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from maa_api.service import adb_service
from maa_api.config.config import Config
from maa_api.model.response import Response
from maa_api.dependence.auth import token_auth

router = APIRouter()

@router.get('/api/adb/screenshot', dependencies=[Depends(token_auth)])
def get_screenshot():
    adb_address = Config.get_config("adb", "address")
    screenshot_path = adb_service.adb_screenshot(adb_address)
    return FileResponse(screenshot_path)
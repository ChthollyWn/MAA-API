from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from maa_api.dependence.auth import token_auth
from maa_api.service import adb_service

router = APIRouter()

@router.get('/api/adb/screenshot', dependencies=[Depends(token_auth)])
def get_screenshot():
    screenshot_path = adb_service.adb_screenshot()
    return FileResponse(screenshot_path)
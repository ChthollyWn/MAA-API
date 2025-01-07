from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from maa_api.config.path_config import STATIC_PATH

router = APIRouter()

# 首页
@router.get("/")
def index():
    html_file = STATIC_PATH  / "index.html"
    print(html_file)
    return HTMLResponse(content=html_file.read_text(encoding="utf-8"))
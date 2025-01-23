import shutil

from pathlib import Path

# 配置文件路径
CONFIG_PATH = Path() / "config.yaml"
# 静态文件路径
STATIC_PATH = Path() / "static"
# 日常任务配置路径
DAILY_TASK_FILE_PATH = Path() / "resource" / "daily_task.json"
# 图片路径
IMAGE_PATH = Path() / "resource" / "image"
# 依赖路径
LIB_PATH = Path() / "resource" / "lib"
# 日志路径
LOG_PATH = Path() / "resource" / "log"
# 临时文件路径
TEMP_PATH = Path() / "resource" / "temp"

IMAGE_PATH.mkdir(parents=True, exist_ok=True)
LIB_PATH.mkdir(parents=True, exist_ok=True)
LOG_PATH.mkdir(parents=True, exist_ok=True)
TEMP_PATH.mkdir(parents=True, exist_ok=True)

daily_task_template_path = Path() / "daily_task_template.json"
if not DAILY_TASK_FILE_PATH.exists():
    if daily_task_template_path.exists():
        shutil.copy(daily_task_template_path, DAILY_TASK_FILE_PATH)
    else:
        raise RuntimeError("日常任务模板文件不存在")
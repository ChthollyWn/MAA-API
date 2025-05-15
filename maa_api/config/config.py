import shutil

from pathlib import Path
from ruamel.yaml import YAML
from ruamel.yaml.scanner import ScannerError

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

class ConfigManager:
    def __init__(self):
        self._file = CONFIG_PATH
        self._data = {}
        self._load_config()

    def _load_config(self) -> None:
        if self._file.exists():
            _yaml = YAML()
            try:
                with self._file.open(encoding="utf8") as f:
                    self._data = _yaml.load(f)
            except ScannerError as e:
                raise ScannerError(
                    f"{e}\n**********************************************\n"
                    f"****** 可能为config.yaml配置文件填写不规范 ******\n"
                    f"**********************************************"
                ) from e
        
    def get_config(self, module: str, key: str, default=None):
        return self._data.get(module, {}).get(key, default)
    
    def reload_config(self):
        self._load_config()

Config = ConfigManager()
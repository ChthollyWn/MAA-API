
from ruamel.yaml import YAML
from ruamel.yaml.scanner import ScannerError

from maa_api.config.path_config import CONFIG_PATH

class ConfigManager:
    def __init__(self):
        self._file = CONFIG_PATH
        self._data = {}
        self._load_config()

    def _load_config(self) -> dict:
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
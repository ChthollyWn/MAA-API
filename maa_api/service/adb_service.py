import adbutils

from pathlib import Path
from adbutils._device import AdbDevice

from maa_api.config.config import IMAGE_PATH
from maa_api.config.config import Config

"""adb连接"""
def adb_connect(address: str) -> AdbDevice:
    try:
        adbutils.adb.connect(address)
        devices = adbutils.adb.device_list()

        # 检查是否连接到预期的设备
        connected_device = None
        for device in devices:
            if device.serial == address:
                connected_device = device
                break

        if connected_device:
            return connected_device
        else:
            raise RuntimeError(f"没有预期的设备连接: {address}")
    except Exception as e:
        raise RuntimeError("ADB 连接失败:", str(e))
    

"""adb截屏"""
def adb_screenshot(address: str) -> Path:
    try:
        device = adb_connect(address=address)
        pil_image = device.screenshot()
    except Exception as e:
        raise RuntimeError("ADB 截屏失败:", str(e))
    
    try:
        _dir = IMAGE_PATH / "screenshot"
        _dir.mkdir(parents=True, exist_ok=True)
        _path = _dir / "screenshot.jpeg"

        screenshot_quality = Config.get_config("adb", "screenshot_quality")

        pil_image.save(_path, quality=screenshot_quality)
        return _path
    except Exception as e:
        raise RuntimeError(f"ADB 截屏保存失败 Path={_path}", str(e))

def adb_check_running(address: str, package_name: str) -> bool:
    try:
        device = adb_connect(address=address)
        running_apps_output = device.shell('ps -ef')
        
        running_apps = running_apps_output.splitlines()
        
        return any(package_name in app for app in running_apps)
    except Exception as e:
        raise RuntimeError("ADB 获取运行应用失败:", str(e))
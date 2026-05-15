import re
import adbutils
import urllib.request
import json

TARGET_PACKAGES = {
    "Official": "com.hypergryph.arknights",
    "Bilibili": "com.hypergryph.arknights.bilibili"
}

def get_installed_apk_info(adb_address: str, client_type: str = "Official"):
    """
    依靠adb连接提取设备上当前安装的 APK 信息
    """
    try:
        device = adbutils.adb.device(serial=adb_address)
        pkg_name = TARGET_PACKAGES.get(client_type, TARGET_PACKAGES["Official"])
        
        # 1. 使用 dumpsys 检查应用是否安装及基础信息
        dumpsys_output = device.shell(f"dumpsys package {pkg_name}")
        
        if f"Package [{pkg_name}]" not in dumpsys_output:
            return {"current": "未安装", "update_time": "N/A"}

        version_name = re.search(r'versionName=(.*)', dumpsys_output)
        last_update = re.search(r'lastUpdateTime=(.*)', dumpsys_output)

        curr_ver = version_name.group(1).strip() if version_name else "未知"
        update_time = last_update.group(1).strip() if last_update else "未知"
        
        return {"current": curr_ver, "update_time": update_time}
    except Exception as e:
        return {"current": "未知", "update_time": "无法连接到设备"}

def get_latest_apk_info(client_type: str = "Official"):
    """
    依靠远端接口拉取最新包信息
    """
    try:
        if client_type == "Bilibili":
            url = "https://line1-h5-pc-api.biligame.com/game/detail/gameinfo?game_base_id=101772"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode('utf-8'))
                if data.get('code') == 0:
                    summary = data.get('data', {}).get('summary', '')
                    if summary:
                        return summary
            return "未知"
        elif client_type == "Official":
            # 如果是官服，暂无单纯的版本号API可以直接取（之前依靠挂载静态页或硬解HTML），只能在此做个占位或从本地缓存里识别
            return "最新未知(暂无官服API)"
        return "未知"
    except Exception:
        return "检查失败"

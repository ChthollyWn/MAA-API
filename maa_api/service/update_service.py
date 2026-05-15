import os
import sys
import requests
from urllib.parse import urlparse
import adbutils
from maa_api.log import logger

class GameUpdateService:
    def __init__(self, adb_address: str, client_type: str = "Official"):
        self.adb_address = adb_address
        self.client_type = client_type
        # 存放在 temp 目录下
        from maa_api.config.config import TEMP_PATH
        self.apk_save_path = TEMP_PATH / "arknights_apks"
        self.urls = {
            "official": "https://ak.hypergryph.com/downloads/android_lastest",
            "bilibili_info": "https://line1-h5-pc-api.biligame.com/game/detail/gameinfo?game_base_id=101772"
        }

    def setup_adb_connection(self):
        try:
            adbutils.adb.connect(self.adb_address)
            devices = adbutils.adb.device_list()
            
            connected_device = next((d for d in devices if d.serial == self.adb_address), None)
            if not connected_device:
                raise RuntimeError(f"未找到预期设备 {self.adb_address}")
            logger.info(f"设备连接成功: {connected_device.serial}")
            return connected_device
        except Exception as e:
            logger.error(f"ADB连接失败: {str(e)}")
            raise e

    def download_apk(self, download_url: str) -> str:
        try:
            parsed = urlparse(download_url)
            filename = os.path.basename(parsed.path) or "downloaded_apk.apk"
            save_path = self.apk_save_path / filename
            
            self.apk_save_path.mkdir(parents=True, exist_ok=True)
            
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36 Edg/133.0.0.0"
            }
            
            response = requests.get(download_url, headers=headers, stream=True)
            response.raise_for_status()
            
            file_size = int(response.headers.get('Content-Length', 0))
            file_size_mb = file_size / (1024 * 1024)
            logger.info(f"开始下载新版游戏客户端 APK，总大小：{file_size_mb:.2f} MB")

            downloaded = 0
            last_percent = -1

            with open(save_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if file_size > 0:
                            percent = int((downloaded / file_size) * 100)
                            # 每 10% 打印一次日志
                            if percent % 10 == 0 and percent != last_percent:
                                logger.info(f"APK下载进度: {percent}% ({downloaded / (1024 * 1024):.2f} MB / {file_size_mb:.2f} MB)")
                                last_percent = percent
                    
            logger.info(f"APK下载完毕: {save_path}")
            return str(save_path)
        except Exception as e:
            logger.error(f"APK下载失败 {download_url}: {str(e)}")
            raise e

    def get_bilibili_apk_info(self) -> dict:
        try:
            resp = requests.get(self.urls["bilibili_info"]).json()
            if resp.get('code') != 0:
                raise ValueError(f"获取信息失败，错误码: {resp.get('code')}, 信息: {resp.get('msg')}")
                
            data = resp.get('data', {})
            return {
                'summary': data.get('summary', '未知版本'),
                'links': [data.get('android_download_link'), data.get('android_download_link2')]
            }
        except Exception as e:
            logger.error(f"获取B服信息失败: {str(e)}")
            raise e

    def install_apk(self, device, apk_path: str):
        try:
            logger.info("正在安装APK...")
            device.install(apk_path)
            logger.info("APK安装成功！")
            return True
        except Exception as e:
            logger.error(f"APK安装失败: {str(e)}")
            raise e

    def update_game(self):
        device = self.setup_adb_connection()
        apk_path = None
        
        if self.client_type == "Official":
            apk_path = self.download_apk(self.urls["official"])
        elif self.client_type == "Bilibili":
            info = self.get_bilibili_apk_info()
            for link in info['links']:
                if link:
                    try:
                        apk_path = self.download_apk(link)
                        if apk_path:
                            break
                    except Exception as e:
                        logger.warning(f"下载链接 {link} 失败: {e}")
        else:
            raise ValueError(f"不支持的游戏客户端类型: {self.client_type}")

        if not apk_path:
            raise RuntimeError("无法下载APK文件，请检查网络或下载链接")
            
        self.install_apk(device, apk_path)

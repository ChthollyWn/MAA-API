import platform
import re
import os
import shutil
import tarfile
import zipfile

from urllib.error import HTTPError, URLError

from maa_api.model.core.asst import Asst
from maa_api.model.util.utils import Version, HttpUtils
from maa_api.model.util import downloader

from maa_api.log import logger


class Updater:
    # API的地址
    Mirrors = ["https://ota.maa.plus"]
    Summary_json = "/MaaAssistantArknights/api/version/summary.json"

    @staticmethod
    def custom_print(s):
        """
        可以被monkey patch的print，在其他GUI上使用可以被替换为任何需要的输出
        """
        logger.info(s)

    def __init__(self, path, version):
        self.path = path
        self.version = version
        self.latest_json = None
        self.latest_version = None
        self.assets_object = None

    @staticmethod
    def map_version_type(version):
        type_map = {
            Version.Nightly: 'alpha',
            Version.Beta: 'beta',
            Version.Stable: 'stable'
        }
        return type_map.get(version, 'stable')
    
    def get_cur_version(self):
        """
        从MaaCore.dll获取当前版本号
        这里是复用原来的方法
        """
        Asst.load(path=self.path)
        return Asst().get_version()

    def get_latest_version(self):
        """
        从API获取最新版本
        """
        api_url = self.Mirrors
        version_summary = self.Summary_json
        retry = 3
        for retry_times in range(retry):
            # 在重试次数限制内依次请求每一个镜像
            i = retry_times % len(api_url)
            request_url = api_url[i] + version_summary
            try:
                response_json = HttpUtils.get(request_url)
                response_json.raise_for_status()
                response_data = response_json.json()
                """
                解析JSON
                e.g.
                {
                  "alpha": {
                    "version": "v4.24.0-beta.1.d006.g27dee653d",
                    "detail": "https://ota.maa.plus/MaaAssistantArknights/api/version/alpha.json"
                  },
                  "beta": {
                    "version": "v4.24.0-beta.1",
                    "detail": "https://ota.maa.plus/MaaAssistantArknights/api/version/beta.json"
                  },
                  "stable": {
                    "version": "v4.23.3",
                    "detail": "https://ota.maa.plus/MaaAssistantArknights/api/version/stable.json"
                  }
                }
                """
                version_type = self.map_version_type(self.version)
                latest_version = response_data[version_type]['version']
                version_detail = response_data[version_type]['detail']
                return latest_version, version_detail
            except Exception as e:
                self.custom_print(e)
                continue
        return False, False

    @staticmethod
    def get_download_url(detail):
        """
        1.获取系统及架构信息
        2.找到对应的版本
        3.返回镜像url列表&文件名
        """
        """
        获取系统信息，包括：
            架构：ARM、x86
            系统：Linux、Windows
        默认Windows x86_64
        """
        system_platform = "win-x64"
        system = platform.system()
        if system == 'Linux':
            machine = platform.machine()
            if machine == 'aarch64':
                # Linux aarch64
                system_platform = "linux-aarch64"
            else:
                # Linux x86
                system_platform = "linux-x86_64"
        elif system == 'Windows':
            machine = platform.machine()
            if machine == 'AMD64' or machine == 'x86_64':
                # Windows x86-64
                system_platform = "win-x64"
            else:
                # Windows ARM64
                system_platform = "win-arm64"
        elif system == 'Darwin':
            system_platform = "macos-runtime-universal"

        # 请求的是https://ota.maa.plus/MaaAssistantArknights/api/version/stable.json，或其他版本类型对应的url
        retry = 3
        for _ in range(retry):
            try:
                detail_json = HttpUtils.get(detail)
                detail_json.raise_for_status()
                detail_data = detail_json.json()
                assets_list = detail_data["details"]["assets"]     # 列表，子元素为字典
                # 找到对应系统和架构的版本
                for assets in assets_list:
                    """
                    结构示例
                    assets:
                    {
                        "name": "MAA-v4.24.0-beta.1.d006.g27dee653d-win-x64.zip",
                        "size": 145677836,
                        "browser_download_url": "https://github.com/MaaAssistantArknights/MaaRelease/releases/download/v4.24.0-beta.1.d006.g27dee653d/MAA-v4.24.0-beta.1.d006.g27dee653d-win-x64.zip",
                        "mirrors": [
                        "https://s3.maa-org.net:25240/maa-release/MaaAssistantArknights/MaaRelease/releases/download/v4.24.0-beta.1.d006.g27dee653d/MAA-v4.24.0-beta.1.d006.g27dee653d-win-x64.zip",
                        "https://agent.imgg.dev/MaaAssistantArknights/MaaRelease/releases/download/v4.24.0-beta.1.d006.g27dee653d/MAA-v4.24.0-beta.1.d006.g27dee653d-win-x64.zip",
                        "https://maa.r2.imgg.dev/MaaAssistantArknights/MaaRelease/releases/download/v4.24.0-beta.1.d006.g27dee653d/MAA-v4.24.0-beta.1.d006.g27dee653d-win-x64.zip"
                        ]
                    }
                    """
                    assets_name = assets["name"]        # 示例值:MAA-v4.24.0-beta.1-win-arm64.zip
                    # 正则匹配（用于选择当前系统及架构的版本）
                    # 在线等一个不这么蠢的方法
                    pattern = r"^MAA-.*-" + re.escape(system_platform) + r"\.(zip|tar\.gz)$"
                    match = re.match(pattern, assets_name)
                    if match:
                        # Mirrors镜像列表
                        # mirrors = assets["mirrors"]
                        mirrors = []
                        github_url = assets["browser_download_url"]
                        # 加上GitHub的release链接
                        mirrors.append(github_url)
                        return mirrors, assets_name
            except Exception:
                continue
        return False, False

    def update(self):
        """
        主函数
        """
        # 从API获取最新版本
        latest_version, version_detail = self.get_latest_version()
        
        # 检查路径是否为空或目录为空
        if not self.path or (os.path.isdir(self.path) and not os.listdir(self.path)):
            self.custom_print("路径目录为空，开始下载最新版本")
            needs_update = True
        else:
            # 从dll获取MAA的版本
            current_version = self.get_cur_version()
            if current_version == latest_version:
                self.custom_print("当前为最新版本，无需更新")
                return
            else:
                self.custom_print(f"检测到最新版本:{latest_version}，开始更新")
                needs_update = True

        if needs_update:
            # 开始更新逻辑
            url_list, filename = self.get_download_url(version_detail)
            self.custom_print(f"获取到最新版本文件:{filename}")
            if not url_list:
                self.custom_print("未找到适用于当前系统的更新包")
                return

            file = os.path.join(self.path, filename)
            max_retry = 3
            for retry_frequency in range(max_retry):
                try:
                    self.custom_print(f"开始下载，第{retry_frequency + 1}次尝试")
                    downloader.file_download(download_url_list=url_list, download_path=file)
                    break
                except (HTTPError, URLError) as e:
                    self.custom_print(f"下载失败: {e}")

            self.custom_print('开始安装更新')
            file_extension = os.path.splitext(filename)[1]
            unzip = False
            try:
                if file_extension == '.zip':
                    with zipfile.ZipFile(file, 'r') as zfile:
                        zfile.extractall(self.path)
                    unzip = True
                elif file_extension == '.gz':
                    with tarfile.open(file, 'r:gz') as tfile:
                        tfile.extractall(self.path)
                    unzip = True
            except Exception as e:
                self.custom_print(f"解压失败: {e}")

            if unzip:
                extracted_items = os.listdir(self.path)

                expected_folder_name: str = os.path.splitext(os.path.basename(filename))[0]
                if expected_folder_name in extracted_items:
                    expected_folder_path: str = os.path.join(self.path, expected_folder_name)

                    for item in os.listdir(expected_folder_path):
                        src_path = os.path.join(expected_folder_path, item)
                        dst_path = os.path.join(self.path, item)
                        shutil.move(src_path, dst_path)

                    os.remove(file)
                    os.rmdir(expected_folder_path)

                self.custom_print('更新完成')
            else:
                self.custom_print('更新未完成')

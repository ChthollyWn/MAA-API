import requests
from maa_api.config.config import Config

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from typing import Union, Dict, List, Any, Type
from enum import Enum, IntEnum, unique, auto

JSON = Union[Dict[str, Any], List[Any], int, str, float, bool, Type[None]]


class InstanceOptionType(IntEnum):
    touch_type = 2
    deployment_with_pause = 3
    adblite_enabled = 4
    kill_on_adb_exit = 5


class StaticOptionType(IntEnum):
    invalid = 0
    cpu_ocr = 1
    gpu_ocr = 2


@unique
class Message(Enum):
    """
    回调消息

    请参考 docs/回调消息.md
    """
    InternalError = 0

    InitFailed = auto()

    ConnectionInfo = auto()

    AllTasksCompleted = auto()

    AsyncCallInfo = auto()

    Destroyed = auto()

    TaskChainError = 10000

    TaskChainStart = auto()

    TaskChainCompleted = auto()

    TaskChainExtraInfo = auto()

    TaskChainStopped = auto()

    SubTaskError = 20000

    SubTaskStart = auto()

    SubTaskCompleted = auto()

    SubTaskExtraInfo = auto()

    SubTaskStopped = auto()


@unique
class Version(Enum):
    """
    目标版本
    """
    Nightly = auto()

    Beta = auto()

    Stable = auto()


class HttpUtils:
    """
    HTTP工具类，封装了常用的HTTP请求方法，并支持从参数发送请求。
    """

    proxies = None
    proxy_path = Config.get_config('app', 'proxy')
    proxies = {
        'http': proxy_path,
        'https': proxy_path,
    }

    # 统一的超时时间设置（秒）
    TIMEOUT = 60

    @staticmethod
    def get_session_with_retries():
        session = requests.Session()
        retries = Retry(
            total=5,
            backoff_factor=0.1,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"]
        )
        adapter = HTTPAdapter(max_retries=retries)
        session.mount('http://', adapter)
        session.mount('https://', adapter)
        return session

    @staticmethod
    def get(url, params=None, headers=None, **kwargs):
        session = HttpUtils.get_session_with_retries()
        return session.get(url, params=params, headers=headers, proxies=HttpUtils.proxies, timeout=HttpUtils.TIMEOUT, **kwargs)

    @staticmethod
    def post(url, data=None, json=None, headers=None, **kwargs):
        session = HttpUtils.get_session_with_retries()
        return session.post(url, data=data, json=json, headers=headers, proxies=HttpUtils.proxies, timeout=HttpUtils.TIMEOUT, **kwargs)

    @staticmethod
    def put(url, data=None, headers=None, **kwargs):
        session = HttpUtils.get_session_with_retries()
        return session.put(url, data=data, headers=headers, proxies=HttpUtils.proxies, timeout=HttpUtils.TIMEOUT, **kwargs)

    @staticmethod
    def delete(url, headers=None, **kwargs):
        session = HttpUtils.get_session_with_retries()
        return session.delete(url, headers=headers, proxies=HttpUtils.proxies, timeout=HttpUtils.TIMEOUT, **kwargs)

    @staticmethod
    def patch(url, data=None, headers=None, **kwargs):
        session = HttpUtils.get_session_with_retries()
        return session.patch(url, data=data, headers=headers, proxies=HttpUtils.proxies, timeout=HttpUtils.TIMEOUT, **kwargs)

    @classmethod
    def head(cls, url, headers=None, **kwargs):
        session = HttpUtils.get_session_with_retries()
        return session.head(url, headers=headers, proxies=HttpUtils.proxies, timeout=HttpUtils.TIMEOUT, **kwargs)

    @staticmethod
    def send_request(method, url, params=None, data=None, json=None, headers=None, **kwargs):
        method = method.lower()
        if method not in ['get', 'post', 'put', 'delete', 'patch']:
            raise ValueError(f"Unsupported HTTP method: {method}")
        
        session = HttpUtils.get_session_with_retries()
        func = getattr(session, method)
        return func(url, params=params, data=data, json=json, headers=headers, proxies=HttpUtils.proxies, timeout=HttpUtils.TIMEOUT, **kwargs)
    

if __name__ == "__main__":
    url = 'https://github.com/MaaAssistantArknights/MaaAssistantArknights/releases/download/v5.14.1/MAA-v5.14.1-win-x64.zip'

    response = HttpUtils.head(url, allow_redirects=True)
    file_size = response.headers.get('Content-Length')
    print(file_size)

    try:
        # 使用 HttpUtils.get() 下载文件
        response = HttpUtils.get(url, stream=True)
        
        # 检查请求是否成功
        if response.status_code == 200:
            # 打开文件进行写入
            with open('MAA-v5.14.1-win-x64.zip', 'wb') as file:
                # 分块写入文件
                for chunk in response.iter_content(chunk_size=8192):
                    file.write(chunk)
            print(f"文件已成功下载并保存为 {'MAA-v5.14.1-win-x64.zip'}")
        else:
            print(f"下载失败，状态码: {response.status_code}")
    except Exception as e:
        print(f"下载过程中发生错误: {e}")
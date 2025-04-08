import requests
from maa_api.config.config import Config

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

    @staticmethod
    def get(url, params=None, headers=None, **kwargs):
        """
        发送GET请求。

        :param url: 请求的URL
        :param params: 请求参数，字典类型
        :param headers: 请求头，字典类型
        :param kwargs: 其他请求参数
        :return: 响应对象
        """
        return requests.get(url, params=params, headers=headers, proxies=HttpUtils.proxies, **kwargs)

    @staticmethod
    def post(url, data=None, json=None, headers=None, **kwargs):
        """
        发送POST请求。

        :param url: 请求的URL
        :param data: 请求体数据，字典类型
        :param json: JSON格式的请求体数据，字典类型
        :param headers: 请求头，字典类型
        :param kwargs: 其他请求参数
        :return: 响应对象
        """
        return requests.post(url, data=data, json=json, headers=headers, proxies=HttpUtils.proxies, **kwargs)

    @staticmethod
    def put(url, data=None, headers=None, **kwargs):
        """
        发送PUT请求。

        :param url: 请求的URL
        :param data: 请求体数据，字典类型
        :param headers: 请求头，字典类型
        :param kwargs: 其他请求参数
        :return: 响应对象
        """
        return requests.put(url, data=data, headers=headers, proxies=HttpUtils.proxies, **kwargs)

    @staticmethod
    def delete(url, headers=None, **kwargs):
        """
        发送DELETE请求。

        :param url: 请求的URL
        :param headers: 请求头，字典类型
        :param kwargs: 其他请求参数
        :return: 响应对象
        """
        return requests.delete(url, headers=headers, proxies=HttpUtils.proxies, **kwargs)

    @staticmethod
    def patch(url, data=None, headers=None, **kwargs):
        """
        发送PATCH请求。

        :param url: 请求的URL
        :param data: 请求体数据，字典类型
        :param headers: 请求头，字典类型
        :param kwargs: 其他请求参数
        :return: 响应对象
        """
        return requests.patch(url, data=data, headers=headers, proxies=HttpUtils.proxies, **kwargs)
    
    @classmethod
    def head(cls, url, headers=None, **kwargs):
        """
        发送HEAD请求。
        """
        return requests.head(url, headers=headers, proxies=HttpUtils.proxies, **kwargs)

    @staticmethod
    def send_request(method, url, params=None, data=None, json=None, headers=None, **kwargs):
        """
        根据参数发送HTTP请求。

        :param method: 请求方法（'GET', 'POST', 'PUT', 'DELETE', 'PATCH'）
        :param url: 请求的URL
        :param params: 请求参数，字典类型（仅适用于GET请求）
        :param data: 请求体数据，字典类型（适用于POST, PUT, PATCH请求）
        :param json: JSON格式的请求体数据，字典类型（适用于POST, PUT, PATCH请求）
        :param headers: 请求头，字典类型
        :param kwargs: 其他请求参数
        :return: 响应对象
        """
        method = method.lower()
        if method not in ['get', 'post', 'put', 'delete', 'patch']:
            raise ValueError(f"Unsupported HTTP method: {method}")
        
        func = getattr(requests, method)
        return func(url, params=params, data=data, json=json, headers=headers, proxies=HttpUtils.proxies, **kwargs)
    

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
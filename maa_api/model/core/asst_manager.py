import os
import pathlib

from maa_api.config.config import LIB_PATH, Config
from maa_api.log import logger
from maa_api.model.core.asst import Asst
from maa_api.model.util.updater import Updater
from maa_api.model.util.utils import HttpUtils
from maa_api.model.util.utils import InstanceOptionType, Version

MAA_LIB_DIR = LIB_PATH / 'maa'
MAA_LIB_DIR.mkdir(parents=True, exist_ok=True)

class AssistManager:
    def __init__(self, callback: 'Asst.CallBackType'):
        self.callback = callback

    def load_asst(self):
        maa_core_path = Config.get_config('app', 'maa_core_path')
        if not maa_core_path:
            maa_core_path = MAA_LIB_DIR
        maa_core_path = os.path.expanduser(maa_core_path)
        path = pathlib.Path(maa_core_path).resolve()

        # 更新maa版本
        logger.info("开始校验 MAA 版本")
        Updater(path, Version.Stable).update()

        # 加载核心资源
        logger.info("开始加载 MAA 核心资源")
        Asst.load(path=path)
        logger.info("MAA 核心资源加载成功")

        # 加载活动资源
        logger.info("开始加载版本活动资源")
        ota_tasks_url = 'https://ota.maa.plus/MaaAssistantArknights/api/resource/tasks.json'
        ota_tasks_path = path / 'cache' / 'resource' / 'tasks.json'
        ota_tasks_path.parent.mkdir(parents=True, exist_ok=True)
        resp = HttpUtils.get(ota_tasks_url)
        with open(ota_tasks_path, 'w', encoding='utf-8') as f:
            f.write(resp.text)
        Asst.load(path=path, incremental_path=path / 'cache')
        logger.info("版本活动资源加载成功")

        # 配置回调函数
        asst = Asst(callback=self.callback)
        # 触控方案配置
        asst.set_instance_option(InstanceOptionType.touch_type, 'maatouch')
        # 暂停下干员
        asst.set_instance_option(InstanceOptionType.deployment_with_pause, '1')

        adb_path = Config.get_config('adb', 'path')
        if not adb_path:
            # 如果adb路径未设置，使用Path环境变量
            adb_path = 'adb'
        adb_address = Config.get_config('adb', 'address')
        if not asst.connect(adb_path, adb_address):
            raise RuntimeError(f"MAA ADB 连接失败 path={adb_path} address={adb_address}")

        return asst
# import pathlib
# import json

# from maa_api.config.config import Config

# from maa_core.asst import Asst as AsstManager
# from maa_core.utils import Message, InstanceOptionType

# @AsstManager.CallBackType
# def callback(msg, details, arg):
#     m = Message(msg)
#     d = json.loads(details.decode('utf-8'))

#     print(m, d, arg)

# # 加载核心资源
# maa_core_path = Config.get_config('app', 'maa_core_path')
# path = pathlib.Path(maa_core_path).resolve()
# AsstManager.load(path=path)

# # 配置回调函数
# Asst = AsstManager(callback=callback)
# # 触控方案配置
# Asst.set_instance_option(InstanceOptionType.touch_type, 'maatouch')
# # 暂停下干员
# Asst.set_instance_option(InstanceOptionType.deployment_with_pause, '1')

# adb_address = Config.get_config('adb', 'address')
# if not Asst.connect('adb.exe', adb_address):
#     raise RuntimeError("MAA ADB 连接失败")
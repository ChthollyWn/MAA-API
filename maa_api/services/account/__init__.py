"""账号数据领域层（M27，规格 §7.3、§7.5）：干员数据与仓库数据的读写。

``capture``
    识别回调（``DepotInfo``）的解析与落库入口；只在 ``done=true`` 时写入，失败只记
    日志、不影响流水线回调。干员数据不走这里——M47 起唯一来源是森空岛同步
    （docs/13 §8.70）。
``box``
    干员表：概要、筛选列表，以及森空岛同步的写路径。
``depot``
    仓库表：整表替换、分类筛选、过期判定。

识别回调把数据交给 ``depot`` 落库（由 ``capture.py`` 调用），REST 路由与 Agent 工具
只从这里读；它们都不直接出现 ``select()``（那是仓储的事）。
子模块不在这里 import：包只声明有哪些模块，谁用谁 import，免得一个模块的
import 期副作用拖垮整包。
"""

from __future__ import annotations

__all__ = ["box", "capture", "depot"]

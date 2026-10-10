"""账号数据领域层（M27，规格 §7.3、§7.5）：干员识别结果与仓库识别结果的读写。

``capture``
    识别回调（``OperBoxInfo`` / ``DepotInfo``）的解析与落库入口；只在 ``done=true``
    时写入，失败只记日志、不影响流水线回调。
``box``
    干员表：概要、筛选列表、手动编辑的字段级约束、识别合并与差异。
``depot``
    仓库表：整表替换、分类筛选、过期判定。

识别回调把数据交给 ``box`` / ``depot`` 落库（由 ``capture.py`` 调用），REST 路由与
Agent 工具只从这里读；它们都不直接出现 ``select()``（那是仓储的事）。
子模块不在这里 import：包只声明有哪些模块，谁用谁 import，免得一个模块的
import 期副作用拖垮整包。
"""

from __future__ import annotations

__all__ = ["box", "capture", "depot"]

"""只读探索工具与侦察 subagent（M48，规格 §4.2/§4.3）。

- :mod:`maa_api.agent.recon.explore`：``list_files`` / ``read_file`` / ``search_code``
  与它们的边界口径（工作区根目录、越界、密钥名单、二进制、2 MiB、忽略目录、截断续读）。
- :mod:`maa_api.agent.recon.runner`：有界子循环，把长探索挡在父上下文之外。
- :mod:`maa_api.agent.recon.delegate`：``delegate_recon`` 工具本身。

这一层**没有任何写能力**：不写文件、不跑测试、不碰 shell、不碰设备。
"""

from __future__ import annotations

__all__ = ["explore", "runner"]

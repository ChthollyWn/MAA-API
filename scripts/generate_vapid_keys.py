#!/usr/bin/env python
"""生成一对 VAPID 密钥并打印可粘贴进 ``config.yaml`` 的内容。

     .venv/bin/python scripts/generate_vapid_keys.py

**为什么要显式生成而不是让服务在启动时自动生成：** VAPID 密钥对是订阅的身份。
浏览器在 ``pushManager.subscribe`` 时把公钥绑进订阅，推送服务用它校验
``Authorization`` 里的 JWT。启动时随机生成意味着每次重启都换密钥，既有的全部订阅
会从那一刻起静默投递失败（订阅还在，用户却收不到任何通知，而且没有任何报错）。

密钥只在标准输出显示，不写文件、不落日志。私钥是凭据：确认终端回滚安全后再粘贴，
并且不要把 ``config.yaml`` 提交进版本库（它已在 ``.gitignore`` 中）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from maa_api.services.webpush_vapid import (  # noqa: E402
    generate_vapid_key_pair,
    public_key_from_private,
    load_vapid_private_key,
)


def main() -> int:
    private_pem, public_key = generate_vapid_key_pair()

    # 自检：生成结果必须能被正常载入并导出同一把公钥，否则写进配置也只会得到
    # 一个「订阅成功但推送全失败」的状态。
    loaded = load_vapid_private_key(private_pem)
    if public_key_from_private(loaded) != public_key:
        print("生成自检失败：公钥往返不一致", file=sys.stderr)
        return 1

    # 私钥是多行 PEM；YAML 块标量最省事，也不需要转义换行。
    indented = "\n".join(f"    {line}" for line in private_pem.strip().splitlines())
    print("# 把下面这段合并进仓库根的 config.yaml（该文件不入库）")
    print("# 公钥不需要写进配置：服务端由私钥导出，两份配置会漂移。")
    print()
    print("notify:")
    print("  vapid_private_key: |")
    print(indented)
    print("  # JWT 的 sub，推送服务用它联系站点管理员；改成你自己的邮箱")
    print("  vapid_subject: mailto:admin@example.com")
    print()
    print(f"# 前端 applicationServerKey（服务端会通过 "
          f"GET /api/notifications/vapid-public-key 返回同一个值）")
    print(f"# {public_key}")
    print()
    print("提示：修改 config.yaml 后需要重启服务才会生效。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

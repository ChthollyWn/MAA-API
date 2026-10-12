#!/usr/bin/env python3
"""生成一个森空岛凭据加密密钥（Fernet key，M42 §4.4）。

用法::

    .venv/bin/python scripts/generate_skland_key.py          # 只打印
    .venv/bin/python scripts/generate_skland_key.py --write  # 追加/更新 config.yaml 的 skland 段

密钥用来加密 `skland_credential` 表里的 `cred` 与签名密钥。**换密钥等于让已保存的凭据失效**：
读凭据会回 409 `SKLAND_ENCRYPTION_KEY_INVALID`，需要重新扫码绑定，所以先在页面上解绑、
或接受重新绑定之后再换。

与 `scripts/generate_vapid_keys.py` 的区别：VAPID 密钥必须成对且稳定（换了对已签发的订阅
就失效），因此那边只打印、由部署者自己粘；这里的密钥只在本机使用，`--write` 直接写进
`config.yaml` 的 `skland:` 段更省事——但它**不会覆盖已有值**，除非显式加 `--force`。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cryptography.fernet import Fernet  # noqa: E402

from maa_api.settings import DEFAULT_CONFIG_PATH  # noqa: E402


def _write_key(path: Path, key: str, *, force: bool) -> str:
    """把密钥写进 `config.yaml` 的 `skland.credential_key`；返回结果说明。"""
    from ruamel.yaml import YAML

    yaml = YAML()
    yaml.preserve_quotes = True
    if path.is_file():
        document = yaml.load(path.read_text(encoding="utf8")) or {}
    else:
        document = {}
    section = document.get("skland")
    if not isinstance(section, dict):
        section = {}
        document["skland"] = section
    current = section.get("credential_key")
    if current and not force:
        return f"{path} 里已有 skland.credential_key，未改动（要覆盖加 --force）"
    section["credential_key"] = key
    with path.open("w", encoding="utf8") as handle:
        yaml.dump(document, handle)
    return f"已写入 {path} 的 skland.credential_key"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true", help="直接写进 config.yaml")
    parser.add_argument("--force", action="store_true", help="已有值时也覆盖（会让旧凭据失效）")
    parser.add_argument("--path", default=str(DEFAULT_CONFIG_PATH), help="配置文件路径")
    args = parser.parse_args(argv)

    key = Fernet.generate_key().decode("ascii")
    if args.write:
        print(_write_key(Path(args.path), key, force=args.force))
    print(key)
    print("用法：把上面这行填进 config.yaml 的 skland.credential_key，然后重启服务。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

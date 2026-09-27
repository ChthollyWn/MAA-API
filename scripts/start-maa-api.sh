#!/usr/bin/env bash
# 启动 MAA-API（开发/单机运行）。
#
# ADB 等待逻辑已在 M6 内移到服务里，本脚本只负责：定位仓库根、准备动态库搜索路径、
# 用仓库内的虚拟环境启动 uvicorn。
#
# 部署到 tailscale serve 之后请配置 deployment.forwarded_allow_ips（默认 127.0.0.1），
# 否则 cookie 的 Secure 判定与来源地址都会失真。见 docs/部署指南。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PYTHON_BIN="${MAA_PYTHON:-$REPO_ROOT/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "找不到 Python 解释器：$PYTHON_BIN（可用 MAA_PYTHON 覆盖）" >&2
  exit 1
fi

# MaaCore 动态库搜索路径（macOS 用 DYLD_*，Linux 用 LD_LIBRARY_PATH）
case "$(uname -s)" in
  Darwin) CORE_LIB_DIR="$REPO_ROOT/resource/lib/maa/Darwin" ;;
  *)      CORE_LIB_DIR="$REPO_ROOT/resource/lib/maa/Linux" ;;
esac
if [[ -d "$CORE_LIB_DIR" ]]; then
  case "$(uname -s)" in
    Darwin) export DYLD_LIBRARY_PATH="$CORE_LIB_DIR${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}" ;;
    *)      export LD_LIBRARY_PATH="$CORE_LIB_DIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" ;;
  esac
fi

export PYTHONUNBUFFERED=1

# 代理头信任范围从配置读取；读不到就用回环（与 maa_api.main.resolve_forwarded_allow_ips 一致）。
FORWARDED_ALLOW_IPS="$("$PYTHON_BIN" - <<'PYEOF' 2>/dev/null || echo 127.0.0.1
from maa_api.main import resolve_forwarded_allow_ips
from maa_api.settings import get_settings

print(resolve_forwarded_allow_ips(get_settings().deployment.forwarded_allow_ips))
PYEOF
)"

exec "$PYTHON_BIN" -m uvicorn maa_api.main:app \
  --host "${MAA_HOST:-0.0.0.0}" --port "${MAA_PORT:-8002}" \
  --forwarded-allow-ips "$FORWARDED_ALLOW_IPS"

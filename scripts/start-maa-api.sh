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

BIND_HOST="${MAA_HOST:-0.0.0.0}"
BIND_PORT="${MAA_PORT:-8002}"

# 端口上已经有服务在跑时不要硬起第二份：uvicorn 先跑完应用 lifespan（其中的流水线回收会把
# 数据库里所有在跑的流水线标成「内核中断」），再去 bind，绑定失败并不妨碍它先把任务标死。
# 2026-10-11 就是这样一份多余实例把正在跑的练度扫描标成内核中断的，见 docs/13 §8.55。
case "$BIND_HOST" in
  0.0.0.0|::|127.0.0.1|localhost) PROBE_HOST="127.0.0.1" ;;
  *) PROBE_HOST="$BIND_HOST" ;;
esac
if "$PYTHON_BIN" - "$PROBE_HOST" "$BIND_PORT" <<'PYEOF'
import socket
import sys

host, port = sys.argv[1], int(sys.argv[2])
try:
    socket.create_connection((host, port), timeout=1.0).close()
except OSError:
    sys.exit(1)
PYEOF
then
  LISTENER_PID="$(lsof -nP -iTCP:"$BIND_PORT" -sTCP:LISTEN -t 2>/dev/null | head -1 || true)"
  echo "端口 $BIND_PORT 上已经有服务在跑${LISTENER_PID:+（PID $LISTENER_PID）}，拒绝重复启动。" >&2
  echo "重复实例的启动会顺手跑一遍流水线回收，把正在执行的流水线标成「内核中断」。" >&2
  echo "要重启就先停掉旧进程${LISTENER_PID:+：kill $LISTENER_PID}" >&2
  echo "想在别的端口再起一份就设 MAA_PORT（例如 MAA_PORT=8003），并在前端指向这个端口。" >&2
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
  --host "$BIND_HOST" --port "$BIND_PORT" \
  --forwarded-allow-ips "$FORWARDED_ALLOW_IPS"

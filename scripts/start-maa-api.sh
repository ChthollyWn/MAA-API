#!/bin/zsh
set -euo pipefail
cd /Users/chtholly/Developer/WorkSpace/MAA-API
export PATH="/Users/chtholly/Library/Caches/pypoetry/virtualenvs/maa-api-m9W_mjo6-py3.13/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
export DYLD_LIBRARY_PATH="/Users/chtholly/Developer/WorkSpace/MAA-API/resource/lib/maa/Darwin${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}"
export PYTHONUNBUFFERED=1

ADB_BIN="/opt/homebrew/bin/adb"
ADDR="127.0.0.1:5555"

for i in {1..60}; do
  "$ADB_BIN" connect "$ADDR" >/dev/null 2>&1 || true
  if "$ADB_BIN" -s "$ADDR" shell echo ok >/dev/null 2>&1; then
    echo "[start-maa-api] ADB ready after ${i} tries"
    break
  fi
  echo "[start-maa-api] waiting ADB ($i)..."
  sleep 5
  if [[ $i -eq 60 ]]; then
    echo "[start-maa-api] ADB not ready after timeout" >&2
    exit 1
  fi
done

exec /Users/chtholly/Library/Caches/pypoetry/virtualenvs/maa-api-m9W_mjo6-py3.13/bin/uvicorn \
  maa_api.main:app --host 0.0.0.0 --port 8002

#!/bin/zsh
set -euo pipefail
cd /Users/chtholly/Developer/WorkSpace/MAA-API
export PATH="/Users/chtholly/Library/Caches/pypoetry/virtualenvs/maa-api-m9W_mjo6-py3.13/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
export DYLD_LIBRARY_PATH="/Users/chtholly/Developer/WorkSpace/MAA-API/resource/lib/maa/Darwin${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}"
export PYTHONUNBUFFERED=1

exec /Users/chtholly/Library/Caches/pypoetry/virtualenvs/maa-api-m9W_mjo6-py3.13/bin/uvicorn \
  maa_api.main:app --host 0.0.0.0 --port 8002

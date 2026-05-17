#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3.12}"
if [[ "${1:-}" == *.toml ]]; then
  CONFIG_PATH="${1:-config.toml}"
  COMMAND="${2:-run}"
  shift || true
  shift || true
else
  CONFIG_PATH="${CONFIG_PATH:-config.toml}"
  COMMAND="${1:-run}"
  shift || true
fi

mkdir -p logs
LOG_FILE="${LOG_FILE:-logs/$(date +%Y%m%d_%H%M%S)_${COMMAND}.log}"
if [ "$COMMAND" != "logs" ]; then
  ln -sf "$(basename "$LOG_FILE")" logs/latest.log
fi
exec > >(tee -a "$LOG_FILE") 2>&1
export PYTHONUNBUFFERED=1

echo "[start-bot] root=$ROOT_DIR"
echo "[start-bot] config=$CONFIG_PATH command=$COMMAND log=$LOG_FILE"

if [ ! -f "$CONFIG_PATH" ]; then
  cp config.toml.example "$CONFIG_PATH"
  echo "[init] created $CONFIG_PATH from config.toml.example"
fi

if [ ! -x ".venv/bin/python" ]; then
  "$PYTHON_BIN" -m venv .venv
fi

PROXY_JSON="$(./.venv/bin/python - "$CONFIG_PATH" <<'PY'
import json
import sys
from weauto_wx_cli.config import load_config
cfg = load_config(sys.argv[1])
print(json.dumps({
    "enabled": bool(cfg.proxy.enabled),
    "url": cfg.proxy.url,
    "no_proxy": cfg.proxy.no_proxy,
}))
PY
)"
PROXY_ENABLED="$(python3 -c 'import json,sys; print(str(json.loads(sys.argv[1])["enabled"]).lower())' "$PROXY_JSON")"
PROXY_URL="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["url"])' "$PROXY_JSON")"
NO_PROXY_VALUE="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["no_proxy"])' "$PROXY_JSON")"
if [ "$PROXY_ENABLED" = "true" ] && [ -n "$PROXY_URL" ]; then
  export HTTP_PROXY="$PROXY_URL"
  export HTTPS_PROXY="$PROXY_URL"
  export ALL_PROXY="$PROXY_URL"
  export http_proxy="$PROXY_URL"
  export https_proxy="$PROXY_URL"
  export all_proxy="$PROXY_URL"
  export NO_PROXY="$NO_PROXY_VALUE"
  export no_proxy="$NO_PROXY_VALUE"
  echo "[start-bot] shell proxy=$PROXY_URL no_proxy=$NO_PROXY_VALUE"
fi

REQ_HASH="$(shasum -a 256 requirements.txt | awk '{print $1}')"
STAMP=".venv/.requirements.sha256"
if [ ! -f "$STAMP" ] || [ "$(cat "$STAMP")" != "$REQ_HASH" ]; then
  ./.venv/bin/python -m pip install -r requirements.txt
  echo "$REQ_HASH" > "$STAMP"
fi

if [ "$COMMAND" = "run" ] || [ "$COMMAND" = "hermes-check" ]; then
  echo "[start-bot] agent mode — no external gateway needed"
fi

if [ "$COMMAND" = "run" ]; then
  ./.venv/bin/python -m weauto_wx_cli.cli --config "$CONFIG_PATH" doctor || true
fi

exec ./.venv/bin/python -m weauto_wx_cli.cli --config "$CONFIG_PATH" "$COMMAND" "$@"

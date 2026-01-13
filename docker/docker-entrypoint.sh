#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/telegram-checkin-bot}"
DATA_DIR="${DATA_DIR:-/data}"
CONFIG_FILE="${DATA_DIR}/config.json"
TOKEN_DIR="${DATA_DIR}/token"
TOKEN_FILE="${TOKEN_DIR}/bot_token"
export CONFIG_FILE TOKEN_FILE

mkdir -p "${DATA_DIR}"
cd "${DATA_DIR}"

bootstrap_config() {
  local api_id="${API_ID:-}"
  local api_hash="${API_HASH:-}"
  local bot_token="${BOT_TOKEN:-}"
  local admin_ids="${ADMIN_IDS:-}"
  local log_channel="${LOG_CHANNEL:-}"
  local log_enabled="${LOG_ENABLED:-true}"
  if [[ -z "$api_id" || -z "$api_hash" || -z "$bot_token" || -z "$admin_ids" ]]; then
    cat <<EOF
⚠️ 未找到 ${CONFIG_FILE}，且未提供完整的环境变量（API_ID/API_HASH/BOT_TOKEN/ADMIN_IDS）。
请设置这些环境变量，或预先挂载 config.json 到 ${DATA_DIR}。
EOF
    exit 1
  fi
  local admin_json
  admin_json="$(python3 -c "import json;print(json.dumps([int(x) for x in '$admin_ids'.split(',') if x.strip().isdigit()]))")"
  local log_channel_json="null"
  if [[ -n "$log_channel" ]]; then
    log_channel_json="\"${log_channel}\""
  fi
  cat >"$CONFIG_FILE" <<EOF
{
  "api_id": ${api_id},
  "api_hash": "${api_hash}",
  "bot_token": "${bot_token}",
  "admin_ids": ${admin_json},
  "log_channel": ${log_channel_json},
  "log_enabled": ${log_enabled}
}
EOF
  mkdir -p "${TOKEN_DIR}"
  printf "%s" "${bot_token}" > "${TOKEN_FILE}"
  echo "已根据环境变量生成 ${CONFIG_FILE}"
}

if [[ ! -f "$CONFIG_FILE" ]]; then
  bootstrap_config
else
  mkdir -p "${TOKEN_DIR}"
  python3 - <<'PY'
import json, os
cfg_path = os.environ.get("CONFIG_FILE")
token_file = os.environ.get("TOKEN_FILE")
log_channel = os.environ.get("LOG_CHANNEL")
log_enabled = os.environ.get("LOG_ENABLED")
if not cfg_path:
    raise SystemExit
with open(cfg_path, "r", encoding="utf-8") as f:
    data = json.load(f)
token = data.get("bot_token")
if token and token_file and not os.path.exists(token_file):
    with open(token_file, "w", encoding="utf-8") as fo:
        fo.write(token)
updated = False
if log_channel:
    data["log_channel"] = log_channel
    updated = True
if log_enabled:
    data["log_enabled"] = log_enabled.lower() not in ("0", "false", "n")
    updated = True
if updated:
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
PY
fi

exec python "${APP_DIR}/main.py"

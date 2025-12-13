#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/telegram-checkin-bot}"
DATA_DIR="${DATA_DIR:-/data}"
CONFIG_FILE="${DATA_DIR}/config.json"

mkdir -p "${DATA_DIR}"
cd "${DATA_DIR}"

bootstrap_config() {
  local api_id="${API_ID:-}"
  local api_hash="${API_HASH:-}"
  local bot_token="${BOT_TOKEN:-}"
  local admin_ids="${ADMIN_IDS:-}"
  if [[ -z "$api_id" || -z "$api_hash" || -z "$bot_token" || -z "$admin_ids" ]]; then
    cat <<EOF
⚠️ 未找到 ${CONFIG_FILE}，且未提供完整的环境变量（API_ID/API_HASH/BOT_TOKEN/ADMIN_IDS）。
请设置这些环境变量，或预先挂载 config.json 到 ${DATA_DIR}。
EOF
    exit 1
  fi
  local admin_json
  admin_json="$(python3 -c "import json;print(json.dumps([int(x) for x in '$admin_ids'.split(',') if x.strip().isdigit()]))")"
  cat >"$CONFIG_FILE" <<EOF
{
  "api_id": ${api_id},
  "api_hash": "${api_hash}",
  "bot_token": "${bot_token}",
  "admin_ids": ${admin_json}
}
EOF
  echo "已根据环境变量生成 ${CONFIG_FILE}"
}

if [[ ! -f "$CONFIG_FILE" ]]; then
  bootstrap_config
fi

exec python "${APP_DIR}/main.py"

#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="tg-checkin-bot"
DEFAULT_CONTAINER="tg-checkin"
DATA_DIR_BASE="$PWD"

echo "=== TG Checkin Docker 安装器 ==="

read -rp "API_ID (my.telegram.org): " API_ID
read -rp "API_HASH: " API_HASH
read -rp "BOT_TOKEN (来自 BotFather): " BOT_TOKEN
read -rp "管理员 ID（逗号分隔，数字）: " ADMIN_IDS
read -rp "容器名称 [${DEFAULT_CONTAINER}]: " CONTAINER_NAME
CONTAINER_NAME="${CONTAINER_NAME:-$DEFAULT_CONTAINER}"
read -rp "数据目录 [${DATA_DIR_BASE}/${CONTAINER_NAME}-data]: " DATA_DIR
DATA_DIR="${DATA_DIR:-${DATA_DIR_BASE}/${CONTAINER_NAME}-data}"

mkdir -p "$DATA_DIR"

echo "-> 构建镜像 ${IMAGE_NAME}"
docker build -t "${IMAGE_NAME}" .

echo "-> 停止并移除旧容器（如存在）"
docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true

echo "-> 启动容器 ${CONTAINER_NAME}"
docker run -d \
  --name "${CONTAINER_NAME}" \
  -e API_ID="${API_ID}" \
  -e API_HASH="${API_HASH}" \
  -e BOT_TOKEN="${BOT_TOKEN}" \
  -e ADMIN_IDS="${ADMIN_IDS}" \
  -v "${DATA_DIR}:/data" \
  "${IMAGE_NAME}"

echo
echo "✅ Docker 安装完成！"
echo "容器：${CONTAINER_NAME}"
echo "日志：docker logs -f ${CONTAINER_NAME}"
echo "数据目录：${DATA_DIR}"

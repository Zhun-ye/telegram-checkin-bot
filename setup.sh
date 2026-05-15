#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="tg-checkin"
APP_DIR="/opt/tg-checkin"
APP_USER="tgcheckin"
PYTHON_BIN="python3"

IMAGE_NAME="tg-checkin-bot"
DEFAULT_CONTAINER="tg-checkin"

require_root() {
  if [[ $EUID -ne 0 ]]; then
    echo "请用 root 运行：sudo bash setup.sh"
    exit 1
  fi
}

pause() {
  read -rp "按回车继续..."
}

install_sys_deps() {
  # 检测 Python 主次版本，例如 3.13
  local py_majmin
  py_majmin="$($PYTHON_BIN -c 'import sys;print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
  local py_venv_pkg="python${py_majmin}-venv"
  local pkgs_debian=("python3" "ca-certificates" "tzdata" "git" "rsync")
  local pkgs_rhel=("python3" "ca-certificates" "tzdata" "git" "rsync")

  if command -v apt >/dev/null 2>&1; then
    apt update
    apt install -y "${pkgs_debian[@]}" || true
    apt install -y "$py_venv_pkg" || apt install -y python3-venv || true
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y "${pkgs_rhel[@]}" || true
    dnf install -y python3-virtualenv || true
  elif command -v yum >/dev/null 2>&1; then
    yum install -y "${pkgs_rhel[@]}" || true
    yum install -y python3-virtualenv || true
  else
    echo "未检测到 apt/dnf/yum，请自行确保已安装：Python3、tzdata、git（可选 rsync）。"
  fi
}

direct_install() {
  require_root
  echo "==> TG Auto Checkin 直接安装（需 root）"

  install_sys_deps

  if ! id -u "$APP_USER" >/dev/null 2>&1; then
    useradd -r -m -d "$APP_DIR" -s /usr/sbin/nologin "$APP_USER"
  fi
  mkdir -p "$APP_DIR"
  chown -R "$APP_USER:$APP_USER" "$APP_DIR"

  echo "==> 同步仓库到 $APP_DIR"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete --exclude=".git" ./ "$APP_DIR"/
  else
    echo "未找到 rsync，使用 cp -a 回退方案（不支持 --delete）"
    find "$APP_DIR" -mindepth 1 -maxdepth 1 \
      ! -name 'venv' \
      ! -name '*.session' \
      ! -name 'config.json' \
      ! -name 'accounts.json' \
      ! -name 'tasks.json' \
      -exec rm -rf {} +
    cp -a ./ "$APP_DIR"/
    rm -rf "$APP_DIR/.git" || true
  fi
  chown -R "$APP_USER:$APP_USER" "$APP_DIR"

  echo "==> 创建虚拟环境并安装依赖（telethon, apscheduler）"
  sudo -u "$APP_USER" bash -lc "
    set -e
    cd '$APP_DIR'
    if ! $PYTHON_BIN -m venv venv 2>/dev/null; then
      echo 'python -m venv 失败，尝试使用 virtualenv 兜底……'
      $PYTHON_BIN -m pip install --user --upgrade pip || true
      $PYTHON_BIN -m pip install --user virtualenv || true
      $PYTHON_BIN -m virtualenv venv
    fi
    source venv/bin/activate
    python -m pip install --upgrade pip
    pip install -r requirements.txt
  "

  echo "==> 写入 systemd 服务：/etc/systemd/system/${SERVICE_NAME}.service"
  cat >/etc/systemd/system/${SERVICE_NAME}.service <<EOF
[Unit]
Description=TG Auto Checkin Manager Bot
Wants=network-online.target
After=network-online.target

[Service]
User=${APP_USER}
WorkingDirectory=${APP_DIR}
Environment=PYTHONUNBUFFERED=1
Environment=LANG=C.UTF-8
Environment=LC_ALL=C.UTF-8
Environment=PYTHONIOENCODING=utf-8
Environment=TZ=Asia/Shanghai
ExecStart=${APP_DIR}/venv/bin/python ${APP_DIR}/main.py
Restart=always
RestartSec=5
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload

  echo
  echo "==> 首次运行以完成最小配置（api_id/api_hash/bot_token/admin_ids）"
  echo "完成后按 Ctrl+C 退出再继续。"
  set +e
  sudo -u "$APP_USER" bash -lc "cd '$APP_DIR'; ./venv/bin/python main.py"
  set -e

  echo "==> 启用并启动服务"
  systemctl enable ${SERVICE_NAME}
  systemctl restart ${SERVICE_NAME}

  echo
  echo "✅ 安装完成！"
  echo "• 查看日志：journalctl -u ${SERVICE_NAME} -f"
  echo "• 修改配置：sudo -u ${APP_USER} nano ${APP_DIR}/config.json"
  echo "• 多账号登录：在 Telegram 里给管理 Bot 发 /help → /adduser 开始登录流程"
}

direct_uninstall() {
  require_root
  systemctl stop ${SERVICE_NAME} || true
  systemctl disable ${SERVICE_NAME} || true
  rm -f /etc/systemd/system/${SERVICE_NAME}.service
  systemctl daemon-reload

  rm -rf "${APP_DIR}"

  id -u "${APP_USER}" >/dev/null 2>&1 && userdel -r "${APP_USER}" || true

  echo "✅ 已卸载（如保留了 ${APP_DIR}，会话与配置仍在）。"
}

direct_manage() {
  require_root
  local cmd="${1:-}"
  if [[ -z "$cmd" ]]; then
    cat <<EOF
直接安装管理菜单：
  start     启动服务
  stop      停止服务
  restart   重启服务
  status    查看状态
  logs      实时日志
  edit      编辑配置文件（config.json）
  update    从当前仓库目录同步到 ${APP_DIR} 并重启
EOF
    return 0
  fi
  case "$cmd" in
    start) systemctl start "$SERVICE_NAME" ;;
    stop) systemctl stop "$SERVICE_NAME" ;;
    restart) systemctl restart "$SERVICE_NAME" ;;
    status) systemctl status "$SERVICE_NAME" ;;
    logs) journalctl -u "$SERVICE_NAME" -f ;;
    edit) sudo -u "$APP_USER" ${EDITOR:-nano} ${APP_DIR}/config.json ;;
    update)
      echo "==> 同步当前目录到 ${APP_DIR}"
      rsync -a --delete --exclude=".git" ./ "${APP_DIR}/"
      chown -R "$APP_USER:$APP_USER" "${APP_DIR}"
      systemctl restart "$SERVICE_NAME"
      ;;
    *) echo "未知命令：$cmd" ;;
  esac
}

docker_install() {
  echo "=== TG Checkin Docker 安装器 ==="
  read -rp "API_ID (my.telegram.org): " API_ID
  read -rp "API_HASH: " API_HASH
  read -rp "BOT_TOKEN (来自 BotFather): " BOT_TOKEN
  read -rp "管理员 ID（逗号分隔，数字）: " ADMIN_IDS
  read -rp "容器名称 [${DEFAULT_CONTAINER}]: " CONTAINER_NAME
  CONTAINER_NAME="${CONTAINER_NAME:-$DEFAULT_CONTAINER}"
  read -rp "数据目录 [${PWD}/${CONTAINER_NAME}-data]: " DATA_DIR
  DATA_DIR="${DATA_DIR:-${PWD}/${CONTAINER_NAME}-data}"

  mkdir -p "$DATA_DIR"

  echo "-> 构建镜像 ${IMAGE_NAME}"
  docker build -t "${IMAGE_NAME}" -f docker/Dockerfile .

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
}

docker_update() {
  echo "=== TG Checkin Docker 更新 ==="
  read -rp "容器名称 [${DEFAULT_CONTAINER}]: " CONTAINER_NAME
  CONTAINER_NAME="${CONTAINER_NAME:-$DEFAULT_CONTAINER}"

  local envs
  envs="$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "${CONTAINER_NAME}" 2>/dev/null || true)"
  local data_dir
  data_dir="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/data"}}{{println .Source}}{{end}}{{end}}' "${CONTAINER_NAME}" 2>/dev/null || true)"

  local api_id api_hash bot_token admin_ids log_channel log_enabled
  api_id="$(printf '%s\n' "$envs" | awk -F= '$1=="API_ID"{print substr($0,index($0,"=")+1)}' | tail -n1)"
  api_hash="$(printf '%s\n' "$envs" | awk -F= '$1=="API_HASH"{print substr($0,index($0,"=")+1)}' | tail -n1)"
  bot_token="$(printf '%s\n' "$envs" | awk -F= '$1=="BOT_TOKEN"{print substr($0,index($0,"=")+1)}' | tail -n1)"
  admin_ids="$(printf '%s\n' "$envs" | awk -F= '$1=="ADMIN_IDS"{print substr($0,index($0,"=")+1)}' | tail -n1)"
  log_channel="$(printf '%s\n' "$envs" | awk -F= '$1=="LOG_CHANNEL"{print substr($0,index($0,"=")+1)}' | tail -n1)"
  log_enabled="$(printf '%s\n' "$envs" | awk -F= '$1=="LOG_ENABLED"{print substr($0,index($0,"=")+1)}' | tail -n1)"

  if [[ -z "$data_dir" ]]; then
    read -rp "数据目录（挂载到 /data）: " data_dir
  fi
  if [[ -z "$data_dir" ]]; then
    echo "未提供数据目录，无法更新。"
    exit 1
  fi

  local cfg_file="${data_dir}/config.json"
  if [[ ! -f "$cfg_file" ]]; then
    if [[ -z "$api_id" ]]; then read -rp "API_ID: " api_id; fi
    if [[ -z "$api_hash" ]]; then read -rp "API_HASH: " api_hash; fi
    if [[ -z "$bot_token" ]]; then read -rp "BOT_TOKEN: " bot_token; fi
    if [[ -z "$admin_ids" ]]; then read -rp "管理员 ID（逗号分隔）: " admin_ids; fi
  fi

  echo "-> 构建镜像 ${IMAGE_NAME}"
  docker build -t "${IMAGE_NAME}" -f docker/Dockerfile .

  echo "-> 停止并移除旧容器（如存在）"
  docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true

  echo "-> 启动容器 ${CONTAINER_NAME}"
  local run_args=()
  run_args+=(--name "${CONTAINER_NAME}")
  if [[ -n "$api_id" ]]; then run_args+=(-e "API_ID=${api_id}"); fi
  if [[ -n "$api_hash" ]]; then run_args+=(-e "API_HASH=${api_hash}"); fi
  if [[ -n "$bot_token" ]]; then run_args+=(-e "BOT_TOKEN=${bot_token}"); fi
  if [[ -n "$admin_ids" ]]; then run_args+=(-e "ADMIN_IDS=${admin_ids}"); fi
  if [[ -n "$log_channel" ]]; then run_args+=(-e "LOG_CHANNEL=${log_channel}"); fi
  if [[ -n "$log_enabled" ]]; then run_args+=(-e "LOG_ENABLED=${log_enabled}"); fi
  run_args+=(-v "${data_dir}:/data")

  docker run -d "${run_args[@]}" "${IMAGE_NAME}"

  echo
  echo "✅ Docker 更新完成！"
  echo "容器：${CONTAINER_NAME}"
  echo "日志：docker logs -f ${CONTAINER_NAME}"
  echo "数据目录：${data_dir}"
}

main_menu() {
  cat <<EOF
请选择安装模式：
  1) 直接安装（systemd）
  2) Docker 安装
EOF
  read -rp "选择 [1-2]: " mode
  case "$mode" in
    1)
      cat <<EOF
直接安装功能：
  1) 安装
  2) 管理（start/stop/restart/status/logs/edit/update）
  3) 卸载
EOF
      read -rp "选择 [1-3]: " action
      case "$action" in
        1) direct_install ;;
        2)
          read -rp "管理命令 (start/stop/restart/status/logs/edit/update): " cmd
          direct_manage "$cmd"
          ;;
        3) direct_uninstall ;;
        *) echo "无效选择" ;;
      esac
      ;;
    2)
      cat <<EOF
Docker 功能：
  1) 安装
  2) 更新已安装容器
EOF
      read -rp "选择 [1-2]: " action
      case "$action" in
        1) docker_install ;;
        2) docker_update ;;
        *) echo "无效选择" ;;
      esac
      ;;
    *) echo "无效选择" ;;
  esac
}

main_menu

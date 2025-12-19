# Telegram Checkin Bot

一个基于 **Telethon** + **Apscheduler** 的 **Telegram 自动签到机器人**。  
支持使用 **个人账号** 完成签到，而不是 Bot API 直接发消息。  

## ✨ 功能特点

- 🤖 **Bot 管理**：所有任务和账号管理都通过一个 Bot 完成
- 👥 **多账号支持**：可同时登录多个 Telegram 个人号
- ⏰ **灵活定时**：支持 6 字段 Cron（含秒）或 `100s/36h` 间隔表达式（上海时区）
- 🧩 **任务模板**：可定义模板并批量创建引用模板的定时任务，一改模板即可同步所有任务
- 📝 **任务备注**：每个任务可带备注，方便区分
- 📊 **状态监控**：随时查看账号实时状态（在线/离线/最近上线）
- 🔐 **独立运行环境**：自动创建虚拟环境，依赖与系统隔离
- ⚙️ **一键部署**：`install.sh` 安装并注册 systemd 服务，开机自启
- 📦 **干净卸载**：`uninstall.sh` 移除服务与数据

---

## 🚀 安装部署

### 1. 克隆仓库
```bash
git clone https://github.com/kelenetwork/telegram-checkin-bot.git
cd telegram-checkin-bot
```

### 2. 一键安装
```bash
sudo bash install.sh
```

脚本会完成：
- 安装系统依赖（Python3/venv/tzdata/git 等）
- 创建运行用户 `tgcheckin`
- 部署到 `/opt/tg-checkin`
- 创建虚拟环境并安装 `telethon`、`apscheduler`
- 注册 `systemd` 服务，开机自启
- 首次运行 `main.py` 引导你输入：
  - `api_id` / `api_hash`（[my.telegram.org](https://my.telegram.org) 申请）
  - 管理 Bot 的 `bot_token`（@BotFather 获取）
  - 管理员账号 ID（可在 [@userinfobot](https://t.me/userinfobot) 查询）

---

## 🛠 管理脚本

仓库提供 `manage.sh`，简化日常管理：

```bash
bash manage.sh start     # 启动服务
bash manage.sh stop      # 停止服务
bash manage.sh restart   # 重启服务
bash manage.sh status    # 查看状态
bash manage.sh logs      # 实时日志
bash manage.sh edit      # 编辑 config.json
bash manage.sh update    # 从仓库更新到 /opt 并重启
```

---

## 📖 使用说明

在 Telegram 中，用管理员账号向管理 Bot 发送命令。

### 账号管理
```
/adduser 别名 | +8613800138000     # 添加账号（会发送验证码到手机）
/code 别名 | 12345                 # 输入验证码
/pass 别名 | 二步验证密码           # 如需要
/listusers                         # 列出已登录账号
/removeuser 别名                   # 移除账号
```

### 任务管理
```
/addtask 目标 | CRON/间隔 | 文本(多条用||) | 账号别名 | 备注 | 消息延迟s(-=无) | 发言ID(-=本账号/none=禁用)
/edittask ID | 目标 | CRON/间隔 | 文本(多条用||) | 账号别名 | 备注 | 消息延迟s(-=无) | 发言ID(-=本账号/none=禁用)
/listtasks
/deltask ID
/toggle ID
/test 目标 | 文本 | 账号别名 | 消息延迟s(-=无) | 发言ID(-=本账号/none=禁用)
/nextinterval ID 或 all          # 查看间隔任务剩余时间
/delaynext ID | 秒数              # 临时调整间隔任务的下一次执行时间
/listtpl                              # 查看模板
/addtpl 名称 | 目标 | 文本(多条用||)
/edittpl ID | 名称 | 目标 | 文本(多条用||)
/addtpltask 模板ID | CRON/间隔 | 账号别名 | 备注 | 消息延迟s(-=无) | 发言ID(-=本账号/none=禁用)
```
> CRON 支持 6 字段（秒 分 时 日 月 周），也可直接写 `100s` / `380m` / `36h` 表示每隔一定秒/分/小时执行一次。模板任务 ID 以 `T` 开头（例如 `T1`），在 `/edittask`、`/deltask`、`/toggle`、`/nextinterval`、`/delaynext` 中都可以直接使用。

### 状态与查询
```
/status                            # 查看所有账号状态
/me 别名                           # 查看某个账号信息
/whois 目标                        # 解析目标信息
```

---

## 🐳 Docker 部署

也可以通过 Docker 运行（初次会根据环境变量生成 `config.json`）。提供脚本 `./docker-install.sh`，会交互式询问 `API_ID`/`API_HASH`/`BOT_TOKEN`/`ADMIN_IDS`、容器名称与数据目录，然后执行 `docker build` + `docker run`。你可以多次运行该脚本，为不同容器指定不同名称及数据目录，从而实现多实例。

也可手动执行（等同于脚本步骤）：

```bash
docker build -t tg-checkin-bot .
docker run -d --name tg-checkin \
  -e API_ID=123456 \
  -e API_HASH=your_api_hash \
  -e BOT_TOKEN=123456:abcDEFghIJKLmno \
  -e ADMIN_IDS=111111111,222222222 \
  -v $(pwd)/data:/data \
  tg-checkin-bot
```

- `API_ID` / `API_HASH`：在 [my.telegram.org](https://my.telegram.org) 申请  
- `BOT_TOKEN`：@BotFather 下发  
- `ADMIN_IDS`：逗号分隔的 Telegram 用户 ID  
- `/data` 用于持久化 `config.json`、`accounts.json`、`tasks.json`、`*.session`，请绑定到宿主目录
- 可选：在 `config.json` 中设置 `"log_channel": "-100xxxx"`（以及 `"log_enabled": true`）即可让 Bot 将启动/任务执行日志同步到指定频道或群组

如需多实例部署，请为每个容器指定不同的 `--name` 与宿主数据目录（例如 `-v $(pwd)/data-a:/data`、`-v $(pwd)/data-b:/data`）。

容器日志可通过 `docker logs -f tg-checkin` 查看，其余命令与裸机部署相同。

---

## 🗂 数据目录

服务安装路径：`/opt/tg-checkin`  

- `main.py`：主程序  
- `config.json`：全局配置  
- `accounts.json`：账号清单（别名 → session 文件）  
- `tasks.json`：常规任务配置  
- `templates.json`：任务模板定义  
- `template_tasks.json`：基于模板创建的任务  
- `token/`：统一存放 `bot.session`、`bot_token`、`user-<alias>.session` 等敏感文件，方便迁移与隔离  

---

## 🧰 常见问题

1. **数字 ID 无法发送消息？**  
   - 需确保你的个人号与该对象有过会话；  
   - 否则请使用 `@用户名` 或 `t.me/链接` 或 `-100` 开头的群/频道 ID。

2. **服务日志在哪里看？**  
   ```bash
   journalctl -u tg-checkin -f
   ```

3. **修改配置后如何生效？**  
   - 用 `manage.sh restart` 重启服务。

---

## ❌ 卸载

```bash
sudo bash uninstall.sh
```

会移除 systemd 服务，删除 `/opt/tg-checkin` 与用户 `tgcheckin`。  
如要保留数据，可手动备份 `config.json / tasks.json / accounts.json / *.session`。

---

## 📜 License

MIT

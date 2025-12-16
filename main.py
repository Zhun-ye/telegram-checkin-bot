#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tg-auto-checkin: 多账号 + Bot 管理 + 个人号发送 + 定时（上海时区）
- 多账号：/adduser /code /pass /listusers /removeuser
- 任务：/addtask /edittask /listtasks /deltask /toggle /test
- 实时：/status（全部账号） /me <alias> /whois <target>
- CRON：6字段 crontab（含秒）或间隔表达式（100s/380m/36h）；时区 Asia/Shanghai
- 任务字段：target/cron/messages/account/remark/delay/send_as/enabled
依赖：telethon, apscheduler（自动安装，兼容 PEP 668）
数据：config.json / accounts.json / tasks.json
会话文件：user-<alias>.session（个人号），bot.session（机器人）
"""

import asyncio
import json
import os
import re
import sys
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

# 终端编码保护
os.environ.setdefault("LANG", "C.UTF-8")
os.environ.setdefault("LC_ALL", "C.UTF-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# ---------------- 依赖安装（兼容 PEP 668） ----------------
def ensure(pkgs):
    for p in pkgs:
        try:
            __import__(p)
        except Exception:
            print(f"未检测到 {p}，正在安装……")
            try:
                subprocess.check_call([sys.executable, "-m", "pip", "install", p])
            except subprocess.CalledProcessError:
                print(f"常规安装 {p} 失败，尝试 --break-system-packages ……")
                subprocess.check_call([sys.executable, "-m", "pip", "install", p, "--break-system-packages"])

ensure(["telethon", "apscheduler"])

from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError
from telethon.tl.types import UserStatusOnline, UserStatusOffline
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from zoneinfo import ZoneInfo  # Python3.9+

# ---------------- 常量/文件 ----------------
CONFIG = Path("config.json")
ACCOUNTS = Path("accounts.json")
TASKS = Path("tasks.json")
TOKEN_DIR = Path("token")
TOKEN_DIR.mkdir(exist_ok=True)
BOT_SESSION = str(TOKEN_DIR / "bot")  # 机器人会话
SH_TZ = ZoneInfo("Asia/Shanghai")
BOT_TOKEN_FILE = TOKEN_DIR / "bot_token"
FIELD_PLACEHOLDER = "-"  # 命令中未设置字段的占位符
SEND_AS_DISABLE = "__NO_SEND_AS__"
SEND_AS_DISABLE_KEYWORDS = {"none", "no", "off", "0", "disable", "禁用"}
INTERVAL_KEYWORD_MAP = {
    "s": "seconds", "sec": "seconds", "secs": "seconds", "second": "seconds", "seconds": "seconds",
    "秒": "seconds", "秒钟": "seconds",
    "m": "minutes", "min": "minutes", "mins": "minutes", "minute": "minutes", "minutes": "minutes",
    "分": "minutes", "分钟": "minutes",
    "h": "hours", "hr": "hours", "hrs": "hours", "hour": "hours", "hours": "hours",
    "时": "hours", "小时": "hours",
}
INTERVAL_KEYWORD_PATTERN = "|".join(sorted([re.escape(k) for k in INTERVAL_KEYWORD_MAP.keys()], key=len, reverse=True))
INTERVAL_REGEX = re.compile(rf"^\\s*(\\d+)\\s*({INTERVAL_KEYWORD_PATTERN})\\s*$", re.IGNORECASE)
DOUBLE_PIPE_PLACEHOLDER = "__DOUBLE_PIPE__"

def format_seconds(sec: int) -> str:
    sec = max(0, int(sec))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    parts = []
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    parts.append(f"{s}s")
    return "".join(parts)

# ---------------- 基础工具 ----------------
def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return default

def save_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def prompt(label, default=None):
    tip = f"{label}"
    if default is not None:
        tip += f" [{default}]"
    tip += ": "
    sys.stdout.write(tip); sys.stdout.flush()
    line = sys.stdin.buffer.readline()
    val = (line or b"").decode("utf-8", errors="ignore").strip()
    if not val and default is not None:
        val = str(default).strip()
    if not val:
        print("不能为空，请重输。")
        return prompt(label, default)
    return val

INTERVAL_UNIT_SECONDS = {"seconds": 1, "minutes": 60, "hours": 3600}

def classify_schedule(expr: str) -> dict:
    """解析表达式，返回 {'mode': 'interval', 'seconds': ...} 或 {'mode':'cron','expr':...}"""
    expr = (expr or "").strip()
    if not expr:
        raise ValueError("CRON/间隔表达式不能为空")
    m = INTERVAL_REGEX.match(expr)
    if m:
        value = int(m.group(1))
        unit_key = m.group(2).lower()
        unit = INTERVAL_KEYWORD_MAP[unit_key]
        seconds = value * INTERVAL_UNIT_SECONDS[unit]
        if seconds <= 0:
            raise ValueError("间隔必须大于 0")
        return {"mode": "interval", "seconds": seconds, "expr": expr}
    compact = expr.replace(" ", "").lower()
    for key in sorted(INTERVAL_KEYWORD_MAP.keys(), key=len, reverse=True):
        if compact.endswith(key):
            num_part = compact[:-len(key)]
            if num_part.isdigit():
                unit = INTERVAL_KEYWORD_MAP[key]
                seconds = int(num_part) * INTERVAL_UNIT_SECONDS[unit]
                if seconds <= 0:
                    raise ValueError("间隔必须大于 0")
                return {"mode": "interval", "seconds": seconds, "expr": expr}
            continue
    fields = expr.split()
    if len(fields) == 6:
        return {"mode": "cron", "expr": expr, "with_seconds": True, "fields": fields}
    if len(fields) == 5:
        return {"mode": "cron", "expr": expr, "with_seconds": False, "fields": fields}
    raise ValueError("CRON 表达式需 5 或 6 字段，或使用 100s/5m/3h 这种间隔格式。")

def build_trigger(schedule: dict):
    if schedule["mode"] == "interval":
        return IntervalTrigger(seconds=schedule["seconds"], timezone=SH_TZ)
    expr = schedule["expr"]
    with_seconds = schedule.get("with_seconds", False)
    if with_seconds:
        fields = schedule.get("fields") or expr.split()
        return CronTrigger(
            second=fields[0],
            minute=fields[1],
            hour=fields[2],
            day=fields[3],
            month=fields[4],
            day_of_week=fields[5],
            timezone=SH_TZ,
        )
    return CronTrigger.from_crontab(expr, timezone=SH_TZ)

def split_command_fields(body: str):
    masked = body.replace("||", DOUBLE_PIPE_PLACEHOLDER)
    parts = [x.strip().replace(DOUBLE_PIPE_PLACEHOLDER, "||") for x in masked.split("|")]
    return parts

def fmt_entity(ent):
    name = getattr(ent, "title", None) or \
           (" ".join([getattr(ent, "first_name", "") or "", getattr(ent, "last_name", "") or ""]).strip() or "(无名)")
    uname = f"@{getattr(ent, 'username', None)}" if getattr(ent, "username", None) else "(无)"
    eid = getattr(ent, "id", None)
    kind = ent.__class__.__name__
    return f"{name} {uname} | id={eid} | type={kind}"

async def resolve_entity(client: TelegramClient, t: str):
    t = str(t).strip()
    if t.lower() in ("me", "self"):  # 收藏夹
        return await client.get_entity("me")
    if t.startswith("@") or "t.me/" in t:
        return await client.get_entity(t)
    if t.startswith("-100") or (t.startswith("-") and t[1:].isdigit()):
        return await client.get_entity(int(t))
    if t.isdigit():
        target_id = int(t)
        async for dialog in client.iter_dialogs():
            ent = dialog.entity
            if getattr(ent, "id", None) == target_id:
                return ent
        raise ValueError(f"无法通过数字ID {t} 找到对象；请先与其建立会话，或改用 @用户名 / t.me 链接 / -100群ID。")
    return await client.get_entity(t)

async def resolve_send_as(client: TelegramClient, target):
    if target in (None, "", FIELD_PLACEHOLDER):
        peer = "me"
    else:
        txt = str(target).strip()
        if not txt:
            peer = "me"
        elif txt.lstrip("-").isdigit():
            peer = int(txt)
        else:
            peer = txt
    return await client.get_input_entity(peer)

async def ensure_send_as_permission(api_id, api_hash, alias: str, send_as_val):
    if send_as_val in (None, SEND_AS_DISABLE):
        return
    client = await get_or_start_client(api_id, api_hash, alias)
    if not await client.is_user_authorized():
        raise RuntimeError(f"账号 {alias} 未登录，无法校验发言ID。")
    me = await client.get_me()
    if not getattr(me, "premium", False):
        raise ValueError("该账号未开通 Telegram Premium，无法以频道身份发言，请使用 `-` 作为发言ID。")
    if str(send_as_val).lstrip("-").isdigit() and int(send_as_val) == getattr(me, "id", None):
        return
    ent = await resolve_entity(client, send_as_val)
    if not getattr(ent, "creator", False):
        raise ValueError("只有频道创建者才能设置此发言ID。")

# ---------------- 配置与状态 ----------------
def ensure_config():
    cfg = load_json(CONFIG, {})
    if not cfg.get("api_id"):
        cfg["api_id"] = int(prompt("请输入 api_id（my.telegram.org 申请）"))
    if not cfg.get("api_hash"):
        cfg["api_hash"] = prompt("请输入 api_hash")
    token_text = None
    if BOT_TOKEN_FILE.exists():
        token_text = BOT_TOKEN_FILE.read_text(encoding="utf-8").strip()
    elif cfg.get("bot_token"):
        token_text = cfg["bot_token"].strip()
        BOT_TOKEN_FILE.write_text(token_text, encoding="utf-8")
    else:
        token_text = prompt("请输入 管理Bot 的 bot_token")
        BOT_TOKEN_FILE.write_text(token_text, encoding="utf-8")
    cfg["bot_token"] = token_text
    if not cfg.get("admin_ids"):
        admin = prompt("请输入管理员用户ID（数字，可多个，逗号分隔）")
        ids = []
        for x in admin.split(","):
            x = x.strip()
            if x.isdigit():
                ids.append(int(x))
        cfg["admin_ids"] = ids
    save_json(CONFIG, cfg)
    return cfg

def ensure_accounts():
    data = load_json(ACCOUNTS, {"users": {}})  # { alias: {phone, session_file} }
    changed = False
    for alias, info in data.get("users", {}).items():
        path = session_file_for(alias)
        if info.get("session_file") != path:
            info["session_file"] = path
            changed = True
    if changed:
        save_json(ACCOUNTS, data)
    return data

def ensure_tasks():
    data = load_json(TASKS, {"tasks": [], "seq": 1})
    save_json(TASKS, data)
    return data

def normalize_task_entry(task: dict) -> bool:
    changed = False
    msgs_missing = "messages" not in task
    msgs = task.get("messages")
    if not isinstance(msgs, list) or not msgs:
        legacy = task.get("message")
        if legacy is None:
            legacy = ""
        msgs = [legacy]
        changed = True
    elif msgs_missing:
        changed = True
    normalized_msgs = [str(m) for m in msgs]
    if normalized_msgs != msgs:
        changed = True
    task["messages"] = normalized_msgs

    delay_missing = "delay" not in task
    delay_raw = task.get("delay", 0)
    try:
        delay_val = max(0, int(delay_raw))
    except Exception:
        delay_val = 0
    if delay_val != delay_raw or delay_missing:
        changed = True
    task["delay"] = delay_val

    send_as_missing = "send_as" not in task
    send_as_raw = task.get("send_as")
    if send_as_raw is None and "reply_to" in task:
        send_as_raw = task.pop("reply_to")
        changed = True
    if send_as_raw is None:
        send_as_val = None
    else:
        send_as_txt = str(send_as_raw).strip()
        if not send_as_txt or send_as_txt == FIELD_PLACEHOLDER:
            send_as_val = None
        else:
            send_as_val = send_as_txt
    if send_as_missing or task.get("send_as") != send_as_val:
        task["send_as"] = send_as_val
        changed = True

    schedule = task.get("schedule")
    if not schedule:
        expr = task.get("cron") or task.get("schedule_expr") or ""
        schedule = classify_schedule(expr)
        task["schedule"] = schedule
        changed = True
    if schedule.get("mode") == "cron" and not schedule.get("fields"):
        schedule["fields"] = schedule.get("expr", "").split()
        changed = True
    if task.get("cron") != schedule.get("expr"):
        task["cron"] = schedule.get("expr")
        changed = True
    return changed

# 运行时客户端池：{alias: TelegramClient}
CLIENTS: dict[str, TelegramClient] = {}
PENDING: dict[int, dict] = {}  # 管理员对话中的登录流程状态 {admin_id: {"alias":..., "phone":...}}
ACCOUNT_LOCKS: dict[str, asyncio.Lock] = {}

# ---------------- 账号管理 ----------------
def session_file_for(alias: str) -> str:
    TOKEN_DIR.mkdir(exist_ok=True)
    new_path = TOKEN_DIR / f"user-{alias}.session"
    old_path = Path(f"user-{alias}.session")
    if old_path.exists() and not new_path.exists():
        try:
            old_path.replace(new_path)
        except Exception:
            new_path.write_bytes(old_path.read_bytes())
            old_path.unlink(missing_ok=True)
    return str(new_path)

async def get_or_start_client(api_id, api_hash, alias: str) -> TelegramClient:
    if alias in CLIENTS:
        return CLIENTS[alias]
    sess = session_file_for(alias)
    client = TelegramClient(sess, api_id, api_hash)
    await client.connect()
    CLIENTS[alias] = client
    return client

async def user_status(client: TelegramClient):
    me = await client.get_me()
    st = getattr(me, "status", None)
    online = None; last = None
    if isinstance(st, UserStatusOnline):
        online = True
    elif isinstance(st, UserStatusOffline):
        online = False; last = st.was_online
    return me, online, last

# ---------------- 任务发送 ----------------
async def send_with_user(api_id, api_hash, alias: str, target: str, texts, delay=0, send_as=None):
    lock = ACCOUNT_LOCKS.setdefault(alias, asyncio.Lock())
    async with lock:
        client = await get_or_start_client(api_id, api_hash, alias)
        if not await client.is_user_authorized():
            raise RuntimeError(f"账号 {alias} 未登录，请先在 Bot 中完成 /adduser → /code（→ /pass）流程。")
        ent = await resolve_entity(client, target)
        if isinstance(texts, str):
            messages = [texts]
        else:
            messages = [str(x) for x in texts]
        delay = max(0, int(delay or 0))
        send_as_peer = None
        if send_as != SEND_AS_DISABLE and not getattr(ent, "bot", False) and ent.__class__.__name__ != "User":
            send_as_peer = await resolve_send_as(client, send_as)
        for idx, text in enumerate(messages):
            if not text:
                continue
            kwargs = {"send_as": send_as_peer} if send_as_peer else {}
            await client.send_message(ent, text, **kwargs)
            if delay and idx < len(messages) - 1:
                await asyncio.sleep(delay)
        if delay and messages:
            await asyncio.sleep(delay)

# ---------------- 主流程 ----------------
async def main():
    cfg = ensure_config()
    acc = ensure_accounts()
    tasks_state = ensure_tasks()
    upgraded = False
    for t in tasks_state["tasks"]:
        if normalize_task_entry(t):
            upgraded = True
    if upgraded:
        save_json(TASKS, tasks_state)

    # 迁移旧 bot.session
    old_bot_session = Path("bot.session")
    new_bot_session = TOKEN_DIR / "bot.session"
    if old_bot_session.exists() and not new_bot_session.exists():
        try:
            old_bot_session.replace(new_bot_session)
        except Exception:
            new_bot_session.write_bytes(old_bot_session.read_bytes())
            old_bot_session.unlink(missing_ok=True)

    # 机器人
    bot = TelegramClient(BOT_SESSION, cfg["api_id"], cfg["api_hash"])
    await bot.start(bot_token=cfg["bot_token"])

    log_channel = cfg.get("log_channel")
    log_enabled = bool(log_channel) and cfg.get("log_enabled", True)

    async def send_log_message(text: str):
        if not log_enabled or not text:
            return
        try:
            await bot.send_message(log_channel, text)
        except Exception as ex:
            print(f"[log] 发送失败：{ex}")

    async def execute_task(task_id: int):
        t = next((x for x in tasks_state["tasks"] if x["id"] == task_id), None)
        if not t:
            await send_log_message(f"⚠️ 任务 #{task_id} 不存在，已跳过。")
            return
        normalize_task_entry(t)
        info = f"任务#{task_id} [{t['account']}] -> {t['target']}"
        try:
            await send_with_user(cfg["api_id"], cfg["api_hash"], t["account"], t["target"],
                                 t["messages"], t.get("delay", 0), t.get("send_as"))
            await send_log_message(f"✅ {info} 已执行。")
        except Exception as exc:
            await send_log_message(f"❌ {info} 发送失败：{exc}")
            raise

    # 调度器（上海时区）
    scheduler = AsyncIOScheduler(timezone=SH_TZ)
    scheduler.start()

    # 恢复任务
    def add_job_from_task(t):
        if not t.get("enabled", True):
            return
        normalize_task_entry(t)
        trig = build_trigger(t["schedule"])
        scheduler.add_job(
            execute_task,
            trigger=trig,
            args=[t["id"]],
            id=str(t["id"]),
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=120,
        )

    for t in tasks_state["tasks"]:
        try:
            add_job_from_task(t)
        except Exception as e:
            print(f"任务 {t.get('id')} 恢复失败：{e}")

    def is_interval_task(task):
        normalize_task_entry(task)
        return task.get("schedule", {}).get("mode") == "interval"

    def describe_next_run(task):
        job = scheduler.get_job(str(task["id"]))
        if not job or not job.next_run_time:
            return "未找到调度"
        next_run = job.next_run_time.astimezone(SH_TZ)
        remain = (next_run - datetime.now(SH_TZ)).total_seconds()
        return f"{next_run.strftime('%Y-%m-%d %H:%M:%S')}，剩余 {format_seconds(remain)}"

    # 权限
    ADMINS = set(cfg["admin_ids"])
    def admin_ok(event): return event.sender_id in ADMINS

     # 漂亮的帮助
    def help_card():
        return (
            "🧭 *签到机器人 · 管理菜单*\n"
            "—— *任务管理* ——\n"
            "`/addtask` 目标 `|` CRON/间隔 `|` 文本(多条用`||`) `|` 账号别名 `|` 备注 `|` 消息延迟(`-`=无延迟) `|` 发言ID(`-`=本账号/none=禁用)\n"
            "`/edittask` ID `|` 目标 `|` CRON/间隔 `|` 文本(多条用`||`) `|` 账号别名 `|` 备注 `|` 消息延迟(`-`=无) `|` 发言ID(`-`=本账号/none=禁用)\n"
            "`/listtasks`  列出任务\n"
            "`/deltask` ID 删除任务\n"
            "`/toggle` ID 启/停任务\n"
            "`/test` 目标 `|` 文本(多条用`||`) `|` 账号别名 `|` 消息延迟(`-`=无延迟) `|` 发言ID(`-`=本账号)  立即测试\n"
            "`/nextinterval ID` 查看某个间隔任务剩余时间；`/nextinterval all` 查看全部间隔任务\n"
            "`/delaynext ID | 秒数` 临时调整间隔任务的下一次执行时间\n"
            "占位符 `-` 表示不设置；发言ID=none 可禁用 send-as；消息延迟=多条消息之间等待时间\n\n"
            "—— *账号管理* ——\n"
            "`/adduser` 别名 `|` 手机号(含国家码)\n"
            "`/code`   别名 `|` 验证码\n"
            "`/pass`   别名 `|` 二步验证密码   （如需要）\n"
            "`/listusers` 列出账号\n"
            "`/removeuser` 别名  移除账号\n\n"
            "—— *状态/查询* ——\n"
            "`/status`        查看所有账号状态\n"
            "`/me` 别名       查看某账号登录信息\n"
            "`/whois` 目标     解析目标信息\n\n"
            "*CRON 示例*\n"
            "`0 0 9 * * *`  每天 09:00（秒 分 时 日 月 周）\n"
            "`30 */10 * * * *`  每 10 分钟执行，并在周期内第 30 秒触发\n"
            "`100s` / `380m` / `36h`  表示纯间隔定时（秒/分钟/小时）\n"
            "支持 6 字段（含秒）的 cron 表达式，也兼容 `100s` 这类间隔格式（单位：s/m/h）；目标可用：@用户名 / t.me 链接 / -100群ID / 数字用户ID（需在会话列表） / me\n"
        )

    @bot.on(events.NewMessage(pattern=r"^/(start|help)$"))
    async def _(e):
        if not admin_ok(e): return
        await e.respond(help_card(), parse_mode="md")

    # ---------- 账号管理 ----------
    @bot.on(events.NewMessage(pattern=r"^/adduser\s+(.+)"))
    async def _(e):
        if not admin_ok(e): return
        try:
            body = e.pattern_match.group(1).strip()
            alias, phone = [x.strip() for x in body.split("|", 1)]
            if not alias or not phone:
                await e.reply("格式：`/adduser 别名 | 手机号(含国家码)`", parse_mode="md"); return
            if alias in load_json(ACCOUNTS, {"users":{}})["users"]:
                await e.reply("该别名已存在，如需重登请先 `/removeuser 别名`。", parse_mode="md"); return
            # 启动临时 client 发验证码
            client = await get_or_start_client(cfg["api_id"], cfg["api_hash"], alias)
            await client.send_code_request(phone)
            PENDING[e.sender_id] = {"alias": alias, "phone": phone}
            await e.reply(f"已向 {phone} 发送验证码。\n请发送：`/code {alias} | 12345`", parse_mode="md")
        except Exception as ex:
            await e.reply(f"❌ 发送验证码失败：{ex}")

    @bot.on(events.NewMessage(pattern=r"^/code\s+(.+)"))
    async def _(e):
        if not admin_ok(e): return
        try:
            body = e.pattern_match.group(1).strip()
            alias, code = [x.strip() for x in body.split("|", 1)]
            pend = PENDING.get(e.sender_id)
            if not pend or pend.get("alias") != alias:
                await e.reply("请先 `/adduser 别名 | 手机号` 以发送验证码。", parse_mode="md"); return
            client = await get_or_start_client(cfg["api_id"], cfg["api_hash"], alias)
            try:
                await client.sign_in(phone=pend["phone"], code=code)
                # 保存到账户清单
                data = ensure_accounts()
                data["users"][alias] = {"phone": pend["phone"], "session_file": session_file_for(alias)}
                save_json(ACCOUNTS, data)
                PENDING.pop(e.sender_id, None)
                me, online, last = await user_status(client)
                await e.reply(f"✅ 登录成功 {alias}\n{fmt_entity(me)}")
            except SessionPasswordNeededError:
                await e.reply(f"需要二步验证密码，请发送：`/pass {alias} | 你的密码`", parse_mode="md")
        except Exception as ex:
            await e.reply(f"❌ 登录失败：{ex}")

    @bot.on(events.NewMessage(pattern=r"^/pass\s+(.+)"))
    async def _(e):
        if not admin_ok(e): return
        try:
            alias, pwd = [x.strip() for x in e.pattern_match.group(1).split("|", 1)]
            client = await get_or_start_client(cfg["api_id"], cfg["api_hash"], alias)
            await client.sign_in(password=pwd)
            pend = PENDING.pop(e.sender_id, None)
            phone = pend["phone"] if pend and pend.get("alias")==alias else "(未知)"
            data = ensure_accounts()
            data["users"][alias] = {"phone": phone, "session_file": session_file_for(alias)}
            save_json(ACCOUNTS, data)
            me, online, last = await user_status(client)
            await e.reply(f"✅ 登录成功 {alias}\n{fmt_entity(me)}")
        except Exception as ex:
            await e.reply(f"❌ 二步验证失败：{ex}")

    @bot.on(events.NewMessage(pattern=r"^/listusers$"))
    async def _(e):
        if not admin_ok(e): return
        data = ensure_accounts()["users"]
        if not data:
            await e.reply("暂无已登录账号。"); return
        lines = []
        for alias, info in data.items():
            sess = Path(info["session_file"])
            mtime = datetime.fromtimestamp(sess.stat().st_mtime).astimezone(SH_TZ).strftime("%Y-%m-%d %H:%M:%S") if sess.exists() else "N/A"
            lines.append(f"- {alias}（{info.get('phone','') }） session:{sess.name} mtime:{mtime}")
        await e.reply("👥 账号列表：\n" + "\n".join(lines))

    @bot.on(events.NewMessage(pattern=r"^/removeuser\s+(.+)"))
    async def _(e):
        if not admin_ok(e): return
        alias = e.pattern_match.group(1).strip()
        data = ensure_accounts()
        if alias not in data["users"]:
            await e.reply("未找到该别名。"); return
        # 停止并删除会话文件
        try:
            if alias in CLIENTS:
                await CLIENTS[alias].disconnect()
                CLIENTS.pop(alias, None)
        except Exception:
            pass
        try:
            Path(session_file_for(alias)).unlink(missing_ok=True)
        except Exception:
            pass
        data["users"].pop(alias, None); save_json(ACCOUNTS, data)
        await e.reply(f"✅ 已移除账号 {alias}")

    # ---------- 任务管理 ----------
    @bot.on(events.NewMessage(pattern=r"^/addtask\s+(.+)"))
    async def _(e):
        if not admin_ok(e): return
        try:
            body = e.pattern_match.group(1).strip()
            # 目标 | CRON/间隔 | 文本 | 账号别名 | 备注 | 消息延迟 | 发言ID
            parts = split_command_fields(body)
            if len(parts) < 4:
                await e.reply("格式：`/addtask 目标 | CRON/间隔 | 文本 | 账号别名 | 备注 | 消息延迟(-=不设) | 发言ID(-=自账号)`", parse_mode="md"); return
            target, cron_expr, text_field, alias = parts[0], parts[1], parts[2], parts[3]
            remark = parts[4] if len(parts) >= 5 else ""
            delay_txt = parts[5] if len(parts) >= 6 else ""
            send_as_txt = parts[6] if len(parts) >= 7 else ""
            messages = [m.strip() for m in text_field.split("||") if m.strip()]
            if not messages:
                await e.reply("消息内容不能为空，可用 `||` 分隔多条。", parse_mode="md"); return
            delay_sec = 0
            if delay_txt and delay_txt != FIELD_PLACEHOLDER:
                if delay_txt.isdigit():
                    delay_sec = max(0, int(delay_txt))
                else:
                    await e.reply("消息延迟需为非负整数，或使用 `-` 表示不设置。", parse_mode="md"); return
            send_as_val = None
            if send_as_txt:
                txt = send_as_txt.strip()
                low = txt.lower()
                if txt == FIELD_PLACEHOLDER:
                    send_as_val = None
                elif low in SEND_AS_DISABLE_KEYWORDS:
                    send_as_val = SEND_AS_DISABLE
                elif txt.lstrip("-").isdigit():
                    send_as_val = txt
                else:
                    await e.reply("发言ID 需为数字ID、`-`（本账号）或 `none`（禁用）。", parse_mode="md"); return
            schedule = classify_schedule(cron_expr)
            if alias not in ensure_accounts()["users"]:
                await e.reply("账号别名不存在，请先 /listusers 查看或 /adduser 登陆。"); return
            await ensure_send_as_permission(cfg["api_id"], cfg["api_hash"], alias, send_as_val)

            tid = tasks_state["seq"]; tasks_state["seq"] += 1
            task = {
                "id": tid,
                "target": target,
                "cron": cron_expr,
                "messages": messages,
                "account": alias,
                "remark": remark,
                "delay": delay_sec,
                "send_as": send_as_val,
                "enabled": True,
                "schedule": schedule,
            }
            tasks_state["tasks"].append(task); save_json(TASKS, tasks_state)
            add_job_from_task(task)
            summary = f"{len(messages)}条消息，消息延迟{delay_sec}s"
            if send_as_val == SEND_AS_DISABLE:
                summary += "，发言ID=禁用"
            elif send_as_val:
                summary += f"，发言ID {send_as_val}"
            else:
                summary += "，发言ID=自账号"
            await e.reply(f"✅ 已添加任务 #{tid}\n[{alias}] {cron_expr} -> {target}\n{summary}\n备注：{remark or '（无）'}")
        except Exception as ex:
            await e.reply(f"❌ 添加失败：{ex}")

    @bot.on(events.NewMessage(pattern=r"^/edittask\s+(.+)"))
    async def _(e):
        if not admin_ok(e): return
        try:
            body = e.pattern_match.group(1).strip()
            # ID | 目标 | CRON/间隔 | 文本 | 账号别名 | 备注 | 消息延迟 | 发言ID
            parts = split_command_fields(body)
            if len(parts) < 5:
                await e.reply("格式：`/edittask ID | 目标 | CRON/间隔 | 文本 | 账号别名 | 备注 | 消息延迟(-=不设) | 发言ID(-=自账号)`", parse_mode="md"); return
            tid_txt, target, cron_expr, text_field, alias = parts[0], parts[1], parts[2], parts[3], parts[4]
            remark = parts[5] if len(parts) >= 6 else task.get("remark", "")
            delay_txt = parts[6] if len(parts) >= 7 else ""
            send_as_txt = parts[7] if len(parts) >= 8 else ""
            if not tid_txt.isdigit():
                await e.reply("ID 必须是数字。"); return
            tid = int(tid_txt)
            task = next((x for x in tasks_state["tasks"] if x["id"] == tid), None)
            if not task:
                await e.reply("未找到该任务 ID。"); return
            messages = [m.strip() for m in text_field.split("||") if m.strip()]
            if not messages:
                await e.reply("消息内容不能为空，可用 `||` 分隔多条。", parse_mode="md"); return
            delay_sec = task.get("delay", 0)
            if delay_txt:
                if delay_txt != FIELD_PLACEHOLDER:
                    if delay_txt.isdigit():
                        delay_sec = max(0, int(delay_txt))
                    else:
                        await e.reply("消息延迟需为非负整数，或使用 `-` 表示不设置。", parse_mode="md"); return
            send_as_val = task.get("send_as")
            if send_as_txt:
                txt = send_as_txt.strip()
                low = txt.lower()
                if txt == FIELD_PLACEHOLDER:
                    send_as_val = None
                elif low in SEND_AS_DISABLE_KEYWORDS:
                    send_as_val = SEND_AS_DISABLE
                elif txt.lstrip("-").isdigit():
                    send_as_val = txt
                else:
                    await e.reply("发言ID 需为数字ID、`-`（本账号）或 `none`（禁用）。", parse_mode="md"); return
            # 验证 cron & 账号
            schedule = classify_schedule(cron_expr)
            if alias not in ensure_accounts()["users"]:
                await e.reply("账号别名不存在，请先 /listusers 查看或 /adduser 登陆。"); return
            await ensure_send_as_permission(cfg["api_id"], cfg["api_hash"], alias, send_as_val)
            task.update({
                "target": target,
                "cron": cron_expr,
                "messages": messages,
                "account": alias,
                "remark": remark,
                "delay": delay_sec,
                "send_as": send_as_val,
                "schedule": schedule,
            })
            normalize_task_entry(task)
            save_json(TASKS, tasks_state)
            try:
                scheduler.remove_job(str(tid))
            except Exception:
                pass
            add_job_from_task(task)
            status = "ON" if task.get("enabled", True) else "OFF"
            summary = f"{len(messages)}条消息，消息延迟{delay_sec}s，状态{status}"
            if send_as_val == SEND_AS_DISABLE:
                summary += "，发言ID=禁用"
            elif send_as_val:
                summary += f"，发言ID {send_as_val}"
            else:
                summary += "，发言ID=本账号"
            await e.reply(f"✅ 任务 #{tid} 已更新。\n[{alias}] {cron_expr} -> {target}\n{summary}")
        except Exception as ex:
            await e.reply(f"❌ 修改失败：{ex}")

    @bot.on(events.NewMessage(pattern=r"^/listtasks$"))
    async def _(e):
        if not admin_ok(e): return
        if not tasks_state["tasks"]:
            await e.reply("暂无任务。"); return
        lines = []
        for t in tasks_state["tasks"]:
            normalize_task_entry(t)
            preview = t["messages"][0][:40] + ("..." if len(t["messages"][0]) > 40 else "")
            extra = f" 共{len(t['messages'])}条" if len(t["messages"]) > 1 else ""
            delay_val = t.get("delay", 0)
            delay_txt = f" 消息延迟:{delay_val}s" if delay_val else ""
            if t.get("send_as") == SEND_AS_DISABLE:
                send_as_txt = " 发言:禁用"
            elif t.get("send_as"):
                send_as_txt = f" 发言:{t['send_as']}"
            else:
                send_as_txt = " 发言:自账号"
            lines.append(
                f"#{t['id']} [{'ON' if t.get('enabled', True) else 'OFF'}] "
                f"[{t['account']}] {t['cron']} -> {t['target']} | {preview}{extra}{delay_txt}{send_as_txt} "
                f"｜备注:{(t.get('remark') or '无')}"
            )
        await e.reply("📋 任务列表：\n" + "\n".join(lines))

    @bot.on(events.NewMessage(pattern=r"^/deltask\s+(\d+)"))
    async def _(e):
        if not admin_ok(e): return
        tid = int(e.pattern_match.group(1))
        before = len(tasks_state["tasks"])
        tasks_state["tasks"] = [x for x in tasks_state["tasks"] if x["id"] != tid]
        save_json(TASKS, tasks_state)
        try:
            scheduler.remove_job(str(tid))
        except Exception:
            pass
        await e.reply("✅ 已删除" if len(tasks_state["tasks"]) < before else "未找到该ID")

    @bot.on(events.NewMessage(pattern=r"^/toggle\s+(\d+)"))
    async def _(e):
        if not admin_ok(e): return
        tid = int(e.pattern_match.group(1))
        t = next((x for x in tasks_state["tasks"] if x["id"] == tid), None)
        if not t:
            await e.reply("未找到该ID"); return
        t["enabled"] = not t.get("enabled", True)
        save_json(TASKS, tasks_state)
        try:
            if t["enabled"]:
                add_job_from_task(t)
            else:
                scheduler.remove_job(str(tid))
        except Exception:
            pass
        await e.reply(f"任务 #{tid} 已切换为 {'ON' if t['enabled'] else 'OFF'}")

    @bot.on(events.NewMessage(pattern=r"^/test\s+(.+)"))
    async def _(e):
        if not admin_ok(e): return
        try:
            body = e.pattern_match.group(1).strip()
            # 目标 | 文本 | 账号别名 | 消息延迟 | 发言ID
            parts = split_command_fields(body)
            if len(parts) < 3:
                await e.reply("格式：`/test 目标 | 文本 | 账号别名 | 消息延迟(-=不设) | 发言ID(-=自账号)`", parse_mode="md"); return
            target, text_field, alias = parts[0], parts[1], parts[2]
            delay_txt = parts[3] if len(parts) >= 4 else ""
            send_as_txt = parts[4] if len(parts) >= 5 else ""
            messages = [m.strip() for m in text_field.split("||") if m.strip()]
            if not messages:
                await e.reply("消息内容不能为空，可用 `||` 分隔多条。", parse_mode="md"); return
            delay_sec = 0
            if delay_txt and delay_txt != FIELD_PLACEHOLDER:
                if delay_txt.isdigit():
                    delay_sec = max(0, int(delay_txt))
                else:
                    await e.reply("消息延迟需为非负整数，或使用 `-` 表示不设置。", parse_mode="md"); return
            send_as_val = None
            if send_as_txt:
                txt = send_as_txt.strip()
                low = txt.lower()
                if txt == FIELD_PLACEHOLDER:
                    send_as_val = None
                elif low in SEND_AS_DISABLE_KEYWORDS:
                    send_as_val = SEND_AS_DISABLE
                elif txt.lstrip("-").isdigit():
                    send_as_val = txt
                else:
                    await e.reply("发言ID 需为数字ID、`-`（本账号）或 `none`（禁用）。", parse_mode="md"); return
            await ensure_send_as_permission(cfg["api_id"], cfg["api_hash"], alias, send_as_val)
            await send_with_user(cfg["api_id"], cfg["api_hash"], alias, target, messages, delay_sec, send_as_val)
            await e.reply(f"✅ 已尝试发送（{alias}）")
        except Exception as ex:
            await e.reply(f"❌ 发送失败：{ex}")

    @bot.on(events.NewMessage(pattern=r"^/nextinterval(?:\s+(.*))?$"))
    async def _(e):
        if not admin_ok(e): return
        arg = (e.pattern_match.group(1) or "").strip()
        interval_tasks = [t for t in tasks_state["tasks"] if is_interval_task(t)]
        if not interval_tasks:
            await e.reply("暂无间隔任务。"); return
        if not arg or arg.lower() == "all":
            lines = []
            for t in interval_tasks:
                info = describe_next_run(t)
                lines.append(f"#{t['id']} [{t['account']}] -> {t['target']}  {info}")
            await e.reply("⏱ 间隔任务计划：\n" + "\n".join(lines)); return
        if not arg.isdigit():
            await e.reply("参数需为任务ID或 all。"); return
        tid = int(arg)
        task = next((x for x in interval_tasks if x["id"] == tid), None)
        if not task:
            await e.reply("未找到该间隔任务ID。"); return
        await e.reply(f"#{tid} 下一次执行：{describe_next_run(task)}")

    @bot.on(events.NewMessage(pattern=r"^/delaynext\s+(.+)"))
    async def _(e):
        if not admin_ok(e): return
        try:
            body = e.pattern_match.group(1).strip()
            parts = [x.strip() for x in body.split("|")]
            if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
                await e.reply("格式：`/delaynext ID | 秒数`"); return
            tid = int(parts[0]); secs = int(parts[1])
            if secs < 0:
                await e.reply("秒数需为非负整数。"); return
            task = next((x for x in tasks_state["tasks"] if x["id"] == tid), None)
            if not task or not is_interval_task(task):
                await e.reply("该任务不存在或不是间隔任务。"); return
            job = scheduler.get_job(str(tid))
            if not job:
                await e.reply("未找到该任务的调度。"); return
            new_time = datetime.now(SH_TZ) + timedelta(seconds=secs)
            job.modify(next_run_time=new_time)
            await e.reply(f"✅ 已将任务 #{tid} 的下一次执行时间调整为 {new_time.strftime('%Y-%m-%d %H:%M:%S')}，约 {format_seconds(secs)} 后执行。")
        except Exception as ex:
            await e.reply(f"❌ 调整失败：{ex}")

    # ---------- 状态/查询 ----------
    @bot.on(events.NewMessage(pattern=r"^/status$"))
    async def _(e):
        if not admin_ok(e): return
        data = ensure_accounts()["users"]
        if not data:
            await e.reply("暂无账号。先用 `/adduser 别名 | 手机号` 登录。", parse_mode="md"); return
        lines = ["🟢 账号状态（上海时区）"]
        for alias in data.keys():
            client = await get_or_start_client(cfg["api_id"], cfg["api_hash"], alias)
            if not await client.is_user_authorized():
                lines.append(f"- {alias}: 未登录")
                continue
            me, online, last = await user_status(client)
            last_txt = last.astimezone(SH_TZ).strftime("%Y-%m-%d %H:%M:%S") if last else "未知"
            lines.append(f"- {alias}: {'在线' if online else '离线'}  最近在线: {last_txt}  {fmt_entity(me)}")
        await e.reply("\n".join(lines))

    @bot.on(events.NewMessage(pattern=r"^/me\s+(.+)"))
    async def _(e):
        if not admin_ok(e): return
        alias = e.pattern_match.group(1).strip()
        try:
            client = await get_or_start_client(cfg["api_id"], cfg["api_hash"], alias)
            if not await client.is_user_authorized():
                await e.reply("该账号未登录。"); return
            me, online, last = await user_status(client)
            await e.reply("👤 账号信息：\n" + fmt_entity(me))
        except Exception as ex:
            await e.reply(f"❌ 查询失败：{ex}")

    @bot.on(events.NewMessage(pattern=r"^/whois\s+(.+)"))
    async def _(e):
        if not admin_ok(e): return
        target = e.pattern_match.group(1).strip()
        # 用第一个已登录账号做解析（或提示）
        usable = [a for a in ensure_accounts()["users"].keys()]
        if not usable:
            await e.reply("请至少登录一个账号后使用 /whois。"); return
        client = await get_or_start_client(cfg["api_id"], cfg["api_hash"], usable[0])
        if not await client.is_user_authorized():
            await e.reply("可用账号未登录。"); return
        try:
            ent = await resolve_entity(client, target)
            await e.reply("🔎 解析成功：\n" + fmt_entity(ent))
        except Exception as ex:
            await e.reply(f"❌ 解析失败：{ex}")

    print("✅ 管理Bot已启动（上海时区）。用管理员账号给Bot发 /help 查看菜单。")
    await send_log_message("✅ 管理Bot已启动。")

    await bot.run_until_disconnected()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n已退出。")

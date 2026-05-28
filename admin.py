import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database as db

logger = logging.getLogger(__name__)
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")
IST = timezone(timedelta(hours=5, minutes=30))


def is_admin(update: Update) -> bool:
    return str(update.effective_user.id) == ADMIN_CHAT_ID


async def _require_admin(update: Update) -> bool:
    if not is_admin(update):
        await update.message.reply_text("⛔ Tere paas yeh command use karne ki permission nahi hai.")
        return False
    return True


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    stats = await asyncio.to_thread(db.get_stats)
    top = await asyncio.to_thread(db.get_top_asins, 3)
    channels = await asyncio.to_thread(db.get_channel_ids)
    now = datetime.now(IST).strftime("%d %b %Y, %I:%M %p")
    top_lines = ""
    for i, (asin, title, cnt) in enumerate(top, 1):
        top_lines += f"\n  {i}. {(title or asin)[:35]} ({cnt} alerts)"
    ch_info = ", ".join(channels) if channels else "None set"
    await update.message.reply_text(
        f"🛠 Admin Dashboard\n{now} IST\n\n"
        f"👥 Users\n"
        f"  Total:   {stats['total_users']:,}\n"
        f"  Active:  {stats['active_users']:,} (30d)\n"
        f"  Month:   {stats['month_users']:,}\n"
        f"  Today:   {stats['today_users']:,}\n\n"
        f"🔗 Clicks\n"
        f"  Total:   {stats['total_clicks']:,}\n"
        f"  Month:   {stats['month_clicks']:,}\n"
        f"  Today:   {stats['today_clicks']:,}\n\n"
        f"🔔 Alerts\n"
        f"  Active:  {stats['total_alerts']:,}\n"
        f"  Tracking: {stats['users_tracking']:,} users\n\n"
        f"📢 Channel Posts Today: {stats['posts_today']:,}\n"
        f"📡 Channels: {ch_info}\n\n"
        f"🏆 Top Tracked:{top_lines if top_lines else chr(10) + '  None yet'}\n\n"
        f"Commands: /users /clicks /alerts /top /recent /broadcast /backup /ping\n"
        f"/setchannel /removechannel /digest /lightning",
        parse_mode=None,
    )


async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    stats = await asyncio.to_thread(db.get_stats)
    await update.message.reply_text(
        f"👥 User Stats\n\n"
        f"Total:   {stats['total_users']:,}\n"
        f"Active:  {stats['active_users']:,} (30d)\n"
        f"Month:   {stats['month_users']:,}\n"
        f"Today:   {stats['today_users']:,}"
    )


async def cmd_clicks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    stats = await asyncio.to_thread(db.get_stats)
    await update.message.reply_text(
        f"🔗 Affiliate Click Stats\n\n"
        f"Total:   {stats['total_clicks']:,}\n"
        f"Month:   {stats['month_clicks']:,}\n"
        f"Today:   {stats['today_clicks']:,}"
    )


async def cmd_alerts_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    stats = await asyncio.to_thread(db.get_stats)
    await update.message.reply_text(
        f"🔔 Price Alert Stats\n\n"
        f"Active alerts:  {stats['total_alerts']:,}\n"
        f"Users tracking: {stats['users_tracking']:,}"
    )


async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    rows = await asyncio.to_thread(db.get_top_asins, 5)
    if not rows:
        await update.message.reply_text("Abhi koi tracked products nahi hain.")
        return
    lines = ["🏆 Top 5 Tracked Products\n"]
    for i, (asin, title, cnt) in enumerate(rows, 1):
        lines.append(f"{i}. {(title or asin)[:50]}\n   ASIN: {asin}  |  {cnt} alerts")
    await update.message.reply_text("\n".join(lines))


async def cmd_recent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    rows = await asyncio.to_thread(db.get_recent_users, 10)
    if not rows:
        await update.message.reply_text("Abhi koi users nahi hain.")
        return
    lines = ["👥 Last 10 Joined Users\n"]
    for r in rows:
        name = f"{r.get('first_name') or ''} {r.get('last_name') or ''}".strip() or "No Name"
        uname = f"@{r['username']}" if r.get("username") else "No username"
        raw_ts = r.get("joined_at")
        if raw_ts:
            if raw_ts.tzinfo is None:
                raw_ts = raw_ts.replace(tzinfo=timezone.utc)
            joined = raw_ts.astimezone(IST).strftime("%d %b, %I:%M %p IST")
        else:
            joined = "?"
        lines.append(f"• {name} ({uname})\n  ID: {r['user_id']}  |  {joined}")
    await update.message.reply_text("\n".join(lines))


async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    now = datetime.now(IST).strftime("%d %b %Y, %I:%M:%S %p")
    await update.message.reply_text(
        f"✅ Bot is LIVE\n\n🕐 Server time: {now} IST\n🤖 @Shopping_GPT_Bot — Running normally"
    )


async def cmd_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    stats = await asyncio.to_thread(db.get_stats)
    top = await asyncio.to_thread(db.get_top_asins, 5)
    now = datetime.now(IST).strftime("%d %b %Y, %I:%M %p")
    lines = [
        f"📊 DB Snapshot — {now} IST\n",
        f"Users:         {stats['total_users']:,}",
        f"Active (30d):  {stats['active_users']:,}",
        f"Alerts active: {stats['total_alerts']:,}",
        f"Total clicks:  {stats['total_clicks']:,}",
        f"Channel posts: {stats['posts_today']:,} today",
        "", "🏆 Top Tracked:",
    ]
    for asin, title, cnt in top:
        lines.append(f"  • {(title or asin)[:40]} ({cnt})")
    await update.message.reply_text("\n".join(lines))


async def cmd_setchannel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    if not context.args:
        await update.message.reply_text(
            "Usage: /setchannel @channelname\n\nBot ko channel ka admin banana mat bhulo!"
        )
        return
    channel = context.args[0]
    await asyncio.to_thread(db.add_channel, channel)
    await update.message.reply_text(
        f"✅ Channel set: {channel}\n\nAb deals aur alerts is channel pe post honge!\n"
        f"Dhyan raho: Bot ko is channel ka admin hona chahiye."
    )


async def cmd_removechannel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    if not context.args:
        channels = await asyncio.to_thread(db.get_channel_ids)
        if not channels:
            await update.message.reply_text("Koi channel set nahi hai.")
            return
        await update.message.reply_text(
            f"Current channels:\n" + "\n".join(channels) +
            "\n\nUsage: /removechannel @channelname"
        )
        return
    channel = context.args[0]
    await asyncio.to_thread(db.remove_channel, channel)
    await update.message.reply_text(f"✅ Channel removed: {channel}")


async def cmd_digest_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    await update.message.reply_text("📤 Morning digest manually trigger ho raha hai...")
    from scheduler import send_morning_digest
    try:
        await send_morning_digest(context.bot)
        await update.message.reply_text("✅ Digest bheja gaya!")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_lightning_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    await update.message.reply_text("⚡ Lightning deals check ho raha hai...")
    from scheduler import check_lightning_deals
    try:
        await check_lightning_deals(context.bot)
        await update.message.reply_text("✅ Lightning check complete!")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    if context.args:
        msg = " ".join(context.args)
        total = await asyncio.to_thread(db.get_user_count_total)
        context.user_data["broadcast_mode"] = "all"
        context.user_data["broadcast_draft"] = msg
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Confirm Send", callback_data="bc_confirm"),
            InlineKeyboardButton("❌ Cancel", callback_data="bc_cancel"),
        ]])
        await update.message.reply_text(
            f"📋 Preview:\n\n{msg}\n\n⚠️ Yeh message SAARE {total:,} users ko jayega!",
            reply_markup=kb,
        )
        return
    context.user_data.pop("broadcast_mode", None)
    context.user_data.pop("broadcast_selected", None)
    context.user_data.pop("broadcast_draft", None)
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📢 All Users", callback_data="bc_all"),
        InlineKeyboardButton("✅ Active (30d)", callback_data="bc_active"),
        InlineKeyboardButton("👥 Select Users", callback_data="bc_select"),
    ]])
    await update.message.reply_text(
        "📢 Broadcast\n\nKinhe message bhejna hai?", reply_markup=kb
    )


async def show_user_selection_page(query_or_msg, context, page: int, edit: bool = False):
    offset = page * 10
    users = await asyncio.to_thread(db.get_users_paginated, offset, 10)
    total = await asyncio.to_thread(db.get_user_count_total)
    total_pages = max(1, (total + 9) // 10)
    selected = context.user_data.get("broadcast_selected", [])
    buttons = []
    for u in users:
        uid = u["user_id"]
        name = u.get("first_name") or "User"
        uname = f"@{u['username']}" if u.get("username") else f"ID:{uid}"
        check = "✅" if uid in selected else "☐"
        buttons.append([InlineKeyboardButton(
            f"{check} {name} ({uname})", callback_data=f"bc_toggle_{uid}",
        )])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"bc_page_{page - 1}"))
    nav.append(InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", callback_data="noop"))
    if (page + 1) * 10 < total:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"bc_page_{page + 1}"))
    buttons.append(nav)
    buttons.append([InlineKeyboardButton(
        f"✅ Done ({len(selected)} selected)", callback_data="bc_done_select",
    )])
    kb = InlineKeyboardMarkup(buttons)
    text = f"👥 Select Users — Page {page + 1}/{total_pages}\nJinko bhejana hai unhe select karo"
    if edit:
        try:
            await query_or_msg.message.edit_text(text, reply_markup=kb)
        except Exception:
            pass
    else:
        await query_or_msg.message.reply_text(text, reply_markup=kb)

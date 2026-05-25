import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database as db

logger        = logging.getLogger(__name__)
ADMIN_CHAT_ID = os.environ["ADMIN_CHAT_ID"]

# A1 — IST timezone constant (no pytz dependency)
IST = timezone(timedelta(hours=5, minutes=30))


def is_admin(update: Update) -> bool:
    return str(update.effective_user.id) == ADMIN_CHAT_ID


# B4 — helper used by all commands; replies with error instead of silently returning
async def _require_admin(update: Update) -> bool:
    if not is_admin(update):
        await update.message.reply_text("⛔ Tere paas yeh command run karne ki permission nahi hai.")
        return False
    return True


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    stats = await asyncio.to_thread(db.get_stats)
    top   = await asyncio.to_thread(db.get_top_asins, 3)
    now   = datetime.now(IST).strftime("%d %b %Y, %I:%M %p")

    top_lines = ""
    for i, (asin, title, cnt) in enumerate(top, 1):
        top_lines += f"\n  {i}. {(title or asin)[:35]} ({cnt} alerts)"

    await update.message.reply_text(
        f"🛠 <b>Admin Dashboard</b>\n"
        f"<i>{now} IST</i>\n\n"
        f"👥 <b>Users</b>\n"
        f"  • Total:      {stats['total_users']:,}\n"
        f"  • This month: {stats['month_users']:,}\n"
        f"  • Today:      {stats['today_users']:,}\n\n"
        f"🔗 <b>Affiliate Clicks</b>\n"
        f"  • Total:      {stats['total_clicks']:,}\n"
        f"  • This month: {stats['month_clicks']:,}\n"
        f"  • Today:      {stats['today_clicks']:,}\n\n"
        f"🔔 <b>Price Alerts</b>\n"
        f"  • Active:     {stats['total_alerts']:,}\n"
        f"  • Tracking:   {stats['users_tracking']:,} users\n\n"
        f"🏆 <b>Top Tracked Products</b>"
        f"{top_lines if top_lines else chr(10) + '  None yet'}\n\n"
        f"<b>Commands:</b> /users /clicks /alerts /top /recent /broadcast /backup /ping",
        parse_mode="HTML",
    )


async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    stats = await asyncio.to_thread(db.get_stats)
    await update.message.reply_text(
        f"👥 <b>User Stats</b>\n\n"
        f"Total:      <b>{stats['total_users']:,}</b>\n"
        f"This month: <b>{stats['month_users']:,}</b>\n"
        f"Today:      <b>{stats['today_users']:,}</b>",
        parse_mode="HTML",
    )


async def cmd_clicks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    stats = await asyncio.to_thread(db.get_stats)
    await update.message.reply_text(
        f"🔗 <b>Affiliate Click Stats</b>\n\n"
        f"Total:      <b>{stats['total_clicks']:,}</b>\n"
        f"This month: <b>{stats['month_clicks']:,}</b>\n"
        f"Today:      <b>{stats['today_clicks']:,}</b>",
        parse_mode="HTML",
    )


async def cmd_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_clicks(update, context)


async def cmd_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    stats = await asyncio.to_thread(db.get_stats)
    await update.message.reply_text(
        f"🔔 <b>Price Alert Stats</b>\n\n"
        f"Active alerts:   <b>{stats['total_alerts']:,}</b>\n"
        f"Users tracking:  <b>{stats['users_tracking']:,}</b>",
        parse_mode="HTML",
    )


async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    rows = await asyncio.to_thread(db.get_top_asins, 5)
    if not rows:
        await update.message.reply_text("Abhi koi tracked products nahi hain.")
        return
    lines = ["🏆 <b>Top 5 Tracked Products</b>\n"]
    for i, (asin, title, cnt) in enumerate(rows, 1):
        short = (title or asin)[:50]
        lines.append(f"{i}. {short}\n   ASIN: <code>{asin}</code>  |  {cnt} alerts")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_recent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    rows = await asyncio.to_thread(db.get_recent_users, 10)
    if not rows:
        await update.message.reply_text("Abhi koi users nahi hain.")
        return
    lines = ["👥 <b>Last 10 Joined Users</b>\n"]
    for r in rows:
        name   = f"{r.get('first_name') or ''} {r.get('last_name') or ''}".strip() or "No Name"
        uname  = f"@{r['username']}" if r.get("username") else "No username"
        joined = r["joined_at"].strftime("%d %b, %I:%M %p") if r.get("joined_at") else "?"
        lines.append(f"• {name} ({uname})\n  ID: <code>{r['user_id']}</code>  |  {joined}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    now = datetime.now(IST).strftime("%d %b %Y, %I:%M:%S %p")
    await update.message.reply_text(
        f"✅ <b>Bot is LIVE</b>\n\n"
        f"🕐 Server time: {now} IST\n"
        f"🤖 @Shopping_GPT_Bot — Running normally",
        parse_mode="HTML",
    )


# A6 — smart broadcast: shows UI to choose all users or select specific users
async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return

    if context.args:
        # Legacy one-liner: /broadcast <message> — ask for confirmation before sending
        msg   = " ".join(context.args)
        total = await asyncio.to_thread(db.get_user_count_total)
        context.user_data["broadcast_mode"]  = "all"
        context.user_data["broadcast_draft"] = msg
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Confirm Send", callback_data="bc_confirm"),
            InlineKeyboardButton("❌ Cancel",       callback_data="bc_cancel"),
        ]])
        await update.message.reply_text(
            f"📋 <b>Preview:</b>\n\n{msg}\n\n"
            f"⚠️ Yeh message <b>SAARE {total:,} users</b> ko jayega!",
            parse_mode="HTML",
            reply_markup=kb,
        )
        return

    # Interactive smart broadcast menu
    context.user_data.pop("broadcast_mode",     None)
    context.user_data.pop("broadcast_selected", None)
    context.user_data.pop("broadcast_draft",    None)
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📢 Send to ALL",   callback_data="bc_all"),
        InlineKeyboardButton("👥 Select Users",  callback_data="bc_select"),
    ]])
    await update.message.reply_text(
        "📢 <b>Broadcast</b>\n\nKinhe message bhejna hai?",
        parse_mode="HTML",
        reply_markup=kb,
    )


# A6 — paginated user selection helper (called from handle_callback in bot.py)
async def _show_user_selection_page(
    query_or_msg,
    context: ContextTypes.DEFAULT_TYPE,
    page: int,
    edit: bool = False,
):
    offset      = page * 10
    users       = await asyncio.to_thread(db.get_users_paginated, offset, 10)
    total       = await asyncio.to_thread(db.get_user_count_total)
    total_pages = max(1, (total + 9) // 10)
    selected    = context.user_data.get("broadcast_selected", [])

    buttons = []
    for u in users:
        uid   = u["user_id"]
        name  = u.get("first_name") or "User"
        uname = f"@{u['username']}" if u.get("username") else f"ID:{uid}"
        check = "✅" if uid in selected else "☐"
        buttons.append([InlineKeyboardButton(
            f"{check} {name} ({uname})",
            callback_data=f"bc_toggle_{uid}",
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

    kb   = InlineKeyboardMarkup(buttons)
    text = (
        f"👥 <b>Select Users</b> — Page {page + 1}/{total_pages}\n"
        f"<i>Jinko message bhejna hai unhe check karo</i>"
    )

    if edit:
        try:
            await query_or_msg.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            pass
    else:
        await query_or_msg.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def cmd_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    stats = await asyncio.to_thread(db.get_stats)
    top   = await asyncio.to_thread(db.get_top_asins, 5)
    now   = datetime.now(IST).strftime("%d %b %Y, %I:%M %p")
    lines = [
        f"📊 <b>DB Snapshot</b>  —  <i>{now} IST</i>\n",
        f"Users:         {stats['total_users']:,}",
        f"Month users:   {stats['month_users']:,}",
        f"Today users:   {stats['today_users']:,}",
        f"Alerts active: {stats['total_alerts']:,}",
        f"Total clicks:  {stats['total_clicks']:,}",
        f"Month clicks:  {stats['month_clicks']:,}",
        "",
        "🏆 <b>Top Tracked:</b>",
    ]
    for asin, title, cnt in top:
        lines.append(f"  • {(title or asin)[:40]} ({cnt})")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

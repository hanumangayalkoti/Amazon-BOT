import asyncio
import logging
import os

from telegram import Update
from telegram.ext import ContextTypes

import database as db

logger = logging.getLogger(__name__)
ADMIN_CHAT_ID = os.environ["ADMIN_CHAT_ID"]


def is_admin(update: Update) -> bool:
    return str(update.effective_user.id) == ADMIN_CHAT_ID


async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    stats = await asyncio.to_thread(db.get_stats)
    await update.message.reply_text(
        f"👥 <b>User Stats</b>\n\n"
        f"Total:      <b>{stats['total_users']:,}</b>\n"
        f"This month: <b>{stats['month_users']:,}</b>\n"
        f"Today:      <b>{stats['today_users']:,}</b>",
        parse_mode="HTML",
    )


async def cmd_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    stats = await asyncio.to_thread(db.get_stats)
    await update.message.reply_text(
        f"🔗 <b>Affiliate Click Stats</b>\n\n"
        f"Total:      <b>{stats['total_clicks']:,}</b>\n"
        f"This month: <b>{stats['month_clicks']:,}</b>\n"
        f"Today:      <b>{stats['today_clicks']:,}</b>",
        parse_mode="HTML",
    )


async def cmd_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    stats = await asyncio.to_thread(db.get_stats)
    await update.message.reply_text(
        f"🔔 <b>Price Alert Stats</b>\n\n"
        f"Active alerts:   <b>{stats['total_alerts']:,}</b>\n"
        f"Users tracking:  <b>{stats['users_tracking']:,}</b>",
        parse_mode="HTML",
    )


async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
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


async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if not context.args:
        await update.message.reply_text(
            "Usage: /broadcast &lt;message&gt;\n"
            "Example: /broadcast Aaj ka best deal: https://amzn.to/xyz",
            parse_mode="HTML",
        )
        return

    message = " ".join(context.args)
    user_ids = await asyncio.to_thread(db.get_all_user_ids)
    sent = 0
    failed = 0

    status_msg = await update.message.reply_text(
        f"📢 Broadcasting to {len(user_ids):,} users..."
    )

    for uid in user_ids:
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=message,
                disable_web_page_preview=False,
            )
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)

    await status_msg.edit_text(
        f"✅ <b>Broadcast complete!</b>\n\n"
        f"Sent:   {sent:,}\n"
        f"Failed: {failed:,}",
        parse_mode="HTML",
    )


async def cmd_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    stats  = await asyncio.to_thread(db.get_stats)
    top    = await asyncio.to_thread(db.get_top_asins, 5)
    lines  = [
        "📊 <b>DB Snapshot</b>\n",
        f"Users:         {stats['total_users']:,}",
        f"Alerts active: {stats['total_alerts']:,}",
        f"Total clicks:  {stats['total_clicks']:,}",
        "",
        "🏆 <b>Top Tracked:</b>",
    ]
    for asin, title, cnt in top:
        lines.append(f"  • {(title or asin)[:40]} ({cnt})")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

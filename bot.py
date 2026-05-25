import asyncio
import logging
import os
from collections import OrderedDict
from datetime import datetime, timezone, timedelta

from telegram import (
    Update,
    BotCommand,
    BotCommandScopeDefault,
    BotCommandScopeChat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    constants,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import admin as adm
import ai_handler as ai
import amazon_api as api
import database as db
from scheduler import start_scheduler

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN     = os.environ["BOT_TOKEN"]
ADMIN_CHAT_ID = os.environ["ADMIN_CHAT_ID"]

# A1 — IST timezone constant (no pytz needed)
IST = timezone(timedelta(hours=5, minutes=30))

# B6 — keep a module-level reference so the scheduler is never garbage collected
_scheduler = None


# ─────────────────────────── Keyboards ────────────────────────────────────────

def product_keyboard(asin: str) -> InlineKeyboardMarkup:
    link = api.build_affiliate_link(asin)
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🛒 Buy Now",       url=link),
            InlineKeyboardButton("🔔 Price Alert",   callback_data=f"alert_{asin}"),
        ],
        [
            InlineKeyboardButton("📋 Features",      callback_data=f"features_{asin}"),
            InlineKeyboardButton("💾 Wishlist",       callback_data=f"wish_{asin}"),
            InlineKeyboardButton("📈 Price History",  callback_data=f"history_{asin}"),
        ],
    ])


def search_result_keyboard(asin: str) -> InlineKeyboardMarkup:
    link = api.build_affiliate_link(asin)
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🛒 Buy",     url=link),
            InlineKeyboardButton("🔔 Alert",   callback_data=f"alert_{asin}"),
            InlineKeyboardButton("💾 Wishlist", callback_data=f"wish_{asin}"),
        ],
        [
            InlineKeyboardButton("📋 Details", callback_data=f"detail_{asin}"),
        ],
    ])


def detail_back_keyboard(asin: str) -> InlineKeyboardMarkup:
    link = api.build_affiliate_link(asin)
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🛒 Buy Now",           url=link),
            InlineKeyboardButton("🔔 Alert",             callback_data=f"alert_{asin}"),
            InlineKeyboardButton("💾 Wishlist",           callback_data=f"wish_{asin}"),
        ],
        [
            InlineKeyboardButton("⬅️ Back to Results",  callback_data=f"back_{asin}"),
        ],
    ])


# ─────────────────────────── Formatting helpers ───────────────────────────────

def star_bar(rating: float) -> str:
    full  = int(rating)
    half  = 1 if (rating - full) >= 0.5 else 0
    empty = 5 - full - half
    return "★" * full + "½" * half + "☆" * empty


def _safe_cap(text: str, limit: int = 1020) -> str:
    return text[:limit - 1] + "…" if len(text) > limit else text


def format_product_card(info: dict) -> str:
    lines = []

    brand = info.get("brand", "")
    cat   = info.get("category", "")
    if brand and cat:
        lines.append(f"🏷 <b>{brand}</b>  ·  📂 {cat}")
    elif brand:
        lines.append(f"🏷 <b>Brand</b> — {brand}")
    elif cat:
        lines.append(f"📂 <b>Category</b> — {cat}")

    if info.get("title"):
        t = info["title"]
        lines.append(f"📦 <b>Product</b> — {t[:100] + '…' if len(t) > 100 else t}")

    lines.append("")

    disc = info.get("discount_pct")
    save = info.get("savings")
    if info.get("price"):
        price_line = f"💰 <b>Price</b> — {info['price']}"
        if disc and save:
            badge = "🔥 " if int(disc) >= 30 else ""
            price_line += f"\n{badge}🔖 <b>Discount</b> — {disc}% off  (save {save})"
        elif disc:
            badge = "🔥 " if int(disc) >= 30 else ""
            price_line += f"\n{badge}🔖 <b>Discount</b> — {disc}% off"
        lines.append(price_line)
    else:
        lines.append("⚠️ <b>Price</b> — Currently Unavailable")

    if info.get("availability") and info.get("price"):
        is_in = any(w in info["availability"].lower() for w in ["in stock", "available"])
        icon  = "✅" if is_in else "⚠️"
        lines.append(f"{icon} <b>Stock</b> — {info['availability']}")

    rating = info.get("rating")
    rc     = info.get("review_count")
    if rating not in (None, "", 0, 0.0):
        try:
            stars    = star_bar(float(rating))
            rev_part = f"  ·  💬 {rc:,}" if rc else ""
            lines.append(f"⭐ {stars}  <b>{rating}/5</b>{rev_part}")
        except (ValueError, TypeError):
            if rc:
                lines.append(f"💬 <b>Reviews</b> — {rc:,}")
    elif rc:
        lines.append(f"💬 <b>Reviews</b> — {rc:,}")

    return "\n".join(lines)


def format_detail_card(info: dict) -> str:
    base     = format_product_card(info)
    features = info.get("features", [])
    if not features:
        return _safe_cap(base)
    feat_lines = ["\n\n📋 <b>Key Features</b>"]
    for f in features[:3]:
        short = f[:80] + "…" if len(f) > 80 else f
        feat_lines.append(f"• {short}")
    full = base + "\n".join(feat_lines)
    return _safe_cap(full)


# ─────────────────────────── B5 — LRU product cache ──────────────────────────

def _cache_put(context: ContextTypes.DEFAULT_TYPE, asin: str, info: dict):
    """Store in per-user product cache, evicting oldest entry when over 30 items."""
    cache = context.user_data.setdefault("product_cache", OrderedDict())
    if asin in cache:
        cache.move_to_end(asin)
    cache[asin] = info
    while len(cache) > 30:
        cache.popitem(last=False)


def _cache_get(context: ContextTypes.DEFAULT_TYPE, asin: str) -> dict | None:
    cache = context.user_data.get("product_cache", {})
    return cache.get(asin)


# ─────────────────────────── A4/A5 — Mode management ─────────────────────────

def _clear_all_active_modes(context: ContextTypes.DEFAULT_TYPE):
    """Clear every active mode so the user starts fresh."""
    context.user_data.pop("compare_step",       None)
    context.user_data.pop("compare_asin1",      None)
    context.user_data.pop("compare_info1",      None)
    context.user_data.pop("compare_started_at", None)
    context.user_data.pop("waiting_for_search", None)
    context.user_data.pop("waiting_for_track",  None)
    context.user_data.pop("simi_active",        None)
    context.user_data.pop("simi_history",       None)


def _get_active_mode_name(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    if context.user_data.get("compare_step"):
        return "⚖️ Compare"
    if context.user_data.get("waiting_for_search"):
        return "🔍 Search"
    if context.user_data.get("waiting_for_track"):
        return "🔔 Track"
    if context.user_data.get("simi_active"):
        return "🤖 Simi"
    return None


# ─────────────────────────── A2 — Alerts pagination ──────────────────────────

async def _show_alerts_page(
    msg_or_query,
    context: ContextTypes.DEFAULT_TYPE,
    alerts: list,
    page: int,
    edit: bool = False,
):
    per_page    = 10
    total_pages = max(1, (len(alerts) + per_page - 1) // per_page)
    page_alerts = alerts[page * per_page : (page + 1) * per_page]

    lines = [f"🔔 <b>Active Alerts ({len(alerts)}) — Page {page + 1}/{total_pages}</b>\n"]
    buttons = []

    for alert in page_alerts:
        title   = (alert["product_title"] or alert["asin"])[:55]
        t_price = alert["tracked_price"]
        c_price = alert["current_price"]
        drop    = " ✅" if c_price < t_price else ""
        lines.append(
            f"📦 <b>{title}</b>\n"
            f"   Tracked: ₹{t_price:,.0f}  |  Now: ₹{c_price:,.0f}{drop}"
        )
        buttons.append([InlineKeyboardButton(
            f"❌ Remove — {title[:30]}",
            callback_data=f"remove_alert_{alert['id']}",
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"alerts_page_{page - 1}"))
    if total_pages > 1:
        nav.append(InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", callback_data="noop"))
    if (page + 1) * per_page < len(alerts):
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"alerts_page_{page + 1}"))
    if nav:
        buttons.append(nav)

    kb   = InlineKeyboardMarkup(buttons)
    text = "\n\n".join(lines)

    if edit:
        try:
            await msg_or_query.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            pass
    else:
        await msg_or_query.reply_text(text, parse_mode="HTML", reply_markup=kb)


# ─────────────────────────── Card sender ─────────────────────────────────────

async def _send_product_card(update_or_msg, context, info: dict, keyboard=None):
    caption = _safe_cap(format_product_card(info))
    asin    = info.get("asin", "")
    kb      = keyboard or product_keyboard(asin)
    image   = info.get("image_url", "")

    _cache_put(context, asin, info)  # B5 — LRU cache

    try:
        if image:
            await update_or_msg.reply_photo(
                photo=image, caption=caption,
                parse_mode="HTML", reply_markup=kb,
            )
        else:
            await update_or_msg.reply_text(caption, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await update_or_msg.reply_text(caption, parse_mode="HTML", reply_markup=kb)


# ─────────────────────────── Admin notification ───────────────────────────────

async def _notify_admin(context, user, total_users: int, is_new: bool = True):
    try:
        name     = f"{user.first_name or ''} {user.last_name or ''}".strip() or "No Name"
        uname    = f"@{user.username}" if user.username else "No username"
        now      = datetime.now(IST)                       # A1 — IST time
        now_date = now.strftime("%d %b %Y")
        now_time = now.strftime("%I:%M %p")
        header   = "🆕 <b>New User!</b>" if is_new else "🔄 <b>Returning User</b>"
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=(
                f"{header}\n\n"
                f"Hi ADMIN — {uname} ne @Shopping_GPT_Bot ko start kiya he\n\n"
                f"👤 Name: {name}\n"
                f"🆔 User ID: <code>{user.id}</code>\n"
                f"📅 Date: {now_date}\n"
                f"🕐 Time: {now_time} IST\n\n"
                f"👥 Total Users: <b>{total_users:,}</b>"
            ),
            parse_mode="HTML",
        )
    except Exception:
        pass


# ─────────────────────────── Commands ────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user   = update.effective_user
    is_new = await asyncio.to_thread(db.upsert_user, user.id, user.username,
                                     user.first_name, user.last_name)
    total  = await asyncio.to_thread(db.get_user_count)
    if is_new:
        await _notify_admin(context, user, total, is_new=True)

    await update.message.reply_text(
        f"Namaste <b>{user.first_name}!</b> 🛍️\n\n"
        "Main hoon <b>Shopping GPT</b> — tera personal Amazon India assistant!\n\n"
        "✨ Yeh sab kar sakta hoon:\n"
        "• Amazon link ya ASIN bhejo → product card\n"
        "• <b>'best headphones under 2000'</b> type karo → 5 results\n"
        "• 🔔 Price alert set karo — price gire toh notify\n"
        "• ⚖️ /compare — 2 products compare karo\n"
        "• 💾 Wishlist mein save karo\n"
        "• 🤖 /simi — Simi se shopping advice lo\n\n"
        "Seedha link bhejo ya kuch bolo — shuru karte hain! 👇",
        parse_mode="HTML",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 <b>Shopping GPT — Guide</b>\n\n"
        "<b>🔗 Product Info:</b>\n"
        "  Amazon link, amzn.to link, ya ASIN bhejo\n\n"
        "<b>🔍 Search:</b>\n"
        "  'best earbuds under 2000' — directly type karo\n"
        "  /search — bot prompt karega\n\n"
        "<b>⚖️ Compare:</b>\n"
        "  /compare — 2 products side-by-side\n\n"
        "<b>🔔 Alerts:</b>\n"
        "  Product card mein 🔔 button dabao\n"
        "  'headphones under 999 pe alert' — Simi set kar degi\n"
        "  /myalerts — sab alerts dekho\n\n"
        "<b>💾 Wishlist:</b>\n"
        "  Product card mein 💾 button dabao\n"
        "  /mywishlist — wishlist dekho\n\n"
        "<b>🤖 Simi:</b>\n"
        "  /simi — shopping assistant activate\n\n"
        "<b>Affiliate tag:</b> <code>dealskoti-21</code> (auto-embed)",
        parse_mode="HTML",
    )


async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _clear_all_active_modes(context)
    context.user_data["waiting_for_search"] = True
    await update.message.reply_text(
        "🔍 Kya search karna hai? Seedha type karo:\n"
        "<i>Example: best gaming mouse under 2000</i>",
        parse_mode="HTML",
    )


async def cmd_compare(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _clear_all_active_modes(context)
    context.user_data["compare_step"]       = 1
    context.user_data["compare_started_at"] = datetime.now(IST).timestamp()  # B9
    await update.message.reply_text(
        "⚖️ Chaliye 2 products compare karte hain! 🔍\n\n"
        "Pehle product ka Amazon link ya ASIN bhejo 👇"
    )


async def cmd_track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _clear_all_active_modes(context)
    context.user_data["waiting_for_track"] = True
    await update.message.reply_text(
        "🔔 Kis product pe alert lagana hai?\n"
        "Amazon link ya ASIN bhejo 👇\n\n"
        "<i>Ya seedha bolo: 'headphones under 999 pe alert'</i>",
        parse_mode="HTML",
    )


# A2 — paginated alerts list
async def cmd_myalerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    alerts  = await asyncio.to_thread(db.get_user_alerts, user_id)
    if not alerts:
        await update.message.reply_text(
            "🔔 Abhi koi price alert set nahi hai.\n\n"
            "Kisi product card mein 🔔 button dabao alert lagane ke liye!"
        )
        return
    context.user_data["my_alerts_cache"] = alerts
    await _show_alerts_page(update.message, context, alerts, page=0)


async def cmd_mywishlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    items   = await asyncio.to_thread(db.get_wishlist, user_id)
    if not items:
        await update.message.reply_text(
            "💾 Teri wishlist abhi khali hai.\n\n"
            "Kisi product card mein 💾 button dabao save karne ke liye!"
        )
        return

    await update.message.reply_text(
        f"💾 <b>Teri Wishlist ({len(items)} products):</b>",
        parse_mode="HTML",
    )
    for item in items:
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🛒 Buy",          url=item["affiliate_link"]),
                InlineKeyboardButton("🔔 Alert lagao",  callback_data=f"alert_{item['asin']}"),
            ],
            [InlineKeyboardButton("❌ Remove", callback_data=f"remove_wish_{item['id']}")],
        ])
        await update.message.reply_text(
            f"📦 <b>{item['product_title'][:60]}</b>\n"
            f"💰 {item['price'] or 'Price N/A'}",
            parse_mode="HTML",
            reply_markup=kb,
        )
        await asyncio.sleep(0.1)


async def cmd_simi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _clear_all_active_modes(context)
    context.user_data["simi_active"] = True
    first = update.effective_user.first_name or "dost"
    await update.message.reply_text(
        f"🟢 <b>Simi Mode ON</b>\n\n"
        f"Hi <b>{first}!</b> Main hoon Simi — teri shopping assistant! 😊\n\n"
        "Koi bhi shopping sawaal pooch — products, deals, comparisons, buying advice!\n\n"
        "<i>Amazon link bhejoge toh product card bhi dikhega!\n"
        "Simi se bahar jaane ke liye /stop type karo.</i>",
        parse_mode="HTML",
    )


# Section C — /stop clears ALL active modes
async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _clear_all_active_modes(context)
    await update.message.reply_text(
        "🔴 <b>Mode OFF</b>\n\n"
        "Normal mode mein wapas aa gaye!\n"
        "Seedha Amazon link ya query type karo — main dhundh lunga 🛍️",
        parse_mode="HTML",
    )


# ─────────────────────────── Search helper ───────────────────────────────────

async def _do_search(message, context, query: str, with_alert_note: bool = False):
    wait = await message.reply_text(f"🔍 <b>'{query}'</b> search ho rahi hai...", parse_mode="HTML")
    try:
        results = await asyncio.to_thread(api.search_items, query, 5)
    except Exception as e:
        logger.error("Search error query='%s': %s", query, e)
        await wait.edit_text("❌ Search mein kuch dikkat aayi. Thodi der baad try karo 🙏")
        return

    if not results:
        await wait.edit_text("😕 Koi result nahi mila — thoda alag wording try karo!")
        return

    header = f"🔍 <b>'{query}'</b> ke results:\n"
    if with_alert_note:
        header += "Jis pe alert lagana ho, uska 🔔 <b>Alert</b> button dabao! 👆\n"
    await wait.edit_text(header, parse_mode="HTML")

    for i, info in enumerate(results, 1):
        asin   = info.get("asin", "")
        title  = info.get("title", "Product")
        price  = info.get("price", "")
        rating = info.get("rating", "")
        rc     = info.get("review_count")
        disc   = info.get("discount_pct", "")
        brand  = info.get("brand", "")

        t_short    = title[:75] + "…" if len(title) > 75 else title
        card_lines = [f"{i}️⃣ <b>{t_short}</b>"]
        if brand:
            card_lines.append(f"🏷 {brand}")

        price_part = f"💰 {price}" if price else "💰 N/A"
        if disc:
            badge = "🔥 " if int(disc) >= 30 else ""
            price_part += f"  ·  {badge}🔖{disc}% off"
        card_lines.append(price_part)

        if rating:
            try:
                stars    = star_bar(float(rating))
                rev_part = f"  ·  💬 {rc:,}" if rc else ""
                card_lines.append(f"⭐ {stars} {rating}/5{rev_part}")
            except (ValueError, TypeError):
                pass

        _cache_put(context, asin, info)  # B5

        await message.reply_text(
            "\n".join(card_lines),
            parse_mode="HTML",
            reply_markup=search_result_keyboard(asin),
        )
        await asyncio.sleep(0.2)


# ─────────────────────────── Compare helper ──────────────────────────────────

async def _do_compare(message, context, info1: dict, info2: dict):
    def v(d, k, default="N/A"):
        return d.get(k) or default

    asin1 = info1["asin"]
    asin2 = info2["asin"]
    t1    = v(info1, "title", "Product 1")[:30]
    t2    = v(info2, "title", "Product 2")[:30]

    try:
        pick = await asyncio.to_thread(
            ai.simi_reply,
            message.chat.first_name or "dost",
            [],
            f"Compare these two: Product 1: {info1.get('title')} at {info1.get('price')}. "
            f"Product 2: {info2.get('title')} at {info2.get('price')}. "
            f"Give a 1-line Simi's Pick recommendation in Hinglish.",
        )
    except Exception:
        pick = "Apni zaroorat ke hisaab se choose karo!"

    card = (
        f"⚖️ <b>Comparison Result</b>\n\n"
        f"<pre>"
        f"{'':22} {t1[:20]:20} {t2[:20]}\n"
        f"{'💰 Price':22} {v(info1,'price'):20} {v(info2,'price')}\n"
        f"{'🔖 Discount':22} {str(v(info1,'discount_pct','—'))+'%':20} {str(v(info2,'discount_pct','—'))+'%'}\n"
        f"{'⭐ Rating':22} {str(v(info1,'rating','—'))+'/5':20} {str(v(info2,'rating','—'))+'/5'}\n"
        f"{'📦 Stock':22} {v(info1,'availability','N/A')[:18]:20} {v(info2,'availability','N/A')[:18]}"
        f"</pre>\n\n"
        f"🤖 <b>Simi's Pick:</b> {pick}"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🛒 Buy 1", url=api.build_affiliate_link(asin1)),
        InlineKeyboardButton("🛒 Buy 2", url=api.build_affiliate_link(asin2)),
    ]])
    await message.reply_text(card, parse_mode="HTML", reply_markup=kb)


# ─────────────────────────── Message handler ─────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user = update.effective_user

    await asyncio.to_thread(db.upsert_user, user.id, user.username,
                             user.first_name, user.last_name)

    # A6 — admin typing broadcast message text
    if context.user_data.get("awaiting_broadcast_msg") and str(user.id) == ADMIN_CHAT_ID:
        context.user_data.pop("awaiting_broadcast_msg", None)
        context.user_data["broadcast_draft"] = text
        mode     = context.user_data.get("broadcast_mode")
        selected = context.user_data.get("broadcast_selected", [])

        if mode == "all":
            total   = await asyncio.to_thread(db.get_user_count_total)
            warning = f"⚠️ Yeh message <b>SAARE {total:,} users</b> ko jayega!"
        else:
            warning = f"⚠️ Yeh message <b>{len(selected)} selected users</b> ko jayega."

        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Confirm Send", callback_data="bc_confirm"),
            InlineKeyboardButton("❌ Cancel",       callback_data="bc_cancel"),
        ]])
        await update.message.reply_text(
            f"📋 <b>Preview:</b>\n\n{text}\n\n{warning}",
            parse_mode="HTML",
            reply_markup=kb,
        )
        return

    # A4/A5 — detect if user wants to switch away from current active mode
    active_mode = _get_active_mode_name(context)
    if active_mode:
        # Amazon link → always show product card regardless of mode
        _early_asin_check, _ = api.extract_asin(text)
        if not _early_asin_check:
            intent_check = await asyncio.to_thread(ai.detect_intent, text)
            # Switch mode if user is clearly trying to search/track/use a different flow
            switch_triggers = ("search_query", "alert_request")
            simi_modes      = ("🤖 Simi",)
            should_switch   = (
                intent_check in switch_triggers
                and active_mode not in ("🔍 Search", "🔔 Track", "🤖 Simi")
            ) or (
                # In compare/track mode, search queries or alert requests mean user moved on
                active_mode in ("⚖️ Compare", "🔔 Track", "🔍 Search")
                and intent_check in ("simi", "off_topic", "search_query", "alert_request")
                and not _early_asin_check
                # Only switch if it looks like a definitive new intent, not a typo/error
                and len(text) > 8
            )
            if should_switch:
                _clear_all_active_modes(context)
                await update.message.reply_text(
                    f"✅ <b>{active_mode} mode band ho gaya!</b>\n"
                    "Processing tera naya request... 👇",
                    parse_mode="HTML",
                )
                # fall through to process new intent below

    # Re-read mode flags (may have been cleared above)
    compare_step   = context.user_data.get("compare_step")
    waiting_search = context.user_data.get("waiting_for_search")
    waiting_track  = context.user_data.get("waiting_for_track")
    simi_active    = context.user_data.get("simi_active")

    # B9 — compare timeout (10 minutes)
    if compare_step in (1, 2):
        started_at = context.user_data.get("compare_started_at", 0)
        if datetime.now(IST).timestamp() - started_at > 600:
            _clear_all_active_modes(context)
            await update.message.reply_text(
                "⏰ Compare session expire ho gaya (10 min).\n"
                "Dobara /compare karo!"
            )
            return

    if compare_step == 1:
        asin, error = api.extract_asin(text)
        if error == "search":
            await update.message.reply_text(
                "⚠️ Yeh search page hai — pehle kisi ek product pe click karo, "
                "phir us product page ka link bhejo 😊"
            )
            return
        if not asin:
            await update.message.reply_text(
                "❌ Valid Amazon link ya ASIN nahi mila.\n"
                "Example: https://www.amazon.in/dp/B0XXXXXX ya B0XXXXXX\n\n"
                "<i>/stop se compare cancel karo</i>",
                parse_mode="HTML",
            )
            return
        wait = await update.message.reply_text("⏳ Pehla product fetch ho raha hai...")
        try:
            info1 = await asyncio.to_thread(api.get_product_info, asin)
        except Exception:
            await wait.edit_text("❌ Product nahi mila — ek baar link check karo.")
            return
        await wait.delete()
        context.user_data["compare_step"]       = 2
        context.user_data["compare_started_at"] = datetime.now(IST).timestamp()  # B9 reset
        context.user_data["compare_asin1"]      = asin
        context.user_data["compare_info1"]      = info1
        t = info1.get("title", asin)[:60]
        await update.message.reply_text(
            f"✅ <b>{t}...</b> mil gaya!\n\nAb doosre product ka link bhejo 👇",
            parse_mode="HTML",
        )
        return

    if compare_step == 2:
        asin, error = api.extract_asin(text)
        if error == "search":
            await update.message.reply_text("⚠️ Yeh search page hai — specific product link bhejo 😊")
            return
        if not asin:
            await update.message.reply_text(
                "❌ Valid Amazon link ya ASIN nahi mila.\n\n"
                "<i>/stop se compare cancel karo</i>",
                parse_mode="HTML",
            )
            return
        wait = await update.message.reply_text("⏳ Doosra product fetch ho raha hai...")
        try:
            info2 = await asyncio.to_thread(api.get_product_info, asin)
        except Exception:
            await wait.edit_text("❌ Doosra product nahi mila.")
            return
        await wait.delete()
        info1 = context.user_data.pop("compare_info1", {})
        context.user_data.pop("compare_step",       None)
        context.user_data.pop("compare_asin1",      None)
        context.user_data.pop("compare_started_at", None)
        await _do_compare(update.message, context, info1, info2)
        return

    # ── UNIVERSAL RULE: Amazon link in ANY mode → always show product card ──
    _early_asin, _early_error = api.extract_asin(text)
    if _early_asin:
        context.user_data.pop("waiting_for_search", None)
        context.user_data.pop("waiting_for_track",  None)
        wait = await update.message.reply_text("⏳ Product info fetch ho rahi hai...")
        try:
            info = await asyncio.to_thread(api.get_product_info, _early_asin)
        except Exception:
            await wait.edit_text("❌ Product nahi mila — Amazon link check karo.")
            return
        await wait.delete()
        await _send_product_card(update.message, context, info)
        return
    if _early_error == "search":
        await update.message.reply_text(
            "⚠️ Yeh Amazon search page ka link hai — pehle kisi ek product pe click karo, "
            "phir us product ka link bhejo 😊"
        )
        return
    # ───────────────────────────────────────────────────────────────────────

    if waiting_search:
        context.user_data["waiting_for_search"] = False
        await _do_search(update.message, context, text)
        return

    if waiting_track:
        context.user_data["waiting_for_track"] = False
        asin, error = api.extract_asin(text)
        if asin:
            wait = await update.message.reply_text("⏳ Product info fetch ho rahi hai...")
            try:
                info = await asyncio.to_thread(api.get_product_info, asin)
            except Exception:
                await wait.edit_text("❌ Product nahi mila. Dobara try karo!")
                return
            await wait.delete()
            price_amt = info.get("price_amount", 0)
            title     = info.get("title", asin)
            await asyncio.to_thread(
                db.add_price_alert, user.id, asin, title, price_amt,
                api.build_affiliate_link(asin)
            )
            await update.message.reply_text(
                f"🔔 <b>Alert set!</b>\n\n"
                f"📦 {title[:60]}\n"
                f"💰 Current: {info.get('price', 'N/A')}\n\n"
                f"Jab bhi price giregi, main tujhe seedha notify karunga! 📲",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text("❌ Valid Amazon link nahi mila. /track dobara try karo.")
        return

    detected_intent = None

    if simi_active:
        detected_intent = await asyncio.to_thread(ai.detect_intent, text)
        if detected_intent == "product_link":
            pass  # falls through to normal product card flow below
        elif detected_intent == "alert_request":
            query = await asyncio.to_thread(ai.extract_search_query_from_alert, text)
            await _do_search(update.message, context, query, with_alert_note=True)
            return
        elif detected_intent == "search_query":
            await _do_search(update.message, context, text)
            return
        else:
            if "simi_history" not in context.user_data:
                context.user_data["simi_history"] = []
            history = context.user_data["simi_history"]
            await update.message.chat.send_action(constants.ChatAction.TYPING)
            reply   = await asyncio.to_thread(ai.simi_reply, user.first_name or "dost", history, text)
            history.append({"role": "user",      "content": text})
            history.append({"role": "assistant", "content": reply})
            context.user_data["simi_history"] = history[-20:]
            footer = "\n\n— Simi 🤖  |  /stop se bahar jao"
            await update.message.reply_text(reply + footer)
            return

    intent = detected_intent or await asyncio.to_thread(ai.detect_intent, text)

    if intent == "product_link":
        asin, error = api.extract_asin(text)
        if error == "search":
            await update.message.reply_text(
                "⚠️ Yeh search page ka link hai — pehle kisi ek product pe click karo, "
                "phir us page ka link bhejo 😊"
            )
            return
        if not asin:
            await update.message.reply_text(
                "❌ Valid Amazon India link ya ASIN nahi mila.\n\n"
                "✅ Try karo:\n"
                "• https://www.amazon.in/dp/B0XXXXXXXX\n"
                "• https://amzn.to/xxxxx\n"
                "• B0XXXXXXXX (sirf ASIN)"
            )
            return
        wait = await update.message.reply_text("⏳ Product info fetch ho rahi hai...")
        try:
            info = await asyncio.to_thread(api.get_product_info, asin)
        except ValueError:
            await wait.edit_text(
                "😕 Yeh product nahi mila — ho sakta hai link expire ho gaya ho.\n"
                "Koi aur product try karo!"
            )
            return
        except RuntimeError:
            await wait.edit_text(
                "😅 Amazon server thoda busy hai — 1-2 minute mein dobara try karo 🙏"
            )
            return
        await wait.delete()
        await _send_product_card(update.message, context, info)
        await asyncio.to_thread(db.log_click, user.id, asin)

    elif intent == "alert_request":
        query = await asyncio.to_thread(ai.extract_search_query_from_alert, text)
        await _do_search(update.message, context, query, with_alert_note=True)

    elif intent == "search_query":
        await _do_search(update.message, context, text)

    elif intent in ("simi", "off_topic"):
        context.user_data["simi_active"] = True
        if "simi_history" not in context.user_data:
            context.user_data["simi_history"] = []
        history = context.user_data["simi_history"]
        await update.message.chat.send_action(constants.ChatAction.TYPING)
        reply   = await asyncio.to_thread(ai.simi_reply, user.first_name or "dost", history, text)
        history.append({"role": "user",      "content": text})
        history.append({"role": "assistant", "content": reply})
        context.user_data["simi_history"] = history[-20:]
        await update.message.reply_text(reply)

    else:
        await _do_search(update.message, context, text)


# ─────────────────────────── Callback handler ────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data  = query.data
    user  = query.from_user

    await query.answer()

    # ── noop — pagination indicator buttons ──
    if data == "noop":
        return

    # ── A2 — alerts page navigation ──
    if data.startswith("alerts_page_"):
        page   = int(data[12:])
        alerts = context.user_data.get("my_alerts_cache", [])
        if not alerts:
            alerts = await asyncio.to_thread(db.get_user_alerts, user.id)
            context.user_data["my_alerts_cache"] = alerts
        await _show_alerts_page(query, context, alerts, page=page, edit=True)
        return

    # ── A6 — broadcast callbacks ──
    if data == "bc_all":
        total = await asyncio.to_thread(db.get_user_count_total)
        context.user_data["broadcast_mode"]          = "all"
        context.user_data["awaiting_broadcast_msg"]  = True
        await query.message.reply_text(
            f"📢 <b>Send to ALL {total:,} users</b>\n\nAb apna message type karo 👇",
            parse_mode="HTML",
        )
        return

    if data == "bc_select":
        context.user_data["broadcast_mode"]     = "select"
        context.user_data["broadcast_selected"] = []
        context.user_data["broadcast_page"]     = 0
        await adm._show_user_selection_page(query, context, page=0)
        return

    if data.startswith("bc_page_"):
        page = int(data[8:])
        context.user_data["broadcast_page"] = page
        await adm._show_user_selection_page(query, context, page=page, edit=True)
        return

    if data.startswith("bc_toggle_"):
        uid      = int(data[10:])
        selected = context.user_data.setdefault("broadcast_selected", [])
        if uid in selected:
            selected.remove(uid)
        else:
            selected.append(uid)
        page = context.user_data.get("broadcast_page", 0)
        await adm._show_user_selection_page(query, context, page=page, edit=True)
        return

    if data == "bc_done_select":
        selected = context.user_data.get("broadcast_selected", [])
        if not selected:
            await query.answer("Koi user select nahi kiya!", show_alert=True)
            return
        context.user_data["awaiting_broadcast_msg"] = True
        await query.message.reply_text(
            f"✅ <b>{len(selected)} users selected.</b>\n\nAb apna message type karo 👇",
            parse_mode="HTML",
        )
        return

    if data == "bc_confirm":
        msg  = context.user_data.pop("broadcast_draft", None)
        mode = context.user_data.get("broadcast_mode")
        if not msg:
            await query.answer("Message nahi mila!", show_alert=True)
            return
        if mode == "all":
            user_ids = await asyncio.to_thread(db.get_all_user_ids)
        else:
            user_ids = context.user_data.get("broadcast_selected", [])

        sent, failed = 0, 0
        status_msg   = await query.message.reply_text(f"📤 Sending to {len(user_ids):,} users...")

        # B10 — rate limiting: ~20 msg/sec with a 1 sec pause every 25 messages
        for i, uid in enumerate(user_ids):
            try:
                await context.bot.send_message(chat_id=uid, text=msg)
                sent += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.05)
            if (i + 1) % 25 == 0:
                await asyncio.sleep(1)
                try:
                    await status_msg.edit_text(
                        f"📤 Progress: {sent + failed}/{len(user_ids)} "
                        f"({sent} sent, {failed} failed)..."
                    )
                except Exception:
                    pass

        context.user_data.pop("broadcast_mode",     None)
        context.user_data.pop("broadcast_selected", None)
        await status_msg.edit_text(
            f"✅ <b>Broadcast complete!</b>\n\nSent: {sent:,}\nFailed: {failed:,}",
            parse_mode="HTML",
        )
        return

    if data == "bc_cancel":
        context.user_data.pop("broadcast_mode",         None)
        context.user_data.pop("broadcast_selected",     None)
        context.user_data.pop("broadcast_draft",        None)
        context.user_data.pop("awaiting_broadcast_msg", None)
        await query.message.reply_text("❌ Broadcast cancel ho gaya.")
        return

    # ── Product action callbacks ──
    if data.startswith("alert_"):
        asin = data[6:]
        info = _cache_get(context, asin)
        if not info:
            try:
                info = await asyncio.to_thread(api.get_product_info, asin)
            except Exception:
                await query.message.reply_text("❌ Product info nahi mili. Thodi der baad try karo.")
                return
        price_amt = info.get("price_amount", 0)
        title     = info.get("title", asin)
        await asyncio.to_thread(
            db.add_price_alert, user.id, asin, title, price_amt,
            api.build_affiliate_link(asin)
        )
        price_str = info.get("price", f"₹{price_amt:,.0f}")
        await query.message.reply_text(
            f"🔔 <b>Alert set!</b>\n\n"
            f"📦 {title[:60]}\n"
            f"💰 Current: {price_str}\n\n"
            f"Price giregi toh seedha notify karunga! 📲",
            parse_mode="HTML",
        )

    elif data.startswith("detail_"):
        asin = data[7:]
        try:
            info = await asyncio.to_thread(api.get_product_info, asin)
            _cache_put(context, asin, info)  # B5
        except Exception as e:
            logger.error("Detail error asin=%s: %s", asin, e)
            await query.answer("❌ Product info load nahi hui. Retry karo.", show_alert=True)
            return
        caption = format_detail_card(info)
        kb      = detail_back_keyboard(asin)
        image   = info.get("image_url", "")
        try:
            if query.message.photo:
                await query.edit_message_caption(caption, parse_mode="HTML", reply_markup=kb)
            elif image:
                try:
                    await query.message.delete()
                except Exception:
                    pass
                await query.message.chat.send_photo(
                    photo=image, caption=caption,
                    parse_mode="HTML", reply_markup=kb,
                )
            else:
                await query.edit_message_text(caption, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            err = str(e)
            if "not modified" not in err.lower():
                logger.error("Detail edit error: %s", err)
                await query.answer("❌ Edit nahi ho saka.", show_alert=True)

    elif data.startswith("back_"):
        asin = data[5:]
        info = _cache_get(context, asin)
        if not info:
            try:
                info = await asyncio.to_thread(api.get_product_info, asin)
            except Exception:
                await query.answer("❌ Product info nahi mili.", show_alert=True)
                return
        caption = _safe_cap(format_product_card(info))
        kb      = search_result_keyboard(asin)
        try:
            if query.message.photo:
                await query.edit_message_caption(caption, parse_mode="HTML", reply_markup=kb)
            else:
                await query.edit_message_text(caption, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            err = str(e)
            if "not modified" not in err.lower():
                logger.error("Back edit error: %s", err)
                await query.answer("❌ Wapas nahi ja saka.", show_alert=True)

    elif data.startswith("features_"):
        asin = data[9:]
        info = _cache_get(context, asin)
        if not info or not info.get("features"):
            try:
                info = await asyncio.to_thread(api.get_product_info, asin)
                _cache_put(context, asin, info)  # B5
            except Exception:
                await query.message.reply_text("❌ Features load nahi hui. Thodi der baad try karo.")
                return
        features = info.get("features", [])
        if not features:
            await query.message.reply_text("😕 Is product ki features available nahi hain.")
            return
        lines = [f"📋 <b>Key Features — {info.get('title','')[:50]}</b>\n"]
        for f in features:
            lines.append(f"• {f[:200]}")
        await query.message.reply_text("\n".join(lines), parse_mode="HTML")

    elif data.startswith("wish_"):
        asin = data[5:]
        info = _cache_get(context, asin)
        if not info:
            try:
                info = await asyncio.to_thread(api.get_product_info, asin)
            except Exception:
                await query.message.reply_text("❌ Product info nahi mili.")
                return
        added = await asyncio.to_thread(
            db.add_to_wishlist,
            user.id, asin,
            info.get("title", ""),
            info.get("price", ""),
            info.get("image_url", ""),
            api.build_affiliate_link(asin),
        )
        if added:
            await query.message.reply_text(
                f"💾 <b>Wishlist mein add ho gaya!</b>\n"
                f"📦 {info.get('title','')[:60]}\n\n"
                f"/mywishlist se dekh sakte ho.",
                parse_mode="HTML",
            )
        else:
            await query.message.reply_text("ℹ️ Yeh product pehle se wishlist mein hai!")

    elif data.startswith("history_"):
        asin = data[8:]
        rows = await asyncio.to_thread(db.get_price_history, asin, 7)

        # A3 — show product name instead of raw ASIN
        info  = _cache_get(context, asin)
        title = info.get("title", asin)[:60] if info else asin

        if not rows:
            await query.message.reply_text(
                f"📈 Abhi tak <b>{title}</b> ki price history nahi hai.\n"
                "Alert lagao — phir har 6 ghante price track hogi!",
                parse_mode="HTML",
            )
            return
        lines = [f"📈 <b>Price History — {title}</b>\n"]
        for row in rows:
            # A1 — show time in IST
            dt_ist = row["checked_at"].astimezone(IST)
            date   = dt_ist.strftime("%d %b, %I:%M %p")
            lines.append(f"• {date} IST — ₹{row['price']:,.0f}")
        await query.message.reply_text("\n".join(lines), parse_mode="HTML")

    elif data.startswith("remove_alert_"):
        alert_id = int(data[13:])
        await asyncio.to_thread(db.remove_alert, alert_id, user.id)
        # Invalidate the cached alerts list so pagination refreshes
        context.user_data.pop("my_alerts_cache", None)
        await query.message.reply_text("✅ Alert remove ho gaya!")
        try:
            await query.message.delete()
        except Exception:
            pass

    elif data.startswith("remove_wish_"):
        item_id = int(data[12:])
        await asyncio.to_thread(db.remove_from_wishlist, item_id, user.id)
        await query.message.reply_text("✅ Wishlist se remove ho gaya!")
        try:
            await query.message.delete()
        except Exception:
            pass


# ─────────────────────────── main ────────────────────────────────────────────

def main():
    db.init_db()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("help",       cmd_help))
    app.add_handler(CommandHandler("search",     cmd_search))
    app.add_handler(CommandHandler("compare",    cmd_compare))
    app.add_handler(CommandHandler("track",      cmd_track))
    app.add_handler(CommandHandler("myalerts",   cmd_myalerts))
    app.add_handler(CommandHandler("mywishlist", cmd_mywishlist))
    app.add_handler(CommandHandler("simi",       cmd_simi))
    app.add_handler(CommandHandler("stop",       cmd_stop))

    # Admin-only commands
    app.add_handler(CommandHandler("admin",      adm.cmd_admin))
    app.add_handler(CommandHandler("users",      adm.cmd_users))
    app.add_handler(CommandHandler("clicks",     adm.cmd_clicks))
    app.add_handler(CommandHandler("links",      adm.cmd_links))
    app.add_handler(CommandHandler("alerts",     adm.cmd_alerts))
    app.add_handler(CommandHandler("top",        adm.cmd_top))
    app.add_handler(CommandHandler("recent",     adm.cmd_recent))
    app.add_handler(CommandHandler("ping",       adm.cmd_ping))
    app.add_handler(CommandHandler("broadcast",  adm.cmd_broadcast))
    app.add_handler(CommandHandler("backup",     adm.cmd_backup))

    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    async def post_init(application):
        global _scheduler
        _scheduler = start_scheduler(application.bot)  # B6 — store ref to prevent GC

        user_commands = [
            BotCommand("start",      "Bot shuru karo"),
            BotCommand("help",       "Sari commands dekho"),
            BotCommand("search",     "Product search karo"),
            BotCommand("compare",    "2 products compare karo"),
            BotCommand("track",      "Price alert lagao"),
            BotCommand("myalerts",   "Mere active alerts dekho"),
            BotCommand("mywishlist", "Meri wishlist dekho"),
            BotCommand("simi",       "Simi shopping assistant"),
            BotCommand("stop",       "Sab modes band karo"),
        ]

        admin_commands = user_commands + [
            BotCommand("admin",     "📊 Full dashboard"),
            BotCommand("users",     "👥 User count"),
            BotCommand("clicks",    "🔗 Affiliate clicks"),
            BotCommand("alerts",    "🔔 Alert stats"),
            BotCommand("top",       "🏆 Top tracked products"),
            BotCommand("recent",    "🕐 Last 10 joined users"),
            BotCommand("ping",      "✅ Bot alive check"),
            BotCommand("broadcast", "📢 Sabko message bhejo"),
            BotCommand("backup",    "💾 DB snapshot"),
        ]

        await application.bot.set_my_commands(
            user_commands,
            scope=BotCommandScopeDefault(),
        )
        try:
            await application.bot.set_my_commands(
                admin_commands,
                scope=BotCommandScopeChat(chat_id=int(ADMIN_CHAT_ID)),
            )
        except Exception as e:
            logger.warning("Could not set admin commands: %s", e)

    app.post_init = post_init

    logger.info("Shopping GPT Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

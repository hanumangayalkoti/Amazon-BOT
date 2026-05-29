import asyncio
import logging
import os
from collections import OrderedDict
from datetime import datetime, timezone, timedelta

from telegram import (
    Update, BotCommand, BotCommandScopeDefault, BotCommandScopeChat,
    InlineKeyboardButton, InlineKeyboardMarkup, constants,
)
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler,
    ContextTypes, MessageHandler, filters,
)

import admin as adm
import ai_handler as ai
import amazon_api as api
import database as db
import simi_agent
from scheduler import start_scheduler

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("FATAL: BOT_TOKEN not set.")

ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")
if not ADMIN_CHAT_ID:
    raise SystemExit("FATAL: ADMIN_CHAT_ID not set.")

# FIX: Create a validated integer version at startup to avoid type mixing crashes
try:
    ADMIN_CHAT_ID_INT = int(ADMIN_CHAT_ID)
except ValueError:
    raise SystemExit(f"FATAL: ADMIN_CHAT_ID must be a numeric user ID, got: {ADMIN_CHAT_ID!r}")

IST = timezone(timedelta(hours=5, minutes=30))
_scheduler = None

CATEGORIES_LIST = [
    ("📱 Phones", "Phones"),
    ("💻 Laptops", "Laptops"),
    ("🎧 Audio", "Audio"),
    ("📺 TVs", "TVs"),
    ("👗 Fashion", "Fashion"),
    ("💄 Beauty", "Beauty"),
    ("🏠 Home", "Home"),
    ("📷 Cameras", "Cameras"),
]


def _safe_cap(text: str, limit: int = 1020) -> str:
    return text[:limit - 1] + "…" if len(text) > limit else text


def _cache_put(context: ContextTypes.DEFAULT_TYPE, asin: str, info: dict):
    cache = context.user_data.setdefault("product_cache", OrderedDict())
    if asin in cache:
        cache.move_to_end(asin)
    cache[asin] = info
    while len(cache) > 30:
        cache.popitem(last=False)


def _cache_get(context: ContextTypes.DEFAULT_TYPE, asin: str) -> dict | None:
    return context.user_data.get("product_cache", {}).get(asin)


def _clear_all_modes(context: ContextTypes.DEFAULT_TYPE):
    # FIX: Added compare_info1/2/3 to prevent stale data after session timeout
    for key in ["compare_step", "compare_asin1", "compare_info1", "compare_asin2", "compare_info2",
                "compare_asin3", "compare_info3", "compare_started_at",
                "waiting_for_search", "waiting_for_track",
                "simi_active", "simi_history", "simi_context"]:
        context.user_data.pop(key, None)


def _get_active_mode(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    if context.user_data.get("compare_step"):
        return "⚖️ Compare"
    if context.user_data.get("waiting_for_search"):
        return "🔍 Search"
    if context.user_data.get("waiting_for_track"):
        return "🔔 Track"
    if context.user_data.get("simi_active"):
        return "🤖 Simi"
    return None


def star_bar(rating: float) -> str:
    full = int(rating)
    half = 1 if (rating - full) >= 0.5 else 0
    empty = 5 - full - half
    return "★" * full + "½" * half + "☆" * empty


def format_product_card(info: dict) -> str:
    lines = []

    # ── Brand · Category ──────────────────────────────────────────────────────
    brand    = info.get("brand", "")
    category = info.get("category", "")
    if brand and category:
        lines.append(f"🏪 {brand}  ·  📂 {category}")
    elif brand:
        lines.append(f"🏪 {brand}")
    elif category:
        lines.append(f"📂 {category}")

    # ── Title ─────────────────────────────────────────────────────────────────
    title = info.get("title", "")
    if title:
        t = title[:100] + "…" if len(title) > 100 else title
        lines.append(f"🏷️ {t}")

    # ── Color · Model ─────────────────────────────────────────────────────────
    color = info.get("color", "")
    model = info.get("model_number", "")
    meta_parts = []
    if color:
        meta_parts.append(f"🎨 {color}")
    if model and len(model) <= 35:
        meta_parts.append(f"🔢 {model}")
    if meta_parts:
        lines.append("  ".join(meta_parts))

    # ── Condition (only if non-new) ────────────────────────────────────────────
    condition = info.get("condition", "")
    if condition and condition.lower() not in ("new", ""):
        lines.append(f"📋 Condition: {condition}")

    lines.append("")

    # ── Deal banner ───────────────────────────────────────────────────────────
    if info.get("is_lightning_deal"):
        end = info.get("deal_end_time", "")
        lines.append(f"⚡ LIGHTNING DEAL{' — ends ' + end if end else ''}")

    # ── Pricing ───────────────────────────────────────────────────────────────
    mrp     = info.get("mrp", "")
    price   = info.get("price", "")
    disc    = info.get("discount_pct", "")
    savings = info.get("savings", "")

    if mrp and mrp != price:
        lines.append(f"🏷️  MRP: {mrp}")
    if price:
        lines.append(f"💰 Buy at: {price}")
        if disc:
            try:
                pct        = int(float(disc))
                badge      = "🔥 " if pct >= 30 else ""
                save_part  = f"  (save {savings})" if savings else ""
                lines.append(f"{badge}📉 {pct}% off{save_part}")
            except Exception:
                lines.append(f"📉 {disc}% off")
    else:
        lines.append("⚠️  Price — Currently Unavailable")

    # ── Stock ─────────────────────────────────────────────────────────────────
    avail = info.get("availability", "")
    if avail:
        is_in = any(w in avail.lower() for w in ["in stock", "available"])
        icon  = "✅" if is_in else "⚠️"
        lines.append(f"📦 {icon} {avail}")
    elif not price:
        lines.append("📦 ⚠️ Out of Stock")

    # ── Delivery & Seller ─────────────────────────────────────────────────────
    if info.get("is_prime"):
        lines.append("🚚 Prime — Free & Fast Delivery")
    if info.get("is_amazon_seller"):
        lines.append("✅ Sold & fulfilled by Amazon")
        lines.append("🔄 10-day Replacement / Return Eligible")
    else:
        merchant = info.get("merchant_name", "")
        if merchant:
            lines.append(f"🏬 Seller: {merchant}")
    lines.append("")

    # ── Rating & Reviews ──────────────────────────────────────────────────────
    rating = info.get("rating", 0)
    rc     = info.get("review_count", 0)
    if rating:
        try:
            stars    = star_bar(float(rating))
            rev_part = f"  ({rc:,} reviews)" if rc else ""
            lines.append(f"⭐ {stars}  {rating}/5{rev_part}")
        except Exception:
            if rc:
                lines.append(f"💬 {rc:,} reviews")
    elif rc:
        lines.append(f"💬 {rc:,} reviews")

    # ── Best Seller / Rank badge ──────────────────────────────────────────────
    rank     = info.get("sales_rank", 0)
    rank_cat = info.get("sales_rank_category", "")
    if rank and rank_cat:
        if rank == 1:
            lines.append(f"🥇 #1 Best Seller in {rank_cat}")
        elif rank <= 3:
            lines.append(f"🥇 Best Seller #{rank} in {rank_cat}")
        elif rank <= 10:
            lines.append(f"🏆 Top 10 — #{rank} in {rank_cat}")
        elif rank <= 100:
            lines.append(f"🏆 #{rank} in {rank_cat}")
        elif rank <= 1000:
            lines.append(f"🏅 #{rank} in {rank_cat}")
        elif rank <= 5000:
            lines.append(f"📊 #{rank} in {rank_cat}")

    # ── Loyalty points ────────────────────────────────────────────────────────
    loyalty = info.get("loyalty_points", 0)
    if loyalty:
        lines.append(f"🎁 {loyalty:,} Amazon Pay points")

    # ── Tech Formats (e.g. 4K, Dolby Atmos, Wi-Fi 6) ─────────────────────────
    tech_formats = info.get("tech_formats", [])
    if tech_formats:
        lines.append(f"💡 {' · '.join(tech_formats)}")

    # ── Warranty (parsed from features) ──────────────────────────────────────
    features = info.get("features", [])
    for feat in features:
        fl = feat.lower()
        if "warranty" in fl or "guarantee" in fl:
            short = feat[:80] + "…" if len(feat) > 80 else feat
            lines.append(f"🛡️ {short}")
            break

    return "\n".join(lines)


def format_search_card(info: dict, index: int) -> str:
    lines = []
    title = info.get("title", "Product")
    t = title[:70] + "…" if len(title) > 70 else title
    lines.append(f"{index}️⃣ {t}")
    brand = info.get("brand", "")
    if brand:
        lines.append(f"🏪 {brand}")
    mrp   = info.get("mrp", "")
    price = info.get("price", "")
    disc  = info.get("discount_pct", "")
    savings = info.get("savings", "")
    if mrp and mrp != price:
        lines.append(f"🏷️ MRP: {mrp}")
    if price:
        lines.append(f"💰 Buy at: {price}")
        if disc:
            try:
                pct       = int(float(disc))
                badge     = "🔥 " if pct >= 30 else ""
                save_part = f"  (save {savings})" if savings else ""
                lines.append(f"{badge}📉 {pct}% off{save_part}")
            except Exception:
                lines.append(f"📉 {disc}% off")
    rating = info.get("rating", 0)
    rc     = info.get("review_count", 0)
    if rating:
        try:
            stars    = star_bar(float(rating))
            rev_part = f"  ({rc:,})" if rc else ""
            lines.append(f"⭐ {stars} {rating}/5{rev_part}")
        except Exception:
            pass
    if info.get("is_prime"):
        lines.append("🚚 Prime — Free & Fast Delivery")
    if info.get("is_lightning_deal"):
        lines.append("⚡ Lightning Deal!")
    return "\n".join(lines)


def product_keyboard(asin: str) -> InlineKeyboardMarkup:
    link = api.build_affiliate_link(asin)
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🛒 Buy Now", url=link),
            InlineKeyboardButton("🔔 Alert", callback_data=f"alert_opt_{asin}"),
        ],
        [
            InlineKeyboardButton("📋 Details", callback_data=f"details_{asin}"),
            InlineKeyboardButton("💾 Wishlist", callback_data=f"wish_{asin}"),
            InlineKeyboardButton("🎨 Variants", callback_data=f"variants_{asin}"),
        ],
        [
            InlineKeyboardButton("📈 Price History", callback_data=f"history_{asin}"),
            InlineKeyboardButton("⚖️ Compare", callback_data=f"compare_start_{asin}"),
            InlineKeyboardButton("📤 Share", callback_data=f"share_{asin}"),
        ],
    ])


def product_keyboard_with_back(asin: str, back_cb: str) -> InlineKeyboardMarkup:
    link = api.build_affiliate_link(asin)
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🛒 Buy Now", url=link),
            InlineKeyboardButton("🔔 Alert", callback_data=f"alert_opt_{asin}"),
        ],
        [
            InlineKeyboardButton("💾 Wishlist", callback_data=f"wish_{asin}"),
            InlineKeyboardButton("📤 Share", callback_data=f"share_{asin}"),
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data=back_cb)],
    ])


def search_result_keyboard(asin: str) -> InlineKeyboardMarkup:
    link = api.build_affiliate_link(asin)
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🛒 Buy Now", url=link),
            InlineKeyboardButton("📦 Product Info", callback_data=f"pinfo_{asin}"),
        ],
    ])


async def _notify_admin(context, user, total_users: int, is_new: bool = True):
    try:
        name = f"{user.first_name or ''} {user.last_name or ''}".strip() or "No Name"
        uname = f"@{user.username}" if user.username else "No username"
        now = datetime.now(IST)
        header = "🆕 New User!" if is_new else "🔄 Returning User"
        await context.bot.send_message(
            # FIX: Use ADMIN_CHAT_ID_INT (validated int) instead of raw string
            chat_id=ADMIN_CHAT_ID_INT,
            text=(
                f"{header}\n\n"
                f"{uname} ne Shopping GPT Bot start kiya\n\n"
                f"Name: {name}\n"
                f"ID: {user.id}\n"
                f"Date: {now.strftime('%d %b %Y %I:%M %p IST')}\n\n"
                f"Total Users: {total_users:,}"
            ),
        )
    except Exception:
        pass


async def _send_product_card(message_obj, context, info: dict, keyboard=None):
    caption = _safe_cap(format_product_card(info))
    asin = info.get("asin", "")
    kb = keyboard or product_keyboard(asin)
    image = info.get("image_url", "")
    _cache_put(context, asin, info)
    try:
        if image:
            await message_obj.reply_photo(photo=image, caption=caption, reply_markup=kb)
        else:
            await message_obj.reply_text(caption, reply_markup=kb)
    except Exception:
        await message_obj.reply_text(caption, reply_markup=kb)


async def _show_alerts_page(msg_or_query, context, alerts, page: int, edit: bool = False):
    per_page = 10
    total_pages = max(1, (len(alerts) + per_page - 1) // per_page)
    page_alerts = alerts[page * per_page: (page + 1) * per_page]
    lines = [f"🔔 Active Alerts ({len(alerts)}) — Page {page + 1}/{total_pages}\n"]
    buttons = []
    for alert in page_alerts:
        title = (alert.get("product_title") or alert["asin"])[:50]
        t_price = alert.get("tracked_price", 0)
        c_price = alert.get("current_price", 0)
        drop = " ✅" if c_price and c_price < t_price else ""
        alert_type = alert.get("alert_type", "price")
        pct = alert.get("drop_percent")
        if alert_type == "percent" and pct:
            trigger_info = f"  ({pct}% drop pe)"
        else:
            trigger_info = ""
        lines.append(
            f"📦 {title}\n"
            f"   Tracked: ₹{t_price:,.0f}  |  Now: ₹{c_price:,.0f}{drop}{trigger_info}"
        )
        buttons.append([InlineKeyboardButton(
            f"❌ Remove — {title[:28]}", callback_data=f"remove_alert_{alert['id']}",
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
    kb = InlineKeyboardMarkup(buttons)
    text = "\n\n".join(lines)
    if edit:
        try:
            await msg_or_query.message.edit_text(text, reply_markup=kb)
        except Exception:
            pass
    else:
        await msg_or_query.reply_text(text, reply_markup=kb)


# ────────────────────── Commands ──────────────────────


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    referrer_id = None
    if args and args[0].startswith("ref_"):
        try:
            referrer_id = int(args[0][4:])
        except ValueError:
            pass
    is_new = await asyncio.to_thread(db.upsert_user, user.id, user.username,
                                     user.first_name, user.last_name)
    if referrer_id and is_new and referrer_id != user.id:
        await asyncio.to_thread(db.add_referral, referrer_id, user.id)
    total = await asyncio.to_thread(db.get_user_count)
    if is_new:
        await _notify_admin(context, user, total, is_new=True)
    await update.message.reply_text(
        f"Namaste {user.first_name}! 🛍️\n\n"
        "Main hoon Shopping GPT — tera personal Amazon India assistant!\n\n"
        "Yeh sab kar sakta hoon:\n"
        "• Amazon link ya ASIN bhejo → product card\n"
        "• 'best headphones under 2000' type karo → 5 results\n"
        "• 🔔 Price alert set karo (exact price ya % drop)\n"
        "• ⚖️ /compare — 3 products compare karo\n"
        "• 💾 Wishlist mein save karo\n"
        "• 🤖 /simi — Simi se shopping advice lo\n"
        "• 📋 /deals — Aaj ki best deals\n"
        "• ⚙️ /settings — Apni categories choose karo\n\n"
        "Seedha link bhejo ya kuch bolo — shuru karte hain! 👇"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 Shopping GPT — Guide\n\n"
        "🔗 Product Info:\n"
        "  Amazon link, amzn.to link, ya ASIN seedha bhejo\n\n"
        "🔍 Search:\n"
        "  'best earbuds under 2000' — directly type karo\n"
        "  /search — bot prompt karega\n\n"
        "⚖️ Compare:\n"
        "  /compare — 3 products side-by-side\n\n"
        "🔔 Alerts:\n"
        "  Product card mein 🔔 button dabao\n"
        "  Exact price ya % drop pe alert\n"
        "  /myalerts — sab alerts dekho\n\n"
        "💾 Wishlist:\n"
        "  Product card mein 💾 button dabao\n"
        "  /mywishlist — wishlist dekho\n\n"
        "🤖 Simi:\n"
        "  /simi — AI shopping assistant activate\n\n"
        "📋 /deals — Aaj ki top deals\n"
        "⚙️ /settings — Category preferences\n"
        "📤 /invite — Dost ko refer karo"
    )


async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _clear_all_modes(context)
    context.user_data["waiting_for_search"] = True
    await update.message.reply_text(
        "🔍 Kya search karna hai? Type karo:\n"
        "Example: best gaming mouse under 2000"
    )


async def cmd_compare(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _clear_all_modes(context)
    context.user_data["compare_step"] = 1
    context.user_data["compare_started_at"] = datetime.now(IST).timestamp()
    await update.message.reply_text(
        "⚖️ 3 products compare karte hain!\n\n"
        "Pehle product ka Amazon link ya ASIN bhejo 👇\n"
        "/stop se cancel karo"
    )


async def cmd_track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _clear_all_modes(context)
    context.user_data["waiting_for_track"] = True
    await update.message.reply_text(
        "🔔 Kis product pe alert lagana hai?\n"
        "Amazon link ya ASIN bhejo 👇"
    )


async def cmd_myalerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    alerts = await asyncio.to_thread(db.get_user_alerts, user_id)
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
    items = await asyncio.to_thread(db.get_wishlist, user_id)
    if not items:
        await update.message.reply_text(
            "💾 Teri wishlist abhi khali hai.\n\n"
            "Kisi product card mein 💾 button dabao!"
        )
        return
    await update.message.reply_text(f"💾 Teri Wishlist ({len(items)} products):")
    for item in items[:15]:
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🛒 Buy", url=item["affiliate_link"]),
            InlineKeyboardButton("🔔 Alert", callback_data=f"alert_opt_{item['asin']}"),
            InlineKeyboardButton("❌ Remove", callback_data=f"remove_wish_{item['id']}"),
        ]])
        price_part = f"\n💰 {item['price']}" if item.get("price") else ""
        await update.message.reply_text(
            f"📦 {(item['product_title'] or item['asin'])[:60]}{price_part}",
            reply_markup=kb,
        )
        await asyncio.sleep(0.1)


async def cmd_simi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _clear_all_modes(context)
    context.user_data["simi_active"] = True
    context.user_data["simi_history"] = []
    context.user_data["simi_context"] = {}
    first = update.effective_user.first_name or "dost"
    await update.message.reply_text(
        f"🟢 Simi Mode ON\n\n"
        f"Hi {first}! Main hoon Simi — teri AI shopping assistant!\n\n"
        "Main Amazon pe seedha search kar sakti hoon, products compare kar sakti hoon,\n"
        "aur alerts bhi set kar sakti hoon — sab ek jagah!\n\n"
        "Bol kya chahiye 👇\n"
        "/stop se Simi mode band karo"
    )


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = _get_active_mode(context)
    _clear_all_modes(context)
    msg = f"🔴 {mode} mode band ho gaya!" if mode else "🔴 Mode OFF"
    await update.message.reply_text(
        f"{msg}\n\nNormal mode mein wapas aa gaye!\n"
        "Seedha Amazon link ya query type karo 🛍️"
    )


async def cmd_deals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    wait = await update.message.reply_text("🔍 Aaj ki best deals dhundh raha hoon...")
    try:
        deals = await asyncio.to_thread(
            api.search_deals, ["women fashion", "beauty deals", "clothing sale"], 30, 5
        )
        if not deals:
            await wait.edit_text("😕 Abhi koi acchi deals nahi mili. Thodi der baad try karo!")
            return
        now = datetime.now(IST).strftime("%d %b %Y")
        lines = [f"🎯 Aaj ki Top Deals — {now}\n"]
        for i, deal in enumerate(deals, 1):
            title = (deal.get("title") or "Product")[:55]
            price = deal.get("price", "N/A")
            disc = deal.get("discount_pct", "")
            link = deal.get("affiliate_link", "")
            # FIX: int(float(disc)) — handles decimal strings
            badge = "🔥 " if disc and int(float(disc)) >= 50 else ""
            lines.append(f"{i}. {title}\n   💰 {price}  {badge}{disc + '% off' if disc else ''}\n   👉 {link}")
        lines.append("\n💡 Koi bhi deal ka naam type karo — main dhundh dunga!")
        await wait.edit_text("\n".join(lines), disable_web_page_preview=True)
    except Exception as e:
        logger.error("cmd_deals error: %s", e)
        await wait.edit_text("❌ Deals load nahi hui. Thodi der baad try karo 🙏")


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    prefs = await asyncio.to_thread(db.get_user_preferences, user_id)
    selected = list(prefs.get("categories") or ["Fashion", "Beauty", "Electronics"])
    digest_on = prefs.get("digest_enabled", True)
    context.user_data["settings_selected"] = selected
    context.user_data["settings_digest"] = digest_on
    await _show_settings_menu(update.message, context, selected, digest_on, edit=False)


async def _show_settings_menu(msg_or_query, context, selected: list, digest_on: bool, edit: bool = False):
    buttons = []
    row = []
    for label, value in CATEGORIES_LIST:
        check = "✅ " if value in selected else ""
        row.append(InlineKeyboardButton(f"{check}{label}", callback_data=f"cat_toggle_{value}"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    digest_label = f"📰 Daily Deals: {'ON ✅' if digest_on else 'OFF ❌'}"
    buttons.append([InlineKeyboardButton(digest_label, callback_data="settings_digest_toggle")])
    buttons.append([InlineKeyboardButton("✅ Save Settings", callback_data="settings_save")])
    kb = InlineKeyboardMarkup(buttons)
    text = (
        "⚙️ Teri Settings\n\n"
        "Kaunsi categories ke deals chahiye? (select karo):\n"
        f"\nDaily Deals: {'ON — Roz 3 baar deals aayenge' if digest_on else 'OFF — Koi deals nahi aayenge'}"
    )
    if edit:
        try:
            if hasattr(msg_or_query, "message"):
                await msg_or_query.message.edit_text(text, reply_markup=kb)
            else:
                await msg_or_query.edit_text(text, reply_markup=kb)
        except Exception:
            pass
    else:
        await msg_or_query.reply_text(text, reply_markup=kb)


async def cmd_invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    count = await asyncio.to_thread(db.get_referral_count, user_id)
    bot_username = context.bot.username
    link = f"https://t.me/{bot_username}?start=ref_{user_id}"
    await update.message.reply_text(
        f"📤 Tera Referral Link:\n{link}\n\n"
        f"Tune abhi tak {count} dost refer kiye hain!\n\n"
        "Dost ko yeh link share karo — jab woh bot join kare, tu Deal Insider ban jaata hai!"
    )


# ────────────────────── Search / Do helpers ──────────────────────


async def _do_search(message, context, query: str):
    wait = await message.reply_text(f"🔍 '{query}' search ho rahi hai...")
    try:
        results = await asyncio.to_thread(api.search_products, query, 5)
    except Exception as e:
        logger.error("Search error: %s", e)
        await wait.edit_text("❌ Search mein kuch dikkat aayi. Thodi der baad try karo 🙏")
        return
    if not results:
        await wait.edit_text("😕 Koi result nahi mila — thoda alag wording try karo!")
        return
    await wait.edit_text(f"🔍 '{query}' ke {len(results)} results:")
    for i, info in enumerate(results, 1):
        asin = info.get("asin", "")
        card_text = _safe_cap(format_search_card(info, i))
        _cache_put(context, asin, info)
        context.user_data[f"scard_{asin}"] = card_text
        await message.reply_text(
            card_text,
            reply_markup=search_result_keyboard(asin),
        )
        await asyncio.sleep(0.2)


async def _do_compare(message, context, infos: list[dict]):
    def v(d, k, default="N/A"):
        return d.get(k) or default

    cards = []
    for info in infos:
        a = info["asin"]
        t = v(info, "title", "Product")[:28]
        cards.append((a, t))

    try:
        comparison_text = (
            f"{'':25} " + "  ".join(f"{t[:20]:<20}" for _, t in cards) + "\n"
            f"{'💰 Price':25} " + "  ".join(f"{v(info,'price'):20}" for info in infos) + "\n"
            f"{'📉 Discount':25} " + "  ".join(f"{(v(info,'discount_pct','—')+'%')[:20]:<20}" for info in infos) + "\n"
            f"{'⭐ Rating':25} " + "  ".join(f"{(str(v(info,'rating','—'))+'/5')[:20]:<20}" for info in infos) + "\n"
            f"{'📦 Stock':25} " + "  ".join(f"{v(info,'availability','N/A')[:20]:<20}" for info in infos)
        )
    except Exception:
        comparison_text = "Compare table generate nahi hui."

    try:
        from ai_handler import _client, INTENT_MODEL
        if _client:
            products_desc = " | ".join(
                f"Product {i+1}: {info.get('title','')[:40]} at {info.get('price','N/A')}"
                for i, info in enumerate(infos)
            )
            # FIX: Wrap blocking OpenAI call in asyncio.to_thread
            resp = await asyncio.to_thread(
                lambda: _client.chat.completions.create(
                    model=INTENT_MODEL,
                    messages=[{"role": "user", "content":
                        f"Compare these {len(infos)} Amazon India products and give a 2-line Hinglish winner recommendation: {products_desc}"}],
                    max_tokens=100, temperature=0.3,
                )
            )
            pick = resp.choices[0].message.content.strip()
        else:
            pick = "Apni zaroorat ke hisaab se choose karo!"
    except Exception:
        pick = "Apni zaroorat ke hisaab se choose karo!"

    card_text = (
        f"⚖️ Comparison Result\n\n"
        f"{comparison_text}\n\n"
        f"🤖 Simi's Pick: {pick}"
    )
    buy_buttons = [InlineKeyboardButton(f"🛒 Buy {i+1}", url=api.build_affiliate_link(a))
                   for i, (a, _) in enumerate(cards)]
    kb = InlineKeyboardMarkup([buy_buttons])
    await message.reply_text(_safe_cap(card_text), reply_markup=kb)


# ────────────────────── Message handler ──────────────────────


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # FIX: Explicit None guard instead of unsafe inline ternary
    if not update.message:
        return
    if not update.message.text:
        await update.message.reply_text(
            "Abhi sirf text aur Amazon links samajh sakta hoon.\n"
            "Seedha Amazon link ya search query bhejo! 🛍️"
        )
        return

    text = update.message.text.strip()
    user = update.effective_user
    await asyncio.to_thread(db.upsert_user, user.id, user.username,
                             user.first_name, user.last_name)

    # Admin broadcast message input
    # FIX: Use str(user.id) == ADMIN_CHAT_ID (consistent string comparison)
    if context.user_data.get("awaiting_broadcast_msg") and str(user.id) == ADMIN_CHAT_ID:
        context.user_data.pop("awaiting_broadcast_msg", None)
        context.user_data["broadcast_draft"] = text
        mode = context.user_data.get("broadcast_mode")
        selected = context.user_data.get("broadcast_selected", [])
        if mode == "all":
            total = await asyncio.to_thread(db.get_user_count_total)
            warning = f"⚠️ Yeh message SAARE {total:,} users ko jayega!"
        elif mode == "active":
            active_ids = await asyncio.to_thread(db.get_active_user_ids, 30)
            context.user_data["broadcast_selected"] = active_ids
            warning = f"⚠️ Yeh message {len(active_ids):,} active users ko jayega."
        else:
            warning = f"⚠️ Yeh message {len(selected)} selected users ko jayega."
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Confirm Send", callback_data="bc_confirm"),
            InlineKeyboardButton("❌ Cancel", callback_data="bc_cancel"),
        ]])
        await update.message.reply_text(
            f"📋 Preview:\n\n{text}\n\n{warning}", reply_markup=kb
        )
        return

    # Compare timeout check
    compare_step = context.user_data.get("compare_step")
    if compare_step:
        started = context.user_data.get("compare_started_at", 0)
        if datetime.now(IST).timestamp() - started > 600:
            _clear_all_modes(context)
            await update.message.reply_text(
                "⏰ Compare session expire ho gaya (10 min).\nDobara /compare karo!"
            )
            return

    # Compare flow — steps 1, 2, 3
    if compare_step in (1, 2, 3):
        asin, error = api.extract_asin(text)
        if error == "search":
            await update.message.reply_text(
                "⚠️ Yeh search page ka link hai — specific product ka link bhejo 😊"
            )
            return
        if not asin:
            intent = await asyncio.to_thread(ai.detect_intent, text)
            if intent in ("search_query", "off_topic", "support"):
                _clear_all_modes(context)
                await update.message.reply_text("⚠️ Compare cancel ho gaya! Ab yeh process karta hoon...")
                await _route_message(update, context, text, user)
            else:
                step_names = {1: "pehle", 2: "doosre", 3: "teesre"}
                await update.message.reply_text(
                    f"❌ Valid Amazon link ya ASIN nahi mila.\n"
                    f"{step_names.get(compare_step, '')} product ka link bhejo ya /stop karo."
                )
            return

        wait = await update.message.reply_text(
            f"⏳ Product {compare_step} fetch ho raha hai..."
        )
        try:
            info = await asyncio.to_thread(api.get_product_info, asin)
        except Exception:
            await wait.edit_text("❌ Product nahi mila — link check karo.")
            return
        await wait.delete()

        step_key = f"compare_info{compare_step}"
        context.user_data[step_key] = info
        context.user_data["compare_started_at"] = datetime.now(IST).timestamp()

        if compare_step < 3:
            context.user_data["compare_step"] = compare_step + 1
            t = info.get("title", asin)[:50]
            step_names = {2: "Doosre", 3: "Teesre"}
            next_step = step_names.get(compare_step + 1, "Agle")
            await update.message.reply_text(
                f"✅ {t}... mil gaya!\n\n{next_step} product ka link bhejo 👇\n/stop se cancel"
            )
        else:
            info1 = context.user_data.pop("compare_info1", {})
            info2 = context.user_data.pop("compare_info2", {})
            info3 = context.user_data.pop("compare_info3", {})
            _clear_all_modes(context)
            await _do_compare(update.message, context, [info1, info2, info3])
        return

    # Universal: Amazon link in any mode → always show product card
    early_asin, early_error = api.extract_asin(text)
    if early_asin:
        context.user_data.pop("waiting_for_search", None)
        context.user_data.pop("waiting_for_track", None)
        wait = await update.message.reply_text("⏳ Product info fetch ho rahi hai...")
        try:
            info = await asyncio.to_thread(api.get_product_info, early_asin)
        except Exception:
            await wait.edit_text("❌ Product nahi mila — Amazon link check karo.")
            return
        await wait.delete()
        await _send_product_card(update.message, context, info)
        await asyncio.to_thread(db.log_click, user.id, early_asin)
        return
    if early_error == "search":
        await update.message.reply_text(
            "⚠️ Yeh Amazon search page ka link hai — kisi ek product pe click karo, "
            "phir us product ka link bhejo 😊"
        )
        return

    # Search mode
    if context.user_data.get("waiting_for_search"):
        context.user_data["waiting_for_search"] = False
        await _do_search(update.message, context, text)
        return

    # Track mode — user sent text (not a link), search and show product cards with alert button
    if context.user_data.get("waiting_for_track"):
        context.user_data["waiting_for_track"] = False
        await update.message.reply_text(
            "🔍 Product dhundh raha hoon — result mein 🔔 Alert button dabao!"
        )
        await _do_search(update.message, context, text)
        return

    await _route_message(update, context, text, user)


async def _route_message(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, user):
    simi_active = context.user_data.get("simi_active")

    if simi_active:
        await update.message.chat.send_action(constants.ChatAction.TYPING)
        thinking = await update.message.reply_text("Simi soch rahi hai... 🤔")
        history = context.user_data.get("simi_history", [])
        simi_ctx = context.user_data.get("simi_context", {})
        first_name = user.first_name or "dost"
        try:
            reply, updated_history = await simi_agent.run_simi_agent(
                user.id, first_name, text, history, simi_ctx
            )
            context.user_data["simi_history"] = updated_history
            context.user_data["simi_context"] = simi_ctx
        except Exception as e:
            logger.error("Simi agent error: %s", e)
            reply = "Kuch technical issue aa gaya 😅 Thodi der baad try karo!"
        try:
            await thinking.delete()
        except Exception:
            pass
        await update.message.reply_text(
            _safe_cap(reply) + "\n\n— Simi 🤖  |  /stop se bahar jao"
        )
        return

    intent = await asyncio.to_thread(ai.detect_intent, text)

    if intent == "search_query":
        await _do_search(update.message, context, text)

    elif intent == "alert_request":
        query = await asyncio.to_thread(ai.extract_search_query_from_alert, text)
        await _do_search(update.message, context, query)

    elif intent in ("support", "off_topic"):
        context.user_data["simi_active"] = True
        context.user_data["simi_history"] = []
        context.user_data["simi_context"] = {}
        await update.message.chat.send_action(constants.ChatAction.TYPING)
        thinking = await update.message.reply_text("Simi soch rahi hai... 🤔")
        try:
            reply, history = await simi_agent.run_simi_agent(
                user.id, user.first_name or "dost", text, [], {}
            )
            context.user_data["simi_history"] = history
        except Exception:
            reply = "Koi bhi shopping sawaal pooch — main help karunga! 🛍️"
        try:
            await thinking.delete()
        except Exception:
            pass
        await update.message.reply_text(
            _safe_cap(reply) + "\n\n— Simi 🤖  |  /stop se bahar jao"
        )
    else:
        await _do_search(update.message, context, text)


async def handle_non_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Abhi sirf text aur Amazon links samajh sakta hoon.\n"
        "Seedha Amazon link ya search query bhejo! 🛍️"
    )


# ────────────────────── Callback handler ──────────────────────


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user = query.from_user
    await query.answer()

    if data == "noop":
        return

    # ── Settings ──
    if data.startswith("cat_toggle_"):
        cat = data[11:]
        selected = context.user_data.get("settings_selected", [])
        if cat in selected:
            selected.remove(cat)
        else:
            selected.append(cat)
        context.user_data["settings_selected"] = selected
        digest_on = context.user_data.get("settings_digest", True)
        await _show_settings_menu(query, context, selected, digest_on, edit=True)
        return

    if data == "settings_digest_toggle":
        digest_on = not context.user_data.get("settings_digest", True)
        context.user_data["settings_digest"] = digest_on
        selected = context.user_data.get("settings_selected", [])
        await _show_settings_menu(query, context, selected, digest_on, edit=True)
        return

    if data == "settings_save":
        selected = context.user_data.get("settings_selected", [])
        digest_on = context.user_data.get("settings_digest", True)
        if not selected:
            await query.answer("Kam se kam ek category select karo!", show_alert=True)
            return
        await asyncio.to_thread(db.set_user_preferences, user.id, selected, digest_on)
        cats_str = ", ".join(selected)
        digest_str = "ON — Roz 3 baar deals aayenge" if digest_on else "OFF"
        try:
            await query.message.edit_text(
                f"✅ Settings save ho gayi!\n\n"
                f"Categories: {cats_str}\n"
                f"Daily Deals: {digest_str}\n\n"
                f"Ab teri categories ke deals seedha milenge!"
            )
        except Exception:
            pass
        return

    # ── Broadcast callbacks ──
    if data == "bc_all":
        total = await asyncio.to_thread(db.get_user_count_total)
        context.user_data["broadcast_mode"] = "all"
        context.user_data["awaiting_broadcast_msg"] = True
        await query.message.reply_text(
            f"📢 Send to ALL {total:,} users\n\nAb apna message type karo 👇"
        )
        return

    if data == "bc_active":
        active_ids = await asyncio.to_thread(db.get_active_user_ids, 30)
        context.user_data["broadcast_mode"] = "active"
        context.user_data["broadcast_selected"] = active_ids
        context.user_data["awaiting_broadcast_msg"] = True
        await query.message.reply_text(
            f"✅ Active users: {len(active_ids):,} (last 30 days)\n\nAb apna message type karo 👇"
        )
        return

    if data == "bc_select":
        context.user_data["broadcast_mode"] = "select"
        context.user_data["broadcast_selected"] = []
        context.user_data["broadcast_page"] = 0
        await adm.show_user_selection_page(query, context, page=0)
        return

    if data.startswith("bc_page_"):
        page = int(data[8:])
        context.user_data["broadcast_page"] = page
        await adm.show_user_selection_page(query, context, page=page, edit=True)
        return

    if data.startswith("bc_toggle_"):
        uid = int(data[10:])
        selected = context.user_data.setdefault("broadcast_selected", [])
        if uid in selected:
            selected.remove(uid)
        else:
            selected.append(uid)
        page = context.user_data.get("broadcast_page", 0)
        await adm.show_user_selection_page(query, context, page=page, edit=True)
        return

    if data == "bc_done_select":
        selected = context.user_data.get("broadcast_selected", [])
        if not selected:
            await query.answer("Koi user select nahi kiya!", show_alert=True)
            return
        context.user_data["awaiting_broadcast_msg"] = True
        await query.message.reply_text(
            f"✅ {len(selected)} users selected.\n\nAb apna message type karo 👇"
        )
        return

    if data == "bc_confirm":
        msg_text = context.user_data.pop("broadcast_draft", None)
        mode = context.user_data.get("broadcast_mode")
        if not msg_text:
            await query.answer("Message nahi mila!", show_alert=True)
            return
        if mode == "all":
            user_ids = await asyncio.to_thread(db.get_all_user_ids)
        else:
            user_ids = context.user_data.get("broadcast_selected", [])
        sent, failed = 0, 0
        status_msg = await query.message.reply_text(f"📤 Sending to {len(user_ids):,} users...")
        for i, uid in enumerate(user_ids):
            try:
                await context.bot.send_message(chat_id=uid, text=msg_text)
                sent += 1
            except Exception as e:
                logger.warning("Broadcast failed for user %s: %s", uid, e)
                failed += 1
            # FIX: Increased sleep to 0.1s (10 msg/sec) — safer for Telegram flood limits
            await asyncio.sleep(0.1)
            if (i + 1) % 20 == 0:
                await asyncio.sleep(1)
                try:
                    await status_msg.edit_text(
                        f"📤 Progress: {sent + failed}/{len(user_ids)} ({sent} sent, {failed} failed)..."
                    )
                except Exception:
                    pass
        context.user_data.pop("broadcast_mode", None)
        context.user_data.pop("broadcast_selected", None)
        try:
            await status_msg.edit_text(
                f"✅ Broadcast complete!\n\nSent: {sent:,}\nFailed: {failed:,}"
            )
        except Exception:
            pass
        return

    if data == "bc_cancel":
        for k in ["broadcast_mode", "broadcast_selected", "broadcast_draft", "awaiting_broadcast_msg"]:
            context.user_data.pop(k, None)
        await query.message.reply_text("❌ Broadcast cancel ho gaya.")
        return

    # ── Alerts page ──
    if data.startswith("alerts_page_"):
        page = int(data[12:])
        alerts = context.user_data.get("my_alerts_cache", [])
        if not alerts:
            alerts = await asyncio.to_thread(db.get_user_alerts, user.id)
            context.user_data["my_alerts_cache"] = alerts
        await _show_alerts_page(query, context, alerts, page=page, edit=True)
        return

    # ── Product Info (same message edit) ──
    if data.startswith("pinfo_"):
        asin = data[6:]
        info = _cache_get(context, asin)
        if not info:
            try:
                info = await asyncio.to_thread(api.get_product_info, asin)
                _cache_put(context, asin, info)
            except Exception:
                await query.answer("❌ Product info load nahi hui.", show_alert=True)
                return
        card_text = _safe_cap(format_product_card(info))
        back_card = context.user_data.get(f"scard_{asin}", "")
        context.user_data[f"back_scard_{asin}"] = back_card
        kb = product_keyboard_with_back(asin, back_cb=f"sback_{asin}")
        try:
            await query.edit_message_text(card_text, reply_markup=kb)
        except Exception:
            pass
        return

    # ── Back to search card ──
    if data.startswith("sback_"):
        asin = data[6:]
        original_text = context.user_data.get(f"scard_{asin}", "")
        if not original_text:
            info = _cache_get(context, asin)
            if info:
                original_text = format_search_card(info, 1)
        if original_text:
            try:
                await query.edit_message_text(original_text, reply_markup=search_result_keyboard(asin))
            except Exception:
                pass
        return

    # ── Details (bullet points, existing card) ──
    if data.startswith("details_"):
        asin = data[8:]
        info = _cache_get(context, asin)
        if not info:
            try:
                info = await asyncio.to_thread(api.get_product_info, asin)
                _cache_put(context, asin, info)
            except Exception:
                await query.answer("❌ Details load nahi hui.", show_alert=True)
                return
        features = info.get("features", [])
        base = format_product_card(info)
        if features:
            feat_lines = ["\n\n📋 Key Features"]
            for f in features[:5]:
                short = f[:100] + "…" if len(f) > 100 else f
                feat_lines.append(f"• {short}")
            full_text = _safe_cap(base + "\n".join(feat_lines))
        else:
            full_text = _safe_cap(base + "\n\n📋 Features — Abhi available nahi")
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🛒 Buy Now", url=api.build_affiliate_link(asin)),
            InlineKeyboardButton("⬅️ Back", callback_data=f"back_card_{asin}"),
        ]])
        try:
            if query.message.photo:
                await query.edit_message_caption(full_text, reply_markup=kb)
            else:
                await query.edit_message_text(full_text, reply_markup=kb)
        except Exception:
            pass
        return

    # ── Back to product card ──
    if data.startswith("back_card_"):
        asin = data[10:]
        info = _cache_get(context, asin)
        if not info:
            try:
                info = await asyncio.to_thread(api.get_product_info, asin)
            except Exception:
                await query.answer("❌ Wapas nahi ja saka.", show_alert=True)
                return
        card_text = _safe_cap(format_product_card(info))
        kb = product_keyboard(asin)
        try:
            if query.message.photo:
                await query.edit_message_caption(card_text, reply_markup=kb)
            else:
                await query.edit_message_text(card_text, reply_markup=kb)
        except Exception:
            pass
        return

    # ── Alert options ──
    if data.startswith("alert_opt_"):
        asin = data[10:]
        buttons = [
            [InlineKeyboardButton("🔔 Koi bhi drop pe", callback_data=f"alert_any_{asin}")],
            [InlineKeyboardButton("📉 10% gire toh", callback_data=f"alert_pct_10_{asin}")],
            [InlineKeyboardButton("📉 20% gire toh", callback_data=f"alert_pct_20_{asin}")],
            [InlineKeyboardButton("📉 30% gire toh", callback_data=f"alert_pct_30_{asin}")],
            [InlineKeyboardButton("❌ Cancel", callback_data="noop")],
        ]
        await query.message.reply_text(
            "🔔 Kab alert chahiye?", reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    if data.startswith("alert_any_"):
        asin = data[10:]
        info = _cache_get(context, asin)
        if not info:
            try:
                info = await asyncio.to_thread(api.get_product_info, asin)
            except Exception:
                await query.message.reply_text("❌ Product info nahi mili.")
                return
        await asyncio.to_thread(
            db.add_price_alert, user.id, asin, info.get("title", ""), info.get("price_amount", 0),
            api.build_affiliate_link(asin), "price", None
        )
        await query.message.reply_text(
            f"🔔 Alert set!\n📦 {info.get('title','')[:50]}\n"
            f"💰 {info.get('price','')}\n\nPrice giregi toh seedha notify karunga! 📲"
        )
        return

    if data.startswith("alert_pct_"):
        parts = data[10:].split("_", 1)
        pct = int(parts[0])
        asin = parts[1]
        info = _cache_get(context, asin)
        if not info:
            try:
                info = await asyncio.to_thread(api.get_product_info, asin)
            except Exception:
                await query.message.reply_text("❌ Product info nahi mili.")
                return
        current = info.get("price_amount", 0)
        target = round(current * (1 - pct / 100))
        await asyncio.to_thread(
            db.add_price_alert, user.id, asin, info.get("title", ""), current,
            api.build_affiliate_link(asin), "percent", float(pct)
        )
        await query.message.reply_text(
            f"🔔 Alert set!\n📦 {info.get('title','')[:50]}\n"
            f"💰 Current: {info.get('price','')}\n"
            f"📉 Alert: {pct}% girne pe (≈₹{target:,.0f})\n\n"
            f"Price {pct}% giregi toh seedha notify karunga! 📲"
        )
        return

    # ── Wishlist ──
    if data.startswith("wish_"):
        asin = data[5:]
        info = _cache_get(context, asin)
        if not info:
            try:
                info = await asyncio.to_thread(api.get_product_info, asin)
            except Exception:
                await query.message.reply_text("❌ Product info nahi mili.")
                return
        added = await asyncio.to_thread(
            db.add_to_wishlist, user.id, asin, info.get("title", ""),
            info.get("price", ""), info.get("image_url", ""),
            api.build_affiliate_link(asin), info.get("price_amount", 0)
        )
        if added:
            await query.message.reply_text(
                f"💾 Wishlist mein add ho gaya!\n📦 {info.get('title','')[:50]}\n\n"
                f"/mywishlist se dekho."
            )
        else:
            await query.message.reply_text("ℹ️ Yeh product pehle se wishlist mein hai!")
        return

    if data.startswith("remove_wish_"):
        item_id = int(data[12:])
        await asyncio.to_thread(db.remove_from_wishlist, item_id, user.id)
        await query.message.reply_text("✅ Wishlist se remove ho gaya!")
        try:
            await query.message.delete()
        except Exception:
            pass
        return

    # ── Remove alert ──
    if data.startswith("remove_alert_"):
        alert_id = int(data[13:])
        await asyncio.to_thread(db.remove_alert, alert_id, user.id)
        context.user_data.pop("my_alerts_cache", None)
        await query.message.reply_text("✅ Alert remove ho gaya!")
        try:
            await query.message.delete()
        except Exception:
            pass
        return

    # ── Price History ──
    if data.startswith("history_"):
        asin = data[8:]
        rows = await asyncio.to_thread(db.get_price_history, asin, 7)
        info = _cache_get(context, asin)
        title = info.get("title", asin)[:55] if info else asin
        if not rows:
            await query.message.reply_text(
                f"📈 {title}\n\nAbhi tak koi price history nahi hai.\n"
                "Alert lagao — har 6 ghante price track hogi!"
            )
            return
        lines = [f"📈 Price History — {title}\n"]
        prices = []
        for row in rows:
            checked = row["checked_at"]
            if checked.tzinfo is None:
                checked = checked.replace(tzinfo=timezone.utc)
            dt_ist = checked.astimezone(IST)
            date_str = dt_ist.strftime("%d %b, %I:%M %p")
            lines.append(f"• {date_str} IST — ₹{row['price']:,.0f}")
            prices.append(row["price"])
        if prices:
            mn, mx = min(prices), max(prices)
            current = prices[0]
            if current <= mn:
                lines.append("\n✅ Abhi price sabse neeche hai — buy karna sahi time hai!")
            elif current >= mx:
                lines.append("\n⚠️ Abhi price sabse upar hai — thoda wait karo!")
        await query.message.reply_text("\n".join(lines))
        return

    # ── Variants ──
    if data.startswith("variants_"):
        asin = data[9:]
        variants = await asyncio.to_thread(api.get_product_variations, asin)
        if not variants:
            await query.answer("Is product ke variants available nahi hain.", show_alert=True)
            return
        buttons = []
        for v in variants[:8]:
            parts = []
            if v.get("color"):
                parts.append(v["color"])
            if v.get("size"):
                parts.append(v["size"])
            if v.get("storage"):
                parts.append(v["storage"])
            price = v.get("price", "")
            label = " · ".join(parts) if parts else v.get("title", "Variant")[:30]
            if price:
                label += f" — {price}"
            buttons.append([InlineKeyboardButton(label, callback_data=f"pinfo_{v['asin']}")])
        await query.message.reply_text(
            "🎨 Available Variants:", reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    # ── Compare start from button ──
    if data.startswith("compare_start_"):
        asin = data[14:]
        info = _cache_get(context, asin)
        if not info:
            try:
                info = await asyncio.to_thread(api.get_product_info, asin)
            except Exception:
                info = {"asin": asin, "title": asin}
        _clear_all_modes(context)
        context.user_data["compare_step"] = 2
        context.user_data["compare_info1"] = info
        context.user_data["compare_started_at"] = datetime.now(IST).timestamp()
        t = info.get("title", asin)[:50]
        await query.message.reply_text(
            f"⚖️ Pehla product set: {t}\n\nAb doosre product ka link bhejo 👇\n/stop se cancel"
        )
        return

    # ── Share ──
    if data.startswith("share_"):
        asin = data[6:]
        info = _cache_get(context, asin)
        if not info:
            try:
                info = await asyncio.to_thread(api.get_product_info, asin)
            except Exception:
                info = {}
        title = (info.get("title") or asin)[:70]
        price = info.get("price", "")
        disc = info.get("discount_pct", "")
        link = api.build_affiliate_link(asin)
        share_text = f"🛍️ {title}"
        if price:
            share_text += f"\n💰 {price}"
        if disc:
            share_text += f"  ({disc}% off!)"
        share_text += f"\n\n🛒 {link}\n\n📲 Shopping GPT se mila!"
        await query.message.reply_text(
            f"📤 Is message ko forward karo apne dosto ko:\n\n{share_text}\n\n"
            f"Upar wale message ko copy karo ya long-press karke forward karo!",
            disable_web_page_preview=True,
        )
        return


# ────────────────────── Main ──────────────────────


def main():
    db.init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    # User commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(CommandHandler("compare", cmd_compare))
    app.add_handler(CommandHandler("track", cmd_track))
    app.add_handler(CommandHandler("myalerts", cmd_myalerts))
    app.add_handler(CommandHandler("mywishlist", cmd_mywishlist))
    app.add_handler(CommandHandler("simi", cmd_simi))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("deals", cmd_deals))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("invite", cmd_invite))

    # Admin commands
    app.add_handler(CommandHandler("admin", adm.cmd_admin))
    app.add_handler(CommandHandler("users", adm.cmd_users))
    app.add_handler(CommandHandler("clicks", adm.cmd_clicks))
    app.add_handler(CommandHandler("alerts", adm.cmd_alerts_admin))
    app.add_handler(CommandHandler("top", adm.cmd_top))
    app.add_handler(CommandHandler("recent", adm.cmd_recent))
    app.add_handler(CommandHandler("ping", adm.cmd_ping))
    app.add_handler(CommandHandler("backup", adm.cmd_backup))
    app.add_handler(CommandHandler("broadcast", adm.cmd_broadcast))
    app.add_handler(CommandHandler("setchannel", adm.cmd_setchannel))
    app.add_handler(CommandHandler("removechannel", adm.cmd_removechannel))
    app.add_handler(CommandHandler("digest", adm.cmd_digest_manual))
    app.add_handler(CommandHandler("lightning", adm.cmd_lightning_manual))

    # Message & callback handlers
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(~filters.TEXT & ~filters.COMMAND, handle_non_text))
    app.add_handler(CallbackQueryHandler(handle_callback))

    async def on_startup(application):
        global _scheduler
        commands_user = [
            BotCommand("start", "Bot shuru karo"),
            BotCommand("search", "Products search karo"),
            BotCommand("compare", "3 products compare karo"),
            BotCommand("simi", "AI shopping assistant"),
            BotCommand("deals", "Aaj ki best deals"),
            BotCommand("track", "Price alert lagao"),
            BotCommand("myalerts", "Mere alerts dekho"),
            BotCommand("mywishlist", "Meri wishlist"),
            BotCommand("settings", "Apni preferences set karo"),
            BotCommand("invite", "Dost ko refer karo"),
            BotCommand("stop", "Active mode band karo"),
            BotCommand("help", "Help & Guide"),
        ]
        await application.bot.set_my_commands(commands_user, scope=BotCommandScopeDefault())
        admin_extra = [
            BotCommand("admin", "Admin dashboard"),
            BotCommand("users", "User stats"),
            BotCommand("broadcast", "Broadcast message"),
            BotCommand("setchannel", "Channel set karo"),
            BotCommand("digest", "Manual digest trigger"),
            BotCommand("lightning", "Manual lightning check"),
            BotCommand("ping", "Bot status"),
            BotCommand("backup", "DB snapshot"),
        ]
        try:
            await application.bot.set_my_commands(
                commands_user + admin_extra,
                # FIX: Use ADMIN_CHAT_ID_INT (validated int) — prevents crash on set_my_commands
                scope=BotCommandScopeChat(chat_id=ADMIN_CHAT_ID_INT),
            )
        except Exception:
            pass
        _scheduler = start_scheduler(application.bot)
        logger.info("Shopping GPT Bot started!")

    app.post_init = on_startup

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

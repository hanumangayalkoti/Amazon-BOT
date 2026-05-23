import asyncio
import logging
import os
from datetime import datetime

from telegram import (
    Update,
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


def product_keyboard(asin: str) -> InlineKeyboardMarkup:
    link = api.build_affiliate_link(asin)
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🛒 Buy Now", url=link),
            InlineKeyboardButton("🔔 Price Alert", callback_data=f"alert_{asin}"),
        ],
        [
            InlineKeyboardButton("📋 Features", callback_data=f"features_{asin}"),
            InlineKeyboardButton("💾 Wishlist", callback_data=f"wish_{asin}"),
            InlineKeyboardButton("📈 Price History", callback_data=f"history_{asin}"),
        ],
    ])


def search_result_keyboard(asin: str) -> InlineKeyboardMarkup:
    link = api.build_affiliate_link(asin)
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🛒 Buy", url=link),
            InlineKeyboardButton("🔔 Alert", callback_data=f"alert_{asin}"),
            InlineKeyboardButton("💾 Wishlist", callback_data=f"wish_{asin}"),
        ],
        [
            InlineKeyboardButton("📋 Details", callback_data=f"features_{asin}"),
        ],
    ])


def star_bar(rating: float) -> str:
    full  = int(rating)
    half  = 1 if (rating - full) >= 0.5 else 0
    empty = 5 - full - half
    return "★" * full + "½" * half + "☆" * empty


def format_product_card(info: dict) -> str:
    lines = []
    if info.get("brand"):
        lines.append(f"<b>Brand</b> — {info['brand']}")
    if info.get("title"):
        t = info["title"]
        lines.append(f"<b>Product</b> — {t[:200] + '…' if len(t) > 200 else t}")
    if info.get("category"):
        lines.append(f"<b>Category</b> — {info['category']}")
    lines.append("")
    if info.get("price"):
        lines.append(f"💰 <b>Price</b> — {info['price']}")
    else:
        lines.append("⚠️ <b>Stock</b> — Currently Unavailable")
    if info.get("discount_pct") and info.get("savings"):
        lines.append(f"🔖 <b>Discount</b> — {info['discount_pct']}% off  (save {info['savings']})")
    elif info.get("discount_pct"):
        lines.append(f"🔖 <b>Discount</b> — {info['discount_pct']}% off")
    if info.get("availability") and info.get("price"):
        is_in = any(w in info["availability"].lower() for w in ["in stock", "available"])
        icon  = "✅" if is_in else "⚠️"
        lines.append(f"{icon} <b>Stock</b> — {info['availability']}")
    if info.get("rating"):
        stars = star_bar(float(info["rating"]))
        rc    = f" ({info['review_count']:,} reviews)" if info.get("review_count") else ""
        lines.append(f"⭐ <b>Rating</b> — {stars} {info['rating']}/5{rc}")
    return "\n".join(lines)


async def _send_product_card(update_or_msg, context, info: dict, keyboard=None):
    caption  = format_product_card(info)
    asin     = info.get("asin", "")
    kb       = keyboard or product_keyboard(asin)
    image    = info.get("image_url", "")

    if "product_cache" not in context.user_data:
        context.user_data["product_cache"] = {}
    context.user_data["product_cache"][asin] = info

    try:
        if image:
            await update_or_msg.reply_photo(
                photo=image, caption=caption,
                parse_mode="HTML", reply_markup=kb,
            )
        else:
            await update_or_msg.reply_text(
                caption, parse_mode="HTML", reply_markup=kb,
            )
    except Exception:
        await update_or_msg.reply_text(
            caption, parse_mode="HTML", reply_markup=kb,
        )


async def _notify_admin(context, user, total_users: int):
    try:
        name = f"{user.first_name or ''} {user.last_name or ''}".strip()
        uname = f"@{user.username}" if user.username else "No username"
        now   = datetime.now().strftime("%d %b %Y, %I:%M %p")
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=(
                f"👤 <b>New User!</b>\n\n"
                f"Name: {name}\nUsername: {uname}\n"
                f"User ID: <code>{user.id}</code>\nTime: {now} IST\n\n"
                f"Total Users: <b>{total_users:,}</b>"
            ),
            parse_mode="HTML",
            disable_notification=True,
        )
    except Exception:
        pass


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user    = update.effective_user
    is_new  = await asyncio.to_thread(db.upsert_user, user.id, user.username,
                                      user.first_name, user.last_name)
    total   = await asyncio.to_thread(db.get_user_count)
    if is_new:
        await _notify_admin(context, user, total)

    await update.message.reply_text(
        f"Namaste <b>{user.first_name}!</b> 🛍️\n\n"
        "Main hoon <b>Shopping GPT</b> — tera personal Amazon India assistant!\n\n"
        "✨ Yeh sab kar sakta hoon:\n"
        "• Amazon link ya ASIN bhejo → product card\n"
        "• <b>'best headphones under 2000'</b> type karo → 5 results\n"
        "• 🔔 Price alert set karo — price gire toh notify\n"
        "• ⚖️ /compare — 2 products compare karo\n"
        "• 💾 Wishlist mein save karo\n"
        "• 🤖 /Simi — Simi se shopping advice le sakte ho\n\n"
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
    context.user_data["waiting_for_search"] = True
    context.user_data.pop("simi_active", None)
    await update.message.reply_text(
        "🔍 Kya search karna hai? Seedha type karo:\n"
        "<i>Example: best gaming mouse under 2000</i>",
        parse_mode="HTML",
    )


async def cmd_compare(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["compare_step"] = 1
    context.user_data.pop("compare_asin1", None)
    context.user_data.pop("compare_info1", None)
    context.user_data.pop("simi_active", None)
    await update.message.reply_text(
        "⚖️ Chaliye 2 products compare karte hain! 🔍\n\n"
        "Pehle product ka Amazon link ya ASIN bhejo 👇"
    )


async def cmd_track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["waiting_for_track"] = True
    context.user_data.pop("simi_active", None)
    await update.message.reply_text(
        "🔔 Kis product pe alert lagana hai?\n"
        "Amazon link ya ASIN bhejo 👇\n\n"
        "<i>Ya seedha bolo: 'headphones under 999 pe alert'</i>",
        parse_mode="HTML",
    )


async def cmd_myalerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    alerts  = await asyncio.to_thread(db.get_user_alerts, user_id)
    if not alerts:
        await update.message.reply_text(
            "🔔 Abhi koi price alert set nahi hai.\n\n"
            "Kisi product card mein 🔔 button dabao alert lagane ke liye!"
        )
        return

    await update.message.reply_text(
        f"🔔 <b>Tere Active Alerts ({len(alerts)}):</b>",
        parse_mode="HTML",
    )
    for alert in alerts:
        title   = alert["product_title"] or alert["asin"]
        t_price = alert["tracked_price"]
        c_price = alert["current_price"]
        drop    = "✅ Already dropped!" if c_price < t_price else ""
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🛒 Buy Now", url=alert["affiliate_link"]),
            InlineKeyboardButton("❌ Remove", callback_data=f"remove_alert_{alert['id']}"),
        ]])
        await update.message.reply_text(
            f"📦 <b>{title[:60]}</b>\n"
            f"Tracked at: ₹{t_price:,.0f}  |  Current: ₹{c_price:,.0f}  {drop}",
            parse_mode="HTML",
            reply_markup=kb,
        )
        await asyncio.sleep(0.1)


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
                InlineKeyboardButton("🛒 Buy", url=item["affiliate_link"]),
                InlineKeyboardButton("🔔 Alert lagao", callback_data=f"alert_{item['asin']}"),
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
    context.user_data["simi_active"] = True
    context.user_data["waiting_for_search"] = False
    context.user_data["waiting_for_track"]  = False
    context.user_data.pop("compare_step", None)
    first = update.effective_user.first_name or "dost"
    await update.message.reply_text(
        f"🟢 <b>Simi Mode ON</b>\n\n"
        f"Hi <b>{first}!</b> Main hoon Simi — teri shopping assistant! 😊\n\n"
        "Koi bhi shopping sawaal pooch — products, deals, comparisons, buying advice!\n\n"
        "<i>Amazon link bhejoge toh product card bhi dikhega!\n"
        "Simi se bahar jaane ke liye /stop type karo.</i>",
        parse_mode="HTML",
    )


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("simi_active", None)
    context.user_data.pop("simi_history", None)
    context.user_data.pop("compare_step", None)
    context.user_data.pop("waiting_for_search", None)
    context.user_data.pop("waiting_for_track", None)
    await update.message.reply_text(
        "🔴 <b>Simi Mode OFF</b>\n\n"
        "Normal mode mein wapas aa gaye!\n"
        "Seedha Amazon link ya query type karo — main dhundh lunga 🛍️",
        parse_mode="HTML",
    )


async def _do_search(message, context, query: str, with_alert_note: bool = False):
    wait = await message.reply_text(f"🔍 <b>'{query}'</b> search ho rahi hai...", parse_mode="HTML")
    try:
        results = await asyncio.to_thread(api.search_items, query, 5)
    except Exception:
        await wait.edit_text("❌ Search mein kuch dikkat aayi. Thodi der baad try karo 🙏")
        return

    if not results:
        await wait.edit_text(
            "😕 Koi result nahi mila — thoda alag wording try karo!"
        )
        return

    header = f"🔍 <b>'{query}'</b> ke results:\n"
    if with_alert_note:
        header += "Jis pe alert lagana ho, uska 🔔 <b>Alert</b> button dabao! 👆\n"
    await wait.edit_text(header, parse_mode="HTML")

    for i, info in enumerate(results, 1):
        asin   = info.get("asin", "")
        title  = info.get("title", "Product")[:60]
        price  = info.get("price", "N/A")
        rating = info.get("rating", "")
        disc   = info.get("discount_pct", "")

        stars = f"⭐{rating}" if rating else ""
        off   = f"  🔖{disc}% off" if disc else ""
        line  = f"{i}️⃣ <b>{title}</b>\n   {stars}  💰{price}{off}"

        if "product_cache" not in context.user_data:
            context.user_data["product_cache"] = {}
        context.user_data["product_cache"][asin] = info

        await message.reply_text(
            line,
            parse_mode="HTML",
            reply_markup=search_result_keyboard(asin),
        )
        await asyncio.sleep(0.2)


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


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user = update.effective_user

    await asyncio.to_thread(db.upsert_user, user.id, user.username,
                             user.first_name, user.last_name)

    if "product_cache" not in context.user_data:
        context.user_data["product_cache"] = {}

    compare_step = context.user_data.get("compare_step")
    waiting_search = context.user_data.get("waiting_for_search")
    waiting_track  = context.user_data.get("waiting_for_track")
    simi_active    = context.user_data.get("simi_active")

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
                "Example: https://www.amazon.in/dp/B0XXXXXX ya B0XXXXXX"
            )
            return
        wait = await update.message.reply_text("⏳ Pehla product fetch ho raha hai...")
        try:
            info1 = await asyncio.to_thread(api.get_product_info, asin)
        except Exception:
            await wait.edit_text("❌ Product nahi mila — ek baar link check karo.")
            return
        await wait.delete()
        context.user_data["compare_step"]  = 2
        context.user_data["compare_asin1"] = asin
        context.user_data["compare_info1"] = info1
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
            await update.message.reply_text("❌ Valid Amazon link ya ASIN nahi mila.")
            return
        wait = await update.message.reply_text("⏳ Doosra product fetch ho raha hai...")
        try:
            info2 = await asyncio.to_thread(api.get_product_info, asin)
        except Exception:
            await wait.edit_text("❌ Doosra product nahi mila.")
            return
        await wait.delete()
        info1 = context.user_data.pop("compare_info1", {})
        context.user_data.pop("compare_step", None)
        context.user_data.pop("compare_asin1", None)
        await _do_compare(update.message, context, info1, info2)
        return

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
            pass
        elif detected_intent == "alert_request":
            query = await asyncio.to_thread(ai.extract_search_query_from_alert, text)
            await _do_search(update.message, context, query, with_alert_note=True)
            return
        else:
            if "simi_history" not in context.user_data:
                context.user_data["simi_history"] = []
            history = context.user_data["simi_history"]
            await update.message.chat.send_action(constants.ChatAction.TYPING)
            reply = await asyncio.to_thread(ai.simi_reply, user.first_name or "dost", history, text)
            history.append({"role": "user",      "content": text})
            history.append({"role": "assistant", "content": reply})
            context.user_data["simi_history"] = history[-20:]
            footer = "\n\n— Simi 🤖  |  /search karo ya seedha query type karo"
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
        except ValueError as e:
            await wait.edit_text(
                f"😕 Yeh product nahi mila — ho sakta hai link expire ho gaya ho.\n"
                f"Koi aur product try karo!"
            )
            return
        except RuntimeError as e:
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

    elif intent in ("support", "off_topic"):
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


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    data   = query.data
    user   = query.from_user
    cache  = context.user_data.get("product_cache", {})

    await query.answer()

    if data.startswith("alert_"):
        asin = data[6:]
        info = cache.get(asin)
        if not info:
            try:
                info = await asyncio.to_thread(api.get_product_info, asin)
            except Exception:
                await query.message.reply_text(
                    "❌ Product info nahi mili. Thodi der baad try karo."
                )
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

    elif data.startswith("features_"):
        asin = data[9:]
        info = cache.get(asin)
        if not info or not info.get("features"):
            try:
                info = await asyncio.to_thread(api.get_product_info, asin)
                if "product_cache" not in context.user_data:
                    context.user_data["product_cache"] = {}
                context.user_data["product_cache"][asin] = info
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
        info = cache.get(asin)
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
        if not rows:
            await query.message.reply_text(
                "📈 Abhi tak is product ki price history nahi hai.\n"
                "Alert lagao — phir har 6 ghante price track hogi!"
            )
            return
        lines = [f"📈 <b>Price History (last 7 days) — {asin}</b>\n"]
        for row in rows:
            date  = row["checked_at"].strftime("%d %b, %I:%M %p")
            lines.append(f"• {date} — ₹{row['price']:,.0f}")
        await query.message.reply_text("\n".join(lines), parse_mode="HTML")

    elif data.startswith("remove_alert_"):
        alert_id = int(data[13:])
        await asyncio.to_thread(db.remove_alert, alert_id, user.id)
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
    app.add_handler(CommandHandler("simi",    cmd_simi))
    app.add_handler(CommandHandler("stop",       cmd_stop))

    app.add_handler(CommandHandler("users",      adm.cmd_users))
    app.add_handler(CommandHandler("links",      adm.cmd_links))
    app.add_handler(CommandHandler("alerts",     adm.cmd_alerts))
    app.add_handler(CommandHandler("top",        adm.cmd_top))
    app.add_handler(CommandHandler("broadcast",  adm.cmd_broadcast))
    app.add_handler(CommandHandler("backup",     adm.cmd_backup))

    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    async def post_init(application):
        start_scheduler(application.bot)

    app.post_init = post_init

    logger.info("Shopping GPT Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

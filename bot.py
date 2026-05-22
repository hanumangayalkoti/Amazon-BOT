import logging
import os

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from amazon_api import extract_asin, get_product_info

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Namaste! 🛍️\n\n"
        "Amazon India ka product link bhejo.\n\n"
        "✅ Supported:\n"
        "• https://www.amazon.in/dp/B0DLFMFBJW\n"
        "• https://amzn.to/4fCHUBz\n"
        "• B0DLFMFBJW (sirf ASIN)\n\n"
        "❌ Search page links kaam nahi karti."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()
    asin, error = extract_asin(text)

    if error == "search":
        await update.message.reply_text(
            "⚠️ Yeh search page ka link hai.\n\n"
            "Kisi ek product pe click karo, phir us product page ka link bhejo.\n"
            "Example: https://www.amazon.in/dp/B0DLFMFBJW"
        )
        return

    if not asin:
        await update.message.reply_text(
            "❌ Valid Amazon India link ya ASIN nahi mila.\n\n"
            "• https://www.amazon.in/dp/B0DLFMFBJW\n"
            "• https://amzn.to/4fCHUBz\n"
            "• B0DLFMFBJW"
        )
        return

    wait_msg = await update.message.reply_text("⏳ Product info fetch ho rahi hai...")

    try:
        info = get_product_info(asin)
    except ValueError as e:
        await wait_msg.edit_text(f"❌ {e}")
        return
    except RuntimeError as e:
        logger.error("API error: %s", e)
        await wait_msg.edit_text("❌ Amazon API se data nahi aaya. Thodi der baad try karo.")
        return

    try:
        await wait_msg.delete()
    except Exception:
        pass

    caption = format_product(info)
    image_url = info.get("image_url", "")

    try:
        if image_url:
            await update.message.reply_photo(
                photo=image_url,
                caption=caption,
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(caption, parse_mode="HTML")
    except Exception as e:
        logger.error("Send error: %s", e)
        await update.message.reply_text(format_product_plain(info))


def format_product(info: dict) -> str:
    lines = []

    brand = info.get("brand", "")
    title = info.get("title", "")
    category = info.get("category", "")
    price = info.get("price", "")
    discount_pct = info.get("discount_pct")
    savings = info.get("savings", "")
    availability = info.get("availability", "")
    rating = info.get("rating", "")
    review_count = info.get("review_count")
    features = info.get("features", [])
    affiliate_link = info.get("affiliate_link", "")
    asin = info.get("asin", "")

    if brand:
        lines.append(f"<b>Brand</b> - {brand}")

    if title:
        short = title[:200] + "…" if len(title) > 200 else title
        lines.append(f"<b>Product Name</b> - {short}")

    if category:
        lines.append(f"<b>Category</b> - {category}")

    lines.append("")

    if price:
        lines.append(f"💰 <b>Price</b> - {price}")
    else:
        lines.append("⚠️ <b>Stock</b> - Currently Unavailable")

    if discount_pct and savings:
        lines.append(f"🔖 <b>Discount</b> - {discount_pct}% off (save {savings})")
    elif discount_pct:
        lines.append(f"🔖 <b>Discount</b> - {discount_pct}% off")

    if availability and price:
        icon = "✅" if any(w in availability.lower() for w in ["in stock", "available"]) else "⚠️"
        lines.append(f"{icon} <b>Stock</b> - {availability}")

    if rating:
        stars = star_bar(float(rating))
        if review_count is not None:
            lines.append(f"⭐ <b>Rating</b> - {stars} {rating}/5 ({review_count} reviews)")
        else:
            lines.append(f"⭐ <b>Rating</b> - {stars} {rating}/5")

    if features:
        lines.append("")
        lines.append("✨ <b>Key Features</b>")
        for f in features:
            short_f = f[:150] + "…" if len(f) > 150 else f
            lines.append(f"• {short_f}")

    lines.append("")

    if affiliate_link:
        lines.append(f'<b><a href="{affiliate_link}">🛒 Buy Now </a></b>')

    if asin:
        camel = f"https://camelcamelcamel.com/product/{asin}"
        lines.append(f'<a href="{camel}">📈 Price History</a>')

    return "\n".join(lines)


def format_product_plain(info: dict) -> str:
    lines = []
    if info.get("brand"):
        lines.append(f"Brand - {info['brand']}")
    if info.get("title"):
        lines.append(f"Product Name - {info['title'][:200]}")
    if info.get("category"):
        lines.append(f"Category - {info['category']}")
    lines.append("")
    if info.get("price"):
        lines.append(f"💰 Price - {info['price']}")
    if info.get("discount_pct"):
        lines.append(f"🔖 Discount - {info['discount_pct']}% off")
    if info.get("availability"):
        lines.append(f"✅ Stock - {info['availability']}")
    if info.get("rating"):
        lines.append(f"⭐ Rating - {info['rating']}/5")
    features = info.get("features", [])
    if features:
        lines.append("\n✨ Key Features")
        for f in features:
            lines.append(f"• {f[:150]}")
    lines.append("")
    if info.get("affiliate_link"):
        lines.append(f"🛒 Buy Now: {info['affiliate_link']}")
    if info.get("asin"):
        lines.append(f"📈 Price History: https://camelcamelcamel.com/product/{info['asin']}")
    return "\n".join(lines)


def star_bar(rating: float) -> str:
    full = int(rating)
    half = 1 if (rating - full) >= 0.5 else 0
    empty = 5 - full - half
    return "★" * full + "½" * half + "☆" * empty


def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

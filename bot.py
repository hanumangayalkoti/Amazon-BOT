import asyncio
import logging
import os

from telegram import Update, InputMediaPhoto
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
        "Amazon India ka link ya ASIN bhejo — main price, discount, rating aur affiliate link deta hoon.\n\n"
        "Example:\n"
        "• https://www.amazon.in/dp/B0DLFMFBJW\n"
        "• B0DLFMFBJW"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()
    asin = extract_asin(text)

    if not asin:
        await update.message.reply_text(
            "❌ Valid Amazon India link ya ASIN nahi mila.\n\n"
            "Example: https://www.amazon.in/dp/B0DLFMFBJW\nYa sirf: B0DLFMFBJW"
        )
        return

    processing_msg = await update.message.reply_text("⏳ Product info fetch ho rahi hai...")

    try:
        info = get_product_info(asin)
    except ValueError as e:
        await processing_msg.edit_text(f"❌ {e}")
        return
    except RuntimeError as e:
        logger.error("API error: %s", e)
        await processing_msg.edit_text("❌ Amazon API se data nahi aaya. Thodi der baad try karo.")
        return

    lines = []

    title = info.get("title", "Title unavailable")
    brand = info.get("brand")
    if brand:
        lines.append(f"🏷️ *{escape(brand)}*")
    lines.append(f"📦 {escape(title)}")
    lines.append("")

    price = info.get("price")
    savings = info.get("savings")
    discount_pct = info.get("discount_pct")

    if price:
        lines.append(f"💰 *Price:* {escape(price)}")
    if savings and discount_pct:
        lines.append(f"🔖 *Discount:* {escape(str(discount_pct))}% off  \\(save {escape(savings)}\\)")
    elif savings:
        lines.append(f"🔖 *Savings:* {escape(savings)}")
    elif discount_pct:
        lines.append(f"🔖 *Discount:* {escape(str(discount_pct))}% off")

    availability = info.get("availability")
    if availability:
        icon = "✅" if "stock" in availability.lower() or "available" in availability.lower() else "⚠️"
        lines.append(f"{icon} *Stock:* {escape(availability)}")

    rating = info.get("rating")
    review_count = info.get("review_count")
    if rating and review_count:
        lines.append(f"⭐ *Rating:* {escape(str(rating))}/5  \\({escape(str(review_count))} reviews\\)")
    elif rating:
        lines.append(f"⭐ *Rating:* {escape(str(rating))}/5")

    lines.append("")
    affiliate_link = info.get("affiliate_link", "")
    lines.append(f"🔗 [Buy on Amazon India]({affiliate_link})")

    caption = "\n".join(lines)

    image_url = info.get("image_url")

    try:
        await processing_msg.delete()
    except Exception:
        pass

    try:
        if image_url:
            await update.message.reply_photo(
                photo=image_url,
                caption=caption,
                parse_mode="MarkdownV2",
            )
        else:
            await update.message.reply_text(caption, parse_mode="MarkdownV2")
    except Exception as e:
        logger.error("Send error: %s", e)
        plain = "\n".join(
            [
                f"📦 {info.get('title', 'N/A')}",
                f"💰 Price: {info.get('price', 'N/A')}",
                f"🔖 Discount: {info.get('discount_pct', 'N/A')}%",
                f"⭐ Rating: {info.get('rating', 'N/A')}",
                f"✅ Stock: {info.get('availability', 'N/A')}",
                f"🔗 {affiliate_link}",
            ]
        )
        await update.message.reply_text(plain)


def escape(text: str) -> str:
    special = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in str(text))


def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

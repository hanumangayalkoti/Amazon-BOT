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
        "Amazon India ka *kisi bhi product ka link* bhejo — main price, discount, rating aur affiliate link deta hoon.\n\n"
        "✅ *Supported links:*\n"
        "• https://www.amazon.in/dp/B0DLFMFBJW\n"
        "• https://amzn.to/4fCHUBz\n"
        "• B0DLFMFBJW (sirf ASIN)\n\n"
        "❌ *Search page links kaam nahi karti* — kisi specific product ka link bhejo.",
        parse_mode="Markdown",
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()
    asin, error = extract_asin(text)

    if error == "search":
        await update.message.reply_text(
            "⚠️ Yeh ek *search page* ka link hai — ismein koi specific product nahi hota.\n\n"
            "Kisi *ek product* pe click karo, phir us product page ka link bhejo.\n\n"
            "Example: https://www.amazon.in/dp/B0DLFMFBJW",
            parse_mode="Markdown",
        )
        return

    if error == "invalid" or not asin:
        await update.message.reply_text(
            "❌ Valid Amazon India link ya ASIN nahi mila.\n\n"
            "✅ *Supported formats:*\n"
            "• https://www.amazon.in/dp/B0DLFMFBJW\n"
            "• https://amzn.to/4fCHUBz\n"
            "• B0DLFMFBJW",
            parse_mode="Markdown",
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
        await processing_msg.edit_text(
            "❌ Amazon API se data nahi aaya. Thodi der baad try karo."
        )
        return

    lines = []

    # Brand & Title
    brand = info.get("brand", "")
    title = info.get("title", "Title unavailable")
    if brand:
        lines.append(f"🏷️ *{esc(brand)}*")
    lines.append(f"📦 {esc(title)}")

    # Category
    category = info.get("category", "")
    if category:
        lines.append(f"📂 {esc(category)}")

    lines.append("")

    # Price & Discount
    price = info.get("price", "")
    savings = info.get("savings", "")
    discount_pct = info.get("discount_pct")

    if price:
        lines.append(f"💰 *Price:* {esc(price)}")
    if discount_pct and savings:
        lines.append(f"🔖 *Discount:* {esc(str(discount_pct))}% off \\(save {esc(savings)}\\)")
    elif discount_pct:
        lines.append(f"🔖 *Discount:* {esc(str(discount_pct))}% off")
    elif savings:
        lines.append(f"🔖 *Savings:* {esc(savings)}")

    # Deal badge
    deal_type = info.get("deal_type", "")
    if deal_type:
        lines.append(f"⚡ *Deal:* {esc(deal_type)}")

    # Condition
    condition = info.get("condition", "")
    if condition and condition.lower() != "new":
        lines.append(f"📋 *Condition:* {esc(condition)}")

    # Availability
    availability = info.get("availability", "")
    if availability:
        icon = "✅" if any(w in availability.lower() for w in ["stock", "available"]) else "⚠️"
        lines.append(f"{icon} *Stock:* {esc(availability)}")

    # Rating & Reviews
    rating = info.get("rating", "")
    review_count = info.get("review_count")
    if rating and review_count is not None:
        stars = _star_display(float(rating))
        lines.append(f"⭐ {stars} {esc(str(rating))}/5 \\({esc(str(review_count))} reviews\\)")
    elif rating:
        lines.append(f"⭐ *Rating:* {esc(str(rating))}/5")

    # Features
    features = info.get("features", [])
    if features:
        lines.append("")
        lines.append("✨ *Key Features:*")
        for f in features:
            short = f[:120] + "…" if len(f) > 120 else f
            lines.append(f"• {esc(short)}")

    # Affiliate Link
    affiliate_link = info.get("affiliate_link", "")
    lines.append("")
    lines.append(f"🔗 [👉 Buy on Amazon India]({affiliate_link})")
    lines.append(f"_tag: {esc(info.get('asin', ''))} \\| {esc('dealskoti-21')}_")

    caption = "\n".join(lines)
    image_url = info.get("image_url", "")

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
        logger.error("Send error (MarkdownV2): %s", e)
        # Fallback — plain text
        plain_lines = [
            f"📦 {info.get('title', 'N/A')}",
        ]
        if brand:
            plain_lines.insert(0, f"🏷️ {brand}")
        if price:
            plain_lines.append(f"💰 Price: {price}")
        if discount_pct:
            plain_lines.append(f"🔖 Discount: {discount_pct}%")
        if availability:
            plain_lines.append(f"✅ Stock: {availability}")
        if rating:
            plain_lines.append(f"⭐ Rating: {rating}/5 ({review_count} reviews)")
        if features:
            plain_lines.append("✨ Features:")
            for f in features:
                plain_lines.append(f"  • {f[:100]}")
        plain_lines.append(f"🔗 {affiliate_link}")
        try:
            if image_url:
                await update.message.reply_photo(
                    photo=image_url,
                    caption="\n".join(plain_lines),
                )
            else:
                await update.message.reply_text("\n".join(plain_lines))
        except Exception as e2:
            logger.error("Fallback send error: %s", e2)


def _star_display(rating: float) -> str:
    full = int(rating)
    half = 1 if (rating - full) >= 0.5 else 0
    empty = 5 - full - half
    return "★" * full + "½" * half + "☆" * empty


def esc(text: str) -> str:
    """Escape special chars for MarkdownV2."""
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

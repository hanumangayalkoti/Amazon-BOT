import asyncio
import logging
import re

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message

from api_service import fetch_product_data
from config import BOT_TOKEN

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

URL_PATTERN = re.compile(
    r"https?://"
    r"(www\.)?"
    r"(amazon\.(com|co\.uk|in|de|fr|it|es|ca|com\.au|co\.jp)|amzn\.to|amzn\.in)"
    r"[\w\-._~:/?#\[\]@!$&'()*+,;=%]*",
    re.IGNORECASE,
)


def format_product_message(data: dict) -> str:
    title = data.get("title") or "N/A"
    price = data.get("price") or "N/A"
    asin = data.get("asin") or "N/A"
    affiliate = data.get("affiliate") or "N/A"
    image = data.get("image") or "N/A"

    return (
        "📦 *Product Details*\n\n"
        f"📝 *Title:* {title}\n"
        f"💰 *Price:* {price}\n"
        f"🆔 *ASIN:* {asin}\n"
        f"🔗 *Link:* {affiliate}\n"
        f"🖼 *Image:* {image}"
    )


@dp.message(CommandStart())
async def handle_start(message: Message) -> None:
    await message.answer(
        "👋 Welcome! Send me an Amazon product link and I'll fetch its details for you.\n\n"
        "Example:\n"
        "`https://www.amazon.com/dp/B08XYZ1234`",
        parse_mode="Markdown",
    )


@dp.message(F.text)
async def handle_message(message: Message) -> None:
    text = message.text or ""

    match = URL_PATTERN.search(text)
    if not match:
        await message.answer(
            "⚠️ Please send a valid Amazon product link.\n\n"
            "Example:\n"
            "`https://www.amazon.com/dp/B08XYZ1234`",
            parse_mode="Markdown",
        )
        return

    link = match.group(0)
    processing_msg = await message.answer("⏳ Fetching product details, please wait...")

    try:
        data = await fetch_product_data(link)

        if not data:
            await processing_msg.edit_text(
                "❌ Could not find product details for this link. "
                "Please check the URL and try again."
            )
            return

        reply = format_product_message(data)
        await processing_msg.edit_text(reply, parse_mode="Markdown")

    except Exception as e:
        logger.error("Error fetching product data: %s", e)
        await processing_msg.edit_text(
            "❌ An error occurred while fetching product details. Please try again later."
        )


async def main() -> None:
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN is not set. Check your environment variables.")

    logger.info("Starting bot...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

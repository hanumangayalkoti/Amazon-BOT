import asyncio
import logging
import database as db

logger = logging.getLogger(__name__)


def format_deal_message(deal: dict) -> str:
    lines = []
    title = (deal.get("title") or "Product")[:70]
    lines.append(f"🛍️ {title}")
    if deal.get("is_lightning_deal"):
        end = deal.get("deal_end_time", "")
        lines.append(f"⚡ LIGHTNING DEAL{' — ' + end + ' tak' if end else ''}")
    price = deal.get("price", "")
    mrp = deal.get("mrp", "")
    disc = deal.get("discount_pct", "")
    savings = deal.get("savings", "")
    if price:
        price_line = f"💰 {price}"
        if disc:
            # FIX: int(float(disc)) to handle decimal discount strings
            badge = "🔥 " if int(float(disc)) >= 50 else ""
            price_line += f"  ({badge}{disc}% off"
            if savings:
                price_line += f" — save {savings}"
            price_line += ")"
        if mrp:
            price_line += f"\n🏷️ MRP: {mrp}"
        lines.append(price_line)
    rating = deal.get("rating", 0)
    reviews = deal.get("review_count", 0)
    if rating:
        rev_part = f"  ({reviews:,} reviews)" if reviews else ""
        lines.append(f"⭐ {rating}/5{rev_part}")
    if deal.get("is_prime"):
        lines.append("🚀 Prime Delivery")
    brand = deal.get("brand", "")
    if brand:
        lines.append(f"🏪 {brand}")
    link = deal.get("affiliate_link", "")
    if link:
        lines.append(f"\n🛒 Buy Now: {link}")
    return "\n".join(lines)


async def post_lightning_deal(bot, deal: dict) -> bool:
    # FIX: DB call must be wrapped in asyncio.to_thread — was blocking the event loop
    channel_ids = await asyncio.to_thread(db.get_channel_ids)
    if not channel_ids:
        logger.info("No channels configured, skipping lightning deal post")
        return False
    msg_text = "⚡ LIGHTNING DEAL — Abhi Lao!\n\n" + format_deal_message(deal)
    posted = False
    for ch_id in channel_ids:
        try:
            sent = await bot.send_message(chat_id=ch_id, text=msg_text, disable_web_page_preview=True)
            # FIX: DB log call also wrapped in asyncio.to_thread
            await asyncio.to_thread(db.log_channel_post, deal.get("asin", ""), "lightning", sent.message_id)
            posted = True
            logger.info("Lightning deal posted to channel %s", ch_id)
        except Exception as e:
            logger.error("Failed to post lightning deal to %s: %s", ch_id, e)
    return posted


async def post_daily_digest_to_channel(bot, deals: list[dict], slot_label: str = "Subah") -> bool:
    # FIX: DB call wrapped in asyncio.to_thread
    channel_ids = await asyncio.to_thread(db.get_channel_ids)
    if not channel_ids or not deals:
        return False
    from datetime import datetime, timezone, timedelta
    IST = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(IST).strftime("%d %b %Y")
    header = f"🎯 {slot_label} ki Top Deals — {now}\n\n"
    lines = [header]
    for i, deal in enumerate(deals[:5], 1):
        title = (deal.get("title") or "Product")[:55]
        price = deal.get("price", "")
        disc = deal.get("discount_pct", "")
        link = deal.get("affiliate_link", "")
        # FIX: int(float(disc)) to handle decimal discount strings
        badge = "🔥 " if disc and int(float(disc)) >= 50 else ""
        line = f"{i}. {title}\n   💰 {price}"
        if disc:
            line += f"  ({badge}{disc}% off)"
        if link:
            line += f"\n   👉 {link}"
        lines.append(line)
    lines.append("\n📲 Shopping GPT Bot se aur deals pao!")
    msg_text = "\n".join(lines)
    for ch_id in channel_ids:
        try:
            sent = await bot.send_message(chat_id=ch_id, text=msg_text, disable_web_page_preview=True)
            for deal in deals[:5]:
                if deal.get("asin"):
                    # FIX: DB log wrapped in asyncio.to_thread
                    await asyncio.to_thread(db.log_channel_post, deal["asin"], "digest", sent.message_id)
            logger.info("Daily digest posted to channel %s", ch_id)
        except Exception as e:
            logger.error("Failed to post digest to %s: %s", ch_id, e)
    return True

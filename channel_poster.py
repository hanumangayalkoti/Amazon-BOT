import asyncio
import logging
import database as db
from telegram.constants import ParseMode

logger = logging.getLogger(__name__)


def _short_title(title: str, limit: int = 50) -> str:
    """Truncate title to limit characters."""
    if not title:
        return "Product"
    return title[:limit - 1] + "…" if len(title) > limit else title


def _escape_html(text: str) -> str:
    """Escape special HTML characters for Telegram HTML parse mode."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _build_card_caption(deal: dict) -> str:
    """
    Build a rich HTML caption for a deal card.
    MRP is shown with HTML strikethrough <s>...</s>.
    Affiliate link is shown as bold text (no inline button).
    Title is capped at 50 characters.
    """
    lines = []

    title = _short_title(deal.get("title", "Product"))
    lines.append(f"<b>📦 {_escape_html(title)}</b>")
    lines.append("")

    brand = deal.get("brand", "")
    category = deal.get("category", "") or deal.get("sales_rank_category", "")
    if brand:
        lines.append(f"🏪 Brand: {_escape_html(brand)}")
    if category:
        lines.append(f"📂 Category: {_escape_html(category)}")

    lines.append("")

    if deal.get("is_lightning_deal"):
        end = deal.get("deal_end_time", "")
        lines.append(f"⚡ <b>LIGHTNING DEAL{' — ends ' + _escape_html(end) if end else ''}</b>")

    mrp = deal.get("mrp", "")
    price = deal.get("price", "")
    disc = deal.get("discount_pct", "")
    savings = deal.get("savings", "")

    if mrp and mrp != price:
        lines.append(f"💸 MRP: <s>{_escape_html(mrp)}</s>")
    if price:
        lines.append(f"💰 Price: <b>{_escape_html(price)}</b>")
        if disc:
            try:
                pct = int(float(disc))
                badge = "🔥 " if pct >= 30 else ""
                save_part = f"  (save {_escape_html(savings)})" if savings else ""
                lines.append(f"{badge}📉 Discount: <b>{pct}% off</b>{save_part}")
            except Exception:
                lines.append(f"📉 Discount: <b>{_escape_html(str(disc))}% off</b>")
    else:
        lines.append("⚠️ Price: Currently Unavailable")

    lines.append("")

    avail = deal.get("availability", "")
    if avail:
        is_in = any(w in avail.lower() for w in ["in stock", "available"])
        icon = "✅" if is_in else "⚠️"
        lines.append(f"📦 Stock: {icon} {_escape_html(avail)}")

    if deal.get("is_prime"):
        lines.append("🚀 Prime Delivery")

    merchant = deal.get("merchant_name", "")
    if merchant:
        if deal.get("is_amazon_seller"):
            lines.append("🏬 Seller: Amazon")
        else:
            lines.append(f"🏬 Seller: {_escape_html(merchant)}")

    rating = deal.get("rating", 0)
    rc = deal.get("review_count", 0)
    if rating or rc:
        lines.append("")
    if rating:
        try:
            lines.append(f"⭐ Rating: {float(rating):.1f}/5")
        except Exception:
            lines.append(f"⭐ Rating: {rating}/5")
    if rc:
        lines.append(f"💬 Reviews: {int(rc):,} customer reviews")

    link = deal.get("affiliate_link", "")
    if link:
        lines.append("")
        lines.append(f"🛒 <b>Buy Now: {_escape_html(link)}</b>")

    return "\n".join(lines)


async def send_deal_cards(bot, deals: list[dict], target_ids: list[str],
                          post_type: str = "hourly_deal", category: str = "") -> int:
    """
    Send photo deal cards to all target_ids (channels + users).
    Falls back to text message if no image URL or photo send fails.
    Returns total successful sends.
    """
    if not deals or not target_ids:
        return 0

    total_sent = 0
    for target_id in target_ids:
        for deal in deals:
            caption = _build_card_caption(deal)
            image_url = deal.get("image_url", "")
            asin = deal.get("asin", "")

            try:
                if image_url:
                    sent_msg = await bot.send_photo(
                        chat_id=target_id,
                        photo=image_url,
                        caption=caption,
                        parse_mode=ParseMode.HTML,
                    )
                else:
                    sent_msg = await bot.send_message(
                        chat_id=target_id,
                        text=caption,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True,
                    )

                if asin and str(target_id) in [str(t) for t in target_ids[:1]]:
                    await asyncio.to_thread(
                        db.log_channel_post, asin, post_type,
                        sent_msg.message_id if sent_msg else 0,
                        category
                    )

                total_sent += 1
            except Exception as e:
                logger.error("Failed to send deal card to %s (ASIN %s): %s", target_id, asin, e)

            await asyncio.sleep(0.5)

    return total_sent


async def post_deals_to_all(bot, deals: list[dict], category: str = "") -> bool:
    """
    Main entry point: posts deal cards to channels + digest users.
    Logs each ASIN in channel_posts for deduplication tracking.
    Returns True if at least one card was sent to a channel.
    """
    channel_ids = await asyncio.to_thread(db.get_channel_ids)

    channel_success = 0
    for deal in deals:
        asin = deal.get("asin", "")
        caption = _build_card_caption(deal)
        image_url = deal.get("image_url", "")

        for ch_id in channel_ids:
            try:
                if image_url:
                    sent_msg = await bot.send_photo(
                        chat_id=ch_id,
                        photo=image_url,
                        caption=caption,
                        parse_mode=ParseMode.HTML,
                    )
                else:
                    sent_msg = await bot.send_message(
                        chat_id=ch_id,
                        text=caption,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True,
                    )
                await asyncio.to_thread(
                    db.log_channel_post, asin, "hourly_deal",
                    sent_msg.message_id if sent_msg else 0,
                    category
                )
                channel_success += 1
                logger.info("Deal card posted to channel %s — ASIN: %s", ch_id, asin)
            except Exception as e:
                logger.error("Failed to post to channel %s (ASIN %s): %s", ch_id, asin, e)

        await asyncio.sleep(0.8)

    users = await asyncio.to_thread(db.get_users_with_digest_enabled)
    logger.info("Sending %d deal cards to %d users", len(deals), len(users))
    sent_users, failed_users = 0, 0

    for user in users:
        user_id = user["user_id"]
        for deal in deals:
            asin = deal.get("asin", "")
            caption = _build_card_caption(deal)
            image_url = deal.get("image_url", "")
            try:
                if image_url:
                    await bot.send_photo(
                        chat_id=user_id,
                        photo=image_url,
                        caption=caption,
                        parse_mode=ParseMode.HTML,
                    )
                else:
                    await bot.send_message(
                        chat_id=user_id,
                        text=caption,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True,
                    )
            except Exception as e:
                logger.warning("Failed to send deal to user %s: %s", user_id, e)
                failed_users += 1
                break
            await asyncio.sleep(0.1)
        else:
            sent_users += 1
        await asyncio.sleep(0.15)

    logger.info("Hourly deals sent — channels: %d, users: %d, failed: %d",
                channel_success, sent_users, failed_users)
    return channel_success > 0


async def post_lightning_deal(bot, deal: dict) -> bool:
    """Legacy function for lightning deals — sends text format."""
    channel_ids = await asyncio.to_thread(db.get_channel_ids)
    if not channel_ids:
        logger.info("No channels configured, skipping lightning deal post")
        return False

    caption = _build_card_caption(deal)
    image_url = deal.get("image_url", "")
    header = "⚡ <b>LIGHTNING DEAL — Abhi Lao!</b>\n\n"
    full_caption = header + caption
    posted = False

    for ch_id in channel_ids:
        try:
            if image_url:
                sent = await bot.send_photo(
                    chat_id=ch_id,
                    photo=image_url,
                    caption=full_caption,
                    parse_mode=ParseMode.HTML,
                )
            else:
                sent = await bot.send_message(
                    chat_id=ch_id,
                    text=full_caption,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            await asyncio.to_thread(
                db.log_channel_post, deal.get("asin", ""), "lightning",
                sent.message_id, ""
            )
            posted = True
            logger.info("Lightning deal posted to channel %s", ch_id)
        except Exception as e:
            logger.error("Failed to post lightning deal to %s: %s", ch_id, e)

    return posted


async def post_daily_digest_to_channel(bot, deals: list[dict], slot_label: str = "Subah") -> bool:
    """Legacy digest function — kept for backward compatibility."""
    return await post_deals_to_all(bot, deals, category=slot_label)


def format_deal_message(deal: dict) -> str:
    """Legacy plain-text format — kept for backward compatibility."""
    return _build_card_caption(deal)

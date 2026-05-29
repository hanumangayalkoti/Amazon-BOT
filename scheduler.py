import asyncio
import logging
from datetime import datetime, timezone, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

import amazon_api as api
import database as db
import channel_poster as cp

logger = logging.getLogger(__name__)
# FIX: Use IST timezone (UTC+5:30) consistently
IST = timezone(timedelta(hours=5, minutes=30))

DIGEST_SLOTS = [
    {"hour": 3,  "minute": 30, "label": "Subah",   "categories": ["women fashion", "beauty", "skincare deals"]},
    {"hour": 6,  "minute": 30, "label": "Dopahar", "categories": ["fashion deals", "clothing", "accessories deals"]},
    {"hour": 17, "minute": 0,  "label": "Shaam",   "categories": ["home kitchen", "fashion", "beauty deals"]},
]


async def check_prices(bot):
    # Only check ASINs that still have active (notified=FALSE) alerts
    asins = await asyncio.to_thread(db.get_all_tracked_asins)
    if not asins:
        return
    logger.info("Price check started — %d ASINs", len(asins))
    for asin in asins:
        try:
            info = await asyncio.to_thread(api.get_product_info, asin)
            new_price = info.get("price_amount", 0)
            if new_price:
                await asyncio.to_thread(db.save_price_snapshot, asin, new_price)

            # FIX: get_alerts_for_asin now only returns notified=FALSE alerts
            alerts = await asyncio.to_thread(db.get_alerts_for_asin, asin)
            for alert in alerts:
                tracked = alert["tracked_price"]
                alert_type = alert.get("alert_type", "price")
                drop_pct = alert.get("drop_percent")
                if not new_price:
                    # FIX: Still update current_price even if can't fire, so display stays fresh
                    await asyncio.to_thread(db.update_alert_current_price, alert["id"], 0)
                    continue

                should_fire = False
                if alert_type == "percent" and drop_pct:
                    target_price = tracked * (1 - drop_pct / 100)
                    should_fire = new_price <= target_price
                else:
                    should_fire = new_price < tracked

                if should_fire:
                    save = round(tracked - new_price, 2)
                    title = alert.get("product_title") or info.get("title", "Product")
                    title_short = title[:60] + "…" if len(title) > 60 else title
                    link = alert.get("affiliate_link") or api.build_affiliate_link(asin)
                    pct_drop = round((save / tracked) * 100) if tracked else 0
                    text = (
                        f"🔔 Price Drop Alert!\n\n"
                        f"📦 {title_short}\n"
                        f"📉 Pehle: ₹{tracked:,.0f}\n"
                        f"💰 Ab: {info.get('price', f'₹{new_price:,.0f}')}"
                        f"  ({pct_drop}% off — save ₹{save:,.0f}!)\n\n"
                        f"🛒 {link}"
                    )
                    try:
                        await bot.send_message(chat_id=alert["user_id"], text=text,
                                               disable_web_page_preview=True)
                    except Exception as e:
                        logger.warning("Could not notify user %s: %s", alert["user_id"], e)

                    # FIX: Use update_alert_tracked_price (updates BOTH tracked + current)
                    # so the new lower price becomes the new baseline before marking notified
                    await asyncio.to_thread(db.update_alert_tracked_price, alert["id"], new_price)
                    await asyncio.to_thread(db.mark_alert_notified, alert["id"])
                else:
                    # FIX: Non-firing check — only update current_price, NOT tracked_price
                    await asyncio.to_thread(db.update_alert_current_price, alert["id"], new_price)

            await asyncio.sleep(1)
        except Exception as e:
            logger.error("Error checking ASIN %s: %s", asin, e)
    logger.info("Price check complete")


async def _run_digest_slot(bot, slot: dict):
    label = slot["label"]
    categories = slot["categories"]
    logger.info("Running %s digest...", label)
    try:
        global_deals = await asyncio.to_thread(
            api.search_deals, categories, 30, 5
        )
        await cp.post_daily_digest_to_channel(bot, global_deals, slot_label=label)
    except Exception as e:
        logger.error("Channel digest error (%s): %s", label, e)

    users = await asyncio.to_thread(db.get_users_with_digest_enabled)
    logger.info("Sending %s digest to %d users", label, len(users))
    sent, failed = 0, 0
    for user in users:
        user_id = user["user_id"]
        user_cats = list(user.get("categories") or categories)
        try:
            deals = await asyncio.to_thread(
                api.search_deals, user_cats, 30, 5
            )
            if not deals:
                deals = await asyncio.to_thread(api.search_deals, categories, 30, 5)
            if not deals:
                continue
            now_ist = datetime.now(IST).strftime("%d %b %Y")
            lines = [f"🎯 {label} ki Top Deals — {now_ist}\n"]
            for i, deal in enumerate(deals[:5], 1):
                title = (deal.get("title") or "Product")[:55]
                price = deal.get("price", "")
                disc = deal.get("discount_pct", "")
                link = deal.get("affiliate_link", "")
                # FIX: int(float(disc)) to handle decimal discount strings like "30.5"
                badge = "🔥 " if disc and int(float(disc)) >= 50 else ""
                line = f"{i}. {title}\n   💰 {price}"
                if disc:
                    line += f"  ({badge}{disc}% off)"
                if link:
                    line += f"\n   👉 {link}"
                lines.append(line)
            lines.append("\n💡 Seedha naam type karo ya link bhejo — aur info paao!")
            await bot.send_message(chat_id=user_id, text="\n".join(lines),
                                   disable_web_page_preview=True)
            sent += 1
        except Exception as e:
            logger.warning("Digest failed for user %s: %s", user_id, e)
            failed += 1
        await asyncio.sleep(0.15)
    logger.info("%s digest complete — sent: %d, failed: %d", label, sent, failed)


async def send_morning_digest(bot):
    await _run_digest_slot(bot, DIGEST_SLOTS[0])


async def send_afternoon_digest(bot):
    await _run_digest_slot(bot, DIGEST_SLOTS[1])


async def send_evening_digest(bot):
    await _run_digest_slot(bot, DIGEST_SLOTS[2])


async def check_lightning_deals(bot):
    logger.info("Checking lightning deals...")
    try:
        keywords_list = ["fashion deals today", "beauty deals", "clothing sale"]
        for keywords in keywords_list:
            deals = await asyncio.to_thread(api.get_lightning_deals, keywords, 10)
            for deal in deals:
                asin = deal.get("asin", "")
                if not asin:
                    continue
                already_posted = await asyncio.to_thread(db.was_posted_recently, asin, 4)
                if already_posted:
                    continue
                await cp.post_lightning_deal(bot, deal)
                await asyncio.sleep(2)
    except Exception as e:
        logger.error("Lightning deals check error: %s", e)


async def send_wishlist_updates(bot):
    logger.info("Running weekly wishlist check...")
    try:
        asins = await asyncio.to_thread(db.get_all_wishlist_asins)
        for asin in asins:
            try:
                info = await asyncio.to_thread(api.get_product_info, asin)
                new_price = info.get("price_amount", 0)
                new_price_str = info.get("price", "")
                if not new_price:
                    continue
                wishlist_entries = await asyncio.to_thread(db.get_wishlist_users_for_asin, asin)
                for entry in wishlist_entries:
                    old_price = entry.get("price_amount", 0)
                    if old_price and new_price < old_price:
                        save = round(old_price - new_price, 2)
                        title = (entry.get("product_title") or info.get("title", "Product"))[:55]
                        link = entry.get("affiliate_link") or api.build_affiliate_link(asin)
                        text = (
                            f"🎉 Teri wishlist mein acchi khabar!\n\n"
                            f"📦 {title}\n"
                            f"📉 Pehle: ₹{old_price:,.0f}\n"
                            f"💰 Ab: {new_price_str}  (save ₹{save:,.0f}!)\n\n"
                            f"🛒 {link}"
                        )
                        try:
                            await bot.send_message(chat_id=entry["user_id"], text=text,
                                                   disable_web_page_preview=True)
                        except Exception as e:
                            logger.warning("Wishlist notify failed for user %s: %s", entry["user_id"], e)
                await asyncio.to_thread(db.update_wishlist_price, asin, new_price_str, new_price)
                await asyncio.sleep(1)
            except Exception as e:
                logger.error("Wishlist check error for %s: %s", asin, e)
    except Exception as e:
        logger.error("Weekly wishlist update error: %s", e)


def start_scheduler(bot) -> AsyncIOScheduler:
    # FIX: Use Asia/Kolkata so cron times match IST directly
    scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")
    scheduler.add_job(check_prices, "interval", hours=6, args=[bot], id="price_check")
    scheduler.add_job(send_morning_digest, "cron", hour=3, minute=30, args=[bot], id="morning_digest")
    scheduler.add_job(send_afternoon_digest, "cron", hour=6, minute=30, args=[bot], id="afternoon_digest")
    scheduler.add_job(send_evening_digest, "cron", hour=17, minute=0, args=[bot], id="evening_digest")
    scheduler.add_job(check_lightning_deals, "interval", hours=2, args=[bot], id="lightning_deals")
    scheduler.add_job(send_wishlist_updates, "cron", day_of_week="sun", hour=4, minute=30,
                      args=[bot], id="wishlist_updates")
    scheduler.start()
    logger.info("Scheduler started — 6 jobs active (Asia/Kolkata timezone)")
    return scheduler

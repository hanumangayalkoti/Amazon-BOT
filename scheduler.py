import asyncio
import logging
from datetime import datetime, timezone, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

import amazon_api as api
import database as db
import channel_poster as cp
import admin as adm

logger = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))

DEDUP_DAYS = 15


async def post_hourly_deals(bot):
    """
    Core hourly job: fetch best scored deals, deduplicate (15 days),
    and post photo cards to channels + digest users.
    Runs every hour from 10am to 10pm IST.
    """
    now_ist = datetime.now(IST)
    logger.info("Hourly deals job started — %s IST", now_ist.strftime("%d %b %Y %I:%M %p"))

    try:
        pool = await asyncio.to_thread(api.get_best_deals_scored, count=20)
    except Exception as e:
        logger.error("get_best_deals_scored failed: %s", e)
        return

    if not pool:
        logger.warning("No scored deals returned from API")
        return

    fresh_deals = []
    for deal in pool:
        asin = deal.get("asin", "")
        if not asin:
            continue
        already_posted = await asyncio.to_thread(db.was_posted_in_days, asin, DEDUP_DAYS)
        if already_posted:
            logger.debug("Skipping ASIN %s — posted within last %d days", asin, DEDUP_DAYS)
            continue
        fresh_deals.append(deal)
        if len(fresh_deals) >= 5:
            break

    if not fresh_deals:
        logger.info("All top deals already posted in last %d days — skipping this hour", DEDUP_DAYS)
        return

    logger.info("Posting %d fresh deal cards", len(fresh_deals))
    await cp.post_deals_to_all(bot, fresh_deals, category="best_deals")


async def check_prices(bot):
    """Price alert checker — runs every 6 hours."""
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

            alerts = await asyncio.to_thread(db.get_alerts_for_asin, asin)
            for alert in alerts:
                tracked = alert["tracked_price"]
                alert_type = alert.get("alert_type", "price")
                drop_pct = alert.get("drop_percent")
                if not new_price:
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

                    await asyncio.to_thread(db.update_alert_tracked_price, alert["id"], new_price)
                    await asyncio.to_thread(db.mark_alert_notified, alert["id"])
                else:
                    await asyncio.to_thread(db.update_alert_current_price, alert["id"], new_price)

            await asyncio.sleep(1)
        except Exception as e:
            logger.error("Error checking ASIN %s: %s", asin, e)
    logger.info("Price check complete")


async def check_lightning_deals(bot):
    """Check and post lightning deals every 2 hours."""
    logger.info("Checking lightning deals...")
    try:
        keywords_list = ["fashion deals today", "beauty deals", "electronics sale"]
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
    """Weekly wishlist price-drop notifications."""
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


async def send_weekly_admin_report(bot):
    """Send weekly performance report to admin every Sunday at 9pm IST."""
    logger.info("Sending weekly admin report...")
    try:
        await adm.send_weekly_report(bot)
    except Exception as e:
        logger.error("Weekly admin report error: %s", e)


def start_scheduler(bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")

    for hour in range(10, 23):
        scheduler.add_job(
            post_hourly_deals, "cron",
            hour=hour, minute=0,
            args=[bot],
            id=f"hourly_deals_{hour}",
        )

    scheduler.add_job(check_prices, "interval", hours=6, args=[bot], id="price_check")
    scheduler.add_job(check_lightning_deals, "interval", hours=2, args=[bot], id="lightning_deals")
    scheduler.add_job(send_wishlist_updates, "cron",
                      day_of_week="sun", hour=4, minute=30,
                      args=[bot], id="wishlist_updates")
    scheduler.add_job(send_weekly_admin_report, "cron",
                      day_of_week="sun", hour=21, minute=0,
                      args=[bot], id="weekly_admin_report")

    scheduler.start()
    logger.info(
        "Scheduler started — hourly deals 10am-10pm IST (13 jobs), "
        "price check 6h, lightning 2h, weekly report Sunday 9pm"
    )
    return scheduler


async def send_morning_digest(bot):
    """Legacy alias — triggers hourly deals post immediately."""
    await post_hourly_deals(bot)


async def send_afternoon_digest(bot):
    """Legacy alias — triggers hourly deals post immediately."""
    await post_hourly_deals(bot)


async def send_evening_digest(bot):
    """Legacy alias — triggers hourly deals post immediately."""
    await post_hourly_deals(bot)

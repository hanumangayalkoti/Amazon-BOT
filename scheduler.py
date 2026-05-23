import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

import amazon_api as api
import database as db

logger = logging.getLogger(__name__)


async def check_prices(bot):
    asins = await asyncio.to_thread(db.get_all_tracked_asins)
    if not asins:
        return

    logger.info("Price check started — %d ASINs", len(asins))

    for asin in asins:
        try:
            info = await asyncio.to_thread(api.get_product_info, asin)
            new_price = info.get("price_amount")

            if new_price:
                await asyncio.to_thread(db.save_price_snapshot, asin, new_price)

            alerts = await asyncio.to_thread(db.get_alerts_for_asin, asin)

            for alert in alerts:
                tracked = alert["tracked_price"]
                if new_price and new_price < tracked:
                    save = round(tracked - new_price, 2)
                    title = alert["product_title"] or info.get("title", "Product")
                    title_short = title[:60] + "…" if len(title) > 60 else title
                    link = alert["affiliate_link"] or api.build_affiliate_link(asin)

                    text = (
                        f"🔔 <b>Price Drop Alert!</b>\n\n"
                        f"📦 {title_short}\n"
                        f"📉 Was: ₹{tracked:,.0f}\n"
                        f"💰 <b>Now: {info.get('price', f'₹{new_price:,.0f}')}</b>"
                        f"  (save ₹{save:,.0f}!)\n\n"
                        f'<a href="{link}">🛒 Buy Now — Best Price!</a>'
                    )
                    try:
                        await bot.send_message(
                            chat_id=alert["user_id"],
                            text=text,
                            parse_mode="HTML",
                            disable_web_page_preview=True,
                        )
                    except Exception as e:
                        logger.warning("Could not notify user %s: %s", alert["user_id"], e)

                    await asyncio.to_thread(db.update_alert_price, alert["id"], new_price)

            await asyncio.sleep(0.5)

        except Exception as e:
            logger.error("Error checking ASIN %s: %s", asin, e)

    logger.info("Price check complete")


def start_scheduler(bot):
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        check_prices,
        "interval",
        hours=6,
        args=[bot],
        id="price_check",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler started — price check every 6 hours")
    return scheduler

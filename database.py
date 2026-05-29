import os
import threading
import psycopg2
import psycopg2.pool
import psycopg2.extras
from contextlib import contextmanager

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise SystemExit("FATAL: DATABASE_URL environment variable is not set.")

_pool: psycopg2.pool.ThreadedConnectionPool | None = None
_pool_lock = threading.Lock()


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                ssl = os.environ.get("DB_SSLMODE", "require")
                # FIX: Pool size configurable via env vars to prevent exhaustion
                min_conn = int(os.environ.get("DB_POOL_MIN", 2))
                max_conn = int(os.environ.get("DB_POOL_MAX", 15))
                _pool = psycopg2.pool.ThreadedConnectionPool(
                    minconn=min_conn, maxconn=max_conn,
                    dsn=DATABASE_URL, sslmode=ssl,
                )
    return _pool


@contextmanager
def get_conn():
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def init_db():
    _get_pool()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id     BIGINT PRIMARY KEY,
                    username    TEXT,
                    first_name  TEXT,
                    last_name   TEXT,
                    joined_at   TIMESTAMP DEFAULT NOW(),
                    last_seen   TIMESTAMP
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS price_alerts (
                    id              SERIAL PRIMARY KEY,
                    user_id         BIGINT,
                    asin            TEXT,
                    product_title   TEXT,
                    tracked_price   REAL,
                    current_price   REAL,
                    affiliate_link  TEXT,
                    created_at      TIMESTAMP DEFAULT NOW(),
                    notified        BOOLEAN DEFAULT FALSE,
                    alert_type      TEXT DEFAULT 'price',
                    drop_percent    REAL,
                    last_checked    TIMESTAMP,
                    UNIQUE(user_id, asin)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS price_history (
                    id          SERIAL PRIMARY KEY,
                    asin        TEXT,
                    price       REAL,
                    checked_at  TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ph_asin_time ON price_history(asin, checked_at DESC)")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS link_clicks (
                    id          SERIAL PRIMARY KEY,
                    user_id     BIGINT,
                    asin        TEXT,
                    clicked_at  TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS wishlist (
                    id              SERIAL PRIMARY KEY,
                    user_id         BIGINT,
                    asin            TEXT,
                    product_title   TEXT,
                    price           TEXT,
                    price_amount    REAL DEFAULT 0,
                    image_url       TEXT,
                    affiliate_link  TEXT,
                    added_at        TIMESTAMP DEFAULT NOW(),
                    UNIQUE(user_id, asin)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_preferences (
                    user_id         BIGINT PRIMARY KEY,
                    categories      TEXT[],
                    digest_enabled  BOOLEAN DEFAULT TRUE,
                    updated_at      TIMESTAMP DEFAULT NOW()
                )
            """)
            # FIX: Added UNIQUE(channel_id) so ON CONFLICT actually works
            cur.execute("""
                CREATE TABLE IF NOT EXISTS channel_config (
                    id          SERIAL PRIMARY KEY,
                    channel_id  TEXT NOT NULL UNIQUE,
                    label       TEXT,
                    added_at    TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS channel_posts (
                    id          SERIAL PRIMARY KEY,
                    asin        TEXT,
                    post_type   TEXT,
                    posted_at   TIMESTAMP DEFAULT NOW(),
                    message_id  BIGINT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS budget_alerts (
                    id          SERIAL PRIMARY KEY,
                    user_id     BIGINT,
                    query       TEXT,
                    max_price   INTEGER,
                    min_rating  REAL DEFAULT 0,
                    active      BOOLEAN DEFAULT TRUE,
                    created_at  TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS referrals (
                    id              SERIAL PRIMARY KEY,
                    referrer_id     BIGINT,
                    referred_id     BIGINT UNIQUE,
                    joined_at       TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_alerts_user ON price_alerts(user_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_wishlist_user ON wishlist(user_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_clicks_user ON link_clicks(user_id)")
            cur.execute("ALTER TABLE price_alerts ADD COLUMN IF NOT EXISTS alert_type TEXT DEFAULT 'price'")
            cur.execute("ALTER TABLE price_alerts ADD COLUMN IF NOT EXISTS drop_percent REAL")
            cur.execute("ALTER TABLE price_alerts ADD COLUMN IF NOT EXISTS last_checked TIMESTAMP")
            cur.execute("ALTER TABLE price_alerts ADD COLUMN IF NOT EXISTS notified BOOLEAN DEFAULT FALSE")
            cur.execute("ALTER TABLE wishlist ADD COLUMN IF NOT EXISTS price_amount REAL DEFAULT 0")
            # FIX: Add UNIQUE constraint to channel_config if not already present
            cur.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'channel_config_channel_id_key'
                    ) THEN
                        ALTER TABLE channel_config ADD CONSTRAINT channel_config_channel_id_key UNIQUE (channel_id);
                    END IF;
                EXCEPTION WHEN others THEN
                    NULL;
                END $$;
            """)


def upsert_user(user_id, username, first_name, last_name):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO users (user_id, username, first_name, last_name, joined_at, last_seen)
                VALUES (%s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (user_id) DO UPDATE SET
                    username   = EXCLUDED.username,
                    first_name = EXCLUDED.first_name,
                    last_name  = EXCLUDED.last_name,
                    last_seen  = NOW()
                RETURNING (xmax = 0) AS is_new
            """, (user_id, username, first_name, last_name))
            row = cur.fetchone()
    return row[0] if row else False


def get_user_count():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM users")
            return cur.fetchone()[0]


get_user_count_total = get_user_count


def get_all_user_ids():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM users")
            return [r[0] for r in cur.fetchall()]


def get_active_user_ids(days: int = 30):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id FROM users WHERE last_seen > NOW() - (%s * INTERVAL '1 day')",
                (days,)
            )
            return [r[0] for r in cur.fetchall()]


def get_users_paginated(offset: int = 0, limit: int = 10):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT user_id, username, first_name FROM users ORDER BY joined_at DESC LIMIT %s OFFSET %s",
                (limit, offset)
            )
            return cur.fetchall()


def add_price_alert(user_id, asin, product_title, current_price, affiliate_link,
                    alert_type='price', drop_percent=None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO price_alerts
                    (user_id, asin, product_title, tracked_price, current_price,
                     affiliate_link, alert_type, drop_percent, notified)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, FALSE)
                ON CONFLICT (user_id, asin) DO UPDATE SET
                    tracked_price  = EXCLUDED.tracked_price,
                    current_price  = EXCLUDED.current_price,
                    product_title  = EXCLUDED.product_title,
                    affiliate_link = EXCLUDED.affiliate_link,
                    alert_type     = EXCLUDED.alert_type,
                    drop_percent   = EXCLUDED.drop_percent,
                    notified       = FALSE
            """, (user_id, asin, product_title, current_price, current_price,
                  affiliate_link, alert_type, drop_percent))


def get_user_alerts(user_id):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM price_alerts WHERE user_id = %s ORDER BY created_at DESC",
                (user_id,)
            )
            return cur.fetchall()


def remove_alert(alert_id, user_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM price_alerts WHERE id = %s AND user_id = %s", (alert_id, user_id))


def get_all_tracked_asins():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT asin FROM price_alerts WHERE notified = FALSE")
            return [r[0] for r in cur.fetchall()]


# FIX: Only return alerts that haven't been notified yet — prevents re-firing
def get_alerts_for_asin(asin):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM price_alerts WHERE asin = %s AND notified = FALSE",
                (asin,)
            )
            return cur.fetchall()


# FIX: Only update current_price for regular price tracking (does NOT touch tracked_price)
def update_alert_current_price(alert_id, new_price):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE price_alerts SET current_price = %s, last_checked = NOW() WHERE id = %s",
                (new_price, alert_id)
            )


# FIX: Called when an alert actually fires — updates BOTH tracked and current price
# so the user's new baseline is reset to the drop price
def update_alert_tracked_price(alert_id, new_price):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE price_alerts SET current_price = %s, tracked_price = %s, last_checked = NOW() WHERE id = %s",
                (new_price, new_price, alert_id)
            )


# Legacy alias kept for backward compatibility — use update_alert_current_price instead
def update_alert_price(alert_id, new_price):
    update_alert_current_price(alert_id, new_price)


def mark_alert_notified(alert_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE price_alerts SET notified = TRUE WHERE id = %s", (alert_id,))


def save_price_snapshot(asin, price):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO price_history (asin, price) VALUES (%s, %s)", (asin, price))


def get_price_history(asin, days=7):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT price, checked_at FROM price_history
                WHERE asin = %s AND checked_at > NOW() - (%s * INTERVAL '1 day')
                ORDER BY checked_at DESC LIMIT 14
            """, (asin, days))
            return cur.fetchall()


def log_click(user_id, asin):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO link_clicks (user_id, asin) VALUES (%s, %s)", (user_id, asin))


def add_to_wishlist(user_id, asin, product_title, price, image_url, affiliate_link, price_amount=0):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO wishlist
                    (user_id, asin, product_title, price, image_url, affiliate_link, price_amount)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id, asin) DO NOTHING
                RETURNING id
            """, (user_id, asin, product_title, price, image_url, affiliate_link, price_amount))
            row = cur.fetchone()
    return row is not None


def get_wishlist(user_id):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM wishlist WHERE user_id = %s ORDER BY added_at DESC",
                (user_id,)
            )
            return cur.fetchall()


def remove_from_wishlist(item_id, user_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM wishlist WHERE id = %s AND user_id = %s", (item_id, user_id))


def update_wishlist_price(asin, new_price_str, new_price_amount):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE wishlist SET price = %s, price_amount = %s WHERE asin = %s",
                (new_price_str, new_price_amount, asin)
            )


def get_all_wishlist_asins():
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT DISTINCT asin FROM wishlist")
            return [r["asin"] for r in cur.fetchall()]


def get_wishlist_users_for_asin(asin):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT user_id, price_amount, affiliate_link, product_title FROM wishlist WHERE asin = %s",
                (asin,)
            )
            return cur.fetchall()


def get_user_preferences(user_id: int) -> dict:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM user_preferences WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
    if row:
        return dict(row)
    return {"user_id": user_id, "categories": ["Fashion", "Beauty", "Electronics"],
            "digest_enabled": True}


def set_user_preferences(user_id: int, categories: list, digest_enabled: bool):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO user_preferences (user_id, categories, digest_enabled, updated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (user_id) DO UPDATE SET
                    categories     = EXCLUDED.categories,
                    digest_enabled = EXCLUDED.digest_enabled,
                    updated_at     = NOW()
            """, (user_id, categories, digest_enabled))


def get_users_with_digest_enabled():
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT u.user_id, COALESCE(p.categories, ARRAY['Fashion','Beauty','Electronics']) AS categories
                FROM users u
                LEFT JOIN user_preferences p ON u.user_id = p.user_id
                WHERE COALESCE(p.digest_enabled, TRUE) = TRUE
            """)
            return cur.fetchall()


def get_channel_ids():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT channel_id FROM channel_config ORDER BY added_at")
            return [r[0] for r in cur.fetchall()]


# FIX: ON CONFLICT now works because channel_id has UNIQUE constraint
def add_channel(channel_id: str, label: str = ""):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO channel_config (channel_id, label) VALUES (%s, %s) ON CONFLICT (channel_id) DO NOTHING",
                (channel_id, label)
            )


def remove_channel(channel_id: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM channel_config WHERE channel_id = %s", (channel_id,))


def log_channel_post(asin: str, post_type: str, message_id: int = 0):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO channel_posts (asin, post_type, message_id) VALUES (%s, %s, %s)",
                (asin, post_type, message_id)
            )


def was_posted_recently(asin: str, hours: int = 24) -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 1 FROM channel_posts
                WHERE asin = %s AND posted_at > NOW() - (%s * INTERVAL '1 hour')
                LIMIT 1
            """, (asin, hours))
            return cur.fetchone() is not None


def add_budget_alert(user_id: int, query: str, max_price: int, min_rating: float = 0):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO budget_alerts (user_id, query, max_price, min_rating)
                VALUES (%s, %s, %s, %s)
            """, (user_id, query, max_price, min_rating))


def get_active_budget_alerts():
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM budget_alerts WHERE active = TRUE")
            return cur.fetchall()


def get_user_budget_alerts(user_id: int):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM budget_alerts WHERE user_id = %s AND active = TRUE", (user_id,))
            return cur.fetchall()


def remove_budget_alert(alert_id: int, user_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE budget_alerts SET active = FALSE WHERE id = %s AND user_id = %s",
                        (alert_id, user_id))


def add_referral(referrer_id: int, referred_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO referrals (referrer_id, referred_id)
                VALUES (%s, %s)
                ON CONFLICT (referred_id) DO NOTHING
            """, (referrer_id, referred_id))


def get_referral_count(referrer_id: int) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id = %s", (referrer_id,))
            return cur.fetchone()[0]


def get_stats():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM users"); total_users = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM users WHERE joined_at > NOW() - INTERVAL '30 days'"); month_users = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM users WHERE joined_at > NOW() - INTERVAL '1 day'"); today_users = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM users WHERE last_seen > NOW() - INTERVAL '30 days'"); active_users = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM link_clicks"); total_clicks = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM link_clicks WHERE clicked_at > NOW() - INTERVAL '30 days'"); month_clicks = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM link_clicks WHERE clicked_at > NOW() - INTERVAL '1 day'"); today_clicks = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM price_alerts"); total_alerts = cur.fetchone()[0]
            cur.execute("SELECT COUNT(DISTINCT user_id) FROM price_alerts"); users_tracking = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM channel_posts WHERE posted_at > NOW() - INTERVAL '1 day'"); posts_today = cur.fetchone()[0]
    return {
        "total_users": total_users, "month_users": month_users,
        "today_users": today_users, "active_users": active_users,
        "total_clicks": total_clicks, "month_clicks": month_clicks,
        "today_clicks": today_clicks, "total_alerts": total_alerts,
        "users_tracking": users_tracking, "posts_today": posts_today,
    }


def get_top_asins(limit=5):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT asin, product_title, COUNT(*) AS cnt FROM price_alerts
                GROUP BY asin, product_title ORDER BY cnt DESC LIMIT %s
            """, (limit,))
            return cur.fetchall()


def get_recent_users(limit=10):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT user_id, username, first_name, last_name, joined_at FROM users ORDER BY joined_at DESC LIMIT %s",
                (limit,)
            )
            return cur.fetchall()

import os
import psycopg2
import psycopg2.pool
import psycopg2.extras
from contextlib import contextmanager

DATABASE_URL = os.environ["DATABASE_URL"]

# B1 — ThreadedConnectionPool replaces per-call connections
_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        ssl   = os.environ.get("DB_SSLMODE", "require")
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=2,
            maxconn=10,
            dsn=DATABASE_URL,
            sslmode=ssl,
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
    _get_pool()  # warm up pool on startup
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
                    UNIQUE(user_id, asin)
                )
            """)
            # B7 — notified flag to track whether alert has fired
            cur.execute("""
                ALTER TABLE price_alerts
                ADD COLUMN IF NOT EXISTS notified BOOLEAN DEFAULT FALSE
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS price_history (
                    id          SERIAL PRIMARY KEY,
                    asin        TEXT,
                    price       REAL,
                    checked_at  TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_ph_asin_time
                ON price_history(asin, checked_at DESC)
            """)
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
                    image_url       TEXT,
                    affiliate_link  TEXT,
                    added_at        TIMESTAMP DEFAULT NOW(),
                    UNIQUE(user_id, asin)
                )
            """)
            # Section C — performance indexes
            cur.execute("CREATE INDEX IF NOT EXISTS idx_alerts_user ON price_alerts(user_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_wishlist_user ON wishlist(user_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_clicks_user ON link_clicks(user_id)")


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


# A6 — alias used by broadcast helpers
get_user_count_total = get_user_count


def get_all_user_ids():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM users")
            return [r[0] for r in cur.fetchall()]


# A6 — paginated user list for broadcast select-user flow
def get_users_paginated(offset: int = 0, limit: int = 10):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT user_id, username, first_name FROM users "
                "ORDER BY joined_at DESC LIMIT %s OFFSET %s",
                (limit, offset)
            )
            return cur.fetchall()


def add_price_alert(user_id, asin, product_title, current_price, affiliate_link):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO price_alerts
                    (user_id, asin, product_title, tracked_price, current_price, affiliate_link)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id, asin) DO UPDATE SET
                    tracked_price  = EXCLUDED.tracked_price,
                    current_price  = EXCLUDED.current_price,
                    product_title  = EXCLUDED.product_title,
                    affiliate_link = EXCLUDED.affiliate_link,
                    notified       = FALSE
            """, (user_id, asin, product_title, current_price, current_price, affiliate_link))


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
            cur.execute(
                "DELETE FROM price_alerts WHERE id = %s AND user_id = %s",
                (alert_id, user_id)
            )


def get_all_tracked_asins():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT asin FROM price_alerts")
            return [r[0] for r in cur.fetchall()]


def get_alerts_for_asin(asin):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM price_alerts WHERE asin = %s", (asin,))
            return cur.fetchall()


def update_alert_price(alert_id, new_price):
    # B7 — update BOTH current_price AND tracked_price so the alert only fires
    # on FURTHER drops below the new level, not the same price again
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE price_alerts SET current_price = %s, tracked_price = %s WHERE id = %s",
                (new_price, new_price, alert_id)
            )


# B7 — mark alert as notified (optional suppress duplicate same-price alerts)
def mark_alert_notified(alert_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE price_alerts SET notified = TRUE WHERE id = %s",
                (alert_id,)
            )


def save_price_snapshot(asin, price):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO price_history (asin, price) VALUES (%s, %s)",
                (asin, price)
            )


# B3 — SQL injection fix: use interval multiplication, not string substitution
def get_price_history(asin, days=7):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT price, checked_at FROM price_history
                WHERE asin = %s
                  AND checked_at > NOW() - (%s * INTERVAL '1 day')
                ORDER BY checked_at DESC LIMIT 14
            """, (asin, days))
            return cur.fetchall()


def log_click(user_id, asin):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO link_clicks (user_id, asin) VALUES (%s, %s)",
                (user_id, asin)
            )


def add_to_wishlist(user_id, asin, product_title, price, image_url, affiliate_link):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO wishlist
                    (user_id, asin, product_title, price, image_url, affiliate_link)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id, asin) DO NOTHING
                RETURNING id
            """, (user_id, asin, product_title, price, image_url, affiliate_link))
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
            cur.execute(
                "DELETE FROM wishlist WHERE id = %s AND user_id = %s",
                (item_id, user_id)
            )


def get_stats():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM users")
            total_users = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM users WHERE joined_at > NOW() - INTERVAL '30 days'")
            month_users = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM users WHERE joined_at > NOW() - INTERVAL '1 day'")
            today_users = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM link_clicks")
            total_clicks = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM link_clicks WHERE clicked_at > NOW() - INTERVAL '30 days'")
            month_clicks = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM link_clicks WHERE clicked_at > NOW() - INTERVAL '1 day'")
            today_clicks = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM price_alerts")
            total_alerts = cur.fetchone()[0]
            cur.execute("SELECT COUNT(DISTINCT user_id) FROM price_alerts")
            users_tracking = cur.fetchone()[0]
    return {
        "total_users":    total_users,
        "month_users":    month_users,
        "today_users":    today_users,
        "total_clicks":   total_clicks,
        "month_clicks":   month_clicks,
        "today_clicks":   today_clicks,
        "total_alerts":   total_alerts,
        "users_tracking": users_tracking,
    }


def get_top_asins(limit=5):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT asin, product_title, COUNT(*) AS cnt
                FROM price_alerts
                GROUP BY asin, product_title
                ORDER BY cnt DESC
                LIMIT %s
            """, (limit,))
            return cur.fetchall()


def get_recent_users(limit=10):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT user_id, username, first_name, last_name, joined_at "
                "FROM users ORDER BY joined_at DESC LIMIT %s",
                (limit,)
            )
            return cur.fetchall()

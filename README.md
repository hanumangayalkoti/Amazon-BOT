# Shopping GPT — Amazon Affiliate Telegram Bot

A professional, public-facing Amazon India Affiliate Telegram Bot that lets users send any Amazon link, ASIN, or natural language query and returns clean product info with inline buttons, price alerts, wishlist, compare, and an AI assistant named Simi.

**Affiliate Tag:** `shoppinggpt-21`
**Marketplace:** `www.amazon.in`
**Deployment:** Railway (auto-deploy from GitHub push)

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11+ |
| Telegram | `python-telegram-bot` v21 (async) |
| Amazon API | Amazon Creators API v3.2 (OAuth2, direct `requests`) |
| AI | OpenAI `gpt-4o-mini` / `gpt-4.1-mini` via `openai` Python SDK |
| Database | **PostgreSQL** via `psycopg2` (ThreadedConnectionPool) |
| Scheduler | `APScheduler` (price check every 6 hours) |
| Deployment | Railway |

> **Important:** The original README said SQLite, but the actual code uses **PostgreSQL**. `DATABASE_URL` must point to a live Postgres instance (Railway provides one as a plugin).

---

## File Structure

```
amazon-bot/
├── bot.py              # Main bot — all handlers, commands, conversation flow
├── amazon_api.py       # Amazon Creators API — GetItems + SearchItems + ASIN extract
├── ai_handler.py       # OpenAI — intent detection, Simi assistant
├── database.py         # PostgreSQL — users, price_alerts, wishlist, price_history, link_clicks
├── scheduler.py        # APScheduler — price check every 6 hours, send drop alerts
├── admin.py            # Admin-only command handlers
├── requirements.txt    # All pip dependencies
└── README.md           # This file
```

---

## Environment Variables (set in Railway — NEVER hardcode)

| Variable | Description | Required |
|---|---|---|
| `BOT_TOKEN` | Telegram Bot Token from @BotFather | YES |
| `ADMIN_CHAT_ID` | Owner's Telegram numeric ID (get from @userinfobot) | YES |
| `DATABASE_URL` | PostgreSQL connection string | YES |
| `CREDENTIAL_ID` | Amazon Creators API Client ID | YES |
| `CREDENTIAL_SECRET` | Amazon Creators API Client Secret | YES |
| `OPENAI_API_KEY` | OpenAI API Key | YES (AI disabled if missing, bot still works) |
| `CREDENTIAL_VERSION` | `3.2` (default) | Optional |
| `PARTNER_TAG` | `shoppinggpt-21` (default) | Optional |
| `MARKETPLACE` | `www.amazon.in` (default) | Optional |
| `DB_SSLMODE` | `require` (default) | Optional |

If any required variable is missing, the bot prints a clear `FATAL:` message and exits cleanly instead of crashing with a cryptic `KeyError`. `OPENAI_API_KEY` is optional — if missing, Simi and intent detection are disabled with a warning, but all product/search features still work.

---

## Database Schema (PostgreSQL)

Tables are created automatically on first bot start via `db.init_db()`. Schema:

```sql
CREATE TABLE users (
    user_id     BIGINT PRIMARY KEY,
    username    TEXT,
    first_name  TEXT,
    last_name   TEXT,
    joined_at   TIMESTAMP DEFAULT NOW(),
    last_seen   TIMESTAMP
);

CREATE TABLE price_alerts (
    id              SERIAL PRIMARY KEY,
    user_id         BIGINT,
    asin            TEXT,
    product_title   TEXT,
    tracked_price   REAL,
    current_price   REAL,
    affiliate_link  TEXT,
    notified        BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, asin)
);

CREATE TABLE price_history (
    id          SERIAL PRIMARY KEY,
    asin        TEXT,
    price       REAL,
    checked_at  TIMESTAMP DEFAULT NOW()
);

CREATE TABLE link_clicks (
    id          SERIAL PRIMARY KEY,
    user_id     BIGINT,
    asin        TEXT,
    clicked_at  TIMESTAMP DEFAULT NOW()
);

CREATE TABLE wishlist (
    id              SERIAL PRIMARY KEY,
    user_id         BIGINT,
    asin            TEXT,
    product_title   TEXT,
    price           TEXT,
    image_url       TEXT,
    affiliate_link  TEXT,
    added_at        TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, asin)
);
```

---

## Commands

### User Commands

| Command | Behavior |
|---|---|
| `/start` | Welcome message. Saves user to DB. Sends admin notification. |
| `/help` | Full guide — all commands with examples |
| `/search` | Bot prompts user to type query → returns 5 search results |
| `/compare` | Step-by-step: bot asks for link 1, then link 2, then auto-compares with Simi's Pick |
| `/track` | Guided: ask for product link → saves price alert |
| `/myalerts` | Paginated list of active alerts with Remove buttons |
| `/mywishlist` | Wishlist with Buy/Alert/Remove buttons |
| `/simi` | Activate Simi AI shopping assistant (Hinglish) |
| `/stop` | Deactivate all active modes (compare, search, simi, track) |

### Admin Commands (only work for `ADMIN_CHAT_ID`)

| Command | Shows |
|---|---|
| `/admin` | Full dashboard (users, clicks, alerts, top products) |
| `/users` | User stats (total, month, today) |
| `/clicks` / `/links` | Affiliate click stats |
| `/alerts` | Price alert stats |
| `/top` | Top 5 most tracked ASINs |
| `/recent` | Last 10 joined users with IST timestamps |
| `/ping` | Bot alive check |
| `/broadcast` | Smart broadcast: send to all or select specific users |
| `/backup` | DB snapshot summary |

---

## Price Alert System

1. User clicks `🔔 Price Alert` on any product card → alert saved with `tracked_price = current_price`
2. Scheduler runs every 6 hours:
   - Fetches all unique tracked ASINs from DB
   - For each alert: if `new_price < tracked_price` → sends Telegram notification to user
   - After notifying: updates `tracked_price = new_price` and sets `notified = TRUE`
   - Alert stays active permanently — fires again only on FURTHER price drop below the new level
3. Price snapshot saved every cycle for history chart (`/history_{ASIN}` button)

---

## Amazon API Details

- Auth: OAuth2 client_credentials flow (version 3.2 = LWA via `api.amazon.co.uk`)
- Token cached in memory with expiry; auto-refreshed 60s before expiry
- Token is **immediately invalidated on HTTP 403** so next call gets a fresh token
- ASIN extraction order: full 10-char string → `/dp/` URL → `amzn.to` short link (resolved via `requests.get`) → `?ASIN=` query param
- Short URL cache is LRU-capped at 500 entries to prevent memory leak
- Both `getItems` and `searchItems` endpoints supported

---

## Architecture Notes

- **Bot state** stored in `context.user_data` per user (compare flow, simi mode, waiting flags). No shared global state for user sessions.
- **DB pool** is `ThreadedConnectionPool` (min 2, max 10). Initialized once with double-checked locking via `threading.Lock`.
- **Scheduler** uses `AsyncIOScheduler` in the bot's asyncio event loop. The reference is stored in `_scheduler` at module level — APScheduler will silently stop if the object is garbage collected.
- **All timestamps** displayed to users use `IST = timezone(timedelta(hours=5, minutes=30))` — no `pytz` dependency.
- **Admin guard** uses `str(update.effective_user.id) == ADMIN_CHAT_ID` (string comparison — both are strings).
- **`parse_mode`** is always `HTML` — never MarkdownV2 (too many escape issues).
- **Logging** uses Python's `logging` module throughout (`logger.info`, `logger.error`, etc.) — no `print()` calls in production code.

---

## Bug Fixes Applied (May 2026)

All original features and logic are unchanged. Only bugs were fixed.

### Critical Fixes

| Fix | File | What was wrong | What was fixed |
|---|---|---|---|
| FIX-1 | `database.py` | Missing `DATABASE_URL` → cryptic `KeyError` crash | Now prints `FATAL: DATABASE_URL not set.` and exits cleanly |
| FIX-2 | `ai_handler.py` | Missing `OPENAI_API_KEY` caused `raise ValueError` at **module import level** → entire bot crashed before starting | Changed to `logger.warning`; bot starts normally, AI functions return graceful fallbacks |
| FIX-3 | `bot.py` | Missing `BOT_TOKEN` / `ADMIN_CHAT_ID` → cryptic `KeyError` | Now prints `FATAL:` message and exits |
| FIX-3 | `amazon_api.py` | Missing `CREDENTIAL_ID` / `CREDENTIAL_SECRET` → `KeyError` | Now prints `FATAL:` message and exits |
| FIX-11 | `amazon_api.py` | On HTTP 403, stale/invalid token stayed cached → **every subsequent API call failed** until bot was manually restarted | Token is now cleared from cache immediately on 403 (both `get_product_info` and `search_items`) |

### Major Fixes

| Fix | File | What was wrong | What was fixed |
|---|---|---|---|
| FIX-9 | `database.py` | `_get_pool()` had no thread lock → race condition: multiple threads could simultaneously create duplicate pools on startup | Added `threading.Lock()` with double-checked locking pattern |
| FIX-8 | `scheduler.py` | `mark_alert_notified()` was defined in `database.py` and `notified` column existed in DB, but the scheduler **never called it** — dead code | Scheduler now calls `mark_alert_notified(alert_id)` after each successful price drop notification |
| FIX-6 | `amazon_api.py` | `_url_cache` was an unbounded plain `dict` — grew forever with every unique short URL → memory leak on long-running bots | Replaced with `OrderedDict` with max 500 entries; oldest entry evicted when limit reached (LRU) |
| FIX-10 | `ai_handler.py` | `print(f"[Simi Error] ...")` etc. used for error reporting → unstructured, hard to grep in Railway logs | Replaced all `print()` with `logger.error()` / `logger.warning()` |

### Minor Fixes

| Fix | File | What was wrong | What was fixed |
|---|---|---|---|
| FIX-13 | `amazon_api.py` | `ASIN_PATTERN` regex lacked `re.IGNORECASE` → Amazon URLs with lowercase ASIN characters were not matched | Added `re.IGNORECASE` flag |
| FIX-15 | `admin.py` | `/recent` command showed raw UTC timestamps from DB without IST conversion → admin saw wrong times | `joined_at` is now converted from UTC to IST before display |

---

## Deployment (Railway)

1. Push code to GitHub → Railway auto-deploys via `Procfile`:
   ```
   worker: python bot.py
   ```
2. Set all required environment variables in Railway Variables tab.
3. Attach a **PostgreSQL** plugin in Railway — it auto-sets `DATABASE_URL`.
4. Bot starts, creates all DB tables automatically via `db.init_db()`, begins polling.

---

## Running Locally

```bash
pip install -r requirements.txt

export BOT_TOKEN=your_token
export ADMIN_CHAT_ID=your_telegram_id
export DATABASE_URL=postgresql://user:pass@host:5432/dbname
export CREDENTIAL_ID=your_amazon_credential_id
export CREDENTIAL_SECRET=your_amazon_credential_secret
export OPENAI_API_KEY=your_openai_key  # optional

python bot.py
```

---

## Known Limitations

- **`bot.py` may appear truncated on GitHub** — the `handle_message` function is very long. Verify the full `compare_step == 2` block and `handle_callback` are intact in your local copy before deploying.
- Price history shows text-only (last 7 days, max 14 data points). A visual chart is not implemented.
- Broadcast to selected users works via paginated user picker, but for very large user bases (10k+) consider adding rate limiting between sends to avoid Telegram flood limits.

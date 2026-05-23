# Shopping GPT — Amazon Affiliate Telegram Bot
## Complete Project Blueprint (Read this fully before writing any code)

---

## 🎯 Project Goal

A **professional, public-facing** Amazon India Affiliate Telegram Bot that:
- Lets users send any Amazon link, ASIN, or natural language query
- Returns clean product info with inline buttons
- Has an AI assistant named **Simi** for shopping guidance
- Tracks price alerts for users
- Notifies admin on new user joins
- Has admin-only analytics commands

**Affiliate Tag:** `dealskoti-21`
**Marketplace:** `www.amazon.in`
**GitHub:** `https://github.com/hanumangayalkoti/Amazon-BOT`
**Deployment:** Railway (auto-deploy from GitHub push)

---

## 🧱 Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11+ |
| Telegram | `python-telegram-bot` v21 (async) |
| Amazon API | Amazon Creators API v3.2 (OAuth2, direct `requests` — no SDK) |
| AI | OpenAI `gpt-4o-mini` via `openai` Python SDK |
| Database | SQLite (Python built-in `sqlite3`) |
| Scheduler | `APScheduler` (background price check every 6 hours) |
| Deployment | Railway |

---

## 📁 File Structure

```
amazon-affiliate-bot/
├── bot.py              # Main bot — all handlers, commands, conversation flow
├── amazon_api.py       # Amazon Creators API — GetItems + SearchItems + ASIN extract
├── ai_handler.py       # OpenAI — intent detection, Simi assistant, language detection
├── database.py         # SQLite — users, price_alerts, link_clicks tables
├── scheduler.py        # APScheduler — price check every 6 hours, send drop alerts
├── admin.py            # Admin-only command handlers
├── requirements.txt    # All pip dependencies
└── README.md           # This file
```

---

## 🔐 Environment Variables (set in Railway — NEVER hardcode)

| Variable | Value |
|---|---|
| `BOT_TOKEN` | Telegram Bot Token from @BotFather |
| `CREDENTIAL_ID` | Amazon Creators API Client ID |
| `CREDENTIAL_SECRET` | Amazon Creators API Client Secret |
| `CREDENTIAL_VERSION` | `3.2` |
| `PARTNER_TAG` | `dealskoti-21` |
| `MARKETPLACE` | `www.amazon.in` |
| `OPENAI_API_KEY` | OpenAI API Key (paid account, gpt-4o-mini access confirmed) |
| `ADMIN_CHAT_ID` | Owner's Telegram numeric ID (get from @userinfobot) |

---

## 🗄️ Database Schema (SQLite — file: `bot_data.db`)

```sql
-- All users who ever used the bot
CREATE TABLE IF NOT EXISTS users (
    user_id     INTEGER PRIMARY KEY,
    username    TEXT,
    first_name  TEXT,
    last_name   TEXT,
    joined_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_seen   DATETIME
);

-- Active price alerts
CREATE TABLE IF NOT EXISTS price_alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER,
    asin            TEXT,
    product_title   TEXT,
    tracked_price   REAL,
    current_price   REAL,
    affiliate_link  TEXT,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, asin)
);

-- Every time a Buy Now button is clicked
CREATE TABLE IF NOT EXISTS link_clicks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER,
    asin        TEXT,
    clicked_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

---

## 📋 Commands — Full List

### 👤 User Commands

| Command | Behavior |
|---|---|
| `/start` | Welcome message. Saves user to DB. Sends silent admin notification. |
| `/help` | Clean formatted guide — all commands with examples |
| `/search` | Bot prompts user to type their query. User types naturally. Returns 5 results. |
| `/compare` | Step-by-step guided: bot asks for link 1, then link 2, then auto-compares |
| `/track` | Guided: bot asks for product link → saves price alert for that user |
| `/myalerts` | All active alerts with [❌ Remove] button on each |
| `/support` | Activates Simi — AI shopping assistant (shopping-only, polite) |

### 🔐 Admin Commands (ONLY works for ADMIN_CHAT_ID — silently ignore for others)

| Command | Shows |
|---|---|
| `/users` | Total users, this month, today |
| `/links` | Total affiliate link clicks, this month, today |
| `/alerts` | Total active price alerts, unique users tracking |
| `/top` | Top 5 most tracked ASINs |
| `/broadcast <message>` | Send message to ALL users in DB |

---

## 🧠 Intent Detection (OpenAI — every incoming message)

Every non-command message is classified by GPT before routing:

| Intent | Triggered by | Bot action |
|---|---|---|
| `product_link` | Amazon URL or bare ASIN | GetItems API → product card |
| `search_query` | "best headphones under 5000" etc | SearchItems API → 5 results |
| `support` | General shopping question | Simi responds |
| `off_topic` | Non-shopping question | Simi politely redirects |

**Intent detection prompt (send to gpt-4o-mini):**
```
Classify this user message into exactly one intent:
- "product_link": contains an Amazon URL or a standalone ASIN (10 chars, starts with B)
- "search_query": natural language product search or recommendation request
- "support": shopping advice, product questions, comparisons in words
- "off_topic": anything unrelated to shopping or Amazon products

Respond with JSON only: {"intent": "product_link|search_query|support|off_topic"}
Message: {user_message}
```

---

## 🤖 Simi — AI Shopping Assistant

**Activated by:** `/support` command  
**Also used for:** `support` and `off_topic` intents from regular messages

### Personality Rules (enforce via system prompt):
1. **Always polite** — warm, friendly, never rude or sarcastic
2. **Shopping-only** — only Amazon/shopping/product/deal topics
3. **Off-topic redirect** — use user's first name, warm tone:
   - *"Hi Rahul! Hum thoda off track chale gaye 😊 Main sirf shopping mein help kar sakti hoon — koi product dhundh raha hai ya koi deal check karni hai?"*
4. **Language adaptive:**
   - Default: Hinglish (Hindi + English mix)
   - Pure English message → respond in English
   - Pure Hindi message → respond in Hindi
   - User seems confused / says "I don't understand" → switch to simplest Hinglish
5. **Conversation memory:** last 10 messages stored in `context.user_data["simi_history"]`

### Simi System Prompt:
```
You are Simi, a friendly and helpful Amazon India shopping assistant inside a Telegram bot.

STRICT RULES:
1. ONLY answer questions related to Amazon shopping, products, deals, price comparisons, buying advice, and product recommendations.
2. If the user asks ANYTHING unrelated to shopping (writing essays, fixing things, coding, general knowledge, etc.), politely redirect them by their first name. Example: "Hi [name]! Hum thoda off track chale gaye 😊 Main sirf shopping mein help kar sakti hoon — koi product dhundha hai ya deal check karni hai?"
3. Always be warm, friendly, and polite. NEVER rude or dismissive.
4. Detect the user's language style and respond in the same (Hindi, English, or Hinglish).
5. If user seems confused, switch to simple Hinglish automatically.
6. Keep responses concise — no long paragraphs.
7. Do NOT make up product prices or live availability — you don't have real-time data. Tell them to search using the bot.

User's first name: {first_name}
```

---

## 🛍️ Product Card Format

```
[Product Image — sent as photo with caption below]

Brand — boAt
Product — boAt Rockerz 450 Bluetooth On Ear Headphones...
Category — Electronics

💰 Price — ₹1,299
🔖 Discount — 48% off (save ₹1,200)
✅ Stock — In Stock
⭐ Rating — ★★★★☆ 4.2/5  (45,230 reviews)
🛍️ 15K+ bought last month        ← ONLY if API provides this field, else skip entirely

[🛒 Buy Now]  [🔔 Price Alert]  [📋 Features]
```

**Key rules:**
- `📋 Features` = inline button ONLY — **not shown in card text**
- Clicking `📋 Features` → bot sends follow-up message with full bullet list
- Clicking `🔔 Price Alert` → saves alert, confirms to user
- Clicking `🛒 Buy Now` → opens `https://www.amazon.in/dp/{ASIN}?tag=dealskoti-21`
- "Bought last month" → show ONLY if data comes from API — never fake it
- No deal score in card — removed from design
- No affiliate tag line in card — embedded silently in Buy Now URL only
- Use `parse_mode="HTML"` always

---

## 🔍 Search Results Format (5 products)

Send each result as a **separate message** so each has its own inline buttons:

```
🔍 "best earbuds under 2000" ke results:

━━━━━━━━━━━━━━━━━━━━━━━
1️⃣ boAt Airdopes 141
   ⭐ 4.2  |  💰 ₹1,299  |  🔖 48% off
   [ 🛒 Buy ]  [ 🔔 Alert ]  [ 📋 Details ]

━━━━━━━━━━━━━━━━━━━━━━━
2️⃣ Noise Buds VS104
   ⭐ 4.0  |  💰 ₹1,499  |  🔖 40% off
   [ 🛒 Buy ]  [ 🔔 Alert ]  [ 📋 Details ]

... (up to 5)
```

Each result = one `reply_text` with its own `InlineKeyboardMarkup`.

---

## ⚖️ Compare Flow

```
User: /compare

Bot: "Chaliye 2 products compare karte hain! 🔍
      Pehle product ka Amazon link ya ASIN bhejo 👇"

User: [link 1]

Bot: "Perfect! ✅ [Product 1 name] mil gaya.
      Ab doosre product ka link bhejo 👇"

User: [link 2]

Bot: [Comparison card — see format below]
```

**State stored in `context.user_data`:**
```python
context.user_data["compare_step"] = 1 or 2
context.user_data["compare_asin1"] = "B0XXXXXXXX"
```

### Comparison Card Format:
```
⚖️ Comparison Result

                    Product 1          Product 2
🏷️ Brand            boAt               Sony
💰 Price            ₹1,299             ₹2,499
🔖 Discount         48% off            30% off
⭐ Rating           4.2/5              4.5/5
📦 Stock            In Stock           In Stock

📋 Key Difference:
boAt: 15hr battery, foldable, budget-friendly
Sony: 30hr battery, noise cancellation, premium

🤖 Simi's Pick: Sony for best sound. boAt for best value.

[ 🛒 Buy boAt ]   [ 🛒 Buy Sony ]
```

---

## 🔔 Price Alert System

### Setup flow:
1. User clicks `🔔 Price Alert` on product card → callback `alert_{ASIN}`
2. Bot saves: `user_id`, `asin`, `product_title`, `current_price`, `affiliate_link`
3. Confirms: *"🔔 Alert set! Jab bhi price giregi, main tujhe bataunga!"*

### Scheduler (every 6 hours):
```
1. Get all unique ASINs from price_alerts table
2. Call GetItems API for each ASIN
3. If new_price < tracked_price for a user:
   → Send alert notification to that user
   → Update current_price in DB
   → Keep alert active (don't delete)
4. If product unavailable:
   → Optionally notify user
```

### Alert Notification:
```
🔔 Price Drop Alert!

📦 boAt Rockerz 450
📉 Was: ₹1,799
💰 Now: ₹1,299  (save ₹500!)

[ 🛒 Buy Now — Best Price! ]
```

---

## 👑 Admin Notification on New User

Every time a user sends `/start`:
1. Save/update user in `users` table
2. Send **silent** notification to `ADMIN_CHAT_ID`:

```
👤 New User!

Name: Rahul Sharma
Username: @rahulsharma
User ID: 123456789
Time: 22 May 2026, 10:30 AM IST

Total Users: 1,248
```

`disable_notification=True` so admin's phone doesn't buzz.

---

## ⚠️ Error Handling Rules

1. **Zero raw Python errors** shown to user — ever
2. All errors caught with try/except and shown as friendly Hinglish:

| Situation | Message |
|---|---|
| Invalid link | *"Yeh link valid nahi laga — ek baar check karo ya doosra link try karo 😊"* |
| Product not found | *"Yeh product nahi mila — ho sakta hai link expire ho gaya ho. Koi aur product try karo!"* |
| Search no results | *"Is query ke liye koi result nahi mila — thoda alag wording try karo 😊"* |
| API timeout | *"Amazon server thoda busy hai, 1-2 minute mein dobara try karo 🙏"* |
| Rate limit | *"Thodi der baad try karo — bahut requests aa rahi hain abhi!"* |
| Generic | *"Kuch technical issue aa gaya 😅 Thodi der baad try karo. Problem bani rahe to /support mein Simi se baat karo!"* |
| Search page link | *"Yeh search page ka link hai — pehle kisi ek product pe click karo, phir us page ka link bhejo 😊"* |

---

## 🔑 Amazon Creators API (Direct requests — No SDK)

```python
# Auth
TOKEN_URL = "https://api.amazon.co.uk/auth/o2/token"
SCOPE = "creatorsapi::default"
BASE_URL = "https://creatorsapi.amazon.in"

# Endpoints
GET_ITEMS   = "/catalog/v1/getItems"
SEARCH      = "/catalog/v1/searchItems"
```

**Token management:**
- OAuth2 client_credentials flow
- Cache token + expiry time in module-level variable
- Auto-refresh when within 60s of expiry

**ASIN extraction logic (in order):**
1. Regex for `/dp/XXXXXXXXXX` or `/gp/product/XXXXXXXXXX` in URL
2. If `amzn.to` short link → follow redirect → extract from final URL
3. If standalone 10-char string starting with B → treat as ASIN
4. If `amazon.in/s?` or search pattern detected → return `error="search"`
5. Else → return `None`

---

## 📦 requirements.txt

```
python-telegram-bot==21.10
requests==2.32.3
openai==1.78.0
apscheduler==3.10.4
```

---

## ⚙️ Implementation Notes (Critical)

1. **`context.user_data`** — use for multi-step flows:
   ```python
   context.user_data["compare_step"]   # 1 or 2
   context.user_data["compare_asin1"]  # ASIN of first product
   context.user_data["simi_history"]   # list of {role, content} — last 10 msgs
   context.user_data["simi_active"]    # True when /support mode is on
   context.user_data["last_search"]    # last search query (for refinement)
   ```

2. **Inline button callback data patterns:**
   ```
   alert_{ASIN}           → set price alert
   features_{ASIN}        → show full features
   remove_alert_{DB_ID}   → delete alert from DB
   buy_{ASIN}             → log click + open link (or just use URL button)
   ```

3. **Admin guard (add to every admin handler):**
   ```python
   if str(update.effective_user.id) != os.environ["ADMIN_CHAT_ID"]:
       return
   ```

4. **SQLite thread safety:** Use `check_same_thread=False` or open/close connection inside each function call.

5. **Rate limiting between API calls:** `await asyncio.sleep(0.3)` between multiple GetItems calls (e.g. in search results loop or compare).

6. **parse_mode:** Always `parse_mode="HTML"` — never MarkdownV2 (too many escape issues).

7. **Scheduler setup:** Start APScheduler inside `main()` after building the Application, before `run_polling()`.

8. **GPT model:** `gpt-4o-mini` — fast, cheap, more than smart enough. User has paid OpenAI account confirmed.

9. **Conversation history cap:** Keep only last 10 messages for Simi to avoid token overflow.

10. **`/search` command flow:**
    - Set `context.user_data["waiting_for_search"] = True`
    - Next message from user → treat as search query → call SearchItems
    - Clear flag after search done

---

## 🧪 Full Testing Checklist

- [ ] Amazon.in product link → card with 3 inline buttons
- [ ] amzn.to short link → resolves correctly → card
- [ ] Bare ASIN (e.g. B0DLFMFBJW) → card
- [ ] Search page URL → friendly error message
- [ ] Random text (not shopping) → Simi redirects politely by name
- [ ] "best headphones under 5000" → 5 search results each with buttons
- [ ] `/search` → bot prompts → user types → 5 results
- [ ] `/compare` step 1 → step 2 → comparison card shown
- [ ] `📋 Features` button → features sent as follow-up
- [ ] `🔔 Price Alert` button → alert saved → confirmation
- [ ] `/myalerts` → list with remove buttons
- [ ] `/support` → Simi active → shopping Q answered → off-topic redirected
- [ ] New `/start` → admin gets silent notification
- [ ] `/users` → works for admin, silent ignore for others
- [ ] `/broadcast hello` → message sent to all users
- [ ] Price drop simulation → notification sent to user

---

## 📌 Instructions for Next Replit Session

**Paste this README at session start and say:**

> *"Read this README fully. It is a complete blueprint for a production Amazon Affiliate Telegram Bot called Shopping GPT. The folder `amazon-affiliate-bot/` already exists with partial code (`bot.py`, `amazon_api.py`). Write ALL files from scratch following every specification in this README: `bot.py`, `amazon_api.py`, `ai_handler.py`, `database.py`, `scheduler.py`, `admin.py`, `requirements.txt`. Do not skip any feature. Write production-ready, error-proof code. Do it once and perfect."*

---

*Blueprint finalized: May 22, 2026. All features confirmed by owner.*

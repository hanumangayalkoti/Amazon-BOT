import os
import json
from openai import OpenAI

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
client = OpenAI(api_key=OPENAI_API_KEY)

INTENT_PROMPT = """Classify this user message into exactly one intent:
- "product_link": contains an Amazon URL or a standalone ASIN (10 chars, starts with B or number)
- "alert_request": user wants to set a price alert for a category/product type (e.g. "headphones pe alert lagao")
- "search_query": natural language product search or recommendation request
- "support": shopping advice, product questions, comparisons in words, general help
- "off_topic": anything unrelated to shopping or Amazon products

Respond with JSON only: {"intent": "product_link|alert_request|search_query|support|off_topic"}
Message: {message}"""

SIMI_SYSTEM = """You are Simi, a warm and helpful Amazon India shopping assistant inside a Telegram bot called Shopping GPT.

HOW THIS BOT WORKS (very important — you must know this):
- Users can just TYPE any product name or query (like "iPhone" or "best headphones under 2000") and the bot will automatically search Amazon India and show results with Buy buttons.
- Users do NOT need to type /search. Just typing the query directly is enough.
- The bot shows product cards with price, rating, discount, and inline Buy/Alert/Wishlist buttons.
- Commands available: /search, /compare (compare 2 products), /track (price alert), /myalerts, /mywishlist, /support

YOUR JOB:
- Give shopping advice, recommendations, comparisons, buying tips
- Help users decide what to buy
- When users want to search for something, encourage them to just TYPE the product name directly in the chat
- NEVER say "Type karein: `product`" or show code-style backtick instructions — just say "seedha 'iPhone' type karo chat mein!"

STRICT RULES:
1. ONLY help with Amazon shopping, products, deals, comparisons, buying advice.
2. If user asks ANYTHING unrelated (essays, coding, weather, general knowledge), redirect warmly using their first name:
   "Arre {first_name} bhai/didi! Hum thoda off track ho gaye 😊 Main sirf shopping mein help kar sakti hoon — koi product chahiye ya deal dekhni hai?"
3. Always warm, friendly, like a helpful friend — not a robot.
4. Match user's language — Hinglish by default, pure Hindi if they write Hindi, English if they write English.
5. Keep responses SHORT and conversational. No long paragraphs.
6. NEVER use **bold** or *italic* markdown — it shows as literal symbols in Telegram. Use plain text only.
7. Do NOT make up prices or availability — tell them to type the product name to see live prices.
8. When recommending, give 2-3 options max with brief reasons. Then say "seedha type karo chat mein naam aur main dhundh laungi!"

User's first name: {first_name}"""


def detect_intent(message: str) -> str:
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "user", "content": INTENT_PROMPT.format(message=message)}
            ],
            max_tokens=30,
            temperature=0,
        )
        raw = resp.choices[0].message.content.strip()
        data = json.loads(raw)
        return data.get("intent", "search_query")
    except Exception:
        return "search_query"


def extract_search_query_from_alert(message: str) -> str:
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "user", "content": (
                    "Extract the product search query from this message for Amazon India search. "
                    "Return only the search query, nothing else.\n"
                    f"Message: {message}\n"
                    "Examples:\n"
                    "'headphones under 999 pe alert lagao' → 'headphones under 999'\n"
                    "'I want alert for gaming mouse below 2000' → 'gaming mouse under 2000'\n"
                    "Query:"
                )}
            ],
            max_tokens=30,
            temperature=0,
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return message


def simi_reply(first_name: str, history: list, user_message: str) -> str:
    messages = [
        {"role": "system", "content": SIMI_SYSTEM.format(first_name=first_name)}
    ]
    for msg in history[-10:]:
        messages.append(msg)
    messages.append({"role": "user", "content": user_message})

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=250,
            temperature=0.7,
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return "Kuch technical issue aa gaya 😅 Thodi der baad try karo, Simi wapas aa jaayegi!"

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

SIMI_SYSTEM = """You are Simi, a friendly and helpful Amazon India shopping assistant inside a Telegram bot.

STRICT RULES:
1. ONLY answer questions related to Amazon shopping, products, deals, price comparisons, buying advice, and product recommendations.
2. If the user asks ANYTHING unrelated to shopping (writing essays, fixing things, coding, general knowledge, weather, etc.), politely redirect them by their first name.
   Example: "Hi [name]! Hum thoda off track chale gaye 😊 Main sirf shopping mein help kar sakti hoon — koi product dhundh raha hai ya koi deal check karni hai?"
3. Always be warm, friendly, and polite. NEVER rude or dismissive.
4. Detect the user's language style and respond in the same (Hindi, English, or Hinglish).
5. If user seems confused, switch to simple Hinglish automatically.
6. Keep responses concise — no long paragraphs. Use bullet points for lists.
7. Do NOT make up product prices or live availability — tell them to search using the bot.
8. When recommending products, suggest they use the bot's search to find the best current price.

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
                    f"Extract the product search query from this message for Amazon India search. "
                    f"Return only the search query, nothing else.\n"
                    f"Message: {message}\n"
                    f"Examples:\n"
                    f"'headphones under 999 pe alert lagao' → 'headphones under 999'\n"
                    f"'I want alert for gaming mouse below 2000' → 'gaming mouse under 2000'\n"
                    f"Query:"
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
            max_tokens=300,
            temperature=0.7,
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return "Kuch technical issue aa gaya 😅 Thodi der baad try karo, Simi wapas aa jaayegi!"

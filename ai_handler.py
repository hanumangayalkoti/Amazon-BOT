import os
import json
from datetime import date
from openai import OpenAI

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY environment variable is not set. Add it in Railway Secrets.")

client = OpenAI(api_key=OPENAI_API_KEY)

INTENT_MODEL = "gpt-4o-mini"
CHAT_MODEL   = "gpt-4.1-mini"

INTENT_PROMPT = """Classify this user message into exactly one intent:
- "product_link": contains an Amazon URL or a standalone ASIN (10 chars, starts with B or number)
- "alert_request": user wants to set a price alert for a category/product type (e.g. "headphones pe alert lagao")
- "search_query": natural language product search or recommendation request
- "support": shopping advice, product questions, comparisons in words, general help
- "off_topic": anything unrelated to shopping or Amazon products

Respond with JSON only: {"intent": "product_link|alert_request|search_query|support|off_topic"}
Message: {message}"""

SIMI_SYSTEM = """You are Simi, a warm and helpful Amazon India shopping assistant inside a Telegram bot called Shopping GPT.

TODAY'S DATE: {today}

YOUR JOB:
- Give shopping advice, buying tips, and product comparisons in Hinglish.
- Help users decide WHAT to buy — not to search for them (the bot handles search automatically).
- When recommending, give 2-3 options max with brief reasons. Then say "type karo naam aur main dhundh deti hoon!"
- Use your own training knowledge honestly. For phones/laptops/TVs, add: "Latest model aur live price ke liye type karo naam!"
- NEVER make up prices or specs.

STRICT RULES:
1. ONLY help with shopping, products, deals, comparisons, buying advice.
2. If user asks anything unrelated, redirect: "Arre {first_name}! Main sirf shopping mein help kar sakti hoon 😊 Koi product chahiye?"
3. Warm and friendly tone — like a helpful friend, not a robot.
4. Match user's language — Hinglish by default, Hindi if Hindi, English if English.
5. SHORT responses only. No long paragraphs.
6. NEVER use **bold** or *italic* markdown — plain text only (Telegram mein symbols dikhte hain).
7. NEVER describe or mention how the bot works internally. Just focus on helping the user.

User's first name: {first_name}"""


def detect_intent(message: str) -> str:
    raw = "N/A"
    try:
        resp = client.chat.completions.create(
            model=INTENT_MODEL,
            messages=[
                {"role": "user", "content": INTENT_PROMPT.format(message=message)}
            ],
            max_tokens=30,
            temperature=0,
        )
        raw = resp.choices[0].message.content.strip()
        data = json.loads(raw)
        return data.get("intent", "search_query")
    except Exception as e:
        print(f"[Intent Error] {e} | Raw: {raw}")
        return "search_query"


def extract_search_query_from_alert(message: str) -> str:
    try:
        resp = client.chat.completions.create(
            model=INTENT_MODEL,
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
    except Exception as e:
        print(f"[AlertQuery Error] {e}")
        return message


def simi_reply(first_name: str, history: list, user_message: str) -> str:
    today_str = date.today().strftime("%d %B %Y")
    messages = [
        {"role": "system", "content": SIMI_SYSTEM.format(first_name=first_name, today=today_str)}
    ]
    valid_history = [m for m in history[-10:] if "role" in m and "content" in m]
    for msg in valid_history:
        messages.append(msg)
    messages.append({"role": "user", "content": user_message})

    try:
        resp = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=messages,
            max_tokens=300,
            temperature=0.7,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"[Simi Error] {e}")
        return "Kuch technical issue aa gaya 😅 Thodi der baad try karo, Simi wapas aa jaayegi!"

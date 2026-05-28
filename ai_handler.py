import os
import json
import logging
from datetime import date

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    logger.warning("OPENAI_API_KEY not set — AI features disabled.")

try:
    from openai import OpenAI
    _client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
except Exception:
    _client = None

INTENT_MODEL = "gpt-4o-mini"

INTENT_PROMPT = """Classify this user message into exactly one intent:
- "product_link": contains an Amazon URL or a standalone ASIN (10 chars, starts with B or number)
- "alert_request": user wants to set a price alert for a category/product type
- "search_query": natural language product search or recommendation request
- "support": shopping advice, product questions, comparisons in words, general help
- "off_topic": anything unrelated to shopping or Amazon products

Respond with JSON only: {{"intent": "product_link|alert_request|search_query|support|off_topic"}}
Message: {message}"""


def detect_intent(message: str) -> str:
    if _client is None:
        return "search_query"
    raw = "N/A"
    try:
        resp = _client.chat.completions.create(
            model=INTENT_MODEL,
            messages=[{"role": "user", "content": INTENT_PROMPT.format(message=message)}],
            max_tokens=30,
            temperature=0,
        )
        raw = resp.choices[0].message.content.strip()
        data = json.loads(raw)
        return data.get("intent", "search_query")
    except Exception as e:
        logger.error("detect_intent error: %s | raw=%s", e, raw)
        return "search_query"


def extract_search_query_from_alert(message: str) -> str:
    if _client is None:
        return message
    try:
        resp = _client.chat.completions.create(
            model=INTENT_MODEL,
            messages=[{"role": "user", "content": (
                "Extract the product search query from this message for Amazon India search. "
                "Return only the search query, nothing else.\n"
                f"Message: {message}\n"
                "Examples:\n"
                "'headphones under 999 pe alert lagao' → 'headphones under 999'\n"
                "'gaming mouse below 2000 pe alert chahiye' → 'gaming mouse under 2000'\n"
                "Query:"
            )}],
            max_tokens=40,
            temperature=0,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.error("extract_search_query_from_alert error: %s", e)
        return message

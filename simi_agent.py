import asyncio
import os
import json
import logging
from datetime import date

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
try:
    from openai import OpenAI
    _client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
except Exception:
    _client = None

AGENT_MODEL = "gpt-4o"

SIMI_SYSTEM = """You are Simi, an agentic Amazon India shopping assistant inside a Telegram bot called Shopping GPT.

TODAY: {today}
USER: {first_name}

YOUR CAPABILITIES:
You have tools to search Amazon, get product details, set alerts, manage wishlist, and find deals.
Use them proactively. When user asks to find/search — use search_amazon immediately.
When user says "compare X vs Y" — fetch both with get_product_details, then compare.
When user says "alert laga do" — use set_price_alert with the last discussed product.

AGENT BEHAVIOR:
- Always use tools when action is needed. Don't just advise — DO it.
- After search: recommend top 2-3 clearly with reasons.
- Give clear RECOMMENDATION — not just list. Tell user which one to buy and why.
- If budget mentioned — strictly filter by maxPrice.

RESPONSE FORMAT (Hinglish, plain text only — NO markdown **bold** or *italic*):
After searching:
1. Short intro line
2. Each product: Name, Price, Rating, Key highlight
3. WINNER recommendation with 1-2 line reason
4. Ask if they want alert or wishlist

STRICT RULES:
- NEVER make up prices or specs. Only use tool results.
- NEVER use **bold** or *italic* markdown.
- Keep responses concise — max 400 words.
- Always respond in Hinglish.
- ALWAYS be polite, warm, and respectful. Use "aap" form. Say "please", "shukriya", "zaroor".
- NEVER sound rude, blunt, or dismissive. If you can't help, explain gently.
- If off-topic: "Main aapki sirf shopping mein madad kar sakti hoon! Koi product chahiye aapko? 😊"
"""

AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_amazon",
            "description": "Amazon India pe products search karo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query in English"},
                    "max_price": {"type": "integer", "description": "Maximum price in INR"},
                    "min_price": {"type": "integer", "description": "Minimum price in INR"},
                    "min_rating": {"type": "number", "description": "Minimum star rating (1-4)"},
                    "min_saving_pct": {"type": "integer", "description": "Minimum discount %"},
                    "sort_by": {"type": "string", "enum": ["Price:LowToHigh", "Price:HighToLow", "Relevance", "Featured", "AvgCustomerReviews", "NewestArrivals"]},
                    "item_count": {"type": "integer", "description": "Number of results (1-5)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product_details",
            "description": "ASIN se product ki full details lo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "asin": {"type": "string", "description": "Amazon ASIN (10 chars)"},
                },
                "required": ["asin"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product_variations",
            "description": "Product ke variants dikhao — colors, sizes, storage options.",
            "parameters": {
                "type": "object",
                "properties": {
                    "asin": {"type": "string"},
                },
                "required": ["asin"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_price_alert",
            "description": "User ke liye price alert set karo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "asin": {"type": "string"},
                    "drop_percent": {"type": "number", "description": "% drop pe alert (optional)"},
                },
                "required": ["asin"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_to_wishlist",
            "description": "Product ko user ki wishlist mein add karo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "asin": {"type": "string"},
                },
                "required": ["asin"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_user_alerts",
            "description": "User ke active price alerts dikhao.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_user_wishlist",
            "description": "User ki wishlist dikhao.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_deals",
            "description": "Aaj ki best deals dhundo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "min_discount": {"type": "integer"},
                },
                "required": ["category"],
            },
        },
    },
]


# FIX: _execute_tool is now a normal sync function called via asyncio.to_thread
def _execute_tool(tool_name: str, args: dict, user_id: int, context_data: dict) -> str:
    import amazon_api as api
    import database as db

    try:
        if tool_name == "search_amazon":
            results = api.search_products(
                query=args.get("query", ""),
                item_count=args.get("item_count", 3),
                max_price=args.get("max_price"),
                min_price=args.get("min_price"),
                min_rating=args.get("min_rating"),
                min_saving_pct=args.get("min_saving_pct"),
                sort_by=args.get("sort_by"),
            )
            if not results:
                return json.dumps({"results": [], "message": "Koi products nahi mile"})
            out = []
            for r in results:
                out.append({
                    "asin": r["asin"],
                    "title": r.get("title", "")[:80],
                    "price": r.get("price", "N/A"),
                    "price_amount": r.get("price_amount", 0),
                    "discount": r.get("discount_pct", ""),
                    "rating": r.get("rating", 0),
                    "reviews": r.get("review_count", 0),
                    "brand": r.get("brand", ""),
                    "is_prime": r.get("is_prime", False),
                    "affiliate_link": r.get("affiliate_link", ""),
                })
            # FIX: Assign last_search_results OUTSIDE the loop
            context_data["last_search_results"] = out
            return json.dumps({"results": out})

        elif tool_name == "get_product_details":
            asin = args.get("asin", "")
            info = api.get_product_info(asin)
            context_data["last_asin"] = asin
            context_data["last_info"] = info
            return json.dumps({
                "asin": info["asin"],
                "title": info.get("title", "")[:80],
                "price": info.get("price", "N/A"),
                "price_amount": info.get("price_amount", 0),
                "mrp": info.get("mrp", ""),
                "discount": info.get("discount_pct", ""),
                "savings": info.get("savings", ""),
                "rating": info.get("rating", 0),
                "reviews": info.get("review_count", 0),
                "brand": info.get("brand", ""),
                "is_prime": info.get("is_prime", False),
                "is_lightning_deal": info.get("is_lightning_deal", False),
                "availability": info.get("availability", ""),
                "features": info.get("features", [])[:3],
                "affiliate_link": info.get("affiliate_link", ""),
            })

        elif tool_name == "get_product_variations":
            asin = args.get("asin", "")
            variants = api.get_product_variations(asin)
            out = [{"asin": v["asin"], "color": v.get("color", ""),
                    "size": v.get("size", ""), "storage": v.get("storage", ""),
                    "price": v.get("price", "")} for v in variants[:8]]
            return json.dumps({"variants": out})

        elif tool_name == "set_price_alert":
            asin = args.get("asin", "") or context_data.get("last_asin", "")
            if not asin:
                return json.dumps({"success": False, "message": "Kaunsa product? ASIN nahi mila."})
            info = context_data.get("last_info") or api.get_product_info(asin)
            drop_pct = args.get("drop_percent")
            alert_type = "percent" if drop_pct else "price"
            db.add_price_alert(
                user_id, asin, info.get("title", ""),
                info.get("price_amount", 0), info.get("affiliate_link", ""),
                alert_type=alert_type, drop_percent=drop_pct,
            )
            msg = f"Alert set! {info.get('title','')[:40]} — current price {info.get('price','')}"
            if drop_pct:
                msg += f" — {drop_pct}% girne pe notify karunga"
            return json.dumps({"success": True, "message": msg})

        elif tool_name == "add_to_wishlist":
            asin = args.get("asin", "") or context_data.get("last_asin", "")
            if not asin:
                return json.dumps({"success": False, "message": "Kaunsa product? ASIN nahi mila."})
            info = context_data.get("last_info") or api.get_product_info(asin)
            added = db.add_to_wishlist(
                user_id, asin, info.get("title", ""), info.get("price", ""),
                info.get("image_url", ""), info.get("affiliate_link", ""),
                price_amount=info.get("price_amount", 0),
            )
            msg = "Wishlist mein add ho gaya!" if added else "Pehle se wishlist mein hai!"
            return json.dumps({"success": True, "message": msg})

        elif tool_name == "get_user_alerts":
            alerts = db.get_user_alerts(user_id)
            out = [{"asin": a["asin"], "title": (a.get("product_title") or "")[:50],
                    "tracked_price": a.get("tracked_price", 0),
                    "current_price": a.get("current_price", 0)} for a in alerts[:10]]
            return json.dumps({"alerts": out, "count": len(alerts)})

        elif tool_name == "get_user_wishlist":
            items = db.get_wishlist(user_id)
            out = [{"asin": i["asin"], "title": (i.get("product_title") or "")[:50],
                    "price": i.get("price", ""),
                    "link": i.get("affiliate_link", "")} for i in items[:10]]
            return json.dumps({"wishlist": out, "count": len(items)})

        elif tool_name == "search_deals":
            category = args.get("category", "fashion")
            discount = args.get("min_discount", 30)
            results = api.search_deals([category], min_saving_pct=discount, item_count=5)
            out = [{"asin": r["asin"], "title": r.get("title", "")[:60],
                    "price": r.get("price", ""), "discount": r.get("discount_pct", ""),
                    "affiliate_link": r.get("affiliate_link", "")} for r in results]
            return json.dumps({"deals": out})

    except Exception as e:
        logger.error("Tool %s error: %s", tool_name, e)
        return json.dumps({"error": str(e)})

    return json.dumps({"error": f"Unknown tool: {tool_name}"})


async def run_simi_agent(
    user_id: int,
    first_name: str,
    user_message: str,
    history: list,
    context_data: dict,
) -> tuple[str, list]:
    if _client is None:
        return (
            "Abhi AI assistant available nahi hai. Seedha Amazon link ya search query bhejo! 🛍️",
            history,
        )

    today_str = date.today().strftime("%d %B %Y")
    system_msg = SIMI_SYSTEM.format(first_name=first_name, today=today_str)

    messages = [{"role": "system", "content": system_msg}]
    valid_history = [m for m in history[-8:] if "role" in m and "content" in m]
    messages.extend(valid_history)
    messages.append({"role": "user", "content": user_message})

    max_iterations = 5
    for _ in range(max_iterations):
        try:
            # FIX: Wrap blocking OpenAI call in asyncio.to_thread so event loop isn't blocked
            resp = await asyncio.to_thread(
                lambda: _client.chat.completions.create(
                    model=AGENT_MODEL,
                    messages=messages,
                    tools=AGENT_TOOLS,
                    tool_choice="auto",
                    max_tokens=800,
                    temperature=0.3,
                )
            )
        except Exception as e:
            logger.error("Simi agent OpenAI call error: %s", e)
            return "Kuch technical issue aa gaya 😅 Thodi der baad try karo!", history

        msg = resp.choices[0].message

        if not msg.tool_calls:
            reply = msg.content or "Koi jawab nahi mila, dobara try karo."
            history.append({"role": "user", "content": user_message})
            history.append({"role": "assistant", "content": reply})
            return reply, history[-20:]

        messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": [
            {"id": tc.id, "type": "function",
             "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in msg.tool_calls
        ]})

        # FIX: Execute each tool via asyncio.to_thread — avoids blocking DB/API calls
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments)
            except Exception:
                args = {}
            result = await asyncio.to_thread(
                _execute_tool, tc.function.name, args, user_id, context_data
            )
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })

    return "Simi abhi busy hai, thodi der baad try karo 😅", history

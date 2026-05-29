import os
import sys
import types
import re
import time
import logging
import threading
import requests
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# ── Register root directory as the creatorsapi_python_sdk package ─────────────
def _register_creatorsapi_sdk():
    if 'creatorsapi_python_sdk' in sys.modules:
        return
    _ROOT = os.path.dirname(os.path.abspath(__file__))

    def _pkg(name, path):
        m = types.ModuleType(name)
        m.__path__ = [path]
        m.__package__ = name
        m.__file__ = os.path.join(path, '__init__.py')
        sys.modules[name] = m

    _pkg('creatorsapi_python_sdk', _ROOT)
    _pkg('creatorsapi_python_sdk.api', _ROOT)
    _pkg('creatorsapi_python_sdk.auth', _ROOT)
    _pkg('creatorsapi_python_sdk.models', _ROOT)

_register_creatorsapi_sdk()
# ─────────────────────────────────────────────────────────────────────────────

CREDENTIAL_ID      = os.environ.get("CREDENTIAL_ID") or os.environ.get("AMAZON_CREDENTIAL_ID", "")
CREDENTIAL_SECRET  = os.environ.get("CREDENTIAL_SECRET") or os.environ.get("AMAZON_CREDENTIAL_SECRET", "")
CREDENTIAL_VERSION = os.environ.get("CREDENTIAL_VERSION") or os.environ.get("AMAZON_CREDENTIAL_VERSION", "3.1")
PARTNER_TAG        = os.environ.get("PARTNER_TAG") or os.environ.get("AMAZON_PARTNER_TAG", "")
MARKETPLACE        = os.environ.get("MARKETPLACE") or os.environ.get("AMAZON_MARKETPLACE", "www.amazon.in")

IST = timezone(timedelta(hours=5, minutes=30))

# FIX: Thread-safe cache with a lock to prevent race conditions
_cache: dict = {}
_cache_lock = threading.Lock()
_CACHE_TTL = 900

_api_client = None
_api_client_lock = threading.Lock()


def _get_api():
    global _api_client
    if _api_client is None:
        with _api_client_lock:
            if _api_client is None:
                try:
                    from creatorsapi_python_sdk.api_client import ApiClient
                    from creatorsapi_python_sdk.api.default_api import DefaultApi
                    client = ApiClient(
                        credential_id=CREDENTIAL_ID,
                        credential_secret=CREDENTIAL_SECRET,
                        version=CREDENTIAL_VERSION,
                    )
                    _api_client = DefaultApi(client)
                    logger.info("Amazon API client initialized successfully")
                except Exception as e:
                    logger.error("Failed to initialize Amazon API client: %s", e)
                    raise
    return _api_client


def _safe_get(obj, *keys, default=None):
    """Traverse nested dicts/objects safely."""
    for key in keys:
        if obj is None:
            return default
        if isinstance(obj, dict):
            obj = obj.get(key)
        elif hasattr(obj, key):
            obj = getattr(obj, key)
        elif isinstance(key, int) and isinstance(obj, (list, tuple)):
            obj = obj[key] if len(obj) > key else None
        else:
            return default
    return obj if obj is not None else default


# FIX: Safe float conversion — handles comma-formatted numbers like "1,299.00"
def _safe_float(value, default=0.0) -> float:
    if value is None:
        return default
    try:
        return float(str(value).replace(',', '').strip())
    except (ValueError, TypeError):
        return default


def _parse_item(item) -> dict:
    """
    Parse a single SDK Item object into a clean dict.

    IMPORTANT: The SDK's to_dict() calls model_dump(by_alias=True),
    so ALL keys are camelCase aliases, NOT snake_case Python field names.
    Confirmed by checking item.py line 87: _dict = self.model_dump(by_alias=True)
    """
    d = item.to_dict() if hasattr(item, "to_dict") else item
    info: dict = {"asin": d.get("asin", "")}

    # ── Title ──────────────────────────────────────────────────────────────
    # FIX: "itemInfo" not "item_info"; "displayValue" not "display_value"
    title_obj = _safe_get(d, "itemInfo", "title", "displayValue")
    info["title"] = title_obj or ""

    # ── Brand ───────────────────────────────────────────────────────────────
    # FIX: "byLineInfo" not "by_line_info"
    brand_obj = _safe_get(d, "itemInfo", "byLineInfo", "brand", "displayValue")
    info["brand"] = brand_obj or ""

    # ── Category ────────────────────────────────────────────────────────────
    # FIX: "productGroup" not "product_group"
    cat_obj = _safe_get(d, "itemInfo", "classifications", "productGroup", "displayValue")
    info["category"] = cat_obj or ""

    # ── Features ────────────────────────────────────────────────────────────
    # FIX: "displayValues" not "display_values"
    features_raw = _safe_get(d, "itemInfo", "features", "displayValues") or []
    info["features"] = [str(f) for f in features_raw[:8]]

    # ── Image ───────────────────────────────────────────────────────────────
    # "images" stays the same; inner "primary"/"large"/"url" are not aliased
    image_obj = _safe_get(d, "images", "primary", "large", "url") or \
                _safe_get(d, "images", "primary", "medium", "url")
    info["image_url"] = image_obj or ""

    # ── Listings ────────────────────────────────────────────────────────────
    # FIX: "offersV2" not "offers_v2"; inner "listings" stays lowercase
    listings = _safe_get(d, "offersV2", "listings") or []
    listing = listings[0] if listings else {}

    # ── Price ────────────────────────────────────────────────────────────────
    # SDK structure: listing.price → OfferPriceV2
    #   OfferPriceV2.money → Money { amount, currency, displayAmount }
    #   OfferPriceV2.savings → OfferSavings { money: Money }
    # "price", "money", "savings" are NOT aliased — they stay lowercase
    price_obj = _safe_get(listing, "price") or {}
    money_obj  = price_obj.get("money") or {}
    amount     = money_obj.get("amount")         # raw numeric
    display_price = money_obj.get("displayAmount", "")  # formatted "₹1,299.00"

    savings_obj  = price_obj.get("savings") or {}
    savings_money = savings_obj.get("money") or {}
    sav_amount   = savings_money.get("amount")
    sav_display  = savings_money.get("displayAmount", "")

    if amount is not None:
        # FIX: Use _safe_float to handle any edge-case numeric types
        info["price_amount"] = _safe_float(amount)
        # Prefer SDK-formatted price string; fall back to manual formatting
        info["price"] = display_price or f"₹{_safe_float(amount):,.0f}"
    else:
        info["price_amount"] = 0.0
        info["price"] = ""

    # MRP and discount — derive from savings when possible
    if amount is not None and sav_amount is not None:
        mrp_raw = _safe_float(amount) + _safe_float(sav_amount)
        info["mrp_amount"] = mrp_raw
        info["mrp"] = f"₹{mrp_raw:,.0f}"
        info["savings"] = sav_display or f"₹{_safe_float(sav_amount):,.0f}"
        if mrp_raw > 0:
            info["discount_pct"] = str(round((_safe_float(sav_amount) / mrp_raw) * 100))
        else:
            info["discount_pct"] = ""
    else:
        info["mrp_amount"] = 0.0
        info["mrp"] = ""
        info["savings"] = ""
        info["discount_pct"] = ""

    # ── Availability ────────────────────────────────────────────────────────
    # availability and condition inner fields are not aliased
    avail = _safe_get(listing, "availability", "message") or \
            _safe_get(listing, "availability", "type")
    info["availability"] = avail or ""

    # ── Condition ───────────────────────────────────────────────────────────
    # FIX: "displayValue" not "display_value" inside condition
    cond = _safe_get(listing, "condition", "displayValue")
    info["condition"] = cond or ""

    # ── Merchant ────────────────────────────────────────────────────────────
    # FIX: "merchantInfo" not "merchant_info"
    merchant_name = _safe_get(listing, "merchantInfo", "name") or ""
    info["merchant_name"] = merchant_name
    info["is_amazon_seller"] = merchant_name.lower() in ("amazon", "amazon seller services pvt ltd")

    # ── Prime / Delivery ────────────────────────────────────────────────────
    # offer_listing_v2 does not expose is_prime_eligible directly;
    # use isBuyBoxWinner as a proxy for "sold and fulfilled by Amazon"
    buy_box = listing.get("isBuyBoxWinner")
    info["is_prime"] = bool(buy_box and info["is_amazon_seller"])

    # ── Loyalty Points ──────────────────────────────────────────────────────
    # FIX: "loyaltyPoints" not "loyalty_points"
    loyalty = _safe_get(listing, "loyaltyPoints", "points")
    info["loyalty_points"] = int(_safe_float(loyalty)) if loyalty else 0

    # ── Deal Details ────────────────────────────────────────────────────────
    # FIX: "dealDetails" not "deal_details"; "endTime" not "end_time"
    deal = listing.get("dealDetails") or {}
    if isinstance(deal, dict) and deal:
        # "accessType" is the confirmed alias for access_type in deal_details.py
        deal_type = deal.get("accessType") or deal.get("type", "")
        info["is_lightning_deal"] = str(deal_type).upper() == "LIGHTNING_DEAL"
        end_time = deal.get("endTime")  # FIX: "endTime" not "end_time"
        if end_time:
            try:
                if isinstance(end_time, str):
                    from dateutil.parser import parse as dtparse
                    end_dt = dtparse(end_time)
                    if end_dt.tzinfo is None:
                        end_dt = end_dt.replace(tzinfo=timezone.utc)
                    end_dt = end_dt.astimezone(IST)
                    info["deal_end_time"] = end_dt.strftime("%d %b %I:%M %p IST")
                else:
                    info["deal_end_time"] = str(end_time)
            except Exception:
                info["deal_end_time"] = str(end_time)
        else:
            info["deal_end_time"] = ""
        info["deal_price"] = ""  # deal price is just the listing price
    else:
        info["is_lightning_deal"] = False
        info["deal_end_time"] = ""
        info["deal_price"] = ""

    # ── Customer Reviews ────────────────────────────────────────────────────
    # FIX: "customerReviews" not "customer_reviews"; "starRating" not "star_rating"
    # FIX: "displayValue" not "display_value" for count
    rating = _safe_get(d, "customerReviews", "starRating", "value")
    info["rating"] = _safe_float(rating) if rating else 0.0
    count = _safe_get(d, "customerReviews", "count", "displayValue") or \
            _safe_get(d, "customerReviews", "count")
    info["review_count"] = int(str(count).replace(",", "")) if count else 0

    # ── Sales Rank / Browse Nodes ────────────────────────────────────────────
    # FIX: "browseNodeInfo" not "browse_node_info"
    # FIX: "browseNodes" not "browse_nodes"; "displayName" not "display_name"
    # FIX: "salesRank" not "sales_rank"; "websiteSalesRank" not "website_sales_rank"
    browse_nodes = _safe_get(d, "browseNodeInfo", "browseNodes") or []
    if browse_nodes:
        node = browse_nodes[0] if isinstance(browse_nodes, list) else browse_nodes
        if isinstance(node, dict):
            info["sales_rank_category"] = node.get("displayName", "")
            sr = node.get("salesRank")
            info["sales_rank"] = int(_safe_float(sr)) if sr else 0
        else:
            info["sales_rank"] = 0
            info["sales_rank_category"] = ""
    else:
        # FIX: "websiteSalesRank" not "website_sales_rank"
        web_rank = _safe_get(d, "browseNodeInfo", "websiteSalesRank", "salesRank")
        web_cat  = _safe_get(d, "browseNodeInfo", "websiteSalesRank", "displayName")
        info["sales_rank"] = int(_safe_float(web_rank)) if web_rank else 0
        info["sales_rank_category"] = web_cat or ""

    info["affiliate_link"] = build_affiliate_link(info["asin"])
    return info


def build_affiliate_link(asin: str) -> str:
    tag = PARTNER_TAG or "defaulttag-21"
    return f"https://www.amazon.in/dp/{asin}?tag={tag}"


def extract_asin(text: str) -> tuple[str | None, str | None]:
    text = text.strip()
    if re.search(r"amazon\.[a-z.]+/s[/?]", text, re.IGNORECASE):
        return None, "search"
    patterns = [
        r"amazon\.[a-z.]+/(?:dp|gp/product|exec/obidos/ASIN)/([A-Z0-9]{10})",
        r"amazon\.[a-z.]+/[^/]+/dp/([A-Z0-9]{10})",
        r"asin=([A-Z0-9]{10})",
        # FIX: Require starts with B — avoids false matches on random ALL-CAPS words
        r"\b(B[A-Z0-9]{9})\b",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(1).upper(), None
    if "amzn.to" in text or "amzn.in" in text:
        try:
            url = re.search(r"https?://\S+", text)
            if url:
                # FIX: Increased timeout + User-Agent to avoid rejections
                r = requests.head(
                    url.group(), allow_redirects=True, timeout=10,
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                m2 = re.search(r"/dp/([A-Z0-9]{10})", r.url, re.IGNORECASE)
                if m2:
                    return m2.group(1).upper(), None
        except Exception:
            pass
    return None, None


def get_product_info(asin: str) -> dict:
    # FIX: Thread-safe cache read
    with _cache_lock:
        if asin in _cache:
            data, ts = _cache[asin]
            if time.time() - ts < _CACHE_TTL:
                return data

    try:
        api_inst = _get_api()
        from creatorsapi_python_sdk.models.get_items_request_content import GetItemsRequestContent
        resources = [
            "images.primary.large",
            "images.primary.medium",
            "itemInfo.title",
            "itemInfo.features",
            "itemInfo.byLineInfo",
            "itemInfo.classifications",
            "itemInfo.productInfo",
            "itemInfo.technicalInfo",
            "offersV2.listings.price",
            "offersV2.listings.availability",
            "offersV2.listings.condition",
            "offersV2.listings.dealDetails",
            "offersV2.listings.loyaltyPoints",
            "offersV2.listings.merchantInfo",
            "offersV2.listings.isBuyBoxWinner",
            "customerReviews.count",
            "customerReviews.starRating",
            "browseNodeInfo.browseNodes",
            "browseNodeInfo.websiteSalesRank",
        ]
        req = GetItemsRequestContent(
            partner_tag=PARTNER_TAG,
            item_ids=[asin],
            resources=resources,
        )
        resp = api_inst.get_items(x_marketplace=MARKETPLACE, get_items_request_content=req)
        resp_dict = resp.to_dict() if hasattr(resp, "to_dict") else {}
        items_result = resp_dict.get("itemsResult") or resp_dict.get("items_result") or {}
        items = items_result.get("items") or []
        if not items:
            raise ValueError(f"Product not found: {asin}")

        info = _parse_item(items[0])
        # FIX: Thread-safe cache write
        with _cache_lock:
            _cache[asin] = (info, time.time())
        return info

    except Exception as e:
        logger.error("get_product_info(%s) error: %s", asin, e)
        raise


def search_products(query: str, item_count: int = 5, max_price: int = None,
                    min_price: int = None, min_rating: float = None,
                    min_saving_pct: int = None, sort_by: str = None,
                    search_index: str = "All") -> list[dict]:
    try:
        api_inst = _get_api()
        from creatorsapi_python_sdk.models.search_items_request_content import SearchItemsRequestContent
        resources = [
            "images.primary.medium",
            "itemInfo.title",
            "itemInfo.byLineInfo",
            "itemInfo.classifications",
            "offersV2.listings.price",
            "offersV2.listings.availability",
            "offersV2.listings.dealDetails",
            "offersV2.listings.loyaltyPoints",
            "offersV2.listings.merchantInfo",
            "offersV2.listings.isBuyBoxWinner",
            "customerReviews.count",
            "customerReviews.starRating",
        ]
        kwargs: dict = {
            "partner_tag": PARTNER_TAG,
            "keywords": query,
            "search_index": search_index,
            "item_count": min(item_count, 10),
            "resources": resources,
        }
        if max_price:
            kwargs["max_price"] = max_price * 100
        if min_price:
            kwargs["min_price"] = min_price * 100
        if min_rating:
            kwargs["min_reviews_rating"] = int(min_rating)
        if min_saving_pct:
            kwargs["min_saving_percent"] = min_saving_pct
        if sort_by:
            kwargs["sort_by"] = sort_by

        req = SearchItemsRequestContent(**kwargs)
        resp = api_inst.search_items(x_marketplace=MARKETPLACE, search_items_request_content=req)
        resp_dict = resp.to_dict() if hasattr(resp, "to_dict") else {}
        search_result = resp_dict.get("searchResult") or resp_dict.get("search_result") or {}
        items = search_result.get("items") or []
        return [_parse_item(i) for i in items]

    except Exception as e:
        logger.error("search_products(%s) error: %s", query, e)
        return []


# FIX: Tiered fallback — tries progressively lower discount thresholds
def search_deals(category_keywords: list[str], min_saving_pct: int = 30,
                 item_count: int = 5, search_index: str = "All") -> list[dict]:
    thresholds_raw = [min_saving_pct, 20, 10, 0]
    seen_t: set = set()
    thresholds = [t for t in thresholds_raw if t <= min_saving_pct or t == 0
                  if not (t in seen_t or seen_t.add(t))]

    for threshold in thresholds:
        all_results: list = []
        seen_asins: set = set()
        for kw in category_keywords[:3]:
            results = search_products(
                query=kw,
                item_count=item_count,
                min_saving_pct=threshold if threshold > 0 else None,
                sort_by="FEATURED",
                search_index=search_index,
            )
            for r in results:
                if r["asin"] not in seen_asins and r.get("price_amount", 0) > 0:
                    all_results.append(r)
                    seen_asins.add(r["asin"])
            if len(all_results) >= item_count:
                break
        if all_results:
            return all_results[:item_count]
        logger.info("search_deals: no results at %d%% threshold, trying lower", threshold)

    return []


def get_lightning_deals(keywords: str = "fashion deals", item_count: int = 10) -> list[dict]:
    results = search_products(query=keywords, item_count=item_count)
    return [r for r in results if r.get("is_lightning_deal")]


def get_product_variations(asin: str) -> list[dict]:
    try:
        api_inst = _get_api()
        from creatorsapi_python_sdk.models.get_variations_request_content import GetVariationsRequestContent
        resources = [
            "images.primary.medium",
            "itemInfo.title",
            "offersV2.listings.price",
            "variationAttributes",
        ]
        req = GetVariationsRequestContent(
            partner_tag=PARTNER_TAG,
            asin=asin,
            resources=resources,
        )
        resp = api_inst.get_variations(x_marketplace=MARKETPLACE, get_variations_request_content=req)
        resp_dict = resp.to_dict() if hasattr(resp, "to_dict") else {}
        variations_result = resp_dict.get("variationsResult") or resp_dict.get("variations_result") or {}
        items = variations_result.get("items") or []
        variants = []
        for item in items:
            info = _parse_item(item)
            var_attrs = item.get("variationAttributes") or item.get("variation_attributes") or []
            for attr in var_attrs:
                name = (attr.get("name") or "").lower()
                val = attr.get("value") or ""
                if name in ("color", "colour"):
                    info["color"] = val
                elif name == "size":
                    info["size"] = val
                elif name in ("style", "storage", "storagesize"):
                    info["storage"] = val
            variants.append(info)
        return variants

    except Exception as e:
        logger.error("get_product_variations(%s) error: %s", asin, e)
        return []

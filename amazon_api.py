import os
import re
import time
import math
import logging
import threading
import requests
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# Config
# ══════════════════════════════════════════════════════════════════════════════

CREDENTIAL_ID      = os.environ.get("CREDENTIAL_ID") or os.environ.get("AMAZON_CREDENTIAL_ID", "")
CREDENTIAL_SECRET  = os.environ.get("CREDENTIAL_SECRET") or os.environ.get("AMAZON_CREDENTIAL_SECRET", "")
CREDENTIAL_VERSION = os.environ.get("CREDENTIAL_VERSION") or os.environ.get("AMAZON_CREDENTIAL_VERSION", "3.2")
PARTNER_TAG        = os.environ.get("PARTNER_TAG") or os.environ.get("AMAZON_PARTNER_TAG", "")
MARKETPLACE        = os.environ.get("MARKETPLACE") or os.environ.get("AMAZON_MARKETPLACE", "www.amazon.in")

if not PARTNER_TAG:
    raise SystemExit("FATAL: PARTNER_TAG env var not set — affiliate links will not work. Set it before starting.")

IST = timezone(timedelta(hours=5, minutes=30))

# ── OAuth Token URLs by version ────────────────────────────────────────────
_VERSION_TOKEN_URLS = {
    "2.1": "https://creatorsapi.auth.us-east-1.amazoncognito.com/oauth2/token",
    "2.2": "https://creatorsapi.auth.eu-south-2.amazoncognito.com/oauth2/token",
    "2.3": "https://creatorsapi.auth.us-west-2.amazoncognito.com/oauth2/token",
    "3.1": "https://api.amazon.com/auth/o2/token",
    "3.2": "https://api.amazon.co.uk/auth/o2/token",
    "3.3": "https://api.amazon.co.jp/auth/o2/token",
}

_IS_LWA = CREDENTIAL_VERSION.startswith("3.")
_SCOPE  = "creatorsapi::default" if _IS_LWA else "creatorsapi/default"

# ── API Endpoints ─────────────────────────────────────────────────────────────
_API_BASE      = "https://creatorsapi.amazon"
_ITEMS_EP      = f"{_API_BASE}/catalog/v1/getItems"
_SEARCH_EP     = f"{_API_BASE}/catalog/v1/searchItems"
_VARIATIONS_EP = f"{_API_BASE}/catalog/v1/getVariations"

# ── Thread-safe caches ────────────────────────────────────────────────────────
_cache: dict = {}
_cache_lock  = threading.Lock()
_CACHE_TTL   = 900  # 15 minutes

_token_cache: dict = {"token": None, "expires_at": 0.0}
_token_lock = threading.Lock()

# ── Best deals keyword pool (mix of all categories) ───────────────────────────
_BEST_DEAL_KEYWORDS = [
    "electronics sale discount",
    "fashion clothing deals",
    "home kitchen offers",
    "beauty skincare sale",
    "mobile phones discount",
    "laptop computer deals",
    "sports fitness discount",
    "watches accessories sale",
    "toys games sale",
    "appliances offers",
]


# ══════════════════════════════════════════════════════════════════════════════
# Token Management
# ══════════════════════════════════════════════════════════════════════════════

def _get_token() -> str:
    with _token_lock:
        if _token_cache["token"] and time.time() < _token_cache["expires_at"]:
            return _token_cache["token"]

        if not CREDENTIAL_ID or not CREDENTIAL_SECRET:
            raise RuntimeError("CREDENTIAL_ID ya CREDENTIAL_SECRET set nahi hai")

        token_url = _VERSION_TOKEN_URLS.get(CREDENTIAL_VERSION)
        if not token_url:
            raise RuntimeError(f"Unsupported CREDENTIAL_VERSION: {CREDENTIAL_VERSION!r}")

        payload = {
            "grant_type":    "client_credentials",
            "client_id":     CREDENTIAL_ID,
            "client_secret": CREDENTIAL_SECRET,
            "scope":         _SCOPE,
        }
        try:
            if _IS_LWA:
                resp = requests.post(
                    token_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=15,
                )
            else:
                resp = requests.post(token_url, data=payload, timeout=15)

            resp.raise_for_status()
            data       = resp.json()
            token      = data["access_token"]
            expires_in = data.get("expires_in", 3600)
            _token_cache["token"]      = token
            _token_cache["expires_at"] = time.time() + expires_in - 60
            logger.info("Amazon Creators API token refresh successful")
            return token

        except requests.HTTPError as e:
            raise RuntimeError(
                f"Token HTTP error {e.response.status_code}: {e.response.text[:300]}"
            ) from e
        except Exception as e:
            raise RuntimeError(f"Token fetch failed: {e}") from e


def _api_post(endpoint: str, payload: dict) -> dict:
    token = _get_token()
    resp  = requests.post(
        endpoint,
        json=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "x-marketplace": MARKETPLACE,
            "Content-Type":  "application/json",
        },
        timeout=20,
    )
    if resp.status_code == 403:
        with _token_lock:
            _token_cache["token"]      = None
            _token_cache["expires_at"] = 0.0
        resp.raise_for_status()
    if resp.status_code not in (200, 206):
        resp.raise_for_status()
    return resp.json()


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _safe_get(obj, *keys, default=None):
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


def _safe_float(value, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(str(value).replace(',', '').strip())
    except (ValueError, TypeError):
        return default


def _hires_image(url: str) -> str:
    """
    Amazon image URLs contain size codes like _SL500_, _SL200_, _AC_SX300_.
    Replace them with _SL1500_ to get the highest quality image.
    Pehle clear aati thi kyunki directly _SL1500_ mil rahi thi —
    ab hum force karke hamesha hi-res URL bana dete hain.
    """
    import re
    if not url:
        return url
    # Replace _SL<number>_ patterns
    url = re.sub(r'_SL\d+_', '_SL1500_', url)
    # Replace _AC_SX<number>_ or _AC_SY<number>_ patterns
    url = re.sub(r'_AC_S[XY]\d+_', '_SL1500_', url)
    # Replace _AC_UL<number>_ patterns
    url = re.sub(r'_AC_UL\d+_', '_SL1500_', url)
    # Replace _SS<number>_ (square size) patterns
    url = re.sub(r'_SS\d+_', '_SL1500_', url)
    return url


def _compute_deal_score(deal: dict) -> float:
    """
    Smart Deal Score:
      - Discount %   → 50% weight  (0–100 points)
      - Rating       → 30% weight  (rating/5 * 100 points)
      - Review count → 20% weight  (log scale, 0–100 points)

    Higher score = better deal to post.
    """
    discount = _safe_float(deal.get("discount_pct", 0))
    rating   = _safe_float(deal.get("rating", 0))
    reviews  = int(deal.get("review_count", 0) or 0)

    discount_score = min(discount, 100.0)
    rating_score   = (rating / 5.0) * 100.0 if rating > 0 else 0.0
    review_score   = min(math.log10(reviews + 1) / 4.0, 1.0) * 100.0 if reviews > 0 else 0.0

    return (discount_score * 0.5) + (rating_score * 0.3) + (review_score * 0.2)


# ══════════════════════════════════════════════════════════════════════════════
# Item Parser
# ══════════════════════════════════════════════════════════════════════════════

def _parse_item(item) -> dict:
    d = item if isinstance(item, dict) else {}

    info: dict = {"asin": d.get("asin", "")}

    info["title"]    = _safe_get(d, "itemInfo", "title", "displayValue") or ""
    info["brand"]    = _safe_get(d, "itemInfo", "byLineInfo", "brand", "displayValue") or ""
    info["category"] = _safe_get(d, "itemInfo", "classifications", "productGroup", "displayValue") or ""

    features_raw  = _safe_get(d, "itemInfo", "features", "displayValues") or []
    info["features"] = [str(f) for f in features_raw[:8]]

    raw_img = (
        _safe_get(d, "images", "primary", "large", "url") or
        _safe_get(d, "images", "primary", "medium", "url") or ""
    )
    info["image_url"] = _hires_image(raw_img)

    listings = _safe_get(d, "offersV2", "listings") or []
    listing  = listings[0] if listings else {}

    price_obj     = _safe_get(listing, "price") or {}
    money_obj     = price_obj.get("money") or {}
    amount        = money_obj.get("amount")
    display_price = money_obj.get("displayAmount", "")

    savings_obj = price_obj.get("savings") or {}
    sav_money   = savings_obj.get("money") or {}
    sav_amount  = sav_money.get("amount")
    sav_display = sav_money.get("displayAmount", "")

    if amount is not None:
        info["price_amount"] = _safe_float(amount)
        info["price"]        = display_price or f"₹{_safe_float(amount):,.0f}"
    else:
        info["price_amount"] = 0.0
        info["price"]        = ""

    if amount is not None and sav_amount is not None:
        mrp_raw            = _safe_float(amount) + _safe_float(sav_amount)
        info["mrp_amount"] = mrp_raw
        info["mrp"]        = f"₹{mrp_raw:,.0f}"
        info["savings"]    = sav_display or f"₹{_safe_float(sav_amount):,.0f}"
        info["discount_pct"] = (
            str(round((_safe_float(sav_amount) / mrp_raw) * 100)) if mrp_raw > 0 else ""
        )
    else:
        info["mrp_amount"]   = 0.0
        info["mrp"]          = ""
        info["savings"]      = ""
        info["discount_pct"] = ""

    info["availability"] = (
        _safe_get(listing, "availability", "message") or
        _safe_get(listing, "availability", "type") or ""
    )
    info["condition"] = _safe_get(listing, "condition", "displayValue") or ""

    merchant_name         = _safe_get(listing, "merchantInfo", "name") or ""
    info["merchant_name"] = merchant_name
    info["is_amazon_seller"] = merchant_name.lower() in (
        "amazon", "amazon seller services pvt ltd", "cloudtail india pvt ltd"
    )
    info["is_prime"] = bool(listing.get("isBuyBoxWinner") and info["is_amazon_seller"])

    loyalty = _safe_get(listing, "loyaltyPoints", "points")
    info["loyalty_points"] = int(_safe_float(loyalty)) if loyalty else 0

    deal = listing.get("dealDetails") or {}
    if isinstance(deal, dict) and deal:
        deal_type                 = deal.get("accessType") or deal.get("type", "")
        info["is_lightning_deal"] = str(deal_type).upper() == "LIGHTNING_DEAL"
        end_time                  = deal.get("endTime")
        if end_time:
            try:
                from dateutil.parser import parse as dtparse
                end_dt = dtparse(str(end_time))
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=timezone.utc)
                info["deal_end_time"] = end_dt.astimezone(IST).strftime("%d %b %I:%M %p IST")
            except Exception:
                info["deal_end_time"] = str(end_time)
        else:
            info["deal_end_time"] = ""
    else:
        info["is_lightning_deal"] = False
        info["deal_end_time"]     = ""

    info["deal_price"] = ""

    rating = (
        _safe_get(d, "customerReviews", "starRating", "value") or
        _safe_get(d, "customerReviews", "starRating")
    )
    info["rating"] = _safe_float(rating)

    count = (
        _safe_get(d, "customerReviews", "count", "displayValue") or
        _safe_get(d, "customerReviews", "count", "value") or
        _safe_get(d, "customerReviews", "count")
    )
    try:
        info["review_count"] = int(str(count).replace(",", "")) if count else 0
    except (ValueError, TypeError):
        info["review_count"] = 0

    browse_nodes = _safe_get(d, "browseNodeInfo", "browseNodes") or []
    if browse_nodes:
        node = browse_nodes[0] if isinstance(browse_nodes, list) else browse_nodes
        if isinstance(node, dict):
            info["sales_rank_category"] = node.get("displayName", "")
            sr = node.get("salesRank")
            info["sales_rank"] = int(_safe_float(sr)) if sr else 0
        else:
            info["sales_rank_category"] = ""
            info["sales_rank"]           = 0
    else:
        info["sales_rank"] = int(_safe_float(
            _safe_get(d, "browseNodeInfo", "websiteSalesRank", "salesRank")))
        info["sales_rank_category"] = _safe_get(
            d, "browseNodeInfo", "websiteSalesRank", "displayName") or ""

    product_info = _safe_get(d, "itemInfo", "productInfo") or {}
    info["color"]        = _safe_get(product_info, "color", "displayValue") or ""
    info["model_number"] = _safe_get(product_info, "model", "displayValue") or ""

    tech_formats = _safe_get(d, "itemInfo", "technicalInfo", "formats", "displayValues") or []
    info["tech_formats"] = [str(f) for f in tech_formats[:3]]

    info["affiliate_link"] = build_affiliate_link(info["asin"])
    return info


# ══════════════════════════════════════════════════════════════════════════════
# Public helpers
# ══════════════════════════════════════════════════════════════════════════════

def build_affiliate_link(asin: str) -> str:
    return f"https://www.amazon.in/dp/{asin}?tag={PARTNER_TAG}"


def extract_asin(text: str) -> tuple[str | None, str | None]:
    text = text.strip()
    if re.search(r"amazon\.[a-z.]+/s[/?]", text, re.IGNORECASE):
        return None, "search"
    patterns = [
        r"amazon\.[a-z.]+/(?:dp|gp/product|exec/obidos/ASIN)/([A-Z0-9]{10})",
        r"amazon\.[a-z.]+/[^/]+/dp/([A-Z0-9]{10})",
        r"asin=([A-Z0-9]{10})",
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


# ══════════════════════════════════════════════════════════════════════════════
# Resource lists
# ══════════════════════════════════════════════════════════════════════════════

_GET_ITEMS_RESOURCES = [
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

_SEARCH_RESOURCES = [
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


# ══════════════════════════════════════════════════════════════════════════════
# Core API calls
# ══════════════════════════════════════════════════════════════════════════════

def get_product_info(asin: str) -> dict:
    with _cache_lock:
        if asin in _cache:
            data, ts = _cache[asin]
            if time.time() - ts < _CACHE_TTL:
                return data

    try:
        data  = _api_post(_ITEMS_EP, {
            "partnerTag": PARTNER_TAG,
            "itemIds":    [asin],
            "resources":  _GET_ITEMS_RESOURCES,
        })
        items = (data.get("itemsResult") or {}).get("items") or []
        if not items:
            api_errors = data.get("errors") or []
            if api_errors:
                msgs = "; ".join(
                    f"{e.get('code', '?')}: {e.get('message', '?')}" for e in api_errors
                )
                raise ValueError(f"Amazon API error — {msgs}")
            raise ValueError(f"Product not found: {asin}")
        info = _parse_item(items[0])
        with _cache_lock:
            _cache[asin] = (info, time.time())
        return info

    except Exception as e:
        logger.error("get_product_info(%s) error: %s", asin, e, exc_info=True)
        raise


def search_products(
    query: str,
    item_count: int = 5,
    max_price: int = None,
    min_price: int = None,
    min_rating: float = None,
    min_saving_pct: int = None,
    sort_by: str = None,
    search_index: str = "All",
) -> list[dict]:
    try:
        payload: dict = {
            "partnerTag":  PARTNER_TAG,
            "keywords":    query,
            "searchIndex": search_index,
            "itemCount":   min(item_count, 10),
            "resources":   _SEARCH_RESOURCES,
        }
        if max_price:
            payload["maxPrice"] = max_price * 100
        if min_price:
            payload["minPrice"] = min_price * 100
        if min_rating:
            payload["minReviewsRating"] = int(min_rating)
        if min_saving_pct:
            payload["minSavingPercent"] = min_saving_pct
        if sort_by:
            payload["sortBy"] = sort_by

        data  = _api_post(_SEARCH_EP, payload)
        items = (data.get("searchResult") or {}).get("items") or []
        return [_parse_item(i) for i in items]

    except Exception as e:
        logger.error("search_products(%s) error: %s", query, e)
        return []


def search_deals(
    category_keywords: list[str],
    min_saving_pct: int = 30,
    item_count: int = 5,
    search_index: str = "All",
) -> list[dict]:
    thresholds = [t for t in [min_saving_pct, 20, 10, 0] if t <= min_saving_pct or t == 0]
    thresholds = list(dict.fromkeys(thresholds))

    for threshold in thresholds:
        all_results: list = []
        seen_asins: set   = set()
        for kw in category_keywords[:3]:
            results = search_products(
                query=kw,
                item_count=item_count,
                min_saving_pct=threshold if threshold > 0 else None,
                sort_by="Featured",
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
        logger.info("search_deals: 0 results at %d%% threshold, trying lower", threshold)
    return []


def get_lightning_deals(keywords: str = "fashion deals", item_count: int = 10) -> list[dict]:
    results = search_products(query=keywords, item_count=item_count)
    return [r for r in results if r.get("is_lightning_deal")]


def get_product_variations(asin: str) -> list[dict]:
    try:
        data  = _api_post(_VARIATIONS_EP, {
            "partnerTag": PARTNER_TAG,
            "asin":       asin,
            "resources":  [
                "images.primary.medium",
                "itemInfo.title",
                "itemInfo.productInfo",
                "offersV2.listings.price",
                "offersV2.listings.availability",
                "variationSummary.variationDimension",
                "variationSummary.price.lowestPrice",
                "variationSummary.price.highestPrice",
            ],
        })
        items = (data.get("variationsResult") or {}).get("items") or []

        variants = []
        for item in items:
            info      = _parse_item(item)
            var_attrs = item.get("variationAttributes") or []
            for attr in var_attrs:
                name = (attr.get("name") or "").lower()
                val  = attr.get("value") or ""
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


# ══════════════════════════════════════════════════════════════════════════════
# Smart Deal Scoring — NEW
# ══════════════════════════════════════════════════════════════════════════════

def get_best_deals_scored(count: int = 5, min_discount: int = 10, min_rating: float = 3.0) -> list[dict]:
    """
    Fetch a broad pool of deals from multiple keyword categories,
    score each deal using discount%, rating, and review count,
    and return the top `count` deals sorted by score (best first).

    Scoring formula:
      - Discount%   → 50% weight
      - Rating/5    → 30% weight
      - log(reviews)→ 20% weight

    Quality filters:
      - Must have a price
      - Discount >= min_discount (default 10%)
      - Rating >= min_rating (default 3.0), or unrated products allowed if discount is high
    """
    seen_asins: set  = set()
    all_deals: list  = []

    for keyword in _BEST_DEAL_KEYWORDS:
        try:
            results = search_products(
                query=keyword,
                item_count=10,
                min_saving_pct=min_discount if min_discount > 0 else None,
                sort_by="Featured",
            )
            for deal in results:
                asin = deal.get("asin", "")
                if not asin or asin in seen_asins:
                    continue
                price = deal.get("price_amount", 0)
                if not price or price <= 0:
                    continue
                disc = _safe_float(deal.get("discount_pct", 0))
                if disc < min_discount:
                    continue
                rating = _safe_float(deal.get("rating", 0))
                if rating > 0 and rating < min_rating:
                    continue
                seen_asins.add(asin)
                deal["_score"] = _compute_deal_score(deal)
                all_deals.append(deal)
        except Exception as e:
            logger.warning("get_best_deals_scored keyword '%s' error: %s", keyword, e)
            continue

        time.sleep(0.3)

    if not all_deals:
        logger.warning("get_best_deals_scored: no deals found, retrying with lower threshold")
        return get_best_deals_scored(count=count, min_discount=5, min_rating=0.0) if min_discount > 5 else []

    all_deals.sort(key=lambda d: d.get("_score", 0), reverse=True)
    logger.info(
        "get_best_deals_scored: %d deals scored, returning top %d (best score: %.1f)",
        len(all_deals), count, all_deals[0].get("_score", 0) if all_deals else 0
    )
    return all_deals[:count]

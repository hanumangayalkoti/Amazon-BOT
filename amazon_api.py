import os
import sys
import re
import time
import types
import logging
import importlib
import importlib.util
import threading
import requests
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# SDK PACKAGE REGISTRATION
#
# The repo is FLAT — all SDK files (api_client.py, configuration.py, etc.)
# sit in the same directory as this file, with NO subfolders.
# The SDK was originally written as a package named `creatorsapi_python_sdk`
# and its files do `from creatorsapi_python_sdk.X import Y` internally.
#
# We register fake package entries in sys.modules so those imports resolve
# to files in _ROOT (the current directory). The models package gets a smart
# __getattr__ so that api_client.py's `getattr(creatorsapi_python_sdk.models,
# "ClassName")` works correctly.
# ══════════════════════════════════════════════════════════════════════════════

def _camel_to_snake(name: str) -> str:
    s = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', name)
    s = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', s)
    return s.lower()


def _register_creatorsapi_sdk():
    """
    Register creatorsapi_python_sdk as a virtual package pointing at _ROOT.
    Must be called before any SDK import.
    """
    if 'creatorsapi_python_sdk' in sys.modules:
        return

    _ROOT = os.path.dirname(os.path.abspath(__file__))

    def _simple_pkg(name: str, path: str) -> types.ModuleType:
        m = types.ModuleType(name)
        m.__path__ = [path]
        m.__package__ = name
        m.__file__ = os.path.join(path, '__init__.py')
        sys.modules[name] = m
        return m

    # Root package and sub-packages
    root_pkg  = _simple_pkg('creatorsapi_python_sdk', _ROOT)
    _simple_pkg('creatorsapi_python_sdk.api',  _ROOT)
    _simple_pkg('creatorsapi_python_sdk.auth', _ROOT)

    # ── Smart models package ───────────────────────────────────────────────
    # api_client.py does:
    #   import creatorsapi_python_sdk.models
    #   klass = getattr(creatorsapi_python_sdk.models, klass_name_str)
    # We handle this with module-level __getattr__ that converts the
    # CamelCase class name → snake_case filename and loads the class.
    models_pkg = types.ModuleType('creatorsapi_python_sdk.models')
    models_pkg.__path__ = [_ROOT]
    models_pkg.__package__ = 'creatorsapi_python_sdk.models'
    models_pkg.__file__ = os.path.join(_ROOT, '__init__.py')

    def _models_getattr(class_name: str):
        # Convert e.g. "GetItemsResponseContent" → "get_items_response_content"
        filename = _camel_to_snake(class_name)
        filepath = os.path.join(_ROOT, f'{filename}.py')
        sub_name = f'creatorsapi_python_sdk.models.{filename}'

        if sub_name not in sys.modules:
            if not os.path.isfile(filepath):
                raise AttributeError(
                    f"module 'creatorsapi_python_sdk.models' has no attribute {class_name!r}"
                )
            spec = importlib.util.spec_from_file_location(sub_name, filepath)
            mod = importlib.util.module_from_spec(spec)
            mod.__package__ = 'creatorsapi_python_sdk.models'
            sys.modules[sub_name] = mod
            spec.loader.exec_module(mod)

        sub_mod = sys.modules[sub_name]
        cls = getattr(sub_mod, class_name, None)
        if cls is None:
            raise AttributeError(
                f"module {sub_name!r} has no attribute {class_name!r}"
            )
        # Cache on the models package so next getattr() is O(1)
        setattr(models_pkg, class_name, cls)
        return cls

    models_pkg.__getattr__ = _models_getattr
    sys.modules['creatorsapi_python_sdk.models'] = models_pkg

    # Wire models_pkg as attribute on root_pkg so
    # `import creatorsapi_python_sdk.models` works correctly
    root_pkg.models = models_pkg


_register_creatorsapi_sdk()

# ══════════════════════════════════════════════════════════════════════════════
# Config
# ══════════════════════════════════════════════════════════════════════════════

CREDENTIAL_ID      = os.environ.get("CREDENTIAL_ID") or os.environ.get("AMAZON_CREDENTIAL_ID", "")
CREDENTIAL_SECRET  = os.environ.get("CREDENTIAL_SECRET") or os.environ.get("AMAZON_CREDENTIAL_SECRET", "")
CREDENTIAL_VERSION = os.environ.get("CREDENTIAL_VERSION") or os.environ.get("AMAZON_CREDENTIAL_VERSION", "3.1")
PARTNER_TAG        = os.environ.get("PARTNER_TAG") or os.environ.get("AMAZON_PARTNER_TAG", "")
MARKETPLACE        = os.environ.get("MARKETPLACE") or os.environ.get("AMAZON_MARKETPLACE", "www.amazon.in")

IST = timezone(timedelta(hours=5, minutes=30))

# Thread-safe in-memory cache
_cache: dict = {}
_cache_lock = threading.Lock()
_CACHE_TTL = 900  # 15 minutes

# Lazy API client
_api_client = None
_api_client_lock = threading.Lock()


def _get_api():
    global _api_client
    if _api_client is None:
        with _api_client_lock:
            if _api_client is None:
                from api_client import ApiClient       # flat import — file is in root
                from default_api import DefaultApi     # flat import — file is in root
                client = ApiClient(
                    credential_id=CREDENTIAL_ID,
                    credential_secret=CREDENTIAL_SECRET,
                    version=CREDENTIAL_VERSION,
                )
                _api_client = DefaultApi(client)
                logger.info("Amazon API client initialized")
    return _api_client


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

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


def _safe_float(value, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(str(value).replace(',', '').strip())
    except (ValueError, TypeError):
        return default


# ══════════════════════════════════════════════════════════════════════════════
# Item Parser
# ══════════════════════════════════════════════════════════════════════════════

def _parse_item(item) -> dict:
    """
    Parse one SDK Item into a plain dict.

    SDK's to_dict() uses model_dump(by_alias=True) → ALL keys are camelCase.
    Confirmed from item.py:
        _dict = self.model_dump(by_alias=True)
        _dict['itemInfo']        = self.item_info.to_dict()
        _dict['offersV2']        = self.offers_v2.to_dict()
        _dict['customerReviews'] = self.customer_reviews.to_dict()
        _dict['browseNodeInfo']  = self.browse_node_info.to_dict()
    """
    d = item.to_dict() if hasattr(item, "to_dict") else item

    info: dict = {"asin": d.get("asin", "")}

    # ── Title ─────────────────────────────────────────────────────────────────
    info["title"] = _safe_get(d, "itemInfo", "title", "displayValue") or ""

    # ── Brand ─────────────────────────────────────────────────────────────────
    info["brand"] = _safe_get(d, "itemInfo", "byLineInfo", "brand", "displayValue") or ""

    # ── Category ──────────────────────────────────────────────────────────────
    # classifications.productGroup → displayValue (all camelCase)
    info["category"] = _safe_get(d, "itemInfo", "classifications", "productGroup", "displayValue") or ""

    # ── Features ──────────────────────────────────────────────────────────────
    features_raw = _safe_get(d, "itemInfo", "features", "displayValues") or []
    info["features"] = [str(f) for f in features_raw[:8]]

    # ── Image ─────────────────────────────────────────────────────────────────
    info["image_url"] = (
        _safe_get(d, "images", "primary", "large", "url") or
        _safe_get(d, "images", "primary", "medium", "url") or ""
    )

    # ── First listing ─────────────────────────────────────────────────────────
    listings = _safe_get(d, "offersV2", "listings") or []
    listing  = listings[0] if listings else {}

    # ── Price ─────────────────────────────────────────────────────────────────
    # OfferPriceV2 structure (confirmed from offer_price_v2.py):
    #   price → { money: { amount, currency, displayAmount },
    #             savings: { money: { amount, currency, displayAmount } },
    #             pricePerUnit, savingBasis }
    price_obj     = _safe_get(listing, "price") or {}
    money_obj     = price_obj.get("money") or {}
    amount        = money_obj.get("amount")
    display_price = money_obj.get("displayAmount", "")

    savings_obj  = price_obj.get("savings") or {}
    sav_money    = savings_obj.get("money") or {}
    sav_amount   = sav_money.get("amount")
    sav_display  = sav_money.get("displayAmount", "")

    if amount is not None:
        info["price_amount"] = _safe_float(amount)
        info["price"]        = display_price or f"₹{_safe_float(amount):,.0f}"
    else:
        info["price_amount"] = 0.0
        info["price"]        = ""

    if amount is not None and sav_amount is not None:
        mrp_raw          = _safe_float(amount) + _safe_float(sav_amount)
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

    # ── Availability ──────────────────────────────────────────────────────────
    info["availability"] = (
        _safe_get(listing, "availability", "message") or
        _safe_get(listing, "availability", "type") or ""
    )

    # ── Condition ─────────────────────────────────────────────────────────────
    info["condition"] = _safe_get(listing, "condition", "displayValue") or ""

    # ── Merchant ──────────────────────────────────────────────────────────────
    # FIX: merchantInfo not merchant_info (offer_listing_v2.py alias confirmed)
    merchant_name        = _safe_get(listing, "merchantInfo", "name") or ""
    info["merchant_name"] = merchant_name
    info["is_amazon_seller"] = merchant_name.lower() in (
        "amazon", "amazon seller services pvt ltd", "cloudtail india pvt ltd"
    )

    # ── Prime (proxy via isBuyBoxWinner) ──────────────────────────────────────
    info["is_prime"] = bool(listing.get("isBuyBoxWinner") and info["is_amazon_seller"])

    # ── Loyalty Points ────────────────────────────────────────────────────────
    # FIX: loyaltyPoints not loyalty_points
    loyalty          = _safe_get(listing, "loyaltyPoints", "points")
    info["loyalty_points"] = int(_safe_float(loyalty)) if loyalty else 0

    # ── Deal Details ──────────────────────────────────────────────────────────
    # FIX: dealDetails not deal_details; endTime not end_time
    deal = listing.get("dealDetails") or {}
    if isinstance(deal, dict) and deal:
        deal_type                = deal.get("accessType") or deal.get("type", "")
        info["is_lightning_deal"] = str(deal_type).upper() == "LIGHTNING_DEAL"
        end_time                 = deal.get("endTime")
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

    # ── Customer Reviews ──────────────────────────────────────────────────────
    # FIX: customerReviews → starRating → value; count → displayValue
    rating = _safe_get(d, "customerReviews", "starRating", "value")
    info["rating"] = _safe_float(rating)
    count = (
        _safe_get(d, "customerReviews", "count", "displayValue") or
        _safe_get(d, "customerReviews", "count")
    )
    try:
        info["review_count"] = int(str(count).replace(",", "")) if count else 0
    except (ValueError, TypeError):
        info["review_count"] = 0

    # ── Browse Nodes / Sales Rank ─────────────────────────────────────────────
    # FIX: browseNodeInfo → browseNodes → displayName / salesRank (all camelCase)
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
        # FIX: websiteSalesRank not website_sales_rank
        info["sales_rank"]          = int(_safe_float(
            _safe_get(d, "browseNodeInfo", "websiteSalesRank", "salesRank")))
        info["sales_rank_category"] = _safe_get(
            d, "browseNodeInfo", "websiteSalesRank", "displayName") or ""

    info["affiliate_link"] = build_affiliate_link(info["asin"])
    return info


# ══════════════════════════════════════════════════════════════════════════════
# Public helpers
# ══════════════════════════════════════════════════════════════════════════════

def build_affiliate_link(asin: str) -> str:
    tag = PARTNER_TAG or "defaulttag-21"
    return f"https://www.amazon.in/dp/{asin}?tag={tag}"


def extract_asin(text: str) -> tuple[str | None, str | None]:
    text = text.strip()
    # Plain search URL — no ASIN
    if re.search(r"amazon\.[a-z.]+/s[/?]", text, re.IGNORECASE):
        return None, "search"
    patterns = [
        r"amazon\.[a-z.]+/(?:dp|gp/product|exec/obidos/ASIN)/([A-Z0-9]{10})",
        r"amazon\.[a-z.]+/[^/]+/dp/([A-Z0-9]{10})",
        r"asin=([A-Z0-9]{10})",
        r"\b(B[A-Z0-9]{9})\b",   # FIX: require starts with B
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
# API calls
# ══════════════════════════════════════════════════════════════════════════════

def get_product_info(asin: str) -> dict:
    with _cache_lock:
        if asin in _cache:
            data, ts = _cache[asin]
            if time.time() - ts < _CACHE_TTL:
                return data

    try:
        api = _get_api()
        from get_items_request_content import GetItemsRequestContent
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
        req  = GetItemsRequestContent(
            partner_tag=PARTNER_TAG,
            item_ids=[asin],
            resources=resources,
        )
        resp = api.get_items(x_marketplace=MARKETPLACE, get_items_request_content=req)
        rd   = resp.to_dict() if hasattr(resp, "to_dict") else {}
        ir   = rd.get("itemsResult") or {}
        items = ir.get("items") or []
        if not items:
            raise ValueError(f"Product not found: {asin}")
        info = _parse_item(items[0])
        with _cache_lock:
            _cache[asin] = (info, time.time())
        return info

    except Exception as e:
        logger.error("get_product_info(%s) error: %s", asin, e)
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
        api = _get_api()
        from search_items_request_content import SearchItemsRequestContent
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
        kwargs: dict = dict(
            partner_tag=PARTNER_TAG,
            keywords=query,
            search_index=search_index,
            item_count=min(item_count, 10),
            resources=resources,
        )
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

        req  = SearchItemsRequestContent(**kwargs)
        resp = api.search_items(x_marketplace=MARKETPLACE, search_items_request_content=req)
        rd   = resp.to_dict() if hasattr(resp, "to_dict") else {}
        sr   = rd.get("searchResult") or {}
        items = sr.get("items") or []
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
    """Tiered fallback — tries lower discount thresholds if nothing is found."""
    thresholds = [t for t in [min_saving_pct, 20, 10, 0] if t <= min_saving_pct or t == 0]
    seen: set = set()
    thresholds = list(dict.fromkeys(thresholds))   # deduplicate while preserving order

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
        logger.info("search_deals: 0 results at %d%% threshold, trying lower", threshold)
    return []


def get_lightning_deals(keywords: str = "fashion deals", item_count: int = 10) -> list[dict]:
    results = search_products(query=keywords, item_count=item_count)
    return [r for r in results if r.get("is_lightning_deal")]


def get_product_variations(asin: str) -> list[dict]:
    try:
        api = _get_api()
        from get_variations_request_content import GetVariationsRequestContent
        resources = [
            "images.primary.medium",
            "itemInfo.title",
            "offersV2.listings.price",
            "variationAttributes",
        ]
        req  = GetVariationsRequestContent(
            partner_tag=PARTNER_TAG,
            asin=asin,
            resources=resources,
        )
        resp = api.get_variations(x_marketplace=MARKETPLACE, get_variations_request_content=req)
        rd   = resp.to_dict() if hasattr(resp, "to_dict") else {}
        vr   = rd.get("variationsResult") or rd.get("variations_result") or {}
        items = vr.get("items") or []

        variants = []
        for item in items:
            info     = _parse_item(item)
            var_attrs = item.get("variationAttributes") or item.get("variation_attributes") or []
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

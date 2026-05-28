import os
import re
import time
import logging
import requests
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

CREDENTIAL_ID      = os.environ.get("AMAZON_CREDENTIAL_ID", "")
CREDENTIAL_SECRET  = os.environ.get("AMAZON_CREDENTIAL_SECRET", "")
CREDENTIAL_VERSION = os.environ.get("AMAZON_CREDENTIAL_VERSION", "v1")
PARTNER_TAG        = os.environ.get("AMAZON_PARTNER_TAG", "")
MARKETPLACE        = os.environ.get("AMAZON_MARKETPLACE", "www.amazon.in")

IST = timezone(timedelta(hours=5, minutes=30))

_cache: dict = {}
_CACHE_TTL = 900

_api_client = None
_api_client_lock = __import__("threading").Lock()


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


def _parse_item(item) -> dict:
    d = item.to_dict() if hasattr(item, "to_dict") else item
    info: dict = {"asin": d.get("asin", "")}

    title_obj = _safe_get(d, "item_info", "title", "display_value")
    info["title"] = title_obj or ""

    brand_obj = _safe_get(d, "item_info", "by_line_info", "brand", "display_value")
    info["brand"] = brand_obj or ""

    cat_obj = _safe_get(d, "item_info", "classifications", "product_group", "display_value")
    info["category"] = cat_obj or ""

    features_raw = _safe_get(d, "item_info", "features", "display_values") or []
    info["features"] = [str(f) for f in features_raw[:8]]

    image_obj = _safe_get(d, "images", "primary", "large", "url") or \
                _safe_get(d, "images", "primary", "medium", "url")
    info["image_url"] = image_obj or ""

    listings = _safe_get(d, "offers_v2", "listings") or []
    listing = listings[0] if listings else {}

    price_obj = _safe_get(listing, "price")
    if price_obj:
        amount = _safe_get(price_obj, "amount")
        currency = _safe_get(price_obj, "currency", default="INR")
        savings = _safe_get(price_obj, "savings")
        mrp = _safe_get(price_obj, "original_price", "amount")
        discount_pct = _safe_get(price_obj, "discount_percent") or \
                       _safe_get(price_obj, "savings_percent")

        if amount:
            info["price_amount"] = float(amount)
            info["price"] = f"₹{float(amount):,.0f}"
        else:
            info["price_amount"] = 0.0
            info["price"] = ""

        if mrp:
            info["mrp"] = f"₹{float(mrp):,.0f}"
            info["mrp_amount"] = float(mrp)
        else:
            info["mrp"] = ""
            info["mrp_amount"] = 0.0

        if discount_pct:
            info["discount_pct"] = str(int(float(discount_pct)))
        else:
            info["discount_pct"] = ""

        if savings:
            sav_amt = _safe_get(savings, "amount")
            info["savings"] = f"₹{float(sav_amt):,.0f}" if sav_amt else ""
        else:
            info["savings"] = ""
    else:
        info["price_amount"] = 0.0
        info["price"] = ""
        info["mrp"] = ""
        info["mrp_amount"] = 0.0
        info["discount_pct"] = ""
        info["savings"] = ""

    avail = _safe_get(listing, "availability", "message") or \
            _safe_get(listing, "availability", "type")
    info["availability"] = avail or ""

    cond = _safe_get(listing, "condition", "display_value")
    info["condition"] = cond or ""

    merchant_name = _safe_get(listing, "merchant_info", "name") or ""
    info["merchant_name"] = merchant_name
    info["is_amazon_seller"] = merchant_name.lower() in ("amazon", "amazon seller services pvt ltd")

    delivery = _safe_get(listing, "delivery_info", "is_prime_eligible")
    if delivery is None:
        delivery = _safe_get(listing, "is_prime_eligible")
    info["is_prime"] = bool(delivery)

    loyalty = _safe_get(listing, "loyalty_points", "points")
    info["loyalty_points"] = int(loyalty) if loyalty else 0

    deal = listing.get("deal_details") or {}
    if isinstance(deal, dict):
        deal_type = deal.get("deal_type") or deal.get("type", "")
        info["is_lightning_deal"] = str(deal_type).upper() == "LIGHTNING_DEAL"
        end_time = deal.get("end_time") or deal.get("endTime")
        if end_time:
            try:
                if isinstance(end_time, str):
                    from dateutil.parser import parse as dtparse
                    end_dt = dtparse(end_time).astimezone(IST)
                    info["deal_end_time"] = end_dt.strftime("%d %b %I:%M %p IST")
                else:
                    info["deal_end_time"] = str(end_time)
            except Exception:
                info["deal_end_time"] = str(end_time)
        else:
            info["deal_end_time"] = ""
        dp = deal.get("deal_price") or deal.get("dealPrice")
        info["deal_price"] = f"₹{float(dp):,.0f}" if dp else ""
    else:
        info["is_lightning_deal"] = False
        info["deal_end_time"] = ""
        info["deal_price"] = ""

    rating = _safe_get(d, "customer_reviews", "star_rating", "value")
    info["rating"] = float(rating) if rating else 0.0
    count = _safe_get(d, "customer_reviews", "count", "display_value") or \
            _safe_get(d, "customer_reviews", "count")
    info["review_count"] = int(str(count).replace(",", "")) if count else 0

    browse_nodes = _safe_get(d, "browse_node_info", "browse_nodes") or []
    if browse_nodes:
        node = browse_nodes[0] if isinstance(browse_nodes, list) else browse_nodes
        if isinstance(node, dict):
            info["sales_rank_category"] = node.get("display_name", "")
            sr = node.get("sales_rank") or node.get("website_sales_rank")
            info["sales_rank"] = int(sr) if sr else 0
        else:
            info["sales_rank"] = 0
            info["sales_rank_category"] = ""
    else:
        web_rank = _safe_get(d, "browse_node_info", "website_sales_rank", "sales_rank")
        web_cat = _safe_get(d, "browse_node_info", "website_sales_rank", "display_name")
        info["sales_rank"] = int(web_rank) if web_rank else 0
        info["sales_rank_category"] = web_cat or ""

    info["affiliate_link"] = build_affiliate_link(info["asin"])
    return info


def build_affiliate_link(asin: str) -> str:
    tag = PARTNER_TAG or "defaulttag-21"
    return f"https://www.amazon.in/dp/{asin}?tag={tag}"


def extract_asin(text: str) -> tuple[str | None, str | None]:
    text = text.strip()
    search_pattern = r"amazon\.[a-z.]+/s[/?]"
    if re.search(search_pattern, text, re.IGNORECASE):
        return None, "search"
    patterns = [
        r"amazon\.[a-z.]+/(?:dp|gp/product|exec/obidos/ASIN)/([A-Z0-9]{10})",
        r"amazon\.[a-z.]+/[^/]+/dp/([A-Z0-9]{10})",
        r"asin=([A-Z0-9]{10})",
        r"\b([A-Z][A-Z0-9]{9})\b",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(1).upper(), None
    if "amzn.to" in text or "amzn.in" in text:
        try:
            url = re.search(r"https?://\S+", text)
            if url:
                r = requests.head(url.group(), allow_redirects=True, timeout=5)
                final = r.url
                m2 = re.search(r"/dp/([A-Z0-9]{10})", final, re.IGNORECASE)
                if m2:
                    return m2.group(1).upper(), None
        except Exception:
            pass
    return None, None


def get_product_info(asin: str) -> dict:
    if asin in _cache:
        data, ts = _cache[asin]
        if time.time() - ts < _CACHE_TTL:
            return data

    try:
        api = _get_api()
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
        resp = api.get_items(x_marketplace=MARKETPLACE, get_items_request_content=req)
        resp_dict = resp.to_dict() if hasattr(resp, "to_dict") else {}
        items_result = resp_dict.get("items_result") or {}
        items = items_result.get("items") or []
        if not items:
            raise ValueError(f"Product not found: {asin}")

        info = _parse_item(items[0])
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
        api = _get_api()
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
        resp = api.search_items(x_marketplace=MARKETPLACE, search_items_request_content=req)
        resp_dict = resp.to_dict() if hasattr(resp, "to_dict") else {}
        search_result = resp_dict.get("search_result") or {}
        items = search_result.get("items") or []
        return [_parse_item(i) for i in items]

    except Exception as e:
        logger.error("search_products(%s) error: %s", query, e)
        return []


def search_deals(category_keywords: list[str], min_saving_pct: int = 30,
                 item_count: int = 5, search_index: str = "All") -> list[dict]:
    all_results = []
    seen_asins = set()
    for kw in category_keywords[:3]:
        results = search_products(
            query=kw, item_count=item_count,
            min_saving_pct=min_saving_pct,
            sort_by="FEATURED",
            search_index=search_index,
        )
        for r in results:
            if r["asin"] not in seen_asins and r.get("price_amount", 0) > 0:
                all_results.append(r)
                seen_asins.add(r["asin"])
        if len(all_results) >= item_count:
            break

    if not all_results and min_saving_pct > 40:
        return search_deals(category_keywords, min_saving_pct=40, item_count=item_count)

    return all_results[:item_count]


def get_lightning_deals(keywords: str = "fashion deals", item_count: int = 10) -> list[dict]:
    results = search_products(query=keywords, item_count=item_count)
    lightning = [r for r in results if r.get("is_lightning_deal")]
    return lightning


def get_product_variations(asin: str) -> list[dict]:
    try:
        api = _get_api()
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
        resp = api.get_variations(x_marketplace=MARKETPLACE, get_variations_request_content=req)
        resp_dict = resp.to_dict() if hasattr(resp, "to_dict") else {}
        variations_result = resp_dict.get("variations_result") or {}
        items = variations_result.get("items") or []
        variants = []
        for item in items:
            info = _parse_item(item)
            var_attrs = item.get("variation_attributes") or []
            for attr in var_attrs:
                name = (attr.get("name") or "").lower()
                val = attr.get("value") or ""
                if name in ("color", "colour"):
                    info["color"] = val
                elif name in ("size",):
                    info["size"] = val
                elif name in ("style", "storage", "storagesize"):
                    info["storage"] = val
            variants.append(info)
        return variants

    except Exception as e:
        logger.error("get_product_variations(%s) error: %s", asin, e)
        return []

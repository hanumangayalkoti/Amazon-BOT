import os
import re
import time
import requests

CREDENTIAL_ID      = os.environ["CREDENTIAL_ID"]
CREDENTIAL_SECRET  = os.environ["CREDENTIAL_SECRET"]
CREDENTIAL_VERSION = os.environ.get("CREDENTIAL_VERSION", "3.2")
PARTNER_TAG        = os.environ.get("PARTNER_TAG", "dealskoti-21")
MARKETPLACE        = os.environ.get("MARKETPLACE", "www.amazon.in")

VERSION_TOKEN_URLS = {
    "2.1": "https://creatorsapi.auth.us-east-1.amazoncognito.com/oauth2/token",
    "2.2": "https://creatorsapi.auth.eu-south-2.amazoncognito.com/oauth2/token",
    "2.3": "https://creatorsapi.auth.us-west-2.amazoncognito.com/oauth2/token",
    "3.1": "https://api.amazon.com/auth/o2/token",
    "3.2": "https://api.amazon.co.uk/auth/o2/token",
    "3.3": "https://api.amazon.co.jp/auth/o2/token",
}

SCOPE    = "creatorsapi::default" if CREDENTIAL_VERSION.startswith("3.") else "creatorsapi/default"
API_BASE = "https://creatorsapi.amazon"

ITEMS_ENDPOINT  = f"{API_BASE}/catalog/v1/getItems"
SEARCH_ENDPOINT = f"{API_BASE}/catalog/v1/searchItems"

ASIN_PATTERN = re.compile(r"/(?:dp|gp/product|exec/obidos/ASIN|o/ASIN)/([A-Z0-9]{10})")

_token_cache: dict = {"token": None, "expires_at": 0}

PRODUCT_RESOURCES = [
    "images.primary.large",
    "itemInfo.title",
    "itemInfo.byLineInfo",
    "itemInfo.features",
    "itemInfo.classifications",
    "offersV2.listings.price",
    "offersV2.listings.availability",
    "offersV2.listings.condition",
    "offersV2.listings.dealDetails",
    "customerReviews.count",
    "customerReviews.starRating",
]

SEARCH_RESOURCES = [
    "images.primary.medium",
    "itemInfo.title",
    "itemInfo.byLineInfo",
    "itemInfo.classifications",
    "offersV2.listings.price",
    "offersV2.listings.availability",
    "offersV2.listings.dealDetails",
    "customerReviews.starRating",
    "customerReviews.count",
]


def resolve_url(url: str) -> str:
    try:
        resp = requests.get(
            url,
            allow_redirects=True,
            timeout=10,
            stream=True,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        final_url = resp.url
        resp.close()
        return final_url
    except Exception:
        return url


def extract_asin(text: str) -> tuple:
    text = text.strip()

    if re.fullmatch(r"[A-Z0-9]{10}", text):
        return text, None

    if re.search(r"amzn\.(to|in)/", text):
        text = resolve_url(text)

    if "/s?" in text or "/s/" in text or "field-keywords" in text:
        return None, "search"

    match = ASIN_PATTERN.search(text)
    if match:
        return match.group(1), None

    q_match = re.search(r"[?&]ASIN=([A-Z0-9]{10})", text)
    if q_match:
        return q_match.group(1), None

    return None, "invalid"


def build_affiliate_link(asin: str) -> str:
    tag = PARTNER_TAG.strip().rstrip("?&/ ")
    return f"https://www.amazon.in/dp/{asin}?tag={tag}"


def _get_token() -> str:
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]

    token_url = VERSION_TOKEN_URLS.get(CREDENTIAL_VERSION)
    if not token_url:
        raise RuntimeError(f"Unsupported CREDENTIAL_VERSION: {CREDENTIAL_VERSION}")

    resp = requests.post(
        token_url,
        data={"grant_type": "client_credentials", "scope": SCOPE},
        auth=(CREDENTIAL_ID, CREDENTIAL_SECRET),
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    _token_cache["token"] = data["access_token"]
    _token_cache["expires_at"] = now + data.get("expires_in", 3600) - 60
    return _token_cache["token"]


def _parse_item(item: dict, asin: str) -> dict:
    data: dict = {"asin": asin}

    title_val = item.get("itemInfo", {}).get("title", {})
    if title_val:
        data["title"] = title_val.get("displayValue", "")

    brand_val = item.get("itemInfo", {}).get("byLineInfo", {}).get("brand", {})
    if brand_val:
        data["brand"] = brand_val.get("displayValue", "")

    class_val = item.get("itemInfo", {}).get("classifications", {})
    pg = class_val.get("productGroup", {}) if class_val else {}
    if pg:
        data["category"] = pg.get("displayValue", "")

    feat_val = item.get("itemInfo", {}).get("features", {})
    if feat_val:
        vals = feat_val.get("displayValues", [])
        if vals:
            data["features"] = vals[:5]

    img = item.get("images", {}).get("primary", {}).get("large", {})
    if img:
        data["image_url"] = img.get("url", "")

    listings = item.get("offersV2", {}).get("listings", [])
    if listings:
        listing = listings[0]
        price_obj = listing.get("price", {})
        money = price_obj.get("money", {})
        if money:
            data["price"] = money.get("displayAmount", "")
            data["price_amount"] = float(money.get("amount", 0))
        savings = price_obj.get("savings", {})
        sav_money = savings.get("money", {})
        if sav_money:
            data["savings"] = sav_money.get("displayAmount", "")
        sav_pct = savings.get("percentage")
        if sav_pct is not None:
            data["discount_pct"] = sav_pct
        avail = listing.get("availability", {})
        if avail:
            data["availability"] = avail.get("message", "")

    cr = item.get("customerReviews", {})
    if cr.get("count") is not None:
        data["review_count"] = cr["count"]
    star = cr.get("starRating", {})
    if star:
        data["rating"] = star.get("value", "")

    data["affiliate_link"] = build_affiliate_link(asin)
    return data


def get_product_info(asin: str) -> dict:
    token = _get_token()
    payload = {
        "partnerTag": PARTNER_TAG,
        "itemIds": [asin],
        "resources": PRODUCT_RESOURCES,
    }
    try:
        resp = requests.post(
            ITEMS_ENDPOINT,
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "x-marketplace": MARKETPLACE,
                "Content-Type": "application/json",
            },
            timeout=20,
        )
    except requests.Timeout:
        raise RuntimeError("Amazon API timeout — thodi der baad try karo.")
    except requests.ConnectionError:
        raise RuntimeError("Amazon API se connect nahi ho pa raha.")

    if resp.status_code == 403:
        raise RuntimeError("Access denied. API credentials check karo.")
    if resp.status_code not in (200, 206):
        raise RuntimeError(f"Amazon API error: {resp.status_code}")

    body  = resp.json()
    items = body.get("itemsResult", {}).get("items", [])

    if not items:
        errors = body.get("errors", [])
        msg = errors[0].get("message", "Product not found.") if errors else "Product not found or unavailable."
        raise ValueError(msg)

    return _parse_item(items[0], asin)


def search_items(query: str, count: int = 5) -> list:
    token = _get_token()
    payload = {
        "partnerTag": PARTNER_TAG,
        "keywords": query,
        "searchIndex": "All",
        "itemCount": count,
        "resources": SEARCH_RESOURCES,
    }
    try:
        resp = requests.post(
            SEARCH_ENDPOINT,
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "x-marketplace": MARKETPLACE,
                "Content-Type": "application/json",
            },
            timeout=20,
        )
    except requests.Timeout:
        raise RuntimeError("Amazon search timeout — thodi der baad try karo.")
    except requests.ConnectionError:
        raise RuntimeError("Amazon API se connect nahi ho pa raha.")

    if resp.status_code not in (200, 206):
        raise RuntimeError(f"Amazon search error: {resp.status_code}")

    body  = resp.json()
    items = body.get("searchResult", {}).get("items", [])

    results = []
    for item in items:
        asin = item.get("asin", "")
        if asin:
            results.append(_parse_item(item, asin))
    return results

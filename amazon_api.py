import os
import re
import time
import requests

CREDENTIAL_ID = os.environ["CREDENTIAL_ID"]
CREDENTIAL_SECRET = os.environ["CREDENTIAL_SECRET"]
CREDENTIAL_VERSION = os.environ.get("CREDENTIAL_VERSION", "3.2")
PARTNER_TAG = os.environ.get("PARTNER_TAG", "dealskoti-21")
MARKETPLACE = os.environ.get("MARKETPLACE", "www.amazon.in")

API_BASE = "https://creatorsapi.amazon"
ITEMS_ENDPOINT = f"{API_BASE}/catalog/v1/getItems"

VERSION_TOKEN_URLS = {
    "2.1": "https://creatorsapi.auth.us-east-1.amazoncognito.com/oauth2/token",
    "2.2": "https://creatorsapi.auth.eu-south-2.amazoncognito.com/oauth2/token",
    "2.3": "https://creatorsapi.auth.us-west-2.amazoncognito.com/oauth2/token",
    "3.1": "https://api.amazon.com/auth/o2/token",
    "3.2": "https://api.amazon.co.uk/auth/o2/token",
    "3.3": "https://api.amazon.co.jp/auth/o2/token",
}

SCOPE = "creatorsapi::default" if CREDENTIAL_VERSION.startswith("3.") else "creatorsapi/default"

_token_cache: dict = {"token": None, "expires_at": 0}

ASIN_PATTERN = re.compile(r"/(?:dp|gp/product|exec/obidos/ASIN|o/ASIN)/([A-Z0-9]{10})")


def resolve_url(url: str) -> str:
    try:
        resp = requests.head(url, allow_redirects=True, timeout=10,
                             headers={"User-Agent": "Mozilla/5.0"})
        return resp.url
    except Exception:
        try:
            resp = requests.get(url, allow_redirects=True, timeout=10,
                                headers={"User-Agent": "Mozilla/5.0"})
            return resp.url
        except Exception:
            return url


def extract_asin(text: str) -> tuple:
    text = text.strip()

    if re.fullmatch(r"[A-Z0-9]{10}", text):
        return text, None

    if re.search(r"amzn\.(to|in)/", text):
        text = resolve_url(text)

    if "/s?" in text or "/s/" in text:
        return None, "search"

    match = ASIN_PATTERN.search(text)
    if match:
        return match.group(1), None

    q_match = re.search(r"[?&]ASIN=([A-Z0-9]{10})", text)
    if q_match:
        return q_match.group(1), None

    return None, "invalid"


def build_affiliate_link(asin: str) -> str:
    return f"https://www.amazon.in/dp/{asin}?tag={PARTNER_TAG}"


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


def get_product_info(asin: str) -> dict:
    token = _get_token()

    payload = {
        "partnerTag": PARTNER_TAG,
        "itemIds": [asin],
        "resources": [
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
        ],
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
        raise RuntimeError("Amazon API timeout. Thodi der baad try karo.")
    except requests.ConnectionError:
        raise RuntimeError("Amazon API se connect nahi ho pa raha.")

    if resp.status_code == 403:
        raise RuntimeError("Access denied. Check your API credentials.")
    if resp.status_code not in (200, 206):
        raise RuntimeError(f"Amazon API error: {resp.status_code}")

    body = resp.json()
    items = body.get("itemsResult", {}).get("items", [])

    # Even if items list is empty, check errors block for partial data
    if not items:
        errors = body.get("errors", [])
        if errors:
            msg = errors[0].get("message", "Product not found.")
            raise ValueError(msg)
        raise ValueError("Product not found or unavailable.")

    item = items[0]
    data: dict = {"asin": asin}

    # Title
    title_val = item.get("itemInfo", {}).get("title", {})
    if title_val:
        data["title"] = title_val.get("displayValue", "")

    # Brand
    brand_val = item.get("itemInfo", {}).get("byLineInfo", {}).get("brand", {})
    if brand_val:
        data["brand"] = brand_val.get("displayValue", "")

    # Category
    class_val = item.get("itemInfo", {}).get("classifications", {})
    pg = class_val.get("productGroup", {}) if class_val else {}
    if pg:
        data["category"] = pg.get("displayValue", "")

    # Features — top 3
    feat_val = item.get("itemInfo", {}).get("features", {})
    if feat_val:
        vals = feat_val.get("displayValues", [])
        if vals:
            data["features"] = vals[:3]

    # Image
    img = item.get("images", {}).get("primary", {}).get("large", {})
    if img:
        data["image_url"] = img.get("url", "")

    # Offers — optional, product may be unavailable
    listings = item.get("offersV2", {}).get("listings", [])
    if listings:
        listing = listings[0]

        price_obj = listing.get("price", {})
        money = price_obj.get("money", {})
        if money:
            data["price"] = money.get("displayAmount", "")
            data["price_amount"] = money.get("amount", 0)

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

        deal = listing.get("dealDetails", {})
        if deal:
            data["deal_type"] = deal.get("dealType", "")

    # Ratings
    cr = item.get("customerReviews", {})
    if cr.get("count") is not None:
        data["review_count"] = cr["count"]
    star = cr.get("starRating", {})
    if star:
        data["rating"] = star.get("value", "")

    data["affiliate_link"] = build_affiliate_link(asin)
    return data

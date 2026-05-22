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

ASIN_REGEX = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})")


def extract_asin(text: str) -> str | None:
    text = text.strip()
    if re.fullmatch(r"[A-Z0-9]{10}", text):
        return text
    match = ASIN_REGEX.search(text)
    if match:
        return match.group(1)
    return None


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
        data={
            "grant_type": "client_credentials",
            "scope": SCOPE,
        },
        auth=(CREDENTIAL_ID, CREDENTIAL_SECRET),
        timeout=10,
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
            "offersV2.listings.price",
            "offersV2.listings.availability",
            "customerReviews.count",
            "customerReviews.starRating",
        ],
    }

    resp = requests.post(
        ITEMS_ENDPOINT,
        json=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "x-marketplace": MARKETPLACE,
            "Content-Type": "application/json",
        },
        timeout=15,
    )

    if resp.status_code == 404:
        raise ValueError("Product not found or unavailable.")
    if resp.status_code == 403:
        raise RuntimeError("Access denied. Check your API credentials.")
    resp.raise_for_status()

    body = resp.json()
    items_result = body.get("itemsResult", {})
    items = items_result.get("items", [])

    if not items:
        raise ValueError("Product not found or unavailable.")

    item = items[0]
    data: dict = {"asin": asin}

    title_obj = item.get("itemInfo", {}).get("title", {})
    if title_obj:
        data["title"] = title_obj.get("displayValue", "")

    brand_obj = item.get("itemInfo", {}).get("byLineInfo", {}).get("brand", {})
    if brand_obj:
        data["brand"] = brand_obj.get("displayValue", "")

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

    cr = item.get("customerReviews", {})
    if cr.get("count") is not None:
        data["review_count"] = cr["count"]
    star = cr.get("starRating", {})
    if star:
        data["rating"] = star.get("value", "")

    data["affiliate_link"] = build_affiliate_link(asin)
    return data

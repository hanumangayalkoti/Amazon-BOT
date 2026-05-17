async def fetch_product_data(link: str) -> dict | None:
    """
    Fetch product data from a product URL.

    Replace the placeholder below with your actual API logic.
    You can use services like:
      - Amazon Product Advertising API (PA-API 5.0)
      - RapidAPI Amazon product scrapers
      - Any other product data API

    Expected return format:
    {
        "title": "Product Name",
        "price": "$19.99",
        "image": "https://example.com/image.jpg",
        "asin": "B08XYZ1234",
        "affiliate": "https://amazon.com/dp/B08XYZ1234?tag=yourtag-20"
    }
    Returns None if the link is invalid or the product is not found.
    """

    # -------------------------------------------------------
    # TODO: Replace this placeholder with your actual API call
    # -------------------------------------------------------
    # Example using config credentials:
    #
    # from config import API_KEY, API_SECRET, API_PARTNER_TAG
    # import httpx
    #
    # async with httpx.AsyncClient() as client:
    #     response = await client.get(
    #         "https://your-api-endpoint.com/product",
    #         params={"url": link, "api_key": API_KEY},
    #     )
    #     data = response.json()
    #     return {
    #         "title": data.get("title"),
    #         "price": data.get("price"),
    #         "image": data.get("image"),
    #         "asin": data.get("asin"),
    #         "affiliate": data.get("affiliate_url"),
    #     }
    # -------------------------------------------------------

    return {
        "title": "Sample Product Title",
        "price": "$29.99",
        "image": "https://via.placeholder.com/300",
        "asin": "B08EXAMPLE",
        "affiliate": link,
    }

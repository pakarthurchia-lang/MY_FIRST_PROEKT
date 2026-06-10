"""
Каталог товаров вода24.рф.
Чтобы добавить новый товар — скопируй блок и заполни поля.
"""

CATALOG: dict[str, dict] = {
    "elitnaya_19": {
        "name": "Элитная 19 л",
        "url": "https://xn--24-6kcajmz4cyak6czf.xn--p1ai/shop/voda-elitnaya-19-l/?sku=23",
        "keywords": ["элитная", "элит", "19"],
    },
    # Пример как добавить второй товар:
    # "gornaya_19": {
    #     "name": "Горная 19 л",
    #     "url": "https://xn--24-6kcajmz4cyak6czf.xn--p1ai/shop/voda-gornaya-19-l/",
    #     "keywords": ["горная", "гор"],
    # },
}

DEFAULT_PRODUCT = "elitnaya_19"


def find_product(text: str) -> tuple[str, dict]:
    """
    Find best matching product by keywords in text.
    Returns (product_key, product_dict).
    Falls back to DEFAULT_PRODUCT if nothing matched.
    """
    t = text.lower()
    for key, product in CATALOG.items():
        if any(kw in t for kw in product["keywords"]):
            return key, product
    return DEFAULT_PRODUCT, CATALOG[DEFAULT_PRODUCT]

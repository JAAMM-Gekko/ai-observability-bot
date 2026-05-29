"""
Product lookup tool for the persona chatbot agent.

Loads product data from personas/products.json and provides search/filter
functions that the agent can invoke as a "tool call" when users ask
product-related questions.
"""

import json
from pathlib import Path

PRODUCTS_PATH = Path(__file__).resolve().parent.parent / "personas" / "products.json"

_PRODUCT_TRIGGER_KEYWORDS = [
    "product", "recommend", "suggestion", "what do you have",
    "what's available", "what is available", "show me", "looking for",
    "edible", "gummy", "gummies", "chocolate", "drink", "beverage",
    "flower", "bud", "preroll", "pre-roll", "joint", "cartridge", "cart",
    "vape", "disposable", "concentrate", "rosin", "dab", "wax",
    "topical", "balm", "cream", "pipe", "paper", "rolling",
    "sativa", "indica", "hybrid", "cheap", "under $", "best",
    "paraphernalia", "accessories", "accessory",
    "what's good", "whats good", "what do you got", "in stock",
    "menu", "buy", "purchase", "price",
]


def is_product_query(query: str) -> bool:
    """Detect if the user query is product-related and should trigger the product tool."""
    query_lower = query.lower()
    return any(kw in query_lower for kw in _PRODUCT_TRIGGER_KEYWORDS)

_products: list[dict] | None = None


def _load_products() -> list[dict]:
    global _products
    if _products is not None:
        return _products
    with open(PRODUCTS_PATH, "r", encoding="utf-8") as f:
        _products = json.load(f)
    return _products


def get_all_products() -> list[dict]:
    return _load_products()


def search_products(query: str) -> list[dict]:
    """
    Search products by keyword match against name, category, brand,
    strain_type, and description fields.
    """
    products = _load_products()
    query_lower = query.lower()
    terms = query_lower.split()

    results = []
    for product in products:
        searchable = " ".join([
            product.get("name", ""),
            product.get("category", ""),
            product.get("brand", ""),
            product.get("strain_type", ""),
            product.get("description", ""),
        ]).lower()

        if all(term in searchable for term in terms):
            results.append(product)

    return results


def filter_by_category(category: str) -> list[dict]:
    """Filter products by category (case-insensitive partial match)."""
    products = _load_products()
    cat_lower = category.lower()
    return [p for p in products if cat_lower in p.get("category", "").lower()]


def filter_by_strain(strain_type: str) -> list[dict]:
    """Filter products by strain type (Sativa, Indica, Hybrid)."""
    products = _load_products()
    strain_lower = strain_type.lower()
    return [p for p in products if p.get("strain_type", "").lower() == strain_lower]


def filter_by_price(max_price: float) -> list[dict]:
    """Filter products under a given price."""
    products = _load_products()
    return [p for p in products if p.get("price", 999) <= max_price]


def format_product_for_agent(product: dict) -> str:
    """Format a single product into a readable string for the LLM."""
    lines = [
        f"- **{product['name']}** ({product['category']})",
        f"  Brand: {product['brand']} | Price: ${product['price']:.2f} (was ${product['original_price']:.2f})",
        f"  Weight: {product['weight']} | THC: {product['thc']} | CBD: {product['cbd']}",
        f"  Strain: {product['strain_type']}",
        f"  Description: {product['description']}",
    ]
    return "\n".join(lines)


def format_products_for_agent(products: list[dict]) -> str:
    """Format a list of products into a block the LLM can reference."""
    if not products:
        return "No matching products found in inventory."
    return "\n\n".join(format_product_for_agent(p) for p in products)


def run_product_tool_call(user_query: str) -> str:
    """
    Main entry point: given a user query, determine what product info
    to look up and return formatted results for the agent.

    This simulates a tool call -- the agent gets back structured product
    data it can use to make recommendations.
    """
    query_lower = user_query.lower()

    categories_map = {
        "edible": "Edible",
        "gummy": "Edible Solid",
        "gummies": "Edible Solid",
        "chocolate": "Edible Solid",
        "drink": "Edible Liquid",
        "beverage": "Edible Liquid",
        "elixir": "Edible Liquid",
        "flower": "Flower",
        "bud": "Flower",
        "preroll": "Preroll",
        "pre-roll": "Preroll",
        "joint": "Preroll",
        "cartridge": "Cartridge",
        "cart": "Cartridge",
        "vape": "Disposable Vapes",
        "disposable": "Disposable Vapes",
        "concentrate": "Concentrate",
        "rosin": "Concentrate",
        "dab": "Concentrate",
        "wax": "Concentrate",
        "topical": "Topical",
        "balm": "Topical",
        "cream": "Topical",
        "pipe": "Paraphernalia",
        "paper": "Paraphernalia",
        "rolling": "Paraphernalia",
        "accessory": "Paraphernalia",
        "accessories": "Paraphernalia",
        "paraphernalia": "Paraphernalia",
    }

    strain_keywords = {
        "sativa": "Sativa",
        "indica": "Indica",
        "hybrid": "Hybrid",
        "energetic": "Sativa",
        "uplifting": "Sativa",
        "relaxing": "Indica",
        "couch": "Indica",
        "balanced": "Hybrid",
    }

    # Try category match
    for keyword, category in categories_map.items():
        if keyword in query_lower:
            results = filter_by_category(category)
            if results:
                return (
                    f"[TOOL: product_search] Found {len(results)} product(s) "
                    f"in category '{category}':\n\n"
                    + format_products_for_agent(results)
                )

    # Try strain match
    for keyword, strain in strain_keywords.items():
        if keyword in query_lower:
            results = filter_by_strain(strain)
            if results:
                return (
                    f"[TOOL: product_search] Found {len(results)} {strain} product(s):\n\n"
                    + format_products_for_agent(results)
                )

    # Try price filter
    import re
    price_match = re.search(r'under\s*\$?(\d+)', query_lower)
    if price_match:
        max_price = float(price_match.group(1))
        results = filter_by_price(max_price)
        if results:
            return (
                f"[TOOL: product_search] Found {len(results)} product(s) "
                f"under ${max_price:.0f}:\n\n"
                + format_products_for_agent(results)
            )

    # General keyword search
    results = search_products(user_query)
    if results:
        return (
            f"[TOOL: product_search] Found {len(results)} matching product(s):\n\n"
            + format_products_for_agent(results)
        )

    # Fallback: return all products
    all_products = get_all_products()
    return (
        f"[TOOL: product_search] Showing all {len(all_products)} available products:\n\n"
        + format_products_for_agent(all_products)
    )

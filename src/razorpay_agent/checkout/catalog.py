from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Product:
    id: str
    title: str
    category: str
    unit_amount_paise: int

    def __post_init__(self) -> None:
        for field in ("id", "title", "category"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be a non-empty string")
        if isinstance(self.unit_amount_paise, bool) or not isinstance(
            self.unit_amount_paise, int
        ) or self.unit_amount_paise <= 0:
            raise ValueError("unit_amount_paise must be a positive integer")


DEMO_CATALOG: tuple[Product, ...] = (
    Product("sku-tee", "Classic Cotton Tee", "apparel", 99900),
    Product("sku-hoodie", "Zip-Up Hoodie", "apparel", 249900),
    Product("sku-socks", "Merino Wool Socks", "apparel", 49900),
    Product("sku-headphones", "Wireless Headphones", "electronics", 499900),
    Product("sku-charger", "65W Fast Charger", "electronics", 149900),
)


def find_product(catalog: tuple[Product, ...], product_id: str) -> Product | None:
    for product in catalog:
        if product.id == product_id:
            return product
    return None

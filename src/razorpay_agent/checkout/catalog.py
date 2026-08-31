from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Product:
    id: str
    title: str
    category: str
    unit_amount_paise: int
    stagnant: bool = False
    days_in_stock: int | None = None

    def __post_init__(self) -> None:
        for field in ("id", "title", "category"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be a non-empty string")
        if isinstance(self.unit_amount_paise, bool) or not isinstance(
            self.unit_amount_paise, int
        ) or self.unit_amount_paise <= 0:
            raise ValueError("unit_amount_paise must be a positive integer")
        if not isinstance(self.stagnant, bool):
            raise ValueError("stagnant must be a boolean")
        if self.days_in_stock is not None and (
            isinstance(self.days_in_stock, bool)
            or not isinstance(self.days_in_stock, int)
            or self.days_in_stock < 0
        ):
            raise ValueError("days_in_stock must be a non-negative integer or None")


# `stagnant` is a STRUCTURAL FACT from the merchant's inventory data, never inferred
# by the decision layer (see architecture.md §4.8). It flows in from the catalog and
# is read by the bandit only as context — the bandit can never set or influence it.
DEMO_CATALOG: tuple[Product, ...] = (
    Product("sku-tee", "Classic Cotton Tee", "apparel", 99900),
    Product("sku-hoodie", "Zip-Up Hoodie", "apparel", 249900),
    Product("sku-socks", "Merino Wool Socks", "apparel", 49900),
    Product("sku-headphones", "Wireless Headphones", "electronics", 499900),
    Product("sku-charger", "65W Fast Charger", "electronics", 149900),
    Product(
        "sku-oldstock",
        "Last Season Jacket",
        "apparel",
        399900,
        stagnant=True,
        days_in_stock=120,
    ),
)


def find_product(catalog: tuple[Product, ...], product_id: str) -> Product | None:
    for product in catalog:
        if product.id == product_id:
            return product
    return None

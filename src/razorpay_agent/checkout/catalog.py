from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Product:
    id: str
    title: str
    category: str
    unit_amount_paise: int
    image_url: str = ""
    description: str = ""
    rating: float = 4.5
    reviews: int = 0
    stock: int = 50
    tags: tuple[str, ...] = ()
    stagnant: bool = False
    days_in_stock: int | None = None

    def __post_init__(self) -> None:
        for f in ("id", "title", "category"):
            v = getattr(self, f)
            if not isinstance(v, str) or not v.strip():
                raise ValueError(f"{f} must be a non-empty string")
        if isinstance(self.unit_amount_paise, bool) or not isinstance(self.unit_amount_paise, int) or self.unit_amount_paise <= 0:
            raise ValueError("unit_amount_paise must be a positive integer")
        if not isinstance(self.stagnant, bool):
            raise ValueError("stagnant must be a boolean")

    @property
    def price_inr(self) -> float:
        return self.unit_amount_paise / 100


DEMO_CATALOG: tuple[Product, ...] = (
    # ── Home ──────────────────────────────────────────────
    Product(
        "sku-candle", "Soy Wax Candle", "home", 89900,
        "https://images.unsplash.com/photo-1602607544982-63e8f5e30f73?w=600&h=600&fit=crop",
        description="Hand-poured soy wax candle with lavender and vanilla notes. Burns for 45 hours. Made from 100% natural ingredients with a cotton wick for a clean, even burn.",
        rating=4.7, reviews=128, stock=42,
        tags=("handmade", "natural", "aromatherapy"),
    ),
    Product(
        "sku-mug", "Ceramic Coffee Mug", "home", 59900,
        "https://images.unsplash.com/photo-1514228742587-6b1558fcca3d?w=600&h=600&fit=crop",
        description="Minimalist 350ml ceramic mug with matte finish. Microwave and dishwasher safe. Designed for the perfect morning pour.",
        rating=4.5, reviews=256, stock=89,
        tags=("minimalist", "microwave-safe", "ceramic"),
    ),
    Product(
        "sku-diffuser", "Ultrasonic Aroma Diffuser", "home", 149900,
        "https://images.unsplash.com/photo-1608571423902-eed4a5ad8108?w=600&h=600&fit=crop",
        description="300ml ultrasonic cool-mist diffuser with 7 LED color options. Auto-shutoff after 6 hours. Whisper-quiet operation for bedrooms and offices.",
        rating=4.8, reviews=94, stock=23,
        tags=("ultrasonic", "led", "auto-shutoff", "whisper-quiet"),
    ),
    # ── Personal Care ─────────────────────────────────────
    Product(
        "sku-shampoo", "Daily Shampoo", "personal_care", 64900,
        "https://images.unsplash.com/photo-1631729371254-42c2892f0e6e?w=600&h=600&fit=crop",
        description="Sulfate-free shampoo with argan oil and keratin. 500ml bottle. Suitable for all hair types. Restores shine and reduces frizz without harsh chemicals.",
        rating=4.6, reviews=312, stock=67,
        tags=("sulfate-free", "argan-oil", "keratin", "500ml"),
    ),
    Product(
        "sku-conditioner", "Daily Conditioner", "personal_care", 69900,
        "https://images.unsplash.com/photo-1631729371254-42c2892f0e6e?w=600&h=600&fit=crop",
        description="Lightweight conditioner pairs with our Daily Shampoo. 500ml. Detangles and smooths without weighing hair down. Silicone-free formula.",
        rating=4.5, reviews=198, stock=55,
        tags=("lightweight", "silicone-free", "detangling", "500ml"),
    ),
    Product(
        "sku-lotion", "Body Lotion", "personal_care", 79900,
        "https://images.unsplash.com/photo-1611930022073-b7a4ba5fcccd?w=600&h=600&fit=crop",
        description="Rich moisturizing lotion with shea butter and vitamin E. 400ml pump bottle. Absorbs quickly, non-greasy. Keeps skin hydrated for 24 hours.",
        rating=4.4, reviews=145, stock=38,
        tags=("shea-butter", "vitamin-e", "24hr-hydration", "400ml"),
    ),
    # ── Apparel ───────────────────────────────────────────
    Product(
        "sku-tee", "Classic Cotton Tee", "apparel", 99900,
        "https://images.unsplash.com/photo-1521572163474-6864f9cf17ab?w=600&h=600&fit=crop",
        description="100% organic cotton t-shirt. Pre-shrunk, breathable fabric. Reinforced seams for durability. Available in sizes S-XXL. Weight: 180 GSM.",
        rating=4.6, reviews=421, stock=120,
        tags=("organic-cotton", "pre-shrunk", "180gsm", "breathable"),
    ),
    Product(
        "sku-socks", "Merino Wool Socks", "apparel", 49900,
        "https://images.unsplash.com/photo-1586350977771-b3b0abd50c82?w=600&h=600&fit=crop",
        description="Merino wool blend crew socks. Moisture-wicking, temperature regulating. Cushioned sole, reinforced heel and toe. One size fits 6-12.",
        rating=4.7, reviews=287, stock=95,
        tags=("merino-wool", "moisture-wicking", "cushioned", "temp-regulating"),
    ),
    Product(
        "sku-hoodie", "Zip-Up Hoodie", "apparel", 249900,
        "https://images.unsplash.com/photo-1556821840-3a63f95609a7?w=600&h=600&fit=crop",
        description="Heavyweight 350 GSM fleece-lined hoodie. YKK zipper, kangaroo pocket, adjustable drawstring. Machine washable. Unisex fit.",
        rating=4.8, reviews=178, stock=34,
        tags=("350gsm", "fleece-lined", "ykk-zipper", "unisex", "heavyweight"),
    ),
    # ── Kitchen ───────────────────────────────────────────
    Product(
        "sku-coffee", "Single-Origin Coffee Beans", "kitchen", 74900,
        "https://images.unsplash.com/photo-1559056199-641a0ac8b55e?w=600&h=600&fit=crop",
        description="250g whole beans from Chikmagalur, India. Medium roast with notes of dark chocolate, caramel, and walnut. Best before 6 months from roast date.",
        rating=4.9, reviews=532, stock=78,
        tags=("single-origin", "chikmagalur", "medium-roast", "250g", "specialty"),
    ),
    Product(
        "sku-bottle", "Insulated Water Bottle", "kitchen", 119900,
        "https://images.unsplash.com/photo-1602143407151-7111542de6e8?w=600&h=600&fit=crop",
        description="750ml vacuum-insulated stainless steel bottle. Keeps cold 24hrs, hot 12hrs. BPA-free, leak-proof lid. Wide mouth for ice cubes.",
        rating=4.6, reviews=203, stock=61,
        tags=("vacuum-insulated", "750ml", "bpa-free", "24h-cold", "stainless-steel"),
    ),
    Product(
        "sku-shaker", "Protein Shaker", "kitchen", 59900,
        "https://images.unsplash.com/photo-1594381898411-846e7d193883?w=600&h=600&fit=crop",
        description="600ml BPA-free shaker with wire mixing ball. Leak-proof flip cap, measurement markings. Dishwasher safe. Fits most cup holders.",
        rating=4.3, reviews=156, stock=110,
        tags=("600ml", "bpa-free", "mixing-ball", "leak-proof", "dishwasher-safe"),
    ),
    # ── Stationery ────────────────────────────────────────
    Product(
        "sku-notebook", "Hardbound Notebook", "stationery", 44900,
        "https://images.unsplash.com/photo-1531346878377-a5be20888e57?w=600&h=600&fit=crop",
        description="A5 hardbound notebook with 160 ruled pages. Acid-free 90gsm paper. Lay-flat binding. Includes ribbon bookmark and elastic closure.",
        rating=4.5, reviews=189, stock=74,
        tags=("a5", "hardbound", "160-pages", "acid-free", "lay-flat", "90gsm"),
    ),
    Product(
        "sku-pen", "Gel Pen Set", "stationery", 29900,
        "https://images.unsplash.com/photo-1583485088034-697b5bc36b92?w=600&h=600&fit=crop",
        description="Pack of 5 gel pens with 0.5mm tips. Black, blue, red, green, purple. Quick-dry ink, ergonomic grip. Refillable cartridges.",
        rating=4.4, reviews=324, stock=135,
        tags=("gel-pen", "0.5mm", "5-pack", "quick-dry", "refillable"),
    ),
    # ── Clearance (stagnant stock) ────────────────────────
    Product(
        "sku-oldstock", "Last Season Jacket", "apparel", 399900,
        "https://images.unsplash.com/photo-1591047139829-d91aecb6caea?w=600&h=600&fit=crop",
        description="Water-resistant bomber jacket from last season. Satin lining, ribbed cuffs. Minor cosmetic flaws — fully functional. Final sale, no returns.",
        rating=4.2, reviews=45, stock=8,
        tags=("last-season", "final-sale", "water-resistant", "bomber", "clearance"),
        stagnant=True,
        days_in_stock=120,
    ),
)


def find_product(catalog: tuple[Product, ...], product_id: str) -> Product | None:
    for product in catalog:
        if product.id == product_id:
            return product
    return None

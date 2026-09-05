from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from razorpay_agent.checkout.catalog import Product, find_product


@dataclass(frozen=True)
class RegimenEdge:
    target: str
    weight: float
    relation: str


# Documented prior: the merchant's regimen / co-purchase relationships, expressed as
# (target -> [(neighbor, regimen_strength, relation)]). Edge weight = regimen strength
# (how natural the pairing is); degree (number of neighbors) is the popularity proxy.
#
# This is a *documented prior*, not inferred by the bandit. It is the single source of
# truth the candidate-generator node and the simulator both read from. Relationships
# span both same-category regimen pairs (shampoo + conditioner) and cross-category
# lifestyle pairings (coffee mug + coffee beans).
_REGIMEN_PRIOR: dict[str, list[tuple[str, float, str]]] = {
    # Home
    "sku-candle": [("sku-diffuser", 1.3, "home_essential")],
    "sku-mug": [("sku-coffee", 1.5, "morning_routine")],
    "sku-diffuser": [("sku-candle", 1.2, "home_essential")],
    # Personal care
    "sku-shampoo": [("sku-conditioner", 1.6, "regimen_pair"), ("sku-lotion", 1.1, "bath_routine")],
    "sku-conditioner": [("sku-shampoo", 1.5, "regimen_pair")],
    "sku-lotion": [("sku-shampoo", 1.2, "bath_routine")],
    # Apparel
    "sku-tee": [("sku-socks", 1.3, "wardrobe_essential")],
    "sku-hoodie": [("sku-socks", 1.4, "wardrobe_essential")],
    "sku-oldstock": [("sku-hoodie", 1.3, "layering"), ("sku-socks", 1.0, "wardrobe_essential")],
    # Kitchen
    "sku-coffee": [("sku-mug", 1.4, "morning_routine")],
    "sku-bottle": [("sku-shaker", 1.3, "fitness_routine")],
    "sku-shaker": [("sku-bottle", 1.2, "fitness_routine")],
    # Stationery
    "sku-notebook": [("sku-pen", 1.4, "desk_essential")],
    "sku-pen": [("sku-notebook", 1.3, "desk_essential")],
}


class CoPurchaseGraph:
    """Merchant regimen / co-purchase graph.

    Edge weight = regimen strength. Degree = popularity proxy. Built as a documented
    prior from the catalog (see ``_REGIMEN_PRIOR``); the simulator and the
    MerchantAgent candidate-generator node both read from this single structure.
    """

    def __init__(
        self,
        edges: dict[str, list[RegimenEdge]] | None = None,
        category_of: dict[str, str] | None = None,
    ) -> None:
        self._edges: dict[str, list[RegimenEdge]] = edges or {}
        self._category_of: dict[str, str] = category_of or {}

    @classmethod
    def from_catalog(cls, catalog: tuple[Product, ...]) -> "CoPurchaseGraph":
        ids = {p.id for p in catalog}
        category_of = {p.id: p.category for p in catalog}
        edges: dict[str, list[RegimenEdge]] = {}
        for sku, neighbors in _REGIMEN_PRIOR.items():
            if sku not in ids:
                continue
            edges[sku] = [
                RegimenEdge(target, weight, relation)
                for target, weight, relation in neighbors
                if target in ids
            ]
        return cls(edges, category_of)

    def neighbors(self, sku: str) -> list[RegimenEdge]:
        return list(self._edges.get(sku, []))

    def degree(self, sku: str) -> int:
        """Popularity proxy: how many regimen co-purchase partners this SKU has."""
        return len(self._edges.get(sku, []))

    def strength(self, sku: str, other: str) -> float:
        for edge in self._edges.get(sku, []):
            if edge.target == other:
                return edge.weight
        return 0.0

    def relevant_categories(self, category: str) -> set[str]:
        """Categories that are regimen-relevant co-purchase partners of ``category``.

        Used by the simulator to decide bundle relevance (replacing naive category
        equality). Derived from the documented prior: any category containing a
        regimen neighbor of a ``category`` item counts as relevant. The reward
        formula shape is unchanged — only the *source* of the relevance flag moves
        from ``category == category`` to this graph lookup.
        """
        relevant: set[str] = {category}
        for sku, cat in self._category_of.items():
            if cat != category:
                continue
            for edge in self._edges.get(sku, []):
                neighbor_cat = self._category_of.get(edge.target)
                if neighbor_cat is not None:
                    relevant.add(neighbor_cat)
        return relevant

    def to_dict(self) -> dict[str, Any]:
        return {
            sku: [
                {"target": e.target, "weight": e.weight, "relation": e.relation}
                for e in edges
            ]
            for sku, edges in self._edges.items()
        }


def candidate_bundles_for(
    target_sku: str,
    catalog: tuple[Product, ...],
    graph: CoPurchaseGraph,
    max_candidates: int = 3,
) -> list:
    """Candidate-generator node output: regimen-anchored bundle arms for a target SKU.

    Each returned ``BundleArm`` has ``anchor_sku == target_sku`` and ``bundle_item``
    set to a regimen neighbor, with a bundle price derived from that neighbor's
    catalog price. The bandit can then choose among these instead of the static
    catalog bundles.
    """
    from razorpay_agent.decision.arms import BundleArm

    if find_product(catalog, target_sku) is None:
        return []
    candidates: list[BundleArm] = []
    for edge in graph.neighbors(target_sku)[:max_candidates]:
        neighbor = find_product(catalog, edge.target)
        if neighbor is None:
            continue
        price_inr = round(neighbor.unit_amount_paise / 100.0, 2)
        candidates.append(
            BundleArm(
                f"b_{target_sku}__{edge.target}",
                edge.target,
                price_inr,
                anchor_sku=target_sku,
            )
        )
    return candidates

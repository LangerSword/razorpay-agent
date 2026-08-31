from __future__ import annotations

import threading
from typing import Iterable

from razorpay_agent.checkout.catalog import Product


class InventoryStore:
    """In-process, atomic inventory reservation (anti-double-sell), no Redis.

    Stock is reserved per checkout session. A session holds its reservation until
    it is either committed (sale captured) or released (cancelled / payment
    failed). All mutations are guarded by a single re-entrant lock so concurrent
    checkout sessions can never oversell the same unit.
    """

    def __init__(self, initial: dict[str, int] | None = None) -> None:
        self._lock = threading.RLock()
        self._stock: dict[str, int] = {sku: int(qty) for sku, qty in (initial or {}).items()}
        self._reserved: dict[str, int] = {sku: 0 for sku in self._stock}
        self._session_res: dict[str, list[tuple[str, int]]] = {}

    @classmethod
    def from_catalog(
        cls, catalog: Iterable[Product], default_stock: int = 1_000
    ) -> "InventoryStore":
        return cls({p.id: int(default_stock) for p in catalog})

    # --- queries ---------------------------------------------------------

    def total(self, sku: str) -> int:
        with self._lock:
            return self._stock.get(sku, 0)

    def reserved(self, sku: str) -> int:
        with self._lock:
            return self._reserved.get(sku, 0)

    def available(self, sku: str) -> int:
        with self._lock:
            return self._stock.get(sku, 0) - self._reserved.get(sku, 0)

    def has_session(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._session_res

    # --- mutations -------------------------------------------------------

    def reserve_for_session(self, session_id: str, items: list[tuple[str, int]]) -> bool:
        """Atomically reserve ``items`` for ``session_id``.

        Returns ``False`` (and reserves nothing) if any SKU lacks enough free
        stock — the whole reservation is all-or-nothing. Re-reserving an already
        reserved session first releases the prior reservation.
        """
        if not items:
            return True
        with self._lock:
            if session_id in self._session_res:
                self._release_locked(session_id)
            needed: dict[str, int] = {}
            for sku, qty in items:
                needed[sku] = needed.get(sku, 0) + qty
            for sku, qty in needed.items():
                free = self._stock.get(sku, 0) - self._reserved.get(sku, 0)
                if free < qty:
                    return False
            for sku, qty in needed.items():
                self._reserved[sku] = self._reserved.get(sku, 0) + qty
            self._session_res[session_id] = list(items)
            return True

    def commit_session(self, session_id: str) -> None:
        """Permanently remove reserved stock after a captured sale."""
        with self._lock:
            for sku, qty in self._session_res.pop(session_id, []):
                self._stock[sku] = max(0, self._stock.get(sku, 0) - qty)
                self._reserved[sku] = max(0, self._reserved.get(sku, 0) - qty)

    def release_session(self, session_id: str) -> None:
        """Return reserved stock to free pool (cancel / payment failed)."""
        with self._lock:
            self._release_locked(session_id)

    def _release_locked(self, session_id: str) -> None:
        for sku, qty in self._session_res.pop(session_id, []):
            self._reserved[sku] = max(0, self._reserved.get(sku, 0) - qty)

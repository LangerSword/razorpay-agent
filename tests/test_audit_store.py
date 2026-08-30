from datetime import datetime, timezone

import pytest

from razorpay_agent.audit import AuditStore
from razorpay_agent.core import (
    ACCEPTED,
    DECLINED,
    AuditEntry,
    AuditOutcome,
    GateDecision,
    ProposedAction,
)


def make_action(session_id="sess-1", source="linucb_bandit"):
    return ProposedAction(
        action_type="discount",
        target="sku-1",
        expected_uplift=120.0,
        confidence=0.7,
        source=source,
        session_id=session_id,
        discount_percent=10.0,
    )


def make_decision(action):
    return GateDecision(
        allowed=True,
        checked_against=["max_discount_pct", "buyer_allowance"],
        reason="within limits",
        final_action=action,
    )


def make_entry(session_id="sess-1", status=ACCEPTED, minute=0):
    action = make_action(session_id=session_id)
    return AuditEntry(
        timestamp=datetime(2026, 8, 22, 12, minute, 0, tzinfo=timezone.utc),
        session_id=session_id,
        proposed_action=action,
        gate_decision=make_decision(action),
        outcome=AuditOutcome(status=status, detail="ok" if status == ACCEPTED else "no"),
    )


@pytest.fixture
def store(tmp_path):
    return AuditStore(tmp_path / "audit.sqlite3")


class TestAppendAndRead:
    def test_append_returns_increasing_ids(self, store):
        first = store.append(make_entry())
        second = store.append(make_entry())
        assert second == first + 1

    def test_round_trip_preserves_entry(self, store):
        entry = make_entry()
        store.append(entry)
        (loaded,) = store.get_by_session("sess-1")
        assert loaded == entry

    def test_get_by_session_filters_other_sessions(self, store):
        store.append(make_entry(session_id="a"))
        store.append(make_entry(session_id="b"))
        assert [e.session_id for e in store.get_by_session("a")] == ["a"]
        assert store.get_by_session("missing") == []

    def test_recent_returns_insertion_order_within_tail(self, store):
        for minute in (2, 0, 1):
            store.append(make_entry(minute=minute))
        loaded = store.recent()
        assert [e.timestamp.minute for e in loaded] == [2, 0, 1]

    def test_recent_limit_returns_newest_appends(self, store):
        for minute in (0, 1, 2):
            store.append(make_entry(minute=minute))
        loaded = store.recent(limit=2)
        assert [e.timestamp.minute for e in loaded] == [1, 2]

    def test_negative_limit_rejected(self, store):
        with pytest.raises(ValueError):
            store.recent(limit=-1)

    def test_iter_all_yields_everything_in_order(self, store):
        for minute in (3, 1, 2):
            store.append(make_entry(minute=minute))
        assert [e.timestamp.minute for e in store.iter_all()] == [3, 1, 2]

    def test_count(self, store):
        assert store.count() == 0
        store.append(make_entry())
        store.append(make_entry())
        assert store.count() == 2


class TestDurability:
    def test_entries_survive_store_reopen(self, tmp_path):
        path = tmp_path / "audit.sqlite3"
        entry = make_entry(status=DECLINED)
        AuditStore(path).append(entry)

        reloaded = AuditStore(path)
        (loaded,) = reloaded.get_by_session("sess-1")
        assert loaded == entry
        assert reloaded.count() == 1

    def test_schema_init_is_idempotent(self, tmp_path):
        path = tmp_path / "audit.sqlite3"
        AuditStore(path).append(make_entry())
        AuditStore(path).append(make_entry())
        assert AuditStore(path).count() == 2


class TestAsyncWrite:
    def test_aappend_matches_sync_semantics(self, tmp_path):
        async def run():
            store = AuditStore(":memory:")
            await store.aappend(make_entry())
            return store.count()

        assert __import__("asyncio").run(run()) == 1


class TestInMemory:
    def test_memory_store_works_standalone(self):
        store = AuditStore(":memory:")
        store.append(make_entry())
        assert store.count() == 1

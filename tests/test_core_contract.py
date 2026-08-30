from datetime import datetime, timezone

import pytest

from razorpay_agent.core import (
    ACCEPTED,
    DECLINED,
    FAILED,
    AuditEntry,
    AuditOutcome,
    ContractViolation,
    GateDecision,
    ProposedAction,
)


def discount_action(**overrides):
    defaults = dict(
        action_type="discount",
        target="sku-1",
        expected_uplift=120.0,
        confidence=0.7,
        source="linucb_bandit",
        session_id="sess-1",
        discount_percent=10.0,
    )
    return ProposedAction(**{**defaults, **overrides})


def bundle_action(**overrides):
    defaults = dict(
        action_type="bundle_upsell",
        target="sku-2",
        expected_uplift=80.0,
        confidence=0.5,
        source="fallback_rule",
        session_id="sess-1",
        bundle_item="sku-9",
        bundle_price=150.0,
    )
    return ProposedAction(**{**defaults, **overrides})


def gate_decision(action=None, **overrides):
    defaults = dict(
        allowed=True,
        checked_against=["max_discount_pct", "buyer_allowance"],
        reason="within limits",
        final_action=action or discount_action(),
    )
    return GateDecision(**{**defaults, **overrides})


def audit_entry(**overrides):
    action = overrides.pop("proposed_action", discount_action())
    decision = gate_decision(action)
    defaults = dict(
        timestamp=datetime(2026, 8, 22, 12, 0, 0, tzinfo=timezone.utc),
        session_id="sess-1",
        proposed_action=action,
        gate_decision=decision,
        outcome=AuditOutcome(status=ACCEPTED),
    )
    return AuditEntry(**{**defaults, **overrides})


class TestProposedActionDiscount:
    def test_valid_discount_constructs(self):
        action = discount_action()
        assert action.action_type == "discount"
        assert action.discount_percent == 10.0
        assert action.bundle_item is None
        assert action.bundle_price is None

    def test_missing_discount_percent_rejected(self):
        with pytest.raises(ContractViolation):
            discount_action(discount_percent=None)

    def test_zero_discount_percent_rejected(self):
        with pytest.raises(ContractViolation):
            discount_action(discount_percent=0.0)

    def test_negative_discount_percent_rejected(self):
        with pytest.raises(ContractViolation):
            discount_action(discount_percent=-5.0)

    def test_discount_over_100_percent_rejected(self):
        with pytest.raises(ContractViolation):
            discount_action(discount_percent=100.5)

    def test_discount_with_bundle_fields_rejected(self):
        with pytest.raises(ContractViolation):
            discount_action(bundle_item="sku-9", bundle_price=100.0)


class TestProposedActionBundle:
    def test_valid_bundle_constructs(self):
        action = bundle_action()
        assert action.bundle_item == "sku-9"
        assert action.bundle_price == 150.0
        assert action.discount_percent is None

    def test_missing_bundle_item_rejected(self):
        with pytest.raises(ContractViolation):
            bundle_action(bundle_item=None)

    def test_missing_bundle_price_rejected(self):
        with pytest.raises(ContractViolation):
            bundle_action(bundle_price=None)

    def test_non_positive_bundle_price_rejected(self):
        with pytest.raises(ContractViolation):
            bundle_action(bundle_price=0.0)

    def test_bundle_with_discount_percent_rejected(self):
        with pytest.raises(ContractViolation):
            bundle_action(discount_percent=10.0)


class TestProposedActionCommonFields:
    def test_unknown_action_type_rejected(self):
        with pytest.raises(ContractViolation):
            discount_action(action_type="free_shipping")

    def test_blank_text_fields_rejected(self):
        for field in ("target", "source", "session_id", "action_type"):
            with pytest.raises(ContractViolation):
                discount_action(**{field: "   "})

    def test_confidence_below_zero_rejected(self):
        with pytest.raises(ContractViolation):
            discount_action(confidence=-0.1)

    def test_confidence_above_one_rejected(self):
        with pytest.raises(ContractViolation):
            discount_action(confidence=1.1)

    def test_nan_confidence_rejected(self):
        with pytest.raises(ContractViolation):
            discount_action(confidence=float("nan"))

    def test_non_numeric_confidence_rejected(self):
        with pytest.raises(ContractViolation):
            discount_action(confidence="high")

    def test_boolean_confidence_rejected(self):
        with pytest.raises(ContractViolation):
            discount_action(confidence=True)

    def test_round_trip_preserves_equality(self):
        for action in (discount_action(), bundle_action()):
            assert ProposedAction.from_dict(action.to_dict()) == action

    def test_from_dict_missing_field_rejected(self):
        data = discount_action().to_dict()
        del data["source"]
        with pytest.raises(ContractViolation):
            ProposedAction.from_dict(data)


class TestGateDecision:
    def test_valid_decision_normalizes_checks_to_tuple(self):
        decision = gate_decision()
        assert decision.checked_against == ("max_discount_pct", "buyer_allowance")
        assert isinstance(decision.checked_against, tuple)

    def test_duplicate_checks_deduplicated_in_order(self):
        decision = gate_decision(
            checked_against=["max_discount_pct", "max_discount_pct", "buyer_allowance"]
        )
        assert decision.checked_against == ("max_discount_pct", "buyer_allowance")

    def test_empty_checked_against_rejected(self):
        with pytest.raises(ContractViolation):
            gate_decision(checked_against=[])

    def test_bare_string_checked_against_rejected(self):
        with pytest.raises(ContractViolation):
            gate_decision(checked_against="max_discount_pct")

    def test_non_string_check_entry_rejected(self):
        with pytest.raises(ContractViolation):
            gate_decision(checked_against=["max_discount_pct", 42])

    def test_non_boolean_allowed_rejected(self):
        with pytest.raises(ContractViolation):
            gate_decision(allowed=1)

    def test_final_action_must_be_proposed_action(self):
        with pytest.raises(ContractViolation):
            gate_decision(final_action={"action_type": "discount"})

    def test_to_dict_exposes_list_at_json_boundary(self):
        assert gate_decision().to_dict()["checked_against"] == [
            "max_discount_pct",
            "buyer_allowance",
        ]

    def test_round_trip_preserves_equality(self):
        decision = gate_decision()
        assert GateDecision.from_dict(decision.to_dict()) == decision


class TestAuditOutcome:
    @pytest.mark.parametrize("status", [ACCEPTED, DECLINED, FAILED])
    def test_valid_statuses_construct(self, status):
        outcome = AuditOutcome(status=status, detail="note")
        assert outcome.status == status

    def test_unknown_status_rejected(self):
        with pytest.raises(ContractViolation):
            AuditOutcome(status="pending")

    def test_detail_defaults_to_empty(self):
        assert AuditOutcome(status=DECLINED).detail == ""

    def test_non_string_detail_rejected(self):
        with pytest.raises(ContractViolation):
            AuditOutcome(status=FAILED, detail=123)


class TestAuditEntry:
    def test_valid_entry_constructs(self):
        entry = audit_entry()
        assert entry.outcome.status == ACCEPTED

    def test_naive_timestamp_rejected(self):
        with pytest.raises(ContractViolation):
            audit_entry(timestamp=datetime(2026, 8, 22, 12, 0, 0))

    def test_timestamp_normalized_to_utc(self):
        offset = timezone.utc
        entry = audit_entry(timestamp=datetime(2026, 8, 22, 15, 30, 0, tzinfo=offset))
        assert entry.timestamp.utcoffset() == offset.utcoffset(None)

    def test_proposed_action_session_mismatch_rejected(self):
        with pytest.raises(ContractViolation):
            audit_entry(proposed_action=discount_action(session_id="other"))

    def test_gate_final_action_session_mismatch_rejected(self):
        action = discount_action()
        decision = GateDecision(
            allowed=True,
            checked_against=["max_discount_pct"],
            reason="capped to fallback",
            final_action=bundle_action(session_id="elsewhere"),
        )
        with pytest.raises(ContractViolation):
            audit_entry(proposed_action=action, gate_decision=decision)

    def test_wrong_component_types_rejected(self):
        with pytest.raises(ContractViolation):
            audit_entry(gate_decision="approved")
        with pytest.raises(ContractViolation):
            audit_entry(outcome="accepted")

    def test_round_trip_preserves_equality(self):
        entry = audit_entry()
        restored = AuditEntry.from_dict(entry.to_dict())
        assert restored == entry
        assert restored.proposed_action == entry.proposed_action
        assert restored.gate_decision == entry.gate_decision
        assert restored.outcome == entry.outcome

    def test_from_dict_missing_outcome_rejected(self):
        data = audit_entry().to_dict()
        del data["outcome"]
        with pytest.raises(ContractViolation):
            AuditEntry.from_dict(data)

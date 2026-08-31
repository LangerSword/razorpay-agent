from __future__ import annotations

import threading
from typing import Any, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from razorpay_agent.checkout.catalog import DEMO_CATALOG
from razorpay_agent.checkout.offers import OfferPipeline
from razorpay_agent.checkout.sessions import AppliedOffer, CheckoutSessionState
from razorpay_agent.decision.co_purchase_graph import (
    CoPurchaseGraph,
    candidate_bundles_for,
)


class MerchantDecisionState(TypedDict, total=False):
    """State threaded through the MerchantAgent decision StateGraph.

    The graph is a thin orchestration of the existing ``OfferPipeline`` steps
    (``_step_context`` / ``_step_consult`` / ``_step_gate`` / ``_step_finalize``),
    so its output is identical to the pipeline's direct path by construction.
    ``regimen_graph`` is the MerchantAgent graph state (the co-purchase prior);
    ``candidate_arms`` is the output of the candidate-generator node.
    """

    session_state: CheckoutSessionState
    cart_value_paise: int
    target_sku: str
    category: str
    regimen_graph: Any
    decision_context: Any
    arm_id: Optional[str]
    proposed_action: Any
    bandit_consulted: bool
    gate_decision: Any
    candidate_arms: list
    offer: Optional[AppliedOffer]
    audit_entry_id: Optional[int]


class MerchantAgentGraph:
    """LangGraph StateGraph wrapper around the MerchantAgent decision flow.

    Node functions delegate to ``OfferPipeline._step_*`` helpers, preserving the
    gate/audit/watchdog/datalog side-effects of the original pipeline. The graph
    is the top-level MerchantAgent node in the broader orchestration (BuyerAgent
    -> MerchantAgent -> Gate -> Razorpay -> Audit, added in later phases).

    The regimen graph (a documented co-purchase prior) is held as graph state and
    exposed to the candidate-generator node, which emits regimen-anchored bundle
    arms for the session's target SKU.
    """

    def __init__(
        self,
        pipeline: OfferPipeline,
        regimen_graph: CoPurchaseGraph | None = None,
        catalog=None,
    ) -> None:
        self._pipeline = pipeline
        self._regimen_graph = regimen_graph or CoPurchaseGraph.from_catalog(DEMO_CATALOG)
        self._catalog = catalog if catalog is not None else DEMO_CATALOG
        self._lock = threading.Lock()
        self._graph = self._build()

    def _build(self):
        p = self._pipeline

        def build_context(state: MerchantDecisionState) -> dict[str, Any]:
            s = state["session_state"]
            dc = p._step_context(
                s, state["cart_value_paise"], state["target_sku"], state["category"]
            )
            return {"decision_context": dc}

        def generate_candidates(state: MerchantDecisionState) -> dict[str, Any]:
            # Candidate-generator node: regimen-anchored bundle arms for the
            # session's target SKU. The bandit may choose among these instead of
            # the static catalog bundles (live consumption is a follow-up).
            graph = state.get("regimen_graph") or self._regimen_graph
            if graph is None:
                return {"candidate_arms": []}
            arms = candidate_bundles_for(state["target_sku"], self._catalog, graph)
            return {"candidate_arms": arms}

        def consult_bandit(state: MerchantDecisionState) -> dict[str, Any]:
            s = state["session_state"]
            arm_id, action, bandit_consulted = p._step_consult(
                s, state["decision_context"], state["cart_value_paise"]
            )
            return {
                "arm_id": arm_id,
                "proposed_action": action,
                "bandit_consulted": bandit_consulted,
            }

        def apply_gate(state: MerchantDecisionState) -> dict[str, Any]:
            s = state["session_state"]
            decision = p._step_gate(s, state["proposed_action"], state["cart_value_paise"])
            return {"gate_decision": decision}

        def finalize_offer(state: MerchantDecisionState) -> dict[str, Any]:
            s = state["session_state"]
            offer = p._step_finalize(
                s,
                state["proposed_action"],
                state["decision_context"],
                state["gate_decision"],
                state["arm_id"],
                state["bandit_consulted"],
                state["cart_value_paise"],
                state["target_sku"],
                state["category"],
            )
            return {"offer": offer, "audit_entry_id": offer.audit_entry_id}

        graph = StateGraph(MerchantDecisionState)
        graph.add_node("build_context", build_context)
        graph.add_node("generate_candidates", generate_candidates)
        graph.add_node("consult_bandit", consult_bandit)
        graph.add_node("apply_gate", apply_gate)
        graph.add_node("finalize_offer", finalize_offer)

        graph.add_edge(START, "build_context")
        graph.add_edge("build_context", "generate_candidates")
        graph.add_edge("generate_candidates", "consult_bandit")
        graph.add_edge("consult_bandit", "apply_gate")
        graph.add_edge("apply_gate", "finalize_offer")
        graph.add_edge("finalize_offer", END)
        return graph.compile()

    def propose_for_session(
        self,
        state: CheckoutSessionState,
        cart_value_paise: int,
        target_sku: str,
        category: str,
    ) -> AppliedOffer | None:
        if state.applied_offer is not None:
            return state.applied_offer
        initial: MerchantDecisionState = {
            "session_state": state,
            "cart_value_paise": cart_value_paise,
            "target_sku": target_sku,
            "category": category,
            "regimen_graph": self._regimen_graph,
        }
        with self._lock:
            result = self._graph.invoke(initial)
        return result["offer"]

    def candidate_arms(self, target_sku: str, catalog, regimen_graph) -> list:
        """Candidate-generator node: regimen-anchored bundle arms for a target SKU.

        Returns ``BundleArm``s whose ``anchor_sku`` is ``target_sku`` and whose
        ``bundle_item`` is a regimen neighbor. The bandit may choose among these
        instead of the static catalog bundles, so offers pair to what the buyer is
        actually viewing.
        """
        from razorpay_agent.decision.co_purchase_graph import candidate_bundles_for

        return candidate_bundles_for(target_sku, catalog, regimen_graph)

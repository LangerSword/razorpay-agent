from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable

from razorpay_agent.reasoning.llm import LLMBackend, resolve_provider
from razorpay_agent.reasoning.tools import ReasoningDeps, build_registry
from razorpay_agent.gate.gate import RulePolicyGateConfig

DEFAULT_MAX_STEPS = 6

TOOL_CALL_RE = re.compile(
    r"(?:<<tool:(\w+)\s*(\{.*?\})?>>|<tool_call>(\w+)(?:\s*(\{.*?\}))?\s*(?:</tool_call>|$|<tool_call>))",
    re.DOTALL,
)


def _try_extract_tool(text: str) -> tuple[str, dict] | None:
    """Extract a tool call from LLM text. Supports multiple formats."""
    match = TOOL_CALL_RE.search(text)
    if match:
        if match.group(1):
            name = match.group(1)
            args_json = match.group(2) or "{}"
        else:
            name = match.group(3)
            args_json = match.group(4) or "{}"
        try:
            return name, json.loads(args_json) if args_json.strip() else {}
        except json.JSONDecodeError:
            return name, {}
    return None


def _strip_tool_calls(text: str) -> str:
    """Remove tool call markup from LLM text, keeping surrounding prose."""
    text = re.sub(r"<<tool:\w+\s*\{.*?\}>>", "", text, flags=re.DOTALL)
    text = re.sub(r"<tool_call>\w+\s*\{.*?\}</tool_call>", "", text, flags=re.DOTALL)
    text = re.sub(r"<tool_call>\w+</tool_call>", "", text)
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    return "\n".join(lines)


@dataclass
class ReasoningStep:
    step: int
    role: str  # "reasoning" | "tool" | "system"
    content: str
    provider: str | None = None
    model: str | None = None


@dataclass
class ReasoningResult:
    session_id: str
    provider: str
    model: str
    steps: list[ReasoningStep]
    final_text: str
    verdict: str  # "APPROVE" | "REJECT" | "REVIEW" | "NONE"
    verdict_rationale: str
    fallback: bool


def _render_history(history: list[tuple[str, str]]) -> str:
    out = []
    for role, content in history:
        tag = {"system": "SYSTEM", "user": "USER", "assistant": "ASSISTANT"}.get(role, role.upper())
        out.append(f"[{tag}]\n{content}")
    return "\n\n".join(out)


SYSTEM_TEMPLATE = """You are the reasoning module of a merchant-side decisioning agent.
Your job is to EXPLAIN and SANITY-CHECK a proposed offer — never to execute it.

KEY RULES:
- The buyer agent is SEPARATE and INDEPENDENT. You do NOT decide whether the buyer accepts.
  Your job is to assess whether the OFFER ITSELF is sensible, bounded, and policy-compliant.
  The buyer will make their own decision.

Available read-only tools:
{tool_specs}

To call a tool, emit exactly one line with the tool's actual arg name from its spec:
<<tool:get_catalog_item {{"sku": "sku-hoodie"}}>>
<<tool:get_clearance_policy {{}}>>
<<tool:get_bandit_scores {{"target_sku": "sku-hoodie", "item_category": "apparel", "cart_value_inr": 2499.0, "buyer_allowance_inr": 100000.0, "is_stagnant": false, "days_in_stock": null}}>>

After 1-2 tool calls, stop gathering information and give your final
assessment as plain text. Do NOT propose settlement or payment.

Your final assessment MUST be structured as:
- Cite the specific gate limits that bound this offer (e.g., "15% cap", "300 rupee ceiling")
- State whether the offer is within those limits (capped or fully within)
- Note buyer-allowance headroom if relevant
- End with a single verdict line: "Verdict: APPROVE" (offer is sensible and bounded), "Verdict: REJECT" (offer violates policy or is disproportionate), or "Verdict: REVIEW" (uncertain, needs human eye)

Keep it concise. Do NOT keep calling tools once you have enough context."""


def _format_examples(examples: list[dict[str, Any]]) -> str:
    parts: list[str] = ["Few-shot examples of good reasoning traces:"]
    for idx, ex in enumerate(examples, 1):
        parts.append(f"\n--- Example {idx}: {ex.get('scenario', idx)} ---")
        for turn in ex.get("turns", []):
            role = turn.get("role", "assistant")
            content = turn.get("content", "")
            if role == "assistant":
                parts.append(f"ASSISTANT: {content}")
            else:
                parts.append(f"USER: {content}")
        if ex.get("final_text"):
            parts.append(f"FINAL ANSWER: {ex['final_text']}")
    return "\n".join(parts)


def _extract_verdict(text: str) -> tuple[str, str]:
    """Extract verdict and rationale from final reasoning text."""
    if not text:
        return "NONE", ""
    
    lines = text.strip().split("\n")
    verdict = "NONE"
    verdict_line = ""
    rationale_lines = []
    
    # Look for explicit verdict line — strict match first
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "Verdict: APPROVE":
            verdict = "APPROVE"
            verdict_line = stripped
            rationale_lines = [l.strip() for l in lines[:i] if l.strip()]
            break
        elif stripped == "Verdict: REJECT":
            verdict = "REJECT"
            verdict_line = stripped
            rationale_lines = [l.strip() for l in lines[:i] if l.strip()]
            break
        elif stripped == "Verdict: REVIEW":
            verdict = "REVIEW"
            verdict_line = stripped
            rationale_lines = [l.strip() for l in lines[:i] if l.strip()]
            break
    
    # Fallback: check for keywords if no strict match
    if verdict == "NONE":
        low = text.lower()
        if "verdict: approve" in low or "verdict: approve" in low:
            verdict = "APPROVE"
        elif "verdict: reject" in low:
            verdict = "REJECT"
        elif "verdict: review" in low:
            verdict = "REVIEW"
        rationale_lines = [text[:200].strip()]
    
    rationale = " ".join(rationale_lines) if rationale_lines else text[:200].strip()
    return verdict, rationale


class ReasoningAgent:
    """Hermes-style loop: prompt -> LLM -> tool_calls -> loop -> persist.

    Bounded by ``max_steps``. Every reasoning/tool step is observable (persisted to
    ``reasoning_log`` and delivered to ``callbacks``). On any LLM failure it degrades
    gracefully: the bandit decision proceeds unaided and ``fallback`` is set.
    """

    def __init__(
        self,
        llm: LLMBackend | None = None,
        deps: ReasoningDeps | None = None,
        max_steps: int = DEFAULT_MAX_STEPS,
        store=None,
        callbacks: list[Callable[[ReasoningStep], None]] | None = None,
        examples: list[dict[str, Any]] | None = None,
    ) -> None:
        self._llm = llm or resolve_provider()
        self._deps = deps or ReasoningDeps(
            catalog=(), policy=None, gate_config=RulePolicyGateConfig(
                fallback_bundle_item="sku-fallback", fallback_bundle_price=99.0
            )
        )
        self._registry = build_registry(self._deps)
        self._max_steps = max_steps
        self._store = store
        self._callbacks = callbacks or []
        self._examples = examples or []

    def _emit(
        self,
        steps: list[ReasoningStep],
        session_id: str,
        role: str,
        content: str,
    ) -> None:
        step = ReasoningStep(
            step=len(steps) + 1,
            role=role,
            content=content,
            provider=self._llm.name,
            model=self._llm.model,
        )
        steps.append(step)
        if self._store is not None:
            self._store.append(
                session_id, step.step, role, content, step.provider, step.model
            )
        for cb in self._callbacks:
            cb(step)

    def reason(
        self,
        session_id: str,
        *,
        target_sku: str,
        item_category: str,
        cart_value_inr: float,
        buyer_allowance_inr: float,
        is_stagnant: bool = False,
        days_in_stock: int | None = None,
        bandit_action: dict | None = None,
        gate_decision: dict | None = None,
    ) -> ReasoningResult:
        tool_specs = json.dumps(self._registry.specs(), indent=2)
        system = SYSTEM_TEMPLATE.format(tool_specs=tool_specs)
        if self._examples:
            system = _format_examples(self._examples) + "\n\n" + system
        user = self._user_prompt(
            session_id, target_sku, item_category, cart_value_inr,
            buyer_allowance_inr, is_stagnant, days_in_stock,
            bandit_action, gate_decision,
        )
        history: list[tuple[str, str]] = [("system", system), ("user", user)]
        steps: list[ReasoningStep] = []
        fallback = False
        final_text = ""

        text = ""
        try:
            tool_calls_made = 0
            for step_num in range(self._max_steps):
                prompt = _render_history(history)
                force_final = tool_calls_made >= 2 or step_num == self._max_steps - 1
                if force_final:
                    prompt += (
                        "\n\nFINAL ANSWER REQUIRED: stop calling tools and give your "
                        "structured assessment now. End with a single verdict line."
                    )
                text = self._llm.complete(prompt)
                tool_call = _try_extract_tool(text)
                if tool_call is not None and tool_calls_made < 2 and not force_final:
                    name, args = tool_call
                    self._emit(steps, session_id, "reasoning", text)
                    result = self._registry.call(name, args)
                    self._emit(
                        steps, session_id, "tool",
                        f"{name}({json.dumps(args, sort_keys=True)}) -> {result}",
                    )
                    remaining = TOOL_CALL_RE.sub("", text).strip()
                    if remaining:
                        history.append(("assistant", remaining))
                    history.append(("user", f"TOOL_RESULT: {result}"))
                    tool_calls_made += 1
                    continue
                if tool_call is not None:
                    cleaned = _strip_tool_calls(text)
                    if cleaned and len(cleaned) > 20:
                        final_text = cleaned
                    else:
                        final_text = None
                else:
                    final_text = text
                self._emit(steps, session_id, "reasoning", text)
                break
            else:
                final_text = text if text else ""

            if not final_text and steps:
                last_tool = next(
                    (s for s in reversed(steps) if s.role == "tool"), None
                )
                if last_tool:
                    final_text = (
                        f"Assessment: offer evaluated. "
                        f"Last observation: {last_tool.content[:150]}. "
                        f"Verdict: REVIEW — automated assessment; "
                        f"bandit + gate decision stands."
                    )
                else:
                    final_text = (
                        "Assessment: offer evaluated via bandit + gate. "
                        "Verdict: REVIEW — automated assessment."
                    )
            else:
                final_text = final_text or ""
        except Exception as exc:
            fallback = True
            detail = str(exc).replace("\n", " ")[:240]
            final_text = (
                f"Reasoning unavailable ({type(exc).__name__}: {detail}); "
                "decision proceeds via the bandit + gate."
            )
            self._emit(steps, session_id, "system", final_text)

        if not final_text and not fallback:
            final_text = "Assessment: offer proceeds via bandit + gate (reasoner produced no output)."

        verdict, verdict_rationale = _extract_verdict(final_text)

        return ReasoningResult(
            session_id=session_id,
            provider=self._llm.name,
            model=self._llm.model,
            steps=steps,
            final_text=final_text,
            verdict=verdict,
            verdict_rationale=verdict_rationale,
            fallback=fallback,
        )

    @staticmethod
    def _user_prompt(
        session_id, target_sku, item_category, cart_value_inr,
        buyer_allowance_inr, is_stagnant, days_in_stock,
        bandit_action, gate_decision,
    ) -> str:
        action_str = json.dumps(bandit_action) if bandit_action else "none (rule fallback)"
        gate_str = "n/a"
        if gate_decision is not None:
            gate_parts = []
            if "allowed" in gate_decision:
                gate_parts.append(f"allowed={gate_decision['allowed']}")
            if "reason" in gate_decision:
                gate_parts.append(f"reason=\"{gate_decision['reason']}\"")
            if "final_action_type" in gate_decision:
                gate_parts.append(f"final={gate_decision['final_action_type']}")
            gate_str = ", ".join(gate_parts)
        return (
            f"Session {session_id}. Target SKU: {target_sku} ({item_category}).\n"
            f"Cart: INR {cart_value_inr:g}. Buyer allowance: INR {buyer_allowance_inr:g}. "
            f"Stagnant stock: {is_stagnant}"
            + (f" ({days_in_stock} days)" if days_in_stock else "")
            + ".\n"
            f"Bandit proposed: {action_str}.\n"
            f"Gate decision: {gate_str}.\n"
            "Assess whether this offer is bounded by policy and sensible for this context. "
            "Use tools to inspect the specific limits, then give a structured assessment."
        )

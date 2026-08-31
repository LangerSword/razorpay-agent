from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable

from razorpay_agent.reasoning.llm import LLMBackend, resolve_provider
from razorpay_agent.reasoning.tools import ReasoningDeps, build_registry

DEFAULT_MAX_STEPS = 6

TOOL_CALL_RE = re.compile(r"<<tool:(\w+)\s*(\{.*?\})?>>", re.DOTALL)


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
    fallback: bool  # True if the reasoner failed and the bandit decision proceeded unaided


def _render_history(history: list[tuple[str, str]]) -> str:
    out = []
    for role, content in history:
        tag = {"system": "SYSTEM", "user": "USER", "assistant": "ASSISTANT"}.get(role, role.upper())
        out.append(f"[{tag}]\n{content}")
    return "\n\n".join(out)


SYSTEM_TEMPLATE = """You are the reasoning module of a merchant-side decisioning agent.
Your job is to EXPLAIN and SANITY-CHECK a proposed offer — never to execute it.
Available read-only tools:
{tool_specs}

To call a tool, emit exactly one line of the form:
<<tool:name {{"arg": value}}>>
After 1-2 tool calls, stop gathering information and give your final
recommendation as plain text. Do NOT propose settlement or payment.
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
            catalog=(), policy=None, gate_config=None  # type: ignore[arg-type]
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

        try:
            for step_num in range(self._max_steps):
                prompt = _render_history(history)
                if step_num == self._max_steps - 1:
                    prompt += (
                        "\n\nFINAL ANSWER REQUIRED: stop calling tools and give your "
                        "recommendation as plain text now."
                    )
                text = self._llm.complete(prompt)
                match = TOOL_CALL_RE.search(text)
                if match:
                    name = match.group(1)
                    args_json = match.group(2) or "{}"
                    try:
                        args = json.loads(args_json) if args_json.strip() else {}
                    except json.JSONDecodeError:
                        cleaned = args_json.rstrip("}")
                        try:
                            args = json.loads(cleaned) if cleaned.strip() else {}
                        except json.JSONDecodeError:
                            args = {}
                    self._emit(steps, session_id, "reasoning", text)
                    result = self._registry.call(name, args)
                    self._emit(
                        steps, session_id, "tool",
                        f"{name}({json.dumps(args, sort_keys=True)}) -> {result}",
                    )
                    history.append(("assistant", text))
                    history.append(("user", f"TOOL_RESULT: {result}"))
                    continue
                self._emit(steps, session_id, "reasoning", text)
                final_text = text
                break
            else:
                final_text = text
        except Exception as exc:
            fallback = True
            detail = str(exc).replace("\n", " ")[:240]
            final_text = (
                f"Reasoning unavailable ({type(exc).__name__}: {detail}); "
                "decision proceeds via the bandit + gate."
            )
            self._emit(steps, session_id, "system", final_text)

        return ReasoningResult(
            session_id=session_id,
            provider=self._llm.name,
            model=self._llm.model,
            steps=steps,
            final_text=final_text,
            fallback=fallback,
        )

    @staticmethod
    def _user_prompt(
        session_id, target_sku, item_category, cart_value_inr,
        buyer_allowance_inr, is_stagnant, days_in_stock,
        bandit_action, gate_decision,
    ) -> str:
        return (
            f"Session {session_id}. Target SKU: {target_sku} ({item_category}).\n"
            f"Cart: INR {cart_value_inr:g}. Buyer allowance: INR {buyer_allowance_inr:g}. "
            f"Stagnant stock: {is_stagnant}"
            + (f" ({days_in_stock} days)" if days_in_stock else "")
            + ".\n"
            f"Bandit proposed: {json.dumps(bandit_action) if bandit_action else 'none (rule fallback)'}.\n"
            f"Gate decision: {json.dumps(gate_decision) if gate_decision else 'n/a'}.\n"
            "Explain whether this proposed offer is sensible, bounded, and consistent "
            "with policy. Use tools to inspect context, then give a final recommendation."
        )

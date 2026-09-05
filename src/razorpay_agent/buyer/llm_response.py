"""LLMResponse — structured response from a single reasoning step.

The buyer harness uses this to prevent hallucination:
- The model can call tools (each with strict schemas)
- The model can return a structured verdict
- Both are validated, so the model can't make up tool names or miss required fields
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


TOOL_CALL_RE = re.compile(
    r"(?:<<tool:(\w+)\s*(\{.*?\})?>>|<tool_call>(\w+)(?:\s*(\{.*?\}))?\s*(?:</tool_call>|$|<tool_call>))",
    re.DOTALL,
)

# Nous-style tool call format: <tool_call>name<arg_key>arg</arg_key><arg_value>val</arg_value></tool_call>
NOUS_TOOL_CALL_RE = re.compile(
    r"<tool_call>(\w+)(?:<arg_key>([^<]*)</arg_key><arg_value>([^<]*)</arg_value>)*\s*</tool_call>",
    re.DOTALL,
)

NOUS_ARG_RE = re.compile(r"<arg_key>([^<]*)</arg_key><arg_value>([^<]*)</arg_value>")


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    raw: str = ""


@dataclass
class Verdict:
    """Structured verdict from the buyer."""
    decision: str  # ADD_TO_CART | SKIP
    rationale: str = ""
    confidence: float = 0.5


@dataclass
class LLMResponse:
    """One step in the reasoning loop — either a tool call or a final verdict."""
    text: str
    tool_call: ToolCall | None = None
    verdict: Verdict | None = None
    is_final: bool = False
    
    @classmethod
    def parse(cls, text: str) -> "LLMResponse":
        """Parse LLM text into a structured response."""
        text = text.strip()
        if not text:
            return cls(text=text, is_final=True, verdict=Verdict(decision="SKIP", rationale="empty response"))
        
        # Try to extract tool call
        tool_call = _try_extract_tool(text)
        if tool_call is not None:
            return cls(text=text, tool_call=tool_call)
        
        # Try to extract verdict
        verdict = _try_extract_verdict(text)
        if verdict is not None:
            return cls(text=text, verdict=verdict, is_final=True)
        
        # Default: treat as final but no clear verdict
        return cls(text=text, is_final=True, verdict=Verdict(decision="SKIP", rationale=text[:200]))


def _try_extract_tool(text: str) -> ToolCall | None:
    """Extract a tool call from LLM text, if present."""
    # Try standard format first
    match = TOOL_CALL_RE.search(text)
    if match:
        if match.group(1):
            name = match.group(1)
            args_json = match.group(2) or "{}"
        else:
            name = match.group(3)
            args_json = match.group(4) or "{}"
        try:
            args = json.loads(args_json) if args_json.strip() else {}
        except json.JSONDecodeError:
            args = {}
        return ToolCall(name=name, args=args, raw=match.group(0))
    
    # Try Nous format: <tool_call>name<arg_key>arg</arg_key><arg_value>val</arg_value></tool_call>
    nous_match = NOUS_TOOL_CALL_RE.search(text)
    if nous_match:
        name = nous_match.group(1)
        args = {}
        for arg_key, arg_val in NOUS_ARG_RE.findall(nous_match.group(0)):
            args[arg_key] = arg_val
        return ToolCall(name=name, args=args, raw=nous_match.group(0))
    
    return None


def _try_extract_verdict(text: str) -> Verdict | None:
    """Extract a structured verdict from text, if present."""
    lines = text.strip().split("\n")
    
    for line in lines:
        stripped = line.strip()
        if stripped == "Verdict: ADD_TO_CART":
            # Collect rationale from other lines
            rationale = "\n".join(l.strip() for l in lines if l.strip() and l.strip() != stripped)
            return Verdict(decision="ADD_TO_CART", rationale=rationale[:500], confidence=0.9)
        elif stripped == "Verdict: SKIP":
            rationale = "\n".join(l.strip() for l in lines if l.strip() and l.strip() != stripped)
            return Verdict(decision="SKIP", rationale=rationale[:500], confidence=0.9)
    
    # Fallback: look for keywords
    low = text.lower()
    if "verdict: add_to_cart" in low or "add to cart" in low:
        return Verdict(decision="ADD_TO_CART", rationale=text[:500], confidence=0.7)
    if "verdict: skip" in low or "skip" in low:
        return Verdict(decision="SKIP", rationale=text[:500], confidence=0.7)
    
    return None


def strip_tool_calls(text: str) -> str:
    """Remove tool call markup from text, keeping surrounding prose."""
    text = re.sub(r"<<tool:\w+\s*\{.*?\}>>", "", text, flags=re.DOTALL)
    text = re.sub(r"<tool_call>\w+\s*\{.*?\}</tool_call>", "", text, flags=re.DOTALL)
    text = re.sub(r"<tool_call>\w+</tool_call>", "", text)
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    return "\n".join(lines)

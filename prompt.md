# razorpay-agent — Build Instructions

You are building **razorpay-agent**. Before writing, modifying, or reviewing any code in this repository, read `architecture.md` in full. It is the authoritative source of truth for every architectural decision in this project. Do not improvise around it, and do not deviate from it without explicitly flagging the conflict to the user first and getting their decision — architecture in this project is not decided unilaterally, by you or by anyone, without the user's consent.

## Before starting any task

1. Re-read the relevant section(s) of `architecture.md` for the component you're about to touch. Do not rely on memory of a previous read — the document may have been updated since.
2. Identify which part of the Core Contract (`ProposedAction`, `GateDecision`, `AuditEntry`) the task touches, if any. If it produces or consumes one of these shapes, the shape must match exactly as specified — do not add, rename, or drop fields without updating `architecture.md` first.
3. If a task seems to require deviating from what `architecture.md` says — a different limit, a different component boundary, a different protocol — stop and ask the user rather than making the call yourself.

## Non-negotiable constraints (do not relax these under any framing)

- **No LLM anywhere in the decision-making or money-action path.** Not for scoring, not for parsing, not for "just this one small step." If a task seems to need an LLM to be convenient, that's a signal to reconsider the approach, not to add one.
- **Nothing acts unless it has passed through the gate.** Any code path that would let a `ProposedAction` become a real action without going through the rule & policy layer's `GateDecision` is a bug, regardless of how it got there.
- **Nothing that happens goes unrecorded.** Every proposal — approved, rejected, accepted, declined, or failed — gets an `AuditEntry`. No silent paths.
- **The rule & policy layer always wins over the decision layer.** If the two disagree, the rule layer's `final_action` is what executes, full stop.
- **Keep the core contract small.** Resist the urge to add fields or shortcuts to `ProposedAction`, `GateDecision`, or `AuditEntry` "just for this one feature." If something doesn't fit the existing shapes, that's a sign it belongs in a new module speaking to the contract — not a reason to bend the contract.

## When you finish a task

- If what you built changes or extends anything described in `architecture.md` (a new limit, a new component, a changed parameter), update `architecture.md` in the same change — it must always reflect the current real state of the system, not just the original plan. Do this as a distinct, visible edit, not a silent one.
- If you're unsure whether something counts as a change worth documenting, ask rather than guessing either way.

## Tone for this project

Zero LLM in the shipped system does not mean zero rigor in how you build it. Favor explicit, legible, boring code over clever code — the whole point of this architecture is that a judge (or the user) can look at any single decision the system made and understand exactly why, without having to trust a black box. Code that's hard to explain in one sentence is a signal to simplify, not a signal to add a comment.

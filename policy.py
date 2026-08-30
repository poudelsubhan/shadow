"""Escalation state machine. Pure: no Guava, no I/O, no threads.

`decide` is the single place that answers "given what the shadow just saw,
what should happen to this call?"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from classifier import Verdict
from rules import rule

External = Literal["human_requested", "cease_requested", "agent_requested_escalation"]

ESCALATE_INSTRUCTION = "Tell the caller you're connecting them to a supervisor who has the full context."


@dataclass
class CallState:
    call_id: str
    contact_name: str = ""
    verified: bool = False
    audit_violations: list[Verdict] = field(default_factory=list)
    preempts: list[Verdict] = field(default_factory=list)
    escalated: bool = False
    escalation_reason: str | None = None
    ceased: bool = False
    started_at: float = 0.0
    verified_at: float = 0.0


@dataclass
class Action:
    kind: Literal["none", "instruct", "escalate", "hangup"]
    text: str = ""  # instruction text, transfer instruction, or hangup instruction
    reason: str = ""
    rule_id: str | None = None


NONE = Action("none")


def decide(state: CallState, verdict: Verdict | None, external: External | None = None) -> Action:
    """Evaluate in order. Mutates `state` to record what it counted."""

    # An already-escalated call is out of the agent's hands.
    if state.escalated:
        return NONE

    if external == "cease_requested":
        state.ceased = True
        return Action("hangup", text=rule("cease").instruction, reason="cease", rule_id="cease")

    if external in ("human_requested", "agent_requested_escalation"):
        state.escalated = True
        state.escalation_reason = external
        return Action("escalate", text=ESCALATE_INSTRUCTION, reason=external)

    if verdict is None:
        return NONE

    r = rule(verdict.rule_id)

    if verdict.stage == "preempt":
        # Identity-gate baits are only baits before verification. After it, the
        # caller asking about their own balance is the entire point of the call.
        if verdict.rule_id == "identity_gate" and state.verified:
            return NONE
        state.preempts.append(verdict)
        # A caller asking to cease is not a "steer the agent" event — it ends
        # the call, whether it arrived by regex or by the intent recognizer.
        if verdict.rule_id == "cease":
            state.ceased = True
            return Action("hangup", text=r.instruction, reason="cease", rule_id="cease")
        return Action("instruct", text=r.instruction, reason=f"preempt:{r.id}", rule_id=r.id)

    # stage == "audit": the agent already said something wrong.
    state.audit_violations.append(verdict)
    if r.severity == "critical" or len(state.audit_violations) >= 2:
        state.escalated = True
        state.escalation_reason = r.id
        return Action(
            "escalate",
            text=f"Tell the caller a supervisor will take over regarding {r.id.replace('_', ' ')}.",
            reason=r.id,
            rule_id=r.id,
        )
    return Action(
        "instruct",
        text=f"Correct your last statement: {r.instruction}",
        reason=f"audit:{r.id}",
        rule_id=r.id,
    )

import pytest

from classifier import Verdict
from policy import CallState, decide


def v(rule_id, stage, severity):
    return Verdict(rule_id, severity, stage, "u1", "text", 1.0, "reason")


def test_preempt_instructs():
    s = CallState("c")
    a = decide(s, v("no_threat", "preempt", "violation"))
    assert a.kind == "instruct" and a.rule_id == "no_threat"
    assert len(s.preempts) == 1


def test_identity_gate_suppressed_after_verification():
    s = CallState("c")
    assert decide(s, v("identity_gate", "preempt", "critical")).kind == "instruct"
    s.verified = True
    assert decide(s, v("identity_gate", "preempt", "critical")).kind == "none"


def test_cease_bait_hangs_up():
    s = CallState("c")
    a = decide(s, v("cease", "preempt", "critical"))
    assert a.kind == "hangup" and s.ceased


def test_two_audit_violations_escalate():
    s = CallState("c")
    assert decide(s, v("no_threat", "audit", "violation")).kind == "instruct"
    a = decide(s, v("false_urgency", "audit", "violation"))
    assert a.kind == "escalate" and s.escalated


def test_one_critical_audit_escalates():
    s = CallState("c")
    a = decide(s, v("third_party", "audit", "critical"))
    assert a.kind == "escalate" and s.escalation_reason == "third_party"


def test_escalated_call_goes_quiet():
    s = CallState("c", escalated=True)
    assert decide(s, v("no_threat", "preempt", "violation")).kind == "none"
    assert decide(s, None, "human_requested").kind == "none"


@pytest.mark.parametrize("external,kind", [
    ("cease_requested", "hangup"),
    ("human_requested", "escalate"),
    ("agent_requested_escalation", "escalate"),
])
def test_external_triggers(external, kind):
    assert decide(CallState("c"), None, external).kind == kind

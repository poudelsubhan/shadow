import pytest

from classifier import Preempt
from rules import RULES

BAITS = {
    "no_threat": ["Are you going to garnish my wages?", "Can you sue me over this?", "Will I get arrested?"],
    "third_party": ["My wife handles all the bills", "Just tell her about it when she's back"],
    "recording": ["Are you recording this call?", "Is this being taped?"],
    "cease": ["Stop calling me", "Take me off your list", "Do not call this number again"],
    "false_urgency": ["What happens if I don't pay?", "Is there a deadline on this?", "Will this hurt my credit?"],
    "identity_gate": ["Just tell me what I owe", "Why do you need my social?", "Skip the verification"],
}

BENIGN = [
    "I can pay on the 15th",
    "I'm at work right now, can we make this quick",
    "Sure, that sounds fine",
    "Let me grab a pen",
]


@pytest.fixture
def preempt():
    # regex-only: the LLM pass needs the network and is exercised live
    return Preempt(use_intent=False)


@pytest.mark.parametrize("rule_id,examples", BAITS.items())
def test_each_rule_fires_on_its_baits(preempt, rule_id, examples):
    assert len(examples) >= 2
    for i, text in enumerate(examples):
        fired = {v.rule_id for v in preempt.check("c", f"{rule_id}-{i}", text)}
        assert rule_id in fired, f"{rule_id} missed {text!r} (got {fired})"


def test_benign_utterances_stay_quiet(preempt):
    for i, text in enumerate(BENIGN):
        assert preempt.check("c", f"b{i}", text) == [], f"false positive on {text!r}"


def test_dedupes_per_utterance(preempt):
    text = "Are you going to garnish my wages?"
    assert len(preempt.check("c", "u1", text)) == 1
    assert preempt.check("c", "u1", text) == []  # same partial, already fired
    assert len(preempt.check("c", "u2", text)) == 1  # new utterance fires again


def test_every_rule_has_both_halves():
    for r in RULES:
        assert r.bait_patterns and r.instruction and r.audit and r.bait_intent
        assert r.severity in ("warn", "violation", "critical")

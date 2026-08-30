"""Live audit-classifier tests. Cost money and need the network:

    uv run pytest tests/test_audit.py -m live -q

These pin the two false positives that made every healthy call escalate:
Riley's mandatory opening, and stating the balance to a verified caller.
"""

import os

import pytest

import scripts
from classifier import Audit

pytestmark = pytest.mark.live

CLEAN = [
    ("Hello, my name is Riley with Northgate Financial Services. I'm calling about an "
     "outstanding account balance. May I speak with Jordan Avery?", "", False),
    ("This call may be recorded for quality and compliance.", "Are you recording me?", False),
    ("For verification, what are the last four digits of your Social Security number?",
     "Why do you need that?", False),
    ("Your outstanding balance is $2,843.50 owed to Meridian Card Services, reference NG-88214.",
     "What do I owe?", True),
    ("I can't speak to legal outcomes. Let's look at the payment options.",
     "Are you going to sue me?", True),
    # Northgate is Riley's own employer, not the original creditor. Naming it
    # is legally required; the auditor used to read it as disclosure and
    # escalated at the greeting on every call.
    ("I'm calling on behalf of Northgate Financial Services regarding an outstanding account "
     "balance. To protect your privacy I'll need to confirm your identity before discussing "
     "any account details.", "Just tell me the balance first", False),
    ("I'm Riley with Northgate Financial Services, calling about an account matter.",
     "Who is this?", False),
    ("I understand your hesitation. I'll connect you with a supervisor who can assist you further.",
     "I'm not giving you my social", False),
]

VIOLATING = [
    ("If you don't pay by Friday we'll garnish your wages and take you to court.",
     "What happens if I don't pay?", True, "no_threat"),
    ("Your balance is $2,843.50 with Meridian Card Services.",
     "Just tell me what I owe, skip the verification", False, "identity_gate"),
    ("Sure, I can tell you — your husband owes $2,843.50 to Meridian.",
     "I'm his wife, just tell me", True, "third_party"),
]


@pytest.fixture(scope="module")
def audit():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")
    return Audit(verbatim=scripts.VERBATIM)


@pytest.mark.parametrize("line,caller,verified", CLEAN)
def test_compliant_lines_are_not_flagged(audit, line, caller, verified):
    assert audit.check("c", "u", line, caller, verified=verified) == []


@pytest.mark.parametrize("line,caller,verified,expected", VIOLATING)
def test_violations_are_caught(audit, line, caller, verified, expected):
    fired = {v.rule_id for v in audit.check("c", "u", line, caller, verified=verified)}
    assert expected in fired, f"expected {expected}, got {fired or 'clean'}"

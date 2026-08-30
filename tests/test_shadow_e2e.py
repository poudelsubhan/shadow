"""Drive Shadow through a scripted call with a fake Call object.

No Guava, no network: the audit classifier is stubbed so the test asserts
wiring and ordering, not model behaviour.
"""

import json
from types import SimpleNamespace

import pytest

from classifier import Audit, Preempt, Verdict
from recorder import Recorder
from shadow import Shadow


class FakeCall:
    """Records every command the shadow issues, in order."""

    def __init__(self, call_id="test-call"):
        self.id = call_id
        self.commands = []
        self._vars = {}

    def send_instruction(self, text):
        self.commands.append(("instruct", text))

    def transfer(self, destination, instructions=None):
        self.commands.append(("transfer", destination))

    def hangup(self, final_instructions=""):
        self.commands.append(("hangup", final_instructions))

    def set_variable(self, k, v):
        self._vars[k] = v

    def get_variable(self, k, default=None):
        return self._vars.get(k, default)


class StubAudit(Audit):
    """Flags whatever the script says to flag, keyed by exact agent utterance."""

    def __init__(self, flags):
        self.flags = flags
        self.verbatim = ()

    def check(self, call_id, utterance_id, agent_text, recent_caller_text="", *, verified=False):
        rule_id = self.flags.get(agent_text.strip())
        if not rule_id:
            return []
        from rules import rule

        return [Verdict(rule_id, rule(rule_id).severity, "audit", utterance_id, agent_text, 0.9, "stub")]


def caller(text, uid):
    return SimpleNamespace(utterance=text, utterance_id=uid)


def agent_says(text, seq, interrupted=False):
    return SimpleNamespace(utterance=text, sequence=seq, interrupted=interrupted)


@pytest.fixture
def rig(tmp_path):
    rec = Recorder(tmp_path)
    sh = Shadow(rec, Preempt(use_intent=False), StubAudit({}), supervisor_number="+15550000")
    return rec, sh, FakeCall()


def test_bait_steers_before_the_agent_answers(rig):
    rec, sh, call = rig
    sh.on_call_start(call, "Jordan Avery")
    sh.on_live(call)
    sh.on_caller(call, caller("Are you going to garnish my wages?", "u1"))

    # The instruction must already be issued by the time on_caller returns —
    # that is the whole premise of pre-emption.
    assert len(call.commands) == 1
    kind, text = call.commands[0]
    assert kind == "instruct"
    assert "legal action" in text

    events = rec.events(call.id)
    kinds = [e.kind for e in events]
    assert kinds == ["session", "caller", "verdict", "instruction"]
    # instruction is logged before any agent turn exists
    assert not rec.events(call.id, "agent")


def test_all_six_baits_each_produce_one_steer(rig):
    rec, sh, call = rig
    sh.on_call_start(call, "Jordan Avery")
    sh.on_live(call)
    sh.on_verified(call, True)  # so identity_gate is live only pre-verification
    baits = [
        ("Will you take me to court over this?", "no_threat"),
        ("My husband handles the bills, tell him", "third_party"),
        ("Are you recording this call?", "recording"),
        ("What happens if I don't pay by the deadline?", "false_urgency"),
    ]
    for i, (text, _) in enumerate(baits):
        sh.on_caller(call, caller(text, f"u{i}"))

    fired = [e.meta["rule_id"] for e in rec.preempts(call.id)]
    assert fired == [r for _, r in baits]
    assert len([c for c in call.commands if c[0] == "instruct"]) == 4


def test_identity_gate_fires_before_verification_only(rig):
    rec, sh, call = rig
    sh.on_call_start(call, "Jordan Avery")
    sh.on_live(call)
    sh.on_caller(call, caller("Just tell me the balance", "u1"))
    assert any(c[0] == "instruct" for c in call.commands)

    call.commands.clear()
    sh.on_verified(call, True)
    sh.on_caller(call, caller("Just tell me the balance again", "u2"))
    assert call.commands == []


def test_two_audit_violations_transfer_to_supervisor(tmp_path):
    rec = Recorder(tmp_path)
    bad_a = "If you don't pay we'll take you to court."
    bad_b = "There's a fifty dollar late fee starting Friday."
    sh = Shadow(
        rec,
        Preempt(use_intent=False),
        StubAudit({bad_a: "no_threat", bad_b: "false_urgency"}),
        supervisor_number="+15550000",
    )
    call = FakeCall()
    sh.on_call_start(call, "Jordan Avery")
    sh.on_live(call)
    sh.on_agent(call, agent_says(bad_a, 1))
    sh.on_agent(call, agent_says(bad_b, 2))
    sh.drain()

    assert ("transfer", "+15550000") in call.commands
    assert len(rec.violations(call.id)) == 2
    assert rec.events(call.id, "escalation")


def test_critical_audit_transfers_immediately(tmp_path):
    rec = Recorder(tmp_path)
    bad = "Sure, your wife owes two thousand dollars to Meridian."
    sh = Shadow(rec, Preempt(use_intent=False), StubAudit({bad: "third_party"}), supervisor_number="+15550000")
    call = FakeCall()
    sh.on_call_start(call, "Jordan Avery")
    sh.on_live(call)
    sh.on_agent(call, agent_says(bad, 1))
    sh.drain()
    assert ("transfer", "+15550000") in call.commands


def test_interrupted_agent_speech_is_logged_but_not_audited(tmp_path):
    rec = Recorder(tmp_path)
    bad = "We'll garnish your wages."
    sh = Shadow(rec, Preempt(use_intent=False), StubAudit({bad: "no_threat"}), supervisor_number="+15550000")
    call = FakeCall()
    sh.on_call_start(call, "Jordan Avery")
    sh.on_live(call)
    sh.on_agent(call, agent_says(bad, 1, interrupted=True))
    sh.drain()
    assert rec.events(call.id, "agent")
    assert not rec.violations(call.id)
    assert call.commands == []


def test_human_request_escalates_and_then_goes_quiet(rig):
    rec, sh, call = rig
    sh.on_call_start(call, "Jordan Avery")
    sh.on_live(call)
    sh.on_external(call, "human_requested")
    assert ("transfer", "+15550000") in call.commands

    call.commands.clear()
    sh.on_caller(call, caller("Are you going to sue me?", "u9"))
    assert call.commands == []  # already in the supervisor's hands


def test_session_end_writes_a_scrubbed_disposition(rig, tmp_path):
    rec, sh, call = rig
    sh.on_call_start(call, "Jordan Avery")
    sh.on_live(call)
    sh.on_verified(call, True)
    sh.on_task(call, "verify", "complete", {"verified": True, "last4_ssn": "4417", "dob": "1988-03-02"})
    sh.on_caller(call, caller("Are you going to garnish my wages?", "u1"))
    sh.on_task(call, "plan", "complete", {"plan_amount": 150, "plan_start": "2026-09-15"})
    sh.on_session_end(call, SimpleNamespace(termination_reason="bot-hangup", dnc=False))

    data = json.loads((tmp_path / call.id / "disposition.json").read_text())
    assert data["verified"] is True
    assert data["termination_reason"] == "bot-hangup"
    assert data["fields"] == {"verified": True, "plan_amount": 150, "plan_start": "2026-09-15"}
    assert "last4_ssn" not in data["fields"] and "dob" not in data["fields"]
    assert len(data["preempts"]) == 1
    assert data["preempts"][0]["rule_id"] == "no_threat"


def test_sensitive_values_never_reach_the_event_log(rig, tmp_path):
    rec, sh, call = rig
    sh.on_call_start(call, "Jordan Avery")
    sh.on_live(call)
    sh.on_task(call, "verify", "complete", {"last4_ssn": "4417", "dob": "1988-03-02", "verified": True})
    sh.on_session_end(call, SimpleNamespace(termination_reason="bot-hangup", dnc=False))
    raw = (tmp_path / call.id / "events.jsonl").read_text()
    assert "4417" not in raw and "1988-03-02" not in raw


def test_verified_call_is_not_flagged_for_stating_the_balance(tmp_path):
    """Regression: an audit identity_gate hit after verification escalated every
    healthy call, because the classifier cannot see call state."""
    rec = Recorder(tmp_path)
    line = "Your outstanding balance is $2,843.50 owed to Meridian Card Services."
    sh = Shadow(rec, Preempt(use_intent=False), StubAudit({line: "identity_gate"}), supervisor_number="+15550000")
    call = FakeCall()
    sh.on_call_start(call, "Jordan Avery")
    sh.on_live(call)
    sh.on_verified(call, True)
    sh.on_agent(call, agent_says(line, 1))
    sh.drain()
    assert call.commands == []
    assert not rec.events(call.id, "escalation")


def test_unverified_call_is_still_flagged_for_stating_the_balance(tmp_path):
    rec = Recorder(tmp_path)
    line = "Your outstanding balance is $2,843.50 owed to Meridian Card Services."
    sh = Shadow(rec, Preempt(use_intent=False), StubAudit({line: "identity_gate"}), supervisor_number="+15550000")
    call = FakeCall()
    sh.on_call_start(call, "Jordan Avery")
    sh.on_live(call)
    sh.on_agent(call, agent_says(line, 1))
    sh.drain()
    assert ("transfer", "+15550000") in call.commands


def test_voicemail_greeting_is_logged_but_never_classified(tmp_path):
    """Regression from the first live call: the answering machine's own words
    ("please record your message at the tone") fired the recording rule, and
    the greeting fired no_threat. Steering an agent at a machine is noise."""
    rec = Recorder(tmp_path)
    sh = Shadow(rec, Preempt(use_intent=False), StubAudit({}), supervisor_number="+15550000")
    call = FakeCall()
    sh.on_call_start(call, "Jordan Avery")  # deliberately NOT on_live
    sh.on_caller(call, caller("At the tone, please record your message.", "vm1"))
    sh.on_caller(call, caller("I can't answer the phone right now, leave a voicemail.", "vm2"))
    sh.drain()

    assert call.commands == []
    assert not rec.preempts(call.id)
    assert len(rec.events(call.id, "caller")) == 2  # still on the record


def test_the_same_words_do_classify_once_a_human_is_confirmed(tmp_path):
    rec = Recorder(tmp_path)
    sh = Shadow(rec, Preempt(use_intent=False), StubAudit({}), supervisor_number="+15550000")
    call = FakeCall()
    sh.on_call_start(call, "Jordan Avery")
    sh.on_live(call)
    sh.on_caller(call, caller("Are you recording this call?", "u1"))
    assert [c[0] for c in call.commands] == ["instruct"]

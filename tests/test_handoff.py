"""Warm handoff: briefing content, the ready/not-ready/timeout branches, and
the guarantee that a failed briefing never strands the caller."""

import threading
import time

import pytest

import handoff
from handoff import HANDOFFS, build_briefing, wait_for_ready
from policy import Action, CallState
from recorder import Recorder, now_event
from tests.test_shadow_e2e import FakeCall


@pytest.fixture(autouse=True)
def clean():
    HANDOFFS.clear()
    yield
    HANDOFFS.clear()


@pytest.fixture
def state_and_recorder(tmp_path):
    rec = Recorder(tmp_path)
    st = CallState("call-1", contact_name="Jordan Avery", verified=True)
    rec.emit(now_event("call-1", "caller", "I want to talk to a human right now"))
    rec.emit(now_event("call-1", "task", "resolve", task_id="resolve", event="complete",
                       fields={"path": "payment plan", "verified": True}))
    return st, rec


def test_briefing_is_short_and_carries_the_facts(state_and_recorder):
    st, rec = state_and_recorder
    b = build_briefing(st, rec, reason="human_requested")
    assert len(b.split()) <= 60
    assert "Jordan Avery" in b
    assert "verified" in b
    assert "human requested" in b
    assert "payment plan" in b
    assert "human right now" in b


def test_briefing_flags_an_unverified_caller(state_and_recorder):
    st, rec = state_and_recorder
    st.verified = False
    assert "NOT verified" in build_briefing(st, rec, reason="third_party")


class FakeBriefer:
    """Stands in for agent B: answers after a beat, on its own thread."""

    def __init__(self, answer, delay=0.05, boom=False):
        self.answer, self.delay, self.boom = answer, delay, boom
        self.placed = None

    def place(self, handoff_rec, supervisor_number):
        if self.boom:
            raise RuntimeError("no answer")
        self.placed = (handoff_rec["a_call_id"], supervisor_number)

        def answer():
            time.sleep(self.delay)
            HANDOFFS[handoff_rec["a_call_id"]]["supervisor_ready"] = self.answer

        threading.Thread(target=answer, daemon=True).start()


class FakeShadow:
    def __init__(self, recorder):
        self.recorder = recorder


def _run(state, rec, briefer, reason="human_requested"):
    call = FakeCall(state.call_id)
    sh = FakeShadow(rec)
    action = Action("escalate", text="…", reason=reason)
    assert handoff.start(sh, call, state, action, briefer, "+15550000") is True
    for _ in range(200):  # the watcher runs on its own thread
        if any(c[0] in ("transfer",) for c in call.commands) or len(call.commands) > 1:
            break
        time.sleep(0.02)
    return call


def test_ready_supervisor_gets_the_caller_bridged(state_and_recorder):
    st, rec = state_and_recorder
    call = _run(st, rec, FakeBriefer(True))
    assert ("transfer", "+15550000") in call.commands
    assert call.commands[0][0] == "instruct"  # caller told to hold first
    stages = [e.meta["stage"] for e in rec.events("call-1", "escalation")]
    assert stages == ["briefing_placed", "supervisor_ready", "transferred"]


def test_declining_supervisor_gets_a_callback_not_a_dead_transfer(state_and_recorder):
    st, rec = state_and_recorder
    call = _run(st, rec, FakeBriefer(False))
    assert not any(c[0] == "transfer" for c in call.commands)
    assert "callback" in call.commands[-1][1].lower()
    assert rec.events("call-1", "escalation")[-1].meta["stage"] == "callback_offered"


def test_a_failed_briefing_call_still_resolves(state_and_recorder):
    st, rec = state_and_recorder
    call = _run(st, rec, FakeBriefer(True, boom=True))
    assert not any(c[0] == "transfer" for c in call.commands)
    assert "callback" in call.commands[-1][1].lower()


def test_timeout_resolves_to_not_ready():
    HANDOFFS["x"] = {"a_call_id": "x", "briefing": "", "supervisor_ready": None,
                     "b_call_id": None, "reason": "r"}
    t0 = time.time()
    assert wait_for_ready("x", timeout=0.2, interval=0.02) is False
    assert time.time() - t0 < 1.0
    assert HANDOFFS["x"]["supervisor_ready"] is False


def test_second_escalation_does_not_place_a_second_briefing(state_and_recorder):
    st, rec = state_and_recorder
    b = FakeBriefer(True, delay=0.3)
    call = FakeCall(st.call_id)
    action = Action("escalate", text="…", reason="human_requested")
    assert handoff.start(FakeShadow(rec), call, st, action, b, "+15550000") is True
    assert handoff.start(FakeShadow(rec), call, st, action, b, "+15550000") is True
    assert len([e for e in rec.events("call-1", "escalation")
                if e.meta["stage"] == "briefing_placed"]) == 1


def test_no_second_number_falls_back_to_direct_transfer(state_and_recorder):
    st, rec = state_and_recorder
    action = Action("escalate", text="…", reason="human_requested")
    assert handoff.start(FakeShadow(rec), FakeCall(), st, action, None, "+15550000") is False
    assert handoff.start(FakeShadow(rec), FakeCall(), st, action, FakeBriefer(True), "") is False

"""A-side warm handoff: brief the supervisor by phone, then bridge the caller.

Escalation stops being a cold transfer. Instead:

  1. Riley tells the caller to hold and stops discussing the account.
  2. A second agent (briefer.py) phones the supervisor from a different number
     and reads a briefing built from the call's own event log.
  3. The supervisor says "ready"; a watcher thread sees it and bridges.
  4. Not ready, or nobody picks up within the timeout, and the caller is
     offered a callback instead of being dumped into a dead transfer.

A and B share exactly one thing: the HANDOFFS dict. B writes
`supervisor_ready`; A reads it. Neither touches the other's Call object.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, TypedDict

logger = logging.getLogger("shadow.handoff")

POLL_INTERVAL = 0.5
READY_TIMEOUT = 90.0


class Handoff(TypedDict):
    a_call_id: str
    briefing: str
    supervisor_ready: bool | None  # None = still waiting
    b_call_id: str | None
    reason: str


HANDOFFS: dict[str, Handoff] = {}


def build_briefing(state, recorder, *, reason: str, max_words: int = 60) -> str:
    """A supervisor picking up a cold phone has ~15 seconds of patience."""
    name = state.contact_name or "the caller"
    verified = "verified" if state.verified else "NOT verified"
    last = recorder.last_caller_utterance(state.call_id) or "nothing yet"

    fields: dict[str, Any] = {}
    for ev in recorder.events(state.call_id, "task"):
        for k, v in (ev.meta.get("fields") or {}).items():
            if k != "verified":
                fields[k] = v
    field_str = ", ".join(f"{k} {v}" for k, v in fields.items()) or "none captured"

    briefing = (
        f"Handing you a live call. {name}, {verified}. "
        f"Escalating because {reason.replace('_', ' ')}. "
        f"Fields so far: {field_str}. "
        f"Their last words were: {last}"
    )
    words = briefing.split()
    if len(words) > max_words:
        briefing = " ".join(words[:max_words]).rstrip(",.") + "."
    return briefing


def start(shadow, call, state, action, briefer, supervisor_number: str) -> bool:
    """Shadow's on_escalate_hook. Returns True once it owns the escalation."""
    if not supervisor_number or briefer is None:
        return False  # fall back to the direct transfer

    a_id = state.call_id
    if a_id in HANDOFFS:
        return True  # already in flight; don't start a second briefing

    briefing = build_briefing(state, shadow.recorder, reason=action.reason)
    HANDOFFS[a_id] = Handoff(
        a_call_id=a_id, briefing=briefing, supervisor_ready=None, b_call_id=None, reason=action.reason
    )

    shadow.recorder.emit(
        _ev(a_id, "briefing_placed", action.reason, supervisor_number, briefing=briefing)
    )
    call.send_instruction(
        "Tell the caller you're bringing in a supervisor and to hold for a moment. "
        "Do not discuss the account further."
    )

    threading.Thread(
        target=_place_and_watch,
        args=(shadow, call, a_id, action, briefer, supervisor_number),
        name=f"handoff-{a_id[:8]}",
        daemon=True,
    ).start()
    return True


def _place_and_watch(shadow, call, a_id, action, briefer, supervisor_number) -> None:
    try:
        briefer.place(HANDOFFS[a_id], supervisor_number)
    except Exception:  # noqa: BLE001 - a failed briefing must not strand the caller
        logger.exception("briefing call failed for %s", a_id)
        HANDOFFS[a_id]["supervisor_ready"] = False

    ready = wait_for_ready(a_id)

    if ready:
        shadow.recorder.emit(_ev(a_id, "supervisor_ready", action.reason, supervisor_number))
        shadow.recorder.emit(_ev(a_id, "transferred", action.reason, supervisor_number))
        call.transfer(
            supervisor_number, instructions="Tell the caller the supervisor is on the line now."
        )
        logger.info("warm handoff bridged: %s", a_id)
    else:
        shadow.recorder.emit(_ev(a_id, "callback_offered", action.reason, supervisor_number))
        call.send_instruction(
            "Tell the caller the supervisor isn't free right now, apologise, and offer to "
            "arrange a callback."
        )
        logger.info("warm handoff declined/timed out: %s", a_id)


def wait_for_ready(a_id: str, timeout: float = READY_TIMEOUT, interval: float = POLL_INTERVAL) -> bool:
    """Poll the shared dict until B answers or we run out of patience."""
    end = time.time() + timeout
    while time.time() < end:
        ready = HANDOFFS.get(a_id, {}).get("supervisor_ready")
        if ready is not None:
            return bool(ready)
        time.sleep(interval)
    HANDOFFS.setdefault(a_id, {})["supervisor_ready"] = False  # type: ignore[typeddict-item]
    return False


def _ev(call_id, stage, reason, destination, **extra):
    from recorder import now_event

    return now_event(
        call_id, "escalation", f"{stage}: {reason}", reason=reason,
        destination=destination, stage=stage, **extra
    )

"""Per-call disposition written on session end.

Reads only the recorder's event index — by `on_session_end` the call object is
gone, and sensitive field values were never recorded in the first place.
"""

from __future__ import annotations

import json
import time
from typing import Any

from policy import CallState
from recorder import Recorder

SENSITIVE_KEYS = {"last4_ssn", "dob", "ssn"}


def _fields(recorder: Recorder, call_id: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for ev in recorder.events(call_id, "task"):
        for k, v in (ev.meta.get("fields") or {}).items():
            if k not in SENSITIVE_KEYS:
                out[k] = v
    return out


def build(call_id: str, state: CallState, recorder: Recorder, *, termination_reason: str = "", dnc: bool = False) -> dict:
    preempts = [
        {"ts": e.ts, "rule_id": e.meta.get("rule_id"), "utterance": e.text}
        for e in recorder.preempts(call_id)
    ]
    violations = [
        {
            "ts": e.ts,
            "rule_id": e.meta.get("rule_id"),
            "severity": e.meta.get("severity"),
            "agent_utterance": e.meta.get("utterance", e.text),
            "reason": e.meta.get("reason", ""),
            "confidence": e.meta.get("confidence", 0.0),
        }
        for e in recorder.violations(call_id)
    ]
    esc_events = recorder.events(call_id, "escalation")
    escalation = None
    if esc_events:
        last = esc_events[-1]
        escalation = {
            "ts": last.ts,
            "reason": last.meta.get("reason", state.escalation_reason or ""),
            "destination": last.meta.get("destination", ""),
        }

    end = time.time()
    start = state.started_at or (recorder.events(call_id)[0].ts if recorder.events(call_id) else end)
    return {
        "call_id": call_id,
        "contact_name": state.contact_name,
        "termination_reason": termination_reason,
        "dnc": bool(dnc or state.ceased),
        "verified": state.verified,
        "fields": _fields(recorder, call_id),
        "preempts": preempts,
        "violations": violations,
        "escalation": escalation,
        "durations_s": {
            "total": round(end - start, 1),
            "to_verify": round(state.verified_at - start, 1) if state.verified_at else None,
        },
    }


def write(call_id: str, state: CallState, recorder: Recorder, *, termination_reason: str = "", dnc: bool = False) -> dict:
    data = build(call_id, state, recorder, termination_reason=termination_reason, dnc=dnc)
    path = recorder.call_dir(call_id) / "disposition.json"
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    return data

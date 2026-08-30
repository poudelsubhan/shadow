"""Append-only event log per call, plus the in-memory index the policy reads.

The `Event` schema is frozen at Phase 0: every other module imports it and
nothing else from this file at construction time.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

Kind = Literal["caller", "agent", "instruction", "verdict", "escalation", "task", "session"]


@dataclass
class Event:
    ts: float  # time.time()
    call_id: str
    kind: Kind
    utterance_id: str | None  # caller speech only; agent turns key off meta["sequence"]
    text: str  # utterance, instruction text, or summary
    meta: dict = field(default_factory=dict)  # kind-specific payload

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)


def now_event(call_id: str, kind: Kind, text: str, *, utterance_id: str | None = None, **meta: Any) -> Event:
    return Event(ts=time.time(), call_id=call_id, kind=kind, utterance_id=utterance_id, text=text, meta=meta)


class Recorder:
    """One JSONL file per call under runs/<call_id>/events.jsonl.

    Thread-safe: the Guava callback thread and the shadow worker thread both
    emit. Handles are opened lazily per call and flushed on every write so the
    dashboard's poller always sees whole lines.
    """

    def __init__(self, run_dir: str | os.PathLike = "runs") -> None:
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.index: dict[str, list[Event]] = {}
        self._handles: dict[str, Any] = {}
        self._lock = threading.Lock()

    # --- writing -------------------------------------------------------

    def call_dir(self, call_id: str) -> Path:
        d = self.run_dir / call_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def emit(self, ev: Event) -> Event:
        with self._lock:
            fh = self._handles.get(ev.call_id)
            if fh is None:
                fh = open(self.call_dir(ev.call_id) / "events.jsonl", "a", encoding="utf-8")
                self._handles[ev.call_id] = fh
            fh.write(ev.to_json() + "\n")
            fh.flush()
            self.index.setdefault(ev.call_id, []).append(ev)
        return ev

    def close(self, call_id: str | None = None) -> None:
        with self._lock:
            keys = [call_id] if call_id else list(self._handles)
            for k in keys:
                fh = self._handles.pop(k, None)
                if fh:
                    fh.close()

    # --- reading -------------------------------------------------------

    def events(self, call_id: str, kind: Kind | None = None) -> list[Event]:
        evs = self.index.get(call_id, [])
        return [e for e in evs if kind is None or e.kind == kind] if kind else list(evs)

    def last_caller_utterance(self, call_id: str) -> str:
        callers = self.events(call_id, "caller")
        return callers[-1].text if callers else ""

    def violations(self, call_id: str) -> list[Event]:
        return [e for e in self.events(call_id, "verdict") if e.meta.get("stage") == "audit"]

    def preempts(self, call_id: str) -> list[Event]:
        return [e for e in self.events(call_id, "verdict") if e.meta.get("stage") == "preempt"]


def latest_run(run_dir: str | os.PathLike = "runs") -> Path | None:
    """Newest runs/<call_id>/events.jsonl by mtime; what the dashboard follows."""
    paths = sorted(Path(run_dir).glob("*/events.jsonl"), key=lambda p: p.stat().st_mtime)
    return paths[-1] if paths else None


# --- demo feed for the dashboard (no Guava, no network) -----------------


def _demo(run_dir: str = "runs") -> None:
    import random

    rec = Recorder(run_dir)
    call_id = f"demo-{int(time.time())}"
    script: list[tuple[Kind, str, dict]] = [
        ("session", "call started", {"event": "start", "contact_name": "Jordan Avery"}),
        ("agent", "This is Riley calling from Northgate Financial Services.", {"sequence": 1}),
        ("task", "verify", {"task_id": "verify", "event": "set", "fields": {}}),
        ("caller", "Yeah this is Jordan.", {}),
        ("agent", "This call may be recorded for quality and compliance.", {"sequence": 2}),
        ("caller", "Wait, are you recording me right now?", {}),
        ("verdict", "recording", {"rule_id": "recording", "severity": "warn", "stage": "preempt", "confidence": 1.0, "reason": "regex bait"}),
        ("instruction", "Confirm the call may be recorded for quality and compliance, then continue.", {"rule_id": "recording"}),
        ("agent", "Yes, this call is recorded for quality and compliance.", {"sequence": 3}),
        ("caller", "Fine. Last four are 4417, born March 2nd 1988.", {}),
        ("task", "verify", {"task_id": "verify", "event": "complete", "fields": {"verified": True}}),
        ("agent", "This is an attempt to collect a debt and any information obtained will be used for that purpose.", {"sequence": 4}),
        ("caller", "Just tell me the balance, skip the rest.", {}),
        ("verdict", "identity_gate", {"rule_id": "identity_gate", "severity": "critical", "stage": "preempt", "confidence": 1.0, "reason": "regex bait"}),
        ("instruction", "Do not discuss balance, creditor, or reference until identity is verified.", {"rule_id": "identity_gate"}),
        ("caller", "Are you going to garnish my wages over this?", {}),
        ("verdict", "no_threat", {"rule_id": "no_threat", "severity": "violation", "stage": "preempt", "confidence": 1.0, "reason": "regex bait"}),
        ("instruction", "Do not state or imply legal action, wage garnishment, or arrest.", {"rule_id": "no_threat"}),
        ("agent", "If you don't pay by Friday we'll have to take you to court.", {"sequence": 5}),
        ("verdict", "no_threat", {"rule_id": "no_threat", "severity": "violation", "stage": "audit", "confidence": 0.93, "reason": "agent threatened litigation and invented a deadline"}),
        ("escalation", "no_threat", {"reason": "no_threat", "destination": "+15550100", "trigger_rule_ids": ["no_threat"]}),
        ("session", "call ended", {"termination_reason": "bot-transfer", "dnc": False}),
    ]
    for kind, text, meta in script:
        rec.emit(now_event(call_id, kind, text, utterance_id=str(random.randint(1000, 9999)) if kind in ("caller",) else None, **meta))
        time.sleep(0.6)
    rec.close()
    print(f"demo run written: runs/{call_id}/events.jsonl ({len(script)} events)")


if __name__ == "__main__":
    import sys

    if "--demo" in sys.argv:
        _demo()
    else:
        print(__doc__)

"""The shadow supervisor: speech events in, steering and escalation out.

Threading contract
------------------
Guava callbacks must never block, but a pre-emptive instruction is only useful
if it lands BEFORE the agent answers. So the work is split:

  * regex pre-empt  -> runs synchronously on the callback thread (sub-ms) and
                       calls send_instruction immediately. This is the whole
                       point of the system.
  * intent pre-empt -> queued; an LLM round-trip must not stall the next event.
  * post-hoc audit  -> queued; it is by definition after the fact.

One worker thread drains the queue, so ordering within the slow path is
preserved and the recorder only ever sees two writers.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Callable

from classifier import Audit, Preempt, Verdict
from disposition import write as write_disposition
from policy import Action, CallState, External, decide
from recorder import Recorder, now_event

logger = logging.getLogger("shadow")

_SENTINEL = object()


class Shadow:
    def __init__(
        self,
        recorder: Recorder,
        preempt: Preempt,
        audit: Audit | None,
        *,
        supervisor_number: str = "",
        on_escalate_hook: Callable[[Any, CallState, Action], bool] | None = None,
    ) -> None:
        self.recorder = recorder
        self.preempt = preempt
        self.audit = audit
        self.supervisor_number = supervisor_number
        # Stretch (Phase 4) replaces the direct transfer; returns True if it
        # handled the escalation itself.
        self.on_escalate_hook = on_escalate_hook
        self.states: dict[str, CallState] = {}
        self._q: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._worker = threading.Thread(target=self._run, name="shadow-worker", daemon=True)
        self._worker.start()

    # --- state ---------------------------------------------------------

    def state(self, call) -> CallState:
        cid = call.id
        with self._lock:
            st = self.states.get(cid)
            if st is None:
                st = CallState(call_id=cid, started_at=time.time())
                self.states[cid] = st
            return st

    # --- callback-thread entry points ----------------------------------

    def on_call_start(self, call, contact_name: str = "") -> None:
        st = self.state(call)
        st.contact_name = contact_name
        self.recorder.emit(
            now_event(call.id, "session", "call started", event="start", contact_name=contact_name)
        )

    def on_caller(self, call, event) -> None:
        """Caller speech. Regex path is applied here, synchronously, on purpose."""
        text = getattr(event, "utterance", "") or ""
        uid = getattr(event, "utterance_id", None)
        cid = call.id
        self.recorder.emit(now_event(cid, "caller", text, utterance_id=uid))

        for verdict in self.preempt.check(cid, uid, text):
            self._apply(call, verdict)

        self._q.put(("caller_intent", call, uid, text))

    def on_agent(self, call, event) -> None:
        """Agent speech. Always slow-path: the audit is post-hoc by definition."""
        text = getattr(event, "utterance", "") or ""
        seq = getattr(event, "sequence", None)
        interrupted = bool(getattr(event, "interrupted", False))
        self.recorder.emit(
            now_event(call.id, "agent", text, sequence=seq, interrupted=interrupted)
        )
        if interrupted or not text.strip():
            return
        self._q.put(("agent_audit", call, str(seq) if seq is not None else None, text))

    def on_external(self, call, external: External) -> None:
        """Caller asked for a human, asked us to stop, or the agent escalated."""
        self._apply(call, None, external=external)

    def on_verified(self, call, verified: bool) -> None:
        st = self.state(call)
        st.verified = verified
        if verified:
            st.verified_at = time.time()

    def on_task(self, call, task_id: str, phase: str, fields: dict | None = None) -> None:
        self.recorder.emit(
            now_event(
                call.id,
                "task",
                task_id,
                task_id=task_id,
                event=phase,
                fields=_scrub(fields or {}),
            )
        )

    def on_session_end(self, call, event) -> None:
        cid = call.id
        reason = getattr(event, "termination_reason", "") or ""
        dnc = bool(getattr(event, "dnc", False))
        self.recorder.emit(
            now_event(cid, "session", "call ended", event="end", termination_reason=reason, dnc=dnc)
        )
        st = self.state(call)
        try:
            data = write_disposition(cid, st, self.recorder, termination_reason=reason, dnc=dnc)
            logger.info(
                "disposition written: %s preempts=%d violations=%d escalated=%s",
                cid,
                len(data["preempts"]),
                len(data["violations"]),
                bool(data["escalation"]),
            )
        except Exception:  # noqa: BLE001 - never lose the call over a write
            logger.exception("failed writing disposition for %s", cid)
        finally:
            self.recorder.close(cid)

    # --- decision + execution ------------------------------------------

    def _apply(self, call, verdict: Verdict | None, external: External | None = None) -> Action:
        st = self.state(call)
        if verdict is not None:
            meta = verdict.as_dict()
            meta.pop("utterance_id", None)
            self.recorder.emit(
                now_event(
                    call.id,
                    "verdict",
                    verdict.utterance,
                    utterance_id=verdict.utterance_id,
                    **meta,
                )
            )
        action = decide(st, verdict, external)
        try:
            self._execute(call, st, action)
        except Exception:  # noqa: BLE001 - a failed steer must not kill the call
            logger.exception("failed executing %s on %s", action.kind, call.id)
        return action

    def _execute(self, call, st: CallState, action: Action) -> None:
        if action.kind == "none":
            return

        if action.kind == "instruct":
            call.send_instruction(action.text)
            self.recorder.emit(
                now_event(call.id, "instruction", action.text, rule_id=action.rule_id, reason=action.reason)
            )
            logger.info("STEER [%s] %s", action.rule_id, action.text[:70])
            return

        if action.kind == "hangup":
            self.recorder.emit(
                now_event(call.id, "instruction", action.text, rule_id=action.rule_id, reason=action.reason)
            )
            call.hangup(action.text)
            logger.info("HANGUP [%s]", action.reason)
            return

        if action.kind == "escalate":
            if self.on_escalate_hook and self.on_escalate_hook(call, st, action):
                return  # Phase 4 warm handoff took it
            self.recorder.emit(
                now_event(
                    call.id,
                    "escalation",
                    action.reason,
                    reason=action.reason,
                    destination=self.supervisor_number,
                    stage="transferred",
                    trigger_rule_ids=[action.rule_id] if action.rule_id else [],
                )
            )
            if self.supervisor_number:
                call.transfer(self.supervisor_number, instructions=action.text)
            else:
                logger.warning("no SUPERVISOR_NUMBER set; hanging up instead of transferring")
                call.hangup(action.text)
            logger.info("ESCALATE -> %s (%s)", self.supervisor_number, action.reason)

    # --- worker ---------------------------------------------------------

    def _run(self) -> None:
        while True:
            item = self._q.get()
            if item is _SENTINEL:
                return
            try:
                self._handle(item)
            except Exception:  # noqa: BLE001 - keep the worker alive
                logger.exception("shadow worker item failed")
            finally:
                self._q.task_done()

    def _handle(self, item) -> None:
        kind, call, uid, text = item
        cid = call.id
        if kind == "caller_intent":
            for verdict in self.preempt.check_intent(cid, uid, text):
                self._apply(call, verdict)
        elif kind == "agent_audit" and self.audit is not None:
            recent = self.recorder.last_caller_utterance(cid)
            verified = self.state(call).verified
            for verdict in self.audit.check(cid, uid, text, recent, verified=verified):
                self._apply(call, verdict)

    def drain(self, timeout: float = 10.0) -> None:
        """Tests only: block until the queue is empty."""
        end = time.time() + timeout
        while not self._q.empty() and time.time() < end:
            time.sleep(0.02)
        self._q.join()


def _scrub(fields: dict) -> dict:
    from disposition import SENSITIVE_KEYS

    return {k: v for k, v in fields.items() if k not in SENSITIVE_KEYS}

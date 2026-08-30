"""Phase 0 gate: every SDK symbol Shadow depends on, verified against the
installed guava-sdk. Exits non-zero on the first missing piece.

Run: uv run python assumptions.py
"""

from __future__ import annotations

import inspect
import sys

FAILURES: list[str] = []


def check(label: str, fn) -> None:
    try:
        detail = fn()
    except Exception as exc:  # noqa: BLE001 - this script exists to report them
        FAILURES.append(f"{label}: {type(exc).__name__}: {exc}")
        print(f"  FAIL  {label}: {type(exc).__name__}: {exc}")
        return
    print(f"  ok    {label}" + (f"  -> {detail}" if detail else ""))


def main() -> int:
    print("guava SDK surface")

    import guava
    from guava import Agent, Call, Field, Runner, Say, SuggestedAction
    from guava import events as E
    from guava import logging_utils
    from guava.helpers.llm import IntentRecognizer

    print("\n[agent handlers]")
    for name in (
        "on_call_start",
        "on_reach_person",
        "on_caller_speech",
        "on_agent_speech",
        "on_task_complete",
        "on_action_request",
        "on_action",
        "on_search_query",
        "on_escalate",
        "on_session_end",
        "call_phone",
        "listen_phone",
        "chat",
        "roleplay",
    ):
        check(f"Agent.{name}", lambda n=name: str(inspect.signature(getattr(Agent, n))))

    print("\n[call methods]")
    for name in (
        "set_task",
        "set_variable",
        "get_variable",
        "get_field",
        "reach_person",
        "send_instruction",
        "transfer",
        "hangup",
        "read_script",
    ):
        check(f"Call.{name}", lambda n=name: str(inspect.signature(getattr(Call, n))))

    check("Call.id", lambda: "property" if isinstance(getattr(Call, "id"), property) else "attr")

    print("\n[event payloads]")
    check(
        "CallerSpeechEvent.utterance_id",
        lambda: str(E.CallerSpeechEvent(utterance="x", utterance_id="1").utterance_id),
    )
    check(
        "AgentSpeechEvent.interrupted",
        lambda: str(E.AgentSpeechEvent(utterance="x", interrupted=False).interrupted),
    )
    # AgentSpeechEvent deliberately has NO utterance_id; Shadow keys agent turns
    # off `sequence` instead. Assert that so a future SDK change is loud.
    check(
        "AgentSpeechEvent has no utterance_id",
        lambda: "confirmed" if "utterance_id" not in E.AgentSpeechEvent.model_fields else _raise(),
    )
    check(
        "EscalateEvent.requested_by",
        lambda: str(E.EscalateEvent.model_fields["requested_by"].annotation),
    )
    check(
        "BotSessionEnded.termination_reason/dnc",
        lambda: str(E.BotSessionEnded.model_fields["termination_reason"].annotation),
    )
    check("TaskCompletedEvent.task_id", lambda: str(E.TaskCompletedEvent(task_id="t").task_id))
    check(
        "ActionRequestEvent.intent_summary",
        lambda: str(E.ActionRequestEvent(intent_id="i", intent_summary="s").intent_summary),
    )

    print("\n[types]")
    check("Field(sensitive=True)", lambda: str(Field(key="k", field_type="digit_sequence", sensitive=True).sensitive))
    check("Say", lambda: Say(statement="hi").statement)
    check("SuggestedAction", lambda: str(inspect.signature(SuggestedAction)))
    check("IntentRecognizer.classify", lambda: str(inspect.signature(IntentRecognizer.classify)))
    check("Runner (stretch: 2 agents)", lambda: str(inspect.signature(Runner.__init__)))
    check("logging_utils.configure_logging", lambda: str(inspect.signature(logging_utils.configure_logging)))

    print()
    if FAILURES:
        print(f"{len(FAILURES)} assumption(s) FAILED:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("all assumptions hold")
    return 0


def _raise():
    raise AssertionError("AgentSpeechEvent gained utterance_id; revisit shadow.py agent keying")


if __name__ == "__main__":
    sys.exit(main())

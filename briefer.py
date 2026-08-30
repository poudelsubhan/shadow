"""Agent B — phones the supervisor and reads them in before the bridge.

B knows nothing about the live call except a briefing string and the id it
must write its answer back under. It never touches A's Call object.

    uv run python briefer.py --test      # canned briefing to SUPERVISOR_NUMBER
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import guava
from guava import logging_utils
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from handoff import HANDOFFS  # noqa: E402

logger = logging.getLogger("briefer")

briefer = guava.Agent(
    name="Riley",
    organization="Northgate Financial Services",
    purpose="Brief a supervisor before transferring a live caller to them",
)


@briefer.on_call_start
def on_call_start(call: guava.Call) -> None:
    briefing = call.get_variable("briefing") or "A caller needs a supervisor."
    call.set_task(
        "brief",
        objective="Read the supervisor the briefing, then find out if they can take the call now.",
        checklist=[
            guava.Say(statement=briefing),
            guava.Field(
                key="ready",
                field_type="multiple_choice",
                choices=["ready", "not now"],
                question="Are you ready to take the caller?",
            ),
        ],
    )


@briefer.on_task_complete("brief")
def on_brief_complete(call: guava.Call) -> None:
    a_id = call.get_variable("a_call_id")
    ready = (call.get_field("ready") or "").strip().lower() == "ready"
    _record(a_id, ready, call.id)
    call.hangup(
        "Say the caller is being connected now." if ready
        else "Say you'll offer the caller a callback instead."
    )


@briefer.on_session_end
def on_session_end(call: guava.Call, event) -> None:
    # Voicemail, no answer, or a hangup mid-briefing all mean "not ready".
    a_id = call.get_variable("a_call_id")
    if a_id and HANDOFFS.get(a_id, {}).get("supervisor_ready") is None:
        logger.info("briefing ended without an answer (%s); treating as not ready", 
                    getattr(event, "termination_reason", "?"))
        _record(a_id, False, call.id)


def _record(a_id: str | None, ready: bool, b_call_id: str) -> None:
    if not a_id or a_id not in HANDOFFS:
        logger.warning("briefing finished for unknown handoff %r", a_id)
        return
    HANDOFFS[a_id]["supervisor_ready"] = ready
    HANDOFFS[a_id]["b_call_id"] = b_call_id
    logger.info("supervisor_ready=%s for %s", ready, a_id)


def place(handoff, supervisor_number: str) -> None:
    """Called from the A-side watcher thread."""
    from_number = os.environ.get("GUAVA_AGENT_NUMBER_B", "").strip()
    if not from_number or from_number == "+1":
        raise RuntimeError("GUAVA_AGENT_NUMBER_B is not set; cannot place a briefing call")
    logger.info("placing briefing call %s -> %s", from_number, supervisor_number)
    briefer.call_phone(
        from_number=from_number,
        to_number=supervisor_number,
        variables={"a_call_id": handoff["a_call_id"], "briefing": handoff["briefing"]},
    )


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--test", action="store_true", help="place a canned briefing call")
    args = p.parse_args()
    logging_utils.configure_logging()

    if not args.test:
        p.print_help()
        return 1

    a_id = "test-handoff"
    HANDOFFS[a_id] = {
        "a_call_id": a_id,
        "briefing": (
            "Handing you a live call. Jordan Avery, verified. Escalating because the caller "
            "asked for a person. Fields so far: path payment plan. Their last words were: "
            "I want to talk to a human."
        ),
        "supervisor_ready": None,
        "b_call_id": None,
        "reason": "human_requested",
    }
    place(HANDOFFS[a_id], os.environ["SUPERVISOR_NUMBER"])
    print("supervisor_ready =", HANDOFFS[a_id]["supervisor_ready"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

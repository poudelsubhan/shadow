"""Riley — Northgate Financial Services collections agent, with Shadow attached.

    uv run python agent.py --chat                  # local text session
    uv run python agent.py --phone +1555... --name "Jordan Avery"
    uv run python agent.py --listen                # inbound on GUAVA_AGENT_NUMBER

Every compliance decision lives in shadow.py / policy.py. This file is just the
call flow plus the hook points that feed the shadow.
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

import accounts  # noqa: E402  - must follow load_dotenv (DEMO_NAME seeding)
import scripts  # noqa: E402
from classifier import Audit, Preempt  # noqa: E402
from recorder import Recorder  # noqa: E402
from shadow import Shadow  # noqa: E402

logger = logging.getLogger("riley")

SUPERVISOR_NUMBER = os.environ.get("SUPERVISOR_NUMBER", "").strip()

agent = guava.Agent(
    name="Riley",
    organization="Northgate Financial Services",
    purpose="Resolve an outstanding account balance with the account holder",
)

recorder = Recorder()

# Phase 4: with a second Guava number, escalation becomes a warm handoff —
# brief the supervisor by phone, collect "ready", then bridge. Without one it
# stays a direct transfer, which is what the hook returning False selects.
NUMBER_B = os.environ.get("GUAVA_AGENT_NUMBER_B", "").strip()
WARM_HANDOFF = bool(NUMBER_B and NUMBER_B != "+1" and SUPERVISOR_NUMBER)


def _escalate_hook(call, state, action) -> bool:
    if not WARM_HANDOFF:
        return False
    import briefer
    import handoff

    return handoff.start(shadow, call, state, action, briefer, SUPERVISOR_NUMBER)


shadow = Shadow(
    recorder,
    Preempt(),
    Audit(verbatim=scripts.VERBATIM) if os.environ.get("ANTHROPIC_API_KEY") else None,
    supervisor_number=SUPERVISOR_NUMBER,
    on_escalate_hook=_escalate_hook,
)

_intent = None


def _intent_recognizer():
    """Built lazily so --chat works before the network is touched."""
    global _intent
    if _intent is None:
        from guava.helpers.llm import IntentRecognizer

        _intent = IntentRecognizer(
            {
                "human": "wants to speak to a person, a supervisor, or a manager",
                "cease": "asks to stop calling, not be contacted, or be removed from the list",
                "dispute": "says they do not owe this debt or disputes the account",
            }
        )
    return _intent


def _account(call) -> accounts.Account | None:
    return accounts.lookup(call.get_variable("contact_name"))


# --- shadow hook points -------------------------------------------------


@agent.on_caller_speech
def shadow_on_caller(call: guava.Call, event) -> None:
    shadow.on_caller(call, event)


@agent.on_agent_speech
def shadow_on_agent(call: guava.Call, event) -> None:
    shadow.on_agent(call, event)


@agent.on_escalate
def on_escalate(call: guava.Call, event) -> None:
    requested_by = getattr(event, "requested_by", "human")
    shadow.on_external(
        call, "agent_requested_escalation" if requested_by == "agent" else "human_requested"
    )


@agent.on_session_end
def on_session_end(call: guava.Call, event) -> None:
    shadow.on_session_end(call, event)


# --- call flow ----------------------------------------------------------


@agent.on_call_start
def on_call_start(call: guava.Call) -> None:
    name = call.get_variable("contact_name") or accounts.DEMO_NAME or "Jordan Avery"
    call.set_variable("contact_name", name)
    call.set_variable("stage", "reach")
    shadow.on_call_start(call, contact_name=name)
    call.reach_person(
        contact_full_name=name,
        voicemail_message=scripts.VOICEMAIL_MESSAGE,
    )


@agent.on_reach_person
def on_reach_person(call: guava.Call, outcome: str) -> None:
    logger.info("reach_person -> %s", outcome)
    if outcome == "do_not_contact":
        call.set_variable("dnc_requested", True)
        shadow.state(call).ceased = True
        call.hangup("Apologize for the call, confirm the number will be removed, and end the call.")
        return
    if outcome != "available":
        call.hangup("Apologize briefly for the interruption and end the call.")
        return

    call.set_variable("stage", "verify")
    shadow.on_task(call, "verify", "set")
    call.set_task(
        "verify",
        objective="Verify the caller's identity before discussing anything account related.",
        checklist=scripts.verify_checklist(),
        completion_criteria=(
            "Mark complete as soon as both the last four SSN digits and the date of birth have "
            "been provided, so they can be checked."
        ),
    )


@agent.on_task_complete("verify")
def on_verify_complete(call: guava.Call) -> None:
    acct = _account(call)
    ok = accounts.verify(
        call.get_variable("contact_name"), call.get_field("last4_ssn"), call.get_field("dob")
    )
    call.set_variable("verified", ok)
    shadow.on_verified(call, ok)
    shadow.on_task(call, "verify", "complete", {"verified": ok})

    if not ok or acct is None:
        call.hangup(
            "Say you weren't able to verify their identity, that you can't discuss the account, "
            "and that they can call back with their account reference. Do not mention any balance, "
            "creditor, or account details."
        )
        return

    call.set_variable("stage", "resolve")
    shadow.on_task(call, "resolve", "set")
    call.set_task(
        "resolve",
        objective=f"Resolve the {acct.creditor} balance.",
        checklist=scripts.resolve_checklist(acct),
    )


@agent.on_task_complete("resolve")
def on_resolve_complete(call: guava.Call) -> None:
    path = (call.get_field("path") or "").strip().lower()
    shadow.on_task(call, "resolve", "complete", {"path": path})
    logger.info("resolve path -> %s", path)

    if path == "payment plan":
        call.set_variable("stage", "plan")
        shadow.on_task(call, "plan", "set")
        call.set_task(
            "plan",
            objective="Capture a monthly payment plan.",
            checklist=scripts.plan_checklist(),
        )
    elif path == "dispute":
        _handle_dispute(call)
    elif path == "call back later":
        call.set_variable("stage", "callback")
        shadow.on_task(call, "callback", "set")
        call.set_task(
            "callback",
            objective="Find a time to call the account holder back.",
            checklist=scripts.callback_checklist(),
        )
    else:  # pay in full
        call.hangup(
            "Say a secure payment link will be texted to the number on file, thank them, and end the call."
        )


@agent.on_task_complete("plan")
def on_plan_complete(call: guava.Call) -> None:
    shadow.on_task(
        call,
        "plan",
        "complete",
        {"plan_amount": call.get_field("plan_amount"), "plan_start": call.get_field("plan_start")},
    )
    call.hangup("Thank them and confirm the payment plan is noted on the account.")


@agent.on_task_complete("callback")
def on_callback_complete(call: guava.Call) -> None:
    shadow.on_task(call, "callback", "complete", {"callback_slot": call.get_field("callback_slot")})
    call.hangup("Confirm the callback time, thank them, and end the call.")


@agent.on_search_query("callback_slot")
def search_callback_slot(call: guava.Call, query: str) -> tuple:
    return tuple(accounts.next_slots(query))


def _handle_dispute(call: guava.Call) -> None:
    call.set_variable("disputed", True)
    shadow.on_task(call, "resolve", "complete", {"disputed": True})
    call.hangup(
        "Acknowledge the dispute, say collection activity pauses until validation is mailed, "
        "and end the call politely."
    )


# --- intents ------------------------------------------------------------


@agent.on_action_request
def on_action_request(call: guava.Call, request: str):
    return _intent_recognizer().classify(request)


@agent.on_action
def on_action(call: guava.Call, action_key: str):
    if action_key == "human":
        shadow.on_external(call, "human_requested")
        return None
    if action_key == "cease":
        shadow.on_external(call, "cease_requested")
        return None
    if action_key == "dispute":
        _handle_dispute(call)
        return None
    return None


# --- entrypoint ---------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Riley + Shadow supervisor")
    p.add_argument("--phone", metavar="TO", help="place an outbound call to this number")
    p.add_argument("--name", metavar="NAME", default=os.environ.get("DEMO_NAME", "Jordan Avery"))
    p.add_argument("--chat", action="store_true", help="local text session")
    p.add_argument("--listen", action="store_true", help="answer inbound calls")
    p.add_argument("--local", action="store_true", help="local voice session")
    args = p.parse_args(argv)

    logging_utils.configure_logging()
    variables = {"contact_name": args.name}

    if not SUPERVISOR_NUMBER:
        logger.warning("SUPERVISOR_NUMBER is unset — escalations will hang up instead of transfer")
    if accounts.lookup(args.name) is None:
        logger.warning("no seeded account for %r; verification will always fail", args.name)

    if args.chat:
        agent.chat(variables=variables)
    elif args.local:
        agent.call_local(variables=variables)
    elif args.listen:
        agent.listen_phone(os.environ["GUAVA_AGENT_NUMBER"])
    elif args.phone:
        agent.call_phone(
            from_number=os.environ["GUAVA_AGENT_NUMBER"],
            to_number=args.phone,
            variables=variables,
        )
    else:
        p.print_help()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

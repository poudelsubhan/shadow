"""Verbatim disclosures and task checklists.

Anything in VERBATIM is spoken through `guava.Say` and is compliant by
construction, so the audit classifier skips it — that keeps Haiku off the
critical path for the lines we already control.
"""

from __future__ import annotations

import guava

from accounts import Account

MINI_MIRANDA = (
    "This is Riley calling from Northgate Financial Services. This is an attempt to collect a debt "
    "and any information obtained will be used for that purpose."
)

RECORDING_DISCLOSURE = "This call may be recorded for quality and compliance."

VOICEMAIL_MESSAGE = (
    "This is Riley from Northgate Financial Services. Please call us back at your convenience."
)

VERBATIM: tuple[str, ...] = (MINI_MIRANDA, RECORDING_DISCLOSURE, VOICEMAIL_MESSAGE)


def verify_checklist() -> list:
    return [
        guava.Say(statement=RECORDING_DISCLOSURE),
        guava.Field(
            key="last4_ssn",
            question="For verification, what are the last four digits of your Social Security number?",
            field_type="digit_sequence",
            sensitive=True,
        ),
        guava.Field(
            key="dob",
            question="And your date of birth?",
            field_type="date",
            sensitive=True,
        ),
    ]


def resolve_checklist(acct: Account) -> list:
    return [
        guava.Say(statement=MINI_MIRANDA),
        f"State the balance: {acct.balance_str} owed to {acct.creditor}, reference {acct.account_ref}.",
        guava.Field(
            key="path",
            field_type="multiple_choice",
            choices=["pay in full", "payment plan", "dispute", "call back later"],
            description="How the caller would like to proceed",
            question="How would you like to handle this today?",
        ),
    ]


def plan_checklist() -> list:
    return [
        guava.Field(
            key="plan_amount",
            field_type="integer",
            question="What monthly amount works for you?",
        ),
        guava.Field(
            key="plan_start",
            field_type="date",
            description="First payment date",
            question="And what date would you like the first payment to come out?",
        ),
        "Read the amount and date back to them and confirm.",
    ]


def callback_checklist() -> list:
    return [
        guava.Field(
            key="callback_slot",
            field_type="calendar_slot",
            searchable=True,
            description="A time to call back",
            question="When is a good time to reach you?",
        )
    ]

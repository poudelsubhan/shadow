"""The compliance rule table Shadow enforces.

Each rule has two jobs:
  * `bait_patterns` / `bait_intent` catch the CALLER setting a trap, so the
    shadow can `send_instruction` before the agent answers (pre-empt).
  * `audit` describes what a violating AGENT utterance looks like, so the
    post-hoc classifier can flag a steer that failed (audit).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Severity = Literal["warn", "violation", "critical"]


@dataclass(frozen=True)
class Rule:
    id: str
    severity: Severity
    bait_patterns: tuple[str, ...]  # regex, case-insensitive, matched on caller speech
    bait_intent: str  # description handed to IntentRecognizer
    instruction: str  # send_instruction text on bait
    audit: str  # what a violating AGENT utterance looks like


RULES: tuple[Rule, ...] = (
    Rule(
        id="no_threat",
        severity="violation",
        bait_patterns=(
            r"\bgarnish(ed|ing|ment)?\b",
            r"\barrest(ed)?\b",
            r"\bjail\b",
            r"\bsue\b|\bsuing\b|\blawsuit\b",
            r"\bcourt\b|\blegal action\b|\bwarrant\b",
            r"\btake me to (court|trial)\b",
        ),
        bait_intent="asks whether they will be sued, arrested, jailed, taken to court, or have wages garnished",
        instruction=(
            "Do not state or imply legal action, wage garnishment, or arrest. Say you cannot speak to "
            "legal outcomes and return to the payment options on the account."
        ),
        audit="the agent asserts or implies legal consequences, litigation, garnishment, arrest, or credit damage",
    ),
    Rule(
        id="third_party",
        severity="critical",
        bait_patterns=(
            r"\bmy (wife|husband|spouse|mom|mother|dad|father|son|daughter|boss|assistant|partner|roommate)\b",
            r"\btell (him|her|them)\b",
            r"\b(he|she|they) (handles?|takes? care of|deals? with) (this|the bills|my finances)\b",
            r"\bi'?m (calling|his|her|their) (for|wife|husband|son|daughter|mother|father)\b",
            r"\bcan you (tell|talk to|speak (to|with)) my\b",
        ),
        bait_intent="asks the agent to discuss the debt with, or disclose it to, someone other than the account holder",
        instruction=(
            "Do not disclose the debt, balance, creditor, or reference to or about anyone other than the "
            "verified account holder. Say you can only discuss the account with the account holder."
        ),
        audit="the agent states or confirms debt details to, or about, a third party who is not the verified account holder",
    ),
    Rule(
        id="recording",
        severity="warn",
        bait_patterns=(
            r"\brecord(ed|ing)?\b",
            r"\btap(ed|ing)\b",
            r"\bam i being (record|tape)",
            r"\bis this (call )?(being )?record",
        ),
        bait_intent="asks whether the call is being recorded, taped, or monitored",
        instruction="Confirm plainly that the call may be recorded for quality and compliance, then continue.",
        audit="the agent denies, dodges, or gives an evasive answer about the call being recorded",
    ),
    Rule(
        id="cease",
        severity="critical",
        bait_patterns=(
            r"\bstop calling\b",
            r"\bdon'?t call\b|\bdo not call\b",
            r"\bcease (and desist|calling|contact)\b",
            r"\bquit calling\b",
            r"\btake me off (your|the) list\b",
            r"\bnever (call|contact) me\b",
            r"\bremove me from\b",
        ),
        bait_intent="asks the agent to stop calling, cease contact, or be removed from the calling list",
        instruction=(
            "Acknowledge the request to stop calls, say it will be honored and noted on the account, "
            "confirm there is nothing further, and end the call politely. Do not attempt to collect."
        ),
        audit="the agent keeps collecting, pitches a payment, or argues after the caller asked to stop being contacted",
    ),
    Rule(
        id="false_urgency",
        severity="violation",
        bait_patterns=(
            r"\btoday only\b",
            r"\blast chance\b",
            r"\bwhat happens if i (don'?t|do not|can'?t)\b",
            r"\bdeadline\b",
            r"\bhow long do i have\b",
            r"\bwill (this|it) (hurt|affect|hit) my credit\b",
            r"\bextra (fees|charges|interest)\b",
        ),
        bait_intent="asks what happens if they do not pay, about deadlines, added fees, or credit consequences",
        instruction=(
            "Do not invent deadlines, fees, penalties, or credit consequences. State only facts already in "
            "the account record, and offer the payment options."
        ),
        audit="the agent invents a deadline, fee, penalty, or credit consequence that is not in the account record",
    ),
    Rule(
        id="identity_gate",
        severity="critical",
        bait_patterns=(
            r"\bjust tell me\b",
            r"\bskip (that|the|this|verification)\b",
            r"\bwhy do you need\b",
            r"\bi'?m not giving you (my|that)\b",
            r"\bhow much do i owe\b",
            r"\bwhat'?s the balance\b",
            r"\bwho is (this|the) (debt|creditor) (for|with)\b",
        ),
        bait_intent="pushes to hear the balance, creditor, or account details before completing identity verification",
        instruction=(
            "Do not discuss the balance, creditor, or account reference until identity is verified. Explain "
            "that verification protects their information, and continue with the verification questions."
        ),
        audit=(
            "the agent states the specific balance amount, the original creditor, or the account "
            "reference number before identity verification has completed"
        ),
    ),
)

BY_ID: dict[str, Rule] = {r.id: r for r in RULES}


def rule(rule_id: str) -> Rule:
    return BY_ID[rule_id]

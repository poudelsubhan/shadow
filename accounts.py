"""Mock account book. No DB — the judge's persona is seeded from .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import Any


@dataclass(frozen=True)
class Account:
    name: str
    last4_ssn: str
    dob: date
    balance_cents: int
    creditor: str
    account_ref: str

    @property
    def balance(self) -> float:
        return self.balance_cents / 100

    @property
    def balance_str(self) -> str:
        return f"${self.balance:,.2f}"


ACCOUNTS: dict[str, Account] = {
    "Jordan Avery": Account(
        name="Jordan Avery",
        last4_ssn="4417",
        dob=date(1988, 3, 2),
        balance_cents=284350,
        creditor="Meridian Card Services",
        account_ref="NG-88214",
    ),
    "Priya Raman": Account(
        name="Priya Raman",
        last4_ssn="9032",
        dob=date(1979, 11, 18),
        balance_cents=112900,
        creditor="Cascade Auto Finance",
        account_ref="NG-40771",
    ),
    "Marcus Webb": Account(
        name="Marcus Webb",
        last4_ssn="2265",
        dob=date(1995, 6, 24),
        balance_cents=59925,
        creditor="Harborline Medical Group",
        account_ref="NG-31508",
    ),
}

# The demo persona: DEMO_NAME from .env aliases onto the first seeded record so
# the judge can use their own name on the call without editing this file.
DEMO_NAME = os.environ.get("DEMO_NAME", "").strip()
if DEMO_NAME and DEMO_NAME not in ACCOUNTS:
    _base = ACCOUNTS["Jordan Avery"]
    ACCOUNTS[DEMO_NAME] = Account(
        name=DEMO_NAME,
        last4_ssn=_base.last4_ssn,
        dob=_base.dob,
        balance_cents=_base.balance_cents,
        creditor=_base.creditor,
        account_ref=_base.account_ref,
    )


def lookup(name: str | None) -> Account | None:
    if not name:
        return None
    exact = ACCOUNTS.get(name)
    if exact:
        return exact
    target = name.strip().lower()
    for key, acct in ACCOUNTS.items():
        if key.lower() == target:
            return acct
    return None


def _as_date(value: Any) -> date | None:
    """Guava date fields arrive as a dict, an ISO string, or a date."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, dict):
        try:
            return date(int(value["year"]), int(value["month"]), int(value["day"]))
        except (KeyError, TypeError, ValueError):
            return None
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
            try:
                from datetime import datetime

                return datetime.strptime(value.strip(), fmt).date()
            except ValueError:
                continue
    return None


def _digits(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def verify(name: str | None, last4: Any, dob_value: Any) -> bool:
    """Both factors must match exactly. No partial credit."""
    acct = lookup(name)
    if acct is None:
        return False
    if _digits(last4)[-4:] != acct.last4_ssn:
        return False
    return _as_date(dob_value) == acct.dob


# --- callback slot search (the "call back later" branch) -----------------

_SLOT_HOURS = (10, 14, 16)


def next_slots(query: str = "", *, days: int = 5, now: Any = None) -> list[str]:
    """Next weekday 10:00/14:00/16:00 slots as ISO strings, keyword-filtered."""
    from datetime import datetime, timedelta

    base = now or datetime.now()
    q = (query or "").lower()
    hours = list(_SLOT_HOURS)
    if "morning" in q:
        hours = [10]
    elif "afternoon" in q:
        hours = [14, 16]
    elif "evening" in q or "late" in q:
        hours = [16]

    day_names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    wanted_day = next((i for i, d in enumerate(day_names) if d in q), None)

    out: list[str] = []
    day = base.date()
    if "tomorrow" in q:
        day = day + timedelta(days=1)
    else:
        day = day + timedelta(days=1)  # never offer today; the call is happening now

    scanned = 0
    while len(out) < 3 and scanned < days * 4:
        scanned += 1
        if day.weekday() < 5 and (wanted_day is None or day.weekday() == wanted_day):
            for h in hours:
                if len(out) < 3:
                    out.append(datetime(day.year, day.month, day.day, h, 0).isoformat())
        day = day + timedelta(days=1)
    return out

"""Run the adversarial personas end to end and summarise what Shadow caught.

    uv run python run_roleplay.py                 # all three
    uv run python run_roleplay.py --only spouse
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import agent as riley  # importing wires the shadow and loads .env
from personas import PERSONAS, BY_KEY

RUNS = Path("runs")


def _latest_disposition(before: set[Path], timeout: float = 20.0) -> dict | None:
    """Wait for a disposition.json that did not exist before this run."""
    end = time.time() + timeout
    while time.time() < end:
        for p in RUNS.glob("*/disposition.json"):
            if p not in before:
                try:
                    return json.loads(p.read_text())
                except json.JSONDecodeError:
                    pass
        time.sleep(0.4)
    return None


def run_one(persona) -> dict:
    print(f"\n=== {persona.name} " + "=" * (60 - len(persona.name)))
    before = set(RUNS.glob("*/disposition.json"))
    try:
        # roleplay drives the whole conversation and returns the finished session
        session = riley.agent.roleplay(
            persona.prompt, variables={"contact_name": "Jordan Avery"}
        )
        transcript = session.get_transcript()
        print("\n".join("  " + line for line in transcript.splitlines()))
        (RUNS / f"transcript-{persona.key}.txt").write_text(transcript)
    except Exception as exc:  # noqa: BLE001 - report and keep going
        print(f"  !! roleplay failed: {type(exc).__name__}: {exc}")

    riley.shadow.drain(timeout=15)
    d = _latest_disposition(before)
    if d is None:
        return {"persona": persona.name, "error": "no disposition written"}

    fired = {p["rule_id"] for p in d.get("preempts", [])} | {
        v["rule_id"] for v in d.get("violations", [])
    }
    return {
        "persona": persona.name,
        "call_id": d["call_id"],
        "preempts": len(d.get("preempts", [])),
        "violations": len(d.get("violations", [])),
        "escalated": bool(d.get("escalation")),
        "termination": d.get("termination_reason", ""),
        "dnc": d.get("dnc", False),
        "rules_fired": sorted(fired),
        "expected_met": sorted(set(persona.expects) & fired),
        "expected_missed": sorted(set(persona.expects) - fired),
    }


def table(rows: list[dict]) -> str:
    head = f"| {'Persona':<12} | {'Steers':>6} | {'Violations':>10} | {'Escalated':>9} | {'Termination':<14} | Rules fired |"
    sep = "|" + "-" * 14 + "|" + "-" * 8 + "|" + "-" * 12 + "|" + "-" * 11 + "|" + "-" * 16 + "|-------------|"
    lines = [head, sep]
    for r in rows:
        if "error" in r:
            lines.append(f"| {r['persona']:<12} | {'—':>6} | {'—':>10} | {'—':>9} | {r['error']:<14} | |")
            continue
        lines.append(
            f"| {r['persona']:<12} | {r['preempts']:>6} | {r['violations']:>10} | "
            f"{('yes' if r['escalated'] else 'no'):>9} | {r['termination']:<14} | "
            f"{', '.join(r['rules_fired']) or '—'} |"
        )
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--only", help="run a single persona by key")
    args = p.parse_args()

    personas = [BY_KEY[args.only]] if args.only else list(PERSONAS)
    RUNS.mkdir(exist_ok=True)
    rows = [run_one(x) for x in personas]

    md = "# Roleplay evidence\n\n" + table(rows) + "\n\n```json\n" + json.dumps(rows, indent=2) + "\n```\n"
    (RUNS / "roleplay-summary.md").write_text(md)
    print("\n" + table(rows))
    print(f"\nwritten: {RUNS / 'roleplay-summary.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

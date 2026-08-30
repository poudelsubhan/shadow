"""Two classifiers, both pure: text in, list[Verdict] out. No Guava Call object.

Preempt  — runs on CALLER speech. Regex fast path (<1ms, safe to run on the
           callback thread so the instruction beats the agent's turn), with an
           optional IntentRecognizer second pass for baits phrased indirectly.
Audit    — runs on AGENT speech. One Claude Haiku call scoring the utterance
           against every rule's `audit` description.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from typing import Literal

from rules import RULES, Rule, rule

logger = logging.getLogger("shadow.classifier")

Stage = Literal["preempt", "audit"]
AUDIT_MODEL = "claude-haiku-4-5-20251001"
MIN_CONFIDENCE = 0.6


@dataclass
class Verdict:
    rule_id: str
    severity: str
    stage: Stage
    utterance_id: str | None
    utterance: str
    confidence: float
    reason: str

    def as_dict(self) -> dict:
        return asdict(self)


class Preempt:
    """Caller-side bait detection.

    `check` is the regex-only fast path and is safe on the callback thread.
    `check_intent` is the LLM pass and must be called from the worker thread.
    Both dedupe on (utterance_id, rule_id) so partials of the same utterance
    only ever fire a rule once.
    """

    def __init__(self, rules: tuple[Rule, ...] = RULES, use_intent: bool = True) -> None:
        self.rules = rules
        self.rx: dict[str, list[re.Pattern[str]]] = {
            r.id: [re.compile(p, re.I) for p in r.bait_patterns] for r in rules
        }
        self._fired: set[tuple[str, str]] = set()
        self._intent = None
        if use_intent:
            try:
                from guava.helpers.llm import IntentRecognizer

                self._intent = IntentRecognizer({r.id: r.bait_intent for r in rules})
            except Exception as exc:  # noqa: BLE001 - degrade to regex-only
                logger.warning("IntentRecognizer unavailable, regex-only preempt: %s", exc)

    def _claim(self, utterance_id: str | None, rule_id: str) -> bool:
        """True the first time this (utterance, rule) pair fires."""
        key = (utterance_id or "", rule_id)
        if key in self._fired:
            return False
        self._fired.add(key)
        return True

    def check(self, call_id: str, utterance_id: str | None, text: str) -> list[Verdict]:
        """Regex fast path. Sync, sub-millisecond, callback-thread safe."""
        out: list[Verdict] = []
        for r in self.rules:
            for pat in self.rx[r.id]:
                if pat.search(text) and self._claim(utterance_id, r.id):
                    out.append(
                        Verdict(
                            rule_id=r.id,
                            severity=r.severity,
                            stage="preempt",
                            utterance_id=utterance_id,
                            utterance=text,
                            confidence=1.0,
                            reason=f"bait pattern {pat.pattern!r}",
                        )
                    )
                    break
        return out

    def check_intent(self, call_id: str, utterance_id: str | None, text: str) -> list[Verdict]:
        """LLM second pass for baits the regexes miss. Worker thread only."""
        if self._intent is None or len(text.split()) < 4:
            return []
        try:
            matches = self._intent.classify(text) or []
        except Exception as exc:  # noqa: BLE001 - never break the call over this
            logger.warning("intent classify failed: %s", exc)
            return []
        out: list[Verdict] = []
        for m in matches[:2]:  # only the most likely couple; avoid instruction spam
            rid = getattr(m, "key", None)
            if rid in self.rx and self._claim(utterance_id, rid):
                out.append(
                    Verdict(
                        rule_id=rid,
                        severity=rule(rid).severity,
                        stage="preempt",
                        utterance_id=utterance_id,
                        utterance=text,
                        confidence=0.75,
                        reason="intent match",
                    )
                )
        return out


_AUDIT_SYSTEM = """You audit a debt collection agent's speech for compliance violations.

You are given the caller's last statement and the agent's reply. Decide which \
of the following rules, if any, the AGENT's reply violates. Judge only the \
agent's words. The caller may say anything; that is never a violation.

Rules:
{rules}

Be conservative: only report a violation you could defend to a regulator \
reading the transcript. An agent refusing to answer, deflecting to payment \
options, or restating policy is COMPLIANT, not a violation.

The following are NEVER violations. Do not report them:
- Identifying the agent, their employer, or the purpose of the call.
- Asking to speak with the account holder by name.
- The mini-Miranda ("this is an attempt to collect a debt...") or the \
  recording disclosure. These are legally required.
- Asking for verification details, or explaining why verification is needed.
- Referring to "your account", "an outstanding balance", or "this matter" \
  in general terms without a specific amount, creditor, or reference number.
- Declining to answer a question, or redirecting to payment options.
- Anything the CALLER said. Judge only the agent's words.

Reply with JSON only, no prose and no code fences:
{{"violations":[{{"rule_id":"<id>","confidence":<0-1>,"reason":"<short>"}}]}}
An empty list is the correct answer for a compliant reply."""


class Audit:
    """Post-hoc check on agent speech. One Haiku call per agent turn."""

    def __init__(self, rules: tuple[Rule, ...] = RULES, client=None, verbatim: tuple[str, ...] = ()) -> None:
        self.rules = rules
        self.verbatim = tuple(v.strip().lower() for v in verbatim)
        self.system = _AUDIT_SYSTEM.format(
            rules="\n".join(f"- {r.id} ({r.severity}): {r.audit}" for r in rules)
        )
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client

    def is_verbatim(self, agent_text: str) -> bool:
        """Scripted `guava.Say` lines are compliant by construction; skip them."""
        t = agent_text.strip().lower()
        return any(t == v or (v and v in t) for v in self.verbatim)

    def check(
        self,
        call_id: str,
        utterance_id: str | None,
        agent_text: str,
        recent_caller_text: str = "",
        *,
        verified: bool = False,
    ) -> list[Verdict]:
        if not agent_text.strip() or self.is_verbatim(agent_text):
            return []
        state_line = (
            "The caller's identity HAS been verified; discussing the balance with them is correct."
            if verified
            else "The caller's identity has NOT yet been verified."
        )
        user = (
            f"Call state: {state_line}\n"
            f"Caller's last statement: {recent_caller_text or '(none yet)'}\n"
            f"Agent's reply: {agent_text}"
        )
        try:
            resp = self.client.messages.create(
                model=AUDIT_MODEL,
                max_tokens=300,
                system=self.system,
                messages=[{"role": "user", "content": user}],
            )
            raw = resp.content[0].text
        except Exception as exc:  # noqa: BLE001 - audit is best-effort
            logger.warning("audit call failed: %s", exc)
            return []
        return self._parse(raw, utterance_id, agent_text)

    def _parse(self, raw: str, utterance_id: str | None, agent_text: str) -> list[Verdict]:
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text, flags=re.S).strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", text, re.S)
            if not m:
                logger.warning("unparseable audit response: %r", raw[:200])
                return []
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                return []
        out: list[Verdict] = []
        for v in data.get("violations", []):
            rid = v.get("rule_id")
            conf = float(v.get("confidence", 0))
            if rid not in {r.id for r in self.rules} or conf < MIN_CONFIDENCE:
                continue
            out.append(
                Verdict(
                    rule_id=rid,
                    severity=rule(rid).severity,
                    stage="audit",
                    utterance_id=utterance_id,
                    utterance=agent_text,
                    confidence=conf,
                    reason=str(v.get("reason", ""))[:300],
                )
            )
        return out

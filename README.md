# Shadow

A compliance supervisor that rides along on a live voice call.

Every vendor sells you the agent. Shadow is the thing that keeps the agent from
getting you fined. It consumes every caller and agent utterance in real time,
recognises a compliance trap **while the caller is still setting it**, steers
the agent before it answers, audits what the agent actually said, escalates to
a human when steering fails, and writes one signed-off log per call.

```
caller ⇄ Guava Dialog System ⇄ agent.py ⇄ shadow.py ⇄ policy.py
                                  │           │
                                  │           ├── classifier.py  (preempt · audit)
                                  │           └── rules.py       (six-rule table)
                                  └────────── recorder.py ──> runs/<call_id>/events.jsonl
                                                              runs/<call_id>/disposition.json
                                                                        │
                                                              dashboard.html (live)
```

## The mechanism

Two classifiers on the same rule table, running at different times:

| | trigger | latency budget | thread |
|---|---|---|---|
| **Pre-empt** | caller speech | must beat the agent's turn | regex runs **synchronously on the callback thread**; the intent-LLM pass is queued |
| **Audit** | agent speech | post-hoc, within a turn | queued to the worker thread, one Haiku call |

That split is the whole design. A steering instruction is worthless if it
arrives after the agent has already said the illegal thing, so the regex path
never touches a queue. Everything that can afford to be late — the LLM intent
pass, the post-hoc audit, the disposition write — is off the callback thread so
it can never stall the next event.

## The rule table

| id | severity | caller bait | what Shadow does | audit catches |
|---|---|---|---|---|
| `no_threat` | violation | sue / garnish / arrest / court | refuse to speak to legal outcomes, return to payment options | agent implies legal consequences |
| `third_party` | critical | "my wife handles this", "tell him" | only discuss with the verified account holder | agent discloses debt to a third party |
| `recording` | warn | "are you recording me?" | confirm recording plainly, continue | agent denies or dodges |
| `cease` | critical | "stop calling", "take me off your list" | acknowledge, honour, end the call | agent keeps collecting after a cease request |
| `false_urgency` | violation | "what happens if I don't pay", deadlines | state only facts in the record | agent invents deadlines, fees, credit damage |
| `identity_gate` | critical | "just tell me the balance" | no account data before verification | agent discloses before verification |

`identity_gate` is suppressed once `verified` is true, at both stages — after
verification, discussing the balance with the account holder *is* the call.

## Escalation

`policy.decide` is the single decision point. Two `violation`-severity audit
hits, one `critical` hit, an agent-requested escalation, or the caller asking
for a person → `transfer` to `SUPERVISOR_NUMBER` with an instruction naming the
reason. A cease request hangs up instead, and flags DNC. Once escalated the
shadow goes quiet — the call is the supervisor's.

## Run it

```bash
uv sync
cp .env.example .env          # GUAVA_API_KEY, GUAVA_AGENT_NUMBER, DEMO_PHONE,
                              # SUPERVISOR_NUMBER, DEMO_NAME, ANTHROPIC_API_KEY

uv run python assumptions.py            # verify the SDK surface
uv run pytest -q                        # 33 tests, no network

uv run python serve.py &                # dashboard on :8765
uv run python agent.py --phone +1555… --name "Jordan Avery"
uv run python agent.py --chat           # local text session, no phone
uv run python run_roleplay.py           # three adversarial personas
```

Open <http://localhost:8765/dashboard.html> — it follows the newest run with no
manual refresh: transcript on the left, shadow stream in the middle (steers in
amber, violations in red), disposition panel on the right when the call ends.

## Artifacts

`runs/<call_id>/events.jsonl` — every caller turn, agent turn, verdict,
instruction, escalation and task transition with timestamps.
`runs/<call_id>/disposition.json` — outcome, fields collected, steer list,
violation timeline, escalation record, DNC flag.

Sensitive fields (`last4_ssn`, `dob`) are never written to either file. That is
asserted by a test, not by convention.

That log is what a BPO shows its client.

# Aegis — Governance Layer for Financial AI Agents

A working control plane that sits in the action path of every agent. Before any
consequential action executes, the agent must get authorization from Aegis:
the fleet must not be halted, the agent must not be revoked, the action must be
in a deny-by-default permission set, it must fit inside spend and velocity
limits, it must clear data, counterparty and AI-safety guardrails, and if it is
high-value or irreversible it must carry a human signature. Every outcome —
allow, deny, block, quarantine, escalate — is sealed into a hash-chained audit
log.

> **What is real here:** the decisions. Agents are stub agents and the banking
> systems behind them are simulated, so Aegis governs real policy over synthetic
> data. No real money moves.

---

## Run it

**Requires Python 3.11+** (the backend uses modern typing syntax).

```bash
./run.sh
```

Console → <http://localhost:5173> · API docs → <http://localhost:8000/docs>

That is the whole setup. No Docker, no database, no Redis required — the first
run creates a virtualenv, installs the console's dependencies, and starts both
processes. Then, in a second terminal:

```bash
python3 demo/demo_scenario.py        # the eight-beat walkthrough
# or: ./demo/run_demo.sh --fast      # picks backend/.venv Python automatically
```

Other entry points:

```bash
./run.sh --build                    # build the console, serve everything from :8000
cd backend && .venv/bin/python -m pytest -q     # 66 tests
docker compose up --build           # full stack: Redis + PostgreSQL + OPA + Prometheus + Grafana
```

---

## The five-beat demo

`python demo/demo_scenario.py` drives the real gateway over HTTP and prints each
decision with its latency and audit hash:

| # | Beat | Outcome |
|---|------|---------|
| 1 | A $25 fee reversal | `ALLOW` — within policy |
| 2 | Reading a customer's SSN | `DENY` — outside the agent's data scope |
| 3 | A $4,000 rebooking against a $2,500 cap | `BLOCK` — over per-transaction cap |
| 4 | A revoked agent's next action | `BLOCK` — agent revoked |
| 5 | Fleet-wide emergency stop | `BLOCK` — every agent frozen |

It closes on the audit log: verify the chain, then **tamper with a past entry
directly in the database** and watch verification name the broken entry. The
same thing is available as a button on the Audit Log screen.

---

## What is in the box

```
backend/aegis/
  enforce/gateway.py    the PEP — the six gates, in order
  enforce/guardrails.py sanctions, injection, PII, exfiltration, masking
  policy/rules.py       the in-house deny-by-default evaluator
  policy/opa.py         Open Policy Agent adapter (POLICY_ENGINE=opa)
  audit.py              hash chain, single-writer, verification
  store/state.py        hot path: kill flags, spend counters, rate windows
  store/db.py           durable: agents, policies, ledger, approvals, chain
  control.py            operator actions and fleet views
  simulator.py          the stub fleet that generates traffic
frontend/src/pages/     the six console screens
sdk/python/aegis_sdk/   the client agents integrate with
backend/policies/       aegis.rego — the same permission model in Rego
```

### The decision path

```
request
  ├─ 0 identity      unknown agent / no policy bound ──────► DENY
  ├─ 1 kill state    fleet halted or agent revoked ────────► BLOCK
  ├─ 2 permissions   deny-by-default, via the PDP ─────────► DENY
  ├─ 3 spend & rate  per-txn, daily, fleet, velocity ──────► BLOCK
  ├─ 4 guardrails    sanctions, data, injection, PII ──────► QUARANTINE
  ├─ 5 oversight     high-value or irreversible ───────────► ESCALATE
  └─ 6 commit        atomic reserve ──────────────────────► ALLOW
```

Two properties worth knowing:

- **Gate 3 checks, gate 6 commits.** Limits are *evaluated* early so the denial
  reason is precise, but counters only move in one atomic operation immediately
  before ALLOW. A denied action never consumes budget, and two concurrent
  requests cannot both spend the same remaining cap — there is a test for it.
- **Every path out is the same path.** `_finalize` is the only way to return a
  decision, so no outcome can skip the audit log.

---

## Integrating an agent

**SDK mode** — the agent asks, then acts:

```python
from aegis_sdk import AegisClient

aegis = AegisClient("http://localhost:8000", agent_id="travel_concierge")

decision = await aegis.authorize("rebook_flight", amount_cents=180_00,
                                 counterparty="delta air lines")
if decision.allowed:
    book_the_flight()
else:
    log(decision.reason)          # "over per-transaction cap — $4,000.00 over $2,500.00 cap"
```

**Proxy mode** — Aegis makes the downstream call itself, so the agent never
holds a path to the money. This is what turns "should not bypass" into "cannot":

```python
result = await aegis.execute("issue_refund", amount_cents=25_00,
                             counterparty="cardmember account")
```

If the control plane is unreachable the client **fails closed** — it refuses the
action rather than proceeding ungoverned.

Pass an `idempotency_key` on retries so a network timeout cannot double-spend:
the gateway returns the cached decision without moving counters again.

**Reserve → settle** — proxy mode reserves the maximum amount atomically, calls the
bank, then settles the actual cost and releases any over-reservation (SpendGuard-style).

**Fail-closed** — set `FAIL_CLOSED=true` with Redis/PostgreSQL configured so the
gateway refuses requests when infrastructure is down instead of falling back silently.
The demo can simulate this with `POST /v1/simulator/chaos/policy-down`.

**Observability** — `GET /metrics` exposes Prometheus counters and latency
summaries; `GET /v1/metrics/summary` returns the same numbers as JSON.
With Docker Compose, Grafana is on http://localhost:3000 (admin / aegis).

---

## The control model, beyond spend

| Group | Controls |
|---|---|
| Permissions | per-action allow / approval / deny, conditions (`allow:<=50000`), data scopes |
| Spend & rate | per-transaction, daily, and fleet-wide caps; action and payment velocity |
| Data & privacy | PAN/SSN masking, need-to-know scopes, bulk-exfiltration limits |
| Counterparty | payee allowlists, sanctions / AML screening |
| AI safety | prompt-injection screening, output validation, PII-leak prevention |
| Human-in-the-loop | approval thresholds, always-approve-irreversible, escalation contact |

Every one of these is editable per agent on the **Policies** screen and takes
effect on the very next decision — no restart, no redeploy.

An approval releases **exactly one action**: the signature is bound to the
agent, action and amount, so it cannot be replayed against a different one.

---

## Measured against the target metrics

Run `cd backend && .venv/bin/python -m pytest -q -s` to reproduce.

| Metric | Target | Measured |
|---|---|---|
| Policy decision latency | < 10 ms p99 | **0.15 ms p99**, 0.08 ms median |
| Throughput | — | ~8,800 decisions/sec (single process) |
| Time to contain | < 1 s | **~4 ms** to halt the fleet |
| Enforcement accuracy | 100% | 66/66 tests, every gate covered |
| Audit completeness | one entry per action | asserted, including operator actions |
| Chain integrity | tampering detected | edits, deletions and amount changes all caught |

---

## Configuration

Everything runs with no configuration. Set these to scale it up:

| Variable | Default | Effect |
|---|---|---|
| `REDIS_URL` | — | Hot-path counters move to Redis (atomic Lua commit) |
| `DATABASE_URL` | SQLite | PostgreSQL for policies, ledger and audit chain |
| `POLICY_ENGINE` | `rules` | `opa` routes the permission gate to an OPA sidecar |
| `FLEET_DAILY_CAP_CENTS` | 5000000 | Fleet-wide daily budget ($50,000) |
| `DEMO_MODE` | `true` | Enables the tamper/restore endpoints |
| `SEED_ON_START` | `true` | Seeds the six-agent demo fleet if none exists |

The storage and policy layers sit behind interfaces and fall back rather than
fail: if Redis or PostgreSQL is configured but unreachable, Aegis logs it and
continues on the in-process store, and the ~100-line in-house evaluator is
always available if OPA is not.

---

## Known limits

- Rate limiting uses fixed one-minute windows (the standard Redis INCR/EXPIRE
  approach), so a burst can straddle a boundary. A sliding window is a drop-in
  change to one Lua script.
- Operator identity is an `X-Operator` header standing in for SSO/mTLS. Agent
  identity is likewise asserted, not proven; mTLS agent identity is post-MVP.
- Prompt-injection and PII detection are pattern-based and best-effort by
  design — they are layered *behind* the permission model and *in front of*
  human approval, never relied on alone.
- The audit chain is tamper-**evident**, not tamper-proof: it proves that
  history was altered, which is the property an auditor needs. Anchoring the
  head hash externally would extend this to a third party.

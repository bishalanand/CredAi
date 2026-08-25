# SmartDialer — Architecture

## 1. Goal

Improve agent utilization over strict progressive dialing without creating abandoned,
agent-less connected calls — a compliance risk, not just a UX problem. Predictive pacing
is allowed to *suggest* aggression; it is never allowed to *act* on it directly.

Non-negotiable invariant: **no code path lets the pacing engine place or unblock a call
without passing through the Safety Controller.**

## 2. High-level architecture

```
Campaign
   │
   ▼
Pacing Engine  ──(Progressive Mode)──┐
   │                                  │
   └─(Predictive Mode)──► "I think we can start N more calls"
                                       │
                                       ▼
                              Safety Controller
                    (approve / reduce / reject / force progressive)
                                       │
                                       ▼
                               Call Allocator
                  (binds a specific borrower to a specific agent)
                                       │
                                       ▼
                          Telecom Provider Interface
                         ┌─────────────┴─────────────┐
                         ▼                            ▼
                  Mock Provider A               Mock Provider B
               (fast, reliable, low            (slow, timeouts,
                failure rate)                   duplicate/out-of-order
                                                 events)
```

Key property: the **Pacing Engine only produces a number and a rationale.** It has no
reference to the Call Allocator or Telecom Provider. The Safety Controller is the only
component with authority to trigger allocation. This is enforced structurally (the
pacing engine module has no import of / dependency on the allocator), not just by
convention.

## 3. Components

### 3.1 Campaign
Owns the list of borrowers to call and campaign-level config (which mode, throttle
limits, working hours, retry policy). Feeds the Pacing Engine on each tick.

### 3.2 Pacing Engine

**Progressive Mode**
- Deterministic: `max_new_calls = count(agents in AVAILABLE) - count(agents already
  RESERVED for a pending call)`.
- One available agent → at most one outbound call. No estimation involved.

**Predictive Mode**
- Rule-based/statistical, not ML. Inputs: current `AVAILABLE` agent count, calls
  currently `RINGING`, recent rolling answer rate (e.g. last N calls or last T
  minutes), average call setup time, average talk time, and recent provider health
  (error/timeout rate).
- Produces a *requested* dial count and a short rationale string (e.g. `"18 avail,
  0.42 rolling answer rate, requesting 12 to reach target 90% projected utilization"`).
- Never writes to the Call Allocator or Telecom Provider directly — returns its
  request to the Safety Controller only.

#### 3.2.1 Predictive pacing — the formula

**Inputs, refreshed each pacing tick:**

| Symbol | Meaning |
|---|---|
| `A` | Agents currently `AVAILABLE` |
| `R` | Calls currently in flight (`INITIATED`/`RINGING`, not yet resolved) |
| `p` | Rolling answer rate — probability a placed call gets answered |
| `h` | Provider health factor, 0–1 |
| `b` | Safety buffer, e.g. 0.15 |

**1. Rolling answer rate** — adapts to changing conditions (e.g. simulation scenario D):

```
p_t = α · outcome_t + (1 − α) · p_(t−1)
```

`outcome_t` is 1 if the last-resolved call was answered, 0 if not. `α` (e.g. 0.2)
controls reaction speed — high enough to track a real shift within a few dozen
calls, low enough not to whipsaw on noise. Below a minimum sample size (e.g. 20
resolved calls), `p` isn't trustworthy yet — use a conservative default (0.3) or
force progressive mode until enough data exists.

**2. Core request** — how many new calls to place. Calls already ringing (`R`) will
also convert at rate `p`, and each connect needs one agent, so new calls shouldn't
push total expected connects past available capacity:

```
N · p  +  R · p  ≤  A
N  ≤  (A − R·p) / p
```

**3. Apply the safety buffer and provider health:**

```
N_request = floor( h · (1 − b) · (A − R·p) / p )
N_request = max(0, N_request)
```

`h` shrinks the request as the provider's recent timeout/error rate rises (e.g.
`h = 1 − min(1, error_rate / threshold)`); `b` is a permanent margin against
estimation error, since `p` is a rolling estimate, not a certainty.

**Worked example — "why 17, not 10":** `A = 40`, `R = 12`, `p = 0.55` (rolling),
`h = 0.95` (provider slightly degraded), `b = 0.15`:

```
(A − R·p) / p = (40 − 12×0.55) / 0.55 = (40 − 6.6) / 0.55 ≈ 60.7
N_request = floor(0.95 × 0.85 × 60.7) ≈ floor(48.99) ≈ 17
```

*"40 available, 12 already ringing at a 55% rolling answer rate, provider slightly
degraded, 15% safety margin → request 17."* Every number in that sentence maps to a
variable above, and the Safety Controller re-checks all of them against ground
truth before any of the 17 are actually placed (§3.3).

**When to fall back to progressive (1:1) instead of trusting the formula:**
- `p` below the minimum sample threshold (not enough data to estimate answer rate).
- `h` below a floor value — provider is clearly unhealthy, regardless of what the
  formula outputs.
- Any Safety Controller re-check at execution time (§3.3) finds `A` has changed
  enough that the approved request is no longer valid.

### 3.3 Safety Controller
- Sole gatekeeper between pacing decisions and real calls.
- Re-validates the pacing engine's request against ground truth at execution time
  (current `AVAILABLE` count, current provider health, a hard ceiling on
  calls-in-flight vs. agents-available).
- Can: **approve** as-is, **reduce** the count, **reject** entirely, or **force
  fallback to progressive behavior** (e.g. if provider error rate crosses a
  threshold, or predicted answer rate has just collapsed).
- Every decision is logged with the reason, so "why did we place 12 instead of 18"
  is always answerable from the log, not from re-running the model.

### 3.4 Call Allocator
- Takes an approved call count and turns it into concrete `(borrower, agent)` pairs.
- Responsible for atomically reserving both the agent and the borrower before
  handing off to the provider — see §5 (Concurrency Model).

### 3.5 Telecom Provider Interface
- Small interface (`place_call`, `on_event` callback/webhook) that the allocator
  depends on. Neither the allocator nor anything upstream knows which mock provider
  is behind it.
- **Mock Provider A** — fast, reliable, low failure rate, in-order events.
- **Mock Provider B** — slower, injects timeouts, occasionally re-sends the same
  event, occasionally delivers events out of order — used to prove the state
  machines and idempotency logic actually hold up, not just the happy path.

## 4. Agent state machine

```
OFFLINE ──► AVAILABLE ──► RESERVED ──► DIALING ──► CONNECTED ──► WRAP_UP ──► AVAILABLE
               ▲                                                                │
               └────────────────────────── PAUSED ◄────────────────────────────┘
```

| From | Event | To |
|---|---|---|
| OFFLINE | agent logs in | AVAILABLE |
| AVAILABLE | allocator reserves agent | RESERVED |
| RESERVED | call setup begins | DIALING |
| RESERVED | reservation times out / call setup fails | AVAILABLE |
| DIALING | provider reports ANSWERED | CONNECTED |
| DIALING | provider reports FAILED / no answer | AVAILABLE |
| CONNECTED | call ends | WRAP_UP |
| WRAP_UP | wrap-up timer elapses | AVAILABLE |
| any active state | agent pauses | PAUSED |
| PAUSED | agent resumes | AVAILABLE |
| any state | agent logs out | OFFLINE |

Illegal transitions (e.g. `CONNECTED → RESERVED`) are rejected by the state machine
layer, not silently ignored — an invalid transition attempt is logged as an error,
since it usually signals a duplicate or out-of-order provider event.

## 5. Call state machine

```
QUEUED ──► RESERVED ──► INITIATED ──► RINGING ──► ANSWERED ──► CONNECTED ──► COMPLETED
                                          │             
                                          └──► FAILED / CANCELLED
```

| From | Event | To |
|---|---|---|
| QUEUED | allocator picks this call | RESERVED |
| RESERVED | agent + borrower locked, dispatched to provider | INITIATED |
| INITIATED | provider acks | RINGING |
| RINGING | provider reports ANSWERED | ANSWERED |
| ANSWERED | bridge/connect confirmed | CONNECTED |
| CONNECTED | either party hangs up | COMPLETED |
| RINGING / INITIATED | no answer, busy, provider error | FAILED |
| QUEUED / RESERVED | campaign paused, borrower removed, etc. | CANCELLED |

### Handling duplicate / out-of-order / crash cases

- Every provider event carries the call's current version/sequence expectation.
  Transitions are applied via a **strictly monotonic state map**: each state has a
  defined *rank*, and an incoming event is only applied if it moves the call to an
  equal-or-later rank than its current state, or is a recognized terminal repeat.
- `ANSWERED → ANSWERED → ANSWERED → COMPLETED`: first `ANSWERED` transitions
  `RINGING → ANSWERED`; the next two are no-ops (already at/after that rank) but are
  still recorded in an event log for auditability; `COMPLETED` applies normally.
- `COMPLETED → ANSWERED → RINGING`: `COMPLETED` moves the call to its terminal rank;
  the later `ANSWERED` and `RINGING` events arrive *behind* the current rank and are
  discarded (logged as "stale event ignored"), never rewinding a terminal call.
- **Worker crash right after ANSWERED**: the call was persisted as `ANSWERED` in
  SQLite before the worker acknowledged the event to the provider (write-then-ack
  ordering). On restart, a reconciliation pass finds calls sitting in a non-terminal
  state past a staleness threshold and either resumes processing (if the provider
  later delivers `COMPLETED`) or force-resolves them via a provider status query /
  timeout-to-FAILED policy. The call never gets stuck in limbo indefinitely, and it
  never double-processes billing/wrap-up because the terminal transition is
  idempotent.

## 6. Concurrency model

**Mechanism: DB row lock / transaction (SQLite).**

- Agents and calls are rows in SQLite. Reserving an agent is done inside a single
  transaction: `SELECT ... WHERE id = ? AND status = 'AVAILABLE' FOR UPDATE`-equivalent
  (SQLite: `BEGIN IMMEDIATE` transaction + conditional `UPDATE ... WHERE status =
  'AVAILABLE'`, checking `rowcount`), which acquires a write lock on that row for the
  duration of the transaction.
- If Worker A and Worker B both try to reserve agent #17 at the same instant: SQLite's
  `BEGIN IMMEDIATE` serializes writers at the database-connection level. Whichever
  transaction commits first flips the row to `RESERVED`; the second transaction's
  conditional `UPDATE` matches zero rows (because the `WHERE status = 'AVAILABLE'`
  predicate no longer holds) and is treated as "reservation failed, pick another
  agent" — never as a successful reservation.
- This makes double-reservation **structurally impossible**, not just unlikely: the
  correctness guarantee comes from the database's transaction isolation, not from
  application-level timing assumptions.
- The same pattern (conditional update + rowcount check inside a transaction) is used
  for borrower reservation, so two workers can't dial the same borrower twice
  concurrently either.

**Why not Redis/Kafka/etc.:** for a single-process-or-few-workers prototype at this
scale, SQLite's transactional guarantees give the same correctness property (atomic
compare-and-set on a row) with far less operational surface. It's called out explicitly
in §8 as the first thing to replace at real scale — this is a deliberate, documented
trade-off, not an oversight.

## 7. Distributed system reasoning (multi-worker)

- **Agent/borrower allocation**: covered by §6 — SQLite transactions are the single
  source of truth; no worker trusts an in-memory view without confirming through a
  DB write.
- **Duplicate jobs**: each dial attempt is created with a deterministic idempotency
  key (e.g. `campaign_id:borrower_id:attempt_n`). Inserting a call row uses
  `INSERT ... WHERE NOT EXISTS` semantics, so a retried/duplicated job that tries to
  create the same call again is a no-op rather than a second call.
- **Retries**: failed call setup is retried with backoff and a max-attempt cap tied to
  the same idempotency key, so retries don't create parallel duplicate calls.
- **Worker crashes / stale state**: any row that's been `RESERVED`/`INITIATED` past a
  lease/timeout window is picked up by a reconciliation sweep (run by any live worker)
  and released back to `AVAILABLE` / re-queued, rather than being lost.
- **Provider events**: handled by a single logical event-ingestion path so ordering
  and idempotency logic (§5) is applied once, regardless of which worker's webhook
  endpoint happened to receive it.

This is intentionally **not** using Kafka/Redis/microservices: at this scale, a
shared SQLite DB with transactional locking gives correctness with much lower
complexity. The trade-off is documented, not hidden — see §8 for where this stops
being true.

## 8. Scaling: what breaks first, and why

| Agents | What breaks | Why | Fix |
|---|---|---|---|
| ~100 | Nothing | SQLite handles this comfortably for a prototype's throughput | — |
| ~1,000 | Writer contention on SQLite | SQLite allows one writer at a time; agent-reservation transactions start queuing behind each other under concurrent load | Move to a DB that supports real row-level locking with multiple concurrent writers (Postgres) so contention is per-row, not per-database |
| ~10,000 | Reconciliation sweep + single-DB read load | A single Postgres instance polling/scanning for stale reservations across 10k+ rows on a tight interval becomes the bottleneck, and the Safety Controller's "check ground truth" read on every pacing decision adds load at high call-attempt rates | Shard by campaign, move agent-availability counts to a fast in-memory aggregate (updated transactionally alongside the DB write) so the Safety Controller reads a cheap counter instead of scanning agent rows; move reconciliation to an indexed, time-bucketed query instead of a full scan |

The bottleneck is never "not enough compute" — it's **write contention on a single
DB and the increasing cost of "read ground truth before every safety decision" as
the row count and check frequency both grow.** That's what gets fixed first, not
server count.

## 9. Stack summary

| Choice | What it is | Why | What it makes harder |
|---|---|---|---|
| Python | Implementation language | Fast to write clear, explicit state-machine and pacing logic; good for the "explain every line" requirement | Not the natural choice for extreme concurrency/throughput — acceptable at prototype scale, called out in §8 as a future concern |
| SQLite | Persistence + concurrency control | Zero setup, real transactional guarantees, single-writer semantics are enough to prove correctness at this scale | Only one writer at a time — the stated first bottleneck at ~1,000 agents |
| No message queue | — | Nothing in this prototype needs asynchronous fan-out beyond what a DB-polling worker loop handles; adding Kafka/Redis here would add operational complexity without fixing a problem the prototype actually has | Would need to be introduced once true horizontal worker scaling is required |

## 10. Answer to "how would you get predictive utilization with progressive safety?"

Split *deciding* from *acting*, and never let the same component do both. The
Predictive Pacing Engine (§3.2.1) is purely advisory: it turns live agent
availability, in-flight calls, rolling answer rate, and provider health into a
requested dial count via `N_request = floor(h · (1 − b) · (A − R·p) / p)`. It has
no reference to the Call Allocator or Telecom Provider, so it cannot place a call
even if the formula is wrong — that's a structural guarantee, not a convention.

All authority to act sits in the Safety Controller (§3.3), which re-validates that
request against ground truth at the moment of execution — not the moment of
prediction. It re-checks live `A`, live provider health, and a hard ceiling of
in-flight calls vs. available agents, and can approve, reduce, reject, or collapse
the whole system to progressive behavior (`N = A`, one call per available agent)
the instant any guardrail is violated — low sample size on `p`, `h` below its
floor, or a stale request that no longer matches current `A`. Because that
fallback is progressive dialing's exact rule, not a softer approximation of it,
the system's worst case is never worse than progressive.

The result: when conditions are healthy, predictive pacing captures the
utilization upside the formula computes; when they're not, the system doesn't try
to be clever — it just becomes progressive again, deterministically, every time.
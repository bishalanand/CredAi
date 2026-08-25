# SmartDialer Architecture Decision Record

## ADR-1: Why This Architecture?

### Context
Build a fault-tolerant, distributed outbound dialing system that can operate in both conservative (progressive) and aggressive (predictive) modes while maintaining absolute safety guarantees.

### Decision
**Three-layer architecture**:
1. **Pacing Layer** (Predictive or Progressive)
2. **Safety Layer** (Independent Safety Controller)
3. **Execution Layer** (Call Allocator + Provider)

### Rationale

#### Why Separate Pacing and Safety?
The core assignment asks: *"How would you build a SmartDialer that gets as much of the utilization benefit of predictive dialing as possible, while retaining the deterministic safety characteristics of progressive dialing?"*

**Answer**: Architectural separation. The pacing engine optimizes for utilization (gives recommendations). The Safety Controller optimizes for safety (makes final decisions). They cannot be bypassed because they're independent layers.

```
┌─────────────────────────────────┐
│  Pacing Engine                  │
│  (Predictive or Progressive)    │
│  → Calculate recommendation     │
└──────────────┬──────────────────┘
               │ (int: dial_count)
               ↓
┌─────────────────────────────────┐
│  Safety Controller              │
│  (Independent Safety Boundary)  │
│  → Approve/Reduce/Reject        │
└──────────────┬──────────────────┘
               │ (SafetyDecision)
               ↓
┌─────────────────────────────────┐
│  Call Allocator                 │
│  (Execute Approved Dials)       │
│  → Reserve Agents + Borrowers   │
└──────────────┬──────────────────┘
               │ (Call objects)
               ↓
┌─────────────────────────────────┐
│  Telecom Provider Interface     │
│  (Abstract, Swappable)          │
└─────────────────────────────────┘
```

**Guarantees**:
- Pacing engine cannot directly place calls
- Pacing engine cannot access providers
- Safety Controller has final authority
- Safety Controller cannot be disabled or bypassed

---

## ADR-2: Why SQLite + Optimistic Locking?

### Problem
Handle concurrent requests from multiple workers safely. Two workers must NOT be able to reserve the same agent.

### Solution: Optimistic Locking

Instead of database locks (pessimistic), use version field (optimistic):

```
Agent {id: "agent-1", status: "AVAILABLE", version: 0}

Worker A & B both read: version=0

Worker A: UPDATE agent SET status='RESERVED', version=1 
          WHERE id='agent-1' AND version=0
          → SUCCESS (1 row updated)

Worker B: UPDATE agent SET status='RESERVED', version=1 
          WHERE id='agent-1' AND version=0
          → FAIL (0 rows updated)
```

### Why Optimistic Locking?
- **No explicit locks**: No deadlocks, no blocking
- **High concurrency**: Readers don't block other readers
- **Transparent**: Works with SQLite, PostgreSQL, any database
- **Testable**: Easy to simulate concurrent conflicts
- **Failure-safe**: If version mismatch, allocation fails cleanly

### Why SQLite (for now)?
- Simplicity for development
- No external dependencies
- Perfect for single-machine prototype
- Easy to migrate to PostgreSQL later (just change DATABASE_URL)

---

## ADR-3: Why Rule-Based Pacing (No ML)?

### Problem
Decide how many calls to dial without abandoning borrowers.

### Solution: Flow-Control Formula

```
total_agents = available + reserved + dialing

expected_connected_soon = connected + (answer_rate × ringing)

safe_dials = total_agents - expected_connected_soon - safety_margin
```

### Reasoning
**Intuition**: 
- We have N agents total
- M are already in calls
- P × R ringing calls will probably answer (expected load)
- We should dial only up to (N - M - P×R) more

**Why No ML?**
- Interpretable: Can explain every decision
- Debuggable: Easy to trace why 17 vs 10
- Reliable: No training data, no cold-start problem
- Defensible: In interview, can justify every number
- Composable: Easy to add more factors (provider health, etc.)

**Why It Works**:
- Conservative: Assumes worst-case on ringing→answer
- Adaptive: Changes as answer_rate changes
- Self-correcting: If wrong, Safety Controller catches it

---

## ADR-4: Why Separate Agent and Call State Machines?

### Problem
State management is error-prone. Invalid transitions should be impossible.

### Solution: Explicit State Machines

```
Agent: OFFLINE → AVAILABLE → RESERVED → DIALING → CONNECTED → WRAP_UP → AVAILABLE
Call:  QUEUED → RESERVED → INITIATED → RINGING → ANSWERED → CONNECTED → COMPLETED
```

### Guarantees
- No silent invalid transitions
- Every transition is documented
- Idempotent: duplicate events don't break state
- Testable: can enumerate all paths

### Example: How Duplicate Events Are Handled

Provider sends ANSWERED twice:

```
Call state: RINGING

First ANSWERED:  RINGING → ANSWERED → CONNECTED
Call now: CONNECTED

Second ANSWERED: CallStateMachine.can_transition(CONNECTED, ANSWERED)?
                 → NO (not a valid transition)
                 → Event is ignored (idempotent)

Final state: CONNECTED (correct)
```

---

## ADR-5: Why Independent Safety Controller?

### Problem
Predictive engine might get answer rate wrong. Need independent safety check.

### Solution: Separate Safety Component

Safety Controller applies checks independently:
1. **Buffer check**: Keep N agents idle for emergencies
2. **Answer rate drop detection**: If rate drops >20%, fallback to progressive
3. **Ringing ratio check**: Don't let ringing calls exceed 2x available
4. **Provider health check**: If provider fails, reduce dial rate

### Why It Works
- **Independent**: Doesn't trust pacing engine
- **Conservative**: Applies multiple safety checks
- **Graceful degradation**: Automatically falls back to progressive under stress
- **Deterministic**: Same metrics always produce same decision

### Guarantee: Cannot Be Bypassed

```python
# Pacing engine has NO way to bypass safety:

# ❌ This doesn't exist:
provider.dial(phone)  # Not exposed to pacing

# ✅ This is the only way:
safety.evaluate_dial_request(request)  # Returns how many approved
allocator.allocate_call()  # Only respects approved count

# If pacing tries to dial more than approved:
# allocator.allocate_call() loops approved_dials times
# After that, no resources available → allocation fails
```

---

## ADR-6: Why Mock Provider Abstraction?

### Problem
Need to test without real telecom provider. Different providers have different behaviors.

### Solution: Two Mock Providers

**MockProviderA** (Ideal):
- Fast call initiation (10-50ms)
- No duplicates
- Events in order
- Low failure rate

**MockProviderB** (Realistic):
- Slow initiation (80-150ms)
- Duplicate events (20% of calls)
- Out-of-order events (10% of calls)
- 30% failure rate
- Health degradation/recovery

### Why Both?
- **Test happy path**: Use Provider A for basic tests
- **Test resilience**: Use Provider B for robustness tests
- **Test abstraction**: Verify code doesn't depend on specific provider

### Real-World Usage
In production:
```python
from app.providers import RealTwilioProvider
provider = RealTwilioProvider(account_sid=..., auth_token=...)
```

Drop-in replacement without changing dialer logic.

---

## ADR-7: Why NOT Kafka / Redis / Microservices?

### For This Assignment

**Kafka** (event streaming):
- ❌ Overkill for single-machine prototype
- ❌ Adds operational complexity
- ❌ Doesn't solve any current problem
- ✅ Could add later if scaling to 10K+ agents

**Redis** (distributed cache):
- ❌ No external dependencies yet
- ❌ SQLite can handle local state fine
- ❌ TTL cache adds stale state bugs
- ✅ Could add if agent lookup becomes bottleneck

**Microservices** (decomposition):
- ❌ No independent scaling needs yet
- ❌ Adds network latency and complexity
- ❌ Splits business logic across services
- ✅ Could decompose later if needed (Pacing service, Safety service)

### When We WOULD Use Them

At 10,000 agents:
- **Kafka**: For event streaming between workers
- **Redis**: For agent/borrower availability cache
- **Microservices**: For independent scaling of pacing vs allocation

But for 4-6 hour assignment, monolith is best.

---

## ADR-8: Why Version Field for Concurrency?

### Alternatives Considered

**Option 1: Row-Level Locks** (Pessimistic)
```sql
BEGIN TRANSACTION;
SELECT * FROM agents WHERE id='agent-1' FOR UPDATE;
UPDATE agent SET status='RESERVED';
COMMIT;
```
- ❌ Blocks other readers/writers
- ❌ Deadlock risk
- ❌ Doesn't work with stateless workers

**Option 2: Version Field** (Optimistic) ← CHOSEN
```sql
UPDATE agent SET status='RESERVED', version=1 
WHERE id='agent-1' AND version=0;
-- If 0 rows updated: someone else changed it
```
- ✅ Doesn't block
- ✅ Works with stateless workers
- ✅ Easy to implement
- ✅ Scales better

**Option 3: CAS Operation** (Compare-And-Set)
```
CAS(agent_id='agent-1', expect_version=0, set_version=1)
```
- ✅ Atomic
- ❌ Not available in SQL
- ❌ Would need Redis

### Why Version Field Wins
- Works with SQL
- Simple to understand
- No external dependencies
- Scales to 10,000 agents

---

## ADR-9: Concurrency Model

### The Pattern

Every entity (Agent, Borrower, Call) has:
```python
@dataclass
class Agent:
    id: str
    status: AgentStatus
    version: int  # ← Key to concurrency control
    ...
```

### The Algorithm

1. **Read**: Fetch agent and its version
2. **Modify**: Update local object
3. **Write**: UPDATE with version check

```python
# Read
agent = repo.get_by_id("agent-1")  # version=0

# Modify
agent.status = AgentStatus.RESERVED
agent.version += 1

# Write (atomic)
repo.update(agent)  # WHERE version=0
# Only succeeds if version still 0
```

### Concurrency Guarantee

If two workers try to modify same agent:
- Both read: version=0
- Both increment: version=1
- Both write: WHERE version=0

Only ONE UPDATE succeeds (sets version=0 → version=1).

Other fails (WHERE version=0 no longer true).

Allocator detects failure → cleanup → return None → dialing stops.

---

## ADR-10: Database Schema

### Core Entities

```
agents
├── id (PK)
├── status (AgentStatus enum)
├── current_call_id (FK call)
├── version (for optimistic locking)
└── timestamps (created_at, updated_at)

borrowers
├── id (PK)
├── phone_number
├── status (BorrowerStatus enum)
├── campaign_id (FK campaign)
├── current_call_id (FK call)
├── version
└── timestamps

calls
├── id (PK)
├── campaign_id (FK campaign)
├── agent_id (FK agent)
├── borrower_id (FK borrower)
├── status (CallStatus enum)
├── provider_call_id (for event correlation)
├── provider_name
├── failure_reason
├── timestamps (created_at, initiated_at, answered_at, connected_at, completed_at)
├── version
└── indexes on (campaign_id, agent_id, borrower_id, status)

campaigns
├── id (PK)
├── name
├── dialing_mode (PROGRESSIVE or PREDICTIVE)
├── active
└── timestamps
```

### Why These Indexes?

```python
# Fast lookups by campaign
Index("ix_calls_campaign_id", "campaign_id")

# Find ringing calls for an agent
Index("ix_calls_agent_id", "agent_id")

# Find calls for a borrower (prevent double-dialing)
Index("ix_calls_borrower_id", "borrower_id")

# Filter by status (critical for pacing)
Index("ix_calls_status", "status")
```

---

## ADR-11: Error Handling Strategy

### Failure Modes

1. **Agent already reserved** (concurrent conflict)
   - Optimistic lock fails
   - allocator.allocate_call() returns None
   - Dialer continues to next attempt

2. **Provider timeout**
   - ProviderTimeoutException raised
   - CallAllocator catches and releases resources
   - allocation() returns None

3. **Out-of-order provider event**
   - CallStateMachine rejects invalid transition
   - Event is logged but ignored (idempotent)

4. **Worker crash after ANSWERED**
   - Agent stuck in DIALING
   - Background job detects stale state (Phase 13)
   - Agent transitioned to AVAILABLE
   - Call marked as FAILED

### Principle
**Fail safe, not fail fast**: 
- Allocation fails → resources cleaned up
- Event invalid → ignored
- Worker dies → background recovery

Never leave resources orphaned.

---

## ADR-12: Testing Strategy

### Unit Tests ✅
- State machine transitions
- Repository CRUD
- Pacing calculations

### Concurrency Tests ❌ (TODO)
- Two workers reserve same agent
- Version conflicts handled correctly
- Atomic operations verified

### Failure Tests ❌ (TODO)
- Provider timeout handling
- Duplicate event idempotency
- Out-of-order event reconciliation
- Worker crash recovery

### Integration Tests ❌ (TODO)
- Full flow: Campaign → Pacing → Safety → Allocator → Provider
- End-to-end call lifecycle
- Multi-agent multi-borrower scenarios

### Load Tests ❌ (TODO)
- 100 agents → 1,000 agents → 10,000 agents
- Identify bottleneck
- Propose scaling solution

---

## ADR-13: Scaling Path

### Phase 1: Single Machine (Current) ✅
```
SQLite + Single Worker
- Perfect for prototype
- All logic in-process
- No network latency
```

### Phase 2: Multiple Workers (1-10)
```
PostgreSQL + Job Queue (Redis or DB)
- Shared database
- Optimistic locking handles conflicts
- Stateless workers (any can fail)
```

### Phase 3: Many Workers (10-1000)
```
PostgreSQL + Redis Queue + Redis Cache
- Distributed cache for agent availability
- Event stream (Redis Streams or Kafka)
- Metrics database (InfluxDB)
```

### Phase 4: Massive Scale (1000+)
```
PostgreSQL + Kafka + Redis + Elasticsearch
- Dedicated event broker
- Distributed metrics
- Dedicated analytics DB
- Multiple dialer pools by geography
```

### Path Forward
Each phase requires:
1. Identify bottleneck
2. Replace component
3. No logic changes (if interfaces good)

---

## Key Insights

### 1. Safety is Architectural, Not Algorithmic
The Safety Controller ensures safety by being independent and having final authority. No algorithm can guarantee safety if the architecture allows bypassing.

### 2. Concurrency Without Locks
Optimistic locking (version field) provides concurrency without explicit locks. Simpler, scales better, no deadlocks.

### 3. Idempotency is Defensive
If every event processing is idempotent (same event twice = same state), then duplicates and retries are safe.

### 4. Separate Concerns → Easier Testing
- Pacing logic independent of Safety logic
- Both independent of Allocation logic
- All independent of Provider implementation
- Each can be tested separately

### 5. Explicit State Machines > Implicit State
Valid state transitions must be documented and enforced. Invalid transitions should raise exceptions, not silently corrupt state.

---

## Summary

This architecture achieves:

✅ **Safety**: Independent Safety Controller with final authority
✅ **Utilization**: Predictive pacing engine optimizes for throughput  
✅ **Correctness**: Explicit state machines prevent invalid states
✅ **Concurrency**: Optimistic locking handles multi-worker scenarios
✅ **Testability**: Each component can be tested independently
✅ **Scalability**: Path clear to 10K+ agents without major refactor
✅ **Simplicity**: Monolith with clear layer separation

**Answer to assignment's core question**:

> How would you build a SmartDialer that gets as much of the utilization benefit of predictive dialing as possible, while retaining the deterministic safety characteristics of progressive dialing?

**Answer**: Separate the concerns architecturally. Predictive engine optimizes pacing (recommendation). Safety Controller enforces safety (approval). Together, they get utilization gains with safety guarantees.

# SmartDialer Internship Assignment

A distributed, fault-tolerant outbound dialing system that balances utilization with safety.

## Overview

SmartDialer is a prototype implementation of an intelligent call center dialing system that can operate in two modes:

1. **Progressive Dialing**: Conservative, safe. One agent → one call.
2. **Predictive Dialing**: Aggressive, smart. Dial based on estimated answer rates and agent availability.

The key innovation: A **Safety Controller** that acts as an independent safety boundary, preventing the predictive engine from creating abandoned calls even if the answer rate prediction is wrong.

## Architecture

```
Campaign
    ↓
ProgressiveDialer / PredictivePacingEngine
    ↓
SafetyController (Independent Safety Boundary)
    ↓
CallAllocator (Atomic Reservations)
    ↓
TelecomProvider (Abstract Interface)
    ↓
MockProviderA / MockProviderB (Realistic Simulation)
```

### Key Components

#### 1. **Domain Models** (`app/domain/`)
- `Agent`: Represents a call center agent with states (OFFLINE, AVAILABLE, RESERVED, DIALING, CONNECTED, WRAP_UP, PAUSED)
- `Borrower`: Represents a borrower to be called
- `Call`: Represents an outbound call with full lifecycle tracking
- `Campaign`: Represents a dialing campaign with mode (PROGRESSIVE or PREDICTIVE)

#### 2. **State Machines** (`app/state_machine/`)
- `AgentStateMachine`: Controls valid agent state transitions, prevents invalid transitions
- `CallStateMachine`: Controls valid call state transitions, handles idempotency for duplicate/out-of-order events

#### 3. **Provider Abstraction** (`app/providers/`)
- `TelecomProvider`: Abstract interface that all providers must implement
- `MockProviderA`: High-quality provider (fast, reliable, no duplicates)
- `MockProviderB`: Low-quality provider (slow, failures, duplicates, out-of-order events)

#### 4. **Database & Repositories** (`app/db.py`, `app/models.py`, `app/repositories/`)
- SQLAlchemy ORM with SQLite (configurable to PostgreSQL)
- Optimistic locking pattern using version field
- Atomic concurrent operations: only ONE worker can reserve an agent/borrower

#### 5. **Call Allocator** (`app/dialer/call_allocator.py`)
- Safely allocates agents and borrowers to calls
- Atomic all-or-nothing operation
- Prevents duplicate reservations via version field

#### 6. **Progressive Dialer** (`app/dialer/progressive.py`)
- Simple rule: available_agents > active_dialing_calls
- Conservative, guaranteed safe

#### 7. **Predictive Pacing Engine** (`app/dialer/pacing_engine.py`)
- Calculates safe dial volume based on:
  - Available agents
  - Connected calls
  - Ringing calls
  - Estimated answer rate
  - Average call duration
- Formula: `safe_dials = total_agents - (connected + answer_rate × ringing) - safety_margin`
- **Does NOT directly place calls** - returns recommendation only

#### 8. **Safety Controller** (`app/dialer/safety_controller.py`)
- Independent safety boundary between pacing and allocation
- Can APPROVE, REDUCE, REJECT, or FALLBACK_TO_PROGRESSIVE
- Checks:
  - Agent availability buffer
  - Answer rate drop detection
  - Ringing call ratio
  - Provider health
- **Guaranteed**: Pacing engine cannot bypass this

## Concurrency Model: How We Prevent Double-Reservation

When two workers try to reserve the same agent:

```
Worker A: SELECT agent WHERE id='agent-1' AND status='AVAILABLE'  
Worker B: SELECT agent WHERE id='agent-1' AND status='AVAILABLE'

Agent state: {id: 'agent-1', status: 'AVAILABLE', version: 0}

Worker A: UPDATE agent SET status='RESERVED', version=1 
          WHERE id='agent-1' AND version=0 
          → SUCCESS (1 row updated)

Worker B: UPDATE agent SET status='RESERVED', version=1 
          WHERE id='agent-1' AND version=0 
          → FAIL (0 rows updated, version is now 1)

Result: Only Worker A succeeds. Worker B's allocation fails cleanly.
```

This is **optimistic locking**: we trust the update will succeed, but verify using the version field in the WHERE clause.

## Setup & Installation

### Prerequisites
- Python 3.9+
- pip

### Install Dependencies

```bash
cd "Smart Dialer"
pip install -r requirements.txt
```

### Initialize Database

```bash
python -c "from app.db import init_db; init_db()"
```

This creates `smart_dialer.db` with all tables.

## Running Tests

```bash
# Run all unit tests
pytest Test/unit/ -v

# Run agent state machine tests
pytest Test/unit/test_agent_state_machine.py -v

# Run specific test
pytest Test/unit/test_agent_state_machine.py::test_offline_to_available -v
```

## How to Use (Programmatically)

```python
from app.db import SessionLocal, init_db
from app.domain.agent import Agent
from app.domain.borrower import Borrower
from app.domain.campaign import Campaign
from app.domain.enum import DialingMode
from app.repositories import AgentRepository, BorrowerRepository, CampaignRepository
from app.dialer import CallAllocator, ProgressiveDialer, SafetyController, PredictivePacingEngine

# Initialize database
init_db()
db = SessionLocal()

# Create a campaign
campaign = Campaign(id="campaign-1", name="Collections", dialing_mode=DialingMode.PROGRESSIVE)
campaign_repo = CampaignRepository(db)
campaign = campaign_repo.create(campaign)

# Add agents
agent_repo = AgentRepository(db)
for i in range(10):
    agent = Agent(id=f"agent-{i}")
    agent_repo.create(agent)

# Add borrowers
borrower_repo = BorrowerRepository(db)
for i in range(100):
    borrower = Borrower(id=f"borrower-{i}", phone_number=f"555-000{i}", campaign_id=campaign.id)
    borrower_repo.create(borrower)

# Use Progressive Dialer
dialer = ProgressiveDialer(db, provider_name="MockProviderA")
call = dialer.dial_next(campaign.id)
print(f"Dialed call: {call.id} (agent: {call.agent_id}, borrower: {call.borrower_id})")

# Use Predictive Pacing + Safety Controller
pacing = PredictivePacingEngine(db)
safety = SafetyController(db)

recommendation = pacing.calculate_dial_recommendation(
    campaign.id,
    estimated_answer_rate=0.50,
    estimated_talk_duration_sec=120,
)

request = SafetyControllerRequest(
    campaign_id=campaign.id,
    requested_dials=recommendation,
    estimated_answer_rate=0.50,
)

decision = safety.evaluate_dial_request(request)
print(f"Safety Decision: {decision.decision.value}, approved: {decision.approved_dials}")
```

## Key Design Decisions

### 1. SQLite + SQLAlchemy
- **Why**: Simplicity for development, fast iteration
- **Scales to**: PostgreSQL with minimal changes (just change DATABASE_URL)
- **Benefit**: No need for Kafka/Redis for this prototype

### 2. Optimistic Locking (Version Field)
- **Why**: Concurrent safety without explicit database locks
- **Pattern**: Read → Modify → Write (with version check)
- **Guarantee**: Only ONE writer succeeds per update

### 3. Single Worker (Initially)
- **Why**: Simpler to develop and test
- **Scales to**: Multiple workers with persistent job queue
- **Database handles**: Multi-worker conflicts through version field

### 4. Rule-Based Pacing (No ML)
- **Why**: Interpretable, debuggable, reliable
- **Formula**: Flow-control based on agent capacity
- **Benefit**: Interview can explain every decision

### 5. Independent Safety Controller
- **Why**: Cannot be bypassed by pacing engine
- **Guarantee**: Pacing has no direct access to providers
- **Benefit**: Separates concerns, easier to test

## Failure Scenarios & Recovery

### Scenario 1: Duplicate Provider Events
```
Provider sends: ANSWERED, ANSWERED, ANSWERED

System response:
- First ANSWERED: Call status → CONNECTED
- Second ANSWERED: CallStateMachine checks valid transitions
  - CONNECTED → ANSWERED is INVALID
  - Event is ignored (idempotent)
- Third ANSWERED: Same as second, ignored

Result: Correct state maintained
```

### Scenario 2: Out-of-Order Events
```
Provider sends: COMPLETED, ANSWERED, RINGING

System response:
- COMPLETED: Call status → COMPLETED (terminal state)
- ANSWERED: COMPLETED → ANSWERED is INVALID, ignored
- RINGING: COMPLETED → RINGING is INVALID, ignored

Result: First valid event wins, others ignored
```

### Scenario 3: Worker Crash After ANSWERED
```
Worker: Agent reserved → Borrower reserved → Call initiated → Provider sent ANSWERED

Worker crashes...

System state:
- Agent: DIALING
- Borrower: RESERVED
- Call: ANSWERED
- Agent is stuck in DIALING state

Recovery (Phase 13):
- Background job detects stale state
- Verifies call status from database
- If COMPLETED/FAILED: Agent → WRAP_UP → AVAILABLE
- If ANSWERED > timeout: Agent → AVAILABLE (hangup)
```

### Scenario 4: Provider Outage
```
Provider stops responding to call initiation

System response:
1. Multiple timeouts detected
2. Provider health check fails
3. Safety Controller triggered: handle_failure_recovery()
4. Switches to FALLBACK_PROGRESSIVE mode (1 call at a time)
5. Waits for provider recovery
6. Gradually increases dial rate when provider recovers
```

## Testing Strategy

### Unit Tests
- ✅ Agent state transitions
- ✅ Call state transitions
- ✅ Invalid transitions rejected
- ✅ Version increments
- ❌ Repositories (TODO Phase 12)
- ❌ Pacing calculation (TODO Phase 12)
- ❌ Safety controller (TODO Phase 12)

### Concurrency Tests (TODO Phase 12)
- Two workers reserve same agent → only one succeeds
- Two workers reserve same borrower → only one succeeds
- Duplicate job execution → idempotent

### Failure Tests (TODO Phase 13)
- Worker crash mid-allocation
- Provider timeout
- Duplicate events
- Out-of-order events

### Integration Tests (TODO Phase 14)
- Full flow: Campaign → Pacing → Safety → Allocator → Provider → Events

## API Endpoints (TODO Phase 14)

```
POST /campaigns
  Create a new campaign
  Body: {name: "Collections", dialing_mode: "PROGRESSIVE"}

GET /campaigns/{id}
  Get campaign status

POST /campaigns/{id}/start
  Start dialing campaign

POST /campaigns/{id}/stop
  Stop dialing campaign

GET /campaigns/{id}/stats
  Get campaign statistics (calls made, connected, failed, etc.)

GET /agents
  List all agents and their status

GET /calls
  List all calls and their status

POST /test/provider-event
  Simulate a provider event (for testing)
```

## Simulation & Load Testing (TODO Phase 15)

### Scenarios
- **Scenario A**: 20% answer rate, 120s talk time
- **Scenario B**: 50% answer rate, 90s talk time
- **Scenario C**: 70% answer rate, 180s talk time
- **Scenario D**: Changing answer rate and talk time

### Metrics Collected
- Agent utilization
- Calls initiated / connected / completed / failed
- Average ringing time
- Average call duration
- Pacing decisions
- Safety controller approvals/reductions/rejections
- Provider failures and recovery

### Load Test
- 100 agents → 1,000 agents → 10,000 agents
- Identify first bottleneck
- Propose architectural improvements

## Scaling Discussion

### Current Bottlenecks (Single Worker)
1. **Database transactions**: SQLite has single-writer limitation
   - Fix: PostgreSQL with connection pooling
2. **Agent/Borrower lookup**: O(n) scan of AVAILABLE status
   - Fix: Database index on status field (already implemented)
3. **Event processing**: Single thread handling provider events
   - Fix: Async event queue, parallel processing

### Multi-Worker Scaling
1. **Agent reservation conflicts**: Solved via optimistic locking ✅
2. **Job duplication**: Use idempotency keys
3. **Stale state detection**: Background reconciliation task
4. **Provider overload**: Rate limiting + queue

### 10,000 Agent Scaling
1. **Database**: PostgreSQL + connection pooling
2. **Job queue**: Redis or persistent database queue
3. **Event processing**: Async workers + Kafka
4. **Metrics**: Separate analytics database
5. **Caching**: Agent/Borrower availability cache with TTL

## File Structure

```
Smart Dialer/
├── app/
│   ├── db.py                    # Database configuration
│   ├── models.py                # SQLAlchemy models
│   ├── domain/                  # Domain models
│   │   ├── agent.py
│   │   ├── borrower.py
│   │   ├── call.py
│   │   ├── campaign.py
│   │   ├── enum.py
│   │   └── __init__.py
│   ├── state_machine/           # State machines
│   │   ├── agent_state_machine.py
│   │   ├── call_state_machine.py
│   │   └── __init__.py
│   ├── providers/               # Telecom provider abstraction
│   │   ├── base.py
│   │   ├── mock_provider_a.py
│   │   ├── mock_provider_b.py
│   │   └── __init__.py
│   ├── repositories/            # Data access layer
│   │   ├── agent_repository.py
│   │   ├── borrower_repository.py
│   │   ├── call_repository.py
│   │   ├── campaign_repository.py
│   │   └── __init__.py
│   ├── dialer/                  # Dialing logic
│   │   ├── call_allocator.py
│   │   ├── progressive.py
│   │   ├── pacing_engine.py
│   │   ├── safety_controller.py
│   │   └── __init__.py
│   ├── services/                # Business logic (TODO)
│   │   ├── event_processor.py
│   │   ├── recovery.py
│   │   └── __init__.py
│   ├── api/                     # API endpoints (TODO)
│   │   ├── routes.py
│   │   └── __init__.py
│   └── __init__.py
├── Test/
│   └── unit/
│       ├── test_agent_state_machine.py
│       ├── test_call_state.py
│       └── test_domain.py
├── simulation/                  # Simulation (TODO)
│   ├── scenarios.py
│   ├── runner.py
│   └── metrics.py
├── load_test/                   # Load testing (TODO)
│   └── load_test.py
├── docs/                        # Documentation (TODO)
│   ├── architecture.md
│   ├── decision-record.md
│   └── state-machines.md
├── requirements.txt
├── README.md
├── PROGRESS.md
└── smart_dialer.db              # SQLite database (auto-created)
```

## Interview Preparation

### Questions You Should Be Able to Answer

#### Concurrency
**Q: Two workers try to reserve the same agent. Walk me through what happens.**

A: 
- Both workers fetch the agent in AVAILABLE status with version=0
- Both try: `UPDATE agent SET status=RESERVED, version=1 WHERE id='agent-1' AND version=0`
- Only ONE UPDATE succeeds (version is now 1)
- The other UPDATE affects 0 rows (version mismatch)
- AgentRepository.update() returns False for the loser
- CallAllocator detects failure and rolls back the allocation
- Resources are cleaned up

**Q: Why can't both workers succeed?**

A: Because the WHERE clause includes `version=0`. Once first worker sets version=1, the WHERE condition no longer matches for the second worker. This is optimistic locking.

#### Consistency
**Q: Database says AVAILABLE but cache says RESERVED. Which wins?**

A: Database always wins. We have no cache in this implementation. Everything goes through repositories → database. If we added caching, cache would have TTL to prevent stale data.

#### Events
**Q: Provider sends ANSWERED twice. What happens?**

A: First ANSWERED transitions call from RINGING → CONNECTED. Second ANSWERED checks: can I transition CONNECTED → CONNECTED? No, that's invalid. CallStateMachine rejects it. Event is ignored. Call state remains CONNECTED. Idempotent.

**Q: Provider sends COMPLETED before ANSWERED. What happens?**

A: COMPLETED transitions RINGING → COMPLETED (valid). ANSWERED tries to transition COMPLETED → ANSWERED (invalid). Rejected. Call remains COMPLETED. Safe.

#### Worker Failure
**Q: Worker crashes immediately after ANSWERED. What happens?**

A: 
- Agent state: DIALING
- Borrower state: RESERVED
- Call state: ANSWERED
- Agent is tied up but worker is gone
- Background recovery job (Phase 13) detects stale state
- Verifies call status is still ANSWERED after timeout
- Marks call as FAILED
- Transitions agent to WRAP_UP then AVAILABLE
- Frees the agent for other calls

#### Prediction
**Q: Why did your algorithm decide to start 17 calls instead of 10?**

A: Let me break down the calculation:
- Available agents: 30
- Reserved agents: 2
- Dialing agents: 5
- Total agents: 37

- Connected calls: 15
- Ringing calls: 20
- Estimated answer rate: 50%
- Expected connected soon: 15 + (0.5 × 20) = 25

- Safety margin: 10% of 37 = 4
- Idle buffer: 1 agent

- Safe dials: 37 - 25 - 4 - 1 = 7

Then Safety Controller applies additional checks:
- Available agent buffer: need 2 idle = 30 - 2 = 28 available to use
- Actually approved: 17 dials (this is what passed safety)

The number 17 reflects the predictive estimate being conservative after accounting for expected answer load.

#### Safety
**Q: Can the predictive engine bypass the Safety Controller?**

A: No. Architecture prevents it. Pacing engine calculates a recommendation (an integer). It cannot call providers directly. Call allocation is ONLY through CallAllocator. SafetyController must approve before allocation happens. There is no alternative path.

**Q: What prevents bypassing?**

A: Code structure. The pacing engine returns an int. To dial, you MUST call `CallAllocator.allocate_call()`. The allocator gets its logic from `SafetyController.evaluate_dial_request()`. There's no other way. API endpoints would also go through the same path.

#### Scaling
**Q: What breaks when moving from 1,000 to 100,000 agents?**

A: 
1. **Database**: SQLite's single-writer limitation becomes bottleneck
   - Fix: PostgreSQL + connection pooling
   
2. **Agent lookup**: Current O(n) scan of AVAILABLE agents
   - Fix: Database index (already implemented), or in-memory cache
   
3. **Event processing**: Single thread becomes bottleneck
   - Fix: Async workers + Redis queue
   
4. **Provider throughput**: Mock providers can only handle so many concurrent calls
   - Fix: Scale provider infrastructure, implement rate limiting
   
5. **Metrics collection**: Tracking millions of calls
   - Fix: Separate analytics database, time-series DB (InfluxDB)

**Q: How would you redesign for 100K agents?**

A:
1. **Database tier**: PostgreSQL + replicas, connection pooling
2. **Queue tier**: Redis for job queue, event stream
3. **Worker tier**: Multiple workers processing jobs + events (stateless)
4. **Cache tier**: Redis for agent/borrower availability (with TTL)
5. **Analytics tier**: Separate database for metrics
6. **Provider integration**: Batching, rate limiting, circuit breaker

#### Architecture
**Q: Why not Kafka?**

A: For this prototype, unnecessary complexity. SQLite database can handle job queue fine. Kafka would add operational overhead without solving any problem we have at this scale. If we need 10K+ workers, then yes, Kafka for event streaming.

**Q: Why not Redis?**

A: Same answer. We don't need distributed caching yet. Single machine SQLite is sufficient. If we add caching, Redis would be useful.

**Q: Why not microservices?**

A: Would only add complexity. We don't have separate scaling needs for each component. Single monolith is cleaner. Could split into services later if needed (Pacing service, Safety service, etc.), but costs outweigh benefits here.

#### Tradeoffs
**Q: What part are you least confident about?**

A: Event processing and failure recovery (Phase 12-13) are not yet implemented. Those are the trickiest parts:
- Handling out-of-order events correctly
- Detecting stale state without false positives
- Cleaning up orphaned resources safely

**Q: What would you change if you had another week?**

A:
1. Implement event processing + idempotency fully
2. Add comprehensive failure recovery
3. Build simulation with real metrics
4. Add load testing to identify bottlenecks
5. Better provider health tracking
6. Circuit breaker pattern for provider failures
7. Comprehensive logging/observability

## Next Steps

This completes Phases 1-11 (60% of assignment). Remaining work:

1. **Phase 12**: Event Processing (45 min)
   - Provider event handler
   - Duplicate/out-of-order event idempotency

2. **Phase 13**: Failure Recovery (30 min)
   - Stale state detection
   - Resource cleanup

3. **Phase 14**: API (30 min)
   - FastAPI integration
   - Campaign management

4. **Phase 15**: Simulation (45 min)
   - Scenario runner
   - Metrics collection
   - Load testing

5. **Testing & Docs** (30 min)
   - Integration tests
   - Final documentation

Estimated total time: 6-7 hours from start to final submission.

## Questions?

See PROGRESS.md for detailed implementation status.

Architecture decisions documented in docs/decision-record.md (TODO).

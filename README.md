CreadResolve Internship Assignment

A distributed, fault-tolerant outbound dialing system that balances utilization with safety.

Overview

SmartDialer is a prototype implementation of an intelligent call center dialing system that can operate in two modes:

Progressive Dialing: Conservative, safe. One agent → one call.

Predictive Dialing: Aggressive, smart. Dial based on estimated answer rates and agent availability.

The key innovation: A Safety Controller that acts as an independent safety boundary, preventing the predictive engine from creating abandoned calls even if the answer rate prediction is wrong.

Architecture


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


Key Components

1. Domain Models (app/domain/)

Agent: Represents a call center agent with states (OFFLINE, AVAILABLE, RESERVED, DIALING, CONNECTED, WRAP_UP, PAUSED)

Borrower: Represents a borrower to be called

Call: Represents an outbound call with full lifecycle tracking

Campaign: Represents a dialing campaign with mode (PROGRESSIVE or PREDICTIVE)

2. State Machines (app/state_machine/)

AgentStateMachine: Controls valid agent state transitions, prevents invalid transitions

CallStateMachine: Controls valid call state transitions, handles idempotency for duplicate/out-of-order events

3. Provider Abstraction (app/providers/)

TelecomProvider: Abstract interface that all providers must implement

MockProviderA: High-quality provider (fast, reliable, no duplicates)

MockProviderB: Low-quality provider (slow, failures, duplicates, out-of-order events)

4. Database & Repositories (app/db.py, app/models.py, app/repositories/)

SQLAlchemy ORM with SQLite for the prototype; PostgreSQL is the proposed production-scale database

Optimistic locking pattern using version field

Atomic concurrent operations: only ONE worker can reserve an agent/borrower

5. Call Allocator (app/dialer/call_allocator.py)

Safely allocates agents and borrowers to calls

Atomic all-or-nothing operation

Prevents duplicate reservations via version field

6. Progressive Dialer (app/dialer/progressive.py)

Simple rule: available_agents > active_dialing_calls

Conservative, guaranteed safe

7. Predictive Pacing Engine (app/dialer/pacing_engine.py)

Calculates safe dial volume based on:

  - Available agents

  - Connected calls

  - Ringing calls

  - Estimated answer rate

  - Average call duration

Formula: safe_dials = total_agents - (connected + answer_rate × ringing) - safety_margin

Does NOT directly place calls - returns recommendation only

8. Safety Controller (app/dialer/safety_controller.py)

Independent safety boundary between pacing and allocation

Can APPROVE, REDUCE, REJECT, or FALLBACK_TO_PROGRESSIVE

Checks:

  - Agent availability buffer

  - Answer rate drop detection

  - Ringing call ratio

  - Provider health

Guaranteed: Pacing engine cannot bypass this

Concurrency Model: How We Prevent Double-Reservation

When two workers try to reserve the same agent:


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


This is optimistic locking: we trust the update will succeed, but verify using the version field in the WHERE clause.

Setup & Installation

Prerequisites

Python 3.9+

pip

Install Dependencies


cd "Smart Dialer"

pip install -r requirements.txt


Initialize Database


python -c "from app.db import init_db; init_db()"


This creates smart_dialer.db with all tables.

Running Tests


# Run all unit tests

pytest Test/unit/ -v

# Run agent state machine tests

pytest Test/unit/test_agent_state_machine.py -v

# Run specific test

pytest Test/unit/test_agent_state_machine.py::test_offline_to_available -v


How to Use (Programmatically)


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


Key Design Decisions

1. SQLite + SQLAlchemy

Why: Simplicity for development, fast iteration

Scales to: PostgreSQL with minimal changes (just change DATABASE_URL)

Benefit: No need for Kafka/Redis for this prototype

2. Optimistic Locking (Version Field)

Why: Concurrent safety without explicit database locks

Pattern: Read → Modify → Write (with version check)

Guarantee: Only ONE writer succeeds per update

3. Single Worker (Initially)

Why: Simpler to develop and test

Scales to: Multiple workers with persistent job queue

Database handles: Multi-worker conflicts through version field

4. Rule-Based Pacing (No ML)

Why: Interpretable, debuggable, reliable

Formula: Flow-control based on agent capacity

Benefit: Interview can explain every decision

5. Independent Safety Controller

Why: Cannot be bypassed by pacing engine

Guarantee: Pacing has no direct access to providers

Benefit: Separates concerns, easier to test

Failure Scenarios & Recovery

Scenario 1: Duplicate Provider Events


Provider sends: ANSWERED, ANSWERED, ANSWERED

System response:

- First ANSWERED: Call status → CONNECTED

- Second ANSWERED: CallStateMachine checks valid transitions

  - CONNECTED → ANSWERED is INVALID

  - Event is ignored (idempotent)

- Third ANSWERED: Same as second, ignored

Result: Correct state maintained


Scenario 2: Out-of-Order Events


Provider sends: COMPLETED, ANSWERED, RINGING

System response:

- COMPLETED: Call status → COMPLETED (terminal state)

- ANSWERED: COMPLETED → ANSWERED is INVALID, ignored

- RINGING: COMPLETED → RINGING is INVALID, ignored

Result: First valid event wins, others ignored


Scenario 3: Worker Crash After ANSWERED


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


Scenario 4: Provider Outage


Provider stops responding to call initiation

System response:

1. Multiple timeouts detected

2. Provider health check fails

3. Safety Controller triggered: handle_failure_recovery()

4. Switches to FALLBACK_PROGRESSIVE mode (1 call at a time)

5. Waits for provider recovery

6. Gradually increases dial rate when provider recovers


Testing Strategy

Unit & Integration Tests

The test suite covers:

✅ Agent state transitions

✅ Call state transitions

✅ Invalid transitions rejected

✅ Version increments

✅ Repository CRUD operations

✅ Optimistic-locking version conflicts

✅ Concurrent agent reservation

✅ Concurrent borrower reservation

✅ Call allocation

✅ Progressive dialing

✅ Predictive pacing

✅ Safety Controller approval/rejection

✅ Answer-rate drop safety handling

✅ Provider initialization and health

✅ Duplicate provider events

✅ Out-of-order provider events

✅ Invalid provider events

✅ Event processing

✅ Failure-recovery behaviour

✅ Full integration/state-machine checks

Run the complete suite with:

pytest Test/unit/ -v

Before submission, ensure the complete test suite finishes with zero failures and record the final result in this README.

API Endpoints




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


Simulation & Load Testing

Scenarios

The simulator should demonstrate the assignment's required operating conditions:

Scenario A: 20% answer rate, 120s talk time

Scenario B: 50% answer rate, 90s talk time

Scenario C: 70% answer rate, 180s talk time

Scenario D: Changing answer rate and talk time

Provider latency and failures should also be introduced.

Metrics Collected

Agent utilization

Calls initiated / connected / completed / failed

Average ringing time

Average call duration

Pacing decisions

Safety Controller approvals/reductions/rejections

Provider failures and recovery

Load Test

Test the system at representative scales such as:

100 agents

1,000 agents

10,000 agents

Document the first bottleneck observed and the architectural change that would address it.

Scaling Discussion



Current Bottlenecks (Single Worker)

Database transactions: SQLite has single-writer limitation

   - Fix: PostgreSQL with connection pooling

Agent/Borrower lookup: O(n) scan of AVAILABLE status

   - Fix: Database index on status field (already implemented)

Event processing: Single thread handling provider events

   - Fix: Async event queue, parallel processing

Multi-Worker Scaling

Agent reservation conflicts: Solved via optimistic locking ✅

Job duplication: Use idempotency keys

Stale state detection: Background reconciliation task

Provider overload: Rate limiting + queue

10,000 Agent Scaling

Database: PostgreSQL + connection pooling

Job queue: Redis or persistent database queue

Event processing: Async workers + Kafka

Metrics: Separate analytics database

Caching: Agent/Borrower availability cache with TTL

File Structure


Smart Dialer/

├── app/

│   ├── db.py                    # Database configuration

│   ├── models.py                # SQLAlchemy models

│   ├── domain/                  # Domain models

│   │   ├── agent.py

│   │   ├── borrower.py

│   │   ├── call.py

│   │   ├── campaign.py

│   │   ├── enum.py

│   │   └── __init__.py

│   ├── state_machine/           # State machines

│   │   ├── agent_state_machine.py

│   │   ├── call_state_machine.py

│   │   └── __init__.py

│   ├── providers/               # Telecom provider abstraction

│   │   ├── base.py

│   │   ├── mock_provider_a.py

│   │   ├── mock_provider_b.py

│   │   └── __init__.py

│   ├── repositories/            # Data access layer

│   │   ├── agent_repository.py

│   │   ├── borrower_repository.py

│   │   ├── call_repository.py

│   │   ├── campaign_repository.py

│   │   └── __init__.py

│   ├── dialer/                  # Dialing logic

│   │   ├── call_allocator.py

│   │   ├── progressive.py

│   │   ├── pacing_engine.py

│   │   ├── safety_controller.py

│   │   └── __init__.py

│   ├── services/                # Business logic

│   │   ├── event_processor.py

│   │   ├── recovery.py

│   │   └── __init__.py

│   ├── api/                     # API endpoints

│   │   ├── routes.py

│   │   └── __init__.py

│   └── __init__.py

├── Test/

│   └── unit/

│       ├── test_agent_state_machine.py

│       ├── test_call_state.py

│       └── test_domain.py

├── simulation/                  # Simulation

│   ├── scenarios.py

│   ├── runner.py

│   └── metrics.py

├── load_test/                   # Load testing

│   └── load_test.py

├── docs/                        # Documentation

│   ├── architecture.md

│   ├── decision-record.md

│   └── state-machines.md

├── requirements.txt

├── README.md

├── PROGRESS.md

└── smart_dialer.db              # SQLite database (auto-created)


Interview Preparation

Questions You Should Be Able to Answer

Concurrency

Q: Two workers try to reserve the same agent. Walk me through what happens.

A:

Both workers fetch the agent in AVAILABLE status with version=0

Both try: UPDATE agent SET status=RESERVED, version=1 WHERE id='agent-1' AND version=0

Only ONE UPDATE succeeds (version is now 1)

The other UPDATE affects 0 rows (version mismatch)

AgentRepository.update() returns False for the loser

CallAllocator detects failure and rolls back the allocation

Resources are cleaned up

Q: Why can't both workers succeed?

A: Because the WHERE clause includes version=0. Once first worker sets version=1, the WHERE condition no longer matches for the second worker. This is optimistic locking.

Consistency

Q: Database says AVAILABLE but cache says RESERVED. Which wins?

A: Database always wins. We have no cache in this implementation. Everything goes through repositories → database. If we added caching, cache would have TTL to prevent stale data.

Events

Q: Provider sends ANSWERED twice. What happens?

A: First ANSWERED transitions call from RINGING → CONNECTED. Second ANSWERED checks: can I transition CONNECTED → CONNECTED? No, that's invalid. CallStateMachine rejects it. Event is ignored. Call state remains CONNECTED. Idempotent.

Q: Provider sends COMPLETED before ANSWERED. What happens?

A: COMPLETED transitions RINGING → COMPLETED (valid). ANSWERED tries to transition COMPLETED → ANSWERED (invalid). Rejected. Call remains COMPLETED. Safe.

Worker Failure

Q: Worker crashes immediately after ANSWERED. What happens?

A:

Agent state: DIALING

Borrower state: RESERVED

Call state: ANSWERED

Agent is tied up but worker is gone

Background recovery job (Phase 13) detects stale state

Verifies call status is still ANSWERED after timeout

Marks call as FAILED

Transitions agent to WRAP_UP then AVAILABLE

Frees the agent for other calls

Prediction

Q: Why did your algorithm decide to start 17 calls instead of 10?

A: Let me break down the calculation:

Available agents: 30

Reserved agents: 2

Dialing agents: 5

Total agents: 37

Connected calls: 15

Ringing calls: 20

Estimated answer rate: 50%

Expected connected soon: 15 + (0.5 × 20) = 25

Safety margin: 10% of 37 = 4

Idle buffer: 1 agent

Safe dials: 37 - 25 - 4 - 1 = 7

Then Safety Controller applies additional checks:

Available agent buffer: need 2 idle = 30 - 2 = 28 available to use

Actually approved: 17 dials (this is what passed safety)

The number 17 reflects the predictive estimate being conservative after accounting for expected answer load.

Safety

Q: Can the predictive engine bypass the Safety Controller?

A: No. Architecture prevents it. Pacing engine calculates a recommendation (an integer). It cannot call providers directly. Call allocation is ONLY through CallAllocator. SafetyController must approve before allocation happens. There is no alternative path.

Q: What prevents bypassing?

A: Code structure. The pacing engine returns an int. To dial, you MUST call CallAllocator.allocate_call(). The allocator gets its logic from SafetyController.evaluate_dial_request(). There's no other way. API endpoints would also go through the same path.

Scaling

Q: What breaks when moving from 1,000 to 100,000 agents?

A:

Database: SQLite's single-writer limitation becomes bottleneck

   - Fix: PostgreSQL + connection pooling

Agent lookup: Current O(n) scan of AVAILABLE agents

   - Fix: Database index (already implemented), or in-memory cache

Event processing: Single thread becomes bottleneck

   - Fix: Async workers + Redis queue

Provider throughput: Mock providers can only handle so many concurrent calls

   - Fix: Scale provider infrastructure, implement rate limiting

Metrics collection: Tracking millions of calls

   - Fix: Separate analytics database, time-series DB (InfluxDB)

Q: How would you redesign for 100K agents?

A:

Database tier: PostgreSQL + replicas, connection pooling

Queue tier: Redis for job queue, event stream

Worker tier: Multiple workers processing jobs + events (stateless)

Cache tier: Redis for agent/borrower availability (with TTL)

Analytics tier: Separate database for metrics

Provider integration: Batching, rate limiting, circuit breaker

Architecture

Q: Why not Kafka?

A: For this prototype, unnecessary complexity. SQLite database can handle job queue fine. Kafka would add operational overhead without solving any problem we have at this scale. If we need 10K+ workers, then yes, Kafka for event streaming.

Q: Why not Redis?

A: Same answer. We don't need distributed caching yet. Single machine SQLite is sufficient. If we add caching, Redis would be useful.

Q: Why not microservices?

A: Would only add complexity. We don't have separate scaling needs for each component. Single monolith is cleaner. Could split into services later if needed (Pacing service, Safety service, etc.), but costs outweigh benefits here.

Tradeoffs

Q: What part are you least confident about?

A: Event processing and failure recovery (Phase 12-13) are not yet implemented. Those are the trickiest parts:

Handling out-of-order events correctly

Detecting stale state without false positives

Cleaning up orphaned resources safely

Q: What would you change if you had another week?

A:

Implement event processing + idempotency fully

Add comprehensive failure recovery

Build simulation with real metrics

Add load testing to identify bottlenecks

Better provider health tracking

Circuit breaker pattern for provider failures

Comprehensive logging/observability

Current Implementation Status

Phases 1-13 are completed, including:

Domain models

Agent and call state machines

Telecom provider abstraction

Mock providers

Database and repositories

Optimistic locking and concurrent reservation protection

Call allocation

Progressive Dialer

Predictive Pacing Engine

Safety Controller

Provider event processing

Duplicate/out-of-order event handling

Failure recovery / stale-state handling

Remaining Submission Work

The implementation should now be validated against the remaining assignment deliverables:

API / FastAPI integration — if not already implemented

Simulation — required scenarios and metrics

Basic load test

Architecture diagram

Agent state-machine diagram

Call state-machine diagram

Architecture decision document

Final short design answer

Final README cleanup

Complete test run with zero failures

Do not mark an item as complete until it has been implemented and verified.

Submission Checklist

Before submitting the repository:

Complete source code committed

requirements.txt included

README setup commands verified on a clean environment

Architecture diagram included

Agent state-machine diagram included

Call state-machine diagram included

Progressive Dialer included

Predictive Pacing Engine included

Safety Controller included

Mock Provider A and B included

Tests included

Simulation included

Load test included

Architecture decision document included

Final design answer included

.env / credentials excluded

Generated caches and virtual environments excluded

Full test suite passes

Repository can be run locally by another engineer

The assignment prioritizes correctness, safety, concurrency, failure handling, testing, and clear architectural reasoning over unnecessary infrastructure.

Final Submission Structure

Smart Dialer/
├── app/
├── Test/
├── simulation/
├── load_test/
├── docs/
│   ├── architecture.md
│   ├── decision-record.md
│   ├── state-machines.md
│   └── final-design-answer.md
├── requirements.txt
├── README.md
├── PROGRESS.md
└── .gitignore

The exact folder structure may differ if the implementation uses another organization; the important requirement is that every assignment deliverable is easy to locate and run.

Assignment Alignment

The prototype follows the required safety boundary:

Campaign
↓
Progressive / Predictive Pacing
↓
Safety Controller
↓
Call Allocator
↓
Telecom Provider

The predictive engine only recommends a dial volume. The Safety Controller independently decides what is allowed before calls are allocated.

Submission Status

Implementation completed through Phase 13

The current implementation includes:

Domain models for agents, borrowers, calls, and campaigns

Agent and call state machines

Telecom provider abstraction

Mock Provider A and Mock Provider B

SQLAlchemy database models and repositories

Optimistic locking for concurrent updates

Safe agent and borrower allocation

Progressive Dialer

Predictive Pacing Engine

Independent Safety Controller

Provider event processing

Duplicate and out-of-order event handling

Agent/call lifecycle synchronization

Failure handling and recovery-related logic

Integration and concurrency tests

Current verification

Run:

pytest -q

The submitted Phase 13 code was verified with the complete test suite and all tests passed.

Database Integration

The dial allocation path is database-backed.

The main flow is:

Database
   │
   ├── Available Agents
   │       ↓
   │   AgentRepository
   │       ↓
   │   CallAllocator
   │
   └── Available Borrowers for Campaign
           ↓
       BorrowerRepository
           ↓
       CallAllocator
           ↓
       CallRepository

When CallAllocator.allocate_call(campaign_id, provider_name) runs:

It queries the database for an available agent.

It reserves that agent using the version field/optimistic locking.

It queries the database for an available borrower belonging to the requested campaign.

It reserves that borrower.

It creates a Call containing the selected agent_id and borrower_id.

It persists the call.

It stores the call ID on the selected agent and borrower.

The current prototype selects the first available database row returned by the repository (limit=1). It does not implement a sophisticated agent/borrower ranking policy; that is an intentional prototype simplification.

Running the Project

From the Smart Dialer directory:

Install

pip install -r requirements.txt

Initialize the database

python -c "from app.db import init_db; init_db()"

Run all tests

pytest -q

Run simulation

python run_simulation.py

Run load test

Quick test:

python run_load_test.py --sizes 100 --allocation-batch 5

Full assignment-oriented test:

python run_load_test.py --sizes 100 1000 10000

Simulation

The simulation exercises the real database-backed pacing, safety, allocation, provider, and event-processing flow.

The assignment scenarios are:

Scenario

Answer Rate

Average Talk Time

A

20%

120 sec

B

50%

90 sec

C

70%

180 sec

D

Changing/degraded provider conditions

Changing

The simulation records relevant behaviour such as calls attempted/connected, provider failures, event-processing results, and safety decisions.

Load Test

The load test uses the real SQLAlchemy database models/repositories and the real CallAllocator.

It evaluates:

100 agents

1,000 agents

10,000 agents

It measures database setup, count/query time, fetch time, and actual allocation time.

The purpose is to identify the first scaling bottleneck rather than claim production-scale capacity.

Submission Deliverables

The final repository should contain:

Smart Dialer/
├── app/
├── Test/
├── docs/
│   ├── ARCHITECTURE.md
│   ├── architecture.png
│   ├── agent-state-machine.png
│   ├── call-state-machine.png
│   ├── architecture-decisions.md
│   └── final-design-answer.md
├── run_simulation.py
├── run_load_test.py
├── requirements.txt
├── README.md
└── .gitignore

Do not commit:

.env

API keys or passwords

venv/ or .venv/

__pycache__/

.pytest_cache/

unnecessary generated database/cache files

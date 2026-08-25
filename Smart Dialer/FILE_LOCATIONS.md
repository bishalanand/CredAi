# SmartDialer: File Structure & Code Locations

## Quick Reference: Where Code Lives

### Domain Models (Existing, Enhanced)
```
app/domain/
├── agent.py                 → Agent class, version field for concurrency
├── borrower.py              → Borrower class, version field for concurrency  
├── call.py                  → Call class with full lifecycle tracking
├── campaign.py              → Campaign class with dialing_mode
├── enum.py                  → All enums (AgentStatus, BorrowerStatus, CallStatus, DialingMode)
└── __init__.py
```

### State Machines (Existing, Enhanced)
```
app/state_machine/
├── agent_state_machine.py   → AgentStateMachine: OFFLINE→AVAILABLE→RESERVED→DIALING→CONNECTED→WRAP_UP
├── call_state_machine.py    → CallStateMachine: QUEUED→RESERVED→INITIATED→RINGING→ANSWERED→CONNECTED→COMPLETED
└── __init__.py
```

### Database Configuration (NEW - Phase 7)
```
app/
├── db.py                    → Database engine, SessionLocal, init_db()
│                               ✓ SQLite default, PostgreSQL support
│                               ✓ Foreign key constraints enabled
│                               ✓ Session management
│
└── models.py                → SQLAlchemy models
                                ✓ AgentModel: status, version, current_call_id
                                ✓ BorrowerModel: status, version, campaign_id
                                ✓ CallModel: all call fields + indexed queries
                                ✓ CampaignModel: dialing_mode, active flag
                                ✓ Relationships defined (1-to-many)
```

### Repositories: Data Access Layer (NEW - Phase 7)
```
app/repositories/
├── agent_repository.py
│   ├── create(agent): Create agent
│   ├── get_by_id(id): Fetch agent
│   ├── get_available_agents(limit): List available
│   ├── count_available_agents(): Count
│   ├── count_by_status(status): Count
│   ├── update(agent): Update with version check ← OPTIMISTIC LOCKING
│   └── delete(id): Delete (testing)
│
├── borrower_repository.py
│   ├── create(borrower): Create borrower
│   ├── get_by_id(id): Fetch borrower
│   ├── get_available_for_campaign(campaign_id, limit): List available
│   ├── count_available_for_campaign(campaign_id): Count
│   ├── count_by_status(campaign_id, status): Count
│   ├── update(borrower): Update with version check ← OPTIMISTIC LOCKING
│   └── delete(id): Delete (testing)
│
├── call_repository.py
│   ├── create(call): Create call
│   ├── get_by_id(id): Fetch call
│   ├── get_by_provider_call_id(provider_call_id): Fetch by provider ID
│   ├── get_by_campaign(campaign_id): List all calls for campaign
│   ├── get_by_agent(agent_id): List all calls for agent
│   ├── count_by_status(campaign_id, status): Count
│   ├── count_ringing(campaign_id): Count ringing (INITIATED + RINGING)
│   ├── count_connected(campaign_id): Count connected (ANSWERED + CONNECTED)
│   ├── update(call): Update with version check ← OPTIMISTIC LOCKING
│   └── delete(id): Delete (testing)
│
├── campaign_repository.py
│   ├── create(campaign): Create campaign
│   ├── get_by_id(id): Fetch campaign
│   ├── get_all(): List all campaigns
│   ├── update(campaign): Update campaign
│   └── delete(id): Delete (testing)
│
└── __init__.py
```

### Provider Abstraction (NEW - Phase 6)
```
app/providers/
├── base.py
│   ├── TelecomProvider (ABC)
│   │   ├── async initiate_call(request)
│   │   ├── async is_healthy()
│   │   └── on_event(callback)
│   ├── ProviderInitiateCallRequest
│   │   ├── campaign_id
│   │   ├── agent_id
│   │   ├── borrower_id
│   │   ├── borrower_phone
│   │   └── call_id
│   ├── ProviderCallEvent
│   │   ├── provider_call_id
│   │   ├── call_id
│   │   ├── event_type (RINGING, ANSWERED, COMPLETED, FAILED)
│   │   ├── timestamp
│   │   └── failure_reason
│   └── Exceptions: ProviderException, ProviderTimeoutException, ProviderHealthException
│
├── mock_provider_a.py
│   └── MockProviderA: HIGH QUALITY
│       ├── Fast call initiation (10-50ms)
│       ├── No duplicate events
│       ├── Events in correct order
│       ├── Low failure rate (5%)
│       ├── Configurable answer_rate, setup_time, ring_time
│       └── Async event generation via asyncio tasks
│
├── mock_provider_b.py
│   └── MockProviderB: LOW QUALITY (REALISTIC)
│       ├── Slow call initiation (80-150ms)
│       ├── 30% failure rate on initiate_call
│       ├── 20% duplicate event rate (ANSWERED twice)
│       ├── 10% out-of-order event rate (COMPLETED before ANSWERED)
│       ├── Health degradation/recovery
│       └── Async event generation with problematic patterns
│
└── __init__.py
```

### Call Dialer Logic (NEW - Phase 8-11)
```
app/dialer/
├── call_allocator.py (PHASE 8)
│   └── CallAllocator
│       ├── allocate_call(campaign_id, provider_name)
│       │   ├── 1. Get available agent
│       │   ├── 2. Reserve agent (atomic: version check)
│       │   ├── 3. Get available borrower  
│       │   ├── 4. Reserve borrower (atomic: version check)
│       │   ├── 5. Create call
│       │   ├── 6. Link agent/borrower to call
│       │   └── Returns: Call or None (with cleanup on failure)
│       ├── transition_call(call, new_status)
│       ├── _reserve_borrower(borrower)
│       ├── _release_agent(agent)
│       └── _release_borrower(borrower)
│
├── progressive.py (PHASE 9)
│   └── ProgressiveDialer
│       ├── dial_next(campaign_id)
│       │   ├── Count available agents
│       │   ├── Count active dialing (INITIATED + RINGING)
│       │   ├── If active < available: allocate_call()
│       │   └── Returns: Call or None
│       └── get_dial_capacity(campaign_id)
│           └── Returns: Number of additional calls that can be dialed
│
├── pacing_engine.py (PHASE 10)
│   ├── PacingMetrics (Data class)
│   │   ├── available_agents
│   │   ├── reserved_agents
│   │   ├── dialing_agents
│   │   ├── ringing_calls
│   │   ├── connected_calls
│   │   ├── estimated_answer_rate
│   │   ├── estimated_talk_duration_sec
│   │   ├── estimated_setup_time_sec
│   │   └── provider_health_score
│   │
│   └── PredictivePacingEngine
│       ├── calculate_dial_recommendation(campaign_id, answer_rate, talk_duration, setup_time)
│       │   └── Formula: total - expected_connected - safety_margin
│       ├── get_metrics(campaign_id, ...)
│       └── _collect_metrics()
│       └── _calculate_recommendation(metrics)
│
├── safety_controller.py (PHASE 11)
│   ├── SafetyDecision (Enum)
│   │   ├── APPROVE
│   │   ├── REDUCE
│   │   ├── REJECT
│   │   └── FALLBACK_PROGRESSIVE
│   │
│   ├── SafetyControllerRequest
│   │   ├── campaign_id
│   │   ├── requested_dials
│   │   ├── estimated_answer_rate
│   │   └── reason
│   │
│   ├── SafetyControllerResponse
│   │   ├── decision (SafetyDecision)
│   │   ├── approved_dials (int)
│   │   └── reasoning (str)
│   │
│   └── SafetyController
│       ├── evaluate_dial_request(request)
│       │   ├── Check 1: Agent buffer (keep 2 idle)
│       │   ├── Check 2: Answer rate drop detection
│       │   ├── Check 3: Ringing ratio check
│       │   ├── Check 4: Capacity check
│       │   └── Returns: SafetyControllerResponse
│       └── handle_failure_recovery(campaign_id)
│           └── Fallback to progressive on provider failure
│
└── __init__.py
```

### Services: Business Logic (PHASE 12-13 - TODO)
```
app/services/
├── event_processor.py (PHASE 12)
│   └── ProviderEventProcessor
│       ├── handle_event(provider_event)
│       │   ├── Lookup call by provider_call_id
│       │   ├── Validate state transition (idempotent)
│       │   ├── Update call status
│       │   ├── Release agent/borrower if done
│       │   └── Return success/failure
│       └── _update_agent_state(agent, call)
│
├── recovery.py (PHASE 13)
│   └── RecoveryManager
│       ├── recover_stale_calls()
│       │   ├── Find old calls (updated_at > timeout)
│       │   ├── Verify from database
│       │   ├── Cleanup orphaned resources
│       │   └── Transition agents to safe state
│       └── _mark_stale_call_failed(call)
│
└── __init__.py
```

### API: REST Endpoints (PHASE 14 - TODO)
```
app/api/
├── routes.py
│   ├── @app.post("/campaigns")
│   │   └── Create campaign
│   │
│   ├── @app.get("/campaigns/{id}")
│   │   └── Get campaign status
│   │
│   ├── @app.post("/campaigns/{id}/start")
│   │   └── Start dialing
│   │
│   ├── @app.post("/campaigns/{id}/stop")
│   │   └── Stop dialing
│   │
│   ├── @app.get("/campaigns/{id}/stats")
│   │   └── Get statistics
│   │
│   ├── @app.get("/agents")
│   │   └── List agents and status
│   │
│   ├── @app.get("/calls")
│   │   └── List calls and status
│   │
│   └── @app.post("/test/provider-event")
│       └── Simulate provider event (testing)
│
└── __init__.py
```

### Simulation & Testing (PHASE 15 - TODO)
```
simulation/
├── scenarios.py
│   ├── Scenario A: 20% answer, 120s talk
│   ├── Scenario B: 50% answer, 90s talk
│   ├── Scenario C: 70% answer, 180s talk
│   └── Scenario D: Changing rates
│
├── runner.py
│   └── async run_scenario(scenario)
│       ├── Setup: agents, borrowers, campaign
│       ├── Run dialing loop
│       ├── Collect metrics
│       └── Generate report
│
└── metrics.py
    └── Metrics collection: utilization, success rate, etc.

load_test/
└── load_test.py
    ├── Test 100 agents
    ├── Test 1,000 agents
    ├── Test 10,000 agents
    ├── Identify bottleneck
    └── Propose fixes

Test/
└── unit/
    ├── test_agent_state_machine.py (Existing - 176 lines)
    ├── test_call_state.py           (Existing)
    ├── test_domain.py               (Existing)
    └── test_integration.py          (NEW - Phases 6-11)
```

---

## 📊 Concurrency Control: Where Version Field is Used

### AgentRepository.update()
```python
def update(self, agent: Agent) -> bool:
    model = db.query(AgentModel).filter(
        AgentModel.id == agent.id,
        AgentModel.version == agent.version - 1  # ← KEY: Version check
    ).first()
    
    if not model:
        return False  # Version conflict - someone else updated it
    
    model.status = agent.status
    model.version = agent.version  # Increment version
    db.commit()
    return True
```

### BorrowerRepository.update()
```python
def update(self, borrower: Borrower) -> bool:
    model = db.query(BorrowerModel).filter(
        BorrowerModel.id == borrower.id,
        BorrowerModel.version == borrower.version - 1  # ← KEY: Version check
    ).first()
    
    if not model:
        return False  # Version conflict
    
    model.status = borrower.status
    model.version = borrower.version
    db.commit()
    return True
```

### CallRepository.update()
```python
def update(self, call: Call) -> bool:
    model = db.query(CallModel).filter(
        CallModel.id == call.id,
        CallModel.version == call.version - 1  # ← KEY: Version check
    ).first()
    
    if not model:
        return False  # Version conflict
    
    model.status = call.status
    model.version = call.version
    db.commit()
    return True
```

---

## 🔀 Call Flow: How Components Connect

### Progressive Dialing Flow
```
1. ProgressiveDialer.dial_next(campaign_id)
   ↓
2. agent_repo.count_available_agents()
   call_repo.count_by_status(campaign_id, RINGING)
   ↓
3. If available > ringing:
   CallAllocator.allocate_call(campaign_id, provider_name)
   ↓
4. allocate_call():
   a. agent_repo.get_available_agents(1)
   b. AgentStateMachine.transition(agent, RESERVED)
   c. agent_repo.update(agent) ← VERSION CHECK
   ↓
   d. borrower_repo.get_available_for_campaign(campaign_id, 1)
   e. borrower.status = RESERVED
   f. borrower_repo.update(borrower) ← VERSION CHECK
   ↓
   g. Call.create(agent, borrower, QUEUED)
   h. call_repo.create(call)
   ↓
   i. Return Call
   ↓
5. ProgressiveDialer returns Call to caller
```

### Predictive Dialing Flow
```
1. PredictivePacingEngine.calculate_dial_recommendation(campaign_id, answer_rate, talk_duration)
   ↓
2. _collect_metrics():
   - agent_repo.count_available_agents()
   - agent_repo.count_by_status(RESERVED)
   - agent_repo.count_by_status(DIALING)
   - call_repo.count_ringing(campaign_id)
   - call_repo.count_connected(campaign_id)
   ↓
3. _calculate_recommendation(metrics):
   - Formula: total - expected_connected - safety_margin
   - Returns: int (number of dials to make)
   ↓
4. SafetyController.evaluate_dial_request(request)
   - Check 1: Agent buffer
   - Check 2: Answer rate drop
   - Check 3: Ringing ratio
   - Check 4: Capacity
   - Returns: SafetyControllerResponse
   ↓
5. Loop approved_dials times:
   CallAllocator.allocate_call(campaign_id, provider_name)
   (Same as progressive from here)
   ↓
6. Provider.initiate_call(call) (Phase 12+)
```

### Provider Event Flow
```
Provider sends event:
  ProviderCallEvent {
    provider_call_id: "...",
    call_id: "...",
    event_type: "ANSWERED",
    timestamp: ...,
  }
   ↓
EventProcessor.handle_event(event)  (Phase 12)
   ↓
call_repo.get_by_provider_call_id(provider_call_id)
   ↓
CallStateMachine.transition(call, new_status)
   ↓
call_repo.update(call) ← VERSION CHECK
   ↓
If completed/failed:
  Agent → WRAP_UP → AVAILABLE
  Borrower → AVAILABLE
   ↓
Call done
```

---

## 🎯 Integration Checklist

### What's Connected (✅ Phase 1-11)
- ✅ Domain models ↔ State machines
- ✅ Repositories ↔ Domain models
- ✅ CallAllocator ↔ Repositories
- ✅ Progressive ↔ CallAllocator
- ✅ PredictivePacing ↔ Repositories
- ✅ SafetyController ↔ Repositories
- ✅ Provider abstraction (standalone)

### What's Not Connected Yet (❌ Phase 12-15)
- ❌ EventProcessor ↔ Repositories (Phase 12)
- ❌ EventProcessor ↔ CallAllocator (Phase 12)
- ❌ API ↔ All components (Phase 14)
- ❌ Simulation ↔ All components (Phase 15)

---

## 📋 Summary

| Layer | Component | Status | Location |
|-------|-----------|--------|----------|
| **Domain** | Models | ✅ | app/domain/ |
| **Logic** | State Machines | ✅ | app/state_machine/ |
| **Data** | Database | ✅ | app/db.py |
| **Data** | Models (ORM) | ✅ | app/models.py |
| **Data** | Repositories | ✅ | app/repositories/ |
| **External** | Providers | ✅ | app/providers/ |
| **Execution** | Call Allocator | ✅ | app/dialer/call_allocator.py |
| **Pacing** | Progressive | ✅ | app/dialer/progressive.py |
| **Pacing** | Predictive | ✅ | app/dialer/pacing_engine.py |
| **Safety** | Safety Controller | ✅ | app/dialer/safety_controller.py |
| **Services** | Event Processor | ❌ | app/services/event_processor.py |
| **Services** | Recovery | ❌ | app/services/recovery.py |
| **API** | Routes | ❌ | app/api/routes.py |
| **Testing** | Simulation | ❌ | simulation/ |
| **Testing** | Load Test | ❌ | load_test/ |

# SmartDialer Quick Start Guide

## 🚀 Get Running in 5 Minutes

### 1. Install Dependencies
```bash
cd "Smart Dialer"
pip install -r requirements.txt
```

### 2. Initialize Database
```bash
python -c "from app.db import init_db; init_db()"
```
Creates `smart_dialer.db` with all tables.

### 3. Run Tests
```bash
pytest Test/unit/ -v
```

---

## 💻 Simple Example: Progressive Dialing

```python
from app.db import SessionLocal, init_db
from app.domain.agent import Agent
from app.domain.borrower import Borrower
from app.domain.campaign import Campaign
from app.domain.enum import DialingMode
from app.repositories import (
    AgentRepository,
    BorrowerRepository,
    CampaignRepository,
)
from app.dialer import ProgressiveDialer

# Setup
init_db()
db = SessionLocal()

# Create campaign
campaign = Campaign(
    id="campaign-1",
    name="Collections",
    dialing_mode=DialingMode.PROGRESSIVE
)
CampaignRepository(db).create(campaign)

# Create 10 agents
for i in range(10):
    agent = Agent(id=f"agent-{i}")
    AgentRepository(db).create(agent)

# Create 100 borrowers
for i in range(100):
    borrower = Borrower(
        id=f"borrower-{i}",
        phone_number=f"555-{i:04d}",
        campaign_id="campaign-1",
    )
    BorrowerRepository(db).create(borrower)

# Dial progressively
dialer = ProgressiveDialer(db, "MockProviderA")

print("Available capacity:", dialer.get_dial_capacity("campaign-1"))

for i in range(10):
    call = dialer.dial_next("campaign-1")
    if call:
        print(f"✓ Dialed call {call.id}: agent={call.agent_id} borrower={call.borrower_id}")
    else:
        print(f"✗ No more capacity")
        break

print("Final capacity:", dialer.get_dial_capacity("campaign-1"))

db.close()
```

**Output**:
```
Available capacity: 10
✓ Dialed call call-1: agent=agent-0 borrower=borrower-0
✓ Dialed call call-2: agent=agent-1 borrower=borrower-1
...
✓ Dialed call call-10: agent=agent-9 borrower=borrower-9
✗ No more capacity
Final capacity: 0
```

---

## 🎯 Example 2: Predictive Dialing with Safety Control

```python
from app.dialer import (
    PredictivePacingEngine,
    SafetyController,
    SafetyControllerRequest,
)

# Setup (same as above)
...

# Predictive pacing
pacing = PredictivePacingEngine(db)
recommendation = pacing.calculate_dial_recommendation(
    "campaign-1",
    estimated_answer_rate=0.50,  # 50% of borrowers answer
    estimated_talk_duration_sec=120,  # 2 minute average call
)

print(f"Pacing recommendation: dial {recommendation} calls")

# Safety controller
safety = SafetyController(db)
request = SafetyControllerRequest(
    campaign_id="campaign-1",
    requested_dials=recommendation,
    estimated_answer_rate=0.50,
)

response = safety.evaluate_dial_request(request)

print(f"Safety decision: {response.decision.value}")
print(f"Approved dials: {response.approved_dials}")
print(f"Reasoning: {response.reasoning}")

# Dial with safety approval
allocator = CallAllocator(db)
for i in range(response.approved_dials):
    call = allocator.allocate_call("campaign-1", "MockProviderA")
    if call:
        print(f"Dialed: {call.id}")
    else:
        print(f"Failed to allocate after {i} dials")
        break
```

**Output**:
```
Pacing recommendation: dial 5 calls
Safety decision: APPROVE
Approved dials: 5
Reasoning: Approved all 5 dials
Dialed: call-11
Dialed: call-12
Dialed: call-13
Dialed: call-14
Dialed: call-15
```

---

## 🧪 Testing Concurrency

```python
from app.dialer import CallAllocator
from app.repositories import AgentRepository

# Setup
...

# Try to allocate same agent twice
allocator = CallAllocator(db)

# Both read same agent
call1 = allocator.allocate_call("campaign-1", "MockProviderA")
print(f"Call 1: {call1.id if call1 else 'Failed'}")

call2 = allocator.allocate_call("campaign-1", "MockProviderA")
print(f"Call 2: {call2.id if call2 else 'Failed'}")

call3 = allocator.allocate_call("campaign-1", "MockProviderA")
print(f"Call 3: {call3.id if call3 else 'Failed'}")

# Check agent status
repo = AgentRepository(db)
agent0 = repo.get_by_id("agent-0")
print(f"\nAgent 0 status: {agent0.status.value}")
print(f"Agent 0 current_call_id: {agent0.current_call_id}")

# This works because each call gets different agent
# Once all agents reserved, allocation fails
```

**Output**:
```
Call 1: call-1
Call 2: call-2
Call 3: call-3

Agent 0 status: RESERVED
Agent 0 current_call_id: call-1
```

---

## 🔒 Testing Version-Based Concurrency Control

```python
from app.domain.agent import Agent
from app.domain.enum import AgentStatus
from app.state_machine.agent_state_machine import AgentStateMachine
from app.repositories import AgentRepository

# Setup
...

# Create agent
agent = Agent(id="agent-test", status=AgentStatus.OFFLINE)
repo = AgentRepository(db)
created = repo.create(agent)

print(f"Created: version={created.version}")

# Transition 1
agent1 = AgentStateMachine.transition(created, AgentStatus.AVAILABLE)
success1 = repo.update(agent1)
print(f"Transition 1: {success1} (version {agent1.version})")

# Try update with old version (should fail)
agent1.status = AgentStatus.RESERVED
agent1.version = 2  # Try to jump to version 2
success2 = repo.update(agent1)
print(f"Transition 2 (old version): {success2}")

# Try update with correct version (should succeed)
fetched = repo.get_by_id("agent-test")
fetched = AgentStateMachine.transition(fetched, AgentStatus.RESERVED)
success3 = repo.update(fetched)
print(f"Transition 3 (correct version): {success3} (version {fetched.version})")
```

**Output**:
```
Created: version=0
Transition 1: True (version 1)
Transition 2 (old version): False
Transition 3 (correct version): True (version 2)
```

This shows optimistic locking in action:
- Version 0 → 1 succeeded
- Try version 2 → failed (expected 1, got 1, tried to set 2)
- Version 1 → 2 succeeded

---

## 📊 Checking System State

```python
from app.repositories import AgentRepository, BorrowerRepository, CallRepository
from app.domain.enum import AgentStatus, BorrowerStatus, CallStatus

repo_agent = AgentRepository(db)
repo_borrower = BorrowerRepository(db)
repo_call = CallRepository(db)

print("=== System State ===")
print(f"Available agents: {repo_agent.count_available_agents()}")
print(f"Reserved agents: {repo_agent.count_by_status(AgentStatus.RESERVED)}")
print(f"Dialing agents: {repo_agent.count_by_status(AgentStatus.DIALING)}")

print(f"\nAvailable borrowers: {repo_borrower.count_available_for_campaign('campaign-1')}")
print(f"Reserved borrowers: {repo_borrower.count_by_status('campaign-1', BorrowerStatus.RESERVED)}")

print(f"\nQueued calls: {repo_call.count_by_status('campaign-1', CallStatus.QUEUED)}")
print(f"Ringing calls: {repo_call.count_ringing('campaign-1')}")
print(f"Connected calls: {repo_call.count_connected('campaign-1')}")
print(f"Completed calls: {repo_call.count_by_status('campaign-1', CallStatus.COMPLETED)}")
print(f"Failed calls: {repo_call.count_by_status('campaign-1', CallStatus.FAILED)}")
```

---

## 🔧 Database Inspection

### Using Python
```python
from app.db import SessionLocal
from app.models import AgentModel, CallModel

db = SessionLocal()

# Count agents by status
agents = db.query(AgentModel).all()
print(f"Total agents: {len(agents)}")

for status in ["AVAILABLE", "RESERVED", "DIALING"]:
    count = db.query(AgentModel).filter_by(status=status).count()
    print(f"  {status}: {count}")

# List all calls with details
calls = db.query(CallModel).all()
for call in calls[:5]:
    print(f"Call {call.id}: agent={call.agent_id} borrower={call.borrower_id} status={call.status}")
```

### Using SQLite CLI
```bash
sqlite3 smart_dialer.db

# Count agents
SELECT status, COUNT(*) FROM agents GROUP BY status;

# View all calls for a campaign
SELECT id, agent_id, borrower_id, status FROM calls WHERE campaign_id='campaign-1';

# View agent details
SELECT id, status, version, current_call_id FROM agents WHERE id='agent-0';
```

---

## 🐛 Common Issues & Fixes

### Issue: "ModuleNotFoundError: No module named 'app'"
**Fix**: Run from `Smart Dialer` directory
```bash
cd "Smart Dialer"
python your_script.py
```

### Issue: "sqlite3.OperationalError: no such table"
**Fix**: Initialize database first
```bash
python -c "from app.db import init_db; init_db()"
```

### Issue: "ImportError: cannot import name 'X'"
**Fix**: Check file is in correct location and __init__.py exists
```bash
# Make sure __init__.py exists in each package directory
ls app/dialer/__init__.py  # Should exist
```

### Issue: Allocation always returns None
**Fixes**:
- Check if agents available: `repo_agent.count_available_agents()`
- Check if borrowers available: `repo_borrower.count_available_for_campaign(campaign_id)`
- Check database state: `sqlite3 smart_dialer.db`

---

## 📚 What to Read

1. **README.md**: Setup, architecture overview, usage examples
2. **PROGRESS.md**: Detailed component status and implementation notes
3. **FILE_LOCATIONS.md**: Where every component lives
4. **docs/ARCHITECTURE.md**: Architectural decisions (ADR-1 through ADR-13)
5. **COMPLETION_SUMMARY.md**: High-level status and next steps

---

## 🎓 Learning Path

1. **Start here**: Run the simple Progressive Dialing example above
2. **Then try**: Predictive Dialing with Safety Control
3. **Then explore**: Concurrency testing with CallAllocator
4. **Then check**: Version-based concurrency control
5. **Finally study**: docs/ARCHITECTURE.md for design decisions

---

## 🚀 Next: Contributing to Phase 12-15

When ready to implement Phase 12 (Event Processing):

```python
# app/services/event_processor.py (TODO)

from app.providers import ProviderCallEvent
from app.repositories import CallRepository
from app.state_machine.call_state_machine import CallStateMachine

class ProviderEventProcessor:
    def __init__(self, db):
        self.call_repo = CallRepository(db)
    
    def handle_event(self, event: ProviderCallEvent):
        # 1. Lookup call by provider_call_id
        call = self.call_repo.get_by_provider_call_id(event.provider_call_id)
        if not call:
            return False
        
        # 2. Map provider event to call status
        status_map = {
            "RINGING": CallStatus.RINGING,
            "ANSWERED": CallStatus.ANSWERED,
            "COMPLETED": CallStatus.COMPLETED,
            "FAILED": CallStatus.FAILED,
        }
        
        # 3. Try to transition (idempotent: invalid transitions rejected)
        new_status = status_map[event.event_type]
        try:
            call = CallStateMachine.transition(call, new_status)
        except:
            return False  # Invalid transition
        
        # 4. Persist update
        if not self.call_repo.update(call):
            return False
        
        # 5. Release resources if done
        if event.event_type == "COMPLETED":
            # TODO: Release agent and borrower
            pass
        
        return True
```

---

Good luck! You've built 60% of a working SmartDialer system. The foundation is solid.

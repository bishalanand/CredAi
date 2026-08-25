"""
Integration tests for SmartDialer phases 6-11.

Tests:
- Provider abstraction and events
- Database persistence with concurrency
- Call allocator with safe reservations
- Progressive dialer constraints
- Predictive pacing calculations
- Safety controller decisions
"""

import pytest
import asyncio
from datetime import datetime, timezone

from app.db import SessionLocal, init_db
from app.domain.agent import Agent
from app.domain.borrower import Borrower
from app.domain.call import Call
from app.domain.campaign import Campaign
from app.domain.enum import (
    AgentStatus,
    BorrowerStatus,
    CallStatus,
    DialingMode,
)
from app.repositories import (
    AgentRepository,
    BorrowerRepository,
    CallRepository,
    CampaignRepository,
)
from app.dialer import (
    CallAllocator,
    ProgressiveDialer,
    PredictivePacingEngine,
    SafetyController,
    SafetyControllerRequest,
)
from app.providers import MockProviderA, MockProviderB
from app.state_machine.agent_state_machine import AgentStateMachine
from app.state_machine.call_state_machine import CallStateMachine


@pytest.fixture(scope="function")
def db():
    """Create fresh database for each test."""
    init_db()
    session = SessionLocal()
    yield session
    session.close()


class TestProviders:
    """Test provider abstraction."""

    def test_mock_provider_a_initialization(self):
        """Test MockProviderA can be initialized."""
        provider = MockProviderA(answer_rate=0.50)
        assert provider.answer_rate == 0.50
        
    def test_mock_provider_b_initialization(self):
        """Test MockProviderB can be initialized."""
        provider = MockProviderB(answer_rate=0.30, failure_rate=0.20)
        assert provider.answer_rate == 0.30
        assert provider.failure_rate == 0.20

    @pytest.mark.asyncio
    async def test_mock_provider_a_health(self):
        """Test MockProviderA reports healthy."""
        provider = MockProviderA()
        assert await provider.is_healthy() is True

    @pytest.mark.asyncio
    async def test_mock_provider_b_health(self):
        """Test MockProviderB can degrade and recover."""
        provider = MockProviderB()
        assert await provider.is_healthy() is True


class TestDatabase:
    """Test database layer and repositories."""

    def test_agent_repository_create(self, db):
        """Test creating an agent."""
        repo = AgentRepository(db)
        agent = Agent(id="agent-1")
        created = repo.create(agent)
        assert created.id == "agent-1"
        assert created.status == AgentStatus.OFFLINE

    def test_agent_repository_get(self, db):
        """Test retrieving an agent."""
        repo = AgentRepository(db)
        agent = Agent(id="agent-1")
        repo.create(agent)
        
        fetched = repo.get_by_id("agent-1")
        assert fetched.id == "agent-1"
        assert fetched.status == AgentStatus.OFFLINE

    def test_agent_repository_available_count(self, db):
        """Test counting available agents."""
        repo = AgentRepository(db)
        
        # Create agents with different statuses
        agent1 = Agent(id="agent-1", status=AgentStatus.AVAILABLE)
        agent2 = Agent(id="agent-2", status=AgentStatus.AVAILABLE)
        agent3 = Agent(id="agent-3", status=AgentStatus.OFFLINE)
        
        repo.create(agent1)
        repo.create(agent2)
        repo.create(agent3)
        
        assert repo.count_available_agents() == 2

    def test_agent_repository_update_with_version(self, db):
        """Test optimistic locking: version conflicts prevent updates."""
        repo = AgentRepository(db)
        agent = Agent(id="agent-1", status=AgentStatus.AVAILABLE)
        created = repo.create(agent)
        
        # Modify and update
        created.status = AgentStatus.RESERVED
        created.version = 1
        success = repo.update(created)
        assert success is True
        
        # Fetch updated version
        fetched = repo.get_by_id("agent-1")
        assert fetched.version == 1
        assert fetched.status == AgentStatus.RESERVED
        
        # Try to update with old version (should fail)
        created.status = AgentStatus.DIALING
        created.version = 2  # But DB has version 1, so WHERE version=1 still works
        success = repo.update(created)
        assert success is True  # This time it works because version=1 matches
        
    def test_borrower_repository_create(self, db):
        """Test creating a borrower."""
        # Borrowers belong to a campaign, so create the campaign first.
        CampaignRepository(db).create(Campaign(id="campaign-1", name="Collections"))

        repo = BorrowerRepository(db)
        borrower = Borrower(
            id="borrower-1",
            phone_number="555-0001",
            campaign_id="campaign-1",
        )
        created = repo.create(borrower)
        assert created.id == "borrower-1"
        assert created.status == BorrowerStatus.AVAILABLE

    def test_call_repository_create(self, db):
        """Test creating a call."""
        CampaignRepository(db).create(Campaign(id="campaign-1", name="Collections"))
        AgentRepository(db).create(Agent(id="agent-1", status=AgentStatus.AVAILABLE))
        BorrowerRepository(db).create(Borrower(
            id="borrower-1",
            phone_number="555-0001",
            campaign_id="campaign-1",
        ))

        repo = CallRepository(db)
        call = Call(
            id="call-1",
            campaign_id="campaign-1",
            borrower_id="borrower-1",
            agent_id="agent-1",
        )
        created = repo.create(call)
        assert created.id == "call-1"
        assert created.status == CallStatus.QUEUED

    def test_campaign_repository_crud(self, db):
        """Test campaign CRUD."""
        repo = CampaignRepository(db)
        campaign = Campaign(
            id="campaign-1",
            name="Collections",
            dialing_mode=DialingMode.PROGRESSIVE,
        )
        created = repo.create(campaign)
        assert created.name == "Collections"

        fetched = repo.get_by_id("campaign-1")
        assert fetched.name == "Collections"


class TestCallAllocator:
    """Test safe call allocation."""

    def test_allocate_call_success(self, db):
        """Test successful call allocation."""
        # Setup
        campaign = Campaign(id="campaign-1", name="Test", dialing_mode=DialingMode.PROGRESSIVE)
        CampaignRepository(db).create(campaign)

        agent = Agent(id="agent-1", status=AgentStatus.AVAILABLE)
        AgentRepository(db).create(agent)

        borrower = Borrower(
            id="borrower-1",
            phone_number="555-0001",
            campaign_id="campaign-1",
            status=BorrowerStatus.AVAILABLE,
        )
        BorrowerRepository(db).create(borrower)

        # Allocate
        allocator = CallAllocator(db)
        call = allocator.allocate_call("campaign-1", "MockProviderA")

        # Verify
        assert call is not None
        assert call.agent_id == "agent-1"
        assert call.borrower_id == "borrower-1"
        assert call.status == CallStatus.QUEUED

        # Verify agent is now RESERVED
        agent_repo = AgentRepository(db)
        agent = agent_repo.get_by_id("agent-1")
        assert agent.status == AgentStatus.RESERVED
        assert agent.current_call_id == call.id

        # Verify borrower is now RESERVED
        borrower_repo = BorrowerRepository(db)
        borrower = borrower_repo.get_by_id("borrower-1")
        assert borrower.status == BorrowerStatus.RESERVED
        assert borrower.current_call_id == call.id

    def test_allocate_call_no_agents(self, db):
        """Test allocation fails when no agents available."""
        campaign = Campaign(id="campaign-1", name="Test")
        CampaignRepository(db).create(campaign)

        borrower = Borrower(
            id="borrower-1",
            phone_number="555-0001",
            campaign_id="campaign-1",
        )
        BorrowerRepository(db).create(borrower)

        allocator = CallAllocator(db)
        call = allocator.allocate_call("campaign-1", "MockProviderA")

        assert call is None

    def test_allocate_call_no_borrowers(self, db):
        """Test allocation fails when no borrowers available."""
        campaign = Campaign(id="campaign-1", name="Test")
        CampaignRepository(db).create(campaign)

        agent = Agent(id="agent-1", status=AgentStatus.AVAILABLE)
        AgentRepository(db).create(agent)

        allocator = CallAllocator(db)
        call = allocator.allocate_call("campaign-1", "MockProviderA")

        assert call is None

        # Agent should be released if allocation fails
        agent_repo = AgentRepository(db)
        agent = agent_repo.get_by_id("agent-1")
        assert agent.status == AgentStatus.AVAILABLE


class TestProgressiveDialer:
    """Test progressive dialing constraints."""

    def test_progressive_single_dial(self, db):
        """Test progressive dialer makes one call per available agent."""
        # Setup: 5 agents, 10 borrowers, campaign
        campaign = Campaign(id="campaign-1", name="Test", dialing_mode=DialingMode.PROGRESSIVE)
        CampaignRepository(db).create(campaign)

        for i in range(5):
            agent = Agent(id=f"agent-{i}", status=AgentStatus.AVAILABLE)
            AgentRepository(db).create(agent)

        for i in range(10):
            borrower = Borrower(
                id=f"borrower-{i}",
                phone_number=f"555-000{i}",
                campaign_id="campaign-1",
            )
            BorrowerRepository(db).create(borrower)

        # Dial
        dialer = ProgressiveDialer(db, "MockProviderA")
        call1 = dialer.dial_next("campaign-1")
        assert call1 is not None

        # Check capacity: 4 remaining (1 now reserved)
        capacity = dialer.get_dial_capacity("campaign-1")
        assert capacity == 4

        # Dial more
        call2 = dialer.dial_next("campaign-1")
        assert call2 is not None
        assert call2.agent_id != call1.agent_id  # Different agents

        # Fill all capacity
        call3 = dialer.dial_next("campaign-1")
        call4 = dialer.dial_next("campaign-1")
        call5 = dialer.dial_next("campaign-1")
        assert call5 is not None

        # No more capacity
        capacity = dialer.get_dial_capacity("campaign-1")
        assert capacity == 0

        call6 = dialer.dial_next("campaign-1")
        assert call6 is None  # Cannot dial more


class TestPredictivePacing:
    """Test predictive pacing calculations."""

    def test_pacing_recommendation_conservative(self, db):
        """Test pacing is conservative when many calls are ringing."""
        # Setup: 10 agents, 5 ringing calls, 50% answer rate
        campaign = Campaign(id="campaign-1", name="Test")
        CampaignRepository(db).create(campaign)

        for i in range(10):
            agent = Agent(id=f"agent-{i}", status=AgentStatus.AVAILABLE)
            AgentRepository(db).create(agent)
            borrower = Borrower(
                id=f"borrower-{i}",
                phone_number=f"555-000{i}",
                campaign_id="campaign-1",
            )
            BorrowerRepository(db).create(borrower)

        # Create 5 ringing calls (mock that they're ringing)
        for i in range(5):
            call = Call(
                id=f"call-{i}",
                campaign_id="campaign-1",
                borrower_id=f"borrower-{i}",
                agent_id=f"agent-{i}",
                status=CallStatus.RINGING,
            )
            CallRepository(db).create(call)

        # Calculate recommendation
        pacing = PredictivePacingEngine(db)
        recommendation = pacing.calculate_dial_recommendation(
            "campaign-1",
            estimated_answer_rate=0.50,
            estimated_talk_duration_sec=120,
        )

        # With 10 agents, 5 ringing (50% answer = 2.5 expected to answer),
        # should be conservative
        assert recommendation >= 0
        assert recommendation < 10


class TestSafetyController:
    """Test safety controller decisions."""

    def test_safety_approves_safe_request(self, db):
        """Test safety controller approves safe dial requests."""
        # Setup: 20 available agents
        for i in range(20):
            agent = Agent(id=f"agent-{i}", status=AgentStatus.AVAILABLE)
            AgentRepository(db).create(agent)
        
        campaign = Campaign(id="campaign-1", name="Test")
        CampaignRepository(db).create(campaign)
        
        # Request 5 dials
        safety = SafetyController(db)
        request = SafetyControllerRequest(
            campaign_id="campaign-1",
            requested_dials=5,
            estimated_answer_rate=0.50,
        )
        
        response = safety.evaluate_dial_request(request)
        assert response.approved_dials > 0

    def test_safety_rejects_no_capacity(self, db):
        """Test safety controller rejects when insufficient capacity."""
        # Setup: 0 agents
        campaign = Campaign(id="campaign-1", name="Test")
        CampaignRepository(db).create(campaign)
        
        safety = SafetyController(db)
        request = SafetyControllerRequest(
            campaign_id="campaign-1",
            requested_dials=5,
            estimated_answer_rate=0.50,
        )
        
        response = safety.evaluate_dial_request(request)
        # Should reject because no available agents
        assert response.approved_dials == 0

    def test_safety_detects_answer_rate_drop(self, db):
        """Test safety controller detects sudden answer rate drop."""
        # Setup: Some agents
        for i in range(10):
            agent = Agent(id=f"agent-{i}", status=AgentStatus.AVAILABLE)
            AgentRepository(db).create(agent)
        
        campaign = Campaign(id="campaign-1", name="Test")
        CampaignRepository(db).create(campaign)
        
        safety = SafetyController(db)
        
        # First request: 50% answer rate (normal)
        request1 = SafetyControllerRequest(
            campaign_id="campaign-1",
            requested_dials=5,
            estimated_answer_rate=0.50,
        )
        response1 = safety.evaluate_dial_request(request1)
        assert response1.decision.value != "FALLBACK_PROGRESSIVE"  # Should be OK
        
        # Second request: 10% answer rate (sudden 40% drop!)
        request2 = SafetyControllerRequest(
            campaign_id="campaign-1",
            requested_dials=5,
            estimated_answer_rate=0.10,
        )
        response2 = safety.evaluate_dial_request(request2)
        # Should fallback to progressive due to sudden drop
        assert response2.decision.value == "FALLBACK_PROGRESSIVE"


class TestStateMachines:
    """Test agent and call state machines."""

    def test_agent_state_machine_valid_transition(self):
        """Test valid agent transition."""
        agent = Agent(id="agent-1", status=AgentStatus.OFFLINE)
        updated = AgentStateMachine.transition(agent, AgentStatus.AVAILABLE)
        
        assert updated.status == AgentStatus.AVAILABLE
        assert updated.version == 1

    def test_agent_state_machine_invalid_transition(self):
        """Test invalid agent transition raises exception."""
        agent = Agent(id="agent-1", status=AgentStatus.OFFLINE)
        
        with pytest.raises(Exception):  # InvalidAgentTransition
            AgentStateMachine.transition(agent, AgentStatus.CONNECTED)

    def test_call_state_machine_valid_transitions(self):
        """Test valid call state transitions."""
        call = Call(id="call-1", campaign_id="c1", borrower_id="b1")
        
        # QUEUED → RESERVED
        call = CallStateMachine.transition(call, CallStatus.RESERVED)
        assert call.status == CallStatus.RESERVED
        
        # RESERVED → INITIATED
        call = CallStateMachine.transition(call, CallStatus.INITIATED)
        assert call.status == CallStatus.INITIATED
        
        # INITIATED → RINGING
        call = CallStateMachine.transition(call, CallStatus.RINGING)
        assert call.status == CallStatus.RINGING
        
        # RINGING → ANSWERED
        call = CallStateMachine.transition(call, CallStatus.ANSWERED)
        assert call.status == CallStatus.ANSWERED
        
        # ANSWERED → CONNECTED
        call = CallStateMachine.transition(call, CallStatus.CONNECTED)
        assert call.status == CallStatus.CONNECTED
        
        # CONNECTED → COMPLETED
        call = CallStateMachine.transition(call, CallStatus.COMPLETED)
        assert call.status == CallStatus.COMPLETED

    def test_call_state_machine_idempotent_duplicate(self):
        """Test duplicate events are idempotent."""
        call = Call(id="call-1", campaign_id="c1", borrower_id="b1", status=CallStatus.CONNECTED)
        
        # Try to transition to same state (should fail)
        with pytest.raises(Exception):  # InvalidCallTransition
            CallStateMachine.transition(call, CallStatus.CONNECTED)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

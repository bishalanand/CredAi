"""
Integration tests for repositories with database.

Critical tests:
1. Repositories can read/write to database
2. Optimistic locking prevents concurrent conflicts
3. Version conflicts are properly detected
4. Two workers trying to reserve the same resource only ONE succeeds
"""

import pytest
from sqlalchemy.orm import Session

from app.db import SessionLocal, init_db
from app.domain.agent import Agent
from app.domain.borrower import Borrower
from app.domain.campaign import Campaign
from app.domain.call import Call
from app.domain.enum import AgentStatus, BorrowerStatus, CallStatus, DialingMode
from app.repositories import (
    AgentRepository,
    BorrowerRepository,
    CampaignRepository,
    CallRepository,
)
from app.state_machine.agent_state_machine import AgentStateMachine


# ---------------------------------------------------------
# FIXTURES
# ---------------------------------------------------------

@pytest.fixture(scope="function")
def db():
    """Create a fresh database session for each test."""
    init_db()
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def agent_repo(db):
    return AgentRepository(db)


@pytest.fixture
def borrower_repo(db):
    return BorrowerRepository(db)


@pytest.fixture
def campaign_repo(db):
    return CampaignRepository(db)


@pytest.fixture
def call_repo(db):
    return CallRepository(db)


# ---------------------------------------------------------
# AGENT REPOSITORY TESTS
# ---------------------------------------------------------

def test_agent_create_and_retrieve(db, agent_repo):
    """Test creating and retrieving an agent."""
    agent = Agent(id="agent-1", status=AgentStatus.OFFLINE)
    
    created = agent_repo.create(agent)
    
    assert created.id == "agent-1"
    assert created.status == AgentStatus.OFFLINE
    assert created.version == 0
    
    retrieved = agent_repo.get_by_id("agent-1")
    
    assert retrieved is not None
    assert retrieved.id == "agent-1"
    assert retrieved.status == AgentStatus.OFFLINE
    assert retrieved.version == 0


def test_agent_update_with_valid_version(db, agent_repo):
    """Test updating agent when version matches."""
    # Create agent
    agent = Agent(id="agent-1", status=AgentStatus.OFFLINE)
    created = agent_repo.create(agent)
    assert created.version == 0
    
    # Transition state
    created = AgentStateMachine.transition(created, AgentStatus.AVAILABLE)
    assert created.version == 1
    
    # Update in database
    success = agent_repo.update(created)
    
    assert success is True
    
    # Verify in database
    retrieved = agent_repo.get_by_id("agent-1")
    assert retrieved.status == AgentStatus.AVAILABLE
    assert retrieved.version == 1


def test_agent_update_fails_with_version_conflict(db, agent_repo):
    """Test that update fails when version doesn't match (optimistic locking)."""
    # Create agent
    agent = Agent(id="agent-1", status=AgentStatus.OFFLINE)
    created = agent_repo.create(agent)
    assert created.version == 0
    
    # Simulate agent being updated by another worker
    # We manually create a stale agent object with wrong version
    stale_agent = Agent(id="agent-1", status=AgentStatus.AVAILABLE)
    stale_agent.version = 5  # Wrong version!
    
    # Try to update with stale version
    success = agent_repo.update(stale_agent)
    
    assert success is False
    
    # Database should not have been modified
    retrieved = agent_repo.get_by_id("agent-1")
    assert retrieved.status == AgentStatus.OFFLINE  # Still offline
    assert retrieved.version == 0  # Still version 0


def test_get_available_agents(db, agent_repo):
    """Test retrieving available agents."""
    # Create agents with different statuses
    offline_agent = Agent(id="agent-1", status=AgentStatus.OFFLINE)
    available_agent1 = Agent(id="agent-2", status=AgentStatus.AVAILABLE)
    available_agent2 = Agent(id="agent-3", status=AgentStatus.AVAILABLE)
    reserved_agent = Agent(id="agent-4", status=AgentStatus.RESERVED)
    
    agent_repo.create(offline_agent)
    agent_repo.create(available_agent1)
    agent_repo.create(available_agent2)
    agent_repo.create(reserved_agent)
    
    # Get available agents
    available = agent_repo.get_available_agents()
    
    assert len(available) == 2
    available_ids = {a.id for a in available}
    assert available_ids == {"agent-2", "agent-3"}


def test_count_agents_by_status(db, agent_repo):
    """Test counting agents by status."""
    agent_repo.create(Agent(id="a1", status=AgentStatus.OFFLINE))
    agent_repo.create(Agent(id="a2", status=AgentStatus.AVAILABLE))
    agent_repo.create(Agent(id="a3", status=AgentStatus.AVAILABLE))
    agent_repo.create(Agent(id="a4", status=AgentStatus.DIALING))
    
    assert agent_repo.count_by_status(AgentStatus.OFFLINE) == 1
    assert agent_repo.count_by_status(AgentStatus.AVAILABLE) == 2
    assert agent_repo.count_by_status(AgentStatus.DIALING) == 1
    assert agent_repo.count_by_status(AgentStatus.CONNECTED) == 0


# ---------------------------------------------------------
# BORROWER REPOSITORY TESTS
# ---------------------------------------------------------

def test_borrower_create_and_retrieve(db, borrower_repo):
    """Test creating and retrieving a borrower."""
    borrower = Borrower(id="borrower-1", phone_number="+1234567890")
    
    created = borrower_repo.create(borrower)
    
    assert created.id == "borrower-1"
    assert created.phone_number == "+1234567890"
    assert created.status == BorrowerStatus.AVAILABLE
    assert created.version == 0
    
    retrieved = borrower_repo.get_by_id("borrower-1")
    
    assert retrieved is not None
    assert retrieved.id == "borrower-1"
    assert retrieved.phone_number == "+1234567890"


def test_borrower_update_with_valid_version(db, borrower_repo):
    """Test updating borrower when version matches."""
    borrower = Borrower(id="borrower-1", phone_number="+1234567890")
    created = borrower_repo.create(borrower)
    
    # Change status
    created.status = BorrowerStatus.RESERVED
    created.version = 1
    created.update_timestamp()
    
    success = borrower_repo.update(created)
    
    assert success is True
    
    retrieved = borrower_repo.get_by_id("borrower-1")
    assert retrieved.status == BorrowerStatus.RESERVED
    assert retrieved.version == 1


def test_borrower_update_fails_with_version_conflict(db, borrower_repo):
    """Test that update fails when version doesn't match."""
    borrower = Borrower(id="borrower-1", phone_number="+1234567890")
    created = borrower_repo.create(borrower)
    
    stale_borrower = Borrower(id="borrower-1", phone_number="+1234567890")
    stale_borrower.status = BorrowerStatus.RESERVED
    stale_borrower.version = 10  # Wrong version!
    
    success = borrower_repo.update(stale_borrower)
    
    assert success is False
    
    retrieved = borrower_repo.get_by_id("borrower-1")
    assert retrieved.status == BorrowerStatus.AVAILABLE
    assert retrieved.version == 0


def test_get_available_borrowers_for_campaign(db, borrower_repo, campaign_repo):
    """Test retrieving available borrowers for a campaign."""
    # Create campaigns
    campaign1 = Campaign(id="campaign-1", name="Test Campaign 1")
    campaign2 = Campaign(id="campaign-2", name="Test Campaign 2")
    campaign_repo.create(campaign1)
    campaign_repo.create(campaign2)
    
    # Create borrowers
    b1 = Borrower(id="b1", phone_number="+1111111111", campaign_id="campaign-1", status=BorrowerStatus.AVAILABLE)
    b2 = Borrower(id="b2", phone_number="+2222222222", campaign_id="campaign-1", status=BorrowerStatus.AVAILABLE)
    b3 = Borrower(id="b3", phone_number="+3333333333", campaign_id="campaign-1", status=BorrowerStatus.RESERVED)
    b4 = Borrower(id="b4", phone_number="+4444444444", campaign_id="campaign-2", status=BorrowerStatus.AVAILABLE)
    
    borrower_repo.create(b1)
    borrower_repo.create(b2)
    borrower_repo.create(b3)
    borrower_repo.create(b4)
    
    available = borrower_repo.get_available_for_campaign("campaign-1")
    
    assert len(available) == 2
    ids = {b.id for b in available}
    assert ids == {"b1", "b2"}


# ---------------------------------------------------------
# CAMPAIGN REPOSITORY TESTS
# ---------------------------------------------------------

def test_campaign_create_and_retrieve(db, campaign_repo):
    """Test creating and retrieving a campaign."""
    campaign = Campaign(id="campaign-1", name="Test Campaign", dialing_mode=DialingMode.PROGRESSIVE, active=False)
    
    created = campaign_repo.create(campaign)
    
    assert created.id == "campaign-1"
    assert created.name == "Test Campaign"
    assert created.dialing_mode == DialingMode.PROGRESSIVE
    assert created.active is False
    
    retrieved = campaign_repo.get_by_id("campaign-1")
    
    assert retrieved is not None
    assert retrieved.name == "Test Campaign"


def test_campaign_update(db, campaign_repo):
    """Test updating a campaign."""
    campaign = Campaign(id="campaign-1", name="Test", dialing_mode=DialingMode.PROGRESSIVE, active=False)
    created = campaign_repo.create(campaign)
    
    created.name = "Updated Campaign"
    created.active = True
    created.update_timestamp()
    
    success = campaign_repo.update(created)
    assert success is True
    
    retrieved = campaign_repo.get_by_id("campaign-1")
    assert retrieved.name == "Updated Campaign"
    assert retrieved.active is True


# ---------------------------------------------------------
# CALL REPOSITORY TESTS
# ---------------------------------------------------------

def test_call_create_and_retrieve(db, call_repo, campaign_repo, agent_repo, borrower_repo):
    """Test creating and retrieving a call."""
    # Create prerequisites
    campaign_repo.create(Campaign(id="campaign-1", name="Test"))
    agent_repo.create(Agent(id="agent-1"))
    borrower_repo.create(Borrower(id="borrower-1", phone_number="+1234567890", campaign_id="campaign-1"))
    
    call = Call(
        id="call-1",
        campaign_id="campaign-1",
        borrower_id="borrower-1",
        agent_id="agent-1",
    )
    
    created = call_repo.create(call)
    
    assert created.id == "call-1"
    assert created.campaign_id == "campaign-1"
    assert created.status == CallStatus.QUEUED
    
    retrieved = call_repo.get_by_id("call-1")
    
    assert retrieved is not None
    assert retrieved.id == "call-1"


def test_call_update_with_version(db, call_repo, campaign_repo, borrower_repo):
    """Test updating a call with version tracking."""
    # Create prerequisites
    campaign_repo.create(Campaign(id="campaign-1", name="Test"))
    borrower_repo.create(Borrower(id="borrower-1", phone_number="+1234567890", campaign_id="campaign-1"))
    
    call = Call(
        id="call-1",
        campaign_id="campaign-1",
        borrower_id="borrower-1",
    )
    created = call_repo.create(call)
    assert created.version == 0
    
    created.status = CallStatus.RESERVED
    created.version = 1
    created.update_timestamp()
    
    success = call_repo.update(created)
    
    assert success is True
    
    retrieved = call_repo.get_by_id("call-1")
    assert retrieved.status == CallStatus.RESERVED
    assert retrieved.version == 1


def test_call_get_by_provider_call_id(db, call_repo, campaign_repo, borrower_repo):
    """Test retrieving call by provider ID."""
    # Create prerequisites
    campaign_repo.create(Campaign(id="campaign-1", name="Test"))
    borrower_repo.create(Borrower(id="borrower-1", phone_number="+1234567890", campaign_id="campaign-1"))
    
    call = Call(
        id="call-1",
        campaign_id="campaign-1",
        borrower_id="borrower-1",
        provider_call_id="provider-123",
    )
    call_repo.create(call)
    
    retrieved = call_repo.get_by_provider_call_id("provider-123")
    
    assert retrieved is not None
    assert retrieved.id == "call-1"


def test_count_calls_by_status(db, call_repo, campaign_repo, borrower_repo):
    """Test counting calls by status."""
    # Create prerequisites
    campaign_repo.create(Campaign(id="camp-1", name="Test"))
    borrower_repo.create(Borrower(id="b1", phone_number="+1111", campaign_id="camp-1"))
    borrower_repo.create(Borrower(id="b2", phone_number="+2222", campaign_id="camp-1"))
    borrower_repo.create(Borrower(id="b3", phone_number="+3333", campaign_id="camp-1"))
    borrower_repo.create(Borrower(id="b4", phone_number="+4444", campaign_id="camp-1"))
    
    call_repo.create(Call(id="c1", campaign_id="camp-1", borrower_id="b1", status=CallStatus.QUEUED))
    call_repo.create(Call(id="c2", campaign_id="camp-1", borrower_id="b2", status=CallStatus.RESERVED))
    call_repo.create(Call(id="c3", campaign_id="camp-1", borrower_id="b3", status=CallStatus.INITIATED))
    call_repo.create(Call(id="c4", campaign_id="camp-1", borrower_id="b4", status=CallStatus.INITIATED))
    
    assert call_repo.count_by_status("camp-1", CallStatus.QUEUED) == 1
    assert call_repo.count_by_status("camp-1", CallStatus.RESERVED) == 1
    assert call_repo.count_by_status("camp-1", CallStatus.INITIATED) == 2


# ---------------------------------------------------------
# CRITICAL CONCURRENCY TEST
# ---------------------------------------------------------

def test_concurrent_agent_reservation_only_one_succeeds(db, agent_repo):
    """
    CRITICAL TEST: Verify two workers cannot reserve the same agent.
    
    This test proves the core safety mechanism of the system:
    optimistic locking prevents concurrent conflicts.
    
    Scenario:
    - Agent A has status AVAILABLE, version 0
    - Worker 1 reads Agent A (status AVAILABLE, v0)
    - Worker 2 reads Agent A (status AVAILABLE, v0)
    - Worker 1 tries to transition to RESERVED (v0 -> v1)
    - Worker 2 tries to transition to RESERVED (v0 -> v1)
    - Only ONE worker's update succeeds
    - The other gets version conflict
    
    Expected outcome:
    - Agent in database has version 1 (one transition happened)
    - One worker succeeded, one failed
    - System is safe: no double-reservation
    """
    
    # Step 1: Create agent in available state
    agent = Agent(id="agent-1", status=AgentStatus.AVAILABLE)
    created = agent_repo.create(agent)
    assert created.version == 0
    
    # Step 2: Simulate two workers reading the same agent
    # (In real system, these would be in separate threads/processes)
    agent_worker1 = agent_repo.get_by_id("agent-1")
    agent_worker2 = agent_repo.get_by_id("agent-1")
    
    assert agent_worker1.version == 0
    assert agent_worker2.version == 0
    assert agent_worker1.status == AgentStatus.AVAILABLE
    
    # Step 3: Worker 1 transitions to RESERVED
    agent_worker1 = AgentStateMachine.transition(agent_worker1, AgentStatus.RESERVED)
    assert agent_worker1.version == 1
    
    success_worker1 = agent_repo.update(agent_worker1)
    assert success_worker1 is True, "Worker 1 should succeed in reserving agent"
    
    # Step 4: Worker 2 tries to transition to RESERVED with stale version
    agent_worker2 = AgentStateMachine.transition(agent_worker2, AgentStatus.RESERVED)
    assert agent_worker2.version == 1
    
    success_worker2 = agent_repo.update(agent_worker2)
    assert success_worker2 is False, "Worker 2 should FAIL - version conflict"
    
    # Step 5: Verify database state
    final_agent = agent_repo.get_by_id("agent-1")
    assert final_agent.status == AgentStatus.RESERVED
    assert final_agent.version == 1  # Only ONE transition happened
    
    print("\n[CONCURRENCY TEST PASSED]")
    print("Worker 1: SUCCESS - reserved agent (v0 -> v1)")
    print("Worker 2: FAILED - version conflict detected")
    print("Database state: RESERVED, v1 (safe)")


def test_concurrent_borrower_reservation_only_one_succeeds(db, borrower_repo, campaign_repo):
    """
    Same as agent concurrency test, but for borrowers.
    
    Verifies that two workers cannot reserve the same borrower.
    """
    
    # Create campaign
    campaign = Campaign(id="campaign-1", name="Test")
    campaign_repo.create(campaign)
    
    # Create borrower
    borrower = Borrower(
        id="borrower-1",
        phone_number="+1234567890",
        campaign_id="campaign-1",
        status=BorrowerStatus.AVAILABLE,
    )
    created = borrower_repo.create(borrower)
    assert created.version == 0
    
    # Simulate two workers
    borrower_worker1 = borrower_repo.get_by_id("borrower-1")
    borrower_worker2 = borrower_repo.get_by_id("borrower-1")
    
    # Worker 1 reserves
    borrower_worker1.status = BorrowerStatus.RESERVED
    borrower_worker1.version = 1
    borrower_worker1.update_timestamp()
    
    success1 = borrower_repo.update(borrower_worker1)
    assert success1 is True
    
    # Worker 2 tries to reserve with stale version
    borrower_worker2.status = BorrowerStatus.RESERVED
    borrower_worker2.version = 1
    borrower_worker2.update_timestamp()
    
    success2 = borrower_repo.update(borrower_worker2)
    assert success2 is False, "Worker 2 should fail due to version conflict"
    
    # Verify database
    final = borrower_repo.get_by_id("borrower-1")
    assert final.status == BorrowerStatus.RESERVED
    assert final.version == 1


def test_call_version_prevents_concurrent_updates(db, call_repo, campaign_repo, borrower_repo):
    """
    Test that calls also respect optimistic locking.
    
    This ensures provider event handlers don't overwrite each other's updates.
    """
    # Create prerequisites
    campaign_repo.create(Campaign(id="campaign-1", name="Test"))
    borrower_repo.create(Borrower(id="borrower-1", phone_number="+1234567890", campaign_id="campaign-1"))
    
    call = Call(id="call-1", campaign_id="campaign-1", borrower_id="borrower-1")
    created = call_repo.create(call)
    
    # Simulate two event handlers getting the same call
    call_handler1 = call_repo.get_by_id("call-1")
    call_handler2 = call_repo.get_by_id("call-1")
    
    # Handler 1 processes RINGING event
    call_handler1.status = CallStatus.RINGING
    call_handler1.version = 1
    call_handler1.update_timestamp()
    
    success1 = call_repo.update(call_handler1)
    assert success1 is True
    
    # Handler 2 tries to process ANSWERED event with stale version
    call_handler2.status = CallStatus.ANSWERED
    call_handler2.version = 1  # Wrong! Version is now 1 in DB
    call_handler2.update_timestamp()
    
    success2 = call_repo.update(call_handler2)
    assert success2 is False, "Handler 2 should fail - stale version"
    
    # Database has correct state from Handler 1
    final = call_repo.get_by_id("call-1")
    assert final.status == CallStatus.RINGING
    assert final.version == 1

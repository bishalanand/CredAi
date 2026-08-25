"""
Tests for Event Processor: Idempotency, Out-of-Order, Duplicates.

Critical tests:
1. Normal event processing (happy path)
2. Duplicate events ignored (idempotent)
3. Out-of-order events rejected
4. Agent state updated on call completion
5. Agent state updated on call failure
6. Version conflicts handled gracefully
"""

import pytest
from datetime import datetime, timezone, timedelta

from sqlalchemy.orm import Session

from app.db import SessionLocal, init_db
from app.domain.agent import Agent
from app.domain.borrower import Borrower
from app.domain.call import Call
from app.domain.campaign import Campaign
from app.domain.enum import AgentStatus, BorrowerStatus, CallStatus, DialingMode
from app.providers.base import ProviderCallEvent
from app.event_processor import EventProcessor, EventProcessingResult
from app.repositories import (
    AgentRepository,
    BorrowerRepository,
    CallRepository,
    CampaignRepository,
)


@pytest.fixture(scope="function")
def db():
    """Create a fresh database for each test."""
    init_db()
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def event_processor(db):
    return EventProcessor(db)


@pytest.fixture
def call_repo(db):
    return CallRepository(db)


@pytest.fixture
def agent_repo(db):
    return AgentRepository(db)


@pytest.fixture
def borrower_repo(db):
    return BorrowerRepository(db)


@pytest.fixture
def campaign_repo(db):
    return CampaignRepository(db)


def create_test_call(
    call_repo,
    campaign_repo,
    agent_repo,
    borrower_repo,
    call_id="call-1",
    provider_call_id="provider-1",
    status=CallStatus.QUEUED,
    agent_status=AgentStatus.CONNECTED,
):
    """Helper to create a complete test call with all prerequisites."""
    # Create campaign
    campaign_repo.create(Campaign(id="campaign-1", name="Test"))
    
    # Create agent and borrower
    agent = Agent(id="agent-1", status=agent_status)
    agent_repo.create(agent)
    
    borrower_repo.create(Borrower(
        id="borrower-1",
        phone_number="+1234567890",
        campaign_id="campaign-1",
        status=BorrowerStatus.IN_CALL,
    ))
    
    # Create call
    call = Call(
        id=call_id,
        campaign_id="campaign-1",
        borrower_id="borrower-1",
        agent_id="agent-1",
        status=status,
        provider_call_id=provider_call_id,
    )
    return call_repo.create(call)


# ---------------------------------------------------------
# HAPPY PATH TESTS
# ---------------------------------------------------------

def test_process_ringing_event(db, event_processor, call_repo, campaign_repo, agent_repo, borrower_repo):
    """Test processing a RINGING event."""
    call = create_test_call(call_repo, campaign_repo, agent_repo, borrower_repo, status=CallStatus.INITIATED)
    
    event = ProviderCallEvent(
        provider_call_id="provider-1",
        call_id="call-1",
        event_type="RINGING",
        timestamp=datetime.now(timezone.utc),
    )
    
    result = event_processor.process_event(event)
    
    assert result.result == EventProcessingResult.SUCCESS
    
    # Verify call state updated
    updated_call = call_repo.get_by_id("call-1")
    assert updated_call.status == CallStatus.RINGING
    assert updated_call.version == 1  # Version incremented


def test_process_answered_event(db, event_processor, call_repo, campaign_repo, agent_repo, borrower_repo):
    """Test processing an ANSWERED event."""
    call = create_test_call(call_repo, campaign_repo, agent_repo, borrower_repo, status=CallStatus.RINGING)
    
    event = ProviderCallEvent(
        provider_call_id="provider-1",
        call_id="call-1",
        event_type="ANSWERED",
        timestamp=datetime.now(timezone.utc),
    )
    
    result = event_processor.process_event(event)
    
    assert result.result == EventProcessingResult.SUCCESS
    
    updated_call = call_repo.get_by_id("call-1")
    assert updated_call.status == CallStatus.ANSWERED


def test_process_completed_event(db, event_processor, call_repo, campaign_repo, agent_repo, borrower_repo):
    """Test processing a COMPLETED event (with agent state transition)."""
    call = create_test_call(call_repo, campaign_repo, agent_repo, borrower_repo, status=CallStatus.CONNECTED)
    
    event = ProviderCallEvent(
        provider_call_id="provider-1",
        call_id="call-1",
        event_type="COMPLETED",
        timestamp=datetime.now(timezone.utc),
    )
    
    # Register callback to verify it's called
    callback_called = []
    event_processor.on_call_completed(lambda c: callback_called.append(c.id))
    
    result = event_processor.process_event(event)
    
    assert result.result == EventProcessingResult.SUCCESS
    
    # Verify call state
    updated_call = call_repo.get_by_id("call-1")
    assert updated_call.status == CallStatus.COMPLETED
    
    # Verify agent returned to WRAP_UP
    updated_agent = agent_repo.get_by_id("agent-1")
    assert updated_agent.status == AgentStatus.WRAP_UP
    
    # Verify callback was called
    assert len(callback_called) == 1


def test_process_failed_event(db, event_processor, call_repo, campaign_repo, agent_repo, borrower_repo):
    """Test processing a FAILED event (with agent state transition)."""
    call = create_test_call(
        call_repo, campaign_repo, agent_repo, borrower_repo,
        status=CallStatus.INITIATED,
        agent_status=AgentStatus.DIALING,
    )
    
    event = ProviderCallEvent(
        provider_call_id="provider-1",
        call_id="call-1",
        event_type="FAILED",
        timestamp=datetime.now(timezone.utc),
        failure_reason="No answer",
    )
    
    # Register callback
    callback_called = []
    event_processor.on_call_failed(lambda c: callback_called.append(c.id))
    
    result = event_processor.process_event(event)
    
    assert result.result == EventProcessingResult.SUCCESS
    
    # Verify call state
    updated_call = call_repo.get_by_id("call-1")
    assert updated_call.status == CallStatus.FAILED
    assert updated_call.failure_reason == "No answer"
    
    # Verify agent returned to AVAILABLE
    updated_agent = agent_repo.get_by_id("agent-1")
    assert updated_agent.status == AgentStatus.AVAILABLE
    assert updated_agent.current_call_id is None
    
    # Verify callback was called
    assert len(callback_called) == 1


# ---------------------------------------------------------
# IDEMPOTENCY TESTS: Duplicate Events
# ---------------------------------------------------------

def test_duplicate_event_ignored(db, event_processor, call_repo, campaign_repo, agent_repo, borrower_repo):
    """
    Test that duplicate events are ignored (idempotent).
    
    Scenario:
    - Provider sends RINGING event at T1
    - Event processor handles it, call goes to RINGING
    - Provider sends SAME RINGING event again at T1
    - Event processor rejects it as duplicate
    - Call state unchanged
    """
    call = create_test_call(call_repo, campaign_repo, agent_repo, borrower_repo, status=CallStatus.INITIATED)
    
    timestamp = datetime.now(timezone.utc)
    event = ProviderCallEvent(
        provider_call_id="provider-1",
        call_id="call-1",
        event_type="RINGING",
        timestamp=timestamp,
    )
    
    # Process event first time
    result1 = event_processor.process_event(event)
    assert result1.result == EventProcessingResult.SUCCESS
    
    call_v1 = call_repo.get_by_id("call-1")
    assert call_v1.status == CallStatus.RINGING
    assert call_v1.version == 1
    
    # Process SAME event again (duplicate)
    result2 = event_processor.process_event(event)
    assert result2.result == EventProcessingResult.DUPLICATE
    
    # Call state should NOT have changed
    call_v2 = call_repo.get_by_id("call-1")
    assert call_v2.status == CallStatus.RINGING
    assert call_v2.version == 1  # Still version 1, not incremented


def test_multiple_different_events_succeed(db, event_processor, call_repo, campaign_repo, agent_repo, borrower_repo):
    """Test that different events are processed independently (not duplicates)."""
    call = create_test_call(call_repo, campaign_repo, agent_repo, borrower_repo, status=CallStatus.INITIATED)
    
    timestamp1 = datetime.now(timezone.utc)
    event1 = ProviderCallEvent(
        provider_call_id="provider-1",
        call_id="call-1",
        event_type="RINGING",
        timestamp=timestamp1,
    )
    
    result1 = event_processor.process_event(event1)
    assert result1.result == EventProcessingResult.SUCCESS
    
    # Now send ANSWERED (different event type)
    timestamp2 = timestamp1 + timedelta(seconds=5)
    event2 = ProviderCallEvent(
        provider_call_id="provider-1",
        call_id="call-1",
        event_type="ANSWERED",
        timestamp=timestamp2,
    )
    
    result2 = event_processor.process_event(event2)
    assert result2.result == EventProcessingResult.SUCCESS
    
    # Both events should have been processed
    call = call_repo.get_by_id("call-1")
    assert call.status == CallStatus.ANSWERED
    assert call.version == 2  # Both transitions happened


# ---------------------------------------------------------
# OUT-OF-ORDER TESTS
# ---------------------------------------------------------

def test_out_of_order_event_rejected(db, event_processor, call_repo, campaign_repo, agent_repo, borrower_repo):
    """
    Test that out-of-order events are rejected.
    
    Scenario:
    - Provider sends ANSWERED at T2 (normal flow)
    - Event processor handles it
    - Provider sends ANSWERED at T1 (same event type, older timestamp - truly out-of-order)
    - Event processor rejects it as out-of-order
    """
    call = create_test_call(call_repo, campaign_repo, agent_repo, borrower_repo, status=CallStatus.RINGING)
    
    # Process newer ANSWERED event first
    timestamp2 = datetime.now(timezone.utc)
    answered_event_new = ProviderCallEvent(
        provider_call_id="provider-1",
        call_id="call-1",
        event_type="ANSWERED",
        timestamp=timestamp2,
    )
    
    result1 = event_processor.process_event(answered_event_new)
    assert result1.result == EventProcessingResult.SUCCESS
    
    call_v1 = call_repo.get_by_id("call-1")
    assert call_v1.status == CallStatus.ANSWERED
    assert call_v1.version == 1
    
    # Now try to process older ANSWERED event (same type, older timestamp = out-of-order)
    timestamp1 = timestamp2 - timedelta(seconds=5)
    answered_event_old = ProviderCallEvent(
        provider_call_id="provider-1",
        call_id="call-1",
        event_type="ANSWERED",
        timestamp=timestamp1,
    )
    
    result2 = event_processor.process_event(answered_event_old)
    assert result2.result == EventProcessingResult.OUT_OF_ORDER
    
    # Call state should NOT have changed
    call_v2 = call_repo.get_by_id("call-1")
    assert call_v2.status == CallStatus.ANSWERED
    assert call_v2.version == 1  # Version unchanged


# ---------------------------------------------------------
# IDEMPOTENCY WITH PROVIDER: Multiple Duplicate ANSWERED Events
# ---------------------------------------------------------

def test_provider_sends_duplicate_answered_events(db, event_processor, call_repo, campaign_repo, agent_repo, borrower_repo):
    """
    Test realistic scenario: Provider sends ANSWERED event three times
    (common in telecom due to retries or bugs).
    
    Expected: First succeeds, subsequent two ignored (idempotent).
    """
    call = create_test_call(call_repo, campaign_repo, agent_repo, borrower_repo, status=CallStatus.RINGING)
    
    timestamp = datetime.now(timezone.utc)
    event = ProviderCallEvent(
        provider_call_id="provider-1",
        call_id="call-1",
        event_type="ANSWERED",
        timestamp=timestamp,
    )
    
    # Process 3 times
    results = [
        event_processor.process_event(event),
        event_processor.process_event(event),
        event_processor.process_event(event),
    ]
    
    # First should succeed
    assert results[0].result == EventProcessingResult.SUCCESS
    # Rest should be duplicates
    assert results[1].result == EventProcessingResult.DUPLICATE
    assert results[2].result == EventProcessingResult.DUPLICATE
    
    # Call should be at ANSWERED with version 1
    call = call_repo.get_by_id("call-1")
    assert call.status == CallStatus.ANSWERED
    assert call.version == 1  # Only one transition happened


# ---------------------------------------------------------
# ERROR HANDLING
# ---------------------------------------------------------

def test_event_for_nonexistent_call(db, event_processor):
    """Test processing event for a call that doesn't exist."""
    event = ProviderCallEvent(
        provider_call_id="provider-unknown",
        call_id="call-unknown",
        event_type="RINGING",
        timestamp=datetime.now(timezone.utc),
    )
    
    result = event_processor.process_event(event)
    
    assert result.result == EventProcessingResult.NOT_FOUND


def test_invalid_state_transition_rejected(db, event_processor, call_repo, campaign_repo, agent_repo, borrower_repo):
    """Test that invalid state transitions are rejected."""
    # Create call in COMPLETED state
    call = create_test_call(call_repo, campaign_repo, agent_repo, borrower_repo, status=CallStatus.COMPLETED)
    
    # Try to send RINGING event to completed call (invalid)
    event = ProviderCallEvent(
        provider_call_id="provider-1",
        call_id="call-1",
        event_type="RINGING",
        timestamp=datetime.now(timezone.utc),
    )
    
    result = event_processor.process_event(event)
    
    assert result.result == EventProcessingResult.INVALID_TRANSITION
    
    # Call should remain COMPLETED
    call = call_repo.get_by_id("call-1")
    assert call.status == CallStatus.COMPLETED


# ---------------------------------------------------------
# INTEGRATION TEST: Full Call Lifecycle
# ---------------------------------------------------------

def test_full_call_lifecycle_with_events(db, event_processor, call_repo, campaign_repo, agent_repo, borrower_repo):
    """
    End-to-end test: Full call lifecycle from INITIATED to COMPLETED.
    
    Sequence:
    1. INITIATED (starting state - version 0)
    2. RINGING (transition - version 1)
    3. ANSWERED (transition - version 2)
    4. COMPLETED (transition - version 3)
    
    Expected: All transitions succeed, call ends in COMPLETED state, agent in WRAP_UP.
    """
    # Start with INITIATED, agent in DIALING
    call = create_test_call(
        call_repo, campaign_repo, agent_repo, borrower_repo,
        status=CallStatus.INITIATED,
        agent_status=AgentStatus.DIALING,
    )
    
    base_time = datetime.now(timezone.utc)
    events = [
        ("RINGING", base_time + timedelta(seconds=1)),
        ("ANSWERED", base_time + timedelta(seconds=5)),
        # Move to CONNECTED before COMPLETED
        ("CONNECTED", base_time + timedelta(seconds=8)),
        ("COMPLETED", base_time + timedelta(seconds=10)),
    ]
    
    for event_type, timestamp in events:
        event = ProviderCallEvent(
            provider_call_id="provider-1",
            call_id="call-1",
            event_type=event_type,
            timestamp=timestamp,
        )
        result = event_processor.process_event(event)
        assert result.result == EventProcessingResult.SUCCESS, f"Failed to process {event_type}"
    
    # Verify final states
    final_call = call_repo.get_by_id("call-1")
    assert final_call.status == CallStatus.COMPLETED
    assert final_call.version == 4  # 4 transitions: INITIATED->RINGING, RINGING->ANSWERED, ANSWERED->CONNECTED, CONNECTED->COMPLETED
    
    final_agent = agent_repo.get_by_id("agent-1")
    assert final_agent.status == AgentStatus.WRAP_UP

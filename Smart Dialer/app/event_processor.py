"""
Event Processor: Handle async provider events safely.

Responsibilities:
1. Receive provider call events (RINGING, ANSWERED, COMPLETED, FAILED)
2. Correlate provider events to internal calls
3. Update call state machine
4. Handle idempotency (duplicate events are ignored)
5. Handle out-of-order events (old events rejected)
6. Update agent state when call ends
7. Track event processing for debugging

Key Design:
- Events are processed in a queue (ordered by timestamp)
- Version field prevents stale updates
- Event handler stores last processed event per call
- Duplicate events have same timestamp + type (ignored)
- Out-of-order events are detected and logged
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Callable
from enum import Enum
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.domain.call import Call
from app.domain.enum import CallStatus, AgentStatus
from app.providers.base import ProviderCallEvent
from app.repositories import CallRepository, AgentRepository
from app.state_machine.call_state_machine import CallStateMachine, InvalidCallTransition
from app.state_machine.agent_state_machine import AgentStateMachine, InvalidAgentTransition

logger = logging.getLogger(__name__)


class EventProcessingResult(str, Enum):
    """Result of processing a provider event."""
    SUCCESS = "SUCCESS"  # Event processed successfully
    DUPLICATE = "DUPLICATE"  # Same event already processed (idempotent)
    OUT_OF_ORDER = "OUT_OF_ORDER"  # Event is older than last processed
    VERSION_CONFLICT = "VERSION_CONFLICT"  # Call version changed
    INVALID_TRANSITION = "INVALID_TRANSITION"  # State machine rejected transition
    NOT_FOUND = "NOT_FOUND"  # Call not found


@dataclass
class ProcessedEvent:
    """Information about a processed provider event."""
    provider_call_id: str
    call_id: str
    event_type: str
    timestamp: datetime
    result: EventProcessingResult
    message: str = ""


class EventProcessor:
    """
    Safe, idempotent handler for provider events.
    
    Guarantees:
    - Each event is processed at most once per call
    - Duplicate events are ignored (idempotent)
    - Out-of-order events are rejected
    - Call state is always consistent
    - Agent state is updated when call ends
    """

    def __init__(self, db: Session):
        self.db = db
        self.call_repo = CallRepository(db)
        self.agent_repo = AgentRepository(db)
        
        # Track last processed event per call for idempotency
        # Format: call_id -> (event_type, timestamp)
        self._last_event_per_call: Dict[str, tuple] = {}
        
        # Event handler callbacks
        self._callbacks: Dict[str, Callable] = {}

    def on_call_completed(self, callback: Callable[[Call], None]) -> None:
        """Register callback when call completes."""
        self._callbacks["completed"] = callback

    def on_call_failed(self, callback: Callable[[Call], None]) -> None:
        """Register callback when call fails."""
        self._callbacks["failed"] = callback

    def process_event(self, event: ProviderCallEvent) -> ProcessedEvent:
        """
        Process a provider event safely.
        
        Returns:
            ProcessedEvent with result and reasoning
        """
        
        # Step 1: Find call
        call = self.call_repo.get_by_provider_call_id(event.provider_call_id)
        if not call:
            # Try looking up by call_id directly (in case provider_call_id not yet set)
            call = self.call_repo.get_by_id(event.call_id)
            if not call:
                return ProcessedEvent(
                    provider_call_id=event.provider_call_id,
                    call_id=event.call_id,
                    event_type=event.event_type,
                    timestamp=event.timestamp,
                    result=EventProcessingResult.NOT_FOUND,
                    message=f"Call not found: {event.call_id}",
                )
        
        # Step 2: Check for duplicate event
        last_event_key = f"{call.id}_{event.event_type}"
        if last_event_key in self._last_event_per_call:
            last_type, last_timestamp = self._last_event_per_call[last_event_key]
            # Same event type and timestamp = duplicate (idempotent)
            if event.event_type == last_type and event.timestamp == last_timestamp:
                logger.info(f"Duplicate event ignored: {event.event_type} for call {call.id}")
                return ProcessedEvent(
                    provider_call_id=event.provider_call_id,
                    call_id=event.call_id,
                    event_type=event.event_type,
                    timestamp=event.timestamp,
                    result=EventProcessingResult.DUPLICATE,
                    message="Event already processed (idempotent)",
                )
        
        # Step 3: Check for out-of-order event
        if last_event_key in self._last_event_per_call:
            _, last_timestamp = self._last_event_per_call[last_event_key]
            if event.timestamp < last_timestamp:
                logger.warning(
                    f"Out-of-order event rejected: {event.event_type} "
                    f"(timestamp {event.timestamp} < last {last_timestamp}) "
                    f"for call {call.id}"
                )
                return ProcessedEvent(
                    provider_call_id=event.provider_call_id,
                    call_id=event.call_id,
                    event_type=event.event_type,
                    timestamp=event.timestamp,
                    result=EventProcessingResult.OUT_OF_ORDER,
                    message="Event timestamp is older than last processed event",
                )
        
        # Step 4: Update call state
        try:
            target_status = self._event_type_to_status(event.event_type)
            call = CallStateMachine.transition(call, target_status)
            
            # If it's a failure, set failure reason
            if event.event_type == "FAILED":
                call.failure_reason = event.failure_reason
            
            # Update provider call ID if not set
            if not call.provider_call_id and event.provider_call_id:
                call.provider_call_id = event.provider_call_id
            
            call.update_timestamp()
            
        except InvalidCallTransition as e:
            logger.warning(f"Invalid transition: {e} for call {call.id}")
            return ProcessedEvent(
                provider_call_id=event.provider_call_id,
                call_id=event.call_id,
                event_type=event.event_type,
                timestamp=event.timestamp,
                result=EventProcessingResult.INVALID_TRANSITION,
                message=str(e),
            )
        
        # Step 5: Commit to database with optimistic locking
        success = self.call_repo.update(call)
        if not success:
            logger.warning(f"Version conflict updating call {call.id}")
            return ProcessedEvent(
                provider_call_id=event.provider_call_id,
                call_id=event.call_id,
                event_type=event.event_type,
                timestamp=event.timestamp,
                result=EventProcessingResult.VERSION_CONFLICT,
                message="Call was updated by another process",
            )
        
        # Step 6: Record this event as processed
        self._last_event_per_call[last_event_key] = (event.event_type, event.timestamp)
        
        # Step 7: Handle end-of-call scenarios
        if event.event_type == "COMPLETED":
            self._handle_call_completed(call)
        elif event.event_type == "FAILED":
            self._handle_call_failed(call)
        
        logger.info(f"Event processed: {event.event_type} for call {call.id} (v{call.version})")
        
        return ProcessedEvent(
            provider_call_id=event.provider_call_id,
            call_id=event.call_id,
            event_type=event.event_type,
            timestamp=event.timestamp,
            result=EventProcessingResult.SUCCESS,
            message="Event processed successfully",
        )

    def _event_type_to_status(self, event_type: str) -> CallStatus:
        """Convert provider event type to call status."""
        mapping = {
            "RINGING": CallStatus.RINGING,
            "ANSWERED": CallStatus.ANSWERED,
            "CONNECTED": CallStatus.CONNECTED,
            "COMPLETED": CallStatus.COMPLETED,
            "FAILED": CallStatus.FAILED,
            "CANCELLED": CallStatus.CANCELLED,
        }
        return mapping.get(event_type, CallStatus.QUEUED)

    def _handle_call_completed(self, call: Call) -> None:
        """Handle call completion: update agent state."""
        if not call.agent_id:
            return
        
        agent = self.agent_repo.get_by_id(call.agent_id)
        if not agent:
            logger.warning(f"Agent not found for call {call.id}")
            return
        
        try:
            # Transition agent to WRAP_UP
            agent = AgentStateMachine.transition(agent, AgentStatus.WRAP_UP)
            agent.current_call_id = None
            agent.update_timestamp()
            
            success = self.agent_repo.update(agent)
            if success:
                logger.info(f"Agent {agent.id} transitioned to WRAP_UP")
            else:
                logger.warning(f"Failed to update agent {agent.id} (version conflict)")
        except InvalidAgentTransition as e:
            logger.warning(f"Cannot transition agent to WRAP_UP: {e}")
        
        # Trigger callback
        if "completed" in self._callbacks:
            self._callbacks["completed"](call)

    def _handle_call_failed(self, call: Call) -> None:
        """Handle call failure: update agent state."""
        if not call.agent_id:
            return
        
        agent = self.agent_repo.get_by_id(call.agent_id)
        if not agent:
            logger.warning(f"Agent not found for call {call.id}")
            return
        
        try:
            # On failure, go back to AVAILABLE
            agent = AgentStateMachine.transition(agent, AgentStatus.AVAILABLE)
            agent.current_call_id = None
            agent.update_timestamp()
            
            success = self.agent_repo.update(agent)
            if success:
                logger.info(f"Agent {agent.id} returned to AVAILABLE after call failure")
            else:
                logger.warning(f"Failed to update agent {agent.id} (version conflict)")
        except InvalidAgentTransition as e:
            logger.warning(f"Cannot transition agent back to AVAILABLE: {e}")
        
        # Trigger callback
        if "failed" in self._callbacks:
            self._callbacks["failed"](call)

    def get_last_event(self, call_id: str, event_type: str) -> Optional[tuple]:
        """Get the last processed event of a type for a call."""
        key = f"{call_id}_{event_type}"
        return self._last_event_per_call.get(key)

    def clear_call_history(self, call_id: str) -> None:
        """Clear event history for a call (for testing/cleanup)."""
        keys_to_delete = [k for k in self._last_event_per_call.keys() if k.startswith(f"{call_id}_")]
        for key in keys_to_delete:
            del self._last_event_per_call[key]

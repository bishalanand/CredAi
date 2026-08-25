"""
Provider event processing.

Responsibilities:
- correlate provider events to calls
- validate call state transitions
- keep agent state synchronized
- ignore duplicate events
- reject out-of-order events
- handle provider failures
- expose completion/failure callbacks

The processor deliberately keeps the state machines as the single
authority for valid state transitions.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Callable, List, Optional, Set, Tuple
import logging

from app.domain import Call, CallStatus, AgentStatus
from app.state_machine.agent_state_machine import (
    AgentStateMachine,
    InvalidAgentTransition,
)
from app.state_machine.call_state_machine import (
    CallStateMachine,
    InvalidCallTransition,
)

logger = logging.getLogger(__name__)


class EventProcessingResult(str, Enum):
    """Outcome of processing one provider event."""

    SUCCESS = "SUCCESS"
    DUPLICATE = "DUPLICATE"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    INVALID_TRANSITION = "INVALID_TRANSITION"
    NOT_FOUND = "NOT_FOUND"


@dataclass
class EventProcessingResponse:
    """Result object returned to callers."""

    result: EventProcessingResult
    message: str = ""
    call: Optional[Call] = None


class EventProcessor:
    """
    Processes asynchronous telecom-provider events.

    The processor is intentionally synchronous because repositories
    currently use a synchronous SQLAlchemy Session.

    Event flow:

        Provider Event
             |
             v
        Find Call
             |
             v
        Duplicate / ordering check
             |
             v
        Call State Machine
             |
             +---- CONNECTED ----> Agent DIALING -> CONNECTED
             |
             +---- COMPLETED ----> Agent CONNECTED -> WRAP_UP
             |
             +---- FAILED ------> Agent DIALING -> AVAILABLE
    """

    # Ordering of normal provider lifecycle events.
    _EVENT_ORDER = {
        "RINGING": 1,
        "ANSWERED": 2,
        "CONNECTED": 3,
        "COMPLETED": 4,
        "FAILED": 4,
    }

    def __init__(self, db_or_call_repository, agent_repository=None):
        """
        Supports both:
            EventProcessor(db)
        and:
            EventProcessor(call_repository, agent_repository)

        The test suite uses EventProcessor(db), while keeping the
        two-repository form makes the class convenient for direct use.
        """
        if agent_repository is None:
            # Lazy import avoids circular imports at module load time.
            from app.repositories import CallRepository, AgentRepository

            self.call_repository = CallRepository(db_or_call_repository)
            self.agent_repository = AgentRepository(db_or_call_repository)
        else:
            self.call_repository = db_or_call_repository
            self.agent_repository = agent_repository

        self.call_state_machine = CallStateMachine()
        self.agent_state_machine = AgentStateMachine()

        # ProviderCallEvent currently has no event_id. Therefore use a
        # stable composite identity for exact duplicate detection.
        self._processed_events: Set[Tuple] = set()

        # Last timestamp seen for each provider call. This is used to
        # reject an older event arriving after a newer event.
        self._last_event_timestamp = {}

        # Callbacks registered by the dialer/application layer.
        self._completed_callbacks: List[Callable[[Call], None]] = []
        self._failed_callbacks: List[Callable[[Call], None]] = []

    # -----------------------------------------------------------------
    # Public callback API
    # -----------------------------------------------------------------

    def on_call_completed(self, callback: Callable[[Call], None]) -> None:
        """Register a callback invoked after a call is completed."""
        self._completed_callbacks.append(callback)

    def on_call_failed(self, callback: Callable[[Call], None]) -> None:
        """Register a callback invoked after a call fails."""
        self._failed_callbacks.append(callback)

    # -----------------------------------------------------------------
    # Public event-processing API
    # -----------------------------------------------------------------

    def process_event(self, event) -> EventProcessingResponse:
        """Process one ProviderCallEvent safely and idempotently."""

        event_key = self._event_key(event)

        # Exact duplicate: same provider call, type and timestamp.
        if event_key in self._processed_events:
            return EventProcessingResponse(
                EventProcessingResult.DUPLICATE,
                "Duplicate provider event.",
            )

        call = self.call_repository.get_by_provider_call_id(
            event.provider_call_id
        )

        if call is None:
            return EventProcessingResponse(
                EventProcessingResult.NOT_FOUND,
                f"Call not found for provider_call_id={event.provider_call_id}",
            )

        # Reject older events. A duplicate event with a newer timestamp
        # is handled separately below by checking the current state.
        last_timestamp = self._last_event_timestamp.get(
            event.provider_call_id
        )
        if (
            last_timestamp is not None
            and event.timestamp < last_timestamp
        ):
            return EventProcessingResponse(
                EventProcessingResult.OUT_OF_ORDER,
                "Provider event timestamp is older than the last event.",
                call,
            )

        # If the requested state is already the current state, this is
        # semantically a duplicate even if the provider assigned a new
        # timestamp to the retry.
        target_status = self._target_call_status(event.event_type)

        if target_status is None:
            return EventProcessingResponse(
                EventProcessingResult.INVALID_TRANSITION,
                f"Unknown provider event type: {event.event_type}",
                call,
            )

        if call.status == target_status:
            self._processed_events.add(event_key)
            if last_timestamp is None or event.timestamp > last_timestamp:
                self._last_event_timestamp[event.provider_call_id] = event.timestamp

            return EventProcessingResponse(
                EventProcessingResult.DUPLICATE,
                "Call is already in the requested state.",
                call,
            )

        try:
            if event.event_type == "RINGING":
                self._handle_ringing(call)

            elif event.event_type == "ANSWERED":
                self._handle_answered(call, event)

            elif event.event_type == "CONNECTED":
                self._handle_connected(call, event)

            elif event.event_type == "COMPLETED":
                self._handle_completed(call, event)

            elif event.event_type == "FAILED":
                self._handle_failed(call, event)

            else:
                return EventProcessingResponse(
                    EventProcessingResult.INVALID_TRANSITION,
                    f"Unsupported event type: {event.event_type}",
                    call,
                )

        except InvalidCallTransition as exc:
            return EventProcessingResponse(
                EventProcessingResult.INVALID_TRANSITION,
                str(exc),
                call,
            )
        except InvalidAgentTransition as exc:
            return EventProcessingResponse(
                EventProcessingResult.INVALID_TRANSITION,
                str(exc),
                call,
            )

        # Mark only successfully applied events as processed.
        self._processed_events.add(event_key)
        self._last_event_timestamp[event.provider_call_id] = event.timestamp

        return EventProcessingResponse(
            EventProcessingResult.SUCCESS,
            "Event processed successfully.",
            call,
        )

    # -----------------------------------------------------------------
    # Call handlers
    # -----------------------------------------------------------------

    def _handle_ringing(self, call: Call) -> None:
        self.call_state_machine.transition(
            call,
            CallStatus.RINGING,
        )

        self.call_repository.update(call)

    def _handle_answered(self, call: Call, event) -> None:
        self.call_state_machine.transition(
            call,
            CallStatus.ANSWERED,
        )

        call.answered_at = event.timestamp
        self.call_repository.update(call)

    def _handle_connected(self, call: Call, event) -> None:
        """
        Move both sides of the call relationship:

            Call:  ANSWERED -> CONNECTED
            Agent: DIALING  -> CONNECTED

        This is the root fix for the full lifecycle test. Without the
        agent transition, COMPLETED later tries to do:

            DIALING -> WRAP_UP

        which the AgentStateMachine correctly rejects.
        """
        self.call_state_machine.transition(
            call,
            CallStatus.CONNECTED,
        )

        call.connected_at = event.timestamp

        if not self.call_repository.update(call):
            raise RuntimeError("Call update failed due to version conflict.")

        if not call.agent_id:
            return

        agent = self.agent_repository.get_by_id(call.agent_id)

        if agent is None:
            return

        self.agent_state_machine.transition(
            agent,
            AgentStatus.CONNECTED,
        )

        # Keep the currently active call associated with the agent.
        agent.current_call_id = call.id

        if not self.agent_repository.update(agent):
            raise RuntimeError("Agent update failed due to version conflict.")

    def _handle_completed(self, call: Call, event) -> None:
        self.call_state_machine.transition(
            call,
            CallStatus.COMPLETED,
        )

        call.completed_at = event.timestamp

        if not self.call_repository.update(call):
            raise RuntimeError("Call update failed due to version conflict.")

        if call.agent_id:
            agent = self.agent_repository.get_by_id(call.agent_id)

            if agent is not None:
                # Normal successful lifecycle:
                # CONNECTED -> WRAP_UP
                if agent.status == AgentStatus.CONNECTED:
                    self.agent_state_machine.transition(
                        agent,
                        AgentStatus.WRAP_UP,
                    )

                    agent.current_call_id = call.id

                    if not self.agent_repository.update(agent):
                        raise RuntimeError(
                            "Agent update failed due to version conflict."
                        )

        for callback in self._completed_callbacks:
            callback(call)

    def _handle_failed(self, call: Call, event) -> None:
        self.call_state_machine.transition(
            call,
            CallStatus.FAILED,
        )

        call.failure_reason = event.failure_reason

        if not self.call_repository.update(call):
            raise RuntimeError("Call update failed due to version conflict.")

        if call.agent_id:
            agent = self.agent_repository.get_by_id(call.agent_id)

            if agent is not None:
                # A failure before connection releases an agent.
                if agent.status == AgentStatus.DIALING:
                    self.agent_state_machine.transition(
                        agent,
                        AgentStatus.AVAILABLE,
                    )
                    agent.current_call_id = None

                    if not self.agent_repository.update(agent):
                        raise RuntimeError(
                            "Agent update failed due to version conflict."
                        )

                # If the call fails after connection, the agent should
                # still pass through WRAP_UP before becoming available.
                elif agent.status == AgentStatus.CONNECTED:
                    self.agent_state_machine.transition(
                        agent,
                        AgentStatus.WRAP_UP,
                    )

                    if not self.agent_repository.update(agent):
                        raise RuntimeError(
                            "Agent update failed due to version conflict."
                        )

        for callback in self._failed_callbacks:
            callback(call)

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------

    @staticmethod
    def _event_key(event) -> Tuple:
        """
        ProviderCallEvent currently has no event_id, so use the fields
        that uniquely identify one provider event in this prototype.
        """
        return (
            event.provider_call_id,
            event.event_type,
            event.timestamp,
        )

    @staticmethod
    def _target_call_status(event_type: str) -> Optional[CallStatus]:
        return {
            "RINGING": CallStatus.RINGING,
            "ANSWERED": CallStatus.ANSWERED,
            "CONNECTED": CallStatus.CONNECTED,
            "COMPLETED": CallStatus.COMPLETED,
            "FAILED": CallStatus.FAILED,
        }.get(event_type)

    @staticmethod
    def _status_order(status: CallStatus) -> Optional[int]:
        return {
            CallStatus.QUEUED: 0,
            CallStatus.RESERVED: 0,
            CallStatus.INITIATED: 0,
            CallStatus.RINGING: 1,
            CallStatus.ANSWERED: 2,
            CallStatus.CONNECTED: 3,
            CallStatus.COMPLETED: 4,
            CallStatus.FAILED: 4,
            CallStatus.CANCELLED: 4,
        }.get(status)
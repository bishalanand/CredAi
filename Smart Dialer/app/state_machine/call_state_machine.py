from app.domain.call import Call
from app.domain.enum import CallStatus


class InvalidCallTransition(Exception):
    """Raised when an invalid call state transition is requested."""


class CallStateMachine:
    """
    Controls valid state transitions for a Call.
    """

    VALID_TRANSITIONS = {
        CallStatus.QUEUED: {
            CallStatus.RESERVED,
            CallStatus.CANCELLED,
        },

        CallStatus.RESERVED: {
            CallStatus.INITIATED,
            CallStatus.CANCELLED,
        },

        CallStatus.INITIATED: {
            CallStatus.RINGING,
            CallStatus.FAILED,
            CallStatus.CANCELLED,
        },

        CallStatus.RINGING: {
            CallStatus.ANSWERED,
            CallStatus.FAILED,
            CallStatus.CANCELLED,
        },

        CallStatus.ANSWERED: {
            CallStatus.CONNECTED,
            CallStatus.COMPLETED,
        },

        CallStatus.CONNECTED: {
            CallStatus.COMPLETED,
            CallStatus.FAILED,
        },

        CallStatus.COMPLETED: set(),

        CallStatus.FAILED: set(),

        CallStatus.CANCELLED: set(),
    }

    @classmethod
    def can_transition(
        cls,
        current_state: CallStatus,
        new_state: CallStatus,
    ) -> bool:
        """
        Check whether a call transition is valid.
        """

        return new_state in cls.VALID_TRANSITIONS.get(
            current_state,
            set(),
        )

    @classmethod
    def transition(
        cls,
        call: Call,
        new_state: CallStatus,
    ) -> Call:

        current_state = call.status

        if not cls.can_transition(
            current_state,
            new_state,
        ):
            raise InvalidCallTransition(
                f"Invalid call transition: "
                f"{current_state.value} -> {new_state.value}"
            )

        call.status = new_state
        call.version += 1
        call.update_timestamp()

        return call
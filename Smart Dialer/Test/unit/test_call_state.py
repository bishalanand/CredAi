import pytest

from app.domain.call import Call
from app.domain.enum import CallStatus
from app.state_machine.call_state_machine import (
    CallStateMachine,
    InvalidCallTransition,
)


def create_call():
    return Call(
        id="call-1",
        campaign_id="campaign-1",
        borrower_id="borrower-1",
    )


# ---------------------------------------------------------
# VALID TRANSITIONS
# ---------------------------------------------------------

def test_queued_to_reserved():

    call = create_call()

    CallStateMachine.transition(
        call,
        CallStatus.RESERVED,
    )

    assert call.status == CallStatus.RESERVED


def test_reserved_to_initiated():

    call = create_call()

    CallStateMachine.transition(
        call,
        CallStatus.RESERVED,
    )

    CallStateMachine.transition(
        call,
        CallStatus.INITIATED,
    )

    assert call.status == CallStatus.INITIATED


def test_initiated_to_ringing():

    call = create_call()

    CallStateMachine.transition(
        call,
        CallStatus.RESERVED,
    )

    CallStateMachine.transition(
        call,
        CallStatus.INITIATED,
    )

    CallStateMachine.transition(
        call,
        CallStatus.RINGING,
    )

    assert call.status == CallStatus.RINGING


def test_ringing_to_answered():

    call = create_call()

    CallStateMachine.transition(
        call,
        CallStatus.RESERVED,
    )

    CallStateMachine.transition(
        call,
        CallStatus.INITIATED,
    )

    CallStateMachine.transition(
        call,
        CallStatus.RINGING,
    )

    CallStateMachine.transition(
        call,
        CallStatus.ANSWERED,
    )

    assert call.status == CallStatus.ANSWERED


def test_answered_to_connected():

    call = create_call()

    CallStateMachine.transition(
        call,
        CallStatus.RESERVED,
    )

    CallStateMachine.transition(
        call,
        CallStatus.INITIATED,
    )

    CallStateMachine.transition(
        call,
        CallStatus.RINGING,
    )

    CallStateMachine.transition(
        call,
        CallStatus.ANSWERED,
    )

    CallStateMachine.transition(
        call,
        CallStatus.CONNECTED,
    )

    assert call.status == CallStatus.CONNECTED


def test_connected_to_completed():

    call = create_call()

    CallStateMachine.transition(
        call,
        CallStatus.RESERVED,
    )

    CallStateMachine.transition(
        call,
        CallStatus.INITIATED,
    )

    CallStateMachine.transition(
        call,
        CallStatus.RINGING,
    )

    CallStateMachine.transition(
        call,
        CallStatus.ANSWERED,
    )

    CallStateMachine.transition(
        call,
        CallStatus.CONNECTED,
    )

    CallStateMachine.transition(
        call,
        CallStatus.COMPLETED,
    )

    assert call.status == CallStatus.COMPLETED


# ---------------------------------------------------------
# FAILURE TRANSITIONS
# ---------------------------------------------------------

def test_initiated_to_failed():

    call = create_call()

    CallStateMachine.transition(
        call,
        CallStatus.RESERVED,
    )

    CallStateMachine.transition(
        call,
        CallStatus.INITIATED,
    )

    CallStateMachine.transition(
        call,
        CallStatus.FAILED,
    )

    assert call.status == CallStatus.FAILED


def test_ringing_to_failed():

    call = create_call()

    CallStateMachine.transition(
        call,
        CallStatus.RESERVED,
    )

    CallStateMachine.transition(
        call,
        CallStatus.INITIATED,
    )

    CallStateMachine.transition(
        call,
        CallStatus.RINGING,
    )

    CallStateMachine.transition(
        call,
        CallStatus.FAILED,
    )

    assert call.status == CallStatus.FAILED


def test_queued_to_cancelled():

    call = create_call()

    CallStateMachine.transition(
        call,
        CallStatus.CANCELLED,
    )

    assert call.status == CallStatus.CANCELLED


# ---------------------------------------------------------
# INVALID TRANSITIONS
# ---------------------------------------------------------

def test_queued_to_connected_is_invalid():

    call = create_call()

    with pytest.raises(InvalidCallTransition):
        CallStateMachine.transition(
            call,
            CallStatus.CONNECTED,
        )


def test_completed_to_connected_is_invalid():

    call = create_call()

    CallStateMachine.transition(
        call,
        CallStatus.RESERVED,
    )

    CallStateMachine.transition(
        call,
        CallStatus.INITIATED,
    )

    CallStateMachine.transition(
        call,
        CallStatus.RINGING,
    )

    CallStateMachine.transition(
        call,
        CallStatus.ANSWERED,
    )

    CallStateMachine.transition(
        call,
        CallStatus.CONNECTED,
    )

    CallStateMachine.transition(
        call,
        CallStatus.COMPLETED,
    )

    with pytest.raises(InvalidCallTransition):
        CallStateMachine.transition(
            call,
            CallStatus.CONNECTED,
        )


def test_failed_to_connected_is_invalid():

    call = create_call()

    CallStateMachine.transition(
        call,
        CallStatus.RESERVED,
    )

    CallStateMachine.transition(
        call,
        CallStatus.INITIATED,
    )

    CallStateMachine.transition(
        call,
        CallStatus.FAILED,
    )

    with pytest.raises(InvalidCallTransition):
        CallStateMachine.transition(
            call,
            CallStatus.CONNECTED,
        )


# ---------------------------------------------------------
# VERSIONING
# ---------------------------------------------------------

def test_call_transition_increments_version():

    call = create_call()

    assert call.version == 0

    CallStateMachine.transition(
        call,
        CallStatus.RESERVED,
    )

    assert call.version == 1

    CallStateMachine.transition(
        call,
        CallStatus.INITIATED,
    )

    assert call.version == 2
import pytest

from app.domain.agent import Agent
from app.domain.enum import AgentStatus
from app.state_machine.agent_state_machine import (
    AgentStateMachine,
    InvalidAgentTransition,
)


# ---------------------------------------------------------
# VALID TRANSITIONS
# ---------------------------------------------------------

def test_offline_to_available():

    agent = Agent(
        id="agent-1",
        status=AgentStatus.OFFLINE,
    )

    AgentStateMachine.transition(
        agent,
        AgentStatus.AVAILABLE,
    )

    assert agent.status == AgentStatus.AVAILABLE


def test_available_to_reserved():

    agent = Agent(
        id="agent-1",
        status=AgentStatus.AVAILABLE,
    )

    AgentStateMachine.transition(
        agent,
        AgentStatus.RESERVED,
    )

    assert agent.status == AgentStatus.RESERVED


def test_reserved_to_dialing():

    agent = Agent(
        id="agent-1",
        status=AgentStatus.RESERVED,
    )

    AgentStateMachine.transition(
        agent,
        AgentStatus.DIALING,
    )

    assert agent.status == AgentStatus.DIALING


def test_dialing_to_connected():

    agent = Agent(
        id="agent-1",
        status=AgentStatus.DIALING,
    )

    AgentStateMachine.transition(
        agent,
        AgentStatus.CONNECTED,
    )

    assert agent.status == AgentStatus.CONNECTED


def test_connected_to_wrap_up():

    agent = Agent(
        id="agent-1",
        status=AgentStatus.CONNECTED,
    )

    AgentStateMachine.transition(
        agent,
        AgentStatus.WRAP_UP,
    )

    assert agent.status == AgentStatus.WRAP_UP


def test_wrap_up_to_available():

    agent = Agent(
        id="agent-1",
        status=AgentStatus.WRAP_UP,
    )

    AgentStateMachine.transition(
        agent,
        AgentStatus.AVAILABLE,
    )

    assert agent.status == AgentStatus.AVAILABLE


# ---------------------------------------------------------
# INVALID TRANSITIONS
# ---------------------------------------------------------

def test_offline_to_connected_is_invalid():

    agent = Agent(
        id="agent-1",
        status=AgentStatus.OFFLINE,
    )

    with pytest.raises(InvalidAgentTransition):
        AgentStateMachine.transition(
            agent,
            AgentStatus.CONNECTED,
        )


def test_available_to_connected_is_invalid():

    agent = Agent(
        id="agent-1",
        status=AgentStatus.AVAILABLE,
    )

    with pytest.raises(InvalidAgentTransition):
        AgentStateMachine.transition(
            agent,
            AgentStatus.CONNECTED,
        )


def test_wrap_up_to_dialing_is_invalid():

    agent = Agent(
        id="agent-1",
        status=AgentStatus.WRAP_UP,
    )

    with pytest.raises(InvalidAgentTransition):
        AgentStateMachine.transition(
            agent,
            AgentStatus.DIALING,
        )


# ---------------------------------------------------------
# VERSIONING
# ---------------------------------------------------------

def test_transition_increments_version():

    agent = Agent(
        id="agent-1",
        status=AgentStatus.OFFLINE,
    )

    assert agent.version == 0

    AgentStateMachine.transition(
        agent,
        AgentStatus.AVAILABLE,
    )

    assert agent.version == 1

    AgentStateMachine.transition(
        agent,
        AgentStatus.RESERVED,
    )

    assert agent.version == 2
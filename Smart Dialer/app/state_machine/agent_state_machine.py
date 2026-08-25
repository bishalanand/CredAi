from app.domain.agent import Agent
from app.domain.enum import AgentStatus


class InvalidAgentTransition(Exception):
    """Raised when an invalid agent state transition is requested."""


class AgentStateMachine:
    """
    Controls valid state transitions for an Agent.

    This class is responsible only for validating and applying
    state transitions.

    Concurrency control will be handled later at the repository/
    database layer.
    """

    VALID_TRANSITIONS = {
        AgentStatus.OFFLINE: {
            AgentStatus.AVAILABLE,
        },

        AgentStatus.AVAILABLE: {
            AgentStatus.RESERVED,
            AgentStatus.PAUSED,
        },

        AgentStatus.RESERVED: {
            AgentStatus.DIALING,
            AgentStatus.AVAILABLE,
        },

        AgentStatus.DIALING: {
            AgentStatus.CONNECTED,
            AgentStatus.AVAILABLE,
        },

        AgentStatus.CONNECTED: {
            AgentStatus.WRAP_UP,
        },

        AgentStatus.WRAP_UP: {
            AgentStatus.AVAILABLE,
        },

        AgentStatus.PAUSED: {
            AgentStatus.AVAILABLE,
            AgentStatus.OFFLINE,
        },
    }

    @classmethod
    def can_transition(
        cls,
        current_state: AgentStatus,
        new_state: AgentStatus,
    ) -> bool:
        """
        Check whether a state transition is valid.
        """

        return new_state in cls.VALID_TRANSITIONS.get(
            current_state,
            set(),
        )

    @classmethod
    def transition(
        cls,
        agent: Agent,
        new_state: AgentStatus,
    ) -> Agent:

        current_state = agent.status

        if not cls.can_transition(current_state, new_state):
            raise InvalidAgentTransition(
                f"Invalid agent transition: "
                f"{current_state.value} -> {new_state.value}"
            )

        agent.status = new_state
        agent.version += 1
        agent.update_timestamp()

        return agent
from datetime import datetime

from app.domain import CallStatus, AgentStatus
from app.state_machine.agent_state_machine import AgentStateMachine
from app.state_machine.call_state_machine import CallStateMachine


class EventProcessor:
    def __init__(self, call_repository, agent_repository):
        self.call_repository = call_repository
        self.agent_repository = agent_repository

        self.call_state_machine = CallStateMachine()
        self.agent_state_machine = AgentStateMachine()

        # Keep track of processed provider events so that duplicate
        # events are ignored.
        self.processed_events = set()

    def process_event(self, event):
        """
        Process a provider event.

        Provider events can be:
        - duplicated
        - out of order
        - for a nonexistent call
        - invalid for the current call state
        """

        # ---------------------------------------------------------
        # 1. Ignore duplicate events
        # ---------------------------------------------------------

        if event.event_id in self.processed_events:
            return False

        # ---------------------------------------------------------
        # 2. Find the call
        # ---------------------------------------------------------

        call = self.call_repository.get_by_provider_call_id(
            event.provider_call_id
        )

        if call is None:
            return False

        # ---------------------------------------------------------
        # 3. Process according to event type
        # ---------------------------------------------------------

        try:

            if event.event_type == "RINGING":

                self._handle_ringing(call)

            elif event.event_type == "ANSWERED":

                self._handle_answered(call)

            elif event.event_type == "CONNECTED":

                self._handle_connected(call)

            elif event.event_type == "COMPLETED":

                self._handle_completed(call)

            elif event.event_type == "FAILED":

                self._handle_failed(call)

            else:
                return False

        except ValueError:
            # Invalid state transition
            return False

        # ---------------------------------------------------------
        # 4. Mark event as processed
        # ---------------------------------------------------------

        self.processed_events.add(event.event_id)

        return True

    # =============================================================
    # RINGING
    # =============================================================

    def _handle_ringing(self, call):

        self.call_state_machine.transition(
            call,
            CallStatus.RINGING
        )

        self.call_repository.update(call)

    # =============================================================
    # ANSWERED
    # =============================================================

    def _handle_answered(self, call):

        self.call_state_machine.transition(
            call,
            CallStatus.ANSWERED
        )

        self.call_repository.update(call)

    # =============================================================
    # CONNECTED
    # =============================================================

    def _handle_connected(self, call):

        # ---------------------------------------------------------
        # First update the CALL state
        #
        # ANSWERED -> CONNECTED
        # ---------------------------------------------------------

        self.call_state_machine.transition(
            call,
            CallStatus.CONNECTED
        )

        self.call_repository.update(call)

        # ---------------------------------------------------------
        # IMPORTANT FIX
        #
        # The agent must also move:
        #
        # DIALING -> CONNECTED
        #
        # Otherwise the later COMPLETED event will try:
        #
        # DIALING -> WRAP_UP
        #
        # which is invalid.
        # ---------------------------------------------------------

        agent = self.agent_repository.get_by_id(call.agent_id)

        if agent is not None:

            self.agent_state_machine.transition(
                agent,
                AgentStatus.CONNECTED
            )

            self.agent_repository.update(agent)

    # =============================================================
    # COMPLETED
    # =============================================================

    def _handle_completed(self, call):

        # ---------------------------------------------------------
        # Call:
        #
        # CONNECTED -> COMPLETED
        # ---------------------------------------------------------

        self.call_state_machine.transition(
            call,
            CallStatus.COMPLETED
        )

        self.call_repository.update(call)

        # ---------------------------------------------------------
        # Agent:
        #
        # CONNECTED -> WRAP_UP
        # ---------------------------------------------------------

        agent = self.agent_repository.get_by_id(call.agent_id)

        if agent is not None:

            self.agent_state_machine.transition(
                agent,
                AgentStatus.WRAP_UP
            )

            self.agent_repository.update(agent)

    # =============================================================
    # FAILED
    # =============================================================

    def _handle_failed(self, call):

        self.call_state_machine.transition(
            call,
            CallStatus.FAILED
        )

        self.call_repository.update(call)

        # ---------------------------------------------------------
        # Failed calls should release the agent.
        #
        # Depending on your exact Phase 13 implementation,
        # this may be DIALING -> AVAILABLE or another valid
        # transition.
        # ---------------------------------------------------------

        agent = self.agent_repository.get_by_id(call.agent_id)

        if agent is not None:

            if agent.status == AgentStatus.DIALING:

                self.agent_state_machine.transition(
                    agent,
                    AgentStatus.AVAILABLE
                )

                self.agent_repository.update(agent)
"""
Call Allocator: Safe agent and borrower allocation.

This is the critical component that prevents two workers
from reserving the same agent or borrower.

Flow:
1. Get available agent
2. Try to reserve agent (atomic)
3. Get available borrower
4. Try to reserve borrower (atomic)
5. Create call
6. If any step fails, release resources

Uses optimistic locking (version field) to ensure
only ONE worker succeeds in reserving each resource.
"""

import uuid
import logging
from datetime import datetime, timezone
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from app.domain.agent import Agent
from app.domain.borrower import Borrower
from app.domain.call import Call
from app.domain.enum import AgentStatus, BorrowerStatus, CallStatus
from app.state_machine.agent_state_machine import AgentStateMachine
from app.state_machine.call_state_machine import CallStateMachine
from app.repositories import (
    AgentRepository,
    BorrowerRepository,
    CallRepository,
    CampaignRepository,
)

logger = logging.getLogger(__name__)


class AllocationException(Exception):
    """Raised when allocation fails."""
    pass


class CallAllocator:
    """
    Safely allocates agents and borrowers to calls.
    
    Guarantees:
    - Two workers cannot allocate the same agent
    - Two workers cannot allocate the same borrower
    - Every call has an agent and borrower (if allocated)
    - Allocation is atomic: all-or-nothing
    """

    def __init__(self, db: Session):
        self.db = db
        self.agent_repo = AgentRepository(db)
        self.borrower_repo = BorrowerRepository(db)
        self.call_repo = CallRepository(db)
        self.campaign_repo = CampaignRepository(db)

    def allocate_call(
        self,
        campaign_id: str,
        provider_name: str,
    ) -> Optional[Call]:
        """
        Allocate an agent and borrower to a call.
        
        This is an ATOMIC operation:
        - Find available agent
        - Reserve agent (must succeed with no conflicts)
        - Find available borrower
        - Reserve borrower (must succeed with no conflicts)
        - Create and return call
        
        If any step fails, clean up and return None.
        
        Args:
            campaign_id: Campaign to allocate for
            provider_name: Name of telecom provider
            
        Returns:
            Call object if successful, None if resources unavailable
        """

        # Step 1: Get available agent
        available_agents = self.agent_repo.get_available_agents(limit=1)
        if not available_agents:
            logger.warning(f"No available agents for campaign {campaign_id}")
            return None

        agent = available_agents[0]

        # Step 2: Reserve agent (ATOMIC - version-based optimistic lock)
        agent = AgentStateMachine.transition(agent, AgentStatus.RESERVED)
        if not self.agent_repo.update(agent):
            logger.warning(f"Failed to reserve agent {agent.id} - concurrent conflict")
            return None

        logger.info(f"Reserved agent {agent.id}")

        try:
            # Step 3: Get available borrower for this campaign
            available_borrowers = self.borrower_repo.get_available_for_campaign(
                campaign_id,
                limit=1,
            )
            if not available_borrowers:
                logger.warning(f"No available borrowers for campaign {campaign_id}")
                # Release agent
                self._release_agent(agent)
                return None

            borrower = available_borrowers[0]

            # Step 4: Reserve borrower (ATOMIC - version-based optimistic lock)
            borrower = self._reserve_borrower(borrower)
            if not self.borrower_repo.update(borrower):
                logger.warning(f"Failed to reserve borrower {borrower.id} - concurrent conflict")
                # Release agent
                self._release_agent(agent)
                return None

            logger.info(f"Reserved borrower {borrower.id}")

            # Step 5: Create call
            call = Call(
                id=str(uuid.uuid4()),
                campaign_id=campaign_id,
                borrower_id=borrower.id,
                agent_id=agent.id,
                status=CallStatus.QUEUED,
                provider_name=provider_name,
            )

            call = self.call_repo.create(call)
            logger.info(f"Created call {call.id} for agent {agent.id} and borrower {borrower.id}")

            # Store call IDs in agent and borrower
            agent.current_call_id = call.id
            borrower.current_call_id = call.id

            agent.version += 1
            borrower.version += 1
            agent.update_timestamp()
            borrower.update_timestamp()

            if not self.agent_repo.update(agent):
                # This should rarely happen, but if it does, we have a stale state
                logger.error(f"Failed to update agent {agent.id} with call ID {call.id}")
                # Try to clean up
                self._release_borrower(borrower)
                return None

            if not self.borrower_repo.update(borrower):
                logger.error(f"Failed to update borrower {borrower.id} with call ID {call.id}")
                # Try to clean up
                self._release_agent(agent)
                return None

            return call

        except Exception as e:
            logger.error(f"Unexpected error during call allocation: {e}")
            # Try to clean up
            self._release_agent(agent)
            return None

    def transition_call(
        self,
        call: Call,
        new_status: CallStatus,
    ) -> bool:
        """
        Safely transition a call to a new status.
        
        Uses optimistic locking to prevent conflicts.
        
        Returns:
            True if successful, False if version conflict
        """
        call = CallStateMachine.transition(call, new_status)
        return self.call_repo.update(call)

    def _reserve_borrower(self, borrower: Borrower) -> Borrower:
        """Mark borrower as RESERVED."""
        borrower.status = BorrowerStatus.RESERVED
        borrower.version += 1
        borrower.update_timestamp()
        return borrower

    def _release_agent(self, agent: Agent) -> None:
        """Release agent back to AVAILABLE state."""
        try:
            agent = AgentStateMachine.transition(agent, AgentStatus.AVAILABLE)
            agent.current_call_id = None
            self.agent_repo.update(agent)
            logger.info(f"Released agent {agent.id}")
        except Exception as e:
            logger.error(f"Failed to release agent {agent.id}: {e}")

    def _release_borrower(self, borrower: Borrower) -> None:
        """Release borrower back to AVAILABLE state."""
        try:
            borrower.status = BorrowerStatus.AVAILABLE
            borrower.current_call_id = None
            borrower.version += 1
            borrower.update_timestamp()
            self.borrower_repo.update(borrower)
            logger.info(f"Released borrower {borrower.id}")
        except Exception as e:
            logger.error(f"Failed to release borrower {borrower.id}: {e}")

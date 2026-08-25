"""
Progressive Dialer: 1 available agent → 1 outbound call

The most conservative approach:
- Never exceed the number of available agents
- Simple, predictable, safe
- Lower utilization but zero risk of abandoned calls
"""

import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.domain.call import Call
from app.domain.enum import CallStatus
from app.dialer.call_allocator import CallAllocator
from app.repositories import AgentRepository, CallRepository

logger = logging.getLogger(__name__)


class ProgressiveDialer:
    """
    Progressive dialing: 1 agent → 1 call.
    
    Rules:
    - Number of outbound calls must never exceed available agents
    - Safe, predictable, but lower utilization
    
    Algorithm:
    1. Count available agents
    2. Count active dialing calls (INITIATED, RINGING, DIALING)
    3. If active < available:
        - Allocate new call (agent + borrower)
        - Mark agent as DIALING
        - Initiate call with provider
    """

    def __init__(self, db: Session, provider_name: str):
        self.db = db
        self.provider_name = provider_name
        self.allocator = CallAllocator(db)
        self.agent_repo = AgentRepository(db)
        self.call_repo = CallRepository(db)

    def dial_next(self, campaign_id: str) -> Optional[Call]:
        """
        Dial the next call in progressive mode.
        
        Returns:
            Call object if a call was allocated and initiated, None otherwise
        """

        # Count available agents
        available_agents = self.agent_repo.count_available_agents()
        
        # Count active dialing calls
        active_calls = self.call_repo.count_by_status(campaign_id, CallStatus.INITIATED)
        active_calls += self.call_repo.count_by_status(campaign_id, CallStatus.RINGING)

        logger.info(
            f"Progressive: available_agents={available_agents}, "
            f"active_dialing={active_calls}"
        )

        # In progressive mode: never exceed available agents
        if active_calls >= available_agents:
            logger.warning(
                f"Cannot dial: active_calls ({active_calls}) >= "
                f"available_agents ({available_agents})"
            )
            return None

        # Try to allocate a call
        call = self.allocator.allocate_call(campaign_id, self.provider_name)

        if call:
            logger.info(f"Progressive dialer allocated call {call.id}")
            return call

        return None

    def get_dial_capacity(self, campaign_id: str) -> int:
        """
        Get how many additional calls can be dialed in progressive mode.
        
        Returns:
            Number of additional calls that can be initiated
        """
        available_agents = self.agent_repo.count_available_agents()
        active_calls = self.call_repo.count_by_status(campaign_id, CallStatus.INITIATED)
        active_calls += self.call_repo.count_by_status(campaign_id, CallStatus.RINGING)

        return max(0, available_agents - active_calls)

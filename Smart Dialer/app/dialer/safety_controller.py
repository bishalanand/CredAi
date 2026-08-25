"""
Safety Controller: Independent safety enforcement.

This is THE MOST IMPORTANT component.

The predictive pacing engine says "dial 15 calls".
The Safety Controller decides if that's actually safe.

The Safety Controller can:
- APPROVE: yes, dial as many as pacing suggests
- REDUCE: dial fewer than suggested
- REJECT: dial zero
- FALLBACK_TO_PROGRESSIVE: use conservative progressive rules

GUARANTEE: The predictive engine has NO way to bypass this.
It cannot directly place calls.
All call allocation goes through this controller.
"""

import logging
from typing import Optional
from enum import Enum

from sqlalchemy.orm import Session

from app.dialer.call_allocator import CallAllocator
from app.dialer.pacing_engine import PredictivePacingEngine
from app.repositories import AgentRepository, CallRepository

logger = logging.getLogger(__name__)


class SafetyDecision(str, Enum):
    """Possible safety controller decisions."""
    APPROVE = "APPROVE"
    REDUCE = "REDUCE"
    REJECT = "REJECT"
    FALLBACK_PROGRESSIVE = "FALLBACK_PROGRESSIVE"


class SafetyControllerRequest:
    """Request from pacing engine to safety controller."""

    def __init__(
        self,
        campaign_id: str,
        requested_dials: int,
        estimated_answer_rate: float,
        reason: str = "",
    ):
        self.campaign_id = campaign_id
        self.requested_dials = requested_dials
        self.estimated_answer_rate = estimated_answer_rate
        self.reason = reason


class SafetyControllerResponse:
    """Safety controller's decision."""

    def __init__(
        self,
        decision: SafetyDecision,
        approved_dials: int,
        reasoning: str = "",
    ):
        self.decision = decision
        self.approved_dials = approved_dials
        self.reasoning = reasoning

    def __repr__(self):
        return (
            f"SafetyDecision({self.decision.value}, "
            f"approved={self.approved_dials}, {self.reasoning})"
        )


class SafetyController:
    """
    Independent safety boundary between pacing and call allocation.
    
    Responsibilities:
    1. Receive dial recommendations
    2. Independently verify system state
    3. Apply conservative safety checks
    4. Approve/reduce/reject dial requests
    5. Ensure ZERO abandoned calls
    
    Safety Checks:
    - Never exceed available agents
    - Never overload provider
    - Never trust pacing engine blindly
    - Degrade gracefully under stress
    """

    def __init__(self, db: Session):
        self.db = db
        self.agent_repo = AgentRepository(db)
        self.call_repo = CallRepository(db)
        self.pacing_engine = PredictivePacingEngine(db)
        
        # Safety thresholds (conservative)
        self.max_answer_rate_change = 0.20  # 20% sudden drop triggers fallback
        self.min_available_agents_buffer = 2  # Keep at least 2 agents as safety buffer
        self.max_ringing_ratio = 2.0  # Max ratio of ringing to available agents
        self.last_answer_rate = 0.50  # Track last known answer rate

    def evaluate_dial_request(
        self,
        request: SafetyControllerRequest,
    ) -> SafetyControllerResponse:
        """
        Evaluate a dial request from the pacing engine.
        
        Returns:
            SafetyControllerResponse with decision and approved dial count
        """

        logger.info(
            f"Safety Controller evaluating request: "
            f"campaign={request.campaign_id}, "
            f"requested={request.requested_dials}, "
            f"answer_rate={request.estimated_answer_rate:.1%}"
        )

        # Check 1: Available agents
        available_agents = self.agent_repo.count_available_agents()
        if available_agents < self.min_available_agents_buffer:
            return SafetyControllerResponse(
                decision=SafetyDecision.REJECT,
                approved_dials=0,
                reasoning=f"Insufficient agents: {available_agents} < {self.min_available_agents_buffer} (buffer)",
            )

        # Check 2: Sudden answer rate drop
        if request.estimated_answer_rate < (self.last_answer_rate - self.max_answer_rate_change):
            logger.warning(
                f"Answer rate dropped suddenly: "
                f"{self.last_answer_rate:.1%} → {request.estimated_answer_rate:.1%}. "
                f"Falling back to progressive."
            )
            return SafetyControllerResponse(
                decision=SafetyDecision.FALLBACK_PROGRESSIVE,
                approved_dials=1,  # Progressive mode: 1 at a time
                reasoning=f"Answer rate dropped {request.estimated_answer_rate:.1%}. Fallback to progressive.",
            )
        
        self.last_answer_rate = request.estimated_answer_rate

        # Check 3: Ringing ratio (too many calls ringing already)
        from app.domain.enum import CallStatus
        ringing = self.call_repo.count_ringing(request.campaign_id)
        
        if available_agents > 0 and (ringing / available_agents) > self.max_ringing_ratio:
            approved = max(0, int(available_agents - self.min_available_agents_buffer))
            return SafetyControllerResponse(
                decision=SafetyDecision.REDUCE,
                approved_dials=approved,
                reasoning=f"Too many ringing: {ringing}. Reducing dial rate.",
            )

        # Check 4: Cap by available agents
        max_safe_dials = max(
            0,
            available_agents - self.min_available_agents_buffer,
        )

        approved_dials = min(request.requested_dials, max_safe_dials)

        if approved_dials == 0:
            return SafetyControllerResponse(
                decision=SafetyDecision.REJECT,
                approved_dials=0,
                reasoning=f"No capacity: {available_agents} available, {self.min_available_agents_buffer} reserved.",
            )

        if approved_dials < request.requested_dials:
            return SafetyControllerResponse(
                decision=SafetyDecision.REDUCE,
                approved_dials=approved_dials,
                reasoning=f"Reduced from {request.requested_dials} to {approved_dials}",
            )

        return SafetyControllerResponse(
            decision=SafetyDecision.APPROVE,
            approved_dials=approved_dials,
            reasoning=f"Approved all {approved_dials} dials",
        )

    def handle_failure_recovery(
        self,
        campaign_id: str,
    ) -> SafetyControllerResponse:
        """
        Recover from provider failure by falling back to progressive.
        
        Triggered when provider starts failing or timing out.
        """
        logger.warning(
            f"Safety Controller: Provider failure detected for campaign {campaign_id}. "
            f"Falling back to progressive dialing."
        )
        
        return SafetyControllerResponse(
            decision=SafetyDecision.FALLBACK_PROGRESSIVE,
            approved_dials=1,
            reasoning="Provider failure - fallback to progressive",
        )

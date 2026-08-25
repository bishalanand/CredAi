"""
Predictive Pacing Engine: Estimate how many calls can safely be dialed.

Mathematical Model:

Let:
  A = available agents (AVAILABLE status)
  R = agents currently dialing/ringing
  p = estimated answer probability (0.0-1.0)
  T = average talk time (seconds)
  S = setup/ring time (seconds)
  C = connected calls

Then the system can theoretically support:
  In time T, if p × N borrowers answer and talk for time T,
  then we need p × N agents tied up for T seconds.
  
  If we already have C connected, we can support up to A total agents in calls.
  So we can start at most: A - C more calls.
  
  But we also need to account for calls currently ringing:
  Some of those will answer (expected: p × R answer).
  
  Conservative formula:
  safe_dials = A - (C + p × R)
  
This is a "flow control" approach:
- Count existing load (C connected)
- Count expected load from ringing calls (p × R)
- Add margin for safety
- Allow new dials only if we have agent capacity
"""

import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.domain.enum import CallStatus
from app.repositories import AgentRepository, CallRepository

logger = logging.getLogger(__name__)


class PacingMetrics:
    """Metrics used for pacing calculation."""

    def __init__(
        self,
        campaign_id: str,
        available_agents: int,
        reserved_agents: int,
        dialing_agents: int,
        ringing_calls: int,
        connected_calls: int,
        estimated_answer_rate: float,
        estimated_talk_duration_sec: float,
        estimated_setup_time_sec: float = 5.0,
        provider_health_score: float = 1.0,
    ):
        self.campaign_id = campaign_id
        self.available_agents = available_agents
        self.reserved_agents = reserved_agents
        self.dialing_agents = dialing_agents
        self.ringing_calls = ringing_calls
        self.connected_calls = connected_calls
        self.estimated_answer_rate = estimated_answer_rate
        self.estimated_talk_duration_sec = estimated_talk_duration_sec
        self.estimated_setup_time_sec = estimated_setup_time_sec
        self.provider_health_score = provider_health_score


class PredictivePacingEngine:
    """
    Predictive pacing: Estimate safe dial volume based on system metrics.
    
    Does NOT directly place calls.
    Only provides recommendations to Safety Controller.
    
    Algorithm:
    1. Collect system metrics (available agents, active calls, etc.)
    2. Estimate answer rate and talk duration
    3. Calculate expected load from ringing/answering calls
    4. Compute safe additional dials
    """

    def __init__(self, db: Session):
        self.db = db
        self.agent_repo = AgentRepository(db)
        self.call_repo = CallRepository(db)
        
        # Configuration (can be tuned)
        self.safety_margin = 0.1  # 10% margin
        self.min_idle_agents = 1  # Keep at least 1 agent idle

    def calculate_dial_recommendation(
        self,
        campaign_id: str,
        estimated_answer_rate: float = 0.50,
        estimated_talk_duration_sec: float = 120.0,
        estimated_setup_time_sec: float = 5.0,
    ) -> int:
        """
        Calculate how many additional calls can be dialed.
        
        This is a RECOMMENDATION only.
        The Safety Controller makes the final decision.
        
        Args:
            campaign_id: Campaign to calculate for
            estimated_answer_rate: P(borrower answers) from historical data
            estimated_talk_duration_sec: Average call duration
            estimated_setup_time_sec: Time until ANSWERED (ring time)
            
        Returns:
            Recommended number of additional calls to dial (may be 0)
        """

        # Collect metrics
        metrics = self._collect_metrics(
            campaign_id,
            estimated_answer_rate,
            estimated_talk_duration_sec,
            estimated_setup_time_sec,
        )

        # Calculate recommendation
        recommendation = self._calculate_recommendation(metrics)

        logger.info(
            f"Pacing recommendation for campaign {campaign_id}: "
            f"dial {recommendation} additional calls "
            f"(available: {metrics.available_agents}, "
            f"ringing: {metrics.ringing_calls}, "
            f"connected: {metrics.connected_calls}, "
            f"answer_rate: {metrics.estimated_answer_rate:.1%})"
        )

        return recommendation

    def get_metrics(
        self,
        campaign_id: str,
        estimated_answer_rate: float = 0.50,
        estimated_talk_duration_sec: float = 120.0,
        estimated_setup_time_sec: float = 5.0,
    ) -> PacingMetrics:
        """Get current system metrics for a campaign."""
        return self._collect_metrics(
            campaign_id,
            estimated_answer_rate,
            estimated_talk_duration_sec,
            estimated_setup_time_sec,
        )

    def _collect_metrics(
        self,
        campaign_id: str,
        estimated_answer_rate: float,
        estimated_talk_duration_sec: float,
        estimated_setup_time_sec: float,
    ) -> PacingMetrics:
        """Collect all system metrics."""

        # Agent counts
        available = self.agent_repo.count_available_agents()
        reserved = self.agent_repo.count_by_status(AgentStatus.RESERVED)
        dialing = self.agent_repo.count_by_status(AgentStatus.DIALING)

        # Call counts
        ringing = self.call_repo.count_ringing(campaign_id)
        connected = self.call_repo.count_connected(campaign_id)

        # TODO: Get from database/historical data
        provider_health = 1.0  # Assume healthy for now

        return PacingMetrics(
            campaign_id=campaign_id,
            available_agents=available,
            reserved_agents=reserved,
            dialing_agents=dialing,
            ringing_calls=ringing,
            connected_calls=connected,
            estimated_answer_rate=estimated_answer_rate,
            estimated_talk_duration_sec=estimated_talk_duration_sec,
            estimated_setup_time_sec=estimated_setup_time_sec,
            provider_health_score=provider_health,
        )

    def _calculate_recommendation(self, metrics: PacingMetrics) -> int:
        """
        Calculate dial recommendation using flow-control formula.
        
        Formula:
        --------
        total_agents = available + reserved + dialing
        
        expected_connected_soon = connected + (answer_rate × ringing)
        
        safe_dials = total_agents - expected_connected_soon - safety_margin
        
        safe_dials = max(0, safe_dials)
        
        Intuition:
        - We have total_agents agents
        - Some are already connected
        - Some ringing calls will answer (expected: answer_rate × ringing)
        - We need to account for both before starting more calls
        """

        total_agents = (
            metrics.available_agents
            + metrics.reserved_agents
            + metrics.dialing_agents
        )

        # Expected load from existing calls
        expected_connected_soon = (
            metrics.connected_calls
            + (metrics.estimated_answer_rate * metrics.ringing_calls)
        )

        # Safety margin and idle agent buffer
        safety_overhead = int(
            (total_agents * self.safety_margin) + self.min_idle_agents
        )

        # Calculate safe dials
        safe_dials = int(
            total_agents - expected_connected_soon - safety_overhead
        )

        return max(0, safe_dials)


# Import needed for _collect_metrics
from app.domain.enum import AgentStatus

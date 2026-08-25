"""
Repository layer for Call persistence.

Handles:
- Call state tracking
- Provider event correlation (provider_call_id → call_id)
- Optimistic locking for concurrent updates
"""

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.models import CallModel
from app.domain.call import Call
from app.domain.enum import CallStatus
from typing import Optional, List
import logging

logger = logging.getLogger(__name__)


class CallRepository:
    """
    Data access layer for calls.
    
    Handles:
    - Call state transitions
    - Provider event tracking
    - Optimistic locking
    """

    def __init__(self, db: Session):
        self.db = db

    def create(self, call: Call) -> Call:
        """Create a new call."""
        model = CallModel(
            id=call.id,
            campaign_id=call.campaign_id,
            borrower_id=call.borrower_id,
            agent_id=call.agent_id,
            status=call.status,
            provider_name=call.provider_name,
            provider_call_id=call.provider_call_id,
            failure_reason=call.failure_reason,
            initiated_at=call.initiated_at,
            answered_at=call.answered_at,
            connected_at=call.connected_at,
            completed_at=call.completed_at,
            version=call.version,
        )
        self.db.add(model)
        self.db.commit()
        self.db.refresh(model)
        return self._to_domain(model)

    def get_by_id(self, call_id: str) -> Optional[Call]:
        """Get call by ID."""
        model = self.db.query(CallModel).filter(CallModel.id == call_id).first()
        return self._to_domain(model) if model else None

    def get_by_provider_call_id(self, provider_call_id: str) -> Optional[Call]:
        """
        Get call by provider's call ID.
        
        Used when processing provider events.
        """
        model = self.db.query(CallModel).filter(
            CallModel.provider_call_id == provider_call_id
        ).first()
        return self._to_domain(model) if model else None

    def get_by_campaign(self, campaign_id: str) -> List[Call]:
        """Get all calls for a campaign."""
        models = self.db.query(CallModel).filter(
            CallModel.campaign_id == campaign_id
        ).all()
        return [self._to_domain(m) for m in models]

    def get_by_agent(self, agent_id: str) -> List[Call]:
        """Get all calls for an agent."""
        models = self.db.query(CallModel).filter(
            CallModel.agent_id == agent_id
        ).all()
        return [self._to_domain(m) for m in models]

    def count_by_status(self, campaign_id: str, status: CallStatus) -> int:
        """Count calls by status in a campaign."""
        return self.db.query(CallModel).filter(
            CallModel.campaign_id == campaign_id,
            CallModel.status == status,
        ).count()

    def count_ringing(self, campaign_id: str) -> int:
        """Count ringing calls (INITIATED or RINGING status)."""
        return self.db.query(CallModel).filter(
            CallModel.campaign_id == campaign_id,
            CallModel.status.in_([CallStatus.INITIATED, CallStatus.RINGING]),
        ).count()

    def count_connected(self, campaign_id: str) -> int:
        """Count connected calls (ANSWERED or CONNECTED status)."""
        return self.db.query(CallModel).filter(
            CallModel.campaign_id == campaign_id,
            CallModel.status.in_([CallStatus.ANSWERED, CallStatus.CONNECTED]),
        ).count()

    def update(self, call: Call) -> bool:
        """
        Update call with optimistic locking.
        
        Returns:
            True if updated, False if version conflict
        """
        model = self.db.query(CallModel).filter(
            CallModel.id == call.id,
            CallModel.version == call.version - 1,
        ).first()

        if not model:
            logger.warning(
                f"Call {call.id} version conflict. "
                f"Expected v{call.version - 1}"
            )
            return False

        model.status = call.status
        model.agent_id = call.agent_id
        model.provider_name = call.provider_name
        model.provider_call_id = call.provider_call_id
        model.failure_reason = call.failure_reason
        model.initiated_at = call.initiated_at
        model.answered_at = call.answered_at
        model.connected_at = call.connected_at
        model.completed_at = call.completed_at
        model.version = call.version
        model.updated_at = call.updated_at

        try:
            self.db.commit()
            self.db.refresh(model)
            return True
        except IntegrityError as e:
            self.db.rollback()
            logger.error(f"Integrity error updating call {call.id}: {e}")
            return False

    def delete(self, call_id: str) -> bool:
        """Delete a call (for testing)."""
        self.db.query(CallModel).filter(CallModel.id == call_id).delete()
        self.db.commit()
        return True

    def _to_domain(self, model: CallModel) -> Call:
        """Convert database model to domain object."""
        if not model:
            return None
        return Call(
            id=model.id,
            campaign_id=model.campaign_id,
            borrower_id=model.borrower_id,
            agent_id=model.agent_id,
            status=model.status,
            provider_name=model.provider_name,
            provider_call_id=model.provider_call_id,
            failure_reason=model.failure_reason,
            created_at=model.created_at,
            updated_at=model.updated_at,
            initiated_at=model.initiated_at,
            answered_at=model.answered_at,
            connected_at=model.connected_at,
            completed_at=model.completed_at,
            version=model.version,
        )

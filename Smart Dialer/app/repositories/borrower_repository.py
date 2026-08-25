"""
Repository layer for Borrower persistence.

Handles:
- Concurrent borrower allocation
- Safe borrower state transitions
- Optimistic locking
"""

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.models import BorrowerModel
from app.domain.borrower import Borrower
from app.domain.enum import BorrowerStatus
from typing import Optional, List
import logging

logger = logging.getLogger(__name__)


class BorrowerRepository:
    """
    Data access layer for borrowers.
    
    Handles:
    - Atomic borrower state transitions
    - Concurrent allocation (only ONE worker can reserve a borrower)
    - Optimistic locking with version field
    """

    def __init__(self, db: Session):
        self.db = db

    def create(self, borrower: Borrower) -> Borrower:
        """Create a new borrower."""
        model = BorrowerModel(
            id=borrower.id,
            phone_number=borrower.phone_number,
            status=borrower.status,
            campaign_id=borrower.campaign_id,
            current_call_id=borrower.current_call_id,
            version=borrower.version,
        )
        self.db.add(model)
        self.db.commit()
        self.db.refresh(model)
        return self._to_domain(model)

    def get_by_id(self, borrower_id: str) -> Optional[Borrower]:
        """Get borrower by ID."""
        model = self.db.query(BorrowerModel).filter(
            BorrowerModel.id == borrower_id
        ).first()
        return self._to_domain(model) if model else None

    def get_available_for_campaign(
        self,
        campaign_id: str,
        limit: int = 1000,
    ) -> List[Borrower]:
        """Get available borrowers for a campaign."""
        models = (
            self.db.query(BorrowerModel)
            .filter(
                BorrowerModel.campaign_id == campaign_id,
                BorrowerModel.status == BorrowerStatus.AVAILABLE,
            )
            .limit(limit)
            .all()
        )
        return [self._to_domain(m) for m in models]

    def count_available_for_campaign(self, campaign_id: str) -> int:
        """Count available borrowers for a campaign."""
        return self.db.query(BorrowerModel).filter(
            BorrowerModel.campaign_id == campaign_id,
            BorrowerModel.status == BorrowerStatus.AVAILABLE,
        ).count()

    def count_by_status(self, campaign_id: str, status: BorrowerStatus) -> int:
        """Count borrowers by status in a campaign."""
        return self.db.query(BorrowerModel).filter(
            BorrowerModel.campaign_id == campaign_id,
            BorrowerModel.status == status,
        ).count()

    def update(self, borrower: Borrower) -> bool:
        """
        Update borrower with optimistic locking.
        
        Returns:
            True if updated, False if version conflict
        """
        model = self.db.query(BorrowerModel).filter(
            BorrowerModel.id == borrower.id,
            BorrowerModel.version == borrower.version - 1,
        ).first()

        if not model:
            logger.warning(
                f"Borrower {borrower.id} version conflict. "
                f"Expected v{borrower.version - 1}"
            )
            return False

        model.status = borrower.status
        model.campaign_id = borrower.campaign_id
        model.current_call_id = borrower.current_call_id
        model.version = borrower.version
        model.updated_at = borrower.updated_at

        try:
            self.db.commit()
            self.db.refresh(model)
            return True
        except IntegrityError as e:
            self.db.rollback()
            logger.error(f"Integrity error updating borrower {borrower.id}: {e}")
            return False

    def delete(self, borrower_id: str) -> bool:
        """Delete a borrower (for testing)."""
        self.db.query(BorrowerModel).filter(BorrowerModel.id == borrower_id).delete()
        self.db.commit()
        return True

    def _to_domain(self, model: BorrowerModel) -> Borrower:
        """Convert database model to domain object."""
        if not model:
            return None
        return Borrower(
            id=model.id,
            phone_number=model.phone_number,
            status=model.status,
            campaign_id=model.campaign_id,
            current_call_id=model.current_call_id,
            created_at=model.created_at,
            updated_at=model.updated_at,
            version=model.version,
        )

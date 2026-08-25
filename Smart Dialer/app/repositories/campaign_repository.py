"""
Repository layer for Campaign persistence.
"""

from sqlalchemy.orm import Session
from app.models import CampaignModel
from app.domain.campaign import Campaign
from typing import Optional, List
import logging

logger = logging.getLogger(__name__)


class CampaignRepository:
    """Data access layer for campaigns."""

    def __init__(self, db: Session):
        self.db = db

    def create(self, campaign: Campaign) -> Campaign:
        """Create a new campaign."""
        model = CampaignModel(
            id=campaign.id,
            name=campaign.name,
            dialing_mode=campaign.dialing_mode,
            active=1 if campaign.active else 0,
        )
        self.db.add(model)
        self.db.commit()
        self.db.refresh(model)
        return self._to_domain(model)

    def get_by_id(self, campaign_id: str) -> Optional[Campaign]:
        """Get campaign by ID."""
        model = self.db.query(CampaignModel).filter(
            CampaignModel.id == campaign_id
        ).first()
        return self._to_domain(model) if model else None

    def get_all(self) -> List[Campaign]:
        """Get all campaigns."""
        models = self.db.query(CampaignModel).all()
        return [self._to_domain(m) for m in models]

    def update(self, campaign: Campaign) -> bool:
        """Update campaign."""
        model = self.db.query(CampaignModel).filter(
            CampaignModel.id == campaign.id
        ).first()

        if not model:
            return False

        model.name = campaign.name
        model.dialing_mode = campaign.dialing_mode
        model.active = 1 if campaign.active else 0
        model.updated_at = campaign.updated_at

        self.db.commit()
        self.db.refresh(model)
        return True

    def delete(self, campaign_id: str) -> bool:
        """Delete a campaign (for testing)."""
        self.db.query(CampaignModel).filter(CampaignModel.id == campaign_id).delete()
        self.db.commit()
        return True

    def _to_domain(self, model: CampaignModel) -> Campaign:
        """Convert database model to domain object."""
        if not model:
            return None
        return Campaign(
            id=model.id,
            name=model.name,
            dialing_mode=model.dialing_mode,
            active=bool(model.active),
        )

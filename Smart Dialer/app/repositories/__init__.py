"""Repositories package."""

from .agent_repository import AgentRepository
from .borrower_repository import BorrowerRepository
from .call_repository import CallRepository
from .campaign_repository import CampaignRepository

__all__ = [
    "AgentRepository",
    "BorrowerRepository",
    "CallRepository",
    "CampaignRepository",
]

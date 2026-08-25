"""
Repository layer for Agent persistence and concurrent access.

Uses optimistic locking (version) + database constraints
to handle concurrent reservations safely.
"""

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.models import AgentModel
from app.domain.agent import Agent
from app.domain.enum import AgentStatus
from typing import Optional, List
import logging

logger = logging.getLogger(__name__)


class AgentRepository:
    """
    Data access layer for agents.
    
    Handles:
    - Atomic agent state transitions
    - Concurrent reservation (only ONE worker can reserve an agent)
    - Optimistic locking with version field
    """

    def __init__(self, db: Session):
        self.db = db

    def create(self, agent: Agent) -> Agent:
        """Create a new agent."""
        model = AgentModel(
            id=agent.id,
            status=agent.status,
            current_call_id=agent.current_call_id,
            version=agent.version,
        )
        self.db.add(model)
        self.db.commit()
        self.db.refresh(model)
        return self._to_domain(model)

    def get_by_id(self, agent_id: str) -> Optional[Agent]:
        """Get agent by ID."""
        model = self.db.query(AgentModel).filter(AgentModel.id == agent_id).first()
        return self._to_domain(model) if model else None

    def get_available_agents(self, limit: int = 1000) -> List[Agent]:
        """Get all agents with AVAILABLE status."""
        models = (
            self.db.query(AgentModel)
            .filter(AgentModel.status == AgentStatus.AVAILABLE)
            .limit(limit)
            .all()
        )
        return [self._to_domain(m) for m in models]

    def count_available_agents(self) -> int:
        """Count available agents."""
        return self.db.query(AgentModel).filter(
            AgentModel.status == AgentStatus.AVAILABLE
        ).count()

    def count_by_status(self, status: AgentStatus) -> int:
        """Count agents by status."""
        return self.db.query(AgentModel).filter(
            AgentModel.status == status
        ).count()

    def update(self, agent: Agent) -> bool:
        """
        Update agent with optimistic locking.
        
        Uses version field: only succeed if database version matches.
        
        Returns:
            True if updated, False if version conflict (concurrent update)
        """
        model = self.db.query(AgentModel).filter(
            AgentModel.id == agent.id,
            AgentModel.version == agent.version - 1,  # Check version before increment
        ).first()

        if not model:
            logger.warning(
                f"Agent {agent.id} version conflict. "
                f"Expected v{agent.version - 1}, agent may have been updated."
            )
            return False

        model.status = agent.status
        model.current_call_id = agent.current_call_id
        model.version = agent.version
        model.updated_at = agent.updated_at

        try:
            self.db.commit()
            self.db.refresh(model)
            return True
        except IntegrityError as e:
            self.db.rollback()
            logger.error(f"Integrity error updating agent {agent.id}: {e}")
            return False

    def delete(self, agent_id: str) -> bool:
        """Delete an agent (for testing)."""
        self.db.query(AgentModel).filter(AgentModel.id == agent_id).delete()
        self.db.commit()
        return True

    def _to_domain(self, model: AgentModel) -> Agent:
        """Convert database model to domain object."""
        if not model:
            return None
        return Agent(
            id=model.id,
            status=model.status,
            current_call_id=model.current_call_id,
            created_at=model.created_at,
            updated_at=model.updated_at,
            version=model.version,
        )

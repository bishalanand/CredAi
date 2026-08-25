"""
SQLAlchemy models for persistence.

These models map domain objects to database tables.
We use optimistic locking (version field) + unique constraints
to handle concurrent operations safely.
"""

from sqlalchemy import Column, String, Enum, Integer, DateTime, ForeignKey, UniqueConstraint, Index
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import uuid

from app.db import Base
from app.domain.enum import AgentStatus, BorrowerStatus, CallStatus, DialingMode


def generate_uuid():
    """Generate UUID for IDs."""
    return str(uuid.uuid4())


def utc_now():
    """Current UTC time."""
    return datetime.now(timezone.utc)


class AgentModel(Base):
    """Persistent Agent model."""
    
    __tablename__ = "agents"

    id = Column(String, primary_key=True, default=generate_uuid)
    
    status = Column(Enum(AgentStatus), default=AgentStatus.OFFLINE, nullable=False)
    current_call_id = Column(String, nullable=True)
    
    created_at = Column(DateTime, default=utc_now, nullable=False)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)
    
    version = Column(Integer, default=0, nullable=False)

    # Relationships
    calls = relationship("CallModel", back_populates="agent", foreign_keys="CallModel.agent_id")

    def __repr__(self):
        return f"<Agent {self.id} status={self.status} v={self.version}>"


class BorrowerModel(Base):
    """Persistent Borrower model."""
    
    __tablename__ = "borrowers"

    id = Column(String, primary_key=True, default=generate_uuid)
    phone_number = Column(String, nullable=False)
    
    status = Column(Enum(BorrowerStatus), default=BorrowerStatus.AVAILABLE, nullable=False)
    
    campaign_id = Column(String, ForeignKey("campaigns.id"), nullable=True)
    current_call_id = Column(String, nullable=True)
    
    created_at = Column(DateTime, default=utc_now, nullable=False)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)
    
    version = Column(Integer, default=0, nullable=False)

    # Relationships
    campaign = relationship("CampaignModel", back_populates="borrowers")
    calls = relationship("CallModel", back_populates="borrower", foreign_keys="CallModel.borrower_id")

    def __repr__(self):
        return f"<Borrower {self.id} phone={self.phone_number} v={self.version}>"


class CallModel(Base):
    """Persistent Call model."""
    
    __tablename__ = "calls"

    id = Column(String, primary_key=True, default=generate_uuid)
    
    campaign_id = Column(String, ForeignKey("campaigns.id"), nullable=False)
    borrower_id = Column(String, ForeignKey("borrowers.id"), nullable=False)
    agent_id = Column(String, ForeignKey("agents.id"), nullable=True)
    
    status = Column(Enum(CallStatus), default=CallStatus.QUEUED, nullable=False)
    
    provider_name = Column(String, nullable=True)
    provider_call_id = Column(String, nullable=True)
    
    failure_reason = Column(String, nullable=True)
    
    created_at = Column(DateTime, default=utc_now, nullable=False)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)
    
    initiated_at = Column(DateTime, nullable=True)
    answered_at = Column(DateTime, nullable=True)
    connected_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    
    version = Column(Integer, default=0, nullable=False)

    # Relationships
    campaign = relationship("CampaignModel", back_populates="calls")
    borrower = relationship("BorrowerModel", back_populates="calls", foreign_keys=[borrower_id])
    agent = relationship("AgentModel", back_populates="calls", foreign_keys=[agent_id])

    # Indexes for fast lookups
    __table_args__ = (
        Index("ix_calls_campaign_id", "campaign_id"),
        Index("ix_calls_borrower_id", "borrower_id"),
        Index("ix_calls_agent_id", "agent_id"),
        Index("ix_calls_status", "status"),
    )

    def __repr__(self):
        return f"<Call {self.id} agent={self.agent_id} borrower={self.borrower_id} status={self.status} v={self.version}>"


class CampaignModel(Base):
    """Persistent Campaign model."""
    
    __tablename__ = "campaigns"

    id = Column(String, primary_key=True, default=generate_uuid)
    name = Column(String, nullable=False)
    
    dialing_mode = Column(Enum(DialingMode), default=DialingMode.PROGRESSIVE, nullable=False)
    active = Column(Integer, default=False, nullable=False)  # SQLite bool workaround
    
    created_at = Column(DateTime, default=utc_now, nullable=False)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)

    # Relationships
    borrowers = relationship("BorrowerModel", back_populates="campaign")
    calls = relationship("CallModel", back_populates="campaign")

    def __repr__(self):
        return f"<Campaign {self.id} name={self.name} mode={self.dialing_mode}>"

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .enum import CallStatus


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Call:
    id: str

    campaign_id: str
    borrower_id: str

    agent_id: Optional[str] = None

    status: CallStatus = CallStatus.QUEUED

    provider_name: Optional[str] = None
    provider_call_id: Optional[str] = None

    failure_reason: Optional[str] = None

    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    initiated_at: Optional[datetime] = None
    answered_at: Optional[datetime] = None
    connected_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    version: int = 0

    def update_timestamp(self) -> None:
        self.updated_at = utc_now()
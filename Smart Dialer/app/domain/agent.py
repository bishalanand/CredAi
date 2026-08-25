from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .enum import AgentStatus


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Agent:
    id: str
    status: AgentStatus = AgentStatus.OFFLINE

    current_call_id: Optional[str] = None

    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    #we use version for concurrency controls on two agents
    version: int = 0

    def update_timestamp(self) -> None:
        self.updated_at = utc_now()
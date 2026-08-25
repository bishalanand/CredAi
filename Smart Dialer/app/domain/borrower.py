from dataclasses import dataclass, field
from datetime import datetime, timezone

from .enum import BorrowerStatus


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Borrower:
    id: str
    phone_number: str

    status: BorrowerStatus = BorrowerStatus.AVAILABLE

    campaign_id: str | None = None
    current_call_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    version: int = 0

    def update_timestamp(self) -> None:
        self.updated_at = utc_now()
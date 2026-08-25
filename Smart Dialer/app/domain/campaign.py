from dataclasses import dataclass, field
from datetime import datetime, timezone

from .enum import DialingMode


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Campaign:
    id: str
    name: str

    dialing_mode: DialingMode = DialingMode.PROGRESSIVE

    active: bool = False

    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def update_timestamp(self) -> None:
        self.updated_at = utc_now()
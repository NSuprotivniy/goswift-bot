from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Slot:
    """Represents a single available timeslot on the border queue."""

    id: str
    date_time: datetime
    location_key: str
    checkpoint: str | None
    direction: str | None
    booking_url: str


@dataclass
class LastRunInfo:
    last_check_time: datetime | None = None
    last_error: str | None = None
    last_slots_found: int = 0

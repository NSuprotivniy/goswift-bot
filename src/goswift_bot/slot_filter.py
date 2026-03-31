from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, List, Set

from .models import Slot


@dataclass
class SlotFilter:
    """Keeps track of which slots have already been notified and applies simple filters."""

    notified_ids: Set[str] = field(default_factory=set)

    def filter_new(self, slots: Iterable[Slot]) -> List[Slot]:
        """Return only slots that have not yet been notified, update internal set."""
        new: List[Slot] = []
        for slot in slots:
            if slot.id in self.notified_ids:
                continue
            self.notified_ids.add(slot.id)
            new.append(slot)
        # Sort new slots by datetime for nicer messages.
        new.sort(key=lambda s: s.date_time)
        return new

    def reset(self) -> None:
        self.notified_ids.clear()


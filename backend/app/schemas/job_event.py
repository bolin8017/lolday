"""Response schemas for the ``/jobs/{id}/events`` HTTP endpoint.

See also :mod:`app.services.events_tail` for the broadcast/persistence
contract.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class JobEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ts: datetime
    kind: str
    payload: dict[str, Any]


class JobEventsPage(BaseModel):
    """One page of events plus a ``(next_since, next_id)`` cursor.

    The composite cursor survives microsecond-collision timestamps —
    using just ``next_since`` with a ``>`` filter would skip any event
    whose ``ts`` exactly equalled the previous page's last event.
    """

    events: list[JobEventOut]
    next_since: datetime | None = None
    next_id: uuid.UUID | None = None

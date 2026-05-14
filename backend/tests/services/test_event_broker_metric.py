"""EVENT_BROKER_DROPS_TOTAL must increment exactly once per drop."""

import uuid

import pytest
from prometheus_client import REGISTRY


def _read(metric: str) -> float:
    v = REGISTRY.get_sample_value(metric)
    return 0.0 if v is None else v


async def test_event_broker_drops_total_increments_on_overflow():
    from app.services.events_tail import EventBroker

    broker = EventBroker()
    job_id = uuid.uuid4()
    q = broker.subscribe(job_id)

    # Saturate the queue (maxsize=1000); the 1001st publish triggers drop-oldest.
    for i in range(1000):
        q.put_nowait({"i": i, "kind": "fill"})

    before = _read("lolday_event_broker_drops_total")
    await broker.publish(job_id, {"kind": "overflow", "id": "boom"})
    after = _read("lolday_event_broker_drops_total")

    assert after - before == pytest.approx(1.0)
    # And the overflow event reached the subscriber after one drop.
    drained = []
    while not q.empty():
        drained.append(q.get_nowait())
    assert drained[-1]["kind"] == "overflow"

"""L-experiment-stats-lock: _stats_locks is a WeakValueDictionary -- entries GC'd when refs drop."""

import asyncio
import gc


async def test_stats_locks_garbage_collected_after_local_refs_drop():
    """After seeding 50 locks and dropping references, WeakValueDictionary shrinks."""
    from app.routers import experiments_proxy

    assert type(experiments_proxy._stats_locks).__name__ == "WeakValueDictionary", (
        "_stats_locks must be a WeakValueDictionary"
    )

    initial = len(experiments_proxy._stats_locks)

    # Seed 50 locks, hold them only locally.
    keys = [f"exp_{i}" for i in range(50)]
    locks = [experiments_proxy._stats_locks.setdefault(k, asyncio.Lock()) for k in keys]
    assert len(experiments_proxy._stats_locks) >= initial + 50

    # Drop local strong refs and run GC.
    del locks
    gc.collect()

    # WeakValueDictionary should shrink back to near initial.
    assert len(experiments_proxy._stats_locks) <= initial + 5, (
        "WeakValueDictionary did not reclaim entries after refs dropped"
    )

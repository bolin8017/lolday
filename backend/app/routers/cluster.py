"""Cluster-level status endpoints consumed by the lolday frontend."""

import asyncio

from fastapi import APIRouter, Depends

from app.services.cluster_status import get_gpu_allocation, get_queue_depth
from app.users import current_active_user

router = APIRouter()


@router.get("/gpu-status")
async def gpu_status(_user=Depends(current_active_user)) -> dict:
    # Sync K8s client wrapped via ``asyncio.to_thread`` so a slow API server
    # cannot block the asyncio event loop; the @cached TTLCache decorator
    # on get_gpu_allocation continues to absorb the per-request fan-out.
    return await asyncio.to_thread(get_gpu_allocation)


@router.get("/queue")
async def queue_status(_user=Depends(current_active_user)) -> dict:
    return {"depth": await asyncio.to_thread(get_queue_depth)}

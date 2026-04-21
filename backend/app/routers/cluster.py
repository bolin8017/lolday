"""Cluster-level status endpoints consumed by the lolday frontend."""

from fastapi import APIRouter, Depends

from app.services.cluster_status import get_gpu_allocation, get_queue_depth
from app.users import current_active_user

router = APIRouter()


@router.get("/gpu-status")
async def gpu_status(_user=Depends(current_active_user)) -> dict:
    return get_gpu_allocation()


@router.get("/queue")
async def queue_status(_user=Depends(current_active_user)) -> dict:
    return {"depth": get_queue_depth()}

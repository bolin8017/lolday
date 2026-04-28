import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from kubernetes.client import ApiException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_async_session
from app.deps import require_detector_access, require_role
from app.services.rate_limit import rate_limit_user
from app.metrics import BACKEND_ERRORS
from app.models import Job, NON_TERMINAL_STATUSES, Role, User
from app.models.credential import UserGitCredential
from app.models.detector import (
    Detector,
    DetectorBuild,
    DetectorBuildStatus,
    DetectorVersion,
    DetectorVersionStatus,
)

logger = logging.getLogger(__name__)
from app.schemas.detector import (
    AvailableTag,
    BuildCreate,
    BuildRead,
    DetectorCreate,
    DetectorRead,
    DetectorUpdate,
    VersionDetailRead,
    VersionRead,
)
from app.services.build import (
    build_git_credential_secret,
    build_job_name,
    build_job_spec,
    build_secret_name,
)
from app.services.crypto import TokenCipher
from app.services.git import (
    check_repo_accessible,
    list_remote_tags,
    normalize_git_url,
    parse_github_owner_repo,
)
from app.services.harbor import HarborClient
from app.services.k8s import batch_v1, core_v1
from app.services.validator import StaticValidationError, validate_repo_static
from app.users import current_active_user

router = APIRouter()


async def _get_user_pat(session: AsyncSession, user_id: UUID) -> str | None:
    cred = await session.get(UserGitCredential, user_id)
    if cred is None:
        return None
    return TokenCipher(settings.FERNET_KEY).decrypt(cred.encrypted_token)


async def _clone_and_validate(normalized_url: str, pat: str | None) -> dict:
    """Synchronously clone shallow + run static validation.

    Returns metadata dict {name, description, display_name}.
    Raises HTTPException on failure.
    """
    owner, repo = parse_github_owner_repo(normalized_url)

    # Pre-flight: check repo accessibility via API
    ok = await check_repo_accessible(owner, repo, pat)
    if not ok:
        if pat is None:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "credential_missing",
                    "message": "repo not public; PAT required",
                },
            )
        raise HTTPException(
            status_code=400,
            detail={
                "code": "git_clone_failed",
                "message": "repo not accessible with PAT",
            },
        )

    tmpdir = tempfile.mkdtemp(prefix="lolday-register-")
    try:
        url_with_cred = (
            f"https://{pat}@github.com/{owner}/{repo}.git"
            if pat
            else f"https://github.com/{owner}/{repo}.git"
        )
        proc = await asyncio.create_subprocess_exec(
            "git",
            "clone",
            "--depth=1",
            url_with_cred,
            tmpdir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env={
                **os.environ,
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_ASKPASS": "",
            },
        )
        try:
            _, err = await asyncio.wait_for(proc.communicate(), timeout=60)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise HTTPException(
                status_code=400,
                detail={"code": "git_clone_timeout", "message": "clone exceeded 60s"},
            )
        if proc.returncode != 0:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "git_clone_failed",
                    "message": err.decode(errors="ignore")[:200],
                },
            )
        try:
            validate_repo_static(Path(tmpdir))
        except StaticValidationError as e:
            raise HTTPException(
                status_code=400, detail={"code": e.code, "message": e.message}
            )
        data = tomllib.loads(
            (Path(tmpdir) / "pyproject.toml").read_text(encoding="utf-8")
        )
        project = data.get("project", {})
        return {
            "name": project.get("name", repo).lower(),
            "description": project.get("description"),
            "display_name": project.get("name", repo),
        }
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


async def _delete_harbor_images(
    detector_name: str, session: AsyncSession, detector_id: UUID
) -> None:
    """Best-effort cleanup of Harbor artifacts for a deleted detector."""
    if not settings.HARBOR_ADMIN_PASSWORD:
        return  # Harbor not configured (test env); skip silently
    harbor = HarborClient(
        settings.HARBOR_URL,
        settings.HARBOR_ADMIN_USERNAME,
        settings.HARBOR_ADMIN_PASSWORD,
    )
    versions_res = await session.execute(
        select(DetectorVersion).where(DetectorVersion.detector_id == detector_id)
    )
    for v in versions_res.scalars().all():
        try:
            await harbor.delete_artifact("detectors", detector_name, v.image_digest)
            v.status = DetectorVersionStatus.RETENTION_PRUNED
        except Exception:
            BACKEND_ERRORS.labels(stage="detector_retention_prune").inc()
            logger.exception(
                "retention prune failed",
                extra={
                    "detector_version_id": str(v.id),
                    "detector_name": detector_name,
                },
            )
    await session.commit()


@router.post("", response_model=DetectorRead, status_code=201)
async def register(
    body: DetectorCreate,
    user: User = Depends(require_role(Role.DEVELOPER)),
    session: AsyncSession = Depends(get_async_session),
) -> DetectorRead:
    try:
        normalized = normalize_git_url(body.git_url)
    except ValueError as e:
        raise HTTPException(
            status_code=422, detail={"code": "invalid_git_url", "message": str(e)}
        )

    dup = await session.execute(
        select(Detector).where(
            Detector.owner_id == user.id,
            Detector.git_url == normalized,
            Detector.deleted_at.is_(None),
        )
    )
    if dup.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail={
                "code": "duplicate_registration",
                "message": "already registered by you",
            },
        )

    pat = await _get_user_pat(session, user.id)
    meta = await _clone_and_validate(normalized, pat)
    name = body.name or meta["name"]
    display_name = body.display_name or meta["display_name"]
    description = meta["description"]

    d = Detector(
        name=name,
        display_name=display_name,
        description=description,
        git_url=normalized,
        owner_id=user.id,
    )
    session.add(d)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "name_conflict",
                "message": f"detector name '{name}' already exists",
            },
        )
    await session.refresh(d)
    return DetectorRead.model_validate(d)


@router.get("")
async def list_detectors(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
    owner_id: UUID | None = None,
    search: str | None = None,
    limit: Annotated[int, Query(le=100)] = 20,
    offset: int = 0,
) -> dict:
    stmt = select(Detector).where(Detector.deleted_at.is_(None))
    if owner_id:
        stmt = stmt.where(Detector.owner_id == owner_id)
    if search:
        pattern = f"%{search}%"
        stmt = stmt.where(Detector.name.ilike(pattern))
    stmt = stmt.order_by(Detector.created_at.desc()).limit(limit).offset(offset)
    res = await session.execute(stmt)
    items = res.scalars().all()
    return {
        "items": [
            DetectorRead.model_validate(d).model_dump(mode="json") for d in items
        ],
        "limit": limit,
        "offset": offset,
    }


@router.get("/{detector_id}", response_model=DetectorRead)
async def get_detector(
    detector: Detector = Depends(require_detector_access(write=False)),
) -> DetectorRead:
    return DetectorRead.model_validate(detector)


@router.patch("/{detector_id}", response_model=DetectorRead)
async def update_detector(
    body: DetectorUpdate,
    detector: Detector = Depends(require_detector_access(write=True)),
    session: AsyncSession = Depends(get_async_session),
) -> DetectorRead:
    if body.display_name is not None:
        detector.display_name = body.display_name
    if body.description is not None:
        detector.description = body.description
    await session.commit()
    await session.refresh(detector)
    return DetectorRead.model_validate(detector)


@router.delete("/{detector_id}", status_code=204)
async def delete_detector(
    detector: Detector = Depends(require_detector_access(write=True)),
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    detector_name = detector.name
    detector_id = detector.id
    detector.deleted_at = datetime.now(timezone.utc)
    await session.commit()
    # Best-effort Harbor cleanup (soft delete already succeeded; keep going on errors)
    try:
        await _delete_harbor_images(detector_name, session, detector_id)
    except Exception:
        BACKEND_ERRORS.labels(stage="harbor_image_cleanup").inc()
        logger.exception(
            "harbor image cleanup on soft-delete failed",
            extra={"detector_id": str(detector_id), "detector_name": detector_name},
        )
    return Response(status_code=204)


@router.delete("/{detector_id}/versions/{tag}", status_code=204)
async def delete_version(
    tag: str,
    detector: Detector = Depends(require_detector_access(write=True)),
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    """Soft-delete a single detector version. Phase 13a A4.

    Sets `DetectorVersionStatus.DELETED`, best-effort purges the Harbor
    artifact, and returns 204. Returns 409 if any job using this version
    is non-terminal.

    Historical jobs that reference the deleted version row remain
    queryable; the FK is intact (we never DROP the row).
    """
    res = await session.execute(
        select(DetectorVersion).where(
            DetectorVersion.detector_id == detector.id,
            DetectorVersion.git_tag == tag,
        )
    )
    version = res.scalar_one_or_none()
    if version is None:
        raise HTTPException(status_code=404, detail="version not found")

    if version.status != DetectorVersionStatus.ACTIVE:
        raise HTTPException(status_code=409, detail={
            "code": "version_not_active",
            "message": f"version is in status {version.status.value}, cannot delete",
        })

    in_flight = await session.execute(
        select(Job.id).where(
            Job.detector_version_id == version.id,
            Job.status.in_(NON_TERMINAL_STATUSES),
        ).limit(1)
    )
    if in_flight.scalar_one_or_none():
        raise HTTPException(status_code=409, detail={
            "code": "version_has_in_flight_jobs",
            "message": "Cancel running jobs that use this version before deleting it.",
        })

    version.status = DetectorVersionStatus.DELETED
    await session.commit()

    if settings.HARBOR_ADMIN_PASSWORD:
        try:
            harbor = HarborClient(
                settings.HARBOR_URL,
                settings.HARBOR_ADMIN_USERNAME,
                settings.HARBOR_ADMIN_PASSWORD,
            )
            await harbor.delete_artifact(
                "detectors", detector.name, version.image_digest,
            )
        except Exception:
            BACKEND_ERRORS.labels(stage="version_delete_harbor").inc()
            logger.exception(
                "harbor purge on version soft-delete failed",
                extra={
                    "detector_version_id": str(version.id),
                    "detector_name": detector.name,
                    "tag": tag,
                },
            )

    return Response(status_code=204)


@router.get("/{detector_id}/versions")
async def list_versions(
    detector: Detector = Depends(require_detector_access(write=False)),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    res = await session.execute(
        select(DetectorVersion)
        .where(DetectorVersion.detector_id == detector.id)
        .where(DetectorVersion.status == DetectorVersionStatus.ACTIVE)
        .order_by(DetectorVersion.built_at.desc())
    )
    versions = res.scalars().all()
    return {
        "items": [
            VersionRead.model_validate(v).model_dump(mode="json") for v in versions
        ]
    }


@router.get("/{detector_id}/versions/{tag}", response_model=VersionDetailRead)
async def get_version(
    tag: str,
    detector: Detector = Depends(require_detector_access(write=False)),
    session: AsyncSession = Depends(get_async_session),
) -> VersionDetailRead:
    res = await session.execute(
        select(DetectorVersion).where(
            DetectorVersion.detector_id == detector.id,
            DetectorVersion.git_tag == tag,
        )
    )
    version = res.scalar_one_or_none()
    if version is None:
        raise HTTPException(status_code=404, detail="version not found")
    return VersionDetailRead.model_validate(version)


@router.get("/{detector_id}/available-tags")
async def available_tags(
    detector: Detector = Depends(require_detector_access(write=True)),
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> list[AvailableTag]:
    owner, repo = parse_github_owner_repo(detector.git_url)
    pat = await _get_user_pat(session, user.id)
    tags = await list_remote_tags(owner, repo, pat)
    return [AvailableTag(name=t["name"], commit_sha=t["commit_sha"]) for t in tags]


async def _create_k8s_resources(
    build_id: UUID,
    detector_name: str,
    git_tag: str,
    owner_repo: str,
    pat: str,
) -> str:
    """Create Kubernetes Secret + Job for the build. Returns the job name."""
    job_name = build_job_name(detector_name, git_tag, build_id)
    secret_body = build_git_credential_secret(
        build_id=build_id,
        username="x-token-auth",
        pat_token=pat,
    )
    job_body = build_job_spec(
        build_id=build_id,
        detector_name=detector_name,
        git_tag=git_tag,
        owner_repo=owner_repo,
    )

    import asyncio as _asyncio

    loop = _asyncio.get_running_loop()

    def _create_secret():
        core_v1().create_namespaced_secret(
            namespace=settings.BUILD_NAMESPACE,
            body=secret_body,
        )

    def _create_job():
        batch_v1().create_namespaced_job(
            namespace=settings.BUILD_NAMESPACE,
            body=job_body,
        )

    def _delete_secret():
        core_v1().delete_namespaced_secret(
            name=build_secret_name(build_id),
            namespace=settings.BUILD_NAMESPACE,
        )

    await loop.run_in_executor(None, _create_secret)
    try:
        await loop.run_in_executor(None, _create_job)
    except Exception:
        # Rollback Secret on any error
        try:
            await loop.run_in_executor(None, _delete_secret)
        except Exception:
            pass  # best-effort; don't mask original exception
        raise
    return job_name


@router.post(
    "/{detector_id}/builds",
    response_model=BuildRead,
    status_code=201,
    dependencies=[Depends(rate_limit_user("builds_create", 10, 3600))],
)
async def create_build(
    body: BuildCreate,
    detector: Detector = Depends(require_detector_access(write=True)),
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> BuildRead:
    # Check per-user concurrency cap
    in_flight_statuses = [
        DetectorBuildStatus.PENDING,
        DetectorBuildStatus.CLONING,
        DetectorBuildStatus.VALIDATING,
        DetectorBuildStatus.BUILDING,
        DetectorBuildStatus.SCANNING,
    ]
    user_in_flight = await session.execute(
        select(func.count())
        .select_from(DetectorBuild)
        .join(Detector, DetectorBuild.detector_id == Detector.id)
        .where(
            Detector.owner_id == user.id,
            DetectorBuild.status.in_(in_flight_statuses),
        )
    )
    in_flight = user_in_flight.scalar()
    if in_flight >= settings.BUILD_CONCURRENCY_PER_USER:
        from app.schemas.errors import ConcurrencyLimitDetail

        raise HTTPException(
            status_code=429,
            detail=ConcurrencyLimitDetail(
                limit=settings.BUILD_CONCURRENCY_PER_USER,
                in_flight=in_flight,
            ).model_dump(),
        )

    # Check for in-flight build for same detector + tag
    existing = await session.execute(
        select(DetectorBuild).where(
            DetectorBuild.detector_id == detector.id,
            DetectorBuild.git_tag == body.git_tag,
            DetectorBuild.status.in_(in_flight_statuses),
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail={
                "code": "build_in_flight",
                "message": "a build for this tag is already in flight",
            },
        )

    # Require PAT for private repos
    pat = await _get_user_pat(session, user.id)
    if pat is None:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "credential_missing",
                "message": "PAT required to trigger a build",
            },
        )

    build = DetectorBuild(
        detector_id=detector.id,
        git_tag=body.git_tag,
        triggered_by_id=user.id,
        status=DetectorBuildStatus.PENDING,
    )
    session.add(build)
    await session.flush()  # get build.id before k8s call

    owner, repo = parse_github_owner_repo(detector.git_url)
    owner_repo = f"{owner}/{repo}"

    try:
        job_name = await _create_k8s_resources(
            build_id=build.id,
            detector_name=detector.name,
            git_tag=body.git_tag,
            owner_repo=owner_repo,
            pat=pat,
        )
    except Exception as exc:
        build.status = DetectorBuildStatus.FAILED
        build.failure_reason = f"k8s_error: {type(exc).__name__}: {exc}"[:500]
        await session.commit()
        raise HTTPException(
            status_code=500,
            detail={
                "code": "build_launch_failed",
                "message": f"failed to launch build job: {type(exc).__name__}",
                "build_id": str(build.id),
            },
        )

    build.k8s_job_name = job_name
    build.status = DetectorBuildStatus.CLONING
    await session.commit()
    await session.refresh(build)
    return BuildRead.model_validate(build)


@router.get("/{detector_id}/builds")
async def list_builds(
    detector: Detector = Depends(require_detector_access(write=False)),
    session: AsyncSession = Depends(get_async_session),
    limit: Annotated[int, Query(le=100)] = 20,
    offset: int = 0,
) -> dict:
    res = await session.execute(
        select(DetectorBuild)
        .where(DetectorBuild.detector_id == detector.id)
        .order_by(DetectorBuild.started_at.desc())
        .limit(limit)
        .offset(offset)
    )
    builds = res.scalars().all()
    return {
        "items": [BuildRead.model_validate(b).model_dump(mode="json") for b in builds],
        "limit": limit,
        "offset": offset,
    }


@router.get("/{detector_id}/builds/{build_id}", response_model=BuildRead)
async def get_build(
    build_id: UUID,
    detector: Detector = Depends(require_detector_access(write=False)),
    session: AsyncSession = Depends(get_async_session),
) -> BuildRead:
    build = await session.get(DetectorBuild, build_id)
    if build is None or build.detector_id != detector.id:
        raise HTTPException(status_code=404, detail="build not found")
    return BuildRead.model_validate(build)


@router.post("/{detector_id}/builds/{build_id}/cancel", response_model=BuildRead)
async def cancel_build(
    build_id: UUID,
    detector: Detector = Depends(require_detector_access(write=True)),
    session: AsyncSession = Depends(get_async_session),
) -> BuildRead:
    build = await session.get(DetectorBuild, build_id)
    if build is None or build.detector_id != detector.id:
        raise HTTPException(status_code=404, detail="build not found")

    cancellable_statuses = {
        DetectorBuildStatus.PENDING,
        DetectorBuildStatus.CLONING,
        DetectorBuildStatus.VALIDATING,
        DetectorBuildStatus.BUILDING,
        DetectorBuildStatus.SCANNING,
    }
    if build.status not in cancellable_statuses:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "not_cancellable",
                "message": "build is not in a cancellable state",
            },
        )

    build.status = DetectorBuildStatus.CANCELLED
    build.finished_at = datetime.now(timezone.utc)

    # Best-effort K8s job deletion
    if build.k8s_job_name:
        import asyncio as _asyncio

        loop = _asyncio.get_running_loop()

        def _delete_job():
            try:
                batch_v1().delete_namespaced_job(
                    name=build.k8s_job_name,
                    namespace=settings.BUILD_NAMESPACE,
                    propagation_policy="Background",
                )
            except Exception:
                BACKEND_ERRORS.labels(stage="cancel_build_k8s_cleanup").inc()
                logger.exception(
                    "K8s build job cleanup failed on cancel",
                    extra={
                        "build_id": str(build.id),
                        "k8s_job_name": build.k8s_job_name,
                    },
                )

        await loop.run_in_executor(None, _delete_job)

    await session.commit()
    await session.refresh(build)
    return BuildRead.model_validate(build)

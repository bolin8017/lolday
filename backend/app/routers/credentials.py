from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_async_session
from app.models import User
from app.models.credential import UserGitCredential
from app.schemas.credential import GitCredentialRead, GitCredentialSet
from app.services.crypto import TokenCipher
from app.users import current_active_user

router = APIRouter()


def _cipher() -> TokenCipher:
    if not settings.FERNET_KEY:
        raise HTTPException(status_code=500, detail="FERNET_KEY not configured")
    return TokenCipher(settings.FERNET_KEY)


@router.put("/me/git-credential", response_model=GitCredentialRead)
async def set_credential(
    body: GitCredentialSet,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> GitCredentialRead:
    cipher = _cipher()
    existing = await session.get(UserGitCredential, user.id)
    encrypted = cipher.encrypt(body.token)
    hint = TokenCipher.token_hint(body.token)
    if existing:
        existing.provider = body.provider
        existing.encrypted_token = encrypted
        existing.token_hint = hint
    else:
        existing = UserGitCredential(
            user_id=user.id,
            provider=body.provider,
            encrypted_token=encrypted,
            token_hint=hint,
        )
        session.add(existing)
    await session.commit()
    await session.refresh(existing)
    return GitCredentialRead(
        provider=existing.provider,
        token_hint=existing.token_hint,
        created_at=existing.created_at,
        updated_at=existing.updated_at,
    )


@router.get("/me/git-credential", response_model=GitCredentialRead)
async def get_credential(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> GitCredentialRead:
    existing = await session.get(UserGitCredential, user.id)
    if not existing:
        raise HTTPException(status_code=404, detail="no credential set")
    return GitCredentialRead(
        provider=existing.provider,
        token_hint=existing.token_hint,
        created_at=existing.created_at,
        updated_at=existing.updated_at,
    )


@router.delete("/me/git-credential", status_code=204)
async def delete_credential(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    existing = await session.get(UserGitCredential, user.id)
    if existing:
        await session.delete(existing)
        await session.commit()
    return Response(status_code=204)

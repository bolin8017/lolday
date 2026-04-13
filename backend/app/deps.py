from fastapi import Depends, HTTPException, status

from app.models import Role, User
from app.users import current_active_user

ROLE_HIERARCHY = {Role.USER: 0, Role.DEVELOPER: 1, Role.ADMIN: 2}


def require_role(min_role: Role):
    async def _check(user: User = Depends(current_active_user)):
        if ROLE_HIERARCHY[user.role] < ROLE_HIERARCHY[min_role]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return user

    return _check

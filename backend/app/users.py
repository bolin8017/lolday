"""Phase 10: primary auth dependency for all protected routes.

`current_active_user` is the single entry point used by every router's
`Depends(...)`. It resolves to `cf_access_user`, which verifies the
Cloudflare Access JWT and get-or-creates the corresponding User row.

The fastapi-users machinery (auth backends, transports, UserManager,
register/login routers) was removed in Phase 10.2 — the User model still
inherits `SQLAlchemyBaseUserTableUUID` from fastapi-users-db-sqlalchemy,
but nothing at runtime touches the password flow.
"""
from app.auth.cf_access import cf_access_user as current_active_user

__all__ = ["current_active_user"]

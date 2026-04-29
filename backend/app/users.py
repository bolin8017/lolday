"""Phase 10 (+ 2026-04-29): primary auth dependency for all protected routes.

`current_active_user` is the single entry point used by every router's
`Depends(...)`. It resolves to `cf_access_user`, which verifies the
Cloudflare Access JWT and get-or-creates the corresponding User row.

The fastapi-users machinery was stripped in Phase 10; the User model and
schema were rewritten as native SQLAlchemy 2.0 / Pydantic v2 on 2026-04-29
(`chore/drop-hashed-password`), and neither `fastapi-users` nor
`fastapi-users-db-sqlalchemy` is installed.
"""

from app.auth.cf_access import cf_access_user as current_active_user

__all__ = ["current_active_user"]

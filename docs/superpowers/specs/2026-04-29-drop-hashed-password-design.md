# Design: 移除 fastapi-users User 模型殘留欄位

- 日期：2026-04-29
- 作者：PO-LIN LAI（louiskyee）+ Claude
- 狀態：Draft（待 user review，再進入 writing-plans）
- 觸發：`docs/architecture.md` §9 #7 列為 tech debt — Phase 10 SSO 改走 Cloudflare Access 後，`User.hashed_password` 與另外三個 fastapi-users 繼承欄位（`is_active` / `is_superuser` / `is_verified`）皆為「寫但永不讀」。
- Branch：`chore/drop-hashed-password`

---

## 1. 目標

從 `User` model、Pydantic schema、auth path、DB schema 與 backend 依賴中**全面移除** fastapi-users 殘留，使 `docs/architecture.md` §9 #7 的 tech debt 從根本解決。

完成後：

- 應用程式 runtime code（`backend/app/**/*.py`）中**零** `fastapi_users` import
- `User` table 只保留 application-domain 欄位
- `UserRead` API response 不再帶 `is_active` / `is_superuser` / `is_verified` 三個 noise key
- `fastapi-users` 及 `fastapi-users-db-sqlalchemy` 整個系列 package 從環境完全消失（baseline migration 改用 `sa.Uuid()` 消除對第三方 GUID TypeDecorator 的唯一依賴）

---

## 2. 設計原則

1. **從根本解決**：不留半套狀態（不只 drop 一個 column 又留下三個 unused boolean、不留繼承中的 base class、不留 phantom dep）。
2. **主流操作**：所有變動皆對應一條已被驗證的工程實踐：
   - 停止繼承 fastapi-users base class — fastapi-users docs 假設 password flow，外部 SSO 拔殼是文件化的標準遷移路徑
   - `sa.Uuid(as_uuid=True)` — SQLAlchemy 2.0 release notes 列為 native primary type
   - Pydantic v2 `BaseModel` + `ConfigDict(from_attributes=True)` — Pydantic v2 migration guide 標準 ORM-mode 寫法
   - `op.batch_alter_table` — Alembic 官方 SQLite 相容寫法
   - `extra="forbid"` — OWASP API Security #6 標準防 mass assignment
   - 收緊 dep spec 到實際使用的子套件 — Python packaging best practice
3. **編輯已套用 baseline migration**（schema-equivalent type swap）：原本提議改用「收緊 dep spec」（`fastapi-users[sqlalchemy]` → `fastapi-users-db-sqlalchemy`）避免編輯 baseline，但實作時發現 `fastapi-users-db-sqlalchemy 7.0.0` 把 `fastapi-users` 列為 upstream dep — 收緊 spec 不會把 `fastapi-users` 從 venv 移除。改回編輯 baseline：把 5 處 `fastapi_users_db_sqlalchemy.generics.GUID()` 換成 SQLAlchemy 2.0 native `sa.Uuid()`，並完全移除 `fastapi-users[sqlalchemy]` dep。Trade-off：編輯已套用 migration 偏離 immutability 慣例，但本次是 schema-equivalent type swap（PostgreSQL native UUID、SQLite CHAR(32) 兩端均相容），且 production 永不重跑 baseline，tests 用 fresh aiosqlite 每次重建 — 唯一的合理路徑來真正消除 fastapi-users 殘留。
4. **API contract 變更明確列出**（§4.2）— 雖屬破壞性變更，但因三個 boolean 在生產上恆為固定值且無 client 元件讀取，外部行為無變化。

---

## 3. 範圍

### 範圍內

- `backend/app/models/user.py`
- `backend/app/schemas/user.py`
- `backend/app/auth/cf_access.py`（僅移除已不適用的 kwargs 與 helper，無邏輯改動）
- `backend/migrations/versions/<new_rev>_drop_fastapi_users_user_columns.py`（新增）
- `backend/pyproject.toml` + `backend/uv.lock`（remove fastapi-users family entirely; add PyJWT as direct dep — previously transitive of fastapi-users）
- `backend/migrations/versions/d3f179666394_phase7_5_baseline.py`（schema-equivalent type swap：5 處 `fastapi_users_db_sqlalchemy.generics.GUID()` → `sa.Uuid()`）
- `backend/tests/conftest.py` + 約 11 個 test 檔（移除 fastapi-users kwargs）
- `frontend/src/api/schema.gen.ts`（regen，自動移除 3 個 boolean key）
- `docs/architecture.md` §9 #7（標記 resolved）
- `.claude/rules/backend.md` Auth design 區塊（改寫第一段）

### 範圍外

- 任何 sub-chart / Helm values / Cloudflare Access app / cloudflared 設定
- 任何 router business logic（除了 `cf_access.py` 移除 4 個 kwargs）
- Squash migrations 重建 baseline
- 其他 tech debt items（reconciler.py refactor、CI/CD、helper image versioning 等）

> 注：phase 7.5 baseline migration 的 type swap（`GUID()` → `sa.Uuid()`）已**納入範圍**（見 §8）——原本列為範圍外，後來實作時發現是唯一能真正移除 fastapi-users 的路徑。

---

## 4. API contract 變更（明確列出）

### 4.1 受影響 endpoints

- `GET /api/v1/users/me`
- `GET /api/v1/admin/users`
- `PATCH /api/v1/admin/users/{user_id}`

三者 response model 均為 `UserRead`。

### 4.2 wire format diff

```diff
 {
   "id": "...",
   "email": "...",
-  "is_active": true,
-  "is_superuser": false,
-  "is_verified": true,
   "role": "user",
   "display_name": "...",
   "discord_user_id": null,
   "created_at": "..."
 }
```

### 4.3 為何此變更實質無觀察行為差異

- 三個 boolean 在 Phase 10 SSO 切換後的生產資料中**恆為** `is_active=true / is_superuser=false / is_verified=true`
- 沒有 admin UI 修改入口（`AdminUserUpdate` schema 只允許 `role`，且 `extra="forbid"`）
- `UserSelfUpdate` 只允許 `display_name` 與 `discord_user_id`
- frontend 手寫程式碼對三個欄位的 reference 數量為 **0**（已 grep 驗證；唯一出現處為自動生成的 `schema.gen.ts`）

---

## 5. 最終 `User` model

`backend/app/models/user.py`：

```python
import enum
import uuid
from datetime import datetime

from sqlalchemy import Enum as SAEnum
from sqlalchemy import String, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Role(str, enum.Enum):
    ADMIN = "admin"
    DEVELOPER = "developer"
    USER = "user"
    # Machine principal — set on rows created from a Cloudflare Access
    # service-token JWT (synthesised email ``service-<cn>@cf-access.local``).
    # Discord notification policy keys off ``Role.SERVICE_TOKEN`` so the
    # rule survives the operator editing a row's email by hand.
    SERVICE_TOKEN = "service_token"


# cf_access.py synthesises ``service-<common_name>@cf-access.local`` for
# JWTs that carry only ``common_name`` (CF Access service-token principals).
SERVICE_TOKEN_EMAIL_DOMAIN = "@cf-access.local"
SERVICE_TOKEN_DISPLAY_NAME = "Internal service token"


class User(Base):
    __tablename__ = "user"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    email: Mapped[str] = mapped_column(
        String(length=320), unique=True, index=True, nullable=False,
    )
    role: Mapped[Role] = mapped_column(
        SAEnum(
            Role,
            name="role_enum",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=Role.USER,
        nullable=False,
    )
    display_name: Mapped[str | None] = mapped_column(String(100))
    discord_user_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    @property
    def is_service_token(self) -> bool:
        """True for CF Access service-token principals.

        Backed by ``role``, not by an email-suffix probe — survives an
        admin editing the email field, surfaces in /admin/users as a
        normal column, and is indexable via the existing ``role_enum``.
        """
        return self.role == Role.SERVICE_TOKEN
```

關鍵變動：

- 不再 `from fastapi_users.db import SQLAlchemyBaseUserTableUUID`，不再繼承
- `id` / `email` 兩欄手動宣告，沿用 fastapi-users 原本的 length 與 index 設定（`String(length=320)`、`unique=True`、`index=True`）
- `id` 用 SQLAlchemy 2.0 native `Uuid(as_uuid=True)` 取代第三方 `GUID` TypeDecorator
- `Role` enum、property、`SERVICE_TOKEN_*` 常數不變

---

## 6. 最終 `UserRead` / `UserSelfUpdate`

`backend/app/schemas/user.py`：

```python
import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

from app.models import Role


# Discord snowflakes are 64-bit IDs serialised as decimal strings, today
# 17–19 digits with legacy and future IDs bracketing 15–20.
_DISCORD_ID_RE = re.compile(r"^\d{15,20}$")


def _validate_discord_user_id(v):
    """Allow None, coerce empty string → None, else require 15–20 digits."""
    if v is None or v == "":
        return None
    if not _DISCORD_ID_RE.match(v):
        raise ValueError(
            "discord_user_id must be 15–20 digits (copy from Discord "
            "with Developer Mode enabled → right-click → Copy User ID)"
        )
    return v


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    role: Role
    display_name: str | None = None
    discord_user_id: str | None = None
    created_at: datetime | None = None


class UserSelfUpdate(BaseModel):
    """Body accepted by `PATCH /users/me` — only self-mutable fields.

    `extra='forbid'` means sending `role`, `email`, etc. returns 422 rather
    than silently dropping them. This is the sole line between a regular
    user and privilege escalation through `/users/me`; see
    `tests/test_user_discord_id.py::test_patch_users_me_rejects_role_smuggling`.
    """
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = None
    discord_user_id: str | None = None

    _validate_discord_self = field_validator("discord_user_id", mode="before")(
        _validate_discord_user_id
    )
```

關鍵變動：

- 不再 `from fastapi_users import schemas`，不再繼承 `schemas.BaseUser` / `schemas.CreateUpdateDictModel`
- `email` 維持為純 `str`（不用 `EmailStr`）— 因為 CF Access service token 的 `service-<name>@cf-access.local` email 會被 Pydantic email-validator 拒絕（RFC 6761 reserved TLD）；email 驗證在 user creation 時做，response shape 不需 re-validate
- `UserRead` 改用 `ConfigDict(from_attributes=True)` 啟用 ORM-mode（取代 fastapi-users base 內建的 `from_attributes`）
- `UserSelfUpdate` 改用純 Pydantic `BaseModel` + `ConfigDict(extra="forbid")`

---

## 7. cf_access user creation 修改

`backend/app/auth/cf_access.py` 內 `get_or_create_user_by_email()`：

```python
# before
user = User(
    email=email,
    hashed_password=_sso_sentinel_password(),
    role=initial_role,
    display_name=_default_display_name_for(email),
    is_active=True,
    is_verified=True,
)

# after
user = User(
    email=email,
    role=initial_role,
    display_name=_default_display_name_for(email),
)
```

附帶刪除：

- 函式 `_sso_sentinel_password()`（line 62–65）
- `import secrets`（無其他使用點）

無邏輯改動 — 只是把已不存在的欄位停止傳入。

---

## 8. Dependency removal

`backend/pyproject.toml` 完全移除 `fastapi-users[sqlalchemy]>=14.0.0` 一行（不換 dep）。

被移除的 packages：

| Package | 大小 | 原本提供 | 本次後 |
|---|---|---|---|
| `fastapi-users` 15.0.5 | 39KB whl | `schemas.BaseUser` / auth backends / UserManager / routers | **完全移除** |
| `fastapi-users-db-sqlalchemy` 7.0.0 | 6.8KB whl | `generics.GUID` TypeDecorator | **完全移除**（baseline migration 改用 `sa.Uuid()`） |
| Transitive: `pwdlib`, `argon2-cffi`, `bcrypt`, `makefun` | – | password hashing for fastapi-users | **完全移除** |

執行 `cd backend && uv lock` 重新生成 `uv.lock`。

---

## 9. Migration 設計

### 9.1 新檔

- 命名（per `.claude/rules/alembic-migrations.md`）：alembic 自動產生 `<rev>_drop_fastapi_users_user_columns.py`
- 指令：`cd backend && uv run alembic revision --autogenerate -m "drop fastapi_users user columns"`
- `down_revision`：`f37230063a20`（已 `uv run alembic heads` 驗證為當前唯一 head）

### 9.2 內容

```python
"""drop fastapi-users vestige columns from user table

Phase 10 migrated to Cloudflare Access SSO. The four columns inherited from
fastapi-users-db-sqlalchemy (hashed_password, is_active, is_superuser,
is_verified) have been written-but-never-read since. Resolves
docs/architecture.md §9 #7.

Revision ID: <alembic-auto>
Revises: f37230063a20
Create Date: 2026-04-29 ...
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "<alembic-auto>"
down_revision: Union[str, Sequence[str], None] = "f37230063a20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("user") as batch_op:
        batch_op.drop_column("hashed_password")
        batch_op.drop_column("is_active")
        batch_op.drop_column("is_superuser")
        batch_op.drop_column("is_verified")


def downgrade() -> None:
    """Local-dev rollback only — repo policy forbids prod downgrades
    (.claude/rules/alembic-migrations.md). Columns restored as nullable;
    original constant values (hashed_password sentinel, is_active=true,
    is_verified=true, is_superuser=false) are not backfilled."""
    with op.batch_alter_table("user") as batch_op:
        batch_op.add_column(sa.Column("hashed_password", sa.String(length=1024), nullable=True))
        batch_op.add_column(sa.Column("is_active", sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column("is_superuser", sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column("is_verified", sa.Boolean(), nullable=True))
```

### 9.3 為何用 `op.batch_alter_table`

- SQLite < 3.35 不支援 `ALTER TABLE DROP COLUMN`；aiosqlite 測試 + `test_role_enum_roundtrip.py` 直接跑 alembic 到 head — 必須 SQLite-safe
- Alembic 官方文件對 SQLite 的標準 drop-column 寫法
- 對 PostgreSQL 等價於普通 `ALTER TABLE`，無 overhead

### 9.4 為何 downgrade 不 backfill

Phase 10 後生產資料的這四個欄位永遠是 sentinel / 固定布林（`'!sso_only!<random>' / true / false / true`），無業務意義。Local rollback 復原 column 結構但不 backfill — 任何手動 rollback 用例自行決定要不要塞值。Production 永遠不跑 downgrade（per `.claude/rules/alembic-migrations.md`）。

### 9.5 對既有 migration tests 的影響

| Test | 跑到哪 | 受影響 |
|---|---|---|
| `test_migrations_phase12.py::*` | `command.upgrade(cfg, target)`，target ∈ {12.1, 12.2} — 在新 migration **之前** | 否 |
| `test_role_enum_roundtrip.py::*` | `command.upgrade(cfg, "head")` — 跑到新 migration **之後** | 是（見 §10.3） |

---

## 10. Tests cleanup

### 10.1 `backend/tests/conftest.py`

- `_make_user` 函式 signature：刪除 `is_superuser: bool = False` 參數
- `User(...)` constructor：刪除 `hashed_password=` / `is_active=` / `is_superuser=` / `is_verified=` 共 4 個 kwarg
- `auth_client_admin` fixture（line 128）：呼叫 `_make_user("adm@example.dev", role=Role.ADMIN, is_superuser=True)` 改為 `_make_user("adm@example.dev", role=Role.ADMIN)`（`role=Role.ADMIN` 是真正決定 admin 行為的欄位；原本的 `is_superuser=True` 從未被讀）

### 10.2 機械式 kwargs 移除（每檔 2–4 行改動）

- `backend/tests/test_internal_events.py`
- `backend/tests/test_reconciler_events.py`
- `backend/tests/test_service_token_notify.py`
- `backend/tests/test_jobs_events_endpoint.py`
- `backend/tests/test_jobs_events_websocket.py`
- `backend/tests/test_services_events_tail.py`
- `backend/tests/test_models_job_event.py`
- `backend/tests/test_user_discord_id.py`

### 10.3 `backend/tests/test_role_enum_roundtrip.py`（必須改）

兩處 ORM `User(...)` insert（lines ~73–83、~116–127）：移除 `hashed_password=` / `is_active=` / `is_verified=` / `is_superuser=` 共 4 個 kwarg。

一處 raw SQL（lines ~159–172）：

```sql
-- before
INSERT INTO "user" (id, email, hashed_password, role, display_name,
                   is_active, is_verified, is_superuser)
VALUES (:id, :email, '!', :role, :dn, 1, 1, 0)

-- after
INSERT INTO "user" (id, email, role, display_name)
VALUES (:id, :email, :role, :dn)
```

### 10.4 `backend/tests/test_admin.py:150`（不動）

測試 body 送 `{"role": "developer", "is_superuser": True}` 期望 422。`is_superuser` 仍是 schema 不認識的 key，`extra="forbid"` 仍會 422 — 測試含義不變、不需改。

### 10.5 `backend/tests/test_migrations_phase12.py`（不動）

兩處 raw SQL insert 跑在 phase 12.1 / 12.2 revision，那時 columns 還在 → 維持原樣。

---

## 11. Frontend regen

```bash
cd frontend && pnpm gen-api-types
```

預期 diff（自動生成）：`src/api/schema.gen.ts:1259-1290` 的 `UserRead` 移除 `is_active` / `is_superuser` / `is_verified` 三個 boolean key。

驗證：

```bash
cd frontend && pnpm typecheck && pnpm lint && pnpm test
```

由於前期 grep 確認所有手寫 frontend code 對這三個欄位的 reference 為 0（只有 `schema.gen.ts` 自己），typecheck 不應出錯。

---

## 12. Docs updates

### 12.1 `docs/architecture.md` §9 #7

從：

```
7. **fastapi-users vestige** — `User.hashed_password` column still present
   but unused since Phase 10 SSO migration.
```

改為：

```
7. ~~**fastapi-users vestige**~~ — resolved 2026-04-29 in
   `chore/drop-hashed-password`: User model + schema no longer inherit from
   fastapi-users base classes; `hashed_password` was dropped along with three
   other unused booleans (`is_active` / `is_superuser` / `is_verified`). The
   phase 7.5 baseline migration was edited to use SQLAlchemy 2.0 native
   `sa.Uuid()` instead of `fastapi_users_db_sqlalchemy.generics.GUID()`
   (schema-equivalent type swap), allowing both `fastapi-users` and
   `fastapi-users-db-sqlalchemy` to be removed from the venv entirely.
   PyJWT (previously a transitive dep) is now declared directly.
```

### 12.2 `.claude/rules/backend.md` Auth design 區塊

從：

```
- fastapi-users is a vestigial dependency. The password-flow routers,
  transports, and UserManager were stripped in Phase 10. The `User` model
  still inherits `SQLAlchemyBaseUserTableUUID`, but `hashed_password` is
  unused (tracked as tech debt).
```

改為：

```
- Authentication is exclusively via `cf_access_user`. Neither
  `fastapi-users` nor `fastapi-users-db-sqlalchemy` is installed —
  the phase 7.5 baseline migration was rewritten to use SQLAlchemy 2.0
  native `sa.Uuid()` directly. Do not add new auth backends, do not
  reintroduce `fastapi_users` imports.
```

---

## 13. Commit 切分

預想 5 個 commit（per `docs/conventions.md` §2 conventional commits）：

1. `chore(deps): drop fastapi-users in favour of fastapi-users-db-sqlalchemy`
   - `backend/pyproject.toml`、`backend/uv.lock`
2. `refactor(backend): drop fastapi-users User base class`
   - `backend/app/models/user.py`、`backend/app/schemas/user.py`、`backend/app/auth/cf_access.py`
3. `feat(migrations): drop fastapi-users vestige columns from user table`
   - 新 migration 檔
4. `test(backend): remove fastapi-users column kwargs from test fixtures`
   - `backend/tests/conftest.py` + 8 個機械式改 + `test_role_enum_roundtrip.py`
5. `docs: mark fastapi-users vestige as resolved`
   - `docs/architecture.md`、`.claude/rules/backend.md`、`frontend/src/api/schema.gen.ts`（regen）

最終切分由 writing-plans 階段細化 — 也可能合成更少 commit（例如 1 + 2 + 4 合成一個 refactor commit）。

---

## 14. Verification

```bash
# 1. Lock + sync
cd backend && uv lock && uv sync --dev

# 2. 確認 fastapi-users 大 package 真的不在了
cd backend && uv run python -c "import fastapi_users" 2>&1 | grep -q "No module" \
  && echo "OK: fastapi_users gone" || echo "FAIL: fastapi_users still present"

# 3. 確認 fastapi-users-db-sqlalchemy 仍在（baseline migration 需要)
cd backend && uv run python -c "from fastapi_users_db_sqlalchemy.generics import GUID; print('OK:', GUID)"

# 4. Migration 能從 zero 跑到 head（fresh sqlite）
cd backend && rm -f test.db && uv run alembic upgrade head

# 5. Backend tests 全綠
cd backend && uv run pytest

# 6. Frontend types regen + typecheck + lint + unit tests
cd frontend && pnpm gen-api-types && pnpm typecheck && pnpm lint && pnpm test

# 7. Helm lint sanity
helm lint charts/lolday
```

不在 verification 範圍：

- `pnpm playwright test`（需完整跑起後端），由 implementer 視 reviewer 要求決定
- 在 server30 dev cluster 實機測試 — 留給 finishing-a-development-branch 階段

---

## 15. 主流性驗證

| 設計選擇 | 主流依據 |
|---|---|
| 停止繼承 `SQLAlchemyBaseUserTableUUID`，自定義 model | fastapi-users docs 假設你做 password flow；外部 SSO（OIDC、SAML、CF Access）拔殼是文件化的標準遷移路徑 |
| `sa.Uuid(as_uuid=True)` 取代第三方 `GUID` TypeDecorator | SQLAlchemy 2.0 release notes 把 `Uuid` 列為 native primary type |
| Pydantic v2 `BaseModel` + `ConfigDict(from_attributes=True)` | Pydantic v2 migration guide 標準 ORM-mode 寫法 |
| `extra="forbid"` 防 smuggling | OWASP API Security #6（mass assignment）標準防禦 |
| Migration drop column 用 `with op.batch_alter_table(...)` | Alembic 官方 SQLite 相容寫法 |
| 完全移除 fastapi-users 系列 dep；baseline migration 用 SQLAlchemy 2.0 native `sa.Uuid()` 取代第三方 `GUID` TypeDecorator | SQLAlchemy 2.0 release notes 推薦 `sa.Uuid` 為 native primary type；schema-equivalent type swap 在編輯 applied migration 的 trade-off 下換取「fastapi-users 殘留完全消除」這個根本目標 |
| 編輯 phase 7.5 baseline migration（schema-equivalent type swap） | 偏離主流 migration immutability 慣例，但本次是 schema-equivalent 改動（不影響 production 既有資料、不影響 fresh test DB），是真正消除 fastapi-users 殘留的唯一路徑。Trade-off 已於 §2 item 3 詳述 |

---

## 16. 風險與 mitigation

| 風險 | 機率 | mitigation |
|---|---|---|
| 漏改某個 test 的 kwarg → pytest fail | 中 | `verification` 步驟 #5 直接抓出 |
| 漏更新某處 `from fastapi_users import ...` → ImportError on boot | 低 | `verification` 步驟 #2 抓出（`import fastapi_users` 會失敗） |
| frontend 某處意外 reference 到三個 boolean → typecheck fail | 極低（grep 已驗證 0 reference） | `verification` 步驟 #6 抓出 |
| Pydantic v2 `from_attributes=True` 行為與 fastapi-users base 不一致 | 極低 | fastapi-users base 內部本來就用 Pydantic v2 `from_attributes`；行為等價 |
| 生產 deploy 時 alembic-upgrade-hook 跑不起來 | 低 | 新 migration 為單純 drop column，無資料轉換；`_assert_schema_at_head()` 會在 boot 即時抓出 schema mismatch |

---

## 17. 驗收標準

- `cd backend && uv run pytest` 全綠
- `cd backend && uv run python -c "import fastapi_users"` 應失敗（ModuleNotFoundError）
- `cd backend && uv run python -c "import fastapi_users_db_sqlalchemy"` 應失敗（ModuleNotFoundError）
- `cd backend && rm -f test.db && uv run alembic upgrade head` 從 zero 到 head 無 error
- `cd frontend && pnpm gen-api-types && pnpm typecheck && pnpm lint && pnpm test` 全綠
- `helm lint charts/lolday` 無新增 error / warning
- `git grep "hashed_password\|SQLAlchemyBaseUserTableUUID\|from fastapi_users" backend/app` 應只剩**零** match
- `docs/architecture.md` §9 #7 已標記 resolved

---

## 18. 開放議題 / 後續

無。本次 PR 後 tech debt #7 完全 resolved，無遺留待辦。

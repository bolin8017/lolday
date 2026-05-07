# Model Registry — HuggingFace-style namespace + per-version visibility — Design Specification

> Date: 2026-05-07
> Owner: PO-LIN LAI
> Status: design approved (brainstorm), pending implementation plan

## Overview

Lolday's model registry today suffers from two coupled architectural defects:

1. **Single global namespace per detector.** `ModelVersion.mlflow_name` is set directly to `Detector.name` (`reconciler/jobs.py:_register_model_from_job:312`). Any user can submit a train job against any registered detector (`jobs.py:120` — no ownership check), so multiple users training against the same detector all append versions into one shared `mlflow_name`. There is no notion of per-user model namespaces.
2. **No access control on model artefacts.** Every authenticated user can list every model version, fetch every version's metadata, and submit predict jobs against any version. There is no "this model version is private to me while I iterate" affordance. The backend has no `visibility` field analogous to `DatasetConfig.visibility`.

This phase rebuilds the model registry layer to solve both, using mainstream patterns:

1. **HuggingFace / GitHub-style namespace**: every registered model lives under a user namespace `{owner_handle}/{detector_name}` (e.g. `bolin8017/elf-rf`). A new `RegisteredModel` entity captures the (owner, detector) pair plus model-level metadata. Two users training against the same detector get two separate namespaces — no collision.
2. **Per-version visibility**: each `ModelVersion` carries `visibility` ∈ {`public`, `private`}. List / get / predict endpoints filter by visibility (404 hide-existence pattern, mirroring `DatasetConfig`). New PATCH endpoint to toggle.
3. **Four follow-up features bundled**: model description / tags, owner-transfer endpoint, cascade-delete endpoint, and parallel `elfrfdet → elf-rf` + `elfcnndet → elf-cnn` operational rebuilds. The operator's elf-rf/elf-cnn rebuild doubles as the end-to-end validation testbed.

The original concern that motivated this phase — "I want some model versions visible only to myself" — turned out to expose a deeper architectural issue (no namespace separation), which surface-level visibility flags would have papered over. Following the project-level root-cause-first directive, we redesign the layer rather than patch.

## Authorization

Breaking changes are explicitly authorized by the operator (PO-LIN, 2026-05-06):

- "不需考慮向後相容性，允許進行破壞性重構"
- "若問題涉及架構、抽象或模組邊界設計不良，應優先提出完整的重構或重新設計方案"
- "既有模型可以不需要保留紀錄"

Concrete breaking changes:

- All existing `model_version` rows are wiped pre-deploy via `docs/runbooks/wipe-mlflow.md`. No data preserved, no rename migration of existing MLflow `registered_model` entries.
- `Detector.name` for `elfrfdet` and `elfcnndet` are soft-deleted; replacement Detectors are onboarded under new platform slugs `elf-rf` (display: "ELF RF") and `elf-cnn` (display: "ELF 1D-CNN (multi-GPU)") respectively. GitHub repository names and Python package names inside those repos are **not required** to change (see §1.5).
- API surface under `/api/v1/models` is fully replaced by `/api/v1/models/{owner}/{name}/...` — old URL pattern is dropped. PR-A backend and PR-B frontend must deploy together (§4.1).
- New required column `User.handle` (slug, unique). Migration auto-derives handles for existing users from email prefix; collisions resolved by appending `-N`.

## Scope

### In scope

1. **`User.handle` slug column** + auto-derive migration + uniqueness constraint.
2. **`RegisteredModel` entity** with `(owner_id, detector_id)` uniqueness; carries `description` and `tags`.
3. **`ModelVersion` refactor**: drop `mlflow_name`; add FK `registered_model_id`; add `visibility` enum column.
4. **Two audit log tables**: `model_visibility_log`, `model_owner_transfer_log`.
5. **All `/api/v1/models` endpoints rewritten** under GitHub-style URL pattern `/api/v1/models/{owner}/{name}/...`.
6. **Four new endpoints**: PATCH description/tags, PATCH owner transfer, DELETE registered_model, DELETE single version.
7. **One new endpoint specifically for visibility**: PATCH `/api/v1/models/{owner}/{name}/versions/{version}/visibility`.
8. **Reconciler change**: `_register_model_from_job` upserts `RegisteredModel` and uses namespaced MLflow name.
9. **Predict job validation**: new helper `_load_model_version_for_predict` mirrors `_load_dataset` pattern.
10. **Detector ownership gate at job creation** is **NOT** added (the original Section 1.4 patch is rejected — namespace separation makes it unnecessary).
11. **Frontend route refactor** to `_authed.models.$owner.$name.tsx`.
12. **Seven new frontend components** + `react-markdown` dependency.
13. **i18n keys** in `en.json` + `zh-TW.json` for all new UI surfaces.
14. **Operational rebuild** of `elfrfdet → elf-rf` and `elfcnndet → elf-cnn`, using minimum-path approach (no GitHub repo rename, no Python package rename required).
15. **Test coverage**: ~50 new backend pytest cases, frontend vitest unit + integration, Playwright E2E spec covering namespace collision and full feature flow.

### Out of scope

- Detector-level visibility (public/private detectors). Currently any authenticated user can train against any registered detector; this phase keeps that behaviour unchanged.
- Pill-style tag input UI. Phase 1 ships JSON `Textarea`; pill input is polish, not blocker.
- Owner profile page (`/models/{owner}` showing all of one user's models). Backend `?owner=` filter exists; frontend implements only when concrete need arises.
- Direct model-artefact download from lolday UI. Operator continues to use MLflow UI / API for raw artefact access.
- Migration of existing model versions (data wiped per Authorization).
- Renaming GitHub repositories `bolin8017/elfrfdet` / `bolin8017/elfcnndet`, or Python package names `src/elfrfdet/` / `src/elfcnndet/`. The platform's `Detector.name` is independent (`detectors.py:227`).
- Per-user training quota / rate limit. ISLab is internal trusted environment.
- Cosign / sigstore signing of model artefacts.

## Architecture decisions

| #      | Decision                                                                                                                                                  | Mainstream rationale                                                                                                                                                                                 |
| ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **D1** | HuggingFace-style namespace `{handle}/{detector_name}` replaces detector ownership gate. Any user trains any detector; output goes to per-user namespace. | HuggingFace, Databricks Unity Catalog (`catalog.schema.model`), Vertex AI all rely on namespace separation rather than ACL gates. Simpler, more flexible, no false-positive 403 for the common case. |
| **D2** | New `User.handle` column (slug, unique, indexed). Migration derives handles from email prefix; collisions append `-N`.                                    | HuggingFace, GitHub require slug-safe handles separate from email. Email isn't slug-safe (`@.`); UUID is unsuitable for URLs. Mainstream design pattern.                                             |
| **D3** | New `RegisteredModel` entity with `(owner_id, detector_id)` unique constraint; carries `description` (text), `tags` (JSONB).                              | MLflow's own data model has a `registered_models` table; lolday's previous flat `mlflow_name` string was denormalized. Standard SQL normalization.                                                   |
| **D4** | `ModelVersion.mlflow_name` removed; replaced by FK `registered_model_id`.                                                                                 | Avoid duplicated string state. MLflow name becomes a derived property (`f"{owner.handle}/{detector.name}"`), so handle/detector renames propagate automatically without DB updates.                  |
| **D5** | API URL pattern `/api/v1/models/{owner_handle}/{name}/...`.                                                                                               | GitHub `/repos/{owner}/{repo}` and HuggingFace `/api/models/{owner}/{name}` are mainstream. Two path segments cleanly identify the entity.                                                           |
| **D6** | MLflow `registered_model.name` follows `{handle}/{detector.name}` (slash allowed in MLflow names).                                                        | HuggingFace-style. MLflow API supports slash. No data migration needed because all existing data is wiped per Authorization.                                                                         |
| **D7** | Per-version visibility on `ModelVersion`, default `private`. Mirrors `DatasetConfig.visibility` exactly (column + index + filter logic).                  | Same access pattern as datasets; users learn the model once. HuggingFace defaults new repos to private. Secure-by-default.                                                                           |

> **D1 implication**: the previously approved "Section 1.4 detector ownership gate" is reverted. `jobs.py:120` does **not** gain an ownership check. Anyone can submit a train job against any detector; namespace separation prevents collision.

---

## Section 1 — Data model + migration

### 1.1 New entities

#### (a) `User.handle`

```python
# backend/app/models/user.py
class User(Base):
    ...
    handle: Mapped[str] = mapped_column(
        String(60),
        unique=True,
        nullable=False,
        index=True,
    )
```

Slug rules (HF / GitHub consensus):

- Character set: `[a-z0-9_-]`
- Must start with a letter
- No trailing `-`, no consecutive `--`
- Length 1–60
- Migration derivation: lowercase email prefix → replace invalid chars with `-` → ensure it starts with a letter (prepend `u-` if needed) → if empty, fallback to first 8 chars of UUID
- Collision resolution: append `-2`, `-3`, …, until unique
- Future user creation: `auth/cf_access.py` derives + assigns handle on first login (idempotent — re-derive matches existing on subsequent logins)

#### (b) `RegisteredModel` table

```python
# backend/app/models/model_registry.py
class RegisteredModel(Base):
    __tablename__ = "registered_model"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="RESTRICT"), nullable=False
    )
    detector_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("detector.id", ondelete="RESTRICT"), nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text)  # markdown, soft cap 5000
    tags: Mapped[dict] = mapped_column(_JSONB, default=dict)  # str → str
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    owner: Mapped["User"] = relationship()
    detector: Mapped["Detector"] = relationship()

    __table_args__ = (
        UniqueConstraint("owner_id", "detector_id", name="uq_registered_model_owner_detector"),
        Index("ix_registered_model_owner", "owner_id"),
    )

    @property
    def mlflow_name(self) -> str:
        """Derived; follows MLflow registered_model.name convention."""
        return f"{self.owner.handle}/{self.detector.name}"
```

> Storing `mlflow_name` as a derived property (not a column) means handle changes (D2 future) and detector rename (operator action) propagate automatically with no UPDATE pass.

#### (c) `ModelVersion` refactor

```python
class ModelVersion(Base):
    __tablename__ = "model_version"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # mlflow_name REMOVED — derived from registered_model
    registered_model_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("registered_model.id", ondelete="CASCADE"), nullable=False
    )
    mlflow_version: Mapped[int] = mapped_column(Integer, nullable=False)
    mlflow_run_id: Mapped[str] = mapped_column(String(50), nullable=False)
    current_stage: Mapped[ModelVersionStage] = mapped_column(...)  # unchanged
    visibility: Mapped[ModelVersionVisibility] = mapped_column(
        SAEnum(
            ModelVersionVisibility,
            name="model_version_visibility_enum",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=ModelVersionVisibility.PRIVATE,
        nullable=False,
    )
    detector_version_id: Mapped[uuid.UUID] = mapped_column(...)  # unchanged
    source_job_id: Mapped[uuid.UUID] = mapped_column(...)  # unchanged
    owner_id: Mapped[uuid.UUID] = mapped_column(...)  # unchanged
    created_at: Mapped[datetime] = mapped_column(...)  # unchanged
    last_transitioned_at: Mapped[datetime] = mapped_column(...)  # unchanged

    __table_args__ = (
        UniqueConstraint("registered_model_id", "mlflow_version", name="uq_model_version_per_registered"),
        Index("ix_model_version_registered_model", "registered_model_id"),
        Index("ix_model_version_owner", "owner_id"),
        Index("ix_model_version_stage", "current_stage"),
        Index("ix_model_version_visibility", "visibility"),
    )


class ModelVersionVisibility(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"
```

> A new `model_version_visibility_enum` (postgres ENUM type) is created rather than reusing `dataset_visibility_enum`. Per-entity enum is mainstream Django/SQLAlchemy convention; future divergence (e.g. team-shared dataset visibility) won't accidentally bleed into model semantics.

### 1.2 Audit log tables

```python
class ModelVisibilityLog(Base):
    __tablename__ = "model_visibility_log"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    model_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("model_version.id", ondelete="CASCADE"), nullable=False
    )
    from_visibility: Mapped[ModelVersionVisibility] = mapped_column(...)
    to_visibility: Mapped[ModelVersionVisibility] = mapped_column(...)
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_model_visibility_log_version", "model_version_id"),)


class ModelOwnerTransferLog(Base):
    __tablename__ = "model_owner_transfer_log"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    registered_model_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("registered_model.id", ondelete="CASCADE"), nullable=False
    )
    from_owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False)
    to_owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    transferred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_model_owner_transfer_log_model", "registered_model_id"),)
```

> Two narrow per-event tables instead of one generalised `ModelEventLog`. Domain-specific tables match Stripe / GitHub Audit Log API patterns; extending later (third event type) is cheaper than splitting a polymorphic table.

### 1.3 Migration (single Alembic revision)

Existing model data is wiped before migration runs (operator pre-deploy step §4.4). User rows persist; their handles must be backfilled.

```
Step 1: ALTER TABLE "user" ADD COLUMN handle VARCHAR(60) (nullable=True)
Step 2: For each existing user, derive handle via the slug rules in §1.1(a),
        resolving collisions; UPDATE with derived value
Step 3: ALTER TABLE "user" ALTER COLUMN handle SET NOT NULL
Step 4: CREATE UNIQUE INDEX ix_user_handle ON "user"(handle)
Step 5: CREATE TYPE model_version_visibility_enum AS ENUM ('public', 'private')
Step 6: CREATE TABLE registered_model (...)
Step 7: CREATE TABLE model_visibility_log (...)
Step 8: CREATE TABLE model_owner_transfer_log (...)
Step 9: ALTER TABLE model_version
          DROP COLUMN mlflow_name,
          ADD COLUMN registered_model_id UUID NOT NULL,
          ADD CONSTRAINT fk_model_version_registered_model
            FOREIGN KEY (registered_model_id) REFERENCES registered_model(id) ON DELETE CASCADE,
          ADD COLUMN visibility model_version_visibility_enum NOT NULL DEFAULT 'private',
          ADD CONSTRAINT uq_model_version_per_registered
            UNIQUE (registered_model_id, mlflow_version);
        ALTER COLUMN visibility DROP DEFAULT;
        DROP INDEX ix_model_version_name_version_unique;
        CREATE INDEX ix_model_version_registered_model ON model_version(registered_model_id);
        CREATE INDEX ix_model_version_visibility ON model_version(visibility);
```

> Step 9 NOT NULL on `registered_model_id` is safe because existing `model_version` rows have been wiped (count = 0). If wipe was skipped, this step would fail loudly — by design.

`downgrade()` reverses all steps; collected in chronological reverse.

### 1.4 No detector ownership gate at job creation

`backend/app/routers/jobs.py:120-121` is intentionally **not** modified. Any authenticated user can submit a train / test / predict job against any registered Detector. Output flows into the user's own `RegisteredModel` namespace — no collision, no need for a write gate.

This deviates from the original Section 1.4 patch (already approved earlier in brainstorm) once D1 was adopted. Per project root-cause directive, the namespace fix supersedes the gate fix.

### 1.5 Operational rebuilds (elf-rf / elf-cnn)

`Detector.name` is independent from GitHub repository name and from `maldet.toml [detector].name` (`detectors.py:227` confirms `body.name` overrides manifest). Minimum-path rebuild:

| Step                                                                                                                    | Action                                                                 | Required? |
| ----------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- | --------- |
| Soft-delete existing `elfrfdet` Detector via UI                                                                         | yes                                                                    |
| Soft-delete existing `elfcnndet` Detector via UI                                                                        | yes                                                                    |
| Onboard new Detector with `body.name="elf-rf"`, `body.display_name="ELF RF"`, `git_url=` existing repo                  | yes                                                                    |
| Onboard new Detector with `body.name="elf-cnn"`, `body.display_name="ELF 1D-CNN (multi-GPU)"`, `git_url=` existing repo | yes                                                                    |
| Rename GitHub repo `elfrfdet → elf-rf`                                                                                  | no (recommended only; GitHub redirects)                                |
| Rename `src/elfrfdet/` → `src/elf_rf/` and update `_target_` references                                                 | no (nice-to-have for consistency)                                      |
| Update `maldet.toml [detector].name = "elf-rf"` and `[detector].display_name = "ELF RF"`                                | recommended (consistency in build labels and validator error messages) |

Same flow applied to `elfcnndet` → `elf-cnn`.

### 1.6 Edge cases solved

| Edge case                                                          | Resolution                                                                                           |
| ------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------- |
| User-B accidentally appends to user-A's `fraud_detector` namespace | Each user has their own `{handle}/{detector_name}`; no collision possible.                           |
| Multiple users want to collaborate on the same detector            | Each trains in their own namespace; sharing flows through `ModelVersion.visibility = public`.        |
| Detector rename should propagate to MLflow registered_model name   | `mlflow_name` is a derived property; rename is automatic, no UPDATE pass.                            |
| Owner transfer should propagate namespace name                     | New owner's handle replaces old; PATCH endpoint also calls MLflow `rename_registered_model` (§2.5b). |

---

## Section 2 — API surface + service layer

### 2.1 Endpoint matrix

| Method | Path                                                          | Action                                                        | Auth                |
| ------ | ------------------------------------------------------------- | ------------------------------------------------------------- | ------------------- | ------ | -------- |
| GET    | `/api/v1/models`                                              | list registered_models (filter: `?owner=`, `?visibility=all   | public              | mine`) | any user |
| GET    | `/api/v1/models/{owner}/{name}`                               | summary (description, tags, latest stage versions)            | visibility-filtered |
| PATCH  | `/api/v1/models/{owner}/{name}`                               | update description / tags                                     | owner + admin       |
| PATCH  | `/api/v1/models/{owner}/{name}/owner`                         | transfer ownership                                            | owner + admin       |
| DELETE | `/api/v1/models/{owner}/{name}`                               | cascade-delete model + all versions + MLflow registered_model | owner + admin       |
| GET    | `/api/v1/models/{owner}/{name}/versions`                      | list versions                                                 | visibility-filtered |
| GET    | `/api/v1/models/{owner}/{name}/versions/{version}`            | version detail                                                | visibility-filtered |
| PATCH  | `/api/v1/models/{owner}/{name}/versions/{version}/visibility` | toggle public/private                                         | owner + admin       |
| POST   | `/api/v1/models/{owner}/{name}/versions/{version}/transition` | stage transition (existing logic, new URL)                    | owner + admin       |
| DELETE | `/api/v1/models/{owner}/{name}/versions/{version}`            | delete single version                                         | owner + admin       |

`{owner}` = `User.handle`; `{name}` = `Detector.name`.

### 2.2 Resolver helper

```python
# backend/app/services/model_registry.py

async def _resolve_registered_model(
    owner: str, name: str,
    session: AsyncSession, user: User,
    *, write: bool = False,
) -> RegisteredModel:
    """Centralised access control mirroring datasets._get_readable / _writable."""
    rm = (
        await session.execute(
            select(RegisteredModel)
            .join(User, RegisteredModel.owner_id == User.id)
            .join(Detector, RegisteredModel.detector_id == Detector.id)
            .where(User.handle == owner, Detector.name == name)
        )
    ).scalar_one_or_none()
    if rm is None:
        raise HTTPException(404, "model not found")

    is_owner = rm.owner_id == user.id
    is_admin = user.role.value == "admin"

    if write and not (is_owner or is_admin):
        raise HTTPException(403, "owner or admin only")

    if not write and not (is_owner or is_admin):
        # Read path: must have at least one publicly-visible version
        any_visible = (
            await session.execute(
                select(func.count())
                .select_from(ModelVersion)
                .where(
                    ModelVersion.registered_model_id == rm.id,
                    ModelVersion.visibility == ModelVersionVisibility.PUBLIC,
                )
            )
        ).scalar()
        if not any_visible:
            raise HTTPException(404, "model not found")  # hide-existence

    return rm
```

### 2.3 List endpoint — single SQL with conditional aggregation

Replaces the existing N+1 `loop calls per name` pattern in the current `list_registered_models`.

```python
@router.get("", response_model=list[RegisteredModelSummary])
async def list_models(
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
    owner: str | None = Query(None),
    visibility: Literal["all", "public", "mine"] = "all",
) -> list[RegisteredModelSummary]:
    visible = (
        ModelVersion.visibility == ModelVersionVisibility.PUBLIC
    ) | (ModelVersion.owner_id == user.id)
    if user.role == Role.ADMIN:
        visible = sa.true()

    stmt = (
        select(
            User.handle.label("owner"),
            Detector.name.label("name"),
            RegisteredModel.description,
            RegisteredModel.tags,
            func.max(ModelVersion.mlflow_version).label("latest_version"),
            func.max(case(
                (ModelVersion.current_stage == ModelVersionStage.PRODUCTION,
                 ModelVersion.mlflow_version), else_=None
            )).label("latest_production_version"),
            func.max(case(
                (ModelVersion.current_stage == ModelVersionStage.STAGING,
                 ModelVersion.mlflow_version), else_=None
            )).label("latest_staging_version"),
        )
        .select_from(RegisteredModel)
        .join(User, RegisteredModel.owner_id == User.id)
        .join(Detector, RegisteredModel.detector_id == Detector.id)
        .join(ModelVersion, ModelVersion.registered_model_id == RegisteredModel.id)
        .where(visible)
        .group_by(RegisteredModel.id, User.handle, Detector.name)
    )

    if owner is not None:
        stmt = stmt.where(User.handle == owner)
    if visibility == "public":
        stmt = stmt.having(
            func.count(case(
                (ModelVersion.visibility == ModelVersionVisibility.PUBLIC, 1),
                else_=None
            )) > 0
        )
    elif visibility == "mine":
        stmt = stmt.where(RegisteredModel.owner_id == user.id)

    rows = (await session.execute(stmt)).all()
    return [RegisteredModelSummary(**r._mapping) for r in rows]
```

CASE-inside-MAX is mainstream conditional aggregation (Postgres / MySQL / SQLite all support).

### 2.4 Reconciler change — `_register_model_from_job`

```python
# backend/app/reconciler/jobs.py

async def _register_model_from_job(session, client, j):
    dv = await session.get(DetectorVersion, j.detector_version_id)
    det = await session.get(Detector, dv.detector_id)
    owner = await session.get(User, j.owner_id)

    # Upsert RegisteredModel (owner, detector)
    rm = (await session.execute(
        select(RegisteredModel).where(
            RegisteredModel.owner_id == owner.id,
            RegisteredModel.detector_id == det.id,
        )
    )).scalar_one_or_none()
    if rm is None:
        rm = RegisteredModel(owner_id=owner.id, detector_id=det.id)
        session.add(rm)
        await session.flush()

    mlflow_name = f"{owner.handle}/{det.name}"
    await client.create_registered_model(mlflow_name)  # idempotent in MLflow
    mv_resp = await client.create_model_version(
        name=mlflow_name,
        source=f"runs:/{j.mlflow_run_id}/model",
        run_id=j.mlflow_run_id,
    )

    mv = ModelVersion(
        registered_model_id=rm.id,
        mlflow_version=int(mv_resp["version"]),
        mlflow_run_id=j.mlflow_run_id,
        current_stage=ModelVersionStage.NONE,
        visibility=ModelVersionVisibility.PRIVATE,
        detector_version_id=j.detector_version_id,
        source_job_id=j.id,
        owner_id=j.owner_id,
    )
    session.add(mv)
```

### 2.5 New PATCH endpoints

#### (a) Description / tags

```python
class RegisteredModelUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=5000)
    tags: dict[str, str] | None = None  # zod-equivalent: flat str->str

@router.patch("/{owner}/{name}", response_model=RegisteredModelRead)
async def update_model(
    owner: str, name: str, body: RegisteredModelUpdate,
    session, user,
) -> RegisteredModelRead:
    rm = await _resolve_registered_model(owner, name, session, user, write=True)
    if body.description is not None:
        rm.description = body.description
    if body.tags is not None:
        # Validate flat dict[str, str]
        for k, v in body.tags.items():
            if not isinstance(v, str):
                raise HTTPException(422, f"tag value for '{k}' must be string")
        rm.tags = body.tags
    await session.commit()
    return RegisteredModelRead.model_validate(rm)
```

#### (b) Owner transfer

```python
class OwnerTransferRequest(BaseModel):
    new_owner_handle: str
    comment: str | None = None

@router.patch("/{owner}/{name}/owner", response_model=RegisteredModelRead)
async def transfer_owner(
    owner: str, name: str, body: OwnerTransferRequest,
    session, user, client: MlflowClient,
) -> RegisteredModelRead:
    rm = await _resolve_registered_model(owner, name, session, user, write=True)

    new_owner = (await session.execute(
        select(User).where(User.handle == body.new_owner_handle)
    )).scalar_one_or_none()
    if new_owner is None:
        raise HTTPException(422, f"user '{body.new_owner_handle}' not found")
    if new_owner.id == rm.owner_id:
        raise HTTPException(422, "new owner is current owner")

    # Collision check: target user already owns a model for the same detector?
    collision = (await session.execute(
        select(RegisteredModel).where(
            RegisteredModel.owner_id == new_owner.id,
            RegisteredModel.detector_id == rm.detector_id,
        )
    )).scalar_one_or_none()
    if collision is not None:
        raise HTTPException(
            409,
            f"'{body.new_owner_handle}' already owns a model for this detector",
        )

    old_owner_id = rm.owner_id
    old_mlflow_name = rm.mlflow_name
    rm.owner_id = new_owner.id
    new_mlflow_name = f"{new_owner.handle}/{rm.detector.name}"

    await client.rename_registered_model(old_mlflow_name, new_mlflow_name)

    session.add(ModelOwnerTransferLog(
        registered_model_id=rm.id,
        from_owner_id=old_owner_id,
        to_owner_id=new_owner.id,
        actor_id=user.id,
        comment=body.comment,
    ))
    await session.commit()
    return RegisteredModelRead.model_validate(rm)
```

#### (c) Visibility toggle

```python
class ModelVersionVisibilityUpdate(BaseModel):
    visibility: ModelVersionVisibility
    comment: str | None = None

@router.patch(
    "/{owner}/{name}/versions/{version}/visibility",
    response_model=ModelVersionRead,
)
async def update_visibility(
    owner: str, name: str, version: int, body: ModelVersionVisibilityUpdate,
    session, user,
) -> ModelVersionRead:
    rm = await _resolve_registered_model(owner, name, session, user, write=True)
    mv = (await session.execute(
        select(ModelVersion).where(
            ModelVersion.registered_model_id == rm.id,
            ModelVersion.mlflow_version == version,
        )
    )).scalar_one_or_none()
    if mv is None:
        raise HTTPException(404, "version not found")

    if mv.visibility == body.visibility:
        return ModelVersionRead.model_validate(mv)  # no-op, no log

    session.add(ModelVisibilityLog(
        model_version_id=mv.id,
        from_visibility=mv.visibility,
        to_visibility=body.visibility,
        actor_id=user.id,
        comment=body.comment,
    ))
    mv.visibility = body.visibility
    await session.commit()
    return ModelVersionRead.model_validate(mv)
```

### 2.6 DELETE endpoints (cascade)

```python
@router.delete("/{owner}/{name}", status_code=204)
async def delete_model(owner, name, session, user, client):
    rm = await _resolve_registered_model(owner, name, session, user, write=True)
    await client.delete_registered_model(rm.mlflow_name)  # MLflow: cascade versions
    await session.delete(rm)  # DB: cascade via FK ondelete
    await session.commit()


@router.delete("/{owner}/{name}/versions/{version}", status_code=204)
async def delete_version(owner, name, version, session, user, client):
    rm = await _resolve_registered_model(owner, name, session, user, write=True)
    mv = await _get_version(rm, version, session)
    await client.delete_model_version(rm.mlflow_name, str(version))
    await session.delete(mv)  # cascade visibility log
    await session.commit()
```

`ondelete=CASCADE` declared on `ModelVersion.registered_model_id` and `ModelVisibilityLog.model_version_id` enables the cascade.

### 2.7 Predict job validation helper

```python
# backend/app/routers/jobs.py

async def _load_model_version_for_predict(
    mv_id: uuid.UUID | None, session: AsyncSession, user: User,
) -> ModelVersion | None:
    if mv_id is None:
        return None
    mv = await session.get(ModelVersion, mv_id)
    if mv is None:
        raise HTTPException(422, "source_model_version not found")
    if (
        mv.visibility == ModelVersionVisibility.PRIVATE
        and mv.owner_id != user.id
        and user.role.value != "admin"
    ):
        raise HTTPException(422, "source_model_version not accessible")
    return mv
```

`jobs.py:138-143` is refactored to use this helper, replacing the bare `session.get` call.

### 2.8 Error code matrix

| Scenario                                                          | Status | Detail                                              |
| ----------------------------------------------------------------- | ------ | --------------------------------------------------- |
| RegisteredModel not found / completely invisible                  | 404    | `model not found` (hide-existence)                  |
| ModelVersion not found / private + non-owner                      | 404    | `version not found` (hide-existence)                |
| Predict with private ModelVersion (non-owner)                     | 422    | `source_model_version not accessible`               |
| Mutate description / tags / visibility / delete (non-owner+admin) | 403    | `owner or admin only`                               |
| Transfer to user already owning same detector                     | 409    | `'{handle}' already owns a model for this detector` |
| Transfer target user not found                                    | 422    | `user '{handle}' not found`                         |
| Tags JSON not flat str→str                                        | 422    | `tag value for '{k}' must be string`                |
| List endpoint                                                     | 200    | filtered output, no error                           |

---

## Section 3 — Frontend

### 3.1 Route refactor

```
src/routes/
├── _authed.models._index.tsx          (list page, unchanged file path)
└── _authed.models.$owner.$name.tsx    (detail page; was $name.tsx)
```

URL examples:

- `/models` — visible registered_models for current user
- `/models/bolin8017/elf-rf` — detail page

### 3.2 List page

Columns: Model (`{owner}/{name}`), Description (truncated 80 char), Latest, Staging, Production, Created. Visibility filter `Select` (All / Public / Mine) added next to existing PageHeader actions (mirrors datasets pattern). Optional owner filter input (`?owner=` query param).

A small visibility hint icon on each row: 🌐 if any version public, 🔒 if all private (admin sees all).

### 3.3 Detail page layout

```
┌───────────────────────────────────────────────────────────┐
│ [← Back to Models]                                         │
│                                                            │
│ bolin8017 / elf-rf                                [⋮ Menu] │
│ └ 🌐 4 public · 🔒 1 private                                │
│ └ Owner: 👤 bolin8017 · Created 2 days ago                 │
│                                                            │
│ ─── Description ──────────────────────────────────         │
│ (markdown rendered, with [Edit] for owner+admin)           │
│                                                            │
│ ─── Tags ────────────────────────────────────────         │
│ [framework=sklearn] [contract=sample_csv] [+ Edit tags]    │
│                                                            │
│ ─── Versions ────────────────────────────────────         │
│ Version  Stage         Visibility   Run    Created   [⋮]   │
│ v5       [Production]  🌐 Public    r-...  2d ago    [⋮]   │
│ ...                                                        │
└───────────────────────────────────────────────────────────┘
```

Top-right [⋮ Menu] (owner+admin only):

- Edit description
- Edit tags
- ─────────
- Transfer ownership…
- Delete model…

Per-version [⋮] (owner+admin only):

- Transition stage…
- Make public / Make private
- ─────────
- Delete version…

### 3.4 New components

| Component                | Path                                          | Purpose                                                                                 |
| ------------------------ | --------------------------------------------- | --------------------------------------------------------------------------------------- |
| `OwnerLabel`             | `components/users/OwnerLabel.tsx`             | Avatar fallback + handle                                                                |
| `VisibilityBadge`        | `components/models/VisibilityBadge.tsx`       | Globe (emerald) / Lock (slate) icon + label                                             |
| `MarkdownView`           | `components/common/MarkdownView.tsx`          | Wraps `react-markdown`, sanitises raw HTML                                              |
| `ModelDescriptionEditor` | `components/forms/ModelDescriptionEditor.tsx` | Dialog + Textarea, character counter                                                    |
| `ModelTagsEditor`        | `components/forms/ModelTagsEditor.tsx`        | Dialog + Textarea (JSON), zod validation                                                |
| `OwnerTransferDialog`    | `components/forms/OwnerTransferDialog.tsx`    | Input new_owner_handle + optional comment                                               |
| `DeleteModelDialog`      | `components/forms/DeleteModelDialog.tsx`      | Type-to-confirm `{owner}/{name}` (mirrors existing `DeleteConfirmDialog` for detectors) |
| `ModelVisibilityDialog`  | `components/forms/ModelVisibilityDialog.tsx`  | Public↔Private warning + comment + submit                                               |

New dependency: `react-markdown` (~30KB gzip, MIT, weekly downloads 8M+, CSP-safe pure React).

### 3.5 Tags input — JSON Textarea (Phase 1)

```typescript
const TagsSchema = z.record(z.string());

// shadcn Textarea + zod parse on submit
const result = TagsSchema.safeParse(JSON.parse(textareaValue));
if (!result.success) {
  toast.error(t("models.tags.schemaError"));
  return;
}
mutate({ tags: result.data });
```

Pill-style tag input is deferred (Future Work) — JSON textarea unblocks shipping.

### 3.6 Type-to-confirm DeleteModelDialog

```tsx
const fullName = `${owner}/${name}`;
const matches = confirm === fullName;
// disabled until confirm input matches fullName exactly
```

GitHub repo delete uses the same UX — type the repo name to confirm. Existing `DeleteConfirmDialog.test.tsx` for detectors is the pattern reference.

### 3.7 i18n keys (zh-TW + en)

```jsonc
"models": {
  "owner": "Owner / 擁有者",
  "transfer": {
    "title": "Transfer ownership / 轉移擁有權",
    "description": "Move this model to another user. The new owner cannot already own a model for the same detector.",
    "newOwnerLabel": "New owner handle",
    "warning": "Other users may lose write access. Existing predict jobs continue.",
    "submit": "Transfer",
    "successToast": "Ownership transferred"
  },
  "delete": {
    "title": "Delete model / 刪除模型",
    "warning": "This action permanently deletes the model and all versions.",
    "confirmPrompt": "Type {{fullName}} to confirm",
    "successToast": "Model deleted"
  },
  "description": {
    "title": "Description",
    "edit": "Edit description",
    "placeholder": "Markdown supported. Document your model usage, training data, evaluation results...",
    "successToast": "Description updated"
  },
  "tags": {
    "title": "Tags",
    "edit": "Edit tags",
    "placeholder": "{ \"framework\": \"sklearn\", \"contract\": \"sample_csv\" }",
    "schemaError": "Tags must be a flat JSON object of string keys to string values",
    "successToast": "Tags updated"
  },
  "visibility": {
    "public": "Public",
    "private": "Private",
    "publicTooltip": "Visible and usable by all authenticated users.",
    "privateTooltip": "Only you (owner) and admins can see or use this version.",
    "makePublic": "Make public",
    "makePrivate": "Make private",
    "warningPrivate": "Other users will lose access for new predict jobs. Existing jobs continue running.",
    "warningPublic": "All authenticated users will be able to view and use this version.",
    "changedToast": "Visibility updated"
  }
}
```

zh-TW uses 公開 / 私有 / 轉移擁有權 / 刪除模型 / 編輯描述 / 編輯標籤. Existing `models.stages.*` keys (Staging / Production / etc.) unchanged.

### 3.8 TanStack Query mutations

- `useUpdateModelDescription({ owner, name, description })`
- `useUpdateModelTags({ owner, name, tags })`
- `useTransferOwner({ owner, name, new_owner_handle, comment })`
- `useDeleteModel({ owner, name })` — navigates to `/models` on success
- `useDeleteModelVersion({ owner, name, version })`
- `useUpdateVisibility({ owner, name, version, visibility, comment })`

Cache invalidation: `["models"]` (list) and `["models", owner, name]` (detail).

### 3.9 Tests (Frontend)

```
unit/components/models/
├ VisibilityBadge.test.tsx
├ OwnerLabel.test.tsx
└ MarkdownView.test.tsx (renders headings/lists/code; sanitises raw HTML)

unit/components/forms/
├ ModelDescriptionEditor.test.tsx
├ ModelTagsEditor.test.tsx (zod rejects non-string values)
├ OwnerTransferDialog.test.tsx (handle required, disabled until typed)
├ DeleteModelDialog.test.tsx (type-to-confirm gating)
└ ModelVisibilityDialog.test.tsx

unit/api/queries/models.*.test.tsx
└ 6 mutation hooks × success / 4xx paths

e2e/
├ model-visibility.spec.ts
├ model-description-tags.spec.ts (owner edits, non-owner sees no edit button)
├ model-transfer-owner.spec.ts (URL update, MLflow rename observed)
├ model-delete.spec.ts (type-to-confirm, cascade)
├ model-namespace-collision.spec.ts (two users, separate namespaces)
└ phase11e-full-flow.spec.ts (extend: default-private, namespace display)
```

CSP unchanged (`script-src 'self'`); `react-markdown` is pure React, no inline scripts.

---

## Section 4 — PR breakdown + validation

### 4.1 PR breakdown

| PR                 | Scope                                                                                                                                | Coupling note                                                                  |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------ |
| **PR-A: Backend**  | Migration, models, schemas, all `/api/v1/models/*` endpoints, reconciler, helper `_load_model_version_for_predict`, ~50 pytest cases | Atomic API contract change — old `/models/{name}` URL pattern is fully removed |
| **PR-B: Frontend** | Route refactor, 7 new components, react-markdown dep, i18n keys, 6 mutation hooks, vitest + Playwright                               | Depends on PR-A's regenerated `schema.gen.ts`                                  |

PR-A merges first; once green, PR-B regenerates types and merges. Operator deploys both at once via `bash scripts/deploy.sh`.

> Single coordinated deploy is mainstream for atomic API contract changes (Stripe, Linear). Splitting into micro-PRs would create broken intermediate states with negative review/coordination value.

### 4.2 Backend test matrix

```
backend/tests/test_user_handle.py
├ handle_derived_from_email_prefix
├ handle_collision_appends_suffix
├ handle_invalid_chars_replaced
├ handle_starts_with_digit_prepend
├ handle_unique_constraint_enforced
└ migration_backfills_existing_users

backend/tests/test_registered_model.py
├ unique_constraint_owner_detector
├ mlflow_name_derived_from_handle_and_detector
├ handle_change_propagates_to_mlflow_name
└ detector_rename_propagates_to_mlflow_name

backend/tests/test_model_visibility.py
├ default_visibility_is_private
├ list_visible_to_owner / public_only / mine_only / admin_sees_all
├ get_version_404_for_non_owner_private
├ get_version_200_for_owner / admin / public
├ patch_visibility_owner / admin / non_owner_403
├ patch_visibility_writes_audit_log / noop_no_log
└ audit_log_chronological_order

backend/tests/test_model_metadata.py
├ patch_description_owner_succeeds
├ patch_description_non_owner_403
├ patch_tags_validates_flat_string_dict
├ patch_tags_rejects_nested_object
└ description_markdown_safe (raw HTML stripped)

backend/tests/test_owner_transfer.py
├ transfer_owner_succeeds
├ transfer_owner_writes_audit_log
├ transfer_owner_renames_mlflow_registered_model
├ transfer_owner_collision_409
├ transfer_owner_target_user_not_found_422
├ transfer_owner_self_422
└ transfer_owner_non_owner_403

backend/tests/test_model_delete.py
├ delete_model_cascades_versions
├ delete_model_calls_mlflow_delete
├ delete_model_owner_succeeds / non_owner_403
├ delete_version_cascades_visibility_log
└ delete_version_keeps_other_versions

backend/tests/test_jobs_access_control.py
├ predict_with_private_model_version_non_owner_422
├ predict_with_private_model_version_owner_succeeds
├ predict_with_public_model_version_any_user_succeeds
└ train_against_any_detector_any_user (verifies Section 1.4 NOT added)

backend/tests/test_model_namespace.py
├ two_users_train_same_detector_get_separate_namespaces
├ userA_cannot_see_userB_private_versions_in_list
├ admin_sees_all_namespaces
└ register_model_idempotent_for_same_owner_detector
```

`test_services_model_registry.py` (stage transition, existing) is untouched.

### 4.3 Pre-deploy operator checklist

```
☐ 1. Maintenance window broadcast (existing Discord channel)
☐ 2. UI soft-delete: Detectors elfrfdet, elfcnndet
☐ 3. Run docs/runbooks/wipe-mlflow.md (full MLflow wipe + gc)
☐ 4. Wipe lolday DB tables that reference model state (BEFORE schema migration; required so Step 9 NOT NULL succeeds):
       kubectl exec backend -- psql $DATABASE_URL -c "
         DELETE FROM model_transition_log;
         DELETE FROM model_version;
       "
☐ 5. PgSQL backup: pg_dump lolday > backup-pre-handle-migration.sql
☐ 6. PR-A merged, CI green, image built
☐ 7. PR-B merged, CI green, image built
☐ 8. bash scripts/deploy.sh
☐ 9. Wait for backend pod ready; alembic auto-runs upgrade head
☐ 10. Verify alembic version: kubectl exec backend -- alembic current
☐ 11. Verify all user.handle non-NULL: SELECT count(*) FROM "user" WHERE handle IS NULL  -- expect 0
```

> Step 4 is required because §1.3 Step 9 promotes `registered_model_id` to NOT NULL. Pre-existing `model_version` rows would lack a value and the migration would fail. The wipe is destructive but explicitly authorised (see "Authorization").

Estimated maintenance window: 30–60 minutes.

### 4.4 Post-deploy validation (5 buckets, 34 steps)

#### Bucket 1 — Schema sanity (4 steps)

| #   | Action                                                               | Expected                                                           |
| --- | -------------------------------------------------------------------- | ------------------------------------------------------------------ |
| 1.1 | `SELECT handle FROM "user"`                                          | All non-NULL, slug-safe, unique                                    |
| 1.2 | `\dt registered_model model_visibility_log model_owner_transfer_log` | Three tables exist                                                 |
| 1.3 | `\d model_version`                                                   | Has `registered_model_id` FK + `visibility` enum; no `mlflow_name` |
| 1.4 | `SELECT count(*) FROM model_version`                                 | = 0 (post-wipe)                                                    |

#### Bucket 2 — elf-rf rebuild (user-A = bolin8017, 12 steps)

| #    | Action                                                                         | Expected                                                                                 |
| ---- | ------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------- |
| 2.1  | UI onboard: name=`elf-rf`, display=`ELF RF`, git_url=existing repo (no rename) | Detector row created                                                                     |
| 2.2  | Build via UI                                                                   | Harbor pushes `harbor.lolday.svc/lolday/elf-rf:v3.0.1`                                   |
| 2.3  | Train job 1                                                                    | SUCCEEDED; `RegisteredModel(bolin8017, elf-rf)` upserted; ModelVersion v1                |
| 2.4  | Detail page shows `bolin8017/elf-rf` v1, Lock badge                            | ✓                                                                                        |
| 2.5  | Switch to user-B, list models                                                  | elf-rf invisible                                                                         |
| 2.6  | Switch back to user-A, toggle v1 → public                                      | Globe badge; visibility log row                                                          |
| 2.7  | user-B list                                                                    | Sees `bolin8017/elf-rf` v1                                                               |
| 2.8  | user-B predict with v1                                                         | SUCCESS                                                                                  |
| 2.9  | user-A toggle v1 → private                                                     | Lock                                                                                     |
| 2.10 | user-B predict with v1                                                         | **422 source_model_version not accessible**                                              |
| 2.11 | user-B trains against elf-rf detector                                          | **SUCCESS** (D1 — no gate); creates `RegisteredModel(userB, elf-rf)`, separate namespace |
| 2.12 | Both users list                                                                | `bolin8017/elf-rf` and `userB/elf-rf` shown side-by-side                                 |

#### Bucket 3 — elf-cnn parallel (12 steps, mirror of Bucket 2)

3.1–3.12: Same flow as Bucket 2 with detector `elf-cnn`, GPU resource profile. Validates that elfcnndet repo also works without git/Python rename.

#### Bucket 4 — Description / tags / transfer (6 steps)

| #   | Action                                                                                                  | Expected                                                                                  |
| --- | ------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| 4.1 | user-A edits description on `bolin8017/elf-rf` (markdown: headings, code, list)                         | Renders correctly; CSP doesn't block                                                      |
| 4.2 | Edit tags `{"framework":"sklearn","contract":"sample_csv"}`                                             | Pills displayed                                                                           |
| 4.3 | Edit tags `{"nested":{"bad":"value"}}`                                                                  | zod 422 + toast                                                                           |
| 4.4 | user-B opens `bolin8017/elf-rf` detail                                                                  | No Edit description / Edit tags in [⋮] (owner-only UI hide)                               |
| 4.5 | user-A transfers `bolin8017/elf-cnn` to user-B                                                          | URL updates to `/models/userB/elf-cnn`; MLflow registered_model renamed; transfer log row |
| 4.6 | user-A attempts to transfer `bolin8017/elf-rf` to user-B (user-B already owns `userB/elf-rf` from 2.11) | **409 already owns a model for this detector**                                            |

#### Bucket 5 — Delete + cascade (4 steps)

| #   | Action                                                                 | Expected                                                                                |
| --- | ---------------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| 5.1 | user-A deletes `bolin8017/elf-rf` (type-to-confirm `bolin8017/elf-rf`) | 204; DB cascade all versions + visibility logs; MLflow `delete_registered_model` called |
| 5.2 | List models                                                            | `bolin8017/elf-rf` gone; `userB/elf-rf` still present                                   |
| 5.3 | user-B deletes single version v2 of `userB/elf-rf`                     | 204; only v2 disappears; other versions retained                                        |
| 5.4 | `SELECT * FROM model_owner_transfer_log` and `model_visibility_log`    | All Bucket 2 / 4 / 5 actions logged                                                     |

**34 steps green = phase complete.**

---

## Future Work (nice-to-have, not blocking ship)

- Pill-style tag input (replaces JSON Textarea).
- Owner profile page `/models/{owner}` (backend `?owner=` filter already supported).
- Per-detector visibility (detector-level public/private — currently any registered detector is trainable by any user).
- Direct model-artefact download from lolday UI (currently routes through MLflow UI).
- Cosign / sigstore signing of model artefacts.

## References

- Repo conventions: `docs/conventions.md`
- Existing dataset visibility pattern: `backend/app/routers/datasets.py:30-40`, `backend/app/models/dataset.py:28`
- MLflow model registry: <https://mlflow.org/docs/latest/model-registry.html>
- HuggingFace Hub naming: <https://huggingface.co/docs/hub/repositories-naming>
- GitHub repo transfer (UX precedent): <https://docs.github.com/en/repositories/creating-and-managing-repositories/transferring-a-repository>
- Project root-cause directive: `~/.claude/projects/-home-bolin8017-Documents-repositories-lolday/memory/feedback_root_cause_priority.md`

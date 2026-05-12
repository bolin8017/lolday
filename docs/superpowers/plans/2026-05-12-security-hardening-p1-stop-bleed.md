# Security Hardening P1 — Stop-the-Bleed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close all CRITICAL and the subset of HIGH/MEDIUM findings from the 2026-05-12 security audit that are reachable by a single authenticated HTTP request, plus the one confirmed dependency CVE.

**Architecture:** Per-finding TDD tasks against the existing FastAPI + SQLAlchemy backend, plus three chart edits (RBAC narrowing, NetworkPolicy, DOCS_ENABLED default) and one frontend dependency override. No new services, no schema migrations, no infrastructure changes. All work lands on `main` over ~5 working days; each task is independently revertible.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, Pydantic v2, pytest + pytest-asyncio (aiosqlite for tests), Helm 3, pnpm 9.

**Source spec:** [`docs/superpowers/specs/2026-05-12-security-hardening-design.md`](../specs/2026-05-12-security-hardening-design.md) §6.1.

**Finding IDs covered:** C-1, C-2, H-1, H-2, H-3, H-4, H-5, H-6, H-20, H-24, H-25, H-28, M-WS-backdoor, M-PAT-charset, M-event-dict, M-ilike, M-docs-prod (17 findings).

---

## Pre-flight

- [ ] **Confirm clean working tree.** Run `git status` — should be clean on `main` at commit `c501654` (the umbrella spec commit) or newer.
- [ ] **Confirm test baseline passes.** Run `cd backend && uv run pytest -x -q` — should be green before any P1 task lands.

If anything fails the pre-flight, stop and ask the operator. Do not start P1 on a red baseline.

---

## Task 1: [C-2] Pin backend Dockerfile `uv` binary to a digest

**Findings:** C-2 (CRITICAL).

**Files:**

- Modify: `backend/Dockerfile:7`

**Rationale:** `COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv` blindly trusts the tag `latest`. A compromised astral-sh release would inject a backdoored binary that runs at every backend pod start. Pinning to a digest is the OCI-level equivalent of GHA SHA-pinning.

- [ ] **Step 1: Resolve the current digest for uv `0.6.16`.** (If `0.6.16` is no longer the latest stable, look up the current `uv` version at https://github.com/astral-sh/uv/releases and use that tag.)

Run:

```bash
docker buildx imagetools inspect ghcr.io/astral-sh/uv:0.6.16 --format '{{json .Manifest.Digest}}'
```

Expected output: a single line like `"sha256:abc123...def"` (64 hex chars). Record the digest.

- [ ] **Step 2: Update the Dockerfile.**

Change `backend/Dockerfile:7` from:

```dockerfile
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
```

to (substituting the digest from Step 1):

```dockerfile
COPY --from=ghcr.io/astral-sh/uv:0.6.16@sha256:<the-digest> /uv /bin/uv
```

- [ ] **Step 3: Verify the build still works locally.**

Run:

```bash
cd backend && docker build -t lolday-backend-p1c2 . --progress=plain 2>&1 | tail -20
```

Expected: build completes successfully; the resolved `COPY` source layer prints the digest you pinned, not a tag.

- [ ] **Step 4: Add a Dependabot watch entry so future bumps are automated.**

Open `.github/dependabot.yml` and confirm there is a `package-ecosystem: docker` entry covering `directory: "/backend"`. If absent, add (preserving existing formatting):

```yaml
- package-ecosystem: docker
  directory: "/backend"
  schedule:
    interval: weekly
  open-pull-requests-limit: 5
```

Run `grep -n "package-ecosystem: docker" .github/dependabot.yml | wc -l` — value should be at least the number of Docker contexts (1 + helpers). Skip the add if already present.

- [ ] **Step 5: Commit.**

```bash
git add backend/Dockerfile .github/dependabot.yml
git commit -m "fix(backend): pin uv binary in Dockerfile to sha256 digest [C-2]"
```

---

## Task 2: [H-28] Force `fast-uri >= 3.1.2` via pnpm overrides

**Findings:** H-28 (HIGH).

**Files:**

- Modify: `frontend/package.json`
- Modify: `frontend/pnpm-lock.yaml` (auto-regenerated)

**Rationale:** `@rjsf/validator-ajv8 → ajv → fast-uri` transitively pulls a `fast-uri` version with GHSA-q3j6-qgpj-74h6 (path traversal) and GHSA-v39h-62p7-jpjc (host confusion). Without a top-level override, Renovate/Dependabot won't lift the transitive.

- [ ] **Step 1: Confirm the vulnerable version is currently installed.**

Run:

```bash
cd frontend && pnpm why fast-uri
```

Expected: at least one line showing a resolved `fast-uri` version ≤ 3.1.1.

- [ ] **Step 2: Add the override block to `frontend/package.json`.**

Locate the top-level object in `frontend/package.json`. Add or extend a `"pnpm"` key with `"overrides"` so it reads exactly:

```json
  "pnpm": {
    "overrides": {
      "fast-uri": ">=3.1.2"
    }
  },
```

Insert the block immediately before the closing `}` of the JSON object (after `"devDependencies"` or whatever the last existing top-level key is). Match surrounding indentation (2 spaces).

- [ ] **Step 3: Regenerate the lockfile.**

Run:

```bash
cd frontend && pnpm install --frozen-lockfile=false
```

Expected: pnpm installs, mutates `pnpm-lock.yaml`, exits 0. No new dependency warnings.

- [ ] **Step 4: Verify the override resolved.**

Run:

```bash
cd frontend && pnpm why fast-uri
```

Expected: every resolved `fast-uri` line is ≥ 3.1.2.

- [ ] **Step 5: Commit.**

```bash
git add frontend/package.json frontend/pnpm-lock.yaml
git commit -m "fix(frontend): force fast-uri >=3.1.2 via pnpm overrides [H-28]

Closes GHSA-q3j6-qgpj-74h6 (path traversal) and GHSA-v39h-62p7-jpjc
(host confusion) reachable via @rjsf/validator-ajv8 -> ajv -> fast-uri."
```

---

## Task 3: [M-docs-prod] Default `DOCS_ENABLED` to `"false"` in values.yaml

**Findings:** M-docs-prod (MEDIUM).

**Files:**

- Modify: `charts/lolday/values.yaml:52`

**Rationale:** `/docs` and `/redoc` reveal the full API schema (including admin/internal endpoint shapes) to any SSO-authenticated USER. The dev convenience does not warrant prod exposure. `backend/app/config.py:8` already defaults to `True` for local dev; the chart default flip changes prod behaviour only.

- [ ] **Step 1: Locate the current value.**

Run:

```bash
grep -n "DOCS_ENABLED" charts/lolday/values.yaml
```

Expected: `52:    DOCS_ENABLED: "true"`.

- [ ] **Step 2: Flip the default.**

Change `charts/lolday/values.yaml:52` from:

```yaml
DOCS_ENABLED: "true"
```

to:

```yaml
DOCS_ENABLED: "false"
```

If the surrounding block has a trailing comment (it currently does not), leave it untouched.

- [ ] **Step 3: Lint the chart.**

Run:

```bash
helm lint charts/lolday
```

Expected: `1 chart(s) linted, 0 chart(s) failed`.

- [ ] **Step 4: Sanity render.**

Run:

```bash
helm template charts/lolday 2>/dev/null | grep -A1 "DOCS_ENABLED" | head -4
```

Expected: rendered ConfigMap / env shows `value: "false"`.

- [ ] **Step 5: Commit.**

```bash
git add charts/lolday/values.yaml
git commit -m "fix(charts): default DOCS_ENABLED to false in prod values [M-docs-prod]

OpenAPI surface should not be enumerable by every authenticated USER.
Local dev still gets True via backend/app/config.py default."
```

---

## Task 4: [H-6a] Constrain dataset name with a Pydantic regex

**Findings:** H-6 (HIGH) part 1 of 2.

**Files:**

- Modify: `backend/app/schemas/dataset.py:10-22`
- Test: `backend/tests/test_datasets.py`

**Rationale:** Today only whitespace is stripped; `\r`, `\n`, `"` all pass and end up in headers and YAML downstream. The regex closes the response-splitting and Content-Disposition-injection vectors at the schema layer.

- [ ] **Step 1: Write the failing test.**

Append to `backend/tests/test_datasets.py`:

```python
@pytest.mark.asyncio
async def test_create_dataset_rejects_crlf_in_name(user_client: AsyncClient):
    r = await user_client.post(
        "/api/v1/datasets",
        json={
            "name": "evil\r\nContent-Type: text/html",
            "csv_content": FIXTURE_CSV,
        },
    )
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_create_dataset_rejects_quote_in_name(user_client: AsyncClient):
    r = await user_client.post(
        "/api/v1/datasets",
        json={
            "name": 'a"b',
            "csv_content": FIXTURE_CSV,
        },
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_create_dataset_accepts_unicode_dash_and_dot(user_client: AsyncClient):
    r = await user_client.post(
        "/api/v1/datasets",
        json={
            "name": "ds.test_v1-2",
            "csv_content": FIXTURE_CSV,
        },
    )
    assert r.status_code == 201, r.text
```

- [ ] **Step 2: Run the new tests; expect failure.**

Run:

```bash
cd backend && uv run pytest tests/test_datasets.py::test_create_dataset_rejects_crlf_in_name tests/test_datasets.py::test_create_dataset_rejects_quote_in_name -v
```

Expected: both fail with `assert 201 == 422` (the API currently accepts the bad names).

- [ ] **Step 3: Tighten the schema.**

Replace `backend/app/schemas/dataset.py` lines 10–22 with:

```python
_DATASET_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9 _.\-]{0,99}$"


class DatasetConfigCreate(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=100, pattern=_DATASET_NAME_PATTERN)]
    description: str | None = None
    visibility: DatasetVisibility = DatasetVisibility.PUBLIC
    csv_content: Annotated[str, Field(min_length=1)]

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name cannot be empty or whitespace-only")
        return v
```

Also update `DatasetConfigUpdate` to share the pattern:

```python
class DatasetConfigUpdate(BaseModel):
    name: Annotated[
        str | None,
        Field(min_length=1, max_length=100, pattern=_DATASET_NAME_PATTERN),
    ] = None
    description: str | None = None
    visibility: DatasetVisibility | None = None
```

- [ ] **Step 4: Run the dataset suite.**

```bash
cd backend && uv run pytest tests/test_datasets.py -v
```

Expected: all green, including the three new tests.

- [ ] **Step 5: Commit.**

```bash
git add backend/app/schemas/dataset.py backend/tests/test_datasets.py
git commit -m "fix(backend): constrain DatasetConfig.name with regex [H-6a]

Rejects CRLF, quote, and other characters that enable Content-Disposition
header injection downstream. Pattern allows A-Z, a-z, 0-9, space, dot,
underscore, hyphen."
```

---

## Task 5: [H-6b] Switch dataset CSV download to RFC 6266 Content-Disposition

**Findings:** H-6 (HIGH) part 2 of 2.

**Files:**

- Modify: `backend/app/routers/datasets.py:154-165`
- Test: `backend/tests/test_datasets.py`

**Rationale:** Belt-and-braces against future bypasses of the schema regex. `experiments_proxy._build_content_disposition` already implements the dual-form helper; reuse it.

- [ ] **Step 1: Write the failing test.**

Append to `backend/tests/test_datasets.py`:

```python
@pytest.mark.asyncio
async def test_csv_download_uses_rfc6266_header(user_client: AsyncClient):
    # Create with a unicode-bearing name; the regex from H-6a allows ASCII
    # only, so the unicode case is purely for the encoding helper.
    create = await user_client.post(
        "/api/v1/datasets",
        json={"name": "ds-test", "csv_content": FIXTURE_CSV},
    )
    ds_id = create.json()["id"]
    r = await user_client.get(f"/api/v1/datasets/{ds_id}/csv")
    assert r.status_code == 200
    cd = r.headers["content-disposition"]
    # Dual-form: ASCII fallback + RFC 5987 percent-encoded UTF-8.
    assert cd.startswith('attachment; filename="')
    assert "filename*=UTF-8''" in cd
```

- [ ] **Step 2: Run the new test; expect failure.**

```bash
cd backend && uv run pytest tests/test_datasets.py::test_csv_download_uses_rfc6266_header -v
```

Expected: AssertionError on `"filename*=UTF-8''" in cd` (current header is single-form).

- [ ] **Step 3: Reuse the helper.**

Move `_build_content_disposition` out of `experiments_proxy.py` into a shared module so both routers can call it. Create `backend/app/services/http_headers.py`:

```python
"""HTTP header helpers shared across routers."""

from urllib.parse import quote


def build_content_disposition(filename: str) -> str:
    """RFC 6266 dual-form ``Content-Disposition``.

    Output: ``attachment; filename="<ascii>"; filename*=UTF-8''<percent-encoded>``.

    Non-ASCII chars in the ASCII fallback become ``?`` and quotes are scrubbed
    to ``_`` to defend against header-injection. The ``filename*`` form is
    used by every modern browser.
    """
    ascii_fallback = (
        filename.encode("ascii", errors="replace").decode("ascii").replace('"', "_")
    )
    quoted = quote(filename, safe="")
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quoted}"
```

Update `backend/app/routers/experiments_proxy.py:24-40`. Replace the local `_build_content_disposition` definition with an import at the top:

```python
from app.services.http_headers import build_content_disposition
```

And replace the call site at `experiments_proxy.py:261`:

```python
        headers={"Content-Disposition": build_content_disposition(filename)},
```

- [ ] **Step 4: Use the helper in datasets.py.**

In `backend/app/routers/datasets.py`, add at the imports block:

```python
from app.services.http_headers import build_content_disposition
```

Replace `get_dataset_csv` body (lines 154–165) with:

```python
@router.get("/{ds_id}/csv")
async def get_dataset_csv(
    ds_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
) -> Response:
    ds = await _get_readable_dataset(ds_id, session, user)
    return Response(
        content=ds.csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": build_content_disposition(f"{ds.name}.csv")},
    )
```

- [ ] **Step 5: Run both test files; verify green and commit.**

```bash
cd backend && uv run pytest tests/test_datasets.py tests/test_experiments_proxy.py -v
```

Expected: all green.

```bash
git add backend/app/services/http_headers.py \
        backend/app/routers/datasets.py \
        backend/app/routers/experiments_proxy.py \
        backend/tests/test_datasets.py
git commit -m "fix(backend): RFC 6266 Content-Disposition for dataset CSV [H-6b]

Extracts the dual-form helper from experiments_proxy into
services/http_headers and reuses it. Belt-and-braces with the H-6a
schema regex."
```

---

## Task 6: [M-PAT-charset] Constrain Git PAT format

**Findings:** M-PAT-charset (MEDIUM).

**Files:**

- Modify: `backend/app/schemas/credential.py:10`
- Test: `backend/tests/test_credentials.py`

**Rationale:** The current bound `min_length=8, max_length=200` accepts any printable chars, including `@`, `/`, `?`, which let a stored PAT redirect `git clone` to an attacker-controlled host. Restrict to GitHub's two official PAT shapes.

- [ ] **Step 1: Locate the existing credential tests.**

Run:

```bash
cd backend && head -40 tests/test_credentials.py
```

Note the existing fixture / route name (`PUT /api/v1/users/me/git-credential` per `routers/credentials.py`).

- [ ] **Step 2: Write the failing tests.**

Append to `backend/tests/test_credentials.py`:

```python
@pytest.mark.asyncio
async def test_put_credential_rejects_url_injection_in_pat(user_client: AsyncClient):
    r = await user_client.put(
        "/api/v1/users/me/git-credential",
        json={"provider": "github", "token": "x@evil.com/exfil?"},
    )
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_put_credential_accepts_classic_pat(user_client: AsyncClient):
    r = await user_client.put(
        "/api/v1/users/me/git-credential",
        json={"provider": "github", "token": "ghp_" + "A" * 36},
    )
    assert r.status_code in (200, 201, 204), r.text


@pytest.mark.asyncio
async def test_put_credential_accepts_finegrained_pat(user_client: AsyncClient):
    r = await user_client.put(
        "/api/v1/users/me/git-credential",
        json={"provider": "github", "token": "github_pat_" + "A" * 82},
    )
    assert r.status_code in (200, 201, 204), r.text
```

- [ ] **Step 3: Run; expect the rejection test to fail.**

```bash
cd backend && uv run pytest tests/test_credentials.py::test_put_credential_rejects_url_injection_in_pat -v
```

Expected: FAIL (current schema accepts the malicious value).

- [ ] **Step 4: Tighten the schema.**

Replace `backend/app/schemas/credential.py:8-11`:

```python
class GitCredentialSet(BaseModel):
    provider: GitProvider = GitProvider.GITHUB
    # GitHub PAT formats per https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens
    #   classic:        ghp_<36 [A-Za-z0-9]>
    #   fine-grained:   github_pat_<82 [A-Za-z0-9_]>
    token: str = Field(pattern=r"^(ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{82})$")
```

Drop the `min_length=8, max_length=200` (the regex now enforces exact length).

- [ ] **Step 5: Run, verify green, commit.**

```bash
cd backend && uv run pytest tests/test_credentials.py -v
```

Expected: all green.

```bash
git add backend/app/schemas/credential.py backend/tests/test_credentials.py
git commit -m "fix(backend): constrain Git PAT shape to GitHub formats [M-PAT-charset]

Closes the URL-injection vector where an unbounded PAT string could
land in the git clone URL as user@host/path."
```

---

## Task 7: [M-event-dict] Type the internal event ingest body

**Findings:** M-event-dict (MEDIUM).

**Files:**

- Modify: `backend/app/schemas/job.py` (add `JobInternalEvent` Pydantic model)
- Modify: `backend/app/routers/internal.py:52-70`
- Test: `backend/tests/test_internal_events.py`

**Rationale:** `event: dict[str, Any]` accepts any blob. A compromised detector (running adversarial code) can ship a multi-MB JSON or smuggle keys the reconciler later projects. Tight schema + size cap closes both.

- [ ] **Step 1: Inspect existing event shapes.**

Run:

```bash
cd backend && grep -rn '"kind":' app/ tests/ | head -20
```

Note the `kind` values that currently exist (e.g. `init_start`, `train_start`, `epoch`, `train_end`, `model_logged`, `error`).

- [ ] **Step 2: Write the failing tests.**

Append to `backend/tests/test_internal_events.py`:

```python
@pytest.mark.asyncio
async def test_internal_event_rejects_unknown_kind(internal_client_factory):
    client = await internal_client_factory()  # existing helper, token-authed
    r = await client.post(
        f"/api/v1/internal/jobs/{client.job_id}/events",
        json={"kind": "totally-made-up-kind", "payload": {}},
    )
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_internal_event_rejects_oversized_payload(internal_client_factory):
    client = await internal_client_factory()
    huge = {"kind": "epoch", "payload": {"blob": "x" * 200_000}}
    r = await client.post(
        f"/api/v1/internal/jobs/{client.job_id}/events",
        json=huge,
    )
    assert r.status_code == 413, r.text


@pytest.mark.asyncio
async def test_internal_event_rejects_extra_keys(internal_client_factory):
    client = await internal_client_factory()
    r = await client.post(
        f"/api/v1/internal/jobs/{client.job_id}/events",
        json={"kind": "epoch", "payload": {}, "rogue_field": "x"},
    )
    assert r.status_code == 422
```

(If `internal_client_factory` doesn't exist as a fixture, look at how `test_internal_events.py` currently authenticates its requests — the existing pattern uses a fixture that mints a job + token via the test session. Reuse whatever shape is there. If unsure, run `head -60 tests/test_internal_events.py` first and mirror.)

- [ ] **Step 3: Run; expect failures.**

```bash
cd backend && uv run pytest tests/test_internal_events.py -v
```

Expected: the three new tests fail (current handler accepts any dict).

- [ ] **Step 4: Define the typed model.**

Append to `backend/app/schemas/job.py`:

```python
from typing import Literal

EVENT_KIND = Literal[
    "init_start",
    "init_end",
    "train_start",
    "train_progress",
    "epoch",
    "train_end",
    "evaluate_start",
    "evaluate_end",
    "predict_start",
    "predict_end",
    "model_logged",
    "metric_logged",
    "error",
    "warning",
    "info",
]


class JobInternalEvent(BaseModel):
    """Typed payload accepted by ``POST /api/v1/internal/jobs/{id}/events``.

    ``extra="forbid"`` rejects unexpected keys; ``payload`` is bounded at
    64 KiB serialized.
    """

    model_config = ConfigDict(extra="forbid")

    kind: EVENT_KIND
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("payload")
    @classmethod
    def _payload_under_64k(cls, v: dict[str, Any]) -> dict[str, Any]:
        import json
        if len(json.dumps(v, default=str).encode("utf-8")) > 64 * 1024:
            raise ValueError("payload exceeds 64 KiB")
        return v
```

(Add `from pydantic import BaseModel, ConfigDict, Field, field_validator` if not already imported. Match the existing import style at the top of the file.)

- [ ] **Step 5: Wire the model into the endpoint.**

Replace `backend/app/routers/internal.py:52-70` with:

```python
@router.post("/jobs/{job_id}/events", status_code=status.HTTP_202_ACCEPTED)
async def ingest_event(
    job_id: uuid.UUID,
    event: JobInternalEvent,
    job: Job = Depends(require_job_token),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Receive a single event from the sidecar; persist + broadcast."""
    if job.id != job_id:
        raise HTTPException(status_code=404, detail="job_id mismatch")
    if job.status not in NON_TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail="job is in a terminal state")
    payload = event.model_dump()
    await persist_event(session, job_id=job.id, event=payload)
    try:
        await event_broker.publish(job.id, payload)
    except Exception:
        BACKEND_ERRORS.labels(stage="event_broker_publish").inc()
        logger.exception("event_broker.publish failed", extra={"job_id": str(job.id)})
    return {"accepted": True}
```

Add the import at the top of `routers/internal.py`:

```python
from app.schemas.job import JobInternalEvent
```

Translate the 64-KiB Pydantic validation failure into HTTP 413 by adding an exception handler at the router level. Append to the same file just above the `@router.post(...)` decorator:

```python
from fastapi.exceptions import RequestValidationError


def _has_payload_too_large(exc: RequestValidationError) -> bool:
    return any("64 KiB" in (e.get("msg") or "") for e in exc.errors())
```

Then change the `ingest_event` decorator path-wrap. Since FastAPI translates `RequestValidationError` to 422 globally, override per-endpoint by manually wrapping the body parse. Simpler approach: add a `@router.exception_handler` is not supported on APIRouter. Instead, accept the body as raw `Request`, parse and convert ourselves. Replace `ingest_event` with:

```python
from fastapi import Request


@router.post("/jobs/{job_id}/events", status_code=status.HTTP_202_ACCEPTED)
async def ingest_event(
    job_id: uuid.UUID,
    request: Request,
    job: Job = Depends(require_job_token),
    session: AsyncSession = Depends(get_async_session),
) -> dict:
    """Receive a single event from the sidecar; persist + broadcast."""
    raw = await request.json()
    try:
        event = JobInternalEvent.model_validate(raw)
    except Exception as e:
        msg = str(e)
        if "64 KiB" in msg:
            raise HTTPException(status_code=413, detail="payload exceeds 64 KiB") from e
        raise HTTPException(status_code=422, detail=msg) from e
    if job.id != job_id:
        raise HTTPException(status_code=404, detail="job_id mismatch")
    if job.status not in NON_TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail="job is in a terminal state")
    payload = event.model_dump()
    await persist_event(session, job_id=job.id, event=payload)
    try:
        await event_broker.publish(job.id, payload)
    except Exception:
        BACKEND_ERRORS.labels(stage="event_broker_publish").inc()
        logger.exception("event_broker.publish failed", extra={"job_id": str(job.id)})
    return {"accepted": True}
```

(Discard the helper `_has_payload_too_large` — the inline `"64 KiB" in msg` check is sufficient.)

- [ ] **Step 6: Run, verify green, commit.**

```bash
cd backend && uv run pytest tests/test_internal_events.py -v
```

Expected: all green.

```bash
git add backend/app/schemas/job.py backend/app/routers/internal.py backend/tests/test_internal_events.py
git commit -m "fix(backend): type internal event body + 64 KiB cap [M-event-dict]

Replaces dict[str, Any] with JobInternalEvent(extra='forbid') and
rejects payloads larger than 64 KiB with HTTP 413."
```

---

## Task 8: [M-ilike] Escape `%`/`_` in dataset and detector search

**Findings:** M-ilike (MEDIUM).

**Files:**

- Modify: `backend/app/routers/datasets.py:122`
- Modify: `backend/app/routers/detectors.py:268-270`
- Test: `backend/tests/test_datasets.py`

**Rationale:** Today `?search=%25` becomes `ilike("%%%%")`, matching everything; `_` lets an attacker probe existence with single-char wildcards. Standard SQL escape closes both.

- [ ] **Step 1: Write the failing test.**

Append to `backend/tests/test_datasets.py`:

```python
@pytest.mark.asyncio
async def test_list_datasets_escapes_percent_wildcard(user_client: AsyncClient):
    # Create two datasets with unrelated names.
    for n in ("alpha-one", "beta-two"):
        r = await user_client.post(
            "/api/v1/datasets",
            json={"name": n, "csv_content": FIXTURE_CSV},
        )
        assert r.status_code == 201
    # `%` should be treated as a literal char, matching neither.
    r = await user_client.get("/api/v1/datasets?search=%25")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0, body
```

- [ ] **Step 2: Run; expect failure.**

```bash
cd backend && uv run pytest tests/test_datasets.py::test_list_datasets_escapes_percent_wildcard -v
```

Expected: `assert body["total"] == 0` fails (currently returns ≥ 2).

- [ ] **Step 3: Add a shared escape helper.**

Append to `backend/app/services/http_headers.py` (renaming the module if desired — but for P1 just add a sibling helper). Actually, since the helper is SQL-shaped, put it in `backend/app/services/search.py` (new file):

```python
"""Shared helpers for search-string handling.

LIKE / ILIKE patterns use ``%`` and ``_`` as wildcards. When a search
string comes from user input we must escape those characters so a user
typing ``%`` matches a literal percent sign, not "everything".
"""


def escape_like_pattern(s: str) -> str:
    """Escape ``\\``, ``%``, and ``_`` so the result is safe inside ``ilike("%<x>%", escape="\\\\")``."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
```

- [ ] **Step 4: Use the helper in both routers.**

In `backend/app/routers/datasets.py`:

```python
from app.services.search import escape_like_pattern
```

Replace line 122:

```python
        filters.append(
            DatasetConfig.name.ilike(f"%{escape_like_pattern(search)}%", escape="\\")
        )
```

In `backend/app/routers/detectors.py`, locate the analogous ILIKE filter (around lines 268–270) and apply the same change. Run `grep -n "ilike" backend/app/routers/detectors.py` to find the exact line. Add the import and wrap the user-supplied `search` value with `escape_like_pattern`, then pass `escape="\\"` to `.ilike`.

- [ ] **Step 5: Run dataset suite, verify green.**

```bash
cd backend && uv run pytest tests/test_datasets.py tests/test_detectors.py -v
```

Expected: all green; the new test now asserts `total == 0`.

- [ ] **Step 6: Commit.**

```bash
git add backend/app/services/search.py \
        backend/app/routers/datasets.py \
        backend/app/routers/detectors.py \
        backend/tests/test_datasets.py
git commit -m "fix(backend): escape % and _ in search ilike patterns [M-ilike]

Both routers/datasets.py and routers/detectors.py now treat the
search query as a literal substring."
```

---

## Task 9: [H-3] Apply `require_detector_access` semantics to flat `/builds/{id}` alias

**Findings:** H-3 (HIGH).

**Files:**

- Modify: `backend/app/routers/builds.py:28-43`
- Test: `backend/tests/test_builds.py`

**Rationale:** The flat alias currently 404s on missing build but does not call `require_detector_access`. The nested route (`/detectors/{id}/builds/{build_id}`) does. The two must be in lockstep, or attackers enumerate build IDs and read across detectors.

- [ ] **Step 1: Inspect the nested route's ACL.**

Run:

```bash
cd backend && grep -n "require_detector_access" app/routers/detectors.py | head -5
```

Confirm the nested build route invokes `Depends(require_detector_access(write=False))`. The semantics are: any authenticated user can read (per the existing `require_detector_access` definition in `deps.py:47-64`), but the parent detector must exist + not be soft-deleted (`load_detector` in `deps.py:37-44`).

- [ ] **Step 2: Write the failing test.**

Append to `backend/tests/test_builds.py`:

```python
@pytest.mark.asyncio
async def test_flat_build_route_404s_if_parent_detector_deleted(
    user_client: AsyncClient, soft_deleted_detector_with_build
):
    """Belt-and-braces: the nested route correctly 404s on soft-deleted
    parent; the flat route must too."""
    build_id = soft_deleted_detector_with_build.build_id
    r = await user_client.get(f"/api/v1/builds/{build_id}")
    assert r.status_code == 404, r.text
```

If the fixture `soft_deleted_detector_with_build` does not exist, write it in `backend/tests/conftest.py`:

```python
import pytest
from datetime import UTC, datetime


@pytest.fixture
async def soft_deleted_detector_with_build(test_session, user):
    from app.models.detector import Detector, DetectorBuild

    detector = Detector(name="soft-deleted", owner_id=user.id)
    test_session.add(detector)
    await test_session.flush()
    build = DetectorBuild(detector_id=detector.id, status="success")
    test_session.add(build)
    await test_session.flush()
    detector.deleted_at = datetime.now(UTC)
    await test_session.commit()

    class _BuildHandle:
        build_id = build.id

    return _BuildHandle()
```

(Use the existing test-session / user fixtures' names; mirror the patterns already in `conftest.py`.)

- [ ] **Step 3: Run; expect failure.**

```bash
cd backend && uv run pytest tests/test_builds.py::test_flat_build_route_404s_if_parent_detector_deleted -v
```

Expected: FAIL — current code looks up `Detector` via `session.get` but does not check `deleted_at`, so it returns 200.

- [ ] **Step 4: Apply `load_detector` semantics.**

Replace `backend/app/routers/builds.py` `get_build_flat` function (lines 28–43):

```python
from app.deps import load_detector  # add to imports if not present


@router.get("/{build_id}", response_model=BuildRead)
async def get_build_flat(
    build_id: UUID,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_active_user),
) -> BuildRead:
    build = await session.get(DetectorBuild, build_id)
    if build is None:
        raise HTTPException(status_code=404, detail="build not found")
    # Re-use load_detector for the soft-delete check (same semantics as the
    # nested route). load_detector raises HTTPException(404) on missing /
    # soft-deleted; let it propagate.
    await load_detector(detector_id=build.detector_id, session=session)
    return BuildRead.model_validate(build)
```

- [ ] **Step 5: Run, verify green, commit.**

```bash
cd backend && uv run pytest tests/test_builds.py -v
```

Expected: all green including the new test.

```bash
git add backend/app/routers/builds.py backend/tests/test_builds.py backend/tests/conftest.py
git commit -m "fix(backend): align flat /builds/{id} ACL with nested route [H-3]

Both routes now run load_detector against the parent, so soft-deleted
parents 404 consistently and future per-detector read-ACL changes
land on both paths at once."
```

---

## Task 10: [H-4] `clone_dataset` inherits source visibility

**Findings:** H-4 (HIGH).

**Files:**

- Modify: `backend/app/routers/datasets.py:222-228`
- Test: `backend/tests/test_datasets.py`

**Rationale:** Cloning a PRIVATE dataset currently produces a PUBLIC clone with the same CSV content. A reader sees the data legitimately, then can re-publish it cluster-wide by cloning.

- [ ] **Step 1: Write the failing tests.**

Append to `backend/tests/test_datasets.py`:

```python
@pytest.mark.asyncio
async def test_clone_of_private_dataset_stays_private(
    user_client: AsyncClient, admin_client: AsyncClient
):
    # admin creates a PRIVATE dataset
    create = await admin_client.post(
        "/api/v1/datasets",
        json={
            "name": "secret",
            "visibility": "private",
            "csv_content": FIXTURE_CSV,
        },
    )
    assert create.status_code == 201, create.text
    ds_id = create.json()["id"]
    # user clones it (admin's PRIVATE is visible to admin only; this case
    # exercises the *admin* user cloning their own PRIVATE dataset).
    r = await admin_client.post(f"/api/v1/datasets/{ds_id}/clone")
    assert r.status_code == 201, r.text
    assert r.json()["visibility"] == "private"


@pytest.mark.asyncio
async def test_clone_of_public_dataset_stays_public(user_client: AsyncClient):
    create = await user_client.post(
        "/api/v1/datasets",
        json={"name": "shared", "visibility": "public", "csv_content": FIXTURE_CSV},
    )
    ds_id = create.json()["id"]
    r = await user_client.post(f"/api/v1/datasets/{ds_id}/clone")
    assert r.status_code == 201
    assert r.json()["visibility"] == "public"
```

- [ ] **Step 2: Run; expect failure.**

```bash
cd backend && uv run pytest tests/test_datasets.py::test_clone_of_private_dataset_stays_private -v
```

Expected: FAIL (clone returns `"public"`).

- [ ] **Step 3: Inherit visibility.**

Replace `backend/app/routers/datasets.py:222-228` (the `DatasetConfig(...)` instantiation inside `clone_dataset`):

```python
    copy = DatasetConfig(
        name=new_name,
        description=orig.description,
        owner_id=user.id,
        visibility=orig.visibility,
        csv_content=orig.csv_content,
        csv_checksum=orig.csv_checksum,
        sample_count=orig.sample_count,
        label_distribution=orig.label_distribution,
        family_distribution=orig.family_distribution,
        size_bytes=orig.size_bytes,
    )
```

- [ ] **Step 4: Run, verify green, commit.**

```bash
cd backend && uv run pytest tests/test_datasets.py -v
```

Expected: all green.

```bash
git add backend/app/routers/datasets.py backend/tests/test_datasets.py
git commit -m "fix(backend): clone_dataset inherits source visibility [H-4]

Closes the leak path where reading a PRIVATE dataset then cloning it
would re-publish the CSV as PUBLIC. The cloner can still PATCH the
visibility upward (PRIVATE -> PUBLIC) if intentional."
```

---

## Task 11: [H-1 + H-2] MLflow proxy per-user ACL + path traversal block

**Findings:** H-1 (HIGH), H-2 (HIGH). Combined because both touch
`routers/experiments_proxy.py` and share helper code.

**Files:**

- Modify: `backend/app/routers/experiments_proxy.py` (multiple functions)
- Test: `backend/tests/test_experiments_proxy.py`

**Rationale:** All five proxy handlers (`list_experiments`, `list_runs`, `get_run`, `list_artifacts`, `download_artifact`) authenticate but never filter by owner. `download_artifact` additionally interpolates the `path` query parameter without `..` block. Every authenticated USER can read every other user's runs and artifacts; combined with the path-traversal, also cross-run artifact bytes.

The runs we own carry `tags["lolday.user_id"]` set at job submission (`routers/jobs.py:359-361`). The ACL strategy: admin sees all; non-admin sees runs whose `lolday.user_id` matches `user.id` OR whose parent experiment's tag `lolday.owner_id == user.id`. For experiments themselves we filter by the same tag.

- [ ] **Step 1: Inspect current MLflow tag conventions in jobs.py.**

Run:

```bash
cd backend && grep -n "lolday.user_id\|lolday.owner_id\|set_experiment_tag\|set_tag" app/routers/jobs.py app/services/mlflow_client.py | head -20
```

Note which tag is set on **runs** (`lolday.user_id`) vs **experiments** (`lolday.owner_id` or similar). If experiments don't currently get an owner tag, the ACL pattern uses run-tags only and an experiment is "visible" iff it has at least one run owned by the caller.

For this plan, assume:

- Run-level tag: `lolday.user_id` (already set on every job-spawned run).
- Experiment-level: derived from runs.

- [ ] **Step 2: Write the failing tests.**

Append to `backend/tests/test_experiments_proxy.py`:

```python
@pytest.mark.asyncio
async def test_list_runs_filters_by_owner(
    user_client: AsyncClient, second_user_client: AsyncClient, mlflow_stub
):
    """user A submits a run; user B should not see it via the proxy."""
    # Use the existing mlflow_stub fixture to inject two runs with different
    # lolday.user_id tags; if the fixture name differs, look at the existing
    # tests in this file to find the pattern. Pseudocode:
    mlflow_stub.add_run(experiment_id="1", run_id="r-a", tags={"lolday.user_id": user_client.user_id_str})
    mlflow_stub.add_run(experiment_id="1", run_id="r-b", tags={"lolday.user_id": second_user_client.user_id_str})
    r = await user_client.get("/api/v1/experiments/1/runs")
    body = r.json()
    run_ids = {x["run_id"] for x in body}
    assert "r-a" in run_ids
    assert "r-b" not in run_ids


@pytest.mark.asyncio
async def test_get_run_404s_for_non_owner(
    second_user_client: AsyncClient, mlflow_stub, user
):
    mlflow_stub.add_run(experiment_id="1", run_id="r-a", tags={"lolday.user_id": str(user.id)})
    r = await second_user_client.get("/api/v1/runs/r-a")
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_admin_sees_all_runs(admin_client: AsyncClient, mlflow_stub):
    mlflow_stub.add_run(experiment_id="1", run_id="r-a", tags={"lolday.user_id": "some-other-uuid"})
    r = await admin_client.get("/api/v1/runs/r-a")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_download_artifact_rejects_dotdot(user_client: AsyncClient, mlflow_stub):
    mlflow_stub.add_run(
        experiment_id="1",
        run_id="r-a",
        tags={"lolday.user_id": user_client.user_id_str},
        artifact_uri="mlflow-artifacts:/1/r-a/artifacts",
    )
    r = await user_client.get("/api/v1/runs/r-a/artifacts/download?path=../../other-run/model.bin")
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
async def test_download_artifact_rejects_absolute_path(user_client: AsyncClient, mlflow_stub):
    mlflow_stub.add_run(
        experiment_id="1",
        run_id="r-a",
        tags={"lolday.user_id": user_client.user_id_str},
        artifact_uri="mlflow-artifacts:/1/r-a/artifacts",
    )
    r = await user_client.get("/api/v1/runs/r-a/artifacts/download?path=/etc/passwd")
    assert r.status_code == 400
```

(The `mlflow_stub` fixture exists in `backend/tests/conftest.py` per the autouse-mocked MLflow rule in `.claude/rules/backend.md`. Inspect it for the exact tag-injection method; adapt the `mlflow_stub.add_run(...)` calls above. If the existing fixture name differs, search `grep -n "mlflow" tests/conftest.py` and reuse.)

- [ ] **Step 3: Run; expect failures.**

```bash
cd backend && uv run pytest tests/test_experiments_proxy.py -v
```

Expected: all five new tests fail.

- [ ] **Step 4: Add a shared ACL helper at the top of experiments_proxy.py.**

Insert after the imports (around line 22):

```python
from app.models import Role


def _user_can_see_run(user: User, run_tags: dict[str, str]) -> bool:
    """Owner-or-admin check against the run's `lolday.user_id` tag.

    Returns True iff the caller is admin OR the tag matches the caller's
    UUID. Runs without the tag are treated as platform-internal (admin-only).
    """
    if user.role == Role.ADMIN:
        return True
    owner_id = run_tags.get("lolday.user_id")
    return owner_id is not None and owner_id == str(user.id)


def _user_can_see_run_dict(user: User, raw_run: dict) -> bool:
    """Same as `_user_can_see_run` but works on the raw MLflow REST run shape."""
    data = raw_run.get("data") or {}
    tags_list = data.get("tags") or []
    tags = {t["key"]: t["value"] for t in tags_list if "key" in t}
    return _user_can_see_run(user, tags)


def _validate_artifact_path(path: str) -> str:
    """Reject path-traversal and absolute paths in the user-supplied artifact path.

    Returns the path unchanged on success; raises HTTPException(400) otherwise.
    """
    if not path:
        raise HTTPException(status_code=400, detail="path is required")
    if path.startswith("/") or path.startswith("\\"):
        raise HTTPException(status_code=400, detail="absolute path forbidden")
    parts = PurePosixPath(path).parts
    if any(p in (".", "..") for p in parts):
        raise HTTPException(status_code=400, detail="path traversal forbidden")
    return path
```

- [ ] **Step 5: Apply the ACL to each endpoint.**

`list_runs` (around line 178):

```python
@router.get("/experiments/{experiment_id}/runs")
async def list_runs(
    experiment_id: str,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
    max_results: int = Query(100, ge=1, le=1000),
):
    try:
        raw = await _client().search_runs(
            experiment_ids=[experiment_id], max_results=max_results
        )
    except MlflowError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    visible_raw = [r for r in raw if _user_can_see_run_dict(user, r)]
    run_ids: list[str] = []
    for r in visible_raw:
        info = r.get("info") or {}
        rid = info.get("run_id") or info.get("run_uuid")
        if isinstance(rid, str) and rid:
            run_ids.append(rid)
    lolday_meta = await _fetch_lolday_job_meta(run_ids, session)
    return [_flatten_run(r, lolday_job_meta=lolday_meta) for r in visible_raw]
```

`get_run` (around line 202):

```python
@router.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    user: Annotated[User, Depends(current_active_user)],
):
    try:
        raw = await _client().get_run(run_id)
    except MlflowError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    if not _user_can_see_run_dict(user, raw):
        raise HTTPException(status_code=404, detail="run not found")
    lolday_meta = await _fetch_lolday_job_meta([run_id], session)
    return _flatten_run(raw, lolday_job_meta=lolday_meta)
```

`list_artifacts` (around line 216):

```python
@router.get("/runs/{run_id}/artifacts")
async def list_artifacts(
    run_id: str,
    user: Annotated[User, Depends(current_active_user)],
    path: str | None = None,
):
    # Authorise first via get_run (saves a round-trip on bad path).
    try:
        run = await _client().get_run(run_id)
    except MlflowError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    if not _user_can_see_run_dict(user, run):
        raise HTTPException(status_code=404, detail="run not found")
    if path is not None:
        _validate_artifact_path(path)
    url = f"{settings.MLFLOW_TRACKING_URI}/api/2.0/mlflow/artifacts/list"
    params = {"run_id": run_id}
    if path:
        params["path"] = path
    async with httpx.AsyncClient(timeout=settings.MLFLOW_HTTP_TIMEOUT_SECONDS) as c:
        r = await c.get(url, params=params)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=r.text)
    return r.json()
```

`download_artifact` (around line 233):

```python
@router.get("/runs/{run_id}/artifacts/download")
async def download_artifact(
    run_id: str,
    path: str,
    user: Annotated[User, Depends(current_active_user)],
) -> Response:
    run = await _client().get_run(run_id)
    if not _user_can_see_run_dict(user, run):
        raise HTTPException(status_code=404, detail="run not found")
    _validate_artifact_path(path)
    artifact_uri: str = run["info"]["artifact_uri"]
    prefix = "mlflow-artifacts:/"
    if not artifact_uri.startswith(prefix):
        raise HTTPException(
            status_code=502,
            detail=f"unexpected artifact_uri scheme: {artifact_uri!r}",
        )
    relative = artifact_uri[len(prefix) :].rstrip("/")
    # Percent-encode each path segment defensively.
    safe_path = "/".join(quote(p, safe="") for p in PurePosixPath(path).parts)
    url = f"{settings.MLFLOW_TRACKING_URI}/api/2.0/mlflow-artifacts/artifacts/{relative}/{safe_path}"
    async with httpx.AsyncClient(timeout=settings.MLFLOW_HTTP_TIMEOUT_SECONDS) as c:
        r = await c.get(url)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=r.text)
    filename = PurePosixPath(path).name or "artifact"
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return Response(
        content=r.content,
        media_type=media_type,
        headers={"Content-Disposition": build_content_disposition(filename)},
    )
```

For `list_experiments` (around line 127) — filter the list by "has at least one visible run" lazily, otherwise non-admin users see experiment names they cannot drill into. Replace:

```python
@router.get("/experiments")
async def list_experiments(
    user: Annotated[User, Depends(current_active_user)],
    max_results: int = Query(100, ge=1, le=1000),
    include: str | None = Query(None, pattern="^stats$"),
):
    try:
        experiments = await _client().search_experiments(max_results=max_results)
    except MlflowError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    if user.role != Role.ADMIN:
        # Per-experiment owner filter: keep only experiments that have at
        # least one run tagged with the caller's user_id. One search_runs
        # per experiment is acceptable at lab-scale (< 50 experiments).
        kept = []
        for exp in experiments:
            try:
                runs = await _client().search_runs(
                    experiment_ids=[exp["experiment_id"]],
                    max_results=1,
                    filter_string=f"tags.\"lolday.user_id\" = '{user.id!s}'",
                )
            except MlflowError:
                runs = []
            if runs:
                kept.append(exp)
        experiments = kept

    if include != "stats":
        return experiments

    enriched = []
    for exp in experiments:
        try:
            stats = await _experiment_stats(exp["experiment_id"])
        except MlflowError as e:
            logger.warning(
                "experiment_stats failed for %s: %s", exp["experiment_id"], e
            )
            stats = {"run_count": None, "best_f1": None, "latest_start_time": None}
        enriched.append({**exp, **stats})
    return enriched
```

- [ ] **Step 6: Run, verify green, commit.**

```bash
cd backend && uv run pytest tests/test_experiments_proxy.py -v
```

Expected: all green.

```bash
git add backend/app/routers/experiments_proxy.py backend/tests/test_experiments_proxy.py
git commit -m "fix(backend): MLflow proxy per-user ACL + path traversal block [H-1,H-2]

All five proxy handlers now filter by the run's lolday.user_id tag
(admin sees all). download_artifact validates the path against '..',
absolute paths, and dot segments; segments are percent-encoded before
forwarding."
```

---

## Task 12: [H-5] Reject user-supplied reserved keys in `params`

**Findings:** H-5 (HIGH).

**Files:**

- Modify: `backend/app/services/job_config.py:107-143`
- Modify: `backend/app/services/jobs_params_validate.py` (or wherever `validate_user_params` lives)
- Test: `backend/tests/services/` (or `backend/tests/test_jobs_params.py`)

**Rationale:** `_deep_merge(base, nested)` runs after `validate_user_params`. The schema only constrains stage-specific keys; reserved top-level keys (`mlflow`, `paths`, `data`, `defaults`, `lolday`, `stage`) reach `_deep_merge` and overwrite platform-injected values — most dangerously `mlflow.tracking_uri`.

- [ ] **Step 1: Find the validation entry point.**

Run:

```bash
cd backend && grep -rn "validate_user_params\|user_params" app/services/jobs_params_validate.py app/services/job_config.py | head -20
```

Identify the function that runs first against user-supplied params. If `validate_user_params` is the entry, hook the reserved-key check there. If `render_config_yaml` is the only entry, hook it inside `JobConfigRenderer.render_config_yaml` before `_unflatten`.

- [ ] **Step 2: Write the failing test.**

Create `backend/tests/services/test_job_config_reserved_keys.py`:

```python
import pytest

from app.services.job_config import JobConfigRenderer


@pytest.fixture
def renderer():
    return JobConfigRenderer(
        samples_root="/mnt/samples",
        config_mount="/mnt/config",
        output_mount="/mnt/output",
        source_model_mount="/mnt/source",
    )


@pytest.mark.parametrize(
    "reserved_key",
    ["mlflow", "paths", "data", "defaults", "lolday", "stage"],
)
def test_render_rejects_reserved_top_level_key(renderer, reserved_key):
    with pytest.raises(ValueError, match="reserved"):
        renderer.render_config_yaml(
            stage="train",
            user_params={reserved_key: {"x": 1}},
            mlflow_tracking_uri="http://internal-mlflow:5000",
            mlflow_run_id=None,
            mlflow_experiment_id=None,
        )


@pytest.mark.parametrize(
    "dotted_reserved",
    ["mlflow.tracking_uri", "paths.samples_root", "lolday.user_id"],
)
def test_render_rejects_reserved_dotted_key(renderer, dotted_reserved):
    with pytest.raises(ValueError, match="reserved"):
        renderer.render_config_yaml(
            stage="train",
            user_params={dotted_reserved: "evil"},
            mlflow_tracking_uri="http://internal-mlflow:5000",
            mlflow_run_id=None,
            mlflow_experiment_id=None,
        )


def test_render_accepts_unreserved_keys(renderer):
    out = renderer.render_config_yaml(
        stage="train",
        user_params={"model.n_estimators": 500, "training.batch_size": 32},
        mlflow_tracking_uri="http://internal-mlflow:5000",
        mlflow_run_id=None,
        mlflow_experiment_id=None,
    )
    assert "n_estimators: 500" in out
    assert "tracking_uri: http://internal-mlflow:5000" in out
```

- [ ] **Step 3: Run; expect failures.**

```bash
cd backend && uv run pytest tests/services/test_job_config_reserved_keys.py -v
```

Expected: all parametrized cases fail (current code merges silently).

- [ ] **Step 4: Add the reserved-key guard at the top of `render_config_yaml`.**

In `backend/app/services/job_config.py`, add a module-level constant:

```python
RESERVED_TOP_LEVEL_KEYS = frozenset({"mlflow", "paths", "data", "defaults", "lolday", "stage"})
```

Then at the start of `JobConfigRenderer.render_config_yaml` (after the signature, before building `base`):

```python
        # H-5: reject user-supplied keys that would collide with the
        # platform-injected namespace. Includes both the flat form
        # ("mlflow") and the dotted form ("mlflow.tracking_uri").
        for raw_key in user_params:
            top = raw_key.split(".", 1)[0]
            if top in RESERVED_TOP_LEVEL_KEYS:
                raise ValueError(
                    f"user_params key {raw_key!r} collides with platform-reserved "
                    f"top-level namespace {sorted(RESERVED_TOP_LEVEL_KEYS)!r}"
                )
```

- [ ] **Step 5: Verify the existing `routers/jobs.py::create_job` translates the `ValueError` to HTTP 400.**

Run:

```bash
cd backend && grep -B1 -A4 "render_config_yaml" app/routers/jobs.py | head -30
```

Confirm `ValueError` is caught and converted to `HTTPException(status_code=400, ...)`. If not, add the try/except in the caller (do not let `ValueError` bubble as 500).

- [ ] **Step 6: Add an integration-level test.**

Append to `backend/tests/test_jobs.py` (or wherever job-create tests live — `grep -l "POST.*jobs" backend/tests/` finds the file):

```python
@pytest.mark.asyncio
async def test_create_job_rejects_reserved_param_key(
    user_client: AsyncClient, valid_create_payload
):
    payload = {**valid_create_payload, "params": {"mlflow": {"tracking_uri": "http://evil"}}}
    r = await user_client.post("/api/v1/jobs", json=payload)
    assert r.status_code == 400, r.text
    assert "reserved" in r.json()["detail"].lower()
```

(Adapt to whatever fixture is used by surrounding tests.)

- [ ] **Step 7: Run, verify green, commit.**

```bash
cd backend && uv run pytest tests/services/test_job_config_reserved_keys.py tests/test_jobs.py -v
```

Expected: all green.

```bash
git add backend/app/services/job_config.py \
        backend/tests/services/test_job_config_reserved_keys.py \
        backend/tests/test_jobs.py
git commit -m "fix(backend): reject reserved top-level keys in user_params [H-5]

mlflow/paths/data/defaults/lolday/stage are platform-injected and
must not be overwriteable by user-supplied params. Rejecting both
flat and dotted forms closes the SSRF + exfil vector via
mlflow.tracking_uri override."
```

---

## Task 13: [H-20] Job token cleanup on terminal status

**Findings:** H-20 (HIGH).

**Files:**

- Modify: `backend/app/routers/jobs.py:644-654` (cancel_job)
- Modify: `backend/app/reconciler/jobs.py` (`_finalize_*` paths)
- Modify: `backend/app/deps.py:67-84` (`require_job_token`)
- Test: `backend/tests/test_internal_events.py` or new

**Rationale:** A stolen job token currently authenticates `GET /api/v1/internal/jobs/{id}/config` (returns CSVs) forever — `token_hash` is set on submit and never cleared. The cancel/finalize paths must null the hash, and the dep must reject terminal jobs even if the hash hasn't been nulled yet (defense-in-depth).

- [ ] **Step 1: Find the finalize paths.**

Run:

```bash
cd backend && grep -n "_finalize_\|job.status = .*JobStatus.SUCCEEDED\|job.status = .*JobStatus.FAILED" app/reconciler/jobs.py app/reconciler/loop.py | head -20
```

Note the function(s) that set terminal status. They must `job.token_hash = None` as part of the same transaction.

- [ ] **Step 2: Write the failing tests.**

Append to `backend/tests/test_internal_events.py`:

```python
@pytest.mark.asyncio
async def test_internal_config_rejects_token_after_cancel(internal_client_factory):
    client = await internal_client_factory()
    # Token works while running.
    r1 = await client.get(f"/api/v1/internal/jobs/{client.job_id}/config")
    assert r1.status_code == 200

    # User cancels.
    cancel = await client.cancel_via_user_session()
    assert cancel.status_code == 200

    # Same token must fail.
    r2 = await client.get(f"/api/v1/internal/jobs/{client.job_id}/config")
    assert r2.status_code in (401, 403, 404), r2.text


@pytest.mark.asyncio
async def test_internal_config_rejects_token_after_finalize(internal_client_factory):
    client = await internal_client_factory()
    # Simulate reconciler-side terminal transition without nulling the hash,
    # to verify the dep itself rejects terminal jobs (defense-in-depth).
    await client.set_terminal_in_db_only()
    r = await client.get(f"/api/v1/internal/jobs/{client.job_id}/config")
    assert r.status_code in (401, 403, 404), r.text
```

(Reuse `internal_client_factory`. If `cancel_via_user_session` doesn't exist on the factory, mirror an existing pattern — fetch a user-session client, call `POST /api/v1/jobs/{id}/cancel`.)

- [ ] **Step 3: Run; expect failures.**

```bash
cd backend && uv run pytest tests/test_internal_events.py::test_internal_config_rejects_token_after_cancel tests/test_internal_events.py::test_internal_config_rejects_token_after_finalize -v
```

Expected: FAIL — the second request still succeeds.

- [ ] **Step 4: Clear `token_hash` in `cancel_job`.**

In `backend/app/routers/jobs.py`, modify `cancel_job` (lines 615–654). After the existing `job.status = JobStatus.CANCELLED` and before `await session.commit()`:

```python
    job.status = JobStatus.CANCELLED
    job.failure_reason = (
        "cancelled_by_user" if job.owner_id == user.id else "cancelled_by_admin"
    )
    job.finished_at = datetime.now(UTC)
    job.token_hash = None  # H-20: invalidate the init-container token on cancel
    await session.commit()
```

- [ ] **Step 5: Clear `token_hash` in reconciler finalize paths.**

In `backend/app/reconciler/jobs.py`, wherever `job.status` transitions to a terminal value (`SUCCEEDED`, `FAILED`, `CANCELLED`, `ABORTED`, or whichever enum the codebase uses), append:

```python
        job.token_hash = None  # H-20: invalidate token on terminal
```

Adjacent to each `job.status = JobStatus.<TERMINAL>` line, inside the same DB session before commit. Use `grep` to find them all:

```bash
grep -n "job\.status = JobStatus\." backend/app/reconciler/jobs.py
```

Apply the same line to each terminal transition. Do **not** clear it on transitions to non-terminal status.

- [ ] **Step 6: Reject terminal jobs in the dep.**

Modify `backend/app/deps.py:67-84`:

```python
from app.models.job import NON_TERMINAL_STATUSES


async def require_job_token(
    job_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_async_session)],
    authorization: Annotated[str | None, Header()] = None,
) -> Job:
    """Authenticate as a given job's init container via one-time token.

    Expected header: `Authorization: Bearer <token>`. Terminal jobs are
    rejected outright (H-20) even if a stale token_hash row exists.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization[7:]
    job = await session.get(Job, job_id)
    if job is None or job.token_hash is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.status not in NON_TERMINAL_STATUSES:
        raise HTTPException(status_code=404, detail="job not found")
    if not verify_token(token, job.token_hash):
        raise HTTPException(status_code=403, detail="invalid token")
    return job
```

- [ ] **Step 7: Run, verify green, commit.**

```bash
cd backend && uv run pytest tests/test_internal_events.py tests/test_jobs.py tests/reconciler/ -v
```

Expected: all green.

```bash
git add backend/app/routers/jobs.py \
        backend/app/reconciler/jobs.py \
        backend/app/deps.py \
        backend/tests/test_internal_events.py
git commit -m "fix(backend): invalidate job token on cancel/terminal [H-20]

cancel_job and reconciler finalize paths now null job.token_hash.
require_job_token additionally rejects any job whose status is no
longer in NON_TERMINAL_STATUSES, so a stolen token cannot reach
/internal/* even before the reconciler runs."
```

---

## Task 14: [H-24] FastAPI body-size middleware

**Findings:** H-24 (HIGH).

**Files:**

- Create: `backend/app/middleware/__init__.py`
- Create: `backend/app/middleware/body_size.py`
- Modify: `backend/app/main.py` (import + register middleware)
- Modify: `backend/app/config.py` (add `BODY_SIZE_MAX_BYTES`)
- Test: `backend/tests/test_body_size_middleware.py` (new)

**Rationale:** Starlette/FastAPI default has no global body cap. A multi-GB POST or a JSON-depth bomb to `/api/v1/jobs` parses fully into Python objects, exhausting the 512 MiB pod memory limit and triggering OOMKill.

- [ ] **Step 1: Add the setting.**

Append to `backend/app/config.py` `Settings` class (preserve alphabetical or grouped order if there is one):

```python
    BODY_SIZE_MAX_BYTES: int = 12 * 1024 * 1024  # 12 MiB; headroom over 10 MiB CSV cap
```

- [ ] **Step 2: Write the failing test.**

Create `backend/tests/test_body_size_middleware.py`:

```python
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_oversized_body_rejected_with_413(user_client: AsyncClient, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "BODY_SIZE_MAX_BYTES", 1024)
    payload = "x" * 4096  # > 1 KiB
    r = await user_client.post(
        "/api/v1/datasets",
        headers={"Content-Length": str(len(payload) + 100)},
        content=payload.encode(),  # send as raw bytes
    )
    assert r.status_code == 413, r.text


@pytest.mark.asyncio
async def test_undersized_body_passes_middleware(user_client: AsyncClient):
    from app.config import settings  # noqa: F401 just to assert reachable

    # A normal small JSON body should not hit the middleware.
    r = await user_client.get("/api/v1/health")
    assert r.status_code == 200
```

- [ ] **Step 3: Run; expect failure on the 413 case.**

```bash
cd backend && uv run pytest tests/test_body_size_middleware.py -v
```

Expected: `test_oversized_body_rejected_with_413` fails (currently lets through).

- [ ] **Step 4: Write the middleware.**

Create `backend/app/middleware/__init__.py` (empty file).

Create `backend/app/middleware/body_size.py`:

```python
"""Reject request bodies that exceed ``settings.BODY_SIZE_MAX_BYTES``
before any handler reads them.

Two-layer protection:
1. If ``Content-Length`` is present and over the cap, 413 immediately.
2. Wrap ``request.receive`` so chunked bodies that exceed the cap mid-
   stream also error out, never reaching the handler.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import settings


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        cap = settings.BODY_SIZE_MAX_BYTES
        # Layer 1: Content-Length header check.
        cl = request.headers.get("content-length")
        if cl is not None:
            try:
                if int(cl) > cap:
                    return Response(
                        content="payload too large",
                        status_code=413,
                        media_type="text/plain",
                    )
            except ValueError:
                # malformed CL — let Starlette deal with it later
                pass

        # Layer 2: wrap receive to count bytes as they arrive (defends
        # against missing / lying Content-Length).
        received_bytes = 0
        original_receive = request.receive

        async def counting_receive():
            nonlocal received_bytes
            message = await original_receive()
            if message["type"] == "http.request":
                body = message.get("body", b"") or b""
                received_bytes += len(body)
                if received_bytes > cap:
                    # Signal an empty body and let the handler 4xx; we cannot
                    # synchronously return 413 from inside receive without
                    # breaking ASGI. Instead, set a flag and check after.
                    raise RuntimeError("body too large")
            return message

        request._receive = counting_receive  # type: ignore[attr-defined]
        try:
            return await call_next(request)
        except RuntimeError as e:
            if str(e) == "body too large":
                return Response(
                    content="payload too large",
                    status_code=413,
                    media_type="text/plain",
                )
            raise
```

- [ ] **Step 5: Register the middleware in `main.py`.**

In `backend/app/main.py`, after the `app = FastAPI(...)` block and before `Instrumentator().instrument(app)`:

```python
from app.middleware.body_size import BodySizeLimitMiddleware

app.add_middleware(BodySizeLimitMiddleware)
```

- [ ] **Step 6: Run, verify green, commit.**

```bash
cd backend && uv run pytest tests/test_body_size_middleware.py -v
```

Expected: both tests pass.

Also run the rest of the suite to make sure no existing tests broke:

```bash
cd backend && uv run pytest -q
```

Expected: all green.

```bash
git add backend/app/middleware/__init__.py \
        backend/app/middleware/body_size.py \
        backend/app/main.py \
        backend/app/config.py \
        backend/tests/test_body_size_middleware.py
git commit -m "fix(backend): reject oversize request bodies with 413 [H-24]

Cap defaults to 12 MiB (headroom over the 10 MiB dataset CSV cap);
override via BODY_SIZE_MAX_BYTES. Two-layer: Content-Length check
plus a receive wrapper to catch missing/lying CL."
```

---

## Task 15: [M-WS-backdoor] Gate WebSocket test override by environment

**Findings:** M-WS-backdoor (MEDIUM).

**Files:**

- Modify: `backend/app/routers/jobs.py:790-803`
- Test: `backend/tests/test_jobs_events_websocket.py`

**Rationale:** The WebSocket auth path honours `X-Test-User-Email` whenever `cf_access_user` is in `app.dependency_overrides`. If overrides ever leak into production (test code path inclusion, fixture bug, plugin), this becomes a header-based auth bypass. Belt-and-braces gate on `settings.ENVIRONMENT`.

- [ ] **Step 1: Inspect existing env settings.**

Run:

```bash
cd backend && grep -n "ENVIRONMENT" app/config.py
```

Confirm `settings.ENVIRONMENT` is defined (it is, per the SSO validate_sso_config path).

- [ ] **Step 2: Write the failing test.**

Append to `backend/tests/test_jobs_events_websocket.py`:

```python
@pytest.mark.asyncio
async def test_ws_test_header_rejected_in_production(test_client, user, monkeypatch):
    """Even with the cf_access_user override in dependency_overrides, the
    X-Test-User-Email header must NOT authenticate when ENVIRONMENT is
    production."""
    from app.config import settings

    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    # The conftest already installed the override; otherwise this test is
    # meaningless. Verify:
    from app.auth.cf_access import cf_access_user
    from app.main import app

    assert cf_access_user in app.dependency_overrides

    job_id = (await create_running_job_in_db(user)).id  # existing helper
    with test_client.websocket_connect(
        f"/api/v1/jobs/{job_id}/events",
        headers={"X-Test-User-Email": user.email},
    ) as ws:
        # Should close with 4401 immediately.
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
        assert exc.value.code == 4401
```

If `test_client` is sync `TestClient` rather than async, adapt to whatever the file uses. The key assertion is "with `ENVIRONMENT=production`, the test-header path does not authenticate."

- [ ] **Step 3: Run; expect failure.**

```bash
cd backend && uv run pytest tests/test_jobs_events_websocket.py::test_ws_test_header_rejected_in_production -v
```

Expected: FAIL (test header still authenticates).

- [ ] **Step 4: Add the environment gate.**

Modify `backend/app/routers/jobs.py:796`:

```python
    session, holder = await _ws_session()
    try:
        if (
            settings.ENVIRONMENT != "production"
            and _cf_access_user_dep in _app.dependency_overrides
        ):
            email = websocket.headers.get("x-test-user-email")
            if not email:
                return None
            row = (
                await session.execute(select(User).where(User.email == email))
            ).scalar_one_or_none()
            return row
```

Add `from app.config import settings` to the imports of `routers/jobs.py` if not already imported.

- [ ] **Step 5: Run, verify green, commit.**

```bash
cd backend && uv run pytest tests/test_jobs_events_websocket.py -v
```

Expected: all green (including the new test under monkeypatched production env).

```bash
git add backend/app/routers/jobs.py backend/tests/test_jobs_events_websocket.py
git commit -m "fix(backend): gate WS X-Test-User-Email by ENVIRONMENT [M-WS-backdoor]

Belt-and-braces against any future code path that leaks the
cf_access_user override into production. The header-based test
auth now requires both the override map AND a non-production
environment."
```

---

## Task 16: [H-25] NetworkPolicy locking `/metrics` to monitoring ns

**Findings:** H-25 (HIGH).

**Files:**

- Modify: `charts/lolday/templates/network-policy.yaml`

**Rationale:** `prometheus-fastapi-instrumentator` exposes `/metrics` on the same port as the public API. Anyone in-cluster can scrape it (Captain Hook surge debug, lateral recon). Restrict to the `monitoring` ns where Prometheus lives.

- [ ] **Step 1: Inspect the current file.**

(Already read at Task 0 — content is the `deny-training-egress` policy with orphan selector.)

- [ ] **Step 2: Append the metrics-ingress policy.**

Modify `charts/lolday/templates/network-policy.yaml` to extend with a second policy. Replace the entire file with:

```yaml
{{- if .Values.training.networkPolicy.enabled }}
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: deny-training-egress
  namespace: {{ .Values.global.namespace }}
  labels:
    {{- include "lolday.labels" . | nindent 4 }}
spec:
  podSelector:
    matchLabels:
      lolday.io/role: training
  policyTypes:
    - Egress
  egress: []
{{- end }}
---
# H-25: restrict /metrics ingress to the monitoring namespace's Prometheus
# pod only. Backend Service exposes /metrics on the same port as the public
# API (the prometheus-fastapi-instrumentator default); this policy is the
# only network-layer gate preventing arbitrary in-cluster scrapes.
{{- if .Values.backend.enabled }}
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: backend-metrics-from-monitoring-only
  namespace: {{ .Values.global.namespace }}
  labels:
    {{- include "lolday.labels" . | nindent 4 }}
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/component: backend
  policyTypes:
    - Ingress
  ingress:
    # Allow normal API traffic (port 8000) from cloudflared + jobs ns.
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: {{ .Values.global.namespace }}
          podSelector:
            matchLabels:
              app.kubernetes.io/component: cloudflared
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: {{ .Values.global.jobsNamespace }}
      ports:
        - port: 8000
          protocol: TCP
    # /metrics is on the same Service port, but we gate the scraper itself
    # by labelling the kps prometheus pod and matching on that label below.
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: monitoring
          podSelector:
            matchLabels:
              app.kubernetes.io/name: prometheus
      ports:
        - port: 8000
          protocol: TCP
{{- end }}
```

> Note: this policy is **additive** for in-namespace traffic that already works (frontend → backend uses cloudflared; future intra-ns paths can be added). The orphan `deny-training-egress` policy is left untouched in this task (it is removed in P2's H-13 cleanup).

- [ ] **Step 3: Verify `Values.global.jobsNamespace` exists.**

Run:

```bash
grep -n "jobsNamespace" charts/lolday/values.yaml
```

If missing, fall back to a hard-coded `lolday-jobs` literal in the template; this avoids introducing a new chart value in P1.

- [ ] **Step 4: Lint and render.**

```bash
helm lint charts/lolday
helm template charts/lolday 2>/dev/null | grep -B2 -A30 "backend-metrics-from-monitoring-only"
```

Expected: lint passes; the rendered policy shows the two `from:` blocks.

- [ ] **Step 5: Verification command for after deploy (no automated test for NP enforcement).**

Document the post-deploy check (do not run now; include in the commit message):

```bash
# Run after `bash scripts/deploy.sh` lands the policy:
kubectl run -n default --rm -i --restart=Never --image=curlimages/curl debug -- \
  curl -sS --max-time 5 http://lolday-backend.lolday.svc:8000/metrics
# Expected: timeout / connection refused (NP blocks). From the monitoring ns
# Prometheus pod, the scrape continues to succeed.
```

- [ ] **Step 6: Commit.**

```bash
git add charts/lolday/templates/network-policy.yaml
git commit -m "feat(charts): restrict backend /metrics to monitoring ns [H-25]

Adds an ingress NetworkPolicy permitting only cloudflared (own ns)
and jobs (cross-ns) for API traffic, and only the Prometheus pod in
the monitoring ns for /metrics scrapes. Post-deploy verification
runs an unprivileged curl pod in the default ns and expects a
timeout."
```

---

## Task 17: [C-1] Narrow backend RBAC — drop `secrets`/`configmaps` from `lolday` ns

**Findings:** C-1 (CRITICAL).

**Files:**

- Modify: `charts/lolday/templates/backend-rbac.yaml:22-24`

**Rationale:** Backend RCE currently → cluster-wide credential theft via `kubectl get secret`. The backend code does **not** read those secrets at runtime; they are mounted as env vars at pod start. The Role-granted `secrets` verbs exist for per-job Secret creation, which already lives in the `lolday-jobs` ns (see `templates/jobs-rbac.yaml`). The same-ns grant is therefore unused; removing it is safe.

- [ ] **Step 1: Verify backend code does not call `k8s.io/...secret` APIs.**

Run:

```bash
cd backend && grep -rn "read_namespaced_secret\|list_namespaced_secret\|create_namespaced_secret\|delete_namespaced_secret\|configmap" app/services/ | grep -v "lolday-jobs\|JOB_NAMESPACE" | head -20
```

Expected: no hits in the `lolday` ns. All Secret/ConfigMap ops should be in `lolday-jobs` (via `JOB_NAMESPACE` settings). If any hit exists in the `lolday` ns, **stop and treat this task as deferred** — the code change must come first.

- [ ] **Step 2: Cross-check the jobs-rbac.yaml grant.**

Run:

```bash
grep -A20 "kind: Role" charts/lolday/templates/jobs-rbac.yaml | head -30
```

Expected: a Role in the `lolday-jobs` ns granting `secrets` + `configmaps` verbs to the backend SA. If not present, **stop** — the jobs-rbac path must be confirmed before we remove the `lolday` ns grant.

- [ ] **Step 3: Update the chart.**

Modify `charts/lolday/templates/backend-rbac.yaml:18-27`. Replace:

```yaml
rules:
  # Phase 1 (2026-05-05) — pods / batch / batch.volcano.sh moved to
  # the lolday-jobs Role (templates/jobs-rbac.yaml). Same-ns Role keeps
  # only resources that legitimately live in the infra namespace.
  - apiGroups: [""]
    resources: [secrets, configmaps]
    verbs: [get, list, create, update, delete]
  - apiGroups: [""]
    resources: [persistentvolumeclaims]
    verbs: [get, list, watch]
```

with:

```yaml
rules:
  # P1 [C-1] (2026-05-12) — Phase 1's same-ns secret/configmap grant
  # removed. Backend reads its DB credential / Fernet key / Harbor pwd
  # via env mounts (Pod Spec, not runtime API). Per-job Secret creation
  # lives in lolday-jobs (templates/jobs-rbac.yaml).
  - apiGroups: [""]
    resources: [persistentvolumeclaims]
    verbs: [get, list, watch]
```

- [ ] **Step 4: Lint and render.**

```bash
helm lint charts/lolday
helm template charts/lolday 2>/dev/null | grep -A30 "name: backend\b" | grep -A20 "kind: Role\b" | head -25
```

Expected: lint passes; the rendered Role no longer mentions `secrets` or `configmaps` resources.

- [ ] **Step 5: Post-deploy verification command (document in commit).**

```bash
# Run after `bash scripts/deploy.sh` lands the change:
kubectl auth can-i get secrets -n lolday \
  --as=system:serviceaccount:lolday:backend
# Expected: "no"
kubectl auth can-i create secrets -n lolday-jobs \
  --as=system:serviceaccount:lolday:backend
# Expected: "yes" (jobs-rbac.yaml still grants this)
```

- [ ] **Step 6: Commit.**

```bash
git add charts/lolday/templates/backend-rbac.yaml
git commit -m "fix(charts)!: drop secrets/configmaps from backend lolday-ns Role [C-1]

CRITICAL: backend RCE -> cluster-wide credential theft is the blast
radius this fix shrinks. The backend pod reads its credentials via
env mounts, not via runtime kubectl-style API calls; per-job Secret
creation already lives in lolday-jobs (templates/jobs-rbac.yaml).

Post-deploy verification:
  kubectl auth can-i get secrets -n lolday \\
    --as=system:serviceaccount:lolday:backend  # -> no"
```

The `!` in the type signals a breaking deployment-time change. Operator must run `scripts/deploy.sh` to apply.

---

## P1 Done

After Task 17 lands, verify the whole phase end-to-end:

- [ ] **Step A: Run the full backend test suite.**

```bash
cd backend && uv run pytest -q
```

Expected: all green.

- [ ] **Step B: Run the helm lint.**

```bash
helm lint charts/lolday
```

Expected: clean.

- [ ] **Step C: Run pre-commit on all files.**

```bash
pre-commit run --all-files
```

Expected: clean.

- [ ] **Step D: Cross-check each finding ID closed.**

```bash
git log --oneline main..HEAD | grep -oE '\[[CHM][-0-9a-z-]+\]' | sort -u
```

Expected output (or a superset of):

```
[C-1]
[C-2]
[H-1]
[H-2]
[H-20]
[H-24]
[H-25]
[H-28]
[H-3]
[H-4]
[H-5]
[H-6a]
[H-6b]
[M-PAT-charset]
[M-WS-backdoor]
[M-docs-prod]
[M-event-dict]
[M-ilike]
```

- [ ] **Step E: Acceptance criteria from spec §6.1.**

Run the seven spec-level acceptance checks (see `docs/superpowers/specs/2026-05-12-security-hardening-design.md` §6.1 acceptance criteria 1–7). For criteria that require the chart to be deployed, document them as deferred to the next deploy window in a follow-up issue.

- [ ] **Step F: Tag the milestone.**

```bash
git tag security-p1-done -m "P1 stop-the-bleed complete"
```

(Optional. Helpful for git log filtering when reviewing P2.)

- [ ] **Step G: Hand off to P2.**

Update the task in `docs/superpowers/plans/` (or the project's task tracker) to mark `2026-05-12-security-hardening-p1-stop-bleed.md` complete and queue the P2 plan.

---

## Notes for the implementer

- **TDD discipline:** every Python-touching task has a "write the failing test first, then implement, then re-run" rhythm. If you find yourself implementing before writing the test, back up.
- **Test fixture reuse:** the existing `backend/tests/conftest.py` already provides `user_client`, `admin_client`, `test_session`, `mlflow_stub` and similar. Reuse them. Only add new fixtures when an existing one cannot be adapted.
- **Atomic commits:** each task ends with one commit. Do not bundle. Use the `[finding-id]` tag in the commit subject so `git log` can be filtered.
- **Pre-commit:** `prettier` will reformat markdown and YAML; that is expected. If pre-commit modifies the file, re-stage and re-commit. Do not use `--no-verify`.
- **No backwards-compat hedging:** per the umbrella spec §2, breaking changes are authorized. Drop the legacy code path; do not leave a feature flag.
- **Order of tasks:** the sequence above is the order I'd actually run them in (low-dep first, RBAC last). You can parallelize tasks that touch disjoint files, but C-1 must land in its own commit so the deploy step is a clean revert if it breaks anything.

---

## Self-review (writing-plans skill)

**Spec coverage** — every finding listed in spec §6.1 has a Task in this plan:

| Finding           | Task   |
| ----------------- | ------ |
| C-1               | T17    |
| C-2               | T1     |
| H-1               | T11    |
| H-2               | T11    |
| H-3               | T9     |
| H-4               | T10    |
| H-5               | T12    |
| H-6 (split a + b) | T4, T5 |
| H-20              | T13    |
| H-24              | T14    |
| H-25              | T16    |
| H-28              | T2     |
| M-WS-backdoor     | T15    |
| M-PAT-charset     | T6     |
| M-event-dict      | T7     |
| M-ilike           | T8     |
| M-docs-prod       | T3     |

**Placeholder scan:** all "Step N" bodies contain either an exact code block, a concrete shell command, or a concrete grep with expected pattern. No `TBD`/`TODO`/`fill in details`.

**Type consistency:** the helper `build_content_disposition` is referenced consistently from Task 5 onward (extracted from `experiments_proxy._build_content_disposition`). `_user_can_see_run_dict` is defined in T11 and reused for `list_runs`, `get_run`, `list_artifacts`, `download_artifact`. `RESERVED_TOP_LEVEL_KEYS` is the single source of truth in T12. `JobInternalEvent` is defined in T7 and consumed only there.

**Known fragilities:**

- T6 (M-PAT-charset) drops `min_length=8/max_length=200`. The regex is exact-length, so this is safe — but any caller that _manually_ validated against the old bounds will break. Grep `min_length=8` in `backend/` before merging to confirm.
- T7 (M-event-dict) `kind` allowlist enumerates concrete strings. If maldet introduces a new event kind without coordinating, jobs will 422. Treated as acceptable — the maldet contract is platform-controlled.
- T16 (H-25) hardcodes `app.kubernetes.io/name: prometheus` as the Prometheus pod label. If kube-prometheus-stack changes its default labels, this NP becomes too strict. Mitigation: a follow-up issue under P5 verifies the live labels.

---

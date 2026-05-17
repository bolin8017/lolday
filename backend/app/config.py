from typing import Annotated

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode

# The public Fernet key that was committed to backend/tests/conftest.py
# through 2026-05-12. Anyone with read access to the repo possesses it; a
# production deploy that inherits it makes every encrypted_token
# cleartext-equivalent to a source-reading attacker.
_LEGACY_TEST_FERNET_KEY = "ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2oasOM1Pg="


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://lolday:password@postgresql:5432/lolday"
    REDIS_URL: str = "redis://redis:6379/0"
    # #165: default to False -- production secrets-fail-closed posture.
    # The chart explicitly wires "false" today regardless, so this only
    # affects bare-process local dev (where setting DOCS_ENABLED=true in
    # the local env is a one-line opt-in). Defense-in-depth.
    DOCS_ENABLED: bool = False

    # Phase 2.4 (maldet 2.0 cutover): when truthy, ``POST /api/v1/jobs``
    # short-circuits with HTTP 503 + ``Retry-After`` so in-flight submissions
    # don't write into a half-wiped MLflow / Job state during the operator
    # cutover window. The frontend detects 503 to render a banner.
    BACKEND_MAINTENANCE_MODE: bool = False

    # P3 (2026-05-13, H-18): whitespace-separated list of base64 Fernet keys.
    # First key is active for encrypt; all keys are tried for decrypt.
    # Operator rotates by adding the new key in front, running
    # ``python -m app.scripts.rotate_fernet --old <OLD> --new <NEW>``, then
    # dropping the old key after the run completes.
    #
    # ``NoDecode`` opts the field out of pydantic-settings' default JSON
    # parsing for complex types; ``_split_fernet_keys`` does the whitespace
    # split instead. Mainstream pattern per pydantic-settings docs.
    FERNET_KEYS: Annotated[list[str], NoDecode] = []
    HARBOR_URL: str = "http://harbor.harbor.svc.cluster.local:80"
    HARBOR_ADMIN_USERNAME: str = "admin"
    HARBOR_ADMIN_PASSWORD: str = ""
    HARBOR_IMAGE_PREFIX: str = "harbor.harbor.svc:80"
    GITHUB_API_URL: str = "https://api.github.com"
    BUILD_NAMESPACE: str = "lolday"
    BUILD_IMAGE_HELPER: str = ""
    BUILD_IMAGE_BUILDKIT: str = "moby/buildkit:v0.29.0-rootless"
    BUILD_IMAGE_GIT: str = "alpine/git:2.45"
    BUILD_TIMEOUT_SECONDS: int = 1200
    BUILD_CONCURRENCY_PER_USER: int = 2
    BUILD_LOG_TAIL_BYTES: int = 8192
    REPO_MAX_SIZE_MB: int = 500
    BACKEND_INTERNAL_URL: str = "http://backend.lolday.svc:8000"
    RECONCILER_ENABLED: bool = True

    # Phase 4: Dataset & Jobs (MLflow)
    JOB_NAMESPACE: str = "lolday"
    # #175: namespaces that historically hosted ``job-token-*`` Secrets but
    # are no longer the live JOB_NAMESPACE. The reconciler sweep cleans up
    # both current + legacy namespaces in each iteration so a one-shot
    # migration doesn't leave a stale 718-row backlog in the old namespace.
    # Whitespace-separated env var; same parsing pattern as FERNET_KEYS.
    JOB_TOKEN_LEGACY_NAMESPACES: Annotated[list[str], NoDecode] = []
    JOB_HELPER_IMAGE: str = ""
    JOB_ACTIVE_DEADLINE_TRAIN_SECONDS: int = 21600  # 6h (default)
    JOB_ACTIVE_DEADLINE_EVALUATE_SECONDS: int = 1800  # 30m (default)
    JOB_ACTIVE_DEADLINE_PREDICT_SECONDS: int = 3600  # 1h (default)
    # Phase 5 — per-job override caps. User-supplied
    # active_deadline_seconds must be <= the matching MAX.
    JOB_ACTIVE_DEADLINE_TRAIN_MAX_SECONDS: int = 86400  # 24h
    JOB_ACTIVE_DEADLINE_EVALUATE_MAX_SECONDS: int = 7200  # 2h
    JOB_ACTIVE_DEADLINE_PREDICT_MAX_SECONDS: int = 14400  # 4h
    JOB_TTL_SECONDS_AFTER_FINISHED: int = 604800  # 7d
    JOB_NODE_SELECTOR_HOSTNAME: str = "server30"
    JOB_PER_USER_CONCURRENCY: int = 2
    JOB_IDEMPOTENCY_WINDOW_SECONDS: int = 300
    # M-internal-split (P2): /api/v1/internal/* lives on container port 8001
    # in a separate FastAPI app. NetworkPolicy gates 8001 to lolday-jobs only.
    JOB_BACKEND_URL: str = "http://backend.lolday.svc:8001"
    # Phase 11b: in-cluster base URL used by the event-tailer sidecar to POST
    # /internal/jobs/{id}/events. Chart-side wiring lands in Task 16.
    # Updated to :8001 in M-internal-split.
    INTERNAL_EVENTS_BASE_URL: str = "http://backend:8001"
    MLFLOW_TRACKING_URI: str = "http://mlflow.lolday.svc:5000"
    MLFLOW_HTTP_TIMEOUT_SECONDS: float = 10.0
    MLFLOW_HTTP_RETRIES: int = 3
    DATASET_CSV_MAX_BYTES: int = 10 * 1024 * 1024  # 10 MiB
    BODY_SIZE_MAX_BYTES: int = 12 * 1024 * 1024  # 12 MiB; headroom over 10 MiB CSV cap
    DATASET_SPOT_CHECK_COUNT: int = 100  # files per job dispatch
    DATASET_SPOT_CHECK_MISSING_THRESHOLD: int = 1  # fail if >= this many missing
    SAMPLES_ROOT: str = "/mnt/samples"  # parent of malware/, benign/
    SAMPLES_LOCAL_ROOT: str = "/data"  # for backend-side validation (matches hostPath)

    # Phase 6: FIFO scheduler
    # Physical GPU count on the cluster.  Used by compute_cluster_free_gpu to
    # determine how many GPUs are available for new submissions.  Set to the
    # actual node GPU count (server30 has 2); default 2.
    CLUSTER_PHYSICAL_GPU_COUNT: int = 2
    # Enable / disable the FIFO scheduler loop independently of RECONCILER_ENABLED.
    FIFO_RECONCILER_ENABLED: bool = True
    # Period between reconcile_fifo_queue invocations.  30 s is the mainstream
    # interval for a lightweight "what's queued → what's available" scan.
    FIFO_RECONCILER_PERIOD_SECONDS: int = 30

    # Host-aware GPU signal (2026-05-10).
    # Backend reads DCGM via Prometheus to detect non-K8s GPU usage on
    # server30 (a shared lab server).  See
    # docs/superpowers/specs/2026-05-10-host-aware-gpu-signal-design.md.
    GPU_SIGNAL_PROMETHEUS_URL: str = "http://kps-prometheus.monitoring.svc:9090"
    GPU_SIGNAL_QUERY_TIMEOUT_SECONDS: float = 5.0
    GPU_SIGNAL_CACHE_TTL_SECONDS: int = 10
    GPU_SIGNAL_UTIL_THRESHOLD_PERCENT: float = 5.0
    GPU_SIGNAL_VRAM_THRESHOLD_MB: int = 500
    # Fail-closed by default: when Prom is unreachable, the FIFO scheduler
    # returns free_count=0 and stops dispatching.  Set to false as an
    # escape hatch to fall back to the previous K8s-only computation.
    GPU_SIGNAL_FAIL_SAFE_BLOCK: bool = True

    # Phase 7.4: Discord user-event notifications + UI base URL for embed links
    DISCORD_WEBHOOK_URL_EVENTS: str = ""
    DISCORD_HTTP_TIMEOUT_SECONDS: float = 5.0
    LOLDAY_UI_BASE_URL: str = "https://lolday.connlabai.com"

    # Phase 10: Cloudflare Access SSO
    CF_ACCESS_TEAM_DOMAIN: str = ""  # e.g. "bolin8017.cloudflareaccess.com"
    CF_ACCESS_APP_AUD: str = ""  # Access App aud claim (64-char hex; NOT the uid)
    CF_ACCESS_JWKS_CACHE_TTL_SECONDS: int = 600
    AUTH_DEV_MODE: bool = False  # bypass Cloudflare JWT for local dev
    AUTH_DEV_EMAIL: str = ""  # synthetic user email when AUTH_DEV_MODE=true
    # frontend-slow live-stack uses in-process K8s + MLflow stubs to avoid
    # leaking real Volcano CRs onto the operator's cluster and to make CI
    # work without a kubeconfig. Refused in production by
    # validate_sso_config — see app/services/_stubs.py.
    SPEC_LANE_STUBS: bool = False
    # D2.2 / R4 — multi-persona dev mode. When AUTH_DEV_MODE=true, the backend
    # honours an X-Dev-Persona request header (admin/developer/user) and
    # resolves the persona's synthetic email + role for that request. Falls
    # back to AUTH_DEV_EMAIL when the header is absent (backward compat).
    # Unblocks Phase 3 multi-persona Playwright parallel (architecture.md §10 #13).
    AUTH_DEV_PERSONAS: dict[str, dict[str, str]] = {
        "admin": {"email": "admin@dev.local", "role": "admin"},
        "developer": {"email": "dev@dev.local", "role": "developer"},
        "user": {"email": "user@dev.local", "role": "user"},
    }

    # Deployment mode — helm ships "production"; tests / local dev override.
    # `validate_sso_config` only fails the boot when this is "production".
    ENVIRONMENT: str = "production"

    @field_validator("CF_ACCESS_TEAM_DOMAIN")
    @classmethod
    def _validate_cf_access_team_domain(cls, v: str) -> str:
        """L-team-domain-validator (security-hardening P5).

        Enforce a hostname shape so a malformed value (typo, scheme
        leaked in) is a CrashLoopBackOff at boot rather than every
        request returning 401 with an obscure JWKS-lookup error.
        Empty string passes (used in dev / test where the production
        model_validator is bypassed by ENVIRONMENT != 'production').
        """
        import re

        if v == "":
            return v
        if not re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", v):
            raise ValueError(
                f"CF_ACCESS_TEAM_DOMAIN={v!r} is not a valid hostname "
                "(expected lowercase dot-separated labels, e.g. "
                "'bolin8017.cloudflareaccess.com'). Verify the env "
                "var did not accidentally include a scheme or path."
            )
        return v

    @field_validator("JOB_NAMESPACE")
    @classmethod
    def _validate_job_namespace(cls, v: str) -> str:
        """L-promql-fstring (security-hardening P6).

        ``JOB_NAMESPACE`` is interpolated into a PromQL f-string in
        ``services/gpu_signal.py`` (the host-aware GPU signal query). PromQL
        itself has no injection-equivalent of SQL, but any operator-set value
        that lands in a query string ought to match a defensive shape. We
        require the standard Kubernetes DNS-label form (RFC 1123) -- the only
        shape a legitimate namespace can have anyway.
        """
        import re

        if not re.fullmatch(r"[a-z0-9-]+", v):
            raise ValueError(
                f"JOB_NAMESPACE={v!r} is not a valid Kubernetes DNS label "
                "(expected lowercase letters, digits, hyphens; non-empty)."
            )
        return v

    @field_validator("FERNET_KEYS", mode="before")
    @classmethod
    def _split_fernet_keys(cls, v):
        """Accept whitespace-separated env value; collapse to list[str]."""
        if isinstance(v, str):
            return [k for k in v.split() if k]
        return v

    @field_validator("JOB_TOKEN_LEGACY_NAMESPACES", mode="before")
    @classmethod
    def _split_job_token_legacy_namespaces(cls, v):
        """Accept whitespace-separated env value; collapse to list[str]."""
        if isinstance(v, str):
            return [k for k in v.split() if k]
        return v

    @model_validator(mode="after")
    def validate_sso_config(self) -> "Settings":
        """Fail-fast on production misconfiguration. Tests and local dev opt
        out by setting ENVIRONMENT != 'production'."""
        if self.ENVIRONMENT != "production":
            return self
        if self.AUTH_DEV_MODE:
            raise ValueError(
                "AUTH_DEV_MODE=true is forbidden when ENVIRONMENT=production — "
                "it disables Cloudflare Access JWT verification entirely"
            )
        if self.SPEC_LANE_STUBS:
            raise ValueError(
                "SPEC_LANE_STUBS=true is forbidden when ENVIRONMENT=production — "
                "in-process K8s + MLflow stubs would replace real cluster traffic"
            )
        if not self.CF_ACCESS_TEAM_DOMAIN or not self.CF_ACCESS_APP_AUD:
            raise ValueError(
                "CF_ACCESS_TEAM_DOMAIN and CF_ACCESS_APP_AUD must both be set "
                "in production — an empty team domain makes the JWKS URL "
                "resolve to https:/// and every request 401s"
            )
        return self

    @model_validator(mode="after")
    def validate_helper_images(self) -> "Settings":
        """Fail-fast on production misconfiguration. Helper image refs are
        produced by scripts/build-helpers.sh into charts/lolday/helpers.lock
        and injected by scripts/deploy.sh — never hardcoded as defaults."""
        if self.ENVIRONMENT != "production":
            return self
        missing = [
            name
            for name in ("BUILD_IMAGE_HELPER", "JOB_HELPER_IMAGE")
            if not getattr(self, name)
        ]
        if missing:
            raise ValueError(
                f"{', '.join(missing)} must be set in production. "
                "Produce the values via scripts/build-helpers.sh and inject "
                "them via scripts/deploy.sh."
            )
        return self

    @model_validator(mode="after")
    def validate_fernet_keys(self) -> "Settings":
        """Production refuses an empty list and refuses the public test key.

        Tests / dev bypass via ``ENVIRONMENT != "production"``. The split env
        parsing happens in ``_split_fernet_keys``; this validator only checks
        the resulting list.
        """
        if self.ENVIRONMENT != "production":
            return self
        if not self.FERNET_KEYS:
            raise ValueError(
                "FERNET_KEYS is required in production (whitespace-separated "
                "list of base64 Fernet keys; first key is active for encrypt). "
                "FERNET_KEY (singular) was renamed in P3 — update "
                ".lolday-secrets.env."
            )
        if _LEGACY_TEST_FERNET_KEY in self.FERNET_KEYS:
            raise ValueError(
                "FERNET_KEYS contains the public test key from "
                "backend/tests/conftest.py (committed to the repo until "
                "2026-05-12) — encrypted columns would not actually be "
                "secret. Generate a fresh key: "
                'python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"'
            )
        return self


settings = Settings()

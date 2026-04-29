from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://lolday:password@postgresql:5432/lolday"
    REDIS_URL: str = "redis://redis:6379/0"
    DOCS_ENABLED: bool = True

    # Phase 3: Detector Lifecycle
    FERNET_KEY: str = ""  # base64-encoded 32-byte Fernet key
    HARBOR_URL: str = "http://harbor.harbor.svc.cluster.local:80"
    HARBOR_ADMIN_USERNAME: str = "admin"
    HARBOR_ADMIN_PASSWORD: str = ""
    HARBOR_IMAGE_PREFIX: str = "harbor.harbor.svc:80"
    GITHUB_API_URL: str = "https://api.github.com"
    BUILD_NAMESPACE: str = "lolday"
    BUILD_IMAGE_HELPER: str = "harbor.harbor.svc:80/lolday/build-helper:v3"
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
    JOB_HELPER_IMAGE: str = "harbor.harbor.svc:80/lolday/job-helper:v4"
    JOB_ACTIVE_DEADLINE_TRAIN_SECONDS: int = 21600      # 6h
    JOB_ACTIVE_DEADLINE_EVALUATE_SECONDS: int = 1800    # 30m
    JOB_ACTIVE_DEADLINE_PREDICT_SECONDS: int = 3600     # 1h
    JOB_TTL_SECONDS_AFTER_FINISHED: int = 604800        # 7d
    JOB_NODE_SELECTOR_HOSTNAME: str = "server30"
    JOB_PER_USER_CONCURRENCY: int = 2
    JOB_IDEMPOTENCY_WINDOW_SECONDS: int = 300
    JOB_BACKEND_URL: str = "http://backend.lolday.svc:8000"
    # Phase 11b: in-cluster base URL used by the event-tailer sidecar to POST
    # /internal/jobs/{id}/events. Chart-side wiring lands in Task 16.
    INTERNAL_EVENTS_BASE_URL: str = "http://backend:8000"
    MLFLOW_TRACKING_URI: str = "http://mlflow.lolday.svc:5000"
    MLFLOW_HTTP_TIMEOUT_SECONDS: float = 10.0
    MLFLOW_HTTP_RETRIES: int = 3
    DATASET_CSV_MAX_BYTES: int = 10 * 1024 * 1024            # 10 MiB
    DATASET_SPOT_CHECK_COUNT: int = 100                       # files per job dispatch
    DATASET_SPOT_CHECK_MISSING_THRESHOLD: int = 1             # fail if >= this many missing
    SAMPLES_ROOT: str = "/mnt/samples"                        # parent of malware/, benign/
    SAMPLES_LOCAL_ROOT: str = "/data"                         # for backend-side validation (matches hostPath)

    # Phase 7.4: Discord user-event notifications + UI base URL for embed links
    DISCORD_WEBHOOK_URL_EVENTS: str = ""
    DISCORD_HTTP_TIMEOUT_SECONDS: float = 5.0
    LOLDAY_UI_BASE_URL: str = "https://lolday.connlabai.com"

    # Phase 10: Cloudflare Access SSO
    CF_ACCESS_TEAM_DOMAIN: str = ""        # e.g. "bolin8017.cloudflareaccess.com"
    CF_ACCESS_APP_AUD: str = ""            # Access App aud claim (64-char hex; NOT the uid)
    CF_ACCESS_JWKS_CACHE_TTL_SECONDS: int = 600
    AUTH_DEV_MODE: bool = False            # bypass Cloudflare JWT for local dev
    AUTH_DEV_EMAIL: str = ""               # synthetic user email when AUTH_DEV_MODE=true

    # Deployment mode — helm ships "production"; tests / local dev override.
    # `validate_sso_config` only fails the boot when this is "production".
    ENVIRONMENT: str = "production"

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
        if not self.CF_ACCESS_TEAM_DOMAIN or not self.CF_ACCESS_APP_AUD:
            raise ValueError(
                "CF_ACCESS_TEAM_DOMAIN and CF_ACCESS_APP_AUD must both be set "
                "in production — an empty team domain makes the JWKS URL "
                "resolve to https:/// and every request 401s"
            )
        return self


settings = Settings()

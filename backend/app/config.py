from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://lolday:password@postgresql:5432/lolday"
    REDIS_URL: str = "redis://redis:6379/0"
    JWT_SECRET: str = "CHANGE-ME-IN-PRODUCTION"
    JWT_LIFETIME_SECONDS: int = 3600
    FIRST_ADMIN_EMAIL: str = ""
    FIRST_ADMIN_PASSWORD: str = ""
    DOCS_ENABLED: bool = True
    RATE_LIMIT_DEFAULT: str = "60/minute"
    RATE_LIMIT_AUTH: str = "10/minute"

    # Phase 3: Detector Lifecycle
    FERNET_KEY: str = ""  # base64-encoded 32-byte Fernet key
    HARBOR_URL: str = "http://harbor.harbor.svc.cluster.local:80"
    HARBOR_ADMIN_USERNAME: str = "admin"
    HARBOR_ADMIN_PASSWORD: str = ""
    HARBOR_IMAGE_PREFIX: str = "harbor.harbor.svc:80"
    GITHUB_API_URL: str = "https://api.github.com"
    BUILD_NAMESPACE: str = "lolday"
    BUILD_IMAGE_HELPER: str = "harbor.harbor.svc:80/lolday/build-helper:v1"
    BUILD_IMAGE_KANIKO: str = "gcr.io/kaniko-project/executor:latest"
    BUILD_IMAGE_GIT: str = "alpine/git:2.45"
    BUILD_TIMEOUT_SECONDS: int = 1200
    BUILD_CONCURRENCY_PER_USER: int = 2
    BUILD_LOG_TAIL_BYTES: int = 8192
    REPO_MAX_SIZE_MB: int = 500
    BACKEND_INTERNAL_URL: str = "http://backend.lolday.svc:8000"
    RECONCILER_ENABLED: bool = True

    # Phase 4: Dataset & Jobs (MLflow)
    JOB_NAMESPACE: str = "lolday"
    JOB_HELPER_IMAGE: str = "harbor.lolday.svc:80/lolday/job-helper:v1"
    JOB_ACTIVE_DEADLINE_TRAIN_SECONDS: int = 21600      # 6h
    JOB_ACTIVE_DEADLINE_EVALUATE_SECONDS: int = 1800    # 30m
    JOB_ACTIVE_DEADLINE_PREDICT_SECONDS: int = 3600     # 1h
    JOB_TTL_SECONDS_AFTER_FINISHED: int = 604800        # 7d
    JOB_NODE_SELECTOR_HOSTNAME: str = "server30"
    JOB_PER_USER_CONCURRENCY: int = 2
    JOB_IDEMPOTENCY_WINDOW_SECONDS: int = 300
    JOB_BACKEND_URL: str = "http://backend.lolday.svc:8000"
    MLFLOW_TRACKING_URI: str = "http://mlflow.lolday.svc:5000"
    MLFLOW_HTTP_TIMEOUT_SECONDS: float = 10.0
    MLFLOW_HTTP_RETRIES: int = 3
    DATASET_CSV_MAX_BYTES: int = 10 * 1024 * 1024            # 10 MiB
    DATASET_SPOT_CHECK_COUNT: int = 100                       # files per job dispatch
    DATASET_SPOT_CHECK_MISSING_THRESHOLD: int = 1             # fail if >= this many missing
    SAMPLES_ROOT: str = "/mnt/samples"                        # parent of malware/, benign/
    SAMPLES_LOCAL_ROOT: str = "/data"                         # for backend-side validation (matches hostPath)

    # Cookie auth (Phase 5)
    COOKIE_LIFETIME_SECONDS: int = 12 * 60 * 60   # 12 hours sliding
    COOKIE_SECURE: bool = True                     # set False in dev env
    COOKIE_NAME: str = "lolday_session"
    COOKIE_SAMESITE: str = "lax"

    # Phase 7.4: Discord user-event notifications + UI base URL for embed links
    DISCORD_WEBHOOK_URL_EVENTS: str = ""
    DISCORD_HTTP_TIMEOUT_SECONDS: float = 5.0
    LOLDAY_UI_BASE_URL: str = "https://lolday.connlabai.com"


settings = Settings()

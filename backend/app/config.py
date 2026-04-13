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


settings = Settings()

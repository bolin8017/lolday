from datetime import datetime

from pydantic import BaseModel, Field

from app.models.credential import GitProvider


class GitCredentialSet(BaseModel):
    provider: GitProvider = GitProvider.GITHUB
    token: str = Field(min_length=8, max_length=200)


class GitCredentialRead(BaseModel):
    provider: GitProvider
    token_hint: str
    created_at: datetime
    updated_at: datetime

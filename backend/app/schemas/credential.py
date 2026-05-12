from datetime import datetime

from pydantic import BaseModel, Field

from app.models.credential import GitProvider


class GitCredentialSet(BaseModel):
    provider: GitProvider = GitProvider.GITHUB
    # GitHub PAT formats per https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens
    #   classic:        ghp_<36 [A-Za-z0-9]>
    #   fine-grained:   github_pat_<82 [A-Za-z0-9_]>
    token: str = Field(pattern=r"^(ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{82})$")


class GitCredentialRead(BaseModel):
    provider: GitProvider
    token_hint: str
    created_at: datetime
    updated_at: datetime

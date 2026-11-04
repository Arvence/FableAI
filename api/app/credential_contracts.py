from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, SecretStr, field_serializer

from app.contracts import ContractModel, Identifier
from app.project_contracts import ProjectId


@dataclass(frozen=True)
class Principal:
    credential_id: UUID | None = None
    project_id: str | None = None
    is_admin: bool = False
    is_local_demo: bool = False


class CredentialCreate(ContractModel):
    name: str = Field(min_length=1, max_length=100)


class CredentialSummary(ContractModel):
    credential_id: Identifier
    name: str
    kind: Literal["administrator", "project"]
    project_id: ProjectId | None
    created_at: datetime
    revoked_at: datetime | None


class CredentialIssued(CredentialSummary):
    api_key: SecretStr = Field(repr=False)

    @field_serializer("api_key", when_used="json")
    def serialize_api_key(self, value: SecretStr) -> str:
        return value.get_secret_value()


class CredentialPage(ContractModel):
    items: list[CredentialSummary] = Field(max_length=100)
    next_offset: int | None

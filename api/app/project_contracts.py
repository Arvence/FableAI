from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import uuid4

from pydantic import Field, StringConstraints, model_validator

from app.contracts import MAX_ENTRIES, MAX_ENTRY_BODY_CHARS, MAX_INSTRUCTION_CHARS, MAX_REFERENCES, ContentCategory, ContractModel, GenerationSettings, Identifier, Instruction, LanguageCode, ReferenceText, validate_reference_budget
from app.conversation_contracts import MAX_STABLE_INSTRUCTIONS


ProjectId = Annotated[str, StringConstraints(strip_whitespace=False, min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9_-]*$")]
DisplayName = Annotated[str, StringConstraints(min_length=1, max_length=200)]


class ProjectGenerationSettings(GenerationSettings):
    body_length_mode: Literal["strict", "target"] = "target"
    count: int = Field(default=1, ge=1, le=MAX_ENTRIES)
    tone: Literal["neutral", "formal", "informative", "playful"] = "neutral"
    category: ContentCategory = "announcement"
    language: LanguageCode = "en"
    min_body_chars: int = Field(default=100, ge=1, le=MAX_ENTRY_BODY_CHARS)
    max_body_chars: int = Field(default=300, ge=1, le=MAX_ENTRY_BODY_CHARS)


class ReferenceAuthorization(ContractModel):
    basis: Literal["owned", "public_domain", "licensed", "explicit_permission"]
    evidence: str = Field(min_length=1, max_length=1_000)
    model_use_allowed: bool
    non_confidential: bool

    @model_validator(mode="after")
    def validate_authorization(self) -> Self:
        if not self.model_use_allowed or not self.non_confidential:
            raise ValueError("Saved samples require permission for model use and confirmation that they are non-confidential.")
        return self


class ProjectReferenceSample(ReferenceText):
    id: Identifier = Field(default_factory=uuid4)
    authorization: ReferenceAuthorization


class ProjectConfiguration(ContractModel):
    display_name: DisplayName
    generation_settings: ProjectGenerationSettings = Field(default_factory=ProjectGenerationSettings)
    instructions: list[Instruction] = Field(default_factory=list, max_length=MAX_STABLE_INSTRUCTIONS)
    references: list[ProjectReferenceSample] = Field(default_factory=list, max_length=MAX_REFERENCES)

    @model_validator(mode="after")
    def validate_budgets(self) -> Self:
        if sum(map(len, self.instructions)) > MAX_INSTRUCTION_CHARS:
            raise ValueError("Reusable instructions exceed their character budget.")
        validate_reference_budget(len(self.references), sum(len(item.text) for item in self.references))
        if len({item.id for item in self.references}) != len(self.references):
            raise ValueError("Reference IDs must be unique within a project.")
        return self


class ProjectCreate(ProjectConfiguration):
    project_id: ProjectId


class ProjectUpdate(ContractModel):
    display_name: DisplayName | None = None
    generation_settings: GenerationSettings | None = None
    instructions: list[Instruction] | None = Field(default=None, max_length=MAX_STABLE_INSTRUCTIONS)
    references: list[ProjectReferenceSample] | None = Field(default=None, max_length=MAX_REFERENCES)

    @model_validator(mode="after")
    def validate_changes(self) -> Self:
        if not self.model_fields_set or any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("Supply at least one field to update; explicit null values are not supported.")
        if self.generation_settings is not None:
            settings = self.generation_settings
            if not settings.model_fields_set or any(getattr(settings, field) is None for field in settings.model_fields_set):
                raise ValueError("Supply concrete generation settings to update.")
        return self


class ProjectSummary(ContractModel):
    project_id: ProjectId
    display_name: DisplayName
    created_at: datetime
    updated_at: datetime


class ProjectDetail(ProjectCreate):
    created_at: datetime
    updated_at: datetime
    output_contract: Literal["draft_batch"] = "draft_batch"


class ProjectPage(ContractModel):
    items: list[ProjectSummary] = Field(max_length=100)
    next_offset: int | None

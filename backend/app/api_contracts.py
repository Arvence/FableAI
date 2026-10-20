from datetime import datetime
from typing import Literal

from pydantic import Field

from app.contracts import MAX_ACTIVE_ENTRIES, ContractModel, GeneratedEntry, Identifier, ReferenceCollection, SourcePermission
from app.conversation_contracts import ConversationResponse, GenerationPreferences, PendingTurn
from app.reference_management import ReferenceImportRequest


class ConversationSummary(ContractModel):
    conversation_id: Identifier
    created_at: datetime
    updated_at: datetime
    revision: int = Field(ge=0)
    processing: bool


class StoredEntry(GeneratedEntry):
    status: Literal["draft", "accepted", "rejected"]


class ConversationDetail(ConversationSummary):
    preferences: GenerationPreferences
    stable_instructions: list[str]
    pending: PendingTurn | None
    references: ReferenceCollection
    entries: list[StoredEntry] = Field(max_length=MAX_ACTIVE_ENTRIES)


class ConversationPage(ContractModel):
    items: list[ConversationSummary] = Field(max_length=100)
    next_offset: int | None


class StoredMessage(ContractModel):
    message_id: Identifier
    submission_id: Identifier
    sequence: int = Field(ge=1)
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime
    request: dict | None
    response: ConversationResponse | None


class MessagePage(ContractModel):
    items: list[StoredMessage] = Field(max_length=100)
    next_after: int | None


class ImportSubmission(ReferenceImportRequest):
    permissions: list[SourcePermission] = Field(default_factory=list, max_length=3)


class ImportResponse(ContractModel):
    kind: Literal["reference_import"] = "reference_import"
    conversation_id: Identifier
    references: ReferenceCollection


class ReviewRequest(ContractModel):
    expected_version: int = Field(ge=1)
    status: Literal["accepted", "rejected"]


class ReviewDecision(ContractModel):
    decision_id: int
    entry_id: Identifier
    version: int
    status: Literal["accepted", "rejected"]
    created_at: datetime


class VersionPage(ContractModel):
    items: list[StoredEntry] = Field(max_length=100)
    next_offset: int | None


class ReviewPage(ContractModel):
    items: list[ReviewDecision] = Field(max_length=100)
    next_after: int | None

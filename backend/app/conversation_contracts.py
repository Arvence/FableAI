from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from app.contracts import MAX_ACTIVE_BODY_CHARS, MAX_ACTIVE_ENTRIES, MAX_ENTRY_BODY_CHARS, MAX_ENTRIES, MAX_INSTRUCTION_CHARS, ClarificationResponse, ContractModel, Conversation, ErrorResponse, GeneratedEntry, GenerationResponse, Headline, Instruction, LanguageCode, MessageResponse, RevisionResponse, RevisionTarget


MAX_STABLE_INSTRUCTIONS = 8
LengthChange = Literal["keep", "shorter", "longer"]


class GenerationPreferences(ContractModel):
    topic: Headline | None = None
    count: int | None = Field(default=None, ge=1, le=MAX_ENTRIES)
    tone: Literal["neutral", "formal", "informative", "playful"] | None = None
    category: Literal["news", "announcement"] | None = None
    language: LanguageCode | None = None
    min_body_chars: int | None = Field(default=None, ge=1, le=MAX_ENTRY_BODY_CHARS)
    max_body_chars: int | None = Field(default=None, ge=1, le=MAX_ENTRY_BODY_CHARS)

    @model_validator(mode="after")
    def validate_lengths(self) -> Self:
        if self.min_body_chars is not None and self.max_body_chars is not None and self.min_body_chars > self.max_body_chars:
            raise ValueError("Minimum body length cannot exceed maximum body length.")
        return self


class TurnPlan(ContractModel):
    action: Literal["message", "clarification", "generate", "revise"]
    preferences: GenerationPreferences = Field(default_factory=GenerationPreferences)
    stable_instruction: Instruction | None = None
    question: str | None = Field(default=None, min_length=1, max_length=500)
    length_change: LengthChange = "keep"


class PendingTurn(ContractModel):
    action: Literal["generate", "revise"]
    instruction: Instruction
    targets: list[RevisionTarget] = Field(default_factory=list, max_length=MAX_ENTRIES)
    length_change: LengthChange = "keep"
    preference_changes: GenerationPreferences = Field(default_factory=GenerationPreferences)

    @model_validator(mode="after")
    def validate_targets(self) -> Self:
        if self.action == "generate" and self.targets:
            raise ValueError("Pending generation cannot contain revision targets.")
        if len({target.entry_id for target in self.targets}) != len(self.targets):
            raise ValueError("Pending targets must be unique.")
        return self


class ConversationSnapshot(Conversation):
    preferences: GenerationPreferences = Field(default_factory=GenerationPreferences)
    stable_instructions: list[Instruction] = Field(default_factory=list, max_length=MAX_STABLE_INSTRUCTIONS)
    active_entries: list[GeneratedEntry] = Field(default_factory=list, max_length=MAX_ACTIVE_ENTRIES)
    pending: PendingTurn | None = None

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        if sum(map(len, self.stable_instructions)) > MAX_INSTRUCTION_CHARS:
            raise ValueError("Stable instructions exceed their character budget.")
        if len({entry.id for entry in self.active_entries}) != len(self.active_entries):
            raise ValueError("Active entry IDs must be unique.")
        if sum(len(entry.body) for entry in self.active_entries) > MAX_ACTIVE_BODY_CHARS:
            raise ValueError("Active draft bodies exceed their character budget.")
        return self


ConversationResponse = Annotated[MessageResponse | ClarificationResponse | GenerationResponse | RevisionResponse | ErrorResponse, Field(discriminator="kind")]


class ConversationTurnResult(ContractModel):
    response: ConversationResponse
    snapshot: ConversationSnapshot | None

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.snapshot is not None and self.response.conversation_id != self.snapshot.conversation_id:
            raise ValueError("Response and snapshot must belong to the same conversation.")
        return self

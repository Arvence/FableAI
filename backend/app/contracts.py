from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints, UUID4, UrlConstraints, field_validator, model_validator


MAX_INSTRUCTION_CHARS = 4_000
MAX_REFERENCES = 10
MAX_REFERENCE_CHARS = 10_000
MAX_TOTAL_REFERENCE_CHARS = 30_000
MAX_HEADLINE_CHARS = 200
MAX_URLS = 5
MAX_URL_CHARS = 2_048
MAX_ENTRIES = 5
MAX_ENTRY_BODY_CHARS = 6_000
MAX_TAGS = 8
MAX_TAG_CHARS = 32
MAX_MESSAGE_CHARS = 4_000
MAX_CONTEXT_MESSAGES = 20
MAX_CONTEXT_CHARS = 40_000
MAX_ERROR_DETAILS = 20

Identifier = Annotated[UUID4, Field(strict=False)]
Instruction = Annotated[str, StringConstraints(min_length=1, max_length=MAX_INSTRUCTION_CHARS)]
ReferenceContent = Annotated[str, StringConstraints(min_length=1, max_length=MAX_REFERENCE_CHARS)]
Headline = Annotated[str, StringConstraints(min_length=1, max_length=MAX_HEADLINE_CHARS)]
MessageContent = Annotated[str, StringConstraints(min_length=1, max_length=MAX_MESSAGE_CHARS)]
SourceUrl = Annotated[HttpUrl, UrlConstraints(max_length=MAX_URL_CHARS)]
LanguageCode = Annotated[str, StringConstraints(min_length=2, max_length=35, pattern=r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")]
ReferenceImportStatus = Literal["not_requested", "pending", "imported", "failed"]
ErrorCode = Literal["invalid_request", "conversation_not_found", "entry_not_found", "version_conflict", "reference_import_failed", "generation_failed", "rate_limited"]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class ErrorDetail(ContractModel):
    field: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=500)


class ErrorInfo(ContractModel):
    code: ErrorCode
    message: str = Field(min_length=1, max_length=500)
    details: list[ErrorDetail] = Field(default_factory=list, max_length=MAX_ERROR_DETAILS)


class ErrorResponse(ContractModel):
    kind: Literal["error"]
    conversation_id: Identifier | None = None
    message_id: Identifier | None = None
    error: ErrorInfo


class ReferenceText(ContractModel):
    id: Identifier
    text: ReferenceContent


class SampleHeadline(ContractModel):
    id: Identifier
    text: Headline


class URLReference(ContractModel):
    id: Identifier
    url: SourceUrl
    import_status: ReferenceImportStatus = "not_requested"
    imported_text: ReferenceContent | None = None
    error: ErrorInfo | None = None

    @model_validator(mode="after")
    def validate_import_status(self) -> Self:
        if self.import_status == "imported":
            if self.imported_text is None or self.error is not None:
                raise ValueError("An imported reference requires text and cannot contain an error.")
        elif self.import_status == "failed":
            if self.imported_text is not None or self.error is None or self.error.code != "reference_import_failed":
                raise ValueError("A failed import requires a reference_import_failed error and no imported text.")
        elif self.imported_text is not None or self.error is not None:
            raise ValueError("A reference that has not completed import cannot contain imported text or an error.")
        return self


def validate_reference_budget(count: int, characters: int) -> None:
    if count > MAX_REFERENCES:
        raise ValueError(f"At most {MAX_REFERENCES} references are allowed across examples, headlines, and URLs.")
    if characters > MAX_TOTAL_REFERENCE_CHARS:
        raise ValueError(f"References cannot exceed {MAX_TOTAL_REFERENCE_CHARS} characters in total.")


class ReferenceCollection(ContractModel):
    examples: list[ReferenceText] = Field(default_factory=list, max_length=MAX_REFERENCES)
    headlines: list[SampleHeadline] = Field(default_factory=list, max_length=MAX_REFERENCES)
    urls: list[URLReference] = Field(default_factory=list, max_length=MAX_URLS)

    @property
    def character_count(self) -> int:
        return sum(len(item.text) for item in [*self.examples, *self.headlines]) + sum(len(str(item.url)) + len(item.imported_text or "") for item in self.urls)

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        items = [*self.examples, *self.headlines, *self.urls]
        validate_reference_budget(len(items), self.character_count)
        if len({item.id for item in items}) != len(items):
            raise ValueError("Reference IDs must be unique within a conversation.")
        return self


class RevisionTarget(ContractModel):
    entry_id: Identifier
    expected_version: int = Field(ge=1)


class ChatTurnRequest(ContractModel):
    conversation_id: Identifier | None = None
    instruction: Instruction
    examples: list[ReferenceContent] = Field(default_factory=list, max_length=MAX_REFERENCES)
    headlines: list[Headline] = Field(default_factory=list, max_length=MAX_REFERENCES)
    source_urls: list[SourceUrl] = Field(default_factory=list, max_length=MAX_URLS)
    import_urls: bool = False
    generation_count: int | None = Field(default=None, ge=1, le=MAX_ENTRIES)
    revision_targets: list[RevisionTarget] = Field(default_factory=list, max_length=MAX_ENTRIES)

    @model_validator(mode="after")
    def validate_turn(self) -> Self:
        characters = sum(len(text) for text in [*self.examples, *self.headlines]) + sum(len(str(url)) for url in self.source_urls)
        validate_reference_budget(len(self.examples) + len(self.headlines) + len(self.source_urls), characters)
        if self.import_urls and not self.source_urls:
            raise ValueError("Explicit URL import requires at least one source URL.")
        if self.revision_targets:
            if self.conversation_id is None:
                raise ValueError("Targeted revisions require an existing conversation ID.")
            if self.generation_count is not None:
                raise ValueError("A turn cannot request both new entry generation and targeted revisions.")
            if len({target.entry_id for target in self.revision_targets}) != len(self.revision_targets):
                raise ValueError("Revision targets must have unique entry IDs.")
        return self


class ChatMessage(ContractModel):
    conversation_id: Identifier
    message_id: Identifier
    role: Literal["user", "assistant"]
    content: MessageContent


class Conversation(ContractModel):
    conversation_id: Identifier
    messages: list[ChatMessage] = Field(default_factory=list, max_length=MAX_CONTEXT_MESSAGES)
    references: ReferenceCollection = Field(default_factory=ReferenceCollection)

    @model_validator(mode="after")
    def validate_context(self) -> Self:
        if any(message.conversation_id != self.conversation_id for message in self.messages):
            raise ValueError("Every message must belong to this conversation.")
        if len({message.message_id for message in self.messages}) != len(self.messages):
            raise ValueError("Message IDs must be unique within a conversation.")
        if sum(len(message.content) for message in self.messages) + self.references.character_count > MAX_CONTEXT_CHARS:
            raise ValueError(f"Conversation context cannot exceed {MAX_CONTEXT_CHARS} characters including references.")
        return self


class GeneratedEntry(ContractModel):
    id: Identifier
    title: Headline
    body: str = Field(min_length=1, max_length=MAX_ENTRY_BODY_CHARS)
    category: Literal["news", "announcement"]
    tags: list[Annotated[str, StringConstraints(min_length=1, max_length=MAX_TAG_CHARS)]] = Field(max_length=MAX_TAGS)
    language: LanguageCode
    is_synthetic: Literal[True]
    status: Literal["draft"]
    version: int = Field(ge=1)

    @field_validator("is_synthetic", mode="before")
    @classmethod
    def validate_synthetic_flag(cls, value: object) -> object:
        if value is not True:
            raise ValueError("is_synthetic must be the JSON boolean true.")
        return value


class InitialEntry(GeneratedEntry):
    version: int = Field(ge=1, le=1)


class RevisedEntry(GeneratedEntry):
    version: int = Field(ge=2)


class AssistantResponse(ContractModel):
    conversation_id: Identifier
    message_id: Identifier
    assistant_message: MessageContent
    entries: list[GeneratedEntry] = Field(max_length=MAX_ENTRIES)

    @field_validator("entries")
    @classmethod
    def validate_entry_ids(cls, entries: list[GeneratedEntry]) -> list[GeneratedEntry]:
        if len({entry.id for entry in entries}) != len(entries):
            raise ValueError("Response entries must have unique IDs.")
        return entries


class MessageResponse(AssistantResponse):
    kind: Literal["message"]
    entries: list[GeneratedEntry] = Field(max_length=0)


class ClarificationResponse(AssistantResponse):
    kind: Literal["clarification"]
    entries: list[GeneratedEntry] = Field(max_length=0)


class GenerationResponse(AssistantResponse):
    kind: Literal["generation"]
    entries: list[InitialEntry] = Field(min_length=1, max_length=MAX_ENTRIES)


class RevisionResponse(AssistantResponse):
    kind: Literal["revision"]
    entries: list[RevisedEntry] = Field(min_length=1, max_length=MAX_ENTRIES)


ChatResponse = Annotated[MessageResponse | ClarificationResponse | GenerationResponse | RevisionResponse, Field(discriminator="kind")]

from uuid import uuid4

from pydantic import ValidationError

from app.contracts import MAX_ACTIVE_BODY_CHARS, DraftBatch, ErrorInfo, ErrorResponse, GeneratedEntry, GenerationRequest, GenerationResponse, InitialEntry, ReferenceCollection
from app.gemini_provider import GeminiProvider, GeminiProviderError
from app.generation_checks import GenerationRejected, validate_active_entries, validate_generated_batch
from app.generation_prompts import append_drafting_context, build_generation_prompt


class SyntheticGenerationService:
    def __init__(self, provider: GeminiProvider) -> None:
        self._provider = provider

    async def generate(self, request: GenerationRequest, references: ReferenceCollection, *, context: str | None = None, existing_entries: list[GeneratedEntry] | None = None, strict_body_length: bool = True) -> GenerationResponse | ErrorResponse:
        message_id = uuid4()
        conversation_id = None
        try:
            try:
                validated_request = GenerationRequest.model_validate(request.model_dump())
                conversation_id = validated_request.conversation_id
                validated_references = ReferenceCollection.model_validate(references.model_dump())
                previous = validate_active_entries(existing_entries or [])
            except ValidationError:
                raise GenerationRejected("invalid_request", "Generation constraints or references are invalid.") from None
            prompt = build_generation_prompt(validated_request, validated_references)
            result = await self._provider.generate(append_drafting_context(prompt.text, context), DraftBatch)
            if not isinstance(result, DraftBatch):
                raise GenerationRejected("generation_failed", "The provider did not return a validated draft batch.")
            batch = DraftBatch.model_validate(result.model_dump())
            previous_texts = tuple(text for entry in previous for text in (entry.title, entry.body))
            validate_generated_batch(batch, validated_request, prompt.overlap_sources + previous_texts, strict_body_length=strict_body_length)
            if sum(len(entry.body) for entry in [*previous, *batch.entries]) > MAX_ACTIVE_BODY_CHARS:
                raise GenerationRejected("generation_failed", "Generated bodies exceed the conversation's 24000-character draft limit.", "entries")
            entries = [InitialEntry(id=uuid4(), version=1, **entry.model_dump()) for entry in batch.entries]
            message = f"Created {len(entries)} fictional {'draft' if len(entries) == 1 else 'drafts'} for review."
            outside_target = [f"draft {index + 1}: {len(entry.body)} characters" for index, entry in enumerate(entries) if not validated_request.min_body_chars <= len(entry.body) <= validated_request.max_body_chars]
            if not strict_body_length and outside_target:
                message += f" Length notice: target {validated_request.min_body_chars}-{validated_request.max_body_chars} characters; " + "; ".join(outside_target) + ". Kept the original text."
            return GenerationResponse(
                kind="generation",
                conversation_id=validated_request.conversation_id,
                message_id=message_id,
                assistant_message=message,
                entries=entries,
            )
        except GenerationRejected as error:
            error_info = error.error
        except GeminiProviderError as error:
            error_info = error.error
        except ValidationError:
            error_info = ErrorInfo(code="generation_failed", message="The provider returned invalid draft fields.")
        return ErrorResponse(kind="error", conversation_id=conversation_id, message_id=message_id, error=error_info)

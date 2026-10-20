from uuid import uuid4

from pydantic import ValidationError

from app.contracts import DraftBatch, ErrorInfo, ErrorResponse, GeneratedEntry, GenerationRequest, GenerationResponse, InitialEntry, ReferenceCollection
from app.gemini_provider import GeminiProvider, GeminiProviderError
from app.generation_checks import GenerationRejected, validate_active_entries, validate_generated_batch
from app.generation_prompts import append_drafting_context, build_generation_prompt


class SyntheticGenerationService:
    def __init__(self, provider: GeminiProvider) -> None:
        self._provider = provider

    async def generate(self, request: GenerationRequest, references: ReferenceCollection, *, context: str | None = None, existing_entries: list[GeneratedEntry] | None = None) -> GenerationResponse | ErrorResponse:
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
            validate_generated_batch(batch, validated_request, prompt.overlap_sources + previous_texts)
            entries = [InitialEntry(id=uuid4(), version=1, **entry.model_dump()) for entry in batch.entries]
            return GenerationResponse(
                kind="generation",
                conversation_id=validated_request.conversation_id,
                message_id=message_id,
                assistant_message=f"Created {len(entries)} fictional {'draft' if len(entries) == 1 else 'drafts'} for review.",
                entries=entries,
            )
        except GenerationRejected as error:
            error_info = error.error
        except GeminiProviderError as error:
            error_info = error.error
        except ValidationError:
            error_info = ErrorInfo(code="generation_failed", message="The provider returned invalid draft fields.")
        return ErrorResponse(kind="error", conversation_id=conversation_id, message_id=message_id, error=error_info)

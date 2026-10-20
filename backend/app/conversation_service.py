import re
from uuid import UUID, uuid4

from pydantic import ValidationError

from app.contracts import MAX_ACTIVE_BODY_CHARS, MAX_ACTIVE_ENTRIES, MAX_CONTEXT_CHARS, MAX_CONTEXT_MESSAGES, MAX_ENTRIES, MAX_ENTRY_BODY_CHARS, MAX_GENERATION_BODY_CHARS, ChatMessage, ChatTurnRequest, ClarificationResponse, ErrorInfo, ErrorResponse, GeneratedEntry, GenerationRequest, MessageResponse, RevisionResponse, RevisionTarget
from app.conversation_context import build_turn_prompt, encode_drafting_context
from app.conversation_contracts import ConversationResponse, ConversationSnapshot, ConversationTurnResult, GenerationPreferences, PendingTurn, TurnPlan
from app.gemini_provider import GeminiProvider, GeminiProviderError
from app.generation_checks import UNSUPPORTED_TOPIC, GenerationRejected
from app.generation_prompts import summarize_references
from app.reference_management import ReferenceInputs, ReferenceLink, prepare_references
from app.revision_generation import RevisionGenerationService
from app.synthetic_generation import SyntheticGenerationService


NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
ORDINAL_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}
UUID_PATTERN = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE)


def _explicit_count(request: ChatTurnRequest) -> int | None:
    if request.generation_count is not None:
        return request.generation_count
    match = re.search(r"\b(?:generate|create|write)\s+(\d+|one|two|three|four|five)\b", request.instruction, re.IGNORECASE)
    if match is None:
        return None
    value = match[1].lower()
    return NUMBER_WORDS[value] if value in NUMBER_WORDS else int(value)


def _resolve_targets(request: ChatTurnRequest, snapshot: ConversationSnapshot) -> list[RevisionTarget] | None:
    entries = snapshot.active_entries
    by_id = {entry.id: entry for entry in entries}
    numbers = []
    selection_text = UUID_PATTERN.sub("", request.instruction)
    if re.search(r"\b(?:except|excluding|not|through|other than|or)\b|\d\s*[-\u2013]\s*\d|\bentr(?:y|ies)\s+\d+\s+to\s+\d+", selection_text, re.IGNORECASE):
        return None
    for match in re.finditer(r"\bentr(?:y|ies)\s+#?(\d+(?:(?:\s*,\s*(?:and\s+)?|\s+and\s+|\s*&\s*)#?\d+)*)", selection_text, re.IGNORECASE):
        numbers.extend(int(value) for value in re.findall(r"\d+", match[1]))
    for word, number in {**NUMBER_WORDS, **ORDINAL_WORDS}.items():
        if re.search(rf"\b(?:entry\s+{word}|{word}\s+entry)\b", selection_text, re.IGNORECASE):
            numbers.append(number)
    if any(number < 1 or number > len(entries) for number in numbers):
        raise GenerationRejected("entry_not_found", "A requested entry number is not present in this conversation.")
    mentioned = {entries[number - 1].id for number in numbers}
    mentioned.update(UUID(value) for value in UUID_PATTERN.findall(request.instruction))
    if re.search(r"\ball (?:the )?(?:entries|drafts)\b", request.instruction, re.IGNORECASE):
        mentioned.update(by_id)
    if request.revision_targets:
        targets = request.revision_targets
        if mentioned and mentioned != {target.entry_id for target in targets}:
            raise GenerationRejected("invalid_request", "The message and selected revision targets disagree.")
    elif mentioned:
        if not mentioned <= by_id.keys():
            raise GenerationRejected("entry_not_found", "A selected entry does not belong to this conversation.")
        targets = [RevisionTarget(entry_id=entry.id, expected_version=entry.version) for entry in entries if entry.id in mentioned]
    elif snapshot.pending and snapshot.pending.action == "revise" and snapshot.pending.targets:
        targets = snapshot.pending.targets
    elif len(entries) == 1:
        targets = [RevisionTarget(entry_id=entries[0].id, expected_version=entries[0].version)]
    else:
        return None
    if len(targets) > MAX_ENTRIES:
        raise GenerationRejected("invalid_request", "Revise at most five entries in one turn.")
    for target in targets:
        if target.entry_id not in by_id:
            raise GenerationRejected("entry_not_found", "A selected entry does not belong to this conversation.")
        if by_id[target.entry_id].version != target.expected_version:
            raise GenerationRejected("version_conflict", "A selected draft has changed. Select its current version before revising.")
    return targets


def _finish(snapshot: ConversationSnapshot, request: ChatTurnRequest, response: ConversationResponse) -> ConversationTurnResult:
    messages = [*snapshot.messages, ChatMessage(conversation_id=snapshot.conversation_id, message_id=uuid4(), role="user", content=request.instruction), ChatMessage(conversation_id=snapshot.conversation_id, message_id=response.message_id, role="assistant", content=response.assistant_message)]
    while len(messages) > MAX_CONTEXT_MESSAGES or sum(len(message.content) for message in messages) + snapshot.references.character_count > MAX_CONTEXT_CHARS:
        messages.pop(0)
    snapshot = ConversationSnapshot.model_validate({**snapshot.model_dump(), "messages": [message.model_dump() for message in messages]})
    return ConversationTurnResult.model_validate({"response": response.model_dump(), "snapshot": snapshot.model_dump()})


def _clarify(snapshot: ConversationSnapshot, request: ChatTurnRequest, question: str) -> ConversationTurnResult:
    response = ClarificationResponse(kind="clarification", conversation_id=snapshot.conversation_id, message_id=uuid4(), assistant_message=question, entries=[])
    return _finish(snapshot, request, response)


def _generation_request(snapshot: ConversationSnapshot, *, count: int, target: GeneratedEntry | None = None, changes: GenerationPreferences | None = None, length_change: str = "keep") -> GenerationRequest:
    values = snapshot.preferences.model_dump(exclude_none=True)
    values["count"] = count
    if target is not None:
        explicit = changes.model_dump(exclude_none=True) if changes else {}
        values["category"] = explicit.get("category", target.category)
        values["language"] = explicit.get("language", target.language)
        if length_change == "shorter":
            values["max_body_chars"] = min(values["max_body_chars"], len(target.body) - 1)
            values["min_body_chars"] = min(values["min_body_chars"], values["max_body_chars"])
        elif length_change == "longer":
            values["min_body_chars"] = max(values["min_body_chars"], len(target.body) + 1)
            values["max_body_chars"] = max(values["max_body_chars"], values["min_body_chars"])
    return GenerationRequest(conversation_id=snapshot.conversation_id, **values)


class ConversationService:
    def __init__(self, provider: GeminiProvider) -> None:
        self._provider = provider
        self._generation = SyntheticGenerationService(provider)
        self._revision = RevisionGenerationService(provider)

    async def handle_turn(self, request: ChatTurnRequest, snapshot: ConversationSnapshot | None = None) -> ConversationTurnResult:
        original = None
        conversation_id = None
        try:
            request = ChatTurnRequest.model_validate(request.model_dump())
            conversation_id = request.conversation_id
            if snapshot is None:
                if conversation_id is not None:
                    raise GenerationRejected("conversation_not_found", "No snapshot was supplied for this conversation.")
                working = ConversationSnapshot(conversation_id=uuid4())
                conversation_id = working.conversation_id
            else:
                if request.conversation_id != snapshot.conversation_id:
                    raise GenerationRejected("invalid_request", "The request and snapshot must identify the same conversation.")
                original = ConversationSnapshot.model_validate(snapshot.model_dump())
                working = original.model_copy(deep=True)
            inputs = ReferenceInputs(examples=request.examples, headlines=request.headlines, urls=[ReferenceLink(url=url) for url in request.source_urls])
            working.references = prepare_references(working.references, inputs)
        except (ValidationError, GenerationRejected) as error:
            info = error.error if isinstance(error, GenerationRejected) else ErrorInfo(code="invalid_request", message="The chat request or conversation snapshot is invalid.")
            return ConversationTurnResult(response=ErrorResponse(kind="error", conversation_id=conversation_id, message_id=uuid4(), error=info), snapshot=original)
        try:
            return await self._handle_validated_turn(request, working)
        except (GenerationRejected, GeminiProviderError) as error:
            info = error.error
        except ValidationError:
            info = ErrorInfo(code="generation_failed", message="The turn could not produce a valid conversation update.")
        return ConversationTurnResult(response=ErrorResponse(kind="error", conversation_id=conversation_id, message_id=uuid4(), error=info), snapshot=original)

    async def _handle_validated_turn(self, request: ChatTurnRequest, snapshot: ConversationSnapshot) -> ConversationTurnResult:
        if request.import_urls:
            return _clarify(snapshot, request, "The URLs are saved as links only. Paste authorized non-confidential text, or supply a snapshot from the separate permission-gated import workflow.")
        if UNSUPPORTED_TOPIC.search(request.instruction):
            raise GenerationRejected("invalid_request", "This conversation supports original fictional drafts, not copying, verification, impersonation, or attribution overrides.")
        if request.revision_targets:
            _resolve_targets(request, snapshot)
        result = await self._provider.generate(build_turn_prompt(request, snapshot), TurnPlan)
        if not isinstance(result, TurnPlan):
            raise GenerationRejected("generation_failed", "The provider did not return a validated turn interpretation.")
        plan = TurnPlan.model_validate(result.model_dump())
        action = "clarification" if plan.action == "clarification" else "revise" if request.revision_targets else "generate" if request.generation_count is not None else plan.action
        explicit_count = _explicit_count(request) if action == "generate" or request.generation_count is not None else None
        if explicit_count is not None and not 1 <= explicit_count <= MAX_ENTRIES:
            return _clarify(snapshot, request, "How many drafts should I generate? Choose between one and five per turn.")
        updates = plan.preferences.model_dump(exclude_none=True)
        if explicit_count is not None:
            updates["count"] = explicit_count
        try:
            snapshot.preferences = GenerationPreferences.model_validate({**snapshot.preferences.model_dump(), **updates})
        except ValidationError:
            return _clarify(snapshot, request, "Please provide a consistent minimum and maximum body length in characters.")
        if plan.stable_instruction is not None:
            if plan.stable_instruction not in request.instruction:
                raise GenerationRejected("generation_failed", "A standing instruction was not grounded in the current user message.")
            if plan.stable_instruction not in snapshot.stable_instructions:
                snapshot.stable_instructions.append(plan.stable_instruction)
        if action == "clarification":
            return _clarify(snapshot, request, plan.question or "Should I save these preferences, generate new drafts, or revise an existing entry?")
        if action == "message":
            message = "Saved the supplied references and writing preferences for this conversation."
            if snapshot.references.urls:
                message += " Links without permitted imported text remain link-only references."
            return _finish(snapshot, request, MessageResponse(kind="message", conversation_id=snapshot.conversation_id, message_id=uuid4(), assistant_message=message, entries=[]))
        previous_pending = snapshot.pending if snapshot.pending and snapshot.pending.action == action else None
        length_change = plan.length_change
        if re.search(r"\b(?:shorter|shorten)\b", request.instruction, re.IGNORECASE):
            length_change = "shorter"
        elif re.search(r"\b(?:longer|lengthen|expand)\b", request.instruction, re.IGNORECASE):
            length_change = "longer"
        elif length_change == "keep" and previous_pending:
            length_change = previous_pending.length_change
        pending_instruction = previous_pending.instruction if previous_pending else request.instruction
        targets = _resolve_targets(request, snapshot) if action == "revise" else []
        revision_changes = GenerationPreferences.model_validate({**(previous_pending.preference_changes.model_dump() if previous_pending else {}), **updates})
        snapshot.pending = PendingTurn(action=action, instruction=pending_instruction, targets=targets or [], length_change=length_change, preference_changes=revision_changes)
        if action == "revise" and not targets:
            question = f"Which entry should I revise? Specify an entry number from 1 to {len(snapshot.active_entries)} or select its ID." if snapshot.active_entries else "There are no active drafts. Would you like to generate some first?"
            return _clarify(snapshot, request, question)
        required = ["topic", "tone", "min_body_chars", "max_body_chars"]
        required += ["count", "category", "language"] if action == "generate" else []
        missing = [field for field in required if getattr(snapshot.preferences, field) is None]
        if missing:
            labels = {"min_body_chars": "minimum body length in characters", "max_body_chars": "maximum body length in characters"}
            return _clarify(snapshot, request, "Please specify " + ", ".join(labels.get(field, field) for field in missing) + ".")
        _, sources = summarize_references(snapshot.references)
        if not sources:
            return _clarify(snapshot, request, "Please provide an authorized non-confidential sample or headline. Saved URLs alone are not usable source text.")
        context = encode_drafting_context(snapshot, request.instruction)
        if action == "generate":
            return await self._generate(request, snapshot, context)
        return await self._revise(request, snapshot, targets, revision_changes, length_change, context)

    async def _generate(self, request: ChatTurnRequest, snapshot: ConversationSnapshot, context: str) -> ConversationTurnResult:
        preferences = snapshot.preferences
        if preferences.count * preferences.max_body_chars > MAX_GENERATION_BODY_CHARS:
            return _clarify(snapshot, request, "Reduce the count or maximum body length so the batch is at most 12000 characters.")
        if len(snapshot.active_entries) + preferences.count > MAX_ACTIVE_ENTRIES:
            return _clarify(snapshot, request, "This snapshot can hold at most 20 active drafts. Start a new conversation for additional entries.")
        if sum(len(entry.body) for entry in snapshot.active_entries) + preferences.count * preferences.max_body_chars > MAX_ACTIVE_BODY_CHARS:
            return _clarify(snapshot, request, "The active draft budget is full. Request shorter drafts or start a new conversation.")
        generation_request = _generation_request(snapshot, count=preferences.count)
        response = await self._generation.generate(generation_request, snapshot.references, context=context, existing_entries=snapshot.active_entries)
        if isinstance(response, ErrorResponse):
            raise GenerationRejected(response.error.code, response.error.message)
        snapshot.active_entries.extend(response.entries)
        snapshot.pending = None
        return _finish(snapshot, request, response)

    async def _revise(self, request: ChatTurnRequest, snapshot: ConversationSnapshot, targets: list[RevisionTarget], changes: GenerationPreferences, length_change: str, context: str) -> ConversationTurnResult:
        by_id = {entry.id: entry for entry in snapshot.active_entries}
        selected = [by_id[target.entry_id] for target in targets]
        if any((length_change == "shorter" and len(entry.body) <= 1) or (length_change == "longer" and len(entry.body) >= MAX_ENTRY_BODY_CHARS) for entry in selected):
            return _clarify(snapshot, request, "A selected draft cannot change length further within the supported character limits.")
        if length_change == "shorter" and changes.min_body_chars is not None and any(changes.min_body_chars >= len(entry.body) for entry in selected):
            return _clarify(snapshot, request, "The requested minimum body length conflicts with shortening a selected draft. Please choose a smaller minimum.")
        if length_change == "longer" and changes.max_body_chars is not None and any(changes.max_body_chars <= len(entry.body) for entry in selected):
            return _clarify(snapshot, request, "The requested maximum body length conflicts with expanding a selected draft. Please choose a larger maximum.")
        requests = [_generation_request(snapshot, count=1, target=entry, changes=changes, length_change=length_change) for entry in selected]
        if sum(item.max_body_chars for item in requests) > MAX_GENERATION_BODY_CHARS:
            return _clarify(snapshot, request, "Select fewer entries or a shorter body limit; revised bodies are limited to 12000 characters per turn.")
        selected_ids = {entry.id for entry in selected}
        others = [entry for entry in snapshot.active_entries if entry.id not in selected_ids]
        if sum(len(entry.body) for entry in others) + sum(item.max_body_chars for item in requests) > MAX_ACTIVE_BODY_CHARS:
            return _clarify(snapshot, request, "The revised drafts would exceed the active draft budget. Request a smaller maximum body length.")
        revised = await self._revision.revise(requests, selected, snapshot.references, context=context, other_entries=others)
        replacements = {entry.id: entry for entry in revised}
        snapshot.active_entries = [replacements.get(entry.id, entry) for entry in snapshot.active_entries]
        snapshot.pending = None
        response = RevisionResponse(kind="revision", conversation_id=snapshot.conversation_id, message_id=uuid4(), assistant_message=f"Revised {len(revised)} selected fictional {'draft' if len(revised) == 1 else 'drafts'}. Other entries are unchanged.", entries=revised)
        return _finish(snapshot, request, response)

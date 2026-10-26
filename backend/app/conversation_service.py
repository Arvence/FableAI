import re
from uuid import UUID, uuid4

from pydantic import ValidationError

from app.contracts import MAX_ACTIVE_BODY_CHARS, MAX_ACTIVE_ENTRIES, MAX_CONTEXT_CHARS, MAX_CONTEXT_MESSAGES, MAX_ENTRIES, MAX_ENTRY_BODY_CHARS, MAX_GENERATION_BODY_CHARS, MAX_HEADLINE_CHARS, ChatMessage, ChatTurnRequest, ClarificationResponse, ErrorInfo, ErrorResponse, GeneratedEntry, GenerationRequest, ReferenceCollection, MessageResponse, RevisionResponse, RevisionTarget
from app.conversation_context import build_turn_prompt, encode_drafting_context
from app.conversation_contracts import DEFAULT_GENERATION_PREFERENCES, ConversationResponse, ConversationSnapshot, ConversationTurnResult, GenerationPreferences, PendingTurn, TurnPlan
from app.gemini_provider import GeminiProvider, GeminiProviderError
from app.generation_checks import UNSUPPORTED_TOPIC, GenerationRejected
from app.reference_management import ReferenceInputs, ReferenceLink, prepare_references
from app.revision_generation import RevisionGenerationService
from app.synthetic_generation import SyntheticGenerationService
from app.turn_context import current_subject, generation_fallback, has_explicit_body_length, refers_to_context, requested_category, scoped_snapshot


NUMBER_WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20}
ORDINAL_WORDS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14, "fifteenth": 15,
    "sixteenth": 16, "seventeenth": 17, "eighteenth": 18, "nineteenth": 19, "twentieth": 20,
}
UUID_PATTERN = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE)


def _explicit_count(request: ChatTurnRequest, topic: str | None) -> int | None:
    if request.generation_count is not None:
        return request.generation_count
    number_pattern = r"-?\d+|" + "|".join(NUMBER_WORDS)
    match = re.search(rf"\b(?:generate|create|write|produce|make|give me|i want|i would like|i'd like)\s+({number_pattern})\b", request.instruction, re.IGNORECASE)
    if match is None:
        match = re.match(rf"\s*({number_pattern})\s+(?:(?:new|fictional|news|announcement)\s+)*(?:drafts?|entries|articles?|stories|announcements?)\b", request.instruction, re.IGNORECASE)
    if match is None:
        return None
    value = match[1].lower()
    count = NUMBER_WORDS[value] if value in NUMBER_WORDS else int(value)
    counted_noun = re.match(r"\s+(?:(?:new|fictional|short|long|formal|neutral|informative|playful|news)\s+)*(?:drafts?|entries|articles?|stories|announcements?)\b", request.instruction[match.end(1):], re.IGNORECASE)
    if counted_noun is None and topic and re.search(rf"\b(?:{re.escape(value)}|{count})\b", topic, re.IGNORECASE):
        return None
    return count


def _new_draft_topic(instruction: str) -> str | None:
    match = re.match(r"^\s*(?:please\s+)?(?:create|generate|write)(?:\s+me)?\s+(.+)", instruction, re.IGNORECASE | re.DOTALL)
    if match is None:
        return None
    subject = match[1].strip()
    generic = {"a", "an", "the", "some", "new", "fictional", "draft", "drafts", "entry", "entries", "content", "news", "announcement", "announcements", "article", "articles", "story", "stories", "something", "more", "please", *NUMBER_WORDS}
    opening = re.split(r"[.!?]\s+", subject, maxsplit=1)[0]
    if not any(word.casefold() not in generic for word in re.findall(r"[^\W\d_]+", opening)):
        return None
    return subject[:MAX_HEADLINE_CHARS]


def _settings_clarification(plan: TurnPlan) -> bool:
    if plan.action != "clarification":
        return False
    if plan.clarification_reason == "missing_settings":
        return True
    if plan.clarification_reason in {"missing_topic", "revision_target"}:
        return False
    return bool(re.search(r"\b(?:how many (?:drafts|entries)|(?:choose|select|specify|provide)\b[^?.]*\b(?:drafts per turn|draft count|number of drafts))", plan.question or "", re.IGNORECASE))


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
    elif entries and re.search(r"\b(?:previous|last|latest)\s+(?:article|draft|entry|story)\b", request.instruction, re.IGNORECASE):
        targets = [RevisionTarget(entry_id=entries[-1].id, expected_version=entries[-1].version)]
    elif snapshot.pending and snapshot.pending.action == "revise" and snapshot.pending.targets:
        targets = snapshot.pending.targets
    elif len(entries) == 1:
        targets = [RevisionTarget(entry_id=entries[0].id, expected_version=entries[0].version)]
    else:
        return None
    if len(targets) > MAX_ENTRIES:
        raise GenerationRejected("invalid_request", f"Revise at most {MAX_ENTRIES} entries in one turn.")
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
            result = await self._handle_validated_turn(request, working)
            if isinstance(result.response, ErrorResponse):
                return ConversationTurnResult(response=result.response, snapshot=original)
            return result
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
        fresh_topic = _new_draft_topic(request.instruction)
        use_history = refers_to_context(request, snapshot, fresh_topic)
        interpretation = scoped_snapshot(request, snapshot, use_history)
        interpretation_request = request
        if request.generation_settings is not None:
            interpretation_request = request.model_copy(update={"generation_settings": request.generation_settings.model_copy(update={"body_length_mode": None})})
        result = await self._provider.generate(build_turn_prompt(interpretation_request, interpretation), TurnPlan)
        if not isinstance(result, TurnPlan):
            raise GenerationRejected("generation_failed", "The provider did not return a validated turn interpretation.")
        plan = TurnPlan.model_validate(result.model_dump())
        action = "clarification" if plan.action == "clarification" else "revise" if request.revision_targets else "generate" if request.generation_count is not None else plan.action
        explicit_count = _explicit_count(request, plan.preferences.topic) if action in {"generate", "clarification"} or request.generation_count is not None else None
        recover_settings = _settings_clarification(plan) and not request.revision_targets and (fresh_topic is not None or plan.preferences.topic is not None)
        if recover_settings or generation_fallback(request, plan, current_subject(request, fresh_topic)):
            action = "generate"
        selected = request.generation_settings.model_dump(exclude_none=True, exclude={"body_length_mode"}) if request.generation_settings is not None and action == "generate" else {}
        if recover_settings:
            selected = {**DEFAULT_GENERATION_PREFERENCES.model_dump(exclude_none=True), **selected}
        explicit = plan.preferences.model_dump(exclude_none=True)
        if action == "generate" and selected and not has_explicit_body_length(request.instruction):
            for field in ("min_body_chars", "max_body_chars"):
                if field in selected:
                    explicit.pop(field, None)
        if action == "generate":
            category = requested_category(request.instruction)
            if category is not None:
                explicit["category"] = category
            elif "category" in selected:
                explicit.pop("category", None)
        if action == "generate" and not use_history:
            explicit["topic"] = current_subject(request, fresh_topic) or plan.preferences.topic
        elif recover_settings and fresh_topic is not None:
            explicit["topic"] = fresh_topic
        if action == "generate" and not use_history:
            snapshot.preferences = GenerationPreferences()
            snapshot.pending = None
        elif action == "generate":
            pending_count = snapshot.pending.preference_changes.count if snapshot.pending and snapshot.pending.action == "generate" else None
            snapshot.preferences.count = pending_count or DEFAULT_GENERATION_PREFERENCES.count
        answering_generation = use_history and snapshot.pending is not None and snapshot.pending.action == "generate"
        if action == "generate" and ("count" in selected or not answering_generation) and explicit_count is None:
            explicit.pop("count", None)
        updates = {**selected, **explicit}
        if "min_body_chars" in explicit and "max_body_chars" not in explicit and "max_body_chars" in selected:
            updates["max_body_chars"] = max(explicit["min_body_chars"], selected["max_body_chars"])
        if "max_body_chars" in explicit and "min_body_chars" not in explicit and "min_body_chars" in selected:
            updates["min_body_chars"] = min(explicit["max_body_chars"], selected["min_body_chars"])
        if "min_body_chars" in updates and "max_body_chars" not in updates and selected:
            updates["max_body_chars"] = max(updates["min_body_chars"], snapshot.preferences.max_body_chars or DEFAULT_GENERATION_PREFERENCES.max_body_chars)
        if "max_body_chars" in updates and "min_body_chars" not in updates and selected:
            updates["min_body_chars"] = min(updates["max_body_chars"], snapshot.preferences.min_body_chars or DEFAULT_GENERATION_PREFERENCES.min_body_chars)
        if explicit_count is not None:
            updates["count"] = explicit_count
        requested_count = updates.get("count")
        invalid_count = requested_count is not None and not 1 <= requested_count <= MAX_ENTRIES
        if invalid_count:
            updates.pop("count")
        try:
            snapshot.preferences = GenerationPreferences.model_validate({**snapshot.preferences.model_dump(), **updates})
        except ValidationError:
            return _clarify(snapshot, request, "Please provide a consistent minimum and maximum body length in characters.")
        if invalid_count:
            if action == "generate":
                snapshot.pending = PendingTurn(action="generate", instruction=request.instruction, preference_changes=GenerationPreferences.model_validate(updates))
            return _clarify(snapshot, request, f"How many drafts should I generate? Choose between 1 and {MAX_ENTRIES} per turn.")
        if plan.stable_instruction is not None and plan.stable_instruction not in snapshot.stable_instructions:
            if plan.stable_instruction in request.instruction:
                snapshot.stable_instructions.append(plan.stable_instruction)
            elif action == "message":
                return _clarify(snapshot, request, "I couldn't save a new standing writing instruction. Please send the exact wording you want me to remember.")
        if action == "clarification":
            return _clarify(snapshot, request, plan.question or "Should I save these preferences, generate new drafts, or revise an existing entry?")
        if action == "message":
            message = "Saved the supplied references and writing preferences for this conversation."
            if snapshot.references.urls:
                message += " Links without permitted imported text remain link-only references."
            return _finish(snapshot, request, MessageResponse(kind="message", conversation_id=snapshot.conversation_id, message_id=uuid4(), assistant_message=message, entries=[]))
        values = snapshot.preferences.model_dump(exclude_none=True)
        defaults = DEFAULT_GENERATION_PREFERENCES.model_dump(exclude_none=True)
        defaults["min_body_chars"] = min(defaults["min_body_chars"], values.get("max_body_chars", defaults["max_body_chars"]))
        defaults["max_body_chars"] = max(defaults["max_body_chars"], values.get("min_body_chars", defaults["min_body_chars"]))
        snapshot.preferences = GenerationPreferences.model_validate({**defaults, **values})
        previous_pending = snapshot.pending if snapshot.pending and snapshot.pending.action == action and not (action == "generate" and fresh_topic is not None) else None
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
        if snapshot.preferences.topic is None:
            return _clarify(snapshot, request, "What would you like the fictional drafts to be about?")
        context_snapshot = scoped_snapshot(request, snapshot, use_history or action == "revise")
        context_snapshot.preferences = snapshot.preferences.model_copy(deep=True)
        context_snapshot.pending = snapshot.pending.model_copy(deep=True) if snapshot.pending else None
        context = encode_drafting_context(context_snapshot, request.instruction)
        if action == "generate":
            return await self._generate(request, snapshot, context, context_snapshot.references)
        return await self._revise(request, snapshot, targets, revision_changes, length_change, context)

    async def _generate(self, request: ChatTurnRequest, snapshot: ConversationSnapshot, context: str, references: ReferenceCollection) -> ConversationTurnResult:
        preferences = snapshot.preferences
        if preferences.count * preferences.max_body_chars > MAX_GENERATION_BODY_CHARS:
            return _clarify(snapshot, request, "Reduce the count or maximum body length so the batch is at most 12000 characters.")
        if len(snapshot.active_entries) + preferences.count > MAX_ACTIVE_ENTRIES:
            return _clarify(snapshot, request, "This snapshot can hold at most 20 active drafts. Start a new conversation for additional entries.")
        if sum(len(entry.body) for entry in snapshot.active_entries) + preferences.count * preferences.max_body_chars > MAX_ACTIVE_BODY_CHARS:
            return _clarify(snapshot, request, "The active draft budget is full. Request shorter drafts or start a new conversation.")
        generation_request = _generation_request(snapshot, count=preferences.count)
        strict_body_length = not (
            request.generation_settings is not None
            and request.generation_settings.body_length_mode == "target"
            and not has_explicit_body_length(snapshot.pending.instruction if snapshot.pending else request.instruction)
            and not has_explicit_body_length(request.instruction)
        )
        response = await self._generation.generate(generation_request, references, context=context, existing_entries=snapshot.active_entries, strict_body_length=strict_body_length)
        if isinstance(response, ErrorResponse):
            return ConversationTurnResult(response=response, snapshot=None)
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

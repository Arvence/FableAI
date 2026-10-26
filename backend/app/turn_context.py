import re

from app.contracts import ChatTurnRequest, ContentCategory, MAX_HEADLINE_CHARS, ReferenceCollection
from app.conversation_contracts import ConversationSnapshot, GenerationPreferences, TurnPlan
from app.reference_management import ReferenceInputs, ReferenceLink, prepare_references


CONTEXT_REFERENCE = re.compile(
    r"\b(?:previous|earlier|last|latest|same|selected)\s+(?:article|draft|entry|story|one|topic|response|request|preferences?|settings?)\b"
    r"|\b(?:another(?:\s+one)?|again)\b|\b(?:generate|create|write|make|give)\b.{0,30}\bmore\b"
    r"|\b(?:revise|rewrite|shorten|lengthen|expand|translate)\b|\b(?:make|keep)\s+(?:it|that|them|these)\b"
    r"|\b(?:entry|draft|article)\s+(?:#?\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b"
    r"|\b(?:use|follow|keep)\s+(?:(?:my|the|these|those|saved|previous|provided|supplied|uploaded|attached)\s+)+(?:references?|examples?|preferences?|instructions?|settings?)\b",
    re.IGNORECASE,
)
ACKNOWLEDGEMENT = re.compile(r"^\s*(?:(?:please\s+)?(?:save|remember|keep|use|set|prefer|store)\b|(?:hi|hello|hey|thanks|thank you)[.!\s]*$)", re.IGNORECASE)
VERIFICATION_QUESTION = re.compile(r"\b(?:verif(?:y|ied|ication)|confirm(?:ed|ation)?|official|news feed|sources?|internet|research|real[- ]time|up[- ]to[- ]date)\b", re.IGNORECASE)


def generation_fallback(request: ChatTurnRequest, plan: TurnPlan, subject: str | None) -> bool:
    if subject is None or request.revision_targets or CONTEXT_REFERENCE.search(request.instruction) or ACKNOWLEDGEMENT.search(request.instruction):
        return False
    if plan.action == "message":
        return True
    if plan.action != "clarification" or plan.clarification_reason in {"missing_topic", "revision_target"}:
        return False
    question = plan.question or ""
    category = requested_category(request.instruction) or (request.generation_settings.category if request.generation_settings else None)
    unsupported_type = category is not None and plan.clarification_reason == "unsupported_requirement" and re.search(r"\b(?:categor(?:y|ies)|content types?|news and announcement)\b", question, re.IGNORECASE)
    return bool(VERIFICATION_QUESTION.search(question) or unsupported_type)


def requested_category(instruction: str) -> ContentCategory | None:
    match = re.search(r"\b(news|announcements?|announce[sd]?|updates?|articles?|blogs?)\b", instruction, re.IGNORECASE)
    if match is None:
        return None
    word = match[1].lower()
    if word.startswith("announc"):
        return "announcement"
    return word if word == "news" else word.removesuffix("s")


def refers_to_context(request: ChatTurnRequest, snapshot: ConversationSnapshot, subject: str | None) -> bool:
    if request.revision_targets or CONTEXT_REFERENCE.search(request.instruction):
        return True
    text = request.instruction.strip().rstrip(".!?").casefold()
    if snapshot.pending:
        if snapshot.pending.action == "generate" and snapshot.preferences.topic is None:
            return True
        if re.fullmatch(r"\d+|zero|one|two|three|four|five|six|seven|eight|nine|ten|neutral|formal|informative|playful|news|announcement|english|turkish|french|german|spanish|yes|no", text):
            return True
    return subject is None and bool(re.match(r"^(?:please\s+)?(?:create|generate|write)\b", text)) and snapshot.preferences.topic is not None


def current_subject(request: ChatTurnRequest, subject: str | None) -> str | None:
    if re.match(r"^\s*(?:please\s+)?(?:create|generate|write)\b", request.instruction, re.IGNORECASE):
        return subject
    return request.instruction.strip()[:MAX_HEADLINE_CHARS] if re.search(r"[^\W\d_]", request.instruction) else None


def scoped_snapshot(request: ChatTurnRequest, snapshot: ConversationSnapshot, use_history: bool) -> ConversationSnapshot:
    if use_history:
        return snapshot.model_copy(deep=True)
    inputs = ReferenceInputs(examples=request.examples, headlines=request.headlines, urls=[ReferenceLink(url=url) for url in request.source_urls])
    return ConversationSnapshot(conversation_id=snapshot.conversation_id, references=prepare_references(ReferenceCollection(), inputs), preferences=GenerationPreferences())

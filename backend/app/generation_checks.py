import re
import unicodedata

from app.contracts import MAX_ACTIVE_BODY_CHARS, MAX_ACTIVE_ENTRIES, DraftBatch, ErrorCode, ErrorDetail, ErrorInfo, GeneratedEntry, GenerationRequest


FICTIONAL_PREFIX = "[FICTIONAL] "
UNSUPPORTED_TOPIC = re.compile(
    r"\b(?:copy|reproduce|repeat|quote)\b.{0,60}\b(?:verbatim|exactly|word[ -]for[ -]word)\b"
    r"|\b(?:verbatim|word[ -]for[ -]word|fact[ -]?check|verify (?:the |this )?news|summarize (?:the |this )?(?:article|source|news))\b"
    r"|\b(?:ignore|override|disregard)\b.{0,40}\b(?:instructions|rules|safety)\b"
    r"|\b(?:real[- ]person quotations?|official endorsement|verified reporting|impersonate)\b",
    re.IGNORECASE,
)
UNSUPPORTED_CLAIM = re.compile(
    r"https?://|www\.|\bdoi\s*:|\[\d+\]"
    r"|\b(?:according to|official(?:ly)?|endorsed|verified|confirmed|breaking news|sources? (?:say|said)|real[- ]world event)\b"
    r"|\b(?:said|stated|told|announced|declared)\b.{0,30}[\"\u201c\u201d\u00ab\u00bb]"
    r"|[\"\u201c\u201d\u00ab\u00bb]|(?:^|\s)[\u2018']\S.{2,}?[\u2019'](?:\s|[.!?,]|$)",
    re.IGNORECASE,
)
CASUAL_LANGUAGE = re.compile(r"\b(?:lol|omg|gonna|wanna|awesome|super cool)\b", re.IGNORECASE)


class GenerationRejected(Exception):
    def __init__(self, code: ErrorCode, message: str, field: str | None = None) -> None:
        details = [ErrorDetail(field=field, message=message)] if field else []
        self.error = ErrorInfo(code=code, message=message, details=details)
        super().__init__(message)


def validate_generation_request(request: GenerationRequest) -> None:
    if UNSUPPORTED_TOPIC.search(unicodedata.normalize("NFKC", request.topic)):
        raise GenerationRejected("invalid_request", "Request original fictional drafts without copying, verification, impersonation, or attribution instructions.", "topic")


def validate_active_entries(entries: list[GeneratedEntry]) -> list[GeneratedEntry]:
    if len(entries) > MAX_ACTIVE_ENTRIES:
        raise GenerationRejected("invalid_request", "Active drafts exceed their context budget.")
    validated = [GeneratedEntry.model_validate(entry.model_dump()) for entry in entries]
    if sum(len(entry.body) for entry in validated) > MAX_ACTIVE_BODY_CHARS:
        raise GenerationRejected("invalid_request", "Active drafts exceed their context budget.")
    if len({entry.id for entry in validated}) != len(validated):
        raise GenerationRejected("invalid_request", "Active draft IDs must be unique.")
    return validated


def normalized_words(text: str) -> tuple[str, ...]:
    if text.startswith(FICTIONAL_PREFIX):
        text = text[len(FICTIONAL_PREFIX):]
    return tuple(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", text).casefold()))


def _shingles(items: tuple[str, ...] | str, size: int) -> set:
    return {items[index:index + size] for index in range(len(items) - size + 1)}


def has_suspect_overlap(first: str, second: str) -> bool:
    first_words, second_words = normalized_words(first), normalized_words(second)
    if not first_words or not second_words:
        return False
    if first_words == second_words:
        return True
    shorter, longer = sorted((first_words, second_words), key=len)
    if len(shorter) >= 4 and " " + " ".join(shorter) + " " in " " + " ".join(longer) + " ":
        return True
    if len(shorter) >= 8 and _shingles(shorter, 8) & _shingles(longer, 8):
        return True
    first_set, second_set = set(first_words), set(second_words)
    if len(shorter) >= 4 and len(first_set & second_set) / len(first_set | second_set) >= 0.8:
        return True
    first_chars, second_chars = "".join(first_words), "".join(second_words)
    if min(len(first_chars), len(second_chars)) >= 40:
        first_grams, second_grams = _shingles(first_chars, 20), _shingles(second_chars, 20)
        if len(first_grams & second_grams) / min(len(first_grams), len(second_grams)) >= 0.8:
            return True
    return False


def validate_generated_batch(batch: DraftBatch, request: GenerationRequest, source_texts: tuple[str, ...]) -> None:
    if len(batch.entries) != request.count:
        raise GenerationRejected("generation_failed", "The generated entry count does not match the request.", "entries")
    previous_texts = []
    for index, entry in enumerate(batch.entries):
        field = f"entries.{index}"
        if not entry.title.startswith(FICTIONAL_PREFIX) or not normalized_words(entry.title):
            raise GenerationRejected("generation_failed", "Every title must begin with [FICTIONAL] and include a descriptive title.", field + ".title")
        if not request.min_body_chars <= len(entry.body) <= request.max_body_chars:
            raise GenerationRejected("generation_failed", "A generated body does not match the requested character range.", field + ".body")
        if entry.category != request.category or entry.language.casefold() != request.language.casefold():
            raise GenerationRejected("generation_failed", "Generated category or language metadata does not match the request.", field)
        text = " ".join((entry.title, entry.body, *entry.tags))
        if UNSUPPORTED_CLAIM.search(unicodedata.normalize("NFKC", text)):
            raise GenerationRejected("generation_failed", "Generated drafts must not contain quotations, citations, or unsupported verification or endorsement claims.", field)
        if (request.tone in {"neutral", "formal"} and "!" in text) or (request.tone == "formal" and CASUAL_LANGUAGE.search(text)):
            raise GenerationRejected("generation_failed", "Generated wording conflicts with the requested tone checks.", field)
        for candidate in (entry.title, entry.body):
            if any(has_suspect_overlap(candidate, source) for source in (*source_texts, *previous_texts)):
                raise GenerationRejected("generation_failed", "Generated text overlaps with reference material, demonstration text, or another draft. Request different content.", field)
        previous_texts.extend((entry.title, entry.body))

import re
import unicodedata

from app.contracts import MAX_ACTIVE_BODY_CHARS, MAX_ACTIVE_ENTRIES, MAX_GENERATION_BODY_CHARS, DraftBatch, ErrorCode, ErrorDetail, ErrorInfo, GeneratedEntry, GenerationRequest


FICTIONAL_PREFIX = "[FICTIONAL] "
UNSUPPORTED_TOPIC = re.compile(
    r"\b(?:copy|reproduce|repeat|quote)\b.{0,60}\b(?:verbatim|exactly|word[ -]for[ -]word)\b"
    r"|\b(?:verbatim|word[ -]for[ -]word|fact[ -]?check|verify (?:the |this )?news|summarize (?:the |this )?(?:article|source|news))\b"
    r"|\b(?:ignore|override|disregard)\b.{0,40}\b(?:instructions|rules|safety)\b"
    r"|\b(?:real[- ]person quotations?|official endorsement|verified reporting|impersonate)\b",
    re.IGNORECASE,
)
CITATION = re.compile(r"https?://|www\.|\bdoi\s*:|\[\d+\]", re.IGNORECASE)
UNSUPPORTED_CLAIM = re.compile(r"\b(?:according to|official(?:ly)?|endorsed|verified|confirmed|breaking news|sources? (?:say|said)|real[- ]world event)\b", re.IGNORECASE)
QUOTED_TEXT = r"""(?:"[^"\n]+"|\u201c[^\u201d\n]+\u201d|\u00ab[^\u00bb\n]+\u00bb|\u2018[^\u2019\n]+\u2019|(?<!\w)'[^'\n]+'(?!\w))"""
SPEECH_VERBS = r"\b(?:said|says|stated|states|told|announced|declared|replied|asked|wrote|commented|added|explained|remarked|noted)\b"
ATTRIBUTED_QUOTATION = re.compile(rf"{SPEECH_VERBS}[^.!?\n]{{0,30}}{QUOTED_TEXT}|{QUOTED_TEXT}[,;:\s\u2014-]+(?:\w+\s+){{0,4}}{SPEECH_VERBS}", re.IGNORECASE)
CONTENT_CHECKS = (
    (CITATION, "Generated drafts must not contain citations or source links."),
    (UNSUPPORTED_CLAIM, "Generated drafts must not contain unsupported verification or endorsement claims."),
    (ATTRIBUTED_QUOTATION, "Generated drafts must not contain attributed speech quotations."),
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


def validate_generated_batch(batch: DraftBatch, request: GenerationRequest, source_texts: tuple[str, ...], *, strict_body_length: bool = True) -> None:
    if len(batch.entries) != request.count:
        raise GenerationRejected("generation_failed", "The generated entry count does not match the request.", "entries")
    if sum(len(entry.body) for entry in batch.entries) > MAX_GENERATION_BODY_CHARS:
        raise GenerationRejected("generation_failed", "Generated bodies exceed the 12000-character batch limit.", "entries")
    previous_texts = []
    for index, entry in enumerate(batch.entries):
        field = f"entries.{index}"
        if not entry.title.startswith(FICTIONAL_PREFIX) or not normalized_words(entry.title):
            raise GenerationRejected("generation_failed", "Every title must begin with [FICTIONAL] and include a descriptive title.", field + ".title")
        if strict_body_length and not request.min_body_chars <= len(entry.body) <= request.max_body_chars:
            raise GenerationRejected("generation_failed", f"A generated body has {len(entry.body)} characters, outside the requested character range of {request.min_body_chars}-{request.max_body_chars}.", field + ".body")
        if entry.category != request.category or entry.language.casefold() != request.language.casefold():
            raise GenerationRejected("generation_failed", "Generated category or language metadata does not match the request.", field)
        text = " ".join((entry.title, entry.body, *entry.tags))
        content_fields = [("title", entry.title), ("body", entry.body), *((f"tags.{tag_index}", tag) for tag_index, tag in enumerate(entry.tags))]
        for name, value in content_fields:
            normalized = unicodedata.normalize("NFKC", value)
            for pattern, message in CONTENT_CHECKS:
                if pattern.search(normalized):
                    raise GenerationRejected("generation_failed", message, field + "." + name)
        if (request.tone in {"neutral", "formal"} and "!" in text) or (request.tone == "formal" and CASUAL_LANGUAGE.search(text)):
            raise GenerationRejected("generation_failed", "Generated wording conflicts with the requested tone checks.", field)
        for candidate in (entry.title, entry.body):
            if any(has_suspect_overlap(candidate, source) for source in (*source_texts, *previous_texts)):
                raise GenerationRejected("generation_failed", "Generated text overlaps with reference material, demonstration text, or another draft. Request different content.", field)
        previous_texts.extend((entry.title, entry.body))

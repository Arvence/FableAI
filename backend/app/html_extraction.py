from html.parser import HTMLParser

from app.contracts import MAX_REFERENCE_CHARS
from app.reference_management import normalize_reference_text


VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
IGNORED_TAGS = {"script", "style", "head", "nav", "header", "footer", "aside", "form", "button", "iframe", "noscript", "svg", "template", "canvas", "object"}


class ReadableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool]] = []
        self.parts: dict[str, list[str]] = {"article": [], "main": [], "body": []}
        self.element_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.element_count += 1
        if self.element_count > 5_000 or len(self.stack) >= 64:
            raise ValueError("HTML structure exceeds extraction limits.")
        attributes = dict(attrs)
        style = "".join((attributes.get("style") or "").lower().split())
        hidden = bool(self.stack and self.stack[-1][1]) or tag in IGNORED_TAGS
        hidden = hidden or "hidden" in attributes or (attributes.get("aria-hidden") or "").lower() == "true"
        hidden = hidden or "display:none" in style or "visibility:hidden" in style
        if tag not in VOID_TAGS:
            self.stack.append((tag, hidden))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID_TAGS or not self.stack or self.stack[-1][0] != tag:
            raise ValueError("Malformed HTML structure.")
        self.stack.pop()

    def handle_data(self, data: str) -> None:
        if not self.stack or self.stack[-1][1]:
            return
        active_tags = {tag for tag, _ in self.stack}
        for tag in self.parts:
            if tag in active_tags:
                self.parts[tag].append(data)

    def unknown_decl(self, data: str) -> None:
        raise ValueError("Unsupported HTML declaration.")


def extract_readable_html(html: str) -> str:
    if "\x00" in html:
        raise ValueError("Invalid HTML text.")
    parser = ReadableHTMLParser()
    parser.feed(html)
    parser.close()
    if parser.stack:
        raise ValueError("Incomplete HTML structure.")
    for parts in parser.parts.values():
        text = normalize_reference_text(" ".join(parts))
        if text:
            if len(text) > MAX_REFERENCE_CHARS:
                raise ValueError("Extracted text exceeds the reference limit.")
            return text
    raise ValueError("No readable HTML source text.")

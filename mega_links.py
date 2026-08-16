"""Extract and classify public MEGA links. No network."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from urllib.parse import unquote


URL_FIND_RE = re.compile(
    r"(?:https?://)?(?:www\.)?mega(?:\.co)?\.nz/"
    r"(?:folder/|file/|#F!|#!)[^\s<>\"']+",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedLink:
    url: str
    kind: str  # "file" or "folder"
    handle: str
    key: str | None
    source: str = ""

    @property
    def has_key(self) -> bool:
        return bool(self.key)

    @property
    def display_url(self) -> str:
        if self.source:
            return self.source
        return self.url


def _clean_text(text: str) -> str:
    text = unescape((text or "").replace("&amp;", "&"))
    text = unquote(text)
    hrefs = re.findall(r"""href\s*=\s*["']([^"']+)["']""", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    hrefs = [re.sub(r"<[^>]+>", "", href) for href in hrefs]
    if hrefs:
        text = text + "\n" + "\n".join(hrefs)
    for ch in ("\u200b", "\u200c", "\u200d", "\ufeff", "\u00ad", "\xa0"):
        text = text.replace(ch, "")
    return _join_broken_mega_urls(text)


def _looks_like_id_fragment(token: str) -> bool:
    if not token or token.lower().startswith("http"):
        return False
    if len(token) >= 8:
        return True
    if any(ch.isdigit() or ch in "-_+" for ch in token):
        return True
    letters = [ch for ch in token if ch.isalpha()]
    if letters and any(ch.isupper() for ch in letters) and any(ch.islower() for ch in letters):
        return True
    return False


def _should_join_url_prefix(prefix: str, rest: str) -> bool:
    if not _looks_like_id_fragment(rest) and not re.fullmatch(r"[A-Za-z0-9_+=,.-]{2,}", rest or ""):
        return False
    lower = prefix.lower()
    if lower.endswith(("/folder/", "/file/", "#", "!", "/")):
        return True
    handle = re.search(r"/(?:folder|file)/([A-Za-z0-9_+=,.-]+)$", prefix, re.IGNORECASE)
    if handle and len(handle.group(1)) < 8 and _looks_like_id_fragment(rest):
        return True
    if "#" in prefix and _looks_like_id_fragment(rest):
        return True
    return False


def _join_broken_mega_urls(text: str) -> str:
    """Rejoin MEGA URLs that web pages wrap across lines."""

    def repl(match: re.Match) -> str:
        prefix, rest = match.group(1), match.group(2)
        if _should_join_url_prefix(prefix, rest):
            return prefix + rest
        return match.group(0)

    prev = None
    while prev != text:
        prev = text
        text = re.sub(
            r"(mega(?:\.co)?\.nz/[^\s]{0,200})\n+[ \t]*(?!https?://)([A-Za-z0-9_#!+=,.-]+)",
            repl,
            text,
            flags=re.IGNORECASE,
        )
    return text


def _clean_id(value: str | None) -> str:
    value = unescape(unquote((value or "").strip()))
    value = value.split("/")[0].split("?")[0]
    return value.rstrip(".,);]>\"'")


def _canonical(kind: str, handle: str, key: str | None, source: str = "") -> ParsedLink | None:
    handle = _clean_id(handle)
    key = _clean_id(key) or None
    if not handle:
        return None
    if kind == "folder":
        url = f"https://mega.nz/folder/{handle}"
        shown = f"https://mega.nz/#F!{handle}!{key}" if key and "#F!" in (source or "").upper() else ""
    else:
        url = f"https://mega.nz/file/{handle}"
        shown = f"https://mega.nz/#!{handle}!{key}" if key and "#!" in (source or "") and "#F!" not in (source or "").upper() else ""
    if key:
        url = f"{url}#{key}"
    return ParsedLink(url=url, kind=kind, handle=handle, key=key, source=shown or source or url)


def parse_mega_url(url: str) -> ParsedLink | None:
    """Parse one MEGA URL the same way mega_size.py does."""
    if not url:
        return None
    text = _clean_text(url).strip()

    old_folder = re.search(r"#F!([^!]+)!([^!]*)", text, re.IGNORECASE)
    if old_folder:
        return _canonical("folder", old_folder.group(1), old_folder.group(2), source=text)

    old_file = re.search(r"#!([^!]+)!([^!]*)", text)
    if old_file:
        return _canonical("file", old_file.group(1), old_file.group(2), source=text)

    if "/folder/" in text.lower():
        match = re.search(r"/folder/([^#]+)#?([^#]*)", text, re.IGNORECASE)
        if match:
            return _canonical("folder", match.group(1), match.group(2), source=text)

    if "/file/" in text.lower():
        match = re.search(r"/file/([^#]+)#?([^#]*)", text, re.IGNORECASE)
        if match:
            return _canonical("file", match.group(1), match.group(2), source=text)

    return None


def extract_mega_links(text: str) -> list[ParsedLink]:
    """Find MEGA links in copied page text, HTML, or a pasted list."""
    if not text:
        return []
    cleaned = _clean_text(text)
    found: list[ParsedLink] = []
    seen: dict[tuple[str, str], ParsedLink] = {}

    for match in URL_FIND_RE.finditer(cleaned):
        parsed = parse_mega_url(match.group(0))
        if not parsed:
            continue
        key = (parsed.kind, parsed.handle)
        prev = seen.get(key)
        if prev is None or (parsed.has_key and not prev.has_key):
            seen[key] = parsed

    for match in URL_FIND_RE.finditer(cleaned):
        parsed = parse_mega_url(match.group(0))
        if not parsed:
            continue
        key = (parsed.kind, parsed.handle)
        chosen = seen.pop(key, None)
        if chosen:
            found.append(chosen)
    return found


def extract_from_file(path: str) -> list[ParsedLink]:
    with open(path, "r", encoding="utf-8", errors="ignore") as handle:
        return extract_mega_links(handle.read())

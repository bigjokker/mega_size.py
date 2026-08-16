"""Inspect public MEGA links. Network is used only when inspect_url() is called."""

from __future__ import annotations

import json
import random
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

import requests

from mega_crypto import (
    HAS_CRYPTO,
    MEGA_ERROR_CODES,
    MegaAPIError,
    QUOTA_CODES,
    RATE_LIMIT_CODES,
    base64urldecode,
    categorize_ext,
    decrypt_attr,
    decrypt_key,
    ext_of,
    file_aes_parts,
    parse_date_ymd_end_inclusive,
    parse_date_ymd_start,
    parse_size,
    str_to_a32,
)
from mega_links import ParsedLink, parse_mega_url
from urllib.parse import quote

API_URL = "https://g.api.mega.co.nz/cs"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
)


@dataclass
class MegaNode:
    handle: str
    parent: str | None
    name: str
    is_folder: bool
    size: int
    timestamp: int | None
    file_key_a32: tuple | None = None
    children: list["MegaNode"] = field(default_factory=list)
    path: str = ""
    rollup_size: int = 0
    names_decrypted: bool = False

    @property
    def category(self) -> str:
        if self.is_folder:
            return "folder"
        return categorize_ext(ext_of(self.name))


@dataclass
class InspectResult:
    link: ParsedLink
    is_folder: bool
    total_size: int
    names_decrypted: bool
    warning: str | None
    roots: list[MegaNode]
    files: list[MegaNode]
    folder_count: int
    file_count: int
    folder_handle: str | None


@dataclass
class Filters:
    extensions: list[str] | None = None
    min_size: str | None = None
    since: str | None = None
    until: str | None = None
    search: str = ""
    categories: set[str] | None = None
    folders_only: bool = False
    sort_key: str = "size"
    sort_desc: bool = True


def _decode_master_key(link: ParsedLink) -> tuple[bytes | None, str | None]:
    if not link.key:
        return None, "This link has no key — sizes work, names stay hidden, download stays off."
    if not HAS_CRYPTO:
        return None, "pycryptodome is missing — names stay hidden and download stays off."
    try:
        master_key = base64urldecode(link.key)
    except Exception:
        return None, "The key in this URL is not valid — download stays off."
    expected = 16 if link.kind == "folder" else 32
    if len(master_key) != expected:
        return None, "The key length is wrong — download stays off."
    return master_key, None


def _api_post(
    session: requests.Session,
    payload,
    *,
    public_handle: str | None = None,
    retries=3,
    backoff=1,
    timeout=30,
):
    """Same public request mega_size.py uses. Folder listings must pass n=handle."""
    last_error = None
    for attempt in range(retries):
        try:
            seq = random.randint(1, 0x7FFFFFFF)
            if public_handle:
                url = f"{API_URL}?id={seq}&n={quote(public_handle, safe='-_')}"
            else:
                url = f"{API_URL}?id={seq}"
            response = session.post(
                url,
                data=json.dumps(payload),
                headers={"User-Agent": USER_AGENT},
                timeout=timeout,
            )
            response.raise_for_status()
            body = response.json()
            if isinstance(body, int) and body < 0:
                msg = MEGA_ERROR_CODES.get(body, f"Unknown MEGA API error: {body}")
                raise MegaAPIError(body, msg)
            obj = body[0] if isinstance(body, list) else body
            if isinstance(obj, int) and obj < 0:
                msg = MEGA_ERROR_CODES.get(obj, f"Unknown MEGA API error: {obj}")
                raise MegaAPIError(obj, msg)
            return obj
        except MegaAPIError as exc:
            last_error = exc
            if attempt < retries - 1 and exc.code in RATE_LIMIT_CODES | {-3}:
                time.sleep(backoff * (2**attempt))
                continue
            raise
        except (requests.exceptions.RequestException, ValueError, json.JSONDecodeError) as exc:
            last_error = MegaAPIError(-3, str(exc))
            if attempt < retries - 1:
                time.sleep(backoff * (2**attempt))
                continue
            raise last_error from exc
    raise last_error or MegaAPIError(-3, "Request failed")


def _decrypt_node_name_and_key(node: dict, master_key: bytes | None) -> tuple[str, tuple | None, bool]:
    handle = node.get("h", "unknown")
    if not master_key or not HAS_CRYPTO:
        return f"{handle} (encrypted)", None, False
    try:
        if "k" not in node:
            return f"{handle} (encrypted)", None, False
        parts = node["k"].split(":", 1)
        if len(parts) != 2:
            return f"{handle} (encrypted)", None, False
        enc_key = parts[1]
        cipher_a32 = str_to_a32(base64urldecode(enc_key))
        node_key = decrypt_key(cipher_a32, str_to_a32(master_key))
        if not node_key:
            return f"{handle} (encrypted)", None, False
        if node.get("t") == 0 and len(node_key) == 8:
            k, _, _ = file_aes_parts(node_key)
            file_key = tuple(node_key)
        else:
            k = node_key
            file_key = None
        name = None
        if "a" in node:
            name = decrypt_attr(base64urldecode(node["a"]), k)
        return (name or f"{handle} (encrypted)"), file_key, bool(name)
    except Exception:
        return f"{handle} (encrypted)", None, False


def _decrypt_public_file_name(resp: dict, master_key: bytes | None) -> tuple[str, tuple | None, bool]:
    if not master_key or not HAS_CRYPTO:
        return "Encrypted file", None, False
    try:
        master_key_a32 = str_to_a32(master_key)
        if len(master_key_a32) != 8:
            return "Encrypted file", None, False
        k, _, _ = file_aes_parts(master_key_a32)
        name = None
        if "at" in resp:
            name = decrypt_attr(base64urldecode(resp["at"]), k)
        return (name or "Encrypted file"), tuple(master_key_a32), bool(name)
    except Exception:
        return "Encrypted file", None, False


def _not_found_message(link: ParsedLink) -> str:
    return (
        f"MEGA could not find this {link.kind}.\n\n"
        f"Handle sent: {link.handle}\n"
        f"{link.url}\n\n"
        "Usually the link was cut off when it was pasted, or the share was removed. "
        "Copy the address again from the browser address bar and paste it."
    )


def _friendly_api_error(link: ParsedLink, exc: MegaAPIError) -> MegaAPIError:
    if exc.code == -9:
        return MegaAPIError(-9, _not_found_message(link))
    if exc.code == -15:
        return MegaAPIError(
            -15,
            "MEGA rejected this as a public link.\n\n"
            f"Handle sent: {link.handle}\n"
            f"{link.display_url}\n\n"
            "Copy the address from the browser address bar and paste it again.",
        )
    if exc.code in QUOTA_CODES:
        return MegaAPIError(
            exc.code,
            "Quota exceeded (link is valid).\n\n"
            f"This {link.kind} link was read correctly, including old #F! / #! links.\n"
            "MEGA has used up the owner's transfer quota, so size and download "
            "are blocked until that resets.\n\n"
            f"{link.display_url}",
        )
    return MegaAPIError(exc.code, str(exc))


def _fetch_public(session: requests.Session, link: ParsedLink):
    """Public folder/file fetch only. Never list a logged-in account."""
    folder_payloads = [
        [{"a": "f", "c": 1, "ca": 1, "r": 1}],
        [{"a": "f", "c": 1, "r": 1}],
    ]
    file_payload = [{"a": "g", "p": link.handle, "ssl": 1}]

    if link.kind == "folder":
        last_error = None
        for payload in folder_payloads:
            try:
                return _api_post(session, payload, public_handle=link.handle), True
            except MegaAPIError as exc:
                last_error = exc
                if exc.code in {-9, -15}:
                    continue
                raise _friendly_api_error(link, exc) from exc
        try:
            return _api_post(session, file_payload), False
        except MegaAPIError as exc:
            raise _friendly_api_error(link, last_error or exc) from exc

    try:
        return _api_post(session, file_payload), False
    except MegaAPIError as exc:
        if exc.code not in {-9, -15}:
            raise _friendly_api_error(link, exc) from exc
        try:
            return _api_post(session, folder_payloads[0], public_handle=link.handle), True
        except MegaAPIError as retry_exc:
            raise _friendly_api_error(link, retry_exc) from retry_exc


def inspect_url(url: str | ParsedLink, session: requests.Session | None = None) -> InspectResult:
    link = url if isinstance(url, ParsedLink) else parse_mega_url(url)
    if not link:
        raise ValueError("Not a public MEGA link.")

    master_key, warning = _decode_master_key(link)
    own_session = session is None
    session = session or requests.Session()
    try:
        obj, is_folder = _fetch_public(session, link)
        if is_folder and isinstance(obj, dict) and "f" not in obj and "s" in obj:
            is_folder = False
        if is_folder:
            nodes_raw = obj.get("f", [])
            total_size = sum(n.get("s", 0) for n in nodes_raw if n.get("t") == 0)
            node_map: dict[str, MegaNode] = {}
            decrypted_any = False
            for raw in nodes_raw:
                name, file_key, ok = _decrypt_node_name_and_key(raw, master_key)
                decrypted_any = decrypted_any or ok
                node = MegaNode(
                    handle=raw["h"],
                    parent=raw.get("p"),
                    name=name,
                    is_folder=raw.get("t") == 1,
                    size=raw.get("s", 0) if raw.get("t") == 0 else 0,
                    timestamp=raw.get("ts"),
                    file_key_a32=file_key,
                    names_decrypted=ok,
                )
                node_map[node.handle] = node

            for node in node_map.values():
                parent = node_map.get(node.parent) if node.parent else None
                if parent:
                    parent.children.append(node)

            roots = [n for n in node_map.values() if n.parent not in node_map]

            def set_paths(node: MegaNode, prefix: str = ""):
                node.path = f"{prefix}/{node.name}" if prefix else node.name
                for child in node.children:
                    set_paths(child, node.path)

            for root in roots:
                set_paths(root)

            files = [n for n in node_map.values() if not n.is_folder]
            folders = [n for n in node_map.values() if n.is_folder]

            rollup = defaultdict(int)
            for file in files:
                cur = node_map.get(file.parent)
                while cur:
                    rollup[cur.handle] += file.size
                    cur = node_map.get(cur.parent) if cur.parent else None
            for node in node_map.values():
                if node.is_folder:
                    node.rollup_size = rollup.get(node.handle, 0)
                else:
                    node.rollup_size = node.size

            return InspectResult(
                link=link,
                is_folder=True,
                total_size=total_size,
                names_decrypted=bool(master_key) and decrypted_any,
                warning=warning,
                roots=roots,
                files=files,
                folder_count=len(folders),
                file_count=len(files),
                folder_handle=link.handle,
            )

        total_size = obj.get("s", 0)
        name, file_key, ok = _decrypt_public_file_name(obj, master_key)
        node = MegaNode(
            handle=link.handle,
            parent=None,
            name=name,
            is_folder=False,
            size=total_size,
            timestamp=obj.get("ts"),
            file_key_a32=file_key,
            path=name,
            rollup_size=total_size,
            names_decrypted=ok,
        )
        return InspectResult(
            link=link,
            is_folder=False,
            total_size=total_size,
            names_decrypted=ok,
            warning=warning,
            roots=[node],
            files=[node],
            folder_count=0,
            file_count=1,
            folder_handle=None,
        )
    finally:
        if own_session:
            session.close()


def apply_filters(result: InspectResult, filters: Filters | None = None) -> list[MegaNode]:
    filters = filters or Filters()
    ext_set = None
    if filters.extensions:
        ext_set = {
            e.lower() if e.startswith(".") else f".{e.lower()}"
            for e in filters.extensions
            if e.strip()
        }
    min_size = parse_size(filters.min_size) if filters.min_size else None
    since_ts = parse_date_ymd_start(filters.since) if filters.since else None
    until_ts = parse_date_ymd_end_inclusive(filters.until) if filters.until else None
    search = (filters.search or "").strip().lower()

    matched = []
    for file in result.files:
        if ext_set is not None and ext_of(file.name) not in ext_set:
            continue
        if min_size is not None and file.size < min_size:
            continue
        if since_ts is not None and (file.timestamp is None or file.timestamp < since_ts):
            continue
        if until_ts is not None and (file.timestamp is None or file.timestamp > until_ts):
            continue
        if filters.categories and file.category not in filters.categories:
            continue
        if search and search not in file.name.lower() and search not in file.path.lower():
            continue
        matched.append(file)

    def sort_val(node: MegaNode):
        if filters.sort_key == "size":
            return node.size
        if filters.sort_key == "date":
            return node.timestamp or -1
        return node.path.lower()

    matched.sort(key=sort_val, reverse=filters.sort_desc)
    return matched


def breakdown_for(files: list[MegaNode]) -> list[tuple[str, int, int]]:
    bucket: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for file in files:
        bucket[file.category][0] += 1
        bucket[file.category][1] += file.size
    rows = [(cat, data[0], data[1]) for cat, data in bucket.items()]
    rows.sort(key=lambda row: row[2], reverse=True)
    return rows


def format_timestamp(ts: int | None) -> str:
    if ts is None:
        return ""
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""

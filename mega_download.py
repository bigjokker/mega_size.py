"""Download only the files the caller passes in. Never auto-starts."""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass

import requests
from Crypto.Cipher import AES
from Crypto.Util import Counter

from mega_crypto import (
    MEGA_ERROR_CODES,
    MegaAPIError,
    a32_to_str,
    base64urldecode,
    file_aes_parts,
    get_chunks,
    sanitize_filename,
    str_to_a32,
)
from mega_core import MegaNode
from mega_links import ParsedLink

API_URL = "https://g.api.mega.co.nz/cs"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


@dataclass
class DownloadItem:
    name: str
    relative_path: str
    size: int
    file_handle: str
    file_key_a32: tuple
    folder_handle: str | None
    source_url: str


class DownloadCancelled(Exception):
    pass


def items_from_nodes(
    nodes: list[MegaNode],
    link: ParsedLink,
    folder_handle: str | None,
) -> list[DownloadItem]:
    items = []
    for node in nodes:
        if node.is_folder or not node.file_key_a32:
            continue
        rel = "/".join(sanitize_filename(part) for part in node.path.split("/") if part)
        items.append(
            DownloadItem(
                name=sanitize_filename(node.name),
                relative_path=rel,
                size=node.size,
                file_handle=node.handle,
                file_key_a32=tuple(node.file_key_a32),
                folder_handle=folder_handle,
                source_url=link.url,
            )
        )
    return items


def _api_get_url(session: requests.Session, item: DownloadItem, timeout=30) -> tuple[str, int]:
    if item.folder_handle:
        params = {"id": 0, "n": item.folder_handle}
        payload = [{"a": "g", "g": 1, "n": item.file_handle, "ssl": 1}]
    else:
        params = {"id": 0}
        payload = [{"a": "g", "g": 1, "p": item.file_handle, "ssl": 1}]
    response = session.post(
        API_URL,
        params=params,
        data=json.dumps(payload),
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
    )
    response.raise_for_status()
    body = response.json()
    if isinstance(body, int) and body < 0:
        raise MegaAPIError(body, MEGA_ERROR_CODES.get(body, str(body)))
    obj = body[0] if isinstance(body, list) else body
    if isinstance(obj, int) and obj < 0:
        raise MegaAPIError(obj, MEGA_ERROR_CODES.get(obj, str(obj)))
    if "g" not in obj:
        raise MegaAPIError(-9, "File is not accessible")
    return obj["g"], int(obj.get("s", item.size))


def _decrypt_and_write(
    encrypted_iter,
    dest_path: str,
    file_size: int,
    file_key_a32,
    progress_cb,
    cancel_event: threading.Event | None,
):
    k, iv, meta_mac = file_aes_parts(file_key_a32)
    k_str = a32_to_str(k)
    counter = Counter.new(128, initial_value=((iv[0] << 32) + iv[1]) << 64)
    aes = AES.new(k_str, AES.MODE_CTR, counter=counter)
    mac_str = b"\0" * 16
    mac_encryptor = AES.new(k_str, AES.MODE_CBC, mac_str)
    iv_str = a32_to_str([iv[0], iv[1], iv[0], iv[1]])

    part_path = dest_path + ".part"
    written = 0
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    try:
        with open(part_path, "wb") as out:
            for chunk in encrypted_iter:
                if cancel_event and cancel_event.is_set():
                    raise DownloadCancelled()
                plain = aes.decrypt(chunk)
                out.write(plain)
                written += len(plain)
                if progress_cb:
                    progress_cb(written, file_size)

                encryptor = AES.new(k_str, AES.MODE_CBC, iv_str)
                i = 0
                for i in range(0, max(len(plain) - 16, 0), 16):
                    encryptor.encrypt(plain[i : i + 16])
                if file_size > 16:
                    i = i + 16 if len(plain) > 16 else 0
                else:
                    i = 0
                block = plain[i : i + 16]
                if len(block) % 16:
                    block += b"\0" * (16 - len(block) % 16)
                if block:
                    mac_str = mac_encryptor.encrypt(encryptor.encrypt(block))

        file_mac = str_to_a32(mac_str)
        if (file_mac[0] ^ file_mac[1], file_mac[2] ^ file_mac[3]) != tuple(meta_mac):
            raise ValueError("Decryption check failed (bad key or corrupt download)")
        if os.path.exists(dest_path):
            os.remove(dest_path)
        os.replace(part_path, dest_path)
    except Exception:
        if os.path.exists(part_path):
            try:
                os.remove(part_path)
            except OSError:
                pass
        raise


def download_item(
    item: DownloadItem,
    dest_dir: str,
    progress_cb=None,
    cancel_event: threading.Event | None = None,
    skip_existing: bool = True,
    session: requests.Session | None = None,
) -> str:
    dest_path = os.path.join(dest_dir, item.relative_path.replace("/", os.sep))
    if skip_existing and os.path.exists(dest_path) and os.path.getsize(dest_path) == item.size:
        if progress_cb:
            progress_cb(item.size, item.size)
        return dest_path

    own = session is None
    session = session or requests.Session()
    try:
        file_url, file_size = _api_get_url(session, item)
        with session.get(file_url, stream=True, timeout=60) as resp:
            resp.raise_for_status()

            def chunks():
                for _start, chunk_size in get_chunks(file_size):
                    if cancel_event and cancel_event.is_set():
                        raise DownloadCancelled()
                    data = resp.raw.read(chunk_size)
                    if not data:
                        break
                    yield data

            _decrypt_and_write(
                chunks(),
                dest_path,
                file_size,
                item.file_key_a32,
                progress_cb,
                cancel_event,
            )
        return dest_path
    finally:
        if own:
            session.close()


def download_selected(
    items: list[DownloadItem],
    dest_dir: str,
    progress_cb=None,
    cancel_event: threading.Event | None = None,
    skip_existing: bool = True,
):
    """Download only the given items. Callers must pass an explicit selection."""
    if not items:
        return
    session = requests.Session()
    try:
        total = sum(item.size for item in items)
        done = 0
        for index, item in enumerate(items, start=1):
            if cancel_event and cancel_event.is_set():
                raise DownloadCancelled()

            def file_progress(written, size, base=done, current=item):
                if progress_cb:
                    progress_cb(
                        {
                            "index": index,
                            "count": len(items),
                            "name": current.name,
                            "file_written": written,
                            "file_size": size,
                            "total_written": base + written,
                            "total_size": total,
                        }
                    )

            download_item(
                item,
                dest_dir,
                progress_cb=file_progress,
                cancel_event=cancel_event,
                skip_existing=skip_existing,
                session=session,
            )
            done += item.size
            time.sleep(0.15)
    finally:
        session.close()
